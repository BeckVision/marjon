"""Shared HTTP request utilities for source connectors."""

import logging
import os
import threading
import time
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

DEFAULT_HTTP2_DISABLED_HOSTS = {
    'api.shyft.to',
    'rpc.shyft.to',
}

# Per-host session pool. Each unique origin (scheme + host) gets its own
# httpx.Client, reusing TCP + TLS connections via HTTP keep-alive + HTTP/2.
_session_pool = {}
_session_lock = threading.Lock()


def _get_session(url):
    """Return a persistent Client for the given URL's origin (thread-safe)."""
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    client_kwargs = _client_kwargs_for_url(url)
    with _session_lock:
        if origin not in _session_pool:
            _session_pool[origin] = httpx.Client(**client_kwargs)
        return _session_pool[origin]


def _http2_enabled_for_url(url):
    """Return whether HTTP/2 should be used for the URL's host."""
    host = (urlparse(url).hostname or '').lower()
    disabled_hosts = set(DEFAULT_HTTP2_DISABLED_HOSTS)
    extra = os.environ.get('MARJON_HTTP2_DISABLED_HOSTS', '')
    if extra:
        disabled_hosts |= {
            item.strip().lower() for item in extra.split(',') if item.strip()
        }
    return host not in disabled_hosts


def _client_kwargs_for_url(url):
    """Return host-specific httpx.Client kwargs.

    Shyft has shown repeated disconnects on reused pooled sessions in live
    RD-001 runs. Disabling keep-alive reuse for those hosts trades a small
    amount of latency for more predictable transport behavior.
    """
    kwargs = {
        'http2': _http2_enabled_for_url(url),
    }

    host = (urlparse(url).hostname or '').lower()
    if host in DEFAULT_HTTP2_DISABLED_HOSTS:
        kwargs['limits'] = httpx.Limits(max_keepalive_connections=0)

    return kwargs


def _drop_session(url):
    """Close and remove the pooled client for a URL origin."""
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    with _session_lock:
        client = _session_pool.pop(origin, None)
    if client is not None:
        client.close()
        logger.info("Dropped HTTP session for %s", origin)


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
    last_error = None
    for attempt in range(max_retries):
        try:
            session = _get_session(url)
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
                last_error = 'rate_limited_429'
                wait = 2 ** (attempt + 1)
                logger.warning(
                    "Rate limited (429), waiting %ds (url=%s)",
                    wait, url,
                )
                time.sleep(wait)
                continue

            if resp.status_code == 417:
                last_error = 'expectation_failed_417'
                wait = 2 ** (attempt + 1)
                _drop_session(url)
                logger.warning(
                    "Expectation failed (417), retrying with a fresh session in %ds (url=%s)",
                    wait, url,
                )
                time.sleep(wait)
                continue

            if resp.status_code >= 500:
                last_error = f'server_error_{resp.status_code}'
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
                    last_error = f'response_validation_error: {e}'
                    wait = 2 ** (attempt + 1)
                    logger.warning(
                        "Response validation failed: %s, waiting %ds (url=%s)",
                        e, wait, url,
                    )
                    time.sleep(wait)
                    continue

            return data

        except ValueError as e:
            # json.JSONDecodeError is a subclass of ValueError
            last_error = f'json_decode_error: {e}'
            wait = 2 ** (attempt + 1)
            logger.warning(
                "JSON decode error, waiting %ds (url=%s)",
                wait, url,
            )
            time.sleep(wait)
            continue

        except httpx.TransportError as e:
            last_error = f'transport_error: {e.__class__.__name__}: {e}'
            wait = 2 ** (attempt + 1)
            _drop_session(url)
            logger.warning(
                "Network error, waiting %ds (url=%s)",
                wait, url,
            )
            time.sleep(wait)
            continue

    detail = f" (last_error: {last_error})" if last_error else ""
    raise RuntimeError(
        f"Failed after {max_retries} retries: {url}{detail}"
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
