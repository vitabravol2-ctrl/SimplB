import sys
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

    def __init__(self, symbol: str, endpoint: str, api_key: str = "", secret: str = ""):
        super().__init__()
        self.symbol = symbol
        self.api_key = api_key
        self.secret = secret
        self.api = BinanceAPI()
        if endpoint in self.api.endpoints:
            self.api.current_endpoint = endpoint
            self.api.current_idx = self.api.endpoints.index(endpoint)
        self.running = False
        self.consecutive_errors = 0
        self.had_error = False
        self.market_timer = None
        self.account_timer = None

    @Slot()
    def start(self):
        if self.running:
            return
        self.running = True
        self.market_timer = QTimer(self)
        self.market_timer.setInterval(1000)
        self.market_timer.timeout.connect(self.poll_market)
        self.market_timer.start()
        self.account_timer = QTimer(self)
        self.account_timer.setInterval(5000)
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

    @Slot(str, str, str)
    def update_settings(self, symbol: str, api_key: str, secret: str):
        self.symbol = symbol
        self.api_key = api_key
        self.secret = secret

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
        form.addRow("API KEY", self.api_key_input)
        form.addRow("SECRET", self.secret_input)
        form.addRow("SYMBOL", self.symbol_input)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        save_btn = QPushButton("SAVE")
        save_btn.clicked.connect(self.handle_save)
        test_btn = QPushButton("TEST CONNECTION")
        test_btn.clicked.connect(self.handle_test)
        buttons.addWidget(save_btn)
        buttons.addWidget(test_btn)
        layout.addLayout(buttons)

    def handle_save(self):
        self.cfg["api_key"] = self.api_key_input.text().strip()
        self.cfg["secret"] = self.secret_input.text().strip()
        self.cfg["symbol"] = self.symbol_input.text().strip().upper() or "EURIUSDT"
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
        self.current_cycle = 0
        self.last_action = "-"
        self.filters: Dict[str, float] = {}
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

        buttons = QHBoxLayout()
        self.start_btn = QPushButton("START")
        self.stop_btn = QPushButton("STOP")
        self.trade_btn = QPushButton("TRADE")
        self.kill_btn = QPushButton("KILL")
        for btn in [self.start_btn, self.stop_btn, self.trade_btn, self.kill_btn]:
            buttons.addWidget(btn)
        layout.addLayout(buttons)

        self.start_btn.clicked.connect(self.start_monitoring)
        self.stop_btn.clicked.connect(self.stop_monitoring)
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
        self.value_labels["state"].setText(state)
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
        try:
            self._check_safety()
            self.current_cycle += 1
            self.last_action = "START_TRADING"
            bid = float(self.last_book.get("bidPrice", "0"))
            self.entry_price = round_price_to_tick(bid, self.filters["tick_size"])
            order_size_usdt = float(self.cfg.get("order_size_usdt", 10))
            self.qty = round_qty_to_step(order_size_usdt / self.entry_price, self.filters["step_size"])
            if self.qty <= 0 or not validate_min_notional(self.qty, self.entry_price, self.filters["min_notional"]):
                raise RuntimeError("minNotional violation after rounding")
            self._place_buy()
            self.tick_timer.start()
        except Exception as exc:
            self.logger.error("ERROR %s", exc)
            self._update_trading_ui("ERROR", str(exc))

    def _check_safety(self):
        self._update_trading_ui("CHECKING")
        api_key = str(self.cfg.get("api_key", ""))
        secret = str(self.cfg.get("secret", ""))
        if not api_key or not secret:
            raise RuntimeError("API keys missing")
        if self.symbol != "EURIUSDT":
            raise RuntimeError("Only EURIUSDT allowed")
        if self.api_status != "CONNECTED":
            raise RuntimeError("Account not connected")
        if float(self.value_labels["usdt"].text() or 0) < float(self.cfg.get("order_size_usdt", 10)):
            raise RuntimeError("USDT balance too low")
        info = self.api.get_exchange_info(self.symbol).data["symbols"][0]
        for f in info["filters"]:
            if f["filterType"] == "PRICE_FILTER":
                self.filters["tick_size"] = float(f["tickSize"])
            if f["filterType"] == "LOT_SIZE":
                self.filters["step_size"] = float(f["stepSize"])
            if f["filterType"] == "MIN_NOTIONAL":
                self.filters["min_notional"] = float(f["minNotional"])

    def _place_buy(self):
        r = self.api.place_limit_buy(self.symbol, self.qty, self.entry_price, str(self.cfg.get("api_key", "")), str(self.cfg.get("secret", ""))).data
        self.active_order_id = r.get("orderId", "-")
        self._update_trading_ui("BUY")

    def _place_sell(self):
        r = self.api.place_limit_sell(self.symbol, self.qty, self.exit_price, str(self.cfg.get("api_key", "")), str(self.cfg.get("secret", ""))).data
        self.active_order_id = r.get("orderId", "-")
        self._update_trading_ui("SELL")

    def _trading_tick(self):
        if self.trading_state not in {"BUY", "SELL"}:
            return
        o = self.api.get_order(self.symbol, int(self.active_order_id), str(self.cfg.get("api_key", "")), str(self.cfg.get("secret", ""))).data
        self.filled_qty = float(o.get("executedQty", "0") or 0)
        if o.get("status") != "FILLED":
            return
        if self.trading_state == "BUY":
            quote = float(o.get("cummulativeQuoteQty", "0") or 0)
            self.entry_price = quote / max(self.filled_qty, 1e-12)
            self.exit_price = round_price_to_tick(self.entry_price + self.filters["tick_size"], self.filters["tick_size"])
            self._place_sell()
        else:
            quote = float(o.get("cummulativeQuoteQty", "0") or 0)
            self.realized_pnl = quote - (self.entry_price * self.filled_qty)
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
            self._update_trading_ui("KILLED")

    def open_settings(self):
        d = SettingsDialog(self, self.cfg, self.api)
        if d.exec():
            self.cfg = load_config()
            self.symbol = str(self.cfg.get("symbol", "EURIUSDT"))

    def start_monitoring(self):
        if self.worker_thread and self.worker_thread.isRunning():
            return
        self.worker_thread = QThread(self)
        self.worker = MarketWorker(self.symbol, self.api.current_endpoint, str(self.cfg.get("api_key", "")), str(self.cfg.get("secret", "")))
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


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = SimplBWindow()
    w.show()
    sys.exit(app.exec())
