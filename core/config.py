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
    "api_key": "",
    "api_secret": "",
    "account_poll_ms": 3000,
    "show_balances": True,
    "active_algorithm": "BasicScalper",
    "algorithms": {
        "BasicScalper": {
            "enabled": False,
            "order_size_usdt": 20.0,
            "take_profit_usdt": 5.0,
            "stop_loss_usdt": 5.0,
            "max_cycles": 20,
            "cooldown_sec": 10,
            "fee_bps": 10,
            "allow_martingale": False,
            "martingale_multiplier": 2.0,
            "max_martingale_steps": 3,
            "max_spread": 5.0,
        }
    },
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
    if "algorithms" not in merged:
        merged["algorithms"] = DEFAULT_CONFIG["algorithms"]
    if "BasicScalper" not in merged["algorithms"]:
        merged["algorithms"]["BasicScalper"] = DEFAULT_CONFIG["algorithms"]["BasicScalper"].copy()
    return merged


def save_config(config: dict[str, Any]) -> None:
    payload = DEFAULT_CONFIG.copy()
    payload.update(config)
    with CONFIG_PATH.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)
