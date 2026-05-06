from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AlgoContext:
    market_data: dict[str, Any] = field(default_factory=dict)
    balances: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    logger: Any = None


class BaseAlgorithm:
    def __init__(self, name: str) -> None:
        self.name = name
        self.state = "STOPPED"

    def on_tick(self, context: AlgoContext) -> dict[str, Any]:
        raise NotImplementedError

    def on_order_update(self, context: AlgoContext) -> None:
        return None

    def start(self) -> None:
        self.state = "IDLE"

    def stop(self) -> None:
        self.state = "STOPPED"

    def reset(self) -> None:
        self.state = "IDLE"

    def get_status(self) -> dict[str, Any]:
        return {"name": self.name, "state": self.state}
