"""Shyft source connector for RD-001 raw transaction data.

Two-phase architecture:
  Phase 1: getSignaturesForAddress via Shyft RPC (fast, no rate limit)
  Phase 2: parse_selected via Shyft REST (rate-limited, 100 sigs/batch)

Batch RPC support for daily automation: discover new signatures for
multiple pools in a single HTTP request (up to 250 pools per batch).
"""

import itertools
import logging
import threading
import time
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
PARSE_BATCH_SIZE = 100    # max signatures per parse_selected call (hard limit)
RPC_BATCH_SIZE = 250      # max calls per batch RPC request (tested)
RATE_LIMIT_SLEEP = 1.0    # seconds between REST calls per key

# Round-robin iterator over Shyft API keys (thread-safe)
_key_pool = itertools.cycle(settings.SHYFT_API_KEYS)
_key_lock = threading.Lock()


def _next_api_key():
    """Return the next API key from the rotation (thread-safe)."""
    with _key_lock:
        return next(_key_pool)


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
    all signatures are exhausted.

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
    discovery. Designed for daily automation.

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

    # Check for truncation: if any pool returned exactly SIG_LIMIT sigs,
    # there may be more. Fall back to paginated fetch for those pools.
    truncated = [p for p, sigs in results.items() if len(sigs) >= SIG_LIMIT]
    if truncated:
        logger.info(
            "%d pools hit sig limit (%d), using paginated fetch",
            len(truncated), SIG_LIMIT,
        )
        for pool in truncated:
            until_sig = pool_watermarks[pool]
            all_sigs = _fetch_signatures(pool, until_sig=until_sig)
            results[pool] = [s for s in all_sigs if s.get('err') is None]

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

def _parse_selected(signatures):
    """Parse transaction signatures via Shyft's parse_selected endpoint.

    Batches signatures into chunks of PARSE_BATCH_SIZE (100 max).
    Rotates API keys across batches. If a key returns an auth error,
    retries with the next key (handles invalid/expired keys).

    Args:
        signatures: List of signature strings.

    Returns:
        List of parsed Shyft transaction dicts (same format as
        /transaction/history).
    """
    all_parsed = []
    key_retries = len(settings.SHYFT_API_KEYS)

    for chunk_start in range(0, len(signatures), PARSE_BATCH_SIZE):
        chunk = signatures[chunk_start:chunk_start + PARSE_BATCH_SIZE]

        payload = {
            'network': 'mainnet-beta',
            'transaction_signatures': chunk,
            'enable_events': True,
            'enable_raw': False,
        }

        # Try keys until one works (skip bad keys)
        for attempt in range(key_retries):
            api_key = _next_api_key()
            try:
                data = request_with_retry(
                    f"{REST_URL}/transaction/parse_selected",
                    headers={'x-api-key': api_key},
                    method='POST',
                    json_body=payload,
                    validate_response=_validate_shyft_response,
                )
                break
            except Exception:
                if attempt < key_retries - 1:
                    logger.warning(
                        "Key %s... failed, trying next key",
                        api_key[:8],
                    )
                    continue
                raise

        result = data.get('result', [])
        all_parsed.extend(result)

        # Rate limit: sleep between REST calls
        if chunk_start + PARSE_BATCH_SIZE < len(signatures):
            time.sleep(RATE_LIMIT_SLEEP)

    return all_parsed


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_transactions(pool_address, start=None, end=None):
    """Fetch and parse transactions for a Pumpswap pool from Shyft.

    Two-phase approach:
      1. Discover signatures via RPC (fast, no rate limit)
      2. Parse selected signatures via REST (rate-limited)

    Args:
        pool_address: Pumpswap pool address string.
        start: Optional datetime (UTC) — filter sigs before this.
        end: Optional datetime (UTC) — filter sigs after this.

    Returns:
        Tuple of (transactions, metadata) where transactions is a list of
        raw Shyft transaction dicts and metadata is {'api_calls': int}.
    """
    # Phase 1: discover signatures
    raw_sigs = _fetch_signatures(pool_address, start, end)
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
    transactions = _parse_selected(filtered)
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
