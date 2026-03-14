"""Shared HTTP request utilities for source connectors."""

import logging
import time
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# Per-host session pool. Each unique origin (scheme + host) gets its own
# httpx.Client, reusing TCP + TLS connections via HTTP keep-alive + HTTP/2.
_session_pool = {}


def _get_session(url):
    """Return a persistent Client for the given URL's origin."""
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin not in _session_pool:
        _session_pool[origin] = httpx.Client(http2=True)
    return _session_pool[origin]


def request_with_retry(url, params, headers=None, timeout=30, max_retries=3,
                       validate_response=None):
    """Make GET request with exponential backoff retry.

    Uses a per-host session pool for TCP/TLS connection reuse (HTTP/2).

    Args:
        url: Request URL.
        params: Query parameters dict.
        headers: Optional HTTP headers dict.
        timeout: Request timeout in seconds.
        max_retries: Maximum retry attempts.
        validate_response: Optional callable(data) -> None that raises on
            invalid response bodies (e.g. Moralis 200-with-error).
            Called after successful JSON parse. If it raises, the request
            is retried.

    Returns:
        Parsed JSON response.

    Raises:
        RuntimeError: After all retries exhausted.
    """
    session = _get_session(url)

    for attempt in range(max_retries):
        try:
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
