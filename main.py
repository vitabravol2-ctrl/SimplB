import sys
from datetime import datetime
from typing import Any, Dict

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


class MarketWorker(QObject):
    data_ready = Signal(dict)
    error = Signal(str)

    def __init__(self, symbol: str, endpoint: str) -> None:
        super().__init__()
        self.symbol = symbol
        self.api = BinanceAPI()
        if endpoint in self.api.endpoints:
            self.api.current_endpoint = endpoint
            self.api.current_idx = self.api.endpoints.index(endpoint)
        self.timer: QTimer | None = None
        self.running = False
        self.consecutive_errors = 0
        self.had_error = False

    @Slot()
    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self.poll()

    @Slot()
    def stop(self) -> None:
        self.running = False
        if self.timer is not None:
            self.timer.stop()
            self.timer.deleteLater()
            self.timer = None

    @Slot()
    def poll(self) -> None:
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
            payload = {
                "symbol": self.symbol,
                "endpoint": self.api.current_endpoint,
                "latency_ms": latency,
                "status": status,
                "consecutive_errors": self.consecutive_errors,
                "recovered": recovered,
                "endpoint_changed": prev_endpoint != self.api.current_endpoint,
                "book": book.data,
                "stat": stat.data,
                "updated": datetime.now().strftime("%H:%M:%S"),
            }
            self.data_ready.emit(payload)
        except HTTPRequestError as exc:
            self.had_error = True
            self.consecutive_errors += 1
            self.error.emit(str(exc))


class SettingsDialog(QDialog):
    def __init__(self, parent: QWidget, cfg: Dict[str, str], api: BinanceAPI) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.cfg = cfg
        self.api = api

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

        btns = QHBoxLayout()
        save = QPushButton("SAVE")
        save.clicked.connect(self.handle_save)
        btns.addWidget(save)

        test = QPushButton("TEST CONNECTION")
        test.clicked.connect(self.handle_test)
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
            QMessageBox.information(
                self,
                "Connection test",
                f"{result['status']}\nendpoint: {result['endpoint']}\nlatency: {result['latency_ms']} ms",
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Connection test", f"ERROR\n{exc}\nendpoint: {self.api.current_endpoint}")


class SimplBWindow(QMainWindow):
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
        self.resize(640, 360)
        root = QWidget()
        root.setStyleSheet("background-color:#000000;color:#e8ffe8;")
        layout = QVBoxLayout(root)

        mono = QFont("Courier New")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(10)

        self.top_status = QLabel()
        self.top_status.setFont(mono)
        self.top_status.setAlignment(Qt.AlignLeft)
        layout.addWidget(self.top_status)

        grid = QGridLayout()
        self.value_labels: Dict[str, QLabel] = {}
        rows = [
            ("BID", "bid"), ("ASK", "ask"), ("MID", "mid"), ("SPREAD", "spread"), ("SPREAD %", "spread_pct"),
            ("LAST", "last"), ("VOLUME", "volume"), ("QUOTE VOL", "quote_vol"), ("UPDATED", "updated"),
        ]
        for idx, (title, key) in enumerate(rows):
            t = QLabel(title)
            t.setFont(mono)
            v = QLabel("-")
            v.setFont(mono)
            v.setMinimumWidth(160)
            v.setAlignment(Qt.AlignRight)
            grid.addWidget(t, idx, 0)
            grid.addWidget(v, idx, 1)
            self.value_labels[key] = v
        layout.addLayout(grid)

        buttons = QHBoxLayout()
        self.start_btn = QPushButton("START")
        self.start_btn.clicked.connect(self.start_monitoring)
        self.stop_btn = QPushButton("STOP")
        self.stop_btn.clicked.connect(self.stop_monitoring)
        self.settings_btn = QPushButton("SETTINGS")
        self.settings_btn.clicked.connect(self.open_settings)
        buttons.addWidget(self.start_btn)
        buttons.addWidget(self.stop_btn)
        buttons.addWidget(self.settings_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.setCentralWidget(root)

    def _set_status(self, status: str, latency: str = "-") -> None:
        color = {"GREEN": "#00ff66", "YELLOW": "#ffdd44", "RED": "#ff4d4d"}.get(status, "#ff4d4d")
        endpoint = self.api.current_endpoint.replace("https://", "")
        self.top_status.setStyleSheet(f"color:{color};font-weight:600;")
        err_count = self.worker.consecutive_errors if self.worker else 0
        self.top_status.setText(
            f"STATUS: {status} | SYMBOL: {self.symbol} | ENDPOINT: {endpoint} | LATENCY: {latency} | ERRORS: {err_count}"
        )
        self._last_status = status

    def open_settings(self) -> None:
        dlg = SettingsDialog(self, self.cfg, self.api)
        if dlg.exec():
            self.cfg = load_config()
            self.symbol = str(self.cfg.get("symbol", "EURIUSDT"))
            self._set_status(self._last_status)

    def start_monitoring(self) -> None:
        if self.worker_thread is not None and self.worker_thread.isRunning():
            return
        self.worker_thread = QThread(self)
        self.worker = MarketWorker(self.symbol, self.api.current_endpoint)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.start)
        self.worker.data_ready.connect(self.on_market_data)
        self.worker.error.connect(self.on_worker_error)
        self.worker_thread.start()
        self.logger.info("start monitoring symbol=%s endpoint=%s", self.symbol, self.api.current_endpoint)

    def stop_monitoring(self) -> None:
        if self.worker is not None:
            self.worker.stop()
        if self.worker_thread is not None:
            self.worker_thread.quit()
            self.worker_thread.wait(2000)
            self.worker_thread = None
            self.worker = None
            self.logger.info("stop monitoring")

    @Slot(dict)
    def on_market_data(self, payload: Dict[str, Any]) -> None:
        endpoint = payload["endpoint"]
        self.api.current_endpoint = endpoint
        if endpoint in self.api.endpoints:
            self.api.current_idx = self.api.endpoints.index(endpoint)
        self.cfg["current_endpoint"] = endpoint
        save_config_values(self.cfg)

        bid = float(payload["book"]["bidPrice"])
        ask = float(payload["book"]["askPrice"])
        mid = (bid + ask) / 2
        spread = ask - bid
        spread_pct = (spread / bid * 100) if bid else 0.0

        self.value_labels["bid"].setText(f"{bid:.6f}")
        self.value_labels["ask"].setText(f"{ask:.6f}")
        self.value_labels["mid"].setText(f"{mid:.6f}")
        self.value_labels["spread"].setText(f"{spread:.6f}")
        self.value_labels["spread_pct"].setText(f"{spread_pct:.4f}")
        self.value_labels["last"].setText(f"{float(payload['stat']['lastPrice']):.6f}")
        self.value_labels["volume"].setText(str(payload["stat"]["volume"]))
        self.value_labels["quote_vol"].setText(str(payload["stat"]["quoteVolume"]))
        self.value_labels["updated"].setText(payload["updated"])
        self._set_status(payload["status"], f"{payload['latency_ms']:.1f}ms")

        if payload["endpoint_changed"]:
            self.logger.warning("endpoint switched to %s", endpoint)
        if payload["recovered"]:
            self.logger.info("recovered after errors endpoint=%s", endpoint)

    @Slot(str)
    def on_worker_error(self, message: str) -> None:
        self._set_status("RED")
        self.logger.error("worker error symbol=%s endpoint=%s error=%s", self.symbol, self.api.current_endpoint, message)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.stop_monitoring()
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    window = SimplBWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
