import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Any, Dict

import requests


TIMEOUT = 1.5
MAX_RETRIES = 1
LATENCY_SWITCH_MS = 1500
DEFAULT_ENDPOINTS = [
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
]
DEFAULT_HEADERS = {"User-Agent": "Mozilla/5.0"}


class HTTPRequestError(RuntimeError):
    pass


@dataclass
class APIResult:
    data: Dict[str, Any]
    latency_ms: float
    retries_used: int
    endpoint: str
    switched: bool = False


class BinanceAPI:
    def __init__(self, endpoints: list[str] | None = None, timeout: float = TIMEOUT, retries: int = MAX_RETRIES) -> None:
        self.endpoints = endpoints or DEFAULT_ENDPOINTS.copy()
        self.current_idx = 0
        self.current_endpoint = self.endpoints[self.current_idx]
        self.timeout = timeout
        self.retries = retries

    def _switch_endpoint(self) -> bool:
        prev = self.current_idx
        self.current_idx = (self.current_idx + 1) % len(self.endpoints)
        self.current_endpoint = self.endpoints[self.current_idx]
        return self.current_idx != prev

    def request(
        self,
        path: str,
        params: Dict[str, Any] | None = None,
        headers: Dict[str, str] | None = None,
        signed: bool = False,
        api_key: str = "",
        secret: str = "",
        method: str = "GET",
    ) -> APIResult:
        all_headers = DEFAULT_HEADERS.copy()
        if headers:
            all_headers.update(headers)

        req_params = dict(params or {})
        if signed:
            timestamp = int(time.time() * 1000)
            req_params["timestamp"] = timestamp
            query = "&".join(f"{k}={req_params[k]}" for k in sorted(req_params))
            signature = hmac.new(secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
            req_params["signature"] = signature
            all_headers["X-MBX-APIKEY"] = api_key

        last_exc: Exception | None = None
        switched = False

        for attempt in range(self.retries + 1):
            start = time.perf_counter()
            try:
                response = requests.request(
                    method,
                    f"{self.current_endpoint}{path}",
                    params=req_params,
                    headers=all_headers,
                    timeout=self.timeout,
                )
                latency_ms = (time.perf_counter() - start) * 1000

                if response.status_code in {403, 451}:
                    if self._switch_endpoint():
                        switched = True
                    last_exc = HTTPRequestError(f"blocked status={response.status_code}")
                    continue

                response.raise_for_status()
                if latency_ms > LATENCY_SWITCH_MS and self._switch_endpoint():
                    switched = True

                return APIResult(response.json(), latency_ms, attempt, self.current_endpoint, switched)
            except requests.RequestException as exc:
                last_exc = exc
                if self._switch_endpoint():
                    switched = True

        raise HTTPRequestError(f"GET {path} failed after {self.retries + 1} attempts: {last_exc}")

    def get_time(self) -> APIResult:
        return self.request("/api/v3/time")

    def get_book_ticker(self, symbol: str) -> APIResult:
        return self.request("/api/v3/ticker/bookTicker", params={"symbol": symbol})

    def get_24hr(self, symbol: str) -> APIResult:
        return self.request("/api/v3/ticker/24hr", params={"symbol": symbol})

    def get_account(self, api_key: str, secret: str) -> APIResult:
        return self.request("/api/v3/account", signed=True, api_key=api_key, secret=secret)

    def get_balances(self, api_key: str, secret: str, assets: list[str] | None = None) -> Dict[str, Dict[str, float]]:
        account = self.get_account(api_key, secret).data
        wanted = set(assets or [])
        out: Dict[str, Dict[str, float]] = {}
        for item in account.get("balances", []):
            asset = item.get("asset", "")
            if wanted and asset not in wanted:
                continue
            free = float(item.get("free", "0") or 0)
            locked = float(item.get("locked", "0") or 0)
            out[asset] = {"free": free, "locked": locked, "total": free + locked}
        for asset in wanted:
            out.setdefault(asset, {"free": 0.0, "locked": 0.0, "total": 0.0})
        return out

    def test_connection(self, symbol: str, api_key: str = "", secret: str = "") -> Dict[str, Any]:
        checks: list[tuple[str, str]] = [("ping", "/api/v3/time"), ("market", "/api/v3/ticker/bookTicker")]
        total_latency = 0.0
        for check, path in checks:
            params = {"symbol": symbol} if check == "market" else None
            res = self.request(path, params=params)
            total_latency += res.latency_ms

        account_status = "SKIPPED"
        if api_key and secret:
            acc = self.get_account(api_key, secret)
            total_latency += acc.latency_ms
            account_status = "OK"

        return {
            "status": "OK",
            "latency_ms": round(total_latency, 1),
            "endpoint": self.current_endpoint,
            "account": account_status,
        }


    def get_exchange_info(self, symbol: str) -> APIResult:
        return self.request("/api/v3/exchangeInfo", params={"symbol": symbol})

    def get_open_orders(self, symbol: str, api_key: str, secret: str) -> APIResult:
        return self.request("/api/v3/openOrders", params={"symbol": symbol}, signed=True, api_key=api_key, secret=secret)

    def place_limit_buy(self, symbol: str, quantity: float, price: float, api_key: str, secret: str) -> APIResult:
        return self.request(
            "/api/v3/order",
            params={"symbol": symbol, "side": "BUY", "type": "LIMIT", "timeInForce": "GTC", "quantity": quantity, "price": price},
            signed=True,
            api_key=api_key,
            secret=secret,
            method="POST",
        )

    def place_limit_sell(self, symbol: str, quantity: float, price: float, api_key: str, secret: str) -> APIResult:
        return self.request(
            "/api/v3/order",
            params={"symbol": symbol, "side": "SELL", "type": "LIMIT", "timeInForce": "GTC", "quantity": quantity, "price": price},
            signed=True,
            api_key=api_key,
            secret=secret,
            method="POST",
        )

    def cancel_order(self, symbol: str, order_id: int, api_key: str, secret: str) -> APIResult:
        return self.request(
            "/api/v3/order",
            params={"symbol": symbol, "orderId": order_id},
            signed=True,
            api_key=api_key,
            secret=secret,
            method="DELETE",
        )

    def get_order(self, symbol: str, order_id: int, api_key: str, secret: str) -> APIResult:
        return self.request(
            "/api/v3/order",
            params={"symbol": symbol, "orderId": order_id},
            signed=True,
            api_key=api_key,
            secret=secret,
        )
