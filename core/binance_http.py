from __future__ import annotations

from typing import Any

import requests


class BinanceHTTP:
    def __init__(self, endpoint: str, timeout: int = 5) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = requests.get(
            f"{self.endpoint}{path}",
            params=params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def get_server_time(self) -> dict[str, Any]:
        return self._get("/api/v3/time")

    def get_ticker_24h(self, symbol: str) -> dict[str, Any]:
        return self._get("/api/v3/ticker/24hr", params={"symbol": symbol.upper()})
