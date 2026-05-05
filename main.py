import sys
from typing import Any, Dict

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
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


class MarketWorker(QObject):
    data_ready = Signal(dict)
    account_ready = Signal(dict)
    error = Signal(str)
    account_error = Signal(str)

    def __init__(self, symbol: str, endpoint: str, api_key: str = "", secret: str = "") -> None:
        super().__init__()
        self.symbol = symbol
        self.api_key = api_key
        self.secret = secret
        self.api = BinanceAPI()
        if endpoint in self.api.endpoints:
            self.api.current_endpoint = endpoint
            self.api.current_idx = self.api.endpoints.index(endpoint)
        self.market_timer: QTimer | None = None
        self.account_timer: QTimer | None = None
        self.running = False
        self.consecutive_errors = 0
        self.had_error = False

    @Slot()
    def start(self) -> None:
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
    def stop(self) -> None:
        self.running = False
        if self.market_timer is not None:
            self.market_timer.stop()
            self.market_timer.deleteLater()
            self.market_timer = None
        if self.account_timer is not None:
            self.account_timer.stop()
            self.account_timer.deleteLater()
            self.account_timer = None

    @Slot(str, str, str)
    def update_settings(self, symbol: str, api_key: str, secret: str) -> None:
        self.symbol = symbol
        self.api_key = api_key
        self.secret = secret

    @Slot()
    def poll_market(self) -> None:
        if not self.running:
            return
        try:
            prev_endpoint = self.api.current_endpoint
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
                    "endpoint": self.api.current_endpoint,
                    "latency_ms": latency,
                    "status": status,
                    "consecutive_errors": self.consecutive_errors,
                    "recovered": recovered,
                    "endpoint_changed": prev_endpoint != self.api.current_endpoint,
                    "book": book.data,
                    "stat": stat.data,
                }
            )
        except HTTPRequestError as exc:
            self.had_error = True
            self.consecutive_errors += 1
            self.error.emit(str(exc))

    @Slot()
    def poll_account(self) -> None:
        if not self.running:
            return
        if not self.api_key or not self.secret:
            self.account_ready.emit({"status": "NO KEYS", "balances": {}})
            return
        try:
            balances = self.api.get_balances(self.api_key, self.secret, assets=["USDT", "EURI"])
            self.account_ready.emit({"status": "CONNECTED", "balances": balances})
        except HTTPRequestError as exc:
            self.account_error.emit(str(exc))


class SettingsDialog(QDialog):
    def __init__(self, parent: QWidget, cfg: Dict[str, str], api: BinanceAPI) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(420)
        self.cfg = cfg
        self.api = api

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        self.api_key_input = QLineEdit(str(cfg.get("api_key", "")))
        self.secret_input = QLineEdit(str(cfg.get("secret", "")))
        self.secret_input.setEchoMode(QLineEdit.Password)
        self.symbol_input = QLineEdit(str(cfg.get("symbol", "EURIUSDT")))

        form.addRow("API KEY", self.api_key_input)
        form.addRow("SECRET", self.secret_input)
        form.addRow("SYMBOL", self.symbol_input)
        layout.addLayout(form)

        btns = QHBoxLayout()
        save = QPushButton("SAVE")
        save.clicked.connect(self.handle_save)
        test = QPushButton("TEST CONNECTION")
        test.clicked.connect(self.handle_test)
        btns.addWidget(save)
        btns.addWidget(test)
        layout.addLayout(btns)

    def handle_save(self) -> None:
        self.cfg["api_key"] = self.api_key_input.text().strip()
        self.cfg["secret"] = self.secret_input.text().strip()
        self.cfg["symbol"] = self.symbol_input.text().strip().upper() or "EURIUSDT"
        save_config_values(self.cfg)
        self.accept()

    def handle_test(self) -> None:
        symbol = self.symbol_input.text().strip().upper() or "EURIUSDT"
        try:
            result = self.api.test_connection(symbol, self.api_key_input.text().strip(), self.secret_input.text().strip())
            account = result.get("account", "SKIPPED")
            QMessageBox.information(
                self,
                "Connection test",
                f"{result['status']}\nendpoint: {result['endpoint']}\nlatency: {result['latency_ms']} ms\naccount: {account}",
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Connection test", f"ERROR\n{exc}\nendpoint: {self.api.current_endpoint}")


class SimplBWindow(QMainWindow):
    stop_worker_signal = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.logger = setup_logger()
        self.cfg = load_config()
        self.api = BinanceAPI()
        saved_endpoint = str(self.cfg.get("current_endpoint", self.api.current_endpoint))
        if saved_endpoint in self.api.endpoints:
            self.api.current_endpoint = saved_endpoint
            self.api.current_idx = self.api.endpoints.index(saved_endpoint)

        self.symbol = str(self.cfg.get("symbol", "EURIUSDT"))
        self.worker_thread: QThread | None = None
        self.worker: MarketWorker | None = None
        self._last_status = "RED"
        self._build_ui()
        self._set_status("RED")

    def _build_ui(self) -> None:
        self.setWindowTitle("SimplB Terminal")
        self.resize(850, 420)
        root = QWidget()
        root.setStyleSheet(
            "QWidget{background:#121417;color:#e8eaed;}QGroupBox{border:1px solid #2a2f36;border-radius:6px;margin-top:8px;padding:8px;}QGroupBox::title{subcontrol-origin:margin;left:8px;padding:0 4px;color:#9aa0a6;}"
        )
        layout = QVBoxLayout(root)

        mono = QFont("Consolas", 10)

        self.top_status = QLabel()
        self.top_status.setFixedHeight(24)
        layout.addWidget(self.top_status)

        middle = QHBoxLayout()
        self.market_labels = {}
        self.stats_labels = {}

        market_box = self._build_group("MARKET", ["BID", "ASK", "MID", "SPREAD", "SPREAD %"], self.market_labels, mono)
        stat_box = self._build_group("24H", ["LAST", "VOLUME", "QUOTE VOL", "CHANGE %"], self.stats_labels, mono)
        middle.addWidget(market_box)
        middle.addWidget(stat_box)
        layout.addLayout(middle)

        self.account_labels = {}
        account_box = self._build_group(
            "ACCOUNT",
            [
                "USDT free", "USDT locked", "USDT total",
                "EURI free", "EURI locked", "EURI total",
                "API STATUS",
            ],
            self.account_labels,
            mono,
        )
        layout.addWidget(account_box)

        buttons = QHBoxLayout()
        self.start_btn = QPushButton("START")
        self.stop_btn = QPushButton("STOP")
        self.settings_btn = QPushButton("SETTINGS")
        self.test_btn = QPushButton("TEST API")
        self.start_btn.clicked.connect(self.start_monitoring)
        self.stop_btn.clicked.connect(self.stop_monitoring)
        self.settings_btn.clicked.connect(self.open_settings)
        self.test_btn.clicked.connect(self.test_api)
        for btn in [self.start_btn, self.stop_btn, self.settings_btn, self.test_btn]:
            buttons.addWidget(btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.setCentralWidget(root)

    def _build_group(self, title: str, rows: list[str], target: Dict[str, QLabel], mono: QFont) -> QGroupBox:
        box = QGroupBox(title)
        grid = QGridLayout(box)
        for i, row in enumerate(rows):
            key = row.lower().replace(" ", "_")
            title_lbl = QLabel(row)
            value = QLabel("-")
            value.setFont(mono)
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            value.setMinimumWidth(160)
            grid.addWidget(title_lbl, i, 0)
            grid.addWidget(value, i, 1)
            target[key] = value
        grid.setColumnStretch(1, 1)
        return box

    def _set_status(self, status: str, latency: str = "-") -> None:
        color = {"GREEN": "#00e676", "YELLOW": "#ffd54f", "RED": "#ff5252"}.get(status, "#ff5252")
        endpoint = self.api.current_endpoint.replace("https://", "")
        self.top_status.setStyleSheet(f"color:{color};font-weight:600;")
        err_count = self.worker.consecutive_errors if self.worker else 0
        self.top_status.setText(f"STATUS | {status} | SYMBOL | {self.symbol} | ENDPOINT | {endpoint} | LATENCY | {latency} | ERRORS | {err_count}")
        self._last_status = status

    def open_settings(self) -> None:
        dlg = SettingsDialog(self, self.cfg, self.api)
        if dlg.exec():
            self.cfg = load_config()
            self.symbol = str(self.cfg.get("symbol", "EURIUSDT"))
            if self.worker:
                self.worker.update_settings(self.symbol, str(self.cfg.get("api_key", "")), str(self.cfg.get("secret", "")))
            self._set_status(self._last_status)

    def start_monitoring(self) -> None:
        if self.worker_thread and self.worker_thread.isRunning():
            return
        self.worker_thread = QThread(self)
        self.worker = MarketWorker(
            self.symbol,
            self.api.current_endpoint,
            str(self.cfg.get("api_key", "")),
            str(self.cfg.get("secret", "")),
        )
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.start)
        self.stop_worker_signal.connect(self.worker.stop)
        self.worker.data_ready.connect(self.on_market_data)
        self.worker.account_ready.connect(self.on_account_data)
        self.worker.error.connect(self.on_worker_error)
        self.worker.account_error.connect(self.on_account_error)
        self.worker_thread.start()
        self.logger.info("start monitoring")

    def stop_monitoring(self) -> None:
        if self.worker_thread and self.worker:
            self.stop_worker_signal.emit()
            self.worker_thread.quit()
            self.worker_thread.wait(2000)
            self.worker_thread = None
            self.worker = None
            self.logger.info("stop monitoring")

    @Slot(dict)
    def on_market_data(self, payload: Dict[str, Any]) -> None:
        endpoint = payload["endpoint"]
        prev_endpoint = self.api.current_endpoint
        self.api.current_endpoint = endpoint
        if endpoint in self.api.endpoints:
            self.api.current_idx = self.api.endpoints.index(endpoint)
        if prev_endpoint != endpoint:
            self.logger.info("endpoint switch %s -> %s", prev_endpoint, endpoint)
        self.cfg["current_endpoint"] = endpoint
        save_config_values(self.cfg)

        bid = float(payload["book"]["bidPrice"])
        ask = float(payload["book"]["askPrice"])
        mid = (bid + ask) / 2
        spread = ask - bid
        spread_pct = (spread / mid * 100) if mid else 0.0

        self.market_labels["bid"].setText(f"{bid:.6f}")
        self.market_labels["ask"].setText(f"{ask:.6f}")
        self.market_labels["mid"].setText(f"{mid:.6f}")
        self.market_labels["spread"].setText(f"{spread:.6f}")
        self.market_labels["spread_%"].setText(f"{spread_pct:.4f}%")

        self.stats_labels["last"].setText(f"{float(payload['stat']['lastPrice']):.6f}")
        self.stats_labels["volume"].setText(f"{float(payload['stat']['volume']):.2f}")
        self.stats_labels["quote_vol"].setText(f"{float(payload['stat']['quoteVolume']):.2f}")
        self.stats_labels["change_%"].setText(f"{float(payload['stat']['priceChangePercent']):.2f}%")

        self._set_status(payload["status"], f"{payload['latency_ms']:.1f} ms")
        if payload.get("recovered"):
            self.logger.info("connection restored")

    @Slot(dict)
    def on_account_data(self, payload: Dict[str, Any]) -> None:
        status = payload.get("status", "UNKNOWN")
        balances = payload.get("balances", {})
        if status == "NO KEYS":
            for key in ["usdt_free", "usdt_locked", "usdt_total", "euri_free", "euri_locked", "euri_total"]:
                self.account_labels[key].setText("-")
            self.account_labels["api_status"].setText("NO KEYS")
            return

        usdt = balances.get("USDT", {})
        euri = balances.get("EURI", {})
        self.account_labels["usdt_free"].setText(f"{usdt.get('free', 0.0):.4f}")
        self.account_labels["usdt_locked"].setText(f"{usdt.get('locked', 0.0):.4f}")
        self.account_labels["usdt_total"].setText(f"{usdt.get('total', 0.0):.4f}")
        self.account_labels["euri_free"].setText(f"{euri.get('free', 0.0):.4f}")
        self.account_labels["euri_locked"].setText(f"{euri.get('locked', 0.0):.4f}")
        self.account_labels["euri_total"].setText(f"{euri.get('total', 0.0):.4f}")
        self.account_labels["api_status"].setText(status)

    @Slot(str)
    def on_worker_error(self, message: str) -> None:
        self._set_status("RED")
        self.logger.error("API error: %s", message)

    @Slot(str)
    def on_account_error(self, message: str) -> None:
        self.account_labels["api_status"].setText("ERROR")
        self.logger.error("account error: %s", message)

    def test_api(self) -> None:
        api_key = str(self.cfg.get("api_key", ""))
        secret = str(self.cfg.get("secret", ""))
        try:
            result = self.api.test_connection(self.symbol, api_key, secret)
            text = f"OK\nendpoint: {result['endpoint']}\nlatency: {result['latency_ms']} ms\naccount: {result.get('account', 'SKIPPED')}"
            QMessageBox.information(self, "TEST API", text)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "TEST API", f"ERROR\n{exc}")

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.stop_monitoring()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = SimplBWindow()
    win.show()
    if bool(win.cfg.get("auto_start", True)) and str(win.cfg.get("monitor_state", "running")) == "running":
        win.start_monitoring()
    sys.exit(app.exec())
