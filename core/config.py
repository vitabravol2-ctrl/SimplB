import json
from pathlib import Path
from typing import Dict

CONFIG_PATH = Path("config.json")


def load_config() -> Dict[str, str]:
    """Load config with API credentials from config.json if it exists."""
    if not CONFIG_PATH.exists():
        return {"api_key": "", "secret": ""}

    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return {
            "api_key": data.get("api_key", ""),
            "secret": data.get("secret", ""),
        }
    except (json.JSONDecodeError, OSError):
        return {"api_key": "", "secret": ""}


def save_config(api_key: str, secret: str) -> None:
    """Persist API credentials to config.json."""
    payload = {"api_key": api_key.strip(), "secret": secret.strip()}
    CONFIG_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
