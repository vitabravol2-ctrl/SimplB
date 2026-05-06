from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlencode

import requests


class BinanceHTTP:
    def __init__(self, endpoint: str, api_key: str = "", api_secret: str = "", timeout: int = 5) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self.timeout = timeout

    def set_credentials(self, api_key: str, api_secret: str) -> None:
        self.api_key = api_key
        self.api_secret = api_secret

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = requests.get(f"{self.endpoint}{path}", params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def signed_request(self, method: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.api_key or not self.api_secret:
            raise ValueError("API credentials are not configured")

        signed_params = dict(params or {})
        signed_params["timestamp"] = int(time.time() * 1000)
        query = urlencode(signed_params)
        signature = hmac.new(self.api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
        signed_params["signature"] = signature

        headers = {"X-MBX-APIKEY": self.api_key}
        response = requests.request(
            method.upper(),
            f"{self.endpoint}{path}",
            params=signed_params,
            headers=headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def get_server_time(self) -> dict[str, Any]:
        return self._get("/api/v3/time")

    def get_ticker_24h(self, symbol: str) -> dict[str, Any]:
        return self._get("/api/v3/ticker/24hr", params={"symbol": symbol.upper()})

    def test_connection(self) -> dict[str, Any]:
        return self.signed_request("GET", "/api/v3/account", {})

    def get_account(self) -> dict[str, Any]:
        return self.signed_request("GET", "/api/v3/account", {})

    def get_balances(self, assets: list[str] | None = None) -> dict[str, dict[str, float]]:
        account = self.get_account()
        assets = assets or ["USDT", "BTC"]
        result: dict[str, dict[str, float]] = {}
        balance_map = {b["asset"]: b for b in account.get("balances", [])}
        for asset in assets:
            item = balance_map.get(asset, {"free": "0", "locked": "0"})
            free = float(item.get("free", 0.0))
            locked = float(item.get("locked", 0.0))
            result[asset] = {"free": free, "locked": locked, "total": free + locked}
        return result
