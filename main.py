import sys
from collections import deque
from typing import Dict

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.binance_api import get_24hr, get_account, get_book_ticker
from core.config import load_config, save_config
from core.logger import setup_logger

SYMBOL = "EURIUSDT"


class SimplBWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.logger = setup_logger()
        self.setWindowTitle("SimplB v0.1 (HTTP ONLY MONITOR)")
        self.resize(860, 540)

        self.logs_buffer = deque(maxlen=10)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.fetch_market_data)

        self._build_ui()
        self._load_saved_keys()
        self.check_api_connection()
        self.start_monitoring()

    def _build_ui(self) -> None:
        root = QWidget()
        main_layout = QVBoxLayout(root)

        self.connection_status = QLabel("NOT CONNECTED")
        self.connection_status.setStyleSheet("font-weight: bold; color: #cc0000;")
        main_layout.addWidget(self.connection_status)

        self.market_table = QTableWidget(1, 8)
        self.market_table.setHorizontalHeaderLabels(
            [
                "bidPrice",
                "askPrice",
                "spread",
                "spread %",
                "lastPrice",
                "volume",
                "quoteVolume",
                "priceChangePercent",
            ]
        )
        self.market_table.verticalHeader().setVisible(False)
        main_layout.addWidget(self.market_table)

        form_layout = QGridLayout()
        form_layout.addWidget(QLabel("API KEY"), 0, 0)
        self.api_key_input = QLineEdit()
        form_layout.addWidget(self.api_key_input, 0, 1)

        form_layout.addWidget(QLabel("SECRET"), 1, 0)
        self.secret_input = QLineEdit()
        self.secret_input.setEchoMode(QLineEdit.Password)
        form_layout.addWidget(self.secret_input, 1, 1)
        main_layout.addLayout(form_layout)

        button_layout = QHBoxLayout()
        self.start_btn = QPushButton("START")
        self.start_btn.clicked.connect(self.start_monitoring)
        button_layout.addWidget(self.start_btn)

        self.stop_btn = QPushButton("STOP")
        self.stop_btn.clicked.connect(self.stop_monitoring)
        button_layout.addWidget(self.stop_btn)

        self.save_btn = QPushButton("SAVE API KEYS")
        self.save_btn.clicked.connect(self.handle_save_keys)
        button_layout.addWidget(self.save_btn)
        main_layout.addLayout(button_layout)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        main_layout.addWidget(self.log_box)

        self.setCentralWidget(root)

    def _load_saved_keys(self) -> None:
        cfg = load_config()
        self.api_key_input.setText(cfg.get("api_key", ""))
        self.secret_input.setText(cfg.get("secret", ""))
        self._append_log("Config loaded.")

    def handle_save_keys(self) -> None:
        try:
            save_config(self.api_key_input.text(), self.secret_input.text())
            self._append_log("API keys saved to config.json")
            self.logger.info("API keys saved")
            self.check_api_connection()
        except OSError as exc:
            self._append_log(f"Failed to save keys: {exc}")
            self.logger.error("Save keys error: %s", exc)

    def _set_connection(self, ok: bool) -> None:
        if ok:
            self.connection_status.setText("CONNECTED")
            self.connection_status.setStyleSheet("font-weight: bold; color: #0f8a0f;")
        else:
            self.connection_status.setText("NOT CONNECTED")
            self.connection_status.setStyleSheet("font-weight: bold; color: #cc0000;")

    def check_api_connection(self) -> None:
        api_key = self.api_key_input.text().strip()
        secret = self.secret_input.text().strip()
        if not api_key or not secret:
            self._set_connection(False)
            self._append_log("No API keys provided. Skipping account check.")
            self.logger.info("API status: NOT CONNECTED (no keys)")
            return

        try:
            get_account(api_key, secret)
            self._set_connection(True)
            self._append_log("Account API check successful.")
            self.logger.info("API status: CONNECTED")
        except Exception as exc:  # noqa: BLE001
            self._set_connection(False)
            self._append_log(f"Account API check failed: {exc}")
            self.logger.error("API status: NOT CONNECTED | %s", exc)

    def start_monitoring(self) -> None:
        if not self.timer.isActive():
            self.timer.start()
            self._append_log(f"Monitoring started for {SYMBOL}")
            self.logger.info("Monitoring started")
            self.fetch_market_data()

    def stop_monitoring(self) -> None:
        if self.timer.isActive():
            self.timer.stop()
            self._append_log("Monitoring stopped")
            self.logger.info("Monitoring stopped")

    def fetch_market_data(self) -> None:
        try:
            book = get_book_ticker(SYMBOL)
            stat = get_24hr(SYMBOL)
            row = self._prepare_row(book, stat)
            self._update_table(row)
            self._set_connection(True)
            self._append_log(f"Fetched market data for {SYMBOL}")
            self.logger.info("HTTP success: market data fetched")
        except Exception as exc:  # noqa: BLE001
            self._set_connection(False)
            self._append_log(f"Market fetch error: {exc}")
            self.logger.error("HTTP error: %s", exc)

    def _prepare_row(self, book: Dict[str, str], stat: Dict[str, str]) -> Dict[str, str]:
        bid = float(book["bidPrice"])
        ask = float(book["askPrice"])
        spread = ask - bid
        spread_pct = (spread / bid * 100) if bid else 0.0

        return {
            "bidPrice": f"{bid:.6f}",
            "askPrice": f"{ask:.6f}",
            "spread": f"{spread:.6f}",
            "spread %": f"{spread_pct:.4f}%",
            "lastPrice": f"{float(stat['lastPrice']):.6f}",
            "volume": stat["volume"],
            "quoteVolume": stat["quoteVolume"],
            "priceChangePercent": f"{float(stat['priceChangePercent']):.3f}%",
        }

    def _update_table(self, values: Dict[str, str]) -> None:
        headers = [
            "bidPrice",
            "askPrice",
            "spread",
            "spread %",
            "lastPrice",
            "volume",
            "quoteVolume",
            "priceChangePercent",
        ]
        for col, key in enumerate(headers):
            item = QTableWidgetItem(values[key])
            if key in {"spread", "spread %"}:
                item.setForeground(QColor("#9c4f00"))
            self.market_table.setItem(0, col, item)

    def _append_log(self, message: str) -> None:
        self.logs_buffer.append(message)
        self.log_box.setPlainText("\n".join(self.logs_buffer))


def main() -> None:
    app = QApplication(sys.argv)
    window = SimplBWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
