"""Shyft source connector for RD-001 raw transaction data.

Two-phase architecture:
  Phase 1: getSignaturesForAddress via Shyft RPC (fast, no rate limit)
  Phase 2: parse_selected via Shyft REST (rate-limited, 100 sigs/batch)

Batch RPC support for daily automation: discover new signatures for
multiple pools in a single HTTP request (up to 250 pools per batch).

Phase 2 supports concurrent parsing via _parse_selected(sigs, max_workers).
Per-key rate limiting ensures each API key respects its cooldown even
when multiple threads share the pool.
"""

import itertools
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from django.conf import settings

from functools import partial

from pipeline.connectors.http import (
    filter_rpc_signatures,
    request_with_retry,
    validate_jsonrpc_response,
)

logger = logging.getLogger(__name__)

# Endpoints
RPC_URL = "https://rpc.shyft.to"
REST_URL = "https://api.shyft.to/sol/v1"

# Limits (verified in exploration)
SIG_LIMIT = 1000          # max signatures per getSignaturesForAddress call
RPC_BATCH_SIZE = 250      # max calls per batch RPC request (tested)
RATE_LIMIT_SLEEP = 0.6    # min seconds between REST calls on the SAME key
MIN_PARSE_BATCH_SIZE = 10
PARSE_BATCH_SIZE = max(
    MIN_PARSE_BATCH_SIZE,
    min(int(os.environ.get('MARJON_U001_RD001_PARSE_BATCH_SIZE', '100')), 100),
)   # hard cap is 100

# Round-robin iterator over Shyft API keys (thread-safe).
# Invalid keys are filtered out at import time to avoid repeated
# auth failures in the rotation.
_validated_keys = None
_key_pool = None
_key_lock = threading.Lock()

# Per-key rate limiting: tracks last-used timestamp per key.
_key_last_used = {}       # {key: monotonic_time}
_rate_lock = threading.Lock()

_init_lock = threading.Lock()


def _init_key_pool():
    """Lazily validate and initialize the key pool on first use (thread-safe)."""
    global _validated_keys, _key_pool
    if _key_pool is not None:
        return

    with _init_lock:
        # Double-check after acquiring lock
        if _key_pool is not None:
            return

        import httpx
        valid = []
        for key in settings.SHYFT_API_KEYS:
            try:
                resp = httpx.post(
                    f"{RPC_URL}?api_key={key}",
                    json={"jsonrpc": "2.0", "id": 0, "method": "getHealth", "params": []},
                    timeout=10,
                )
                if resp.status_code == 200:
                    valid.append(key)
                else:
                    logger.warning("Shyft key %s... invalid (HTTP %d), excluding",
                                   key[:8], resp.status_code)
            except Exception:
                logger.warning("Shyft key %s... unreachable, excluding", key[:8])

        if not valid:
            logger.error("No valid Shyft API keys found!")
            valid = settings.SHYFT_API_KEYS  # fallback to all

        _validated_keys = valid
        _key_pool = itertools.cycle(valid)
        logger.info("Shyft key pool: %d/%d keys valid",
                     len(valid), len(settings.SHYFT_API_KEYS))


def _next_api_key():
    """Return the next API key from the rotation (thread-safe)."""
    _init_key_pool()
    with _key_lock:
        return next(_key_pool)


def _acquire_rate_limited_key():
    """Acquire the least-recently-used key, sleeping if needed for rate limit.

    Thread-safe. With N keys and RATE_LIMIT_SLEEP=S, sustains N/S calls/sec.
    """
    _init_key_pool()

    while True:
        with _rate_lock:
            now = time.monotonic()
            best_key = None
            best_wait = float('inf')
            for key in _validated_keys:
                last = _key_last_used.get(key, 0)
                wait = max(0, RATE_LIMIT_SLEEP - (now - last))
                if wait < best_wait:
                    best_wait = wait
                    best_key = key

            if best_wait <= 0:
                _key_last_used[best_key] = now
                return best_key

        # All keys on cooldown — sleep until soonest is ready, then retry
        time.sleep(best_wait)


def _validate_shyft_response(data):
    """Raise if Shyft REST returned 200 with success=false."""
    if isinstance(data, dict) and data.get('success') is False:
        msg = data.get('message', 'Unknown Shyft error')
        raise ValueError(f"Shyft error: {msg}")


_validate_rpc_response = partial(validate_jsonrpc_response, source_name="Shyft RPC")


# ---------------------------------------------------------------------------
# Phase 1: Signature discovery via RPC
# ---------------------------------------------------------------------------

def _fetch_signatures(pool_address, start=None, end=None, until_sig=None):
    """Fetch transaction signatures for a pool via getSignaturesForAddress.

    Paginates backward from newest. Stops when blockTime < start or
    all signatures are exhausted. Retries with next key on auth failures.

    Args:
        pool_address: Pumpswap pool address.
        start: Optional datetime (UTC) — stop when blockTime < start.
        end: Optional datetime (UTC) — unused (results are newest-first).
        until_sig: Optional signature — only return sigs newer than this
            (exclusive boundary, for incremental fetching).

    Returns:
        List of signature dicts: {signature, blockTime, err}.
    """
    api_key = _next_api_key()
    rpc_url = f"{RPC_URL}?api_key={api_key}"
    all_sigs = []
    cursor = None

    while True:
        params = [pool_address, {"limit": SIG_LIMIT}]
        if cursor:
            params[1]["before"] = cursor
        if until_sig:
            params[1]["until"] = until_sig

        payload = {
            "jsonrpc": "2.0",
            "id": len(all_sigs),
            "method": "getSignaturesForAddress",
            "params": params,
        }

        data = request_with_retry(
            rpc_url, method='POST', json_body=payload,
            validate_response=_validate_rpc_response,
        )

        result = data.get('result', [])
        if not result:
            break

        all_sigs.extend(result)
        # Stop when oldest sig is before start
        if start is not None:
            oldest_time = datetime.fromtimestamp(
                result[-1]['blockTime'], tz=timezone.utc,
            )
            if oldest_time < start:
                break

        # Last page
        if len(result) < SIG_LIMIT:
            break

        cursor = result[-1]['signature']

    return all_sigs


def _fetch_signatures_batch(pool_addresses, limit=SIG_LIMIT):
    """Batch RPC: discover signatures for multiple pools in one HTTP request.

    Packs up to RPC_BATCH_SIZE getSignaturesForAddress calls per request.
    Does NOT paginate — returns first page only (up to `limit` sigs per pool).
    Use for daily automation where incremental discovery (via `until`) means
    one page is typically sufficient.

    Args:
        pool_addresses: List of pool address strings.
        limit: Max signatures per pool (default 1000).

    Returns:
        Dict of {pool_address: [sig_dicts]} where each sig_dict has
        {signature, blockTime, err}.
    """
    api_key = _next_api_key()
    rpc_url = f"{RPC_URL}?api_key={api_key}"

    results = {}

    # Process in chunks of RPC_BATCH_SIZE
    for chunk_start in range(0, len(pool_addresses), RPC_BATCH_SIZE):
        chunk = pool_addresses[chunk_start:chunk_start + RPC_BATCH_SIZE]

        batch_payload = [
            {
                "jsonrpc": "2.0",
                "id": i,
                "method": "getSignaturesForAddress",
                "params": [addr, {"limit": limit}],
            }
            for i, addr in enumerate(chunk)
        ]

        data = request_with_retry(
            rpc_url, method='POST', json_body=batch_payload,
        )

        # Response is a list of RPC results, one per call
        if isinstance(data, list):
            for resp in data:
                idx = resp.get('id', 0)
                if idx < len(chunk):
                    pool = chunk[idx]
                    results[pool] = resp.get('result', [])
        else:
            # Single response (shouldn't happen for batch, but handle it)
            logger.warning(
                "Batch RPC returned non-array response for %d pools",
                len(chunk),
            )

    return results


def discover_new_signatures(pool_watermarks):
    """Batch discover new signatures for multiple pools since their last watermark.

    Uses batch RPC with `until` cursor per pool for efficient incremental
    discovery. Designed for hourly automation.

    Args:
        pool_watermarks: Dict of {pool_address: last_processed_signature}.
            Use None for pools with no prior data (fetches all).

    Returns:
        Dict of {pool_address: [sig_dicts]} with only new, non-failed sigs.
    """
    api_key = _next_api_key()
    rpc_url = f"{RPC_URL}?api_key={api_key}"

    results = {}
    pool_list = list(pool_watermarks.keys())

    for chunk_start in range(0, len(pool_list), RPC_BATCH_SIZE):
        chunk = pool_list[chunk_start:chunk_start + RPC_BATCH_SIZE]

        batch_payload = []
        for i, pool in enumerate(chunk):
            params = {"limit": SIG_LIMIT}
            until_sig = pool_watermarks[pool]
            if until_sig:
                params["until"] = until_sig
            batch_payload.append({
                "jsonrpc": "2.0",
                "id": i,
                "method": "getSignaturesForAddress",
                "params": [pool, params],
            })

        data = request_with_retry(
            rpc_url, method='POST', json_body=batch_payload,
        )

        if isinstance(data, list):
            for resp in data:
                idx = resp.get('id', 0)
                if idx < len(chunk):
                    pool = chunk[idx]
                    sigs = resp.get('result', [])
                    # Pre-filter: drop failed sigs
                    valid = [s for s in sigs if s.get('err') is None]
                    results[pool] = valid

    # Note: pools with >= SIG_LIMIT sigs may have more (truncated).
    # We do NOT paginate here — the caller's fetch_transactions_for_coin
    # handles full pagination during processing. The batch discovery's
    # job is only to identify which pools have new activity.
    truncated = [p for p, sigs in results.items() if len(sigs) >= SIG_LIMIT]
    if truncated:
        logger.info(
            "%d pools hit sig limit (%d) — will be fully fetched during processing",
            len(truncated), SIG_LIMIT,
        )

    logger.info(
        "Batch discovered signatures for %d pools (%d total sigs)",
        len(results),
        sum(len(s) for s in results.values()),
    )
    return results


_filter_signatures = filter_rpc_signatures


# ---------------------------------------------------------------------------
# Phase 2: Transaction parsing via REST
# ---------------------------------------------------------------------------

def _parse_one_batch(chunk):
    """Parse a single batch of ≤100 signatures via Shyft REST (thread-safe).

    Acquires a rate-limited key, makes the request, retries with other
    keys on auth failures.

    Returns:
        List of parsed Shyft transaction dicts.
    """
    _init_key_pool()
    key_retries = len(_validated_keys)

    payload = {
        'network': 'mainnet-beta',
        'transaction_signatures': chunk,
        'enable_events': True,
        'enable_raw': False,
    }

    for attempt in range(key_retries):
        api_key = _acquire_rate_limited_key()
        try:
            data = request_with_retry(
                f"{REST_URL}/transaction/parse_selected",
                headers={'x-api-key': api_key},
                method='POST',
                json_body=payload,
                validate_response=_validate_shyft_response,
            )
            return data.get('result', [])
        except Exception:
            if attempt < key_retries - 1:
                logger.warning(
                    "Key %s... failed, trying next key",
                    api_key[:8],
                )
                continue
            raise


def _parse_with_fallback(chunk):
    """Parse one chunk, splitting it if transport instability persists.

    Shyft `parse_selected` accepts up to 100 signatures, but live RD-001 runs
    still show intermittent disconnects on some larger batches. If a batch
    fails after exhausting retries across all keys, recursively split it into
    smaller chunks until it succeeds or reaches the minimum fallback size.
    """
    try:
        return _parse_one_batch(chunk)
    except Exception:
        if len(chunk) <= MIN_PARSE_BATCH_SIZE:
            raise

        split_at = len(chunk) // 2
        left = chunk[:split_at]
        right = chunk[split_at:]
        logger.warning(
            "parse_selected batch of %d signatures failed; retrying as %d + %d",
            len(chunk), len(left), len(right),
            exc_info=True,
        )
        return _parse_with_fallback(left) + _parse_with_fallback(right)


def _parse_selected(signatures, max_workers=1):
    """Parse transaction signatures via Shyft's parse_selected endpoint.

    Batches signatures into chunks of PARSE_BATCH_SIZE (100 max).
    Uses per-key rate limiting to maximize throughput across keys.

    With max_workers > 1, batches are processed concurrently using a
    thread pool. Each thread acquires its own rate-limited key.

    Args:
        signatures: List of signature strings.
        max_workers: Concurrent parse threads (default 1 = sequential).

    Returns:
        List of parsed Shyft transaction dicts.
    """
    batches = [
        signatures[i:i + PARSE_BATCH_SIZE]
        for i in range(0, len(signatures), PARSE_BATCH_SIZE)
    ]

    if not batches:
        return []

    if max_workers <= 1:
        all_parsed = []
        for batch in batches:
            all_parsed.extend(_parse_with_fallback(batch))
        return all_parsed

    all_parsed = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_parse_with_fallback, b) for b in batches]
        for future in as_completed(futures):
            all_parsed.extend(future.result())
    return all_parsed


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_transactions(pool_address, start=None, end=None, max_workers=1):
    """Fetch and parse transactions for a Pumpswap pool from Shyft.

    Two-phase approach:
      1. Discover signatures via RPC (fast, no rate limit)
      2. Parse selected signatures via REST (rate-limited)

    Args:
        pool_address: Pumpswap pool address string.
        start: Optional datetime (UTC) — filter sigs before this.
        end: Optional datetime (UTC) — filter sigs after this.
        max_workers: Concurrent parse threads for Phase 2 (default 1).

    Returns:
        Tuple of (transactions, metadata) where transactions is a list of
        raw Shyft transaction dicts and metadata is {'api_calls': int}.
    """
    # Phase 1: discover signatures
    raw_sigs = _fetch_signatures(
        pool_address,
        start,
        end,
    )
    rpc_pages = (len(raw_sigs) // SIG_LIMIT) + 1 if raw_sigs else 1

    if not raw_sigs:
        logger.info(
            "No signatures found for pool %s", pool_address,
        )
        return [], {'api_calls': rpc_pages}

    # Pre-filter
    filtered = _filter_signatures(raw_sigs, start, end)
    dropped = len(raw_sigs) - len(filtered)
    if dropped:
        logger.info(
            "Pre-filtered %d/%d signatures (failed or out-of-window)",
            dropped, len(raw_sigs),
        )

    if not filtered:
        logger.info(
            "All %d signatures filtered out for pool %s",
            len(raw_sigs), pool_address,
        )
        return [], {'api_calls': rpc_pages}

    # Phase 2: parse selected
    transactions = _parse_selected(filtered, max_workers=max_workers)
    rest_calls = (len(filtered) + PARSE_BATCH_SIZE - 1) // PARSE_BATCH_SIZE

    logger.info(
        "Fetched %d transactions for pool %s "
        "(%d sigs discovered, %d filtered, %d parsed, "
        "%d RPC pages + %d REST calls)",
        len(transactions), pool_address,
        len(raw_sigs), len(filtered), len(transactions),
        rpc_pages, rest_calls,
    )

    meta = {'api_calls': rpc_pages + rest_calls}
    return transactions, meta
