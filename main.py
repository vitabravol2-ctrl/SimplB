import sys
import time
from typing import Dict

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.binance_api import BinanceAPI, HTTPRequestError
from core.config import load_config, save_config_values
from core.logger import setup_logger
from core.trading_utils import round_price_to_tick, round_qty_to_step, validate_min_notional


class MarketWorker(QObject):
    data_ready = Signal(dict)
    account_ready = Signal(dict)
    error = Signal(str)
    account_error = Signal(str)

    def __init__(
        self,
        symbol: str,
        endpoint: str,
        api_key: str = "",
        secret: str = "",
        poll_ms: int = 1000,
        account_poll_ms: int = 5000,
    ):
        super().__init__()
        self.symbol = symbol
        self.api_key = api_key
        self.secret = secret
        self.poll_ms = poll_ms
        self.account_poll_ms = account_poll_ms
        self.api = BinanceAPI()
        if endpoint in self.api.endpoints:
            self.api.current_endpoint = endpoint
            self.api.current_idx = self.api.endpoints.index(endpoint)
        self.running = False
        self.consecutive_errors = 0
        self.had_error = False
        self.market_timer = None
        self.account_timer = None
        self.poll_ms = poll_ms
        self.account_poll_ms = account_poll_ms

    @Slot()
    def start(self):
        if self.running:
            return
        self.running = True
        self.market_timer = QTimer(self)
        self.market_timer.setInterval(self.poll_ms)
        self.market_timer.timeout.connect(self.poll_market)
        self.market_timer.start()
        self.account_timer = QTimer(self)
        self.account_timer.setInterval(self.account_poll_ms)
        self.account_timer.timeout.connect(self.poll_account)
        self.account_timer.start()
        self.poll_market()
        self.poll_account()

    @Slot()
    def stop(self):
        self.running = False
        for timer in [self.market_timer, self.account_timer]:
            if timer is not None:
                timer.stop()
                timer.deleteLater()
        self.market_timer = None
        self.account_timer = None

    @Slot(str, str, str, int, int)
    def update_settings(self, symbol: str, api_key: str, secret: str, poll_ms: int, account_poll_ms: int):
        self.symbol = symbol
        self.api_key = api_key
        self.secret = secret
        self.poll_ms = poll_ms
        self.account_poll_ms = account_poll_ms

    @Slot()
    def poll_market(self):
        if not self.running:
            return
        try:
            book = self.api.get_book_ticker(self.symbol)
            stat = self.api.get_24hr(self.symbol)
            latency = book.latency_ms + stat.latency_ms
            status = "YELLOW" if (book.retries_used > 0 or stat.retries_used > 0 or book.switched or stat.switched) else "GREEN"
            self.consecutive_errors = 0
            recovered = self.had_error
            self.had_error = False
            self.data_ready.emit(
                {
                    "symbol": self.symbol,
                    "latency_ms": latency,
                    "status": status,
                    "recovered": recovered,
                    "book": book.data,
                    "stat": stat.data,
                }
            )
        except HTTPRequestError as exc:
            self.had_error = True
            self.consecutive_errors += 1
            self.error.emit(str(exc))

    @Slot()
    def poll_account(self):
        if not self.running:
            return
        if not self.api_key or not self.secret:
            self.account_ready.emit({"status": "NO KEYS", "balances": {}})
            return
        try:
            balances = self.api.get_balances(self.api_key, self.secret, ["USDT", "EURI"])
            self.account_ready.emit({"status": "CONNECTED", "balances": balances})
        except HTTPRequestError as exc:
            self.account_error.emit(str(exc))


class SettingsDialog(QDialog):
    def __init__(self, parent, cfg, api):
        super().__init__(parent)
        self.cfg = cfg
        self.api = api
        self.setWindowTitle("Settings")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.api_key_input = QLineEdit(str(cfg.get("api_key", "")))
        self.secret_input = QLineEdit(str(cfg.get("secret", "")))
        self.secret_input.setEchoMode(QLineEdit.Password)
        self.symbol_input = QLineEdit(str(cfg.get("symbol", "EURIUSDT")))
        self.budget_input = QLineEdit(str(cfg.get("budget_usdt", 100)))
        self.order_size_input = QLineEdit(str(cfg.get("order_size_usdt", 10)))
        self.step_ticks_input = QLineEdit(str(cfg.get("step_ticks", 1)))
        self.poll_ms_input = QLineEdit(str(cfg.get("poll_ms", 1000)))
        self.account_poll_ms_input = QLineEdit(str(cfg.get("account_poll_ms", 5000)))
        form.addRow("API KEY", self.api_key_input)
        form.addRow("SECRET", self.secret_input)
        form.addRow("SYMBOL", self.symbol_input)
        form.addRow("Budget USDT", self.budget_input)
        form.addRow("Order size USDT", self.order_size_input)
        form.addRow("Step ticks", self.step_ticks_input)
        form.addRow("Poll ms", self.poll_ms_input)
        form.addRow("Account poll ms", self.account_poll_ms_input)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        save_btn = QPushButton("SAVE")
        save_btn.clicked.connect(self.handle_save)
        test_btn = QPushButton("TEST API")
        test_btn.clicked.connect(self.handle_test)
        buttons.addWidget(save_btn)
        close_btn = QPushButton("CLOSE")
        close_btn.clicked.connect(self.reject)
        buttons.addWidget(test_btn)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

    def handle_save(self):
        try:
            self.cfg["api_key"] = self.api_key_input.text().strip()
            self.cfg["secret"] = self.secret_input.text().strip()
            self.cfg["symbol"] = self.symbol_input.text().strip().upper() or "EURIUSDT"
            self.cfg["budget_usdt"] = float(self.budget_input.text().strip() or "100")
            self.cfg["order_size_usdt"] = float(self.order_size_input.text().strip() or "10")
            self.cfg["step_ticks"] = int(self.step_ticks_input.text().strip() or "1")
            self.cfg["poll_ms"] = max(200, int(self.poll_ms_input.text().strip() or "1000"))
            self.cfg["account_poll_ms"] = max(1000, int(self.account_poll_ms_input.text().strip() or "5000"))
        except ValueError:
            QMessageBox.critical(self, "Settings", "Invalid numeric value")
            return
        save_config_values(self.cfg)
        self.accept()

    def handle_test(self):
        try:
            result = self.api.test_connection(
                self.symbol_input.text().strip().upper() or "EURIUSDT",
                self.api_key_input.text().strip(),
                self.secret_input.text().strip(),
            )
            QMessageBox.information(
                self,
                "Connection test",
                f"{result['status']}\nendpoint: {result['endpoint']}\nlatency: {result['latency_ms']} ms\naccount: {result.get('account', 'SKIPPED')}",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Connection test", f"ERROR\n{exc}")


class SimplBWindow(QMainWindow):
    stop_worker_signal = Signal()

    def __init__(self):
        super().__init__()
        self.logger = setup_logger()
        self.cfg = load_config()
        self.api = BinanceAPI()
        self.symbol = str(self.cfg.get("symbol", "EURIUSDT"))
        self.worker_thread = None
        self.worker = None
        self.last_book = {}
        self.trading_state = "IDLE"
        self.active_order_id = "-"
        self.entry_price = 0.0
        self.exit_price = 0.0
        self.qty = 0.0
        self.filled_qty = 0.0
        self.realized_pnl = 0.0
        self.failure_reason = ""
        self.current_cycle = 0
        self.last_action = "-"
        self.filters: Dict[str, float] = {}
        self.buy_started_at = 0
        self.sell_started_at = 0
        self.buy_quote_total = 0.0
        self.sell_quote_total = 0.0
        self.step_ticks = int(self.cfg.get("step_ticks", 1))
        self.order_size_usdt = float(self.cfg.get("order_size_usdt", 10))
        self.tick_timer = QTimer(self)
        self.tick_timer.setInterval(1000)
        self.tick_timer.timeout.connect(self._trading_tick)
        self._build_ui()
        self._set_status("RED", "-")

    def _build_ui(self):
        self.setWindowTitle("SimplB Trading Panel")
        self.setFixedSize(700, 350)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        mono = QFont("Consolas", 12)
        mono.setStyleHint(QFont.Monospace)

        self.setStyleSheet(
            "QMainWindow, QWidget { background-color: #000000; color: #ffffff; }"
            "QLabel { color: #ffffff; }"
            "QPushButton { background-color: #111111; color: #ffffff; border: 1px solid #22ff66; padding: 4px 10px; }"
            "QPushButton:hover { background-color: #1a1a1a; }"
        )

        self.top_status = QLabel("STATUS ● RED | EURIUSDT | -ms")
        self.top_status.setFont(mono)
        layout.addWidget(self.top_status)
        self.api_status = "NO KEYS"

        self.value_labels = {}
        layout.addLayout(self._row(mono, "BID", "bid", "ASK", "ask"))
        layout.addLayout(self._row(mono, "MID", "mid", "SPR", "spr"))
        layout.addLayout(self._row(mono, "USDT", "usdt", "EURI", "euri"))

        state_row = QHBoxLayout()
        state_title = QLabel("STATE:")
        state_title.setFont(mono)
        self.value_labels["state"] = QLabel("IDLE")
        self.value_labels["state"].setFont(mono)
        self.value_labels["state"].setStyleSheet("color: #22ff66;")
        state_row.addWidget(state_title)
        state_row.addWidget(self.value_labels["state"])
        state_row.addStretch()
        layout.addLayout(state_row)

        error_row = QHBoxLayout()
        error_title = QLabel("ERROR:")
        error_title.setFont(mono)
        self.value_labels["error"] = QLabel("-")
        self.value_labels["error"].setFont(mono)
        self.value_labels["error"].setStyleSheet("color: #ff4d4d;")
        error_row.addWidget(error_title)
        error_row.addWidget(self.value_labels["error"])
        error_row.addStretch()
        layout.addLayout(error_row)

        layout.addLayout(self._row(mono, "ENTRY", "entry", "EXIT", "exit"))
        pnl_row = QHBoxLayout()
        pnl_title = QLabel("PnL:")
        pnl_title.setFont(mono)
        self.value_labels["pnl"] = QLabel("0.000000")
        self.value_labels["pnl"].setFont(mono)
        self.value_labels["pnl"].setStyleSheet("color: #22ff66;")
        self.value_labels["order"] = QLabel("ORD: -")
        self.value_labels["order"].setFont(mono)
        pnl_row.addWidget(pnl_title)
        pnl_row.addWidget(self.value_labels["pnl"])
        pnl_row.addStretch()
        pnl_row.addWidget(self.value_labels["order"])
        layout.addLayout(pnl_row)
        trade_info_row = QHBoxLayout()
        self.value_labels["trade_info"] = QLabel("SIZE: 10.00 USDT | MIN: - | TICK: -")
        self.value_labels["trade_info"].setFont(mono)
        trade_info_row.addWidget(self.value_labels["trade_info"])
        trade_info_row.addStretch()
        layout.addLayout(trade_info_row)

        buttons = QHBoxLayout()
        self.start_btn = QPushButton("START")
        self.stop_btn = QPushButton("STOP")
        self.settings_btn = QPushButton("SETTINGS")
        self.trade_btn = QPushButton("TRADE")
        self.kill_btn = QPushButton("KILL")
        for btn in [self.start_btn, self.stop_btn, self.settings_btn, self.trade_btn, self.kill_btn]:
            buttons.addWidget(btn)
        layout.addLayout(buttons)

        self.start_btn.clicked.connect(self.start_monitoring)
        self.stop_btn.clicked.connect(self.stop_monitoring)
        self.settings_btn.clicked.connect(self.open_settings)
        self.trade_btn.clicked.connect(self.start_trading)
        self.kill_btn.clicked.connect(self.emergency_stop)

        self.setCentralWidget(root)

    def _row(self, mono: QFont, left_title: str, left_key: str, right_title: str, right_key: str):
        row = QHBoxLayout()
        for title, key in [(left_title, left_key), (right_title, right_key)]:
            t = QLabel(f"{title}")
            t.setFont(mono)
            v = QLabel("-")
            v.setFont(mono)
            v.setStyleSheet("color: #22ff66;")
            self.value_labels[key] = v
            row.addWidget(t)
            row.addWidget(v)
            row.addStretch()
        return row

    def _set_status(self, status: str, latency: str):
        color_dot = "🟢" if status == "GREEN" else ("🟡" if status == "YELLOW" else "🔴")
        self.top_status.setText(f"STATUS {color_dot} {status} | {self.symbol} | {latency}ms")

    def _update_trading_ui(self, state: str, error: str = ""):
        self.trading_state = state
        self.failure_reason = error
        self.value_labels["state"].setText(state)
        self.value_labels["error"].setText(error or "-")
        self.value_labels["entry"].setText(f"{self.entry_price:.6f}" if self.entry_price else "-")
        self.value_labels["exit"].setText(f"{self.exit_price:.6f}" if self.exit_price else "-")
        self.value_labels["pnl"].setText(f"{self.realized_pnl:+.6f}")
        ord_short = str(self.active_order_id)
        if len(ord_short) > 8:
            ord_short = ord_short[-8:]
        self.value_labels["order"].setText(f"ORD: {ord_short}")
        if error:
            self.value_labels["state"].setStyleSheet("color: #ff4d4d;")
        else:
            self.value_labels["state"].setStyleSheet("color: #22ff66;")

    def start_trading(self):
        if self.trading_state in {"PLACING_BUY", "WAIT_BUY", "PLACING_SELL", "WAIT_SELL"}:
            return
        try:
            self._reset_cycle_values()
            self.current_cycle += 1
            self.last_action = "START_TRADING"
            self._check_safety()
            self._place_buy()
            self.tick_timer.start()
        except Exception as exc:
            self._fail(str(exc))

    def _reset_cycle_values(self):
        self.active_order_id = "-"
        self.entry_price = 0.0
        self.exit_price = 0.0
        self.qty = 0.0
        self.filled_qty = 0.0
        self.realized_pnl = 0.0
        self.buy_quote_total = 0.0
        self.sell_quote_total = 0.0
        self.buy_started_at = 0
        self.sell_started_at = 0
        self._update_trading_ui("IDLE")

    def _fail(self, reason: str):
        self.tick_timer.stop()
        self.logger.error("FAILED %s", reason)
        self._update_trading_ui("FAILED", reason)

    def _check_safety(self):
        self._update_trading_ui("IDLE")
        api_key = str(self.cfg.get("api_key", ""))
        secret = str(self.cfg.get("secret", ""))
        if not api_key or not secret:
            raise RuntimeError("API keys missing")
        if self.api_status != "CONNECTED":
            raise RuntimeError("Account not connected")
        info = self.api.get_exchange_info(self.symbol).data["symbols"][0]
        for f in info["filters"]:
            if f["filterType"] == "PRICE_FILTER":
                self.filters["tick_size"] = float(f["tickSize"])
            if f["filterType"] == "LOT_SIZE":
                self.filters["step_size"] = float(f["stepSize"])
            if f["filterType"] == "MIN_NOTIONAL":
                self.filters["min_notional"] = float(f["minNotional"])
        bid = float(self.last_book.get("bidPrice", "0") or 0)
        ask = float(self.last_book.get("askPrice", "0") or 0)
        if bid <= 0 or ask <= 0:
            raise RuntimeError("no bid/ask")
        self.entry_price = round_price_to_tick(bid, self.filters["tick_size"])
        order_size_usdt = float(self.cfg.get("order_size_usdt", 10))
        self.order_size_usdt = order_size_usdt
        self._update_trade_info_label()
        if order_size_usdt < self.filters["min_notional"]:
            raise RuntimeError(f"order_size < minNotional: {order_size_usdt:g} < {self.filters['min_notional']:g}")
        self.qty = round_qty_to_step(order_size_usdt / self.entry_price, self.filters["step_size"])
        if self.qty <= 0:
            raise RuntimeError("qty=0 after stepSize")
        if not validate_min_notional(self.qty, self.entry_price, self.filters["min_notional"]):
            raise RuntimeError("minNotional after rounding")
        balances = self.api.get_balances(str(self.cfg.get("api_key", "")), str(self.cfg.get("secret", "")), ["USDT"])
        if balances.get("USDT", {}).get("free", 0.0) < order_size_usdt:
            raise RuntimeError("insufficient balance")

    def _place_buy(self):
        self._update_trading_ui("PLACING_BUY")
        try:
            r = self.api.place_limit_buy(self.symbol, self.qty, self.entry_price, str(self.cfg.get("api_key", "")), str(self.cfg.get("secret", ""))).data
        except Exception as exc:
            raise RuntimeError(f"buy rejected: {exc}") from exc
        self.active_order_id = r.get("orderId", "-")
        self.buy_started_at = int(time.time())
        self.logger.info("BUY SENT id=%s qty=%s price=%s", self.active_order_id, self.qty, self.entry_price)
        self._update_trading_ui("WAIT_BUY")

    def _place_sell(self):
        self._update_trading_ui("PLACING_SELL")
        try:
            r = self.api.place_limit_sell(self.symbol, self.qty, self.exit_price, str(self.cfg.get("api_key", "")), str(self.cfg.get("secret", ""))).data
        except Exception as exc:
            raise RuntimeError(f"sell rejected: {exc}") from exc
        self.active_order_id = r.get("orderId", "-")
        self.sell_started_at = int(time.time())
        self.logger.info("SELL SENT id=%s qty=%s price=%s", self.active_order_id, self.qty, self.exit_price)
        self._update_trading_ui("WAIT_SELL")

    def _trading_tick(self):
        if self.trading_state not in {"WAIT_BUY", "WAIT_SELL"}:
            return
        try:
            o = self.api.get_order(self.symbol, int(self.active_order_id), str(self.cfg.get("api_key", "")), str(self.cfg.get("secret", ""))).data
        except Exception as exc:
            self._fail(f"api timeout: {exc}")
            return
        self.filled_qty = float(o.get("executedQty", "0") or 0)
        status = o.get("status")
        if status == "PARTIALLY_FILLED":
            return
        if status == "CANCELED":
            self._fail("order canceled")
            return
        if status != "FILLED":
            now = int(time.time())
            if self.trading_state == "WAIT_BUY" and self.buy_started_at and now - self.buy_started_at > 20:
                self.api.cancel_order(self.symbol, int(self.active_order_id), str(self.cfg.get("api_key", "")), str(self.cfg.get("secret", "")))
                self._fail("buy timeout")
            if self.trading_state == "WAIT_SELL" and self.sell_started_at and now - self.sell_started_at > 30:
                self.api.cancel_order(self.symbol, int(self.active_order_id), str(self.cfg.get("api_key", "")), str(self.cfg.get("secret", "")))
                self._fail("sell timeout")
            return
        if self.trading_state == "WAIT_BUY":
            quote = float(o.get("cummulativeQuoteQty", "0") or 0)
            self.buy_quote_total = quote
            self.entry_price = quote / max(self.filled_qty, 1e-12)
            self.exit_price = round_price_to_tick(self.entry_price + (self.filters["tick_size"] * self.step_ticks), self.filters["tick_size"])
            self.logger.info("BUY FILLED id=%s qty=%s quote=%s", self.active_order_id, self.filled_qty, self.buy_quote_total)
            try:
                self._place_sell()
            except Exception as exc:
                self._fail(str(exc))
        else:
            quote = float(o.get("cummulativeQuoteQty", "0") or 0)
            self.sell_quote_total = quote
            self.realized_pnl = self.sell_quote_total - self.buy_quote_total
            self.logger.info("SELL FILLED id=%s qty=%s quote=%s pnl=%s", self.active_order_id, self.filled_qty, self.sell_quote_total, self.realized_pnl)
            self.tick_timer.stop()
            self._update_trading_ui("DONE")

    def stop_trading(self):
        self.tick_timer.stop()
        if str(self.active_order_id).isdigit():
            self.api.cancel_order(self.symbol, int(self.active_order_id), str(self.cfg.get("api_key", "")), str(self.cfg.get("secret", "")))
        self._update_trading_ui("STOPPED")

    def emergency_stop(self):
        self.tick_timer.stop()
        try:
            orders = self.api.get_open_orders(self.symbol, str(self.cfg.get("api_key", "")), str(self.cfg.get("secret", ""))).data
            for o in orders:
                self.api.cancel_order(self.symbol, int(o["orderId"]), str(self.cfg.get("api_key", "")), str(self.cfg.get("secret", "")))
        finally:
            self._update_trading_ui("STOPPED")

    def open_settings(self):
        d = SettingsDialog(self, self.cfg, self.api)
        if d.exec():
            self.cfg = load_config()
            self.symbol = str(self.cfg.get("symbol", "EURIUSDT"))
            self.step_ticks = int(self.cfg.get("step_ticks", 1))
            self.order_size_usdt = float(self.cfg.get("order_size_usdt", 10))
            self._update_trade_info_label()

    def start_monitoring(self):
        if self.worker_thread and self.worker_thread.isRunning():
            return
        self.worker_thread = QThread(self)
        self.worker = MarketWorker(
            self.symbol,
            self.api.current_endpoint,
            str(self.cfg.get("api_key", "")),
            str(self.cfg.get("secret", "")),
            int(self.cfg.get("poll_ms", 1000)),
            int(self.cfg.get("account_poll_ms", 5000)),
        )
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.start)
        self.stop_worker_signal.connect(self.worker.stop)
        self.worker.data_ready.connect(self.on_market_data)
        self.worker.account_ready.connect(self.on_account_data)
        self.worker.error.connect(self.on_worker_error)
        self.worker.account_error.connect(self.on_account_error)
        self.worker_thread.start()

    def stop_monitoring(self):
        if self.worker_thread and self.worker:
            self.stop_worker_signal.emit()
            self.worker_thread.quit()
            self.worker_thread.wait(2000)
            self.worker_thread = None
            self.worker = None

    def on_market_data(self, payload):
        self.last_book = payload["book"]
        bid = float(self.last_book.get("bidPrice", 0.0))
        ask = float(self.last_book.get("askPrice", 0.0))
        mid = (bid + ask) / 2 if bid and ask else 0.0
        spr = ask - bid if bid and ask else 0.0
        spr_pct = (spr / mid * 100) if mid else 0.0

        self._set_status(payload.get("status", "RED"), str(payload.get("latency_ms", "-")))
        self.value_labels["bid"].setText(f"{bid:.6f}" if bid else "-")
        self.value_labels["ask"].setText(f"{ask:.6f}" if ask else "-")
        self.value_labels["mid"].setText(f"{mid:.6f}" if mid else "-")
        self.value_labels["spr"].setText(f"{spr:.6f} ({spr_pct:.2f}%)" if mid else "-")

    def on_account_data(self, payload):
        status = payload.get("status", "UNKNOWN")
        bal = payload.get("balances", {})
        usdt = bal.get("USDT", {})
        euri = bal.get("EURI", {})
        self.value_labels["usdt"].setText(f"{usdt.get('total', 0.0):.2f}")
        self.value_labels["euri"].setText(f"{euri.get('total', 0.0):.2f}")
        self.api_status = status

    def on_worker_error(self, m):
        self.logger.error(m)
        self._set_status("RED", "-")
        self.value_labels["state"].setText("ERROR")
        self.value_labels["state"].setStyleSheet("color: #ff4d4d;")

    def on_account_error(self, m):
        self.logger.error(m)
        self.value_labels["state"].setText("ERROR")
        self.value_labels["state"].setStyleSheet("color: #ff4d4d;")

    def _update_trade_info_label(self):
        min_notional = self.filters.get("min_notional")
        tick_size = self.filters.get("tick_size")
        min_txt = f"{min_notional:g}" if min_notional else "-"
        tick_txt = f"{tick_size:g}" if tick_size else "-"
        self.value_labels["trade_info"].setText(f"SIZE: {self.order_size_usdt:.2f} USDT | MIN: {min_txt} | TICK: {tick_txt}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = SimplBWindow()
    w.show()
    sys.exit(app.exec())
