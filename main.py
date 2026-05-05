import sys
from datetime import datetime
from typing import Dict

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFormLayout,
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

from core.binance_api import BinanceAPI, HTTPRequestError
from core.config import load_config, save_config_values
from core.logger import setup_logger


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

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.fetch_market_data)

        self.setWindowTitle("SimplB Terminal")
        self.resize(720, 460)
        self._build_ui()
        self._set_status("RED", "#ff4d4d")

    def _build_ui(self) -> None:
        root = QWidget()
        root.setStyleSheet("background-color:#000000;color:#00ff66;")
        layout = QVBoxLayout(root)

        mono = QFont("Courier New")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(10)

        top = QHBoxLayout()
        self.status_label = QLabel("status: ● RED")
        self.endpoint_label = QLabel(f"endpoint: {self.api.current_endpoint}")
        self.latency_label = QLabel("latency: - ms")
        for w in (self.status_label, self.endpoint_label, self.latency_label):
            w.setFont(mono)
            top.addWidget(w)
        top.addStretch(1)
        layout.addLayout(top)

        self.stream_view = QTextEdit()
        self.stream_view.setReadOnly(True)
        self.stream_view.setFont(mono)
        self.stream_view.setStyleSheet("border:1px solid #00aa44;padding:6px;")
        layout.addWidget(self.stream_view)

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

    def _set_status(self, status: str, color: str) -> None:
        self.status_label.setText(f"status: ● {status}")
        self.status_label.setStyleSheet(f"color:{color};font-weight:600;")
        endpoint = self.api.current_endpoint.replace("https://", "")
        self.endpoint_label.setText(f"endpoint: {endpoint}")
        self.cfg["current_endpoint"] = self.api.current_endpoint
        save_config_values(self.cfg)

    def open_settings(self) -> None:
        dlg = SettingsDialog(self, self.cfg, self.api)
        if dlg.exec():
            self.cfg = load_config()
            self.symbol = str(self.cfg.get("symbol", "EURIUSDT"))

    def start_monitoring(self) -> None:
        if self.timer.isActive():
            return
        self.timer.start()
        self.fetch_market_data()

    def stop_monitoring(self) -> None:
        self.timer.stop()

    def fetch_market_data(self) -> None:
        timestamp = datetime.now().isoformat(timespec="seconds")
        try:
            book = self.api.get_book_ticker(self.symbol)
            stat = self.api.get_24hr(self.symbol)
            latency = book.latency_ms + stat.latency_ms
            status_text = "YELLOW" if (book.retries_used > 0 or stat.retries_used > 0 or book.switched or stat.switched) else "GREEN"
            payload = self._build_stream_payload(book.data, stat.data, latency, timestamp, status_text)
            self.stream_view.setPlainText(payload)
            self.latency_label.setText(f"latency: {latency:.1f} ms")
            if status_text == "YELLOW":
                self._set_status("YELLOW", "#ffdd44")
            else:
                self._set_status("GREEN", "#00ff66")
            self.logger.info("tick ok symbol=%s endpoint=%s latency_ms=%.1f", self.symbol, self.api.current_endpoint, latency)
        except HTTPRequestError as exc:
            self._set_status("RED", "#ff4d4d")
            self.logger.error("tick failed symbol=%s endpoint=%s error=%s", self.symbol, self.api.current_endpoint, exc)

    def _build_stream_payload(self, book: Dict[str, str], stat: Dict[str, str], latency: float, timestamp: str, status: str) -> str:
        bid = float(book["bidPrice"])
        ask = float(book["askPrice"])
        mid = (bid + ask) / 2
        spread = ask - bid
        spread_pct = (spread / bid * 100) if bid else 0.0
        endpoint = self.api.current_endpoint.replace("https://", "")

        return (
            f"{self.symbol}\n"
            "------------------------\n"
            f"bid: {bid:.6f}\n"
            f"ask: {ask:.6f}\n"
            f"mid: {mid:.6f}\n"
            f"spread: {spread:.6f}\n"
            f"spread%: {spread_pct:.4f}%\n\n"
            f"last: {float(stat['lastPrice']):.6f}\n"
            f"volume: {stat['volume']}\n"
            f"quoteVol: {stat['quoteVolume']}\n\n"
            f"latency: {latency:.1f} ms\n"
            f"endpoint: {endpoint}\n"
            f"timestamp: {timestamp}\n"
            f"status: ● {status}\n"
            "------------------------"
        )


def main() -> None:
    app = QApplication(sys.argv)
    window = SimplBWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
