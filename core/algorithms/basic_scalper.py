from __future__ import annotations

from typing import Any

from core.algorithms.base import AlgoContext, BaseAlgorithm


class BasicScalper(BaseAlgorithm):
    def __init__(self, settings: dict[str, Any]) -> None:
        super().__init__("BasicScalper")
        self.settings = settings
        self.planned_action = "WAIT"
        self.fake_entry_price = 0.0
        self.tp_price = 0.0
        self.sl_price = 0.0
        self.in_position = False

        self.martingale_step = 0
        self.current_order_size = float(settings.get("order_size_usdt", 20.0))
        self.last_result = "NONE"
        self.multiplier = float(settings.get("martingale_multiplier", 2.0))
        self.max_steps = int(settings.get("max_martingale_steps", 3))

        self.rules = [
            {"if": "not_in_position and spread_ok", "then": "buy_signal"},
            {"if": "in_position and pnl >= take_profit", "then": "sell_profit"},
            {"if": "in_position and pnl <= stop_loss", "then": "sell_loss"},
            {"if": "loss_closed and allow_martingale", "then": "increase_size"},
        ]

    def start(self) -> None:
        super().start()
        self.state = "IDLE"

    def reset_martingale(self) -> None:
        self.martingale_step = 0
        self.current_order_size = float(self.settings.get("order_size_usdt", 20.0))

    def on_trade_result(self, result: str, logger=None) -> None:
        self.last_result = result
        if result == "profit":
            self.reset_martingale()
            if logger:
                logger.info("martingale step changed | reset after profit")
            return

        if result == "loss":
            if bool(self.settings.get("allow_martingale", False)) and self.martingale_step < self.max_steps:
                self.martingale_step += 1
                self.current_order_size *= self.multiplier
                if logger:
                    logger.info("martingale step changed | step=%s size=%.2f", self.martingale_step, self.current_order_size)
            else:
                self.stop()

    def on_tick(self, context: AlgoContext) -> dict[str, Any]:
        if self.state == "STOPPED":
            return self.get_status()

        enabled = bool(self.settings.get("enabled", False))
        spread = float(context.market_data.get("spread", 0.0))
        ask = float(context.market_data.get("ask", 0.0))
        spread_ok = spread > 0 and spread <= float(self.settings.get("max_spread", 5.0))

        if not enabled:
            self.state = "IDLE"
            self.planned_action = "WAIT"
        elif not self.in_position and spread_ok:
            self.state = "READY_TO_BUY"
            self.fake_entry_price = ask
            self.tp_price = ask + float(self.settings.get("take_profit_usdt", 5.0))
            self.sl_price = max(0.0, ask - float(self.settings.get("stop_loss_usdt", 5.0)))
            self.planned_action = "BUY_SIGNAL"
        elif self.in_position:
            self.state = "READY_TO_SELL"
            self.planned_action = "SELL_SIGNAL"
        else:
            self.state = "WATCHING"
            self.planned_action = "WAIT"

        return self.get_status()

    def get_status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state,
            "planned_action": self.planned_action,
            "current_order_size": self.current_order_size,
            "tp": self.tp_price,
            "sl": self.sl_price,
            "martingale_step": self.martingale_step,
            "last_result": self.last_result,
            "rules": self.rules,
        }
