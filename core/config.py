from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CONFIG_PATH = Path("config.json")

DEFAULT_CONFIG: dict[str, Any] = {
    "symbol": "BTCUSDT",
    "ws_url": "wss://stream.binance.com:9443/ws/btcusdt@bookTicker",
    "http_endpoint": "https://api.binance.com",
    "ui_refresh_ms": 250,
}


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()

    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (json.JSONDecodeError, OSError):
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()

    merged = DEFAULT_CONFIG.copy()
    merged.update(data)
    return merged


def save_config(config: dict[str, Any]) -> None:
    payload = DEFAULT_CONFIG.copy()
    payload.update(config)
    with CONFIG_PATH.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)
