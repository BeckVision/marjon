"""Shared HTTP request utilities for source connectors."""

import logging
import threading
import time
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# Per-host session pool. Each unique origin (scheme + host) gets its own
# httpx.Client, reusing TCP + TLS connections via HTTP keep-alive + HTTP/2.
_session_pool = {}
_session_lock = threading.Lock()


def _get_session(url):
    """Return a persistent Client for the given URL's origin (thread-safe)."""
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    with _session_lock:
        if origin not in _session_pool:
            _session_pool[origin] = httpx.Client(http2=True)
        return _session_pool[origin]


def shutdown_sessions():
    """Close all pooled httpx.Client instances and clear the pool."""
    with _session_lock:
        for origin, client in _session_pool.items():
            client.close()
            logger.info("Closed HTTP session for %s", origin)
        _session_pool.clear()


def request_with_retry(url, params=None, headers=None, timeout=30,
                       max_retries=3, validate_response=None,
                       method='GET', json_body=None):
    """Make HTTP request with exponential backoff retry.

    Uses a per-host session pool for TCP/TLS connection reuse (HTTP/2).

    Args:
        url: Request URL.
        params: Query parameters dict (used for GET, also sent with POST).
        headers: Optional HTTP headers dict.
        timeout: Request timeout in seconds.
        max_retries: Maximum retry attempts.
        validate_response: Optional callable(data) -> None that raises on
            invalid response bodies (e.g. Moralis 200-with-error).
            Called after successful JSON parse. If it raises, the request
            is retried.
        method: HTTP method — 'GET' (default) or 'POST'.
        json_body: JSON-serializable body for POST requests.

    Returns:
        Parsed JSON response.

    Raises:
        RuntimeError: After all retries exhausted.
    """
    session = _get_session(url)

    for attempt in range(max_retries):
        try:
            if method == 'POST':
                resp = session.post(
                    url, params=params, json=json_body,
                    headers=headers, timeout=timeout,
                )
            else:
                resp = session.get(
                    url, params=params, headers=headers, timeout=timeout,
                )

            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                logger.warning(
                    "Rate limited (429), waiting %ds (url=%s)",
                    wait, url,
                )
                time.sleep(wait)
                continue

            if resp.status_code >= 500:
                wait = 2 ** (attempt + 1)
                logger.warning(
                    "Server error %d, waiting %ds (url=%s)",
                    resp.status_code, wait, url,
                )
                time.sleep(wait)
                continue

            resp.raise_for_status()
            data = resp.json()

            if validate_response:
                try:
                    validate_response(data)
                except Exception as e:
                    wait = 2 ** (attempt + 1)
                    logger.warning(
                        "Response validation failed: %s, waiting %ds (url=%s)",
                        e, wait, url,
                    )
                    time.sleep(wait)
                    continue

            return data

        except ValueError:
            # json.JSONDecodeError is a subclass of ValueError
            wait = 2 ** (attempt + 1)
            logger.warning(
                "JSON decode error, waiting %ds (url=%s)",
                wait, url,
            )
            time.sleep(wait)
            continue

        except (httpx.TimeoutException, httpx.NetworkError):
            wait = 2 ** (attempt + 1)
            logger.warning(
                "Network error, waiting %ds (url=%s)",
                wait, url,
            )
            time.sleep(wait)
            continue

    raise RuntimeError(
        f"Failed after {max_retries} retries: {url}"
    )


def validate_jsonrpc_response(data, source_name="RPC"):
    """Raise if a JSON-RPC response contains an error.

    Reusable validator for any Solana RPC endpoint (Shyft, Helius, etc.).
    Pass as `validate_response` to `request_with_retry`.
    """
    if isinstance(data, dict) and 'error' in data:
        err = data['error']
        msg = err.get('message', str(err)) if isinstance(err, dict) else str(err)
        raise ValueError(f"{source_name} error: {msg}")


def filter_rpc_signatures(sigs, start=None, end=None):
    """Filter getSignaturesForAddress results: drop failed and out-of-window.

    Works with any Solana RPC provider (Shyft, Helius, etc.) since the
    response format is standard.

    Args:
        sigs: List of sig dicts from getSignaturesForAddress.
        start: Optional datetime (UTC) — drop sigs before this.
        end: Optional datetime (UTC) — drop sigs after this.

    Returns:
        List of signature strings (just the sig hashes).
    """
    start_ts = int(start.timestamp()) if start else None
    end_ts = int(end.timestamp()) if end else None

    filtered = []
    for s in sigs:
        if s.get('err') is not None:
            continue
        block_time = s.get('blockTime', 0)
        if start_ts and block_time < start_ts:
            continue
        if end_ts and block_time > end_ts:
            continue
        filtered.append(s['signature'])

    return filtered
