import hashlib
import hmac
import time
from typing import Any, Dict, Tuple

import requests

BASE_URL = "https://api.binance.com"
TIMEOUT = 2
MAX_RETRIES = 2


class HTTPRequestError(RuntimeError):
    pass


def _request(
    endpoint: str,
    params: Dict[str, Any] | None = None,
    headers: Dict[str, str] | None = None,
) -> Tuple[Dict[str, Any], float, int]:
    """HTTP GET with timeout/retries. Returns (json, latency_ms, retries_used)."""
    last_exc: Exception | None = None

    for attempt in range(MAX_RETRIES + 1):
        start = time.perf_counter()
        try:
            response = requests.get(
                f"{BASE_URL}{endpoint}", params=params, headers=headers, timeout=TIMEOUT
            )
            latency_ms = (time.perf_counter() - start) * 1000
            response.raise_for_status()
            return response.json(), latency_ms, attempt
        except requests.RequestException as exc:
            last_exc = exc

    raise HTTPRequestError(f"GET {endpoint} failed after {MAX_RETRIES + 1} attempts: {last_exc}")


def get_book_ticker(symbol: str) -> Tuple[Dict[str, Any], float, int]:
    return _request("/api/v3/ticker/bookTicker", params={"symbol": symbol})


def get_24hr(symbol: str) -> Tuple[Dict[str, Any], float, int]:
    return _request("/api/v3/ticker/24hr", params={"symbol": symbol})


def get_account(api_key: str, secret: str) -> Tuple[Dict[str, Any], float, int]:
    timestamp = int(time.time() * 1000)
    query = f"timestamp={timestamp}"
    signature = hmac.new(secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()

    headers = {"X-MBX-APIKEY": api_key}
    params = {"timestamp": timestamp, "signature": signature}
    return _request("/api/v3/account", params=params, headers=headers)
