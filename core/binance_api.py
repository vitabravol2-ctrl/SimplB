import hashlib
import hmac
import time
from typing import Any, Dict

import requests

BASE_URL = "https://api.binance.com"
TIMEOUT = 6


def _request(endpoint: str, params: Dict[str, Any] | None = None, headers: Dict[str, str] | None = None) -> Dict[str, Any]:
    response = requests.get(f"{BASE_URL}{endpoint}", params=params, headers=headers, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def get_book_ticker(symbol: str) -> Dict[str, Any]:
    return _request("/api/v3/ticker/bookTicker", params={"symbol": symbol})


def get_24hr(symbol: str) -> Dict[str, Any]:
    return _request("/api/v3/ticker/24hr", params={"symbol": symbol})


def get_account(api_key: str, secret: str) -> Dict[str, Any]:
    timestamp = int(time.time() * 1000)
    query = f"timestamp={timestamp}"
    signature = hmac.new(secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()

    headers = {"X-MBX-APIKEY": api_key}
    params = {"timestamp": timestamp, "signature": signature}
    return _request("/api/v3/account", params=params, headers=headers)
