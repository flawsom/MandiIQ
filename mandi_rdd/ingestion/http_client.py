"""
MandiIQ — Shared HTTP client for ingestion pipelines.

Consolidates URL open, SSL context, retry, API key resolution, and
safe-float parsing so every fetch_*.py module uses the same logic
instead of duplicating SSL/retry/error-handling code.

Exports:
  SSL_CTX          — permissive SSL context for data.gov.in HTTPS
  safe_float(val)  — parse a value to float or return None
  get_api_key()    — resolve DATA_GOV_IN_API_KEY from env or .env
  http_get()       — raw HTTP GET with retry → HTTPResponse
  http_get_json()  — HTTP GET → parsed JSON
  http_get_text()  — HTTP GET → decoded text
"""

import json
import logging
import os
import ssl
import time
import urllib.error
import urllib.request
import http.client

logger = logging.getLogger(__name__)

# ── SSL context (permissive for government APIs with custom CAs) ──
SSL_CTX = ssl.create_default_context()
SSL_CTK_CHECK = False

try:
    _ctx = ssl.create_default_context()
    _ctx.check_hostname = False
    _ctx.verify_mode = ssl.CERT_NONE
    SSL_CTX = _ctx
except Exception:
    pass


def safe_float(val):
    """Parse a value to float or return None."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def get_api_key(env_var: str = "DATA_GOV_IN_API_KEY") -> str | None:
    """Resolve an API key from environment or .env file.

    Args:
        env_var: Name of the environment variable to check.

    Returns:
        The key string, or None if not set.
    """
    # Try environment first
    key = os.environ.get(env_var)
    if key:
        return key
    # Try .env as fallback
    try:
        from dotenv import load_dotenv
        load_dotenv()
        key = os.environ.get(env_var)
    except Exception:
        pass
    return key


def http_get(
    url: str,
    headers: dict | None = None,
    timeout: int = 25,
    max_retries: int = 3,
    ssl_context=None,
) -> http.client.HTTPResponse:
    """HTTP GET with exponential backoff retry.

    Args:
        url: Full URL to fetch.
        headers: Optional dict of extra HTTP headers.
        timeout: Seconds before timeout (default 25).
        max_retries: Number of retries on failure (default 3).
        ssl_context: SSL context override (defaults to SSL_CTX).

    Returns:
        urllib.request.HttpResponse on success.

    Raises:
        urllib.error.URLError: After all retries exhausted.
    """
    ctx = ssl_context or SSL_CTX
    _headers = {
        "User-Agent": "MandiIQ/1.0",
        "Accept": "application/json, text/plain, */*",
    }
    if headers:
        _headers.update(headers)

    last_error = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=_headers)
            return urllib.request.urlopen(req, timeout=timeout, context=ctx)
        except urllib.error.HTTPError as e:
            last_error = e
            if e.code == 429:
                wait = min(2 ** attempt * 2, 30)
                logger.debug("Rate limited, retrying in %ss (attempt %d/%d)", wait, attempt + 1, max_retries)
                time.sleep(wait)
                continue
            # Non-retryable HTTP errors
            raise
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            last_error = e
            if attempt < max_retries - 1:
                wait = min(2 ** attempt * 3, 15)
                logger.debug("Request failed, retrying in %ss (attempt %d/%d)", wait, attempt + 1, max_retries)
                time.sleep(wait)
                continue
            raise

    raise last_error  # type: ignore[misc]


def http_get_json(
    url: str,
    headers: dict | None = None,
    timeout: int = 25,
    max_retries: int = 3,
) -> dict | list:
    """HTTP GET returning parsed JSON.

    Args:
        url: Full URL to fetch.
        headers: Optional extra HTTP headers.
        timeout: Seconds before timeout.
        max_retries: Number of retries on failure.

    Returns:
        Parsed JSON response (dict or list).

    Raises:
        urllib.error.URLError, json.JSONDecodeError on failure.
    """
    resp = http_get(url, headers=headers, timeout=timeout, max_retries=max_retries)
    raw = resp.read()
    return json.loads(raw)


def http_get_text(
    url: str,
    headers: dict | None = None,
    timeout: int = 25,
    max_retries: int = 3,
    encoding: str = "utf-8",
) -> str:
    """HTTP GET returning decoded text.

    Args:
        url: Full URL to fetch.
        headers: Optional extra HTTP headers.
        timeout: Seconds before timeout.
        max_retries: Number of retries on failure.
        encoding: Text encoding (default utf-8).

    Returns:
        Decoded response body as string.

    Raises:
        urllib.error.URLError on failure.
    """
    resp = http_get(url, headers=headers, timeout=timeout, max_retries=max_retries)
    raw = resp.read()
    return raw.decode(encoding)
