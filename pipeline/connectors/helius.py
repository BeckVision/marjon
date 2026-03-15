"""Helius source connector for RD-001 historical transaction backfill.

Two-phase architecture (same pattern as Shyft connector):
  Phase 1: getSignaturesForAddress via Helius RPC (full history to genesis)
  Phase 2: POST /v0/transactions via Helius Enhanced API

Used for coins whose observation windows have expired beyond Shyft's
3-4 day retention limit. Helius provides full historical access.
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
RPC_URL = "https://mainnet.helius-rpc.com"
ENHANCED_URL = "https://api-mainnet.helius-rpc.com"

# Limits
SIG_LIMIT = 1000          # max signatures per getSignaturesForAddress call
PARSE_BATCH_SIZE = 100    # max signatures per POST /v0/transactions call
RATE_LIMIT_SLEEP = 0.5    # 2 req/sec free tier for Enhanced APIs

# Credit costs (for logging/tracking)
RPC_CREDITS = 10
ENHANCED_CREDITS = 100

# Round-robin iterator over Helius API keys (thread-safe)
_key_pool = itertools.cycle(settings.HELIUS_API_KEYS)
_key_lock = threading.Lock()


def _next_api_key():
    """Return the next API key from the rotation (thread-safe)."""
    with _key_lock:
        return next(_key_pool)


_validate_rpc_response = partial(validate_jsonrpc_response, source_name="Helius RPC")


# ---------------------------------------------------------------------------
# Phase 1: Signature discovery via RPC
# ---------------------------------------------------------------------------

def _fetch_signatures(pool_address, start=None, end=None):
    """Fetch transaction signatures for a pool via Helius RPC.

    Full historical depth — can reach back to Solana genesis.
    Paginates backward from newest, stops when blockTime < start.

    Args:
        pool_address: Pumpswap pool address.
        start: Optional datetime (UTC) — stop when blockTime < start.
        end: Optional datetime (UTC) — unused (results are newest-first).

    Returns:
        List of signature dicts: {signature, blockTime, err, ...}.
    """
    api_key = _next_api_key()
    rpc_url = f"{RPC_URL}/?api-key={api_key}"

    all_sigs = []
    cursor = None
    credits_used = 0

    while True:
        params = [pool_address, {"limit": SIG_LIMIT}]
        if cursor:
            params[1]["before"] = cursor

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
        credits_used += RPC_CREDITS

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

    return all_sigs, credits_used


_filter_signatures = filter_rpc_signatures


# ---------------------------------------------------------------------------
# Phase 2: Transaction parsing via Enhanced API
# ---------------------------------------------------------------------------

def _parse_transactions(signatures):
    """Parse transaction signatures via Helius Enhanced API.

    POST /v0/transactions with batches of PARSE_BATCH_SIZE (100 max).
    Rotates API keys across batches.

    Args:
        signatures: List of signature strings.

    Returns:
        List of Helius EnhancedTransaction dicts.
    """
    all_parsed = []
    credits_used = 0

    for chunk_start in range(0, len(signatures), PARSE_BATCH_SIZE):
        chunk = signatures[chunk_start:chunk_start + PARSE_BATCH_SIZE]

        api_key = _next_api_key()
        url = f"{ENHANCED_URL}/v0/transactions"
        params = {'api-key': api_key}
        payload = {"transactions": chunk}

        data = request_with_retry(
            url, params=params, method='POST', json_body=payload,
        )
        credits_used += ENHANCED_CREDITS

        if isinstance(data, list):
            all_parsed.extend(data)
        else:
            logger.warning(
                "Helius parse returned non-array: %s",
                str(data)[:200],
            )

        # Rate limit
        if chunk_start + PARSE_BATCH_SIZE < len(signatures):
            time.sleep(RATE_LIMIT_SLEEP)

    return all_parsed, credits_used


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_transactions(pool_address, start=None, end=None):
    """Fetch and parse transactions for a Pumpswap pool from Helius.

    Two-phase approach with full historical access:
      1. Discover signatures via Helius RPC (back to genesis)
      2. Parse transactions via Enhanced API

    Args:
        pool_address: Pumpswap pool address string.
        start: Optional datetime (UTC) — filter sigs before this.
        end: Optional datetime (UTC) — filter sigs after this.

    Returns:
        Tuple of (transactions, metadata) where transactions is a list of
        Helius EnhancedTransaction dicts and metadata is
        {'api_calls': int, 'credits_used': int}.
    """
    # Phase 1: discover signatures
    raw_sigs, rpc_credits = _fetch_signatures(pool_address, start, end)
    rpc_calls = (len(raw_sigs) // SIG_LIMIT) + 1 if raw_sigs else 1

    if not raw_sigs:
        logger.info(
            "No signatures found for pool %s via Helius", pool_address,
        )
        return [], {'api_calls': rpc_calls, 'credits_used': rpc_credits}

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
        return [], {'api_calls': rpc_calls, 'credits_used': rpc_credits}

    # Phase 2: parse transactions
    transactions, parse_credits = _parse_transactions(filtered)
    rest_calls = (len(filtered) + PARSE_BATCH_SIZE - 1) // PARSE_BATCH_SIZE

    total_credits = rpc_credits + parse_credits
    logger.info(
        "Fetched %d transactions for pool %s via Helius "
        "(%d sigs discovered, %d filtered, %d parsed, "
        "%d RPC + %d REST calls, %d credits)",
        len(transactions), pool_address,
        len(raw_sigs), len(filtered), len(transactions),
        rpc_calls, rest_calls, total_credits,
    )

    meta = {
        'api_calls': rpc_calls + rest_calls,
        'credits_used': total_credits,
    }
    return transactions, meta
