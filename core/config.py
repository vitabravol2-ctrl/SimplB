import json
from pathlib import Path
from typing import Any, Dict

CONFIG_PATH = Path("config.json")
DEFAULT_CONFIG: Dict[str, Any] = {
    "api_key": "",
    "secret": "",
    "symbol": "EURIUSDT",
    "budget_usdt": 100.0,
    "order_size_usdt": 10.0,
    "step_ticks": 1,
    "poll_ms": 1000,
    "account_poll_ms": 5000,
}


def load_config() -> Dict[str, Any]:
    """Load config from config.json and merge with defaults."""
    if not CONFIG_PATH.exists():
        return DEFAULT_CONFIG.copy()

    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        merged = DEFAULT_CONFIG.copy()
        if isinstance(data, dict):
            merged.update(data)
        return merged
    except (json.JSONDecodeError, OSError):
        return DEFAULT_CONFIG.copy()


def save_config_values(values: Dict[str, Any]) -> None:
    """Persist config values while preserving missing defaults."""
    payload = DEFAULT_CONFIG.copy()
    payload.update(values)
    CONFIG_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def save_config(api_key: str, secret: str) -> None:
    """Persist API credentials to config.json."""
    cfg = load_config()
    cfg["api_key"] = api_key.strip()
    cfg["secret"] = secret.strip()
    save_config_values(cfg)
