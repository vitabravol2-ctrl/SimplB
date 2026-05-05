import sys
import time
from collections import deque
from typing import Dict, Optional

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.binance_api import BinanceAPI
from core.config import load_config, save_config_values
from core.logger import setup_logger
from core.trading_utils import round_price_to_tick, round_qty_to_step, validate_min_notional


class MarketWorker(QObject):
    data_ready = Signal(dict)
    account_ready = Signal(dict)
    error = Signal(str)
    account_error = Signal(str)

    def __init__(self, symbol: str, endpoint: str, api_key: str = "", secret: str = "", poll_ms: int = 1000, account_poll_ms: int = 5000):
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
        self.market_timer = None
        self.account_timer = None

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

    @Slot()
    def poll_market(self):
        if not self.running:
            return
        try:
            book = self.api.get_book_ticker(self.symbol)
            stat = self.api.get_24hr(self.symbol)
            self.data_ready.emit({"symbol": self.symbol, "latency_ms": book.latency_ms + stat.latency_ms, "status": "GREEN", "book": book.data})
        except Exception as exc:
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
        except Exception as exc:
            self.account_error.emit(str(exc))


class SettingsDialog(QDialog):
    def __init__(self, parent, cfg, api, test_callback):
        super().__init__(parent)
        self.cfg = cfg
        self.api = api
        self.test_callback = test_callback
        self.setWindowTitle("Settings")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.api_key_input = QLineEdit(str(cfg.get("api_key", "")))
        self.secret_input = QLineEdit(str(cfg.get("secret", "")))
        self.secret_input.setEchoMode(QLineEdit.Password)
        self.symbol_input = QLineEdit("EURIUSDT")
        self.poll_ms_input = QLineEdit(str(cfg.get("poll_ms", 1000)))
        self.account_poll_ms_input = QLineEdit(str(cfg.get("account_poll_ms", 5000)))
        self.budget_input = QLineEdit(str(cfg.get("budget_usdt", 100)))
        self.order_size_input = QLineEdit(str(cfg.get("order_size_usdt", 10)))
        self.step_ticks_input = QLineEdit(str(cfg.get("step_ticks", 1)))
        self.buy_timeout_input = QLineEdit(str(cfg.get("buy_timeout_sec", 30)))
        self.sell_timeout_input = QLineEdit(str(cfg.get("sell_timeout_sec", 60)))
        self.reprice_enabled = QCheckBox()
        self.reprice_enabled.setChecked(bool(cfg.get("reprice_enabled", True)))
        self.reprice_after_input = QLineEdit(str(cfg.get("reprice_after_sec", 10)))
        self.max_cycles_input = QLineEdit(str(cfg.get("max_cycles", 0)))
        self.stop_after_loss = QCheckBox()
        self.stop_after_loss.setChecked(bool(cfg.get("stop_after_loss", False)))

        form.addRow("API KEY", self.api_key_input)
        form.addRow("SECRET", self.secret_input)
        form.addRow("Symbol", self.symbol_input)
        form.addRow("Poll ms", self.poll_ms_input)
        form.addRow("Account poll ms", self.account_poll_ms_input)
        form.addRow("Budget USDT", self.budget_input)
        form.addRow("Order size USDT", self.order_size_input)
        form.addRow("Step ticks", self.step_ticks_input)
        form.addRow("Buy timeout sec", self.buy_timeout_input)
        form.addRow("Sell timeout sec", self.sell_timeout_input)
        form.addRow("Reprice enabled", self.reprice_enabled)
        form.addRow("Reprice after sec", self.reprice_after_input)
        form.addRow("Max cycles (0=inf)", self.max_cycles_input)
        form.addRow("Stop after loss", self.stop_after_loss)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        save_btn = QPushButton("SAVE")
        save_btn.clicked.connect(self.handle_save)
        test_btn = QPushButton("TEST API")
        test_btn.clicked.connect(self.handle_test)
        close_btn = QPushButton("CLOSE")
        close_btn.clicked.connect(self.reject)
        buttons.addWidget(save_btn)
        buttons.addWidget(test_btn)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

    def handle_save(self):
        try:
            self.cfg.update(
                {
                    "api_key": self.api_key_input.text().strip(),
                    "secret": self.secret_input.text().strip(),
                    "symbol": "EURIUSDT",
                    "poll_ms": max(200, int(self.poll_ms_input.text().strip() or "1000")),
                    "account_poll_ms": max(1000, int(self.account_poll_ms_input.text().strip() or "5000")),
                    "budget_usdt": float(self.budget_input.text().strip() or "100"),
                    "order_size_usdt": float(self.order_size_input.text().strip() or "10"),
                    "step_ticks": int(self.step_ticks_input.text().strip() or "1"),
                    "buy_timeout_sec": int(self.buy_timeout_input.text().strip() or "30"),
                    "sell_timeout_sec": int(self.sell_timeout_input.text().strip() or "60"),
                    "reprice_enabled": self.reprice_enabled.isChecked(),
                    "reprice_after_sec": int(self.reprice_after_input.text().strip() or "10"),
                    "max_cycles": int(self.max_cycles_input.text().strip() or "0"),
                    "stop_after_loss": self.stop_after_loss.isChecked(),
                }
            )
        except ValueError:
            QMessageBox.critical(self, "Settings", "Invalid numeric value")
            return
        save_config_values(self.cfg)
        self.accept()

    def handle_test(self):
        self.test_callback(self.api_key_input.text().strip(), self.secret_input.text().strip())


class SimplBWindow(QMainWindow):
    stop_worker_signal = Signal()

    def __init__(self):
        super().__init__()
        self.logger = setup_logger()
        self.cfg = load_config()
        self.api = BinanceAPI()
        self.symbol = "EURIUSDT"
        self.worker_thread = None
        self.worker = None
        self.last_book = {}
        self.filters: Dict[str, float] = {}
        self.logs = deque(maxlen=20)
        self.state = "IDLE"
        self.cycle = 0
        self.bot_running = False
        self.stop_requested = False
        self.active_order_id: Optional[int] = None
        self.active_side = ""
        self.entry_avg = 0.0
        self.exit_price = 0.0
        self.qty = 0.0
        self.filled_qty = 0.0
        self.buy_quote = 0.0
        self.sell_quote = 0.0
        self.pnl_total = 0.0
        self.last_error = "-"
        self.order_started_at = 0
        self.tick_timer = QTimer(self)
        self.tick_timer.setInterval(1000)
        self.tick_timer.timeout.connect(self._fsm_tick)
        self._build_ui()
        self._fetch_exchange_info()

    def _build_ui(self):
        self.setWindowTitle("SimplB v0.4.0")
        self.setFixedSize(920, 560)
        root = QWidget()
        layout = QVBoxLayout(root)
        mono = QFont("Consolas", 10)
        self.value_labels = {}
        grid = QGridLayout()
        keys = ["STATE", "CYCLE", "SIZE", "MIN NOTIONAL", "TICK", "STEP", "ENTRY", "EXIT", "QTY", "FILLED", "PNL TOTAL", "LAST ERROR"]
        for i, k in enumerate(keys):
            grid.addWidget(QLabel(k), i, 0)
            v = QLabel("-")
            v.setFont(mono)
            self.value_labels[k] = v
            grid.addWidget(v, i, 1)
        layout.addLayout(grid)
        btns = QHBoxLayout()
        for txt, fn in [
            ("START MONITOR", self.start_monitoring),
            ("STOP MONITOR", self.stop_monitoring),
            ("SETTINGS", self.open_settings),
            ("START BOT", self.start_bot),
            ("STOP BOT", self.stop_bot),
            ("KILL", self.kill_bot),
        ]:
            b = QPushButton(txt)
            b.clicked.connect(fn)
            btns.addWidget(b)
        layout.addLayout(btns)
        self.log_panel = QTextEdit()
        self.log_panel.setReadOnly(True)
        layout.addWidget(self.log_panel)
        self.setCentralWidget(root)

    def _log(self, level: str, event: str, reason: str = ""):
        stamp = time.strftime("%H:%M:%S")
        msg = f"[{stamp}] {level} {event} {reason}".strip()
        self.logs.append(msg)
        self.log_panel.setPlainText("\n".join(self.logs))
        if level == "ERROR":
            self.logger.error(msg)
        else:
            self.logger.info(msg)

    def _update_status(self):
        self.value_labels["STATE"].setText(self.state)
        self.value_labels["CYCLE"].setText(str(self.cycle))
        self.value_labels["SIZE"].setText(str(self.cfg.get("order_size_usdt", 10)))
        self.value_labels["MIN NOTIONAL"].setText(str(self.filters.get("min_notional", "-")))
        self.value_labels["TICK"].setText(str(self.filters.get("tick_size", "-")))
        self.value_labels["STEP"].setText(str(self.filters.get("step_size", "-")))
        self.value_labels["ENTRY"].setText(f"{self.entry_avg:.6f}" if self.entry_avg else "-")
        self.value_labels["EXIT"].setText(f"{self.exit_price:.6f}" if self.exit_price else "-")
        self.value_labels["QTY"].setText(f"{self.qty:.6f}" if self.qty else "-")
        self.value_labels["FILLED"].setText(f"{self.filled_qty:.6f}" if self.filled_qty else "-")
        self.value_labels["PNL TOTAL"].setText(f"{self.pnl_total:+.6f}")
        self.value_labels["LAST ERROR"].setText(self.last_error)

    def _set_failed(self, reason: str):
        self.last_error = reason
        self.state = "FAILED"
        self.bot_running = False
        self.tick_timer.stop()
        self._log("ERROR", "FAILED", reason)
        self._update_status()

    def _fetch_exchange_info(self):
        try:
            info = self.api.get_exchange_info(self.symbol).data["symbols"][0]
            for f in info["filters"]:
                if f["filterType"] == "PRICE_FILTER":
                    self.filters["tick_size"] = float(f["tickSize"])
                elif f["filterType"] == "LOT_SIZE":
                    self.filters["step_size"] = float(f["stepSize"])
                elif f["filterType"] == "MIN_NOTIONAL":
                    self.filters["min_notional"] = float(f["minNotional"])
            self._log("INFO", "CHECK", f"ok minNotional={self.filters.get('min_notional')} tick={self.filters.get('tick_size')} step={self.filters.get('step_size')}")
        except Exception:
            self._log("ERROR", "EXCHANGE_INFO_MISSING")
        self._update_status()

    def _binance_error(self, action: str, endpoint: str, exc: Exception):
        self.last_error = f"action={action} endpoint={endpoint} code=- msg={exc}"
        self._log("ERROR", "BINANCE", self.last_error)

    def _fsm_tick(self):
        if not self.bot_running:
            return
        try:
            if self.state == "NEXT_CYCLE":
                self._state_next_cycle()
            elif self.state == "CHECKING":
                self._state_checking()
            elif self.state == "PLACE_BUY":
                self._state_place_buy()
            elif self.state == "WAIT_BUY":
                self._state_wait_buy()
            elif self.state == "PLACE_SELL":
                self._state_place_sell()
            elif self.state == "WAIT_SELL":
                self._state_wait_sell()
        except Exception as exc:
            self._set_failed(str(exc))


    def _state_next_cycle(self):
        self._log("INFO", "NEXT_CYCLE")
        max_cycles = int(self.cfg.get("max_cycles", 0))
        if self.stop_requested:
            self.state = "STOPPED"
            self.bot_running = False
        elif max_cycles > 0 and self.cycle >= max_cycles:
            self.state = "STOPPED"
            self.bot_running = False
        else:
            self.cycle += 1
            self.state = "CHECKING"
        self._update_status()

    def _state_checking(self):
        self.state = "CHECKING"
        if not self.filters:
            raise RuntimeError("EXCHANGE_INFO_MISSING")
        if not self.cfg.get("api_key") or not self.cfg.get("secret"):
            raise RuntimeError("API keys missing")
        bal = self.api.get_balances(self.cfg["api_key"], self.cfg["secret"], ["USDT"])
        if bal["USDT"]["free"] < float(self.cfg.get("order_size_usdt", 10)):
            raise RuntimeError("insufficient USDT")
        self.state = "PLACE_BUY"
        self._update_status()

    def _state_place_buy(self):
        bid = float(self.last_book.get("bidPrice", "0") or 0)
        if bid <= 0:
            raise RuntimeError("no bid")
        self.entry_avg = round_price_to_tick(bid, self.filters["tick_size"])
        self.qty = round_qty_to_step(float(self.cfg.get("order_size_usdt", 10)) / self.entry_avg, self.filters["step_size"])
        if not validate_min_notional(self.qty, self.entry_avg, self.filters["min_notional"]):
            raise RuntimeError("minNotional invalid")
        r = self.api.place_limit_buy(self.symbol, self.qty, self.entry_avg, self.cfg["api_key"], self.cfg["secret"]).data
        self.active_order_id = int(r["orderId"])
        self.active_side = "BUY"
        self.order_started_at = int(time.time())
        self.state = "WAIT_BUY"
        self._log("INFO", "BUY_SENT", f"price={self.entry_avg} qty={self.qty} id={self.active_order_id}")
        self._update_status()

    def _state_wait_buy(self):
        o = self.api.get_order(self.symbol, self.active_order_id, self.cfg["api_key"], self.cfg["secret"]).data
        self.filled_qty = float(o.get("executedQty", 0) or 0)
        st = o.get("status")
        if st == "FILLED":
            self.buy_quote = float(o.get("cummulativeQuoteQty", 0) or 0)
            self.entry_avg = self.buy_quote / max(self.filled_qty, 1e-12)
            self.qty = self.filled_qty
            self.state = "PLACE_SELL"
            self._log("INFO", "BUY_FILLED", f"avg={self.entry_avg:.6f} qty={self.qty}")
        elif st == "PARTIALLY_FILLED":
            self._log("INFO", "BUY_WAIT", f"status=PARTIALLY_FILLED filled={self.filled_qty}")
        elif int(time.time()) - self.order_started_at > int(self.cfg.get("buy_timeout_sec", 30)):
            self.api.cancel_order(self.symbol, self.active_order_id, self.cfg["api_key"], self.cfg["secret"])
            self.state = "PLACE_SELL" if self.filled_qty > 0 else "NEXT_CYCLE"
        self._update_status()

    def _state_place_sell(self):
        self.exit_price = round_price_to_tick(self.entry_avg + self.filters["tick_size"] * int(self.cfg.get("step_ticks", 1)), self.filters["tick_size"])
        r = self.api.place_limit_sell(self.symbol, self.qty, self.exit_price, self.cfg["api_key"], self.cfg["secret"]).data
        self.active_order_id = int(r["orderId"])
        self.active_side = "SELL"
        self.order_started_at = int(time.time())
        self.state = "WAIT_SELL"
        self._log("INFO", "SELL_SENT", f"price={self.exit_price} qty={self.qty} id={self.active_order_id}")
        self._update_status()

    def _state_wait_sell(self):
        o = self.api.get_order(self.symbol, self.active_order_id, self.cfg["api_key"], self.cfg["secret"]).data
        self.filled_qty = float(o.get("executedQty", 0) or 0)
        st = o.get("status")
        if st == "FILLED":
            self.sell_quote = float(o.get("cummulativeQuoteQty", 0) or 0)
            pnl = self.sell_quote - self.buy_quote
            self.pnl_total += pnl
            self._log("INFO", "SELL_FILLED", f"pnl={pnl:+.6f}")
            self.state = "NEXT_CYCLE"
        elif int(time.time()) - self.order_started_at > int(self.cfg.get("sell_timeout_sec", 60)):
            self.api.cancel_order(self.symbol, self.active_order_id, self.cfg["api_key"], self.cfg["secret"])
            if self.cfg.get("reprice_enabled", True):
                ask = float(self.last_book.get("askPrice", "0") or 0)
                self.exit_price = round_price_to_tick(max(ask, self.entry_avg + self.filters["tick_size"]), self.filters["tick_size"])
                self.state = "PLACE_SELL"
            else:
                raise RuntimeError("sell timeout no reprice")
        elif st == "PARTIALLY_FILLED":
            self._log("INFO", "SELL_WAIT", f"status=PARTIALLY_FILLED filled={self.filled_qty}")
        self._update_status()

    def start_bot(self):
        if self.bot_running:
            return
        if not self.filters:
            self._log("ERROR", "EXCHANGE_INFO_MISSING")
            return
        self.bot_running = True
        self.stop_requested = False
        self.cycle = 1
        self.state = "CHECKING"
        self._log("INFO", "START_LOOP", f"size={self.cfg.get('order_size_usdt', 10)}")
        self.tick_timer.start()
        self._update_status()

    def stop_bot(self):
        self.stop_requested = True
        self.bot_running = False
        if self.active_order_id:
            try:
                self.api.cancel_order(self.symbol, self.active_order_id, self.cfg.get("api_key", ""), self.cfg.get("secret", ""))
            except Exception:
                pass
        self.state = "STOPPED"
        self._log("INFO", "STOPPED")
        self._update_status()

    def kill_bot(self):
        self.bot_running = False
        self.tick_timer.stop()
        try:
            orders = self.api.get_open_orders(self.symbol, self.cfg.get("api_key", ""), self.cfg.get("secret", "")).data
            for o in orders:
                self.api.cancel_order(self.symbol, int(o["orderId"]), self.cfg.get("api_key", ""), self.cfg.get("secret", ""))
        except Exception:
            pass
        self.state = "KILLED"
        self._log("INFO", "KILLED")
        self._update_status()

    def open_settings(self):
        d = SettingsDialog(self, self.cfg, self.api, self._test_api)
        if d.exec():
            self.cfg = load_config()
            self._fetch_exchange_info()

    def _test_api(self, api_key: str, secret: str):
        try:
            self.api.test_connection(self.symbol, api_key, secret)
            self._fetch_exchange_info()
            QMessageBox.information(self, "Connection test", "OK")
        except Exception as exc:
            QMessageBox.critical(self, "Connection test", f"ERROR\n{exc}")

    def start_monitoring(self):
        if self.worker_thread and self.worker_thread.isRunning():
            return
        self.worker_thread = QThread(self)
        self.worker = MarketWorker(self.symbol, self.api.current_endpoint, self.cfg.get("api_key", ""), self.cfg.get("secret", ""), int(self.cfg.get("poll_ms", 1000)), int(self.cfg.get("account_poll_ms", 5000)))
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.start)
        self.stop_worker_signal.connect(self.worker.stop)
        self.worker.data_ready.connect(self.on_market_data)
        self.worker.account_ready.connect(self.on_account_data)
        self.worker.error.connect(lambda m: self._log("ERROR", "MONITOR", m))
        self.worker.account_error.connect(lambda m: self._log("ERROR", "ACCOUNT", m))
        self.worker_thread.start()

    def stop_monitoring(self):
        if self.worker_thread and self.worker:
            self.stop_worker_signal.emit()
            self.worker_thread.quit()
            self.worker_thread.wait(2000)
            self.worker_thread = None
            self.worker = None

    def on_market_data(self, payload):
        self.last_book = payload.get("book", {})

    def on_account_data(self, payload):
        pass


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = SimplBWindow()
    w.show()
    sys.exit(app.exec())
