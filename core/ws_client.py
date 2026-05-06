from __future__ import annotations

import json
import threading
import time
from typing import Any

from PySide6.QtCore import QObject, Signal
from websocket import WebSocketApp


class WSClient(QObject):
    tick = Signal(dict)
    status = Signal(dict)

    def __init__(self, ws_url: str, symbol: str, logger) -> None:
        super().__init__()
        self.ws_url = ws_url
        self.symbol = symbol.upper()
        self.logger = logger
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._ws: WebSocketApp | None = None
        self.reconnect_count = 0
        self.last_error = ""
        self._connected_once = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._ws:
            self._ws.close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._ws = WebSocketApp(
                self.ws_url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            self._ws.run_forever(ping_interval=20, ping_timeout=10)

            if self._stop_event.is_set():
                break

            self.reconnect_count += 1
            self.status.emit(
                {
                    "connected": False,
                    "reconnect_count": self.reconnect_count,
                    "last_error": self.last_error,
                }
            )
            self.logger.warning("reconnect #%s", self.reconnect_count)
            time.sleep(2)

    def _on_open(self, _ws: WebSocketApp) -> None:
        self.last_error = ""
        if self._connected_once:
            self.logger.info("ws connected (reconnected)")
        else:
            self._connected_once = True
            self.logger.info("ws connected")
        self.status.emit(
            {
                "connected": True,
                "reconnect_count": self.reconnect_count,
                "last_error": self.last_error,
            }
        )

    def _on_message(self, _ws: WebSocketApp, message: str) -> None:
        data = json.loads(message)
        bid = float(data.get("b", 0.0))
        ask = float(data.get("a", 0.0))
        bid_qty = float(data.get("B", 0.0))
        ask_qty = float(data.get("A", 0.0))
        tick_ts = int(data.get("E", int(time.time() * 1000)))
        now_ms = int(time.time() * 1000)

        mid = (bid + ask) / 2 if (bid and ask) else 0.0
        spread = ask - bid if (ask and bid) else 0.0
        spread_pct = (spread / mid * 100) if mid else 0.0

        payload: dict[str, Any] = {
            "symbol": self.symbol,
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "spread": spread,
            "spread_pct": spread_pct,
            "bid_qty": bid_qty,
            "ask_qty": ask_qty,
            "tick_ts": tick_ts,
            "age_ms": max(0, now_ms - tick_ts),
            "connected": True,
        }
        self.tick.emit(payload)

    def _on_error(self, _ws: WebSocketApp, error: Any) -> None:
        self.last_error = str(error)
        self.logger.error("ws error: %s", error)
        self.status.emit(
            {
                "connected": False,
                "reconnect_count": self.reconnect_count,
                "last_error": self.last_error,
            }
        )

    def _on_close(self, _ws: WebSocketApp, close_status_code: int, close_msg: str) -> None:
        self.logger.warning("ws disconnected: code=%s msg=%s", close_status_code, close_msg)
        self.status.emit(
            {
                "connected": False,
                "reconnect_count": self.reconnect_count,
                "last_error": self.last_error,
            }
        )
