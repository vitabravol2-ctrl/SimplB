import sys
from collections import deque
from datetime import datetime
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
from core.config import load_config, save_config, save_config_values
from core.logger import setup_logger


class SimplBWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.logger = setup_logger()
        self.cfg = load_config()
        self.symbol = str(self.cfg.get("symbol", "EURIUSDT"))
        self.refresh_ms = int(self.cfg.get("refresh_ms", 1000))

        self.setWindowTitle("SimplB v0.1 (HTTP ONLY MONITOR)")
        self.resize(920, 560)

        self.logs_buffer = deque(maxlen=10)
        self.timer = QTimer(self)
        self.timer.setInterval(self.refresh_ms)
        self.timer.timeout.connect(self.fetch_market_data)
        self.consecutive_errors = 0

        self._build_ui()
        self._load_saved_keys()
        self.check_api_connection()
        self._apply_autostart()

    def _build_ui(self) -> None:
        root = QWidget()
        main_layout = QVBoxLayout(root)

        self.connection_status = QLabel("STOPPED")
        self._set_status("STOPPED", "#b8860b")
        main_layout.addWidget(self.connection_status)

        self.meta_label = QLabel("Last update: - | Latency: - ms")
        main_layout.addWidget(self.meta_label)

        self.market_table = QTableWidget(1, 11)
        self.market_table.setHorizontalHeaderLabels(
            [
                "bidPrice",
                "askPrice",
                "midPrice",
                "spread",
                "spread %",
                "lastPrice",
                "volume",
                "quoteVolume",
                "priceChangePercent",
                "timestamp",
                "latency_ms",
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
        self.api_key_input.setText(str(self.cfg.get("api_key", "")))
        self.secret_input.setText(str(self.cfg.get("secret", "")))
        self._append_log("Config loaded.")

    def _apply_autostart(self) -> None:
        auto_start = bool(self.cfg.get("auto_start", True))
        monitor_state = str(self.cfg.get("monitor_state", "running"))
        if auto_start and monitor_state == "running":
            self.start_monitoring()
        else:
            self._append_log("Autostart disabled or previous state stopped.")

    def handle_save_keys(self) -> None:
        try:
            save_config(self.api_key_input.text(), self.secret_input.text())
            self.cfg = load_config()
            self._append_log("API keys saved to config.json")
            self.logger.info("API keys saved")
            self.check_api_connection()
        except OSError as exc:
            self._append_log(f"Failed to save keys: {exc}")
            self.logger.error("Save keys error: %s", exc)

    def _set_status(self, text: str, color: str) -> None:
        self.connection_status.setText(text)
        self.connection_status.setStyleSheet(f"font-weight: bold; color: {color};")

    def check_api_connection(self) -> None:
        api_key = self.api_key_input.text().strip()
        secret = self.secret_input.text().strip()
        if not api_key or not secret:
            self._append_log("No API keys provided. Skipping account check.")
            self.logger.info("API status: no keys")
            return

        try:
            get_account(api_key, secret)
            self._append_log("Account API check successful.")
            self.logger.info("API status: CONNECTED")
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"Account API check failed: {exc}")
            self.logger.error("API status error: %s", exc)

    def start_monitoring(self) -> None:
        if self.timer.isActive():
            self._append_log("Monitor already running.")
            return

        self.timer.start()
        self.cfg["monitor_state"] = "running"
        save_config_values(self.cfg)
        self._append_log(f"Monitoring started for {self.symbol}")
        self.logger.info("Monitoring started")
        self.fetch_market_data()

    def stop_monitoring(self) -> None:
        if not self.timer.isActive():
            return

        self.timer.stop()
        self.cfg["monitor_state"] = "stopped"
        save_config_values(self.cfg)
        self._set_status("STOPPED", "#b8860b")
        self._append_log("Monitoring stopped")
        self.logger.info("Monitoring stopped")

    def fetch_market_data(self) -> None:
        if not self.timer.isActive():
            return
        try:
            book, book_latency, book_retries = get_book_ticker(self.symbol)
            stat, stat_latency, stat_retries = get_24hr(self.symbol)
            latency_ms = book_latency + stat_latency
            retries_used = max(book_retries, stat_retries)
            row = self._prepare_row(book, stat, latency_ms)
            self._update_table(row)
            self.consecutive_errors = 0
            if retries_used > 0:
                self._set_status("RETRY", "#b8860b")
                self._append_log(f"Request recovered after retry x{retries_used}.")
                self.logger.warning("HTTP recovered with retries=%s", retries_used)
            else:
                self._set_status("OK", "#0f8a0f")
            self.meta_label.setText(f"Last update: {row['timestamp']} | Latency: {row['latency_ms']} ms")
        except Exception as exc:  # noqa: BLE001
            self.consecutive_errors += 1
            if self.consecutive_errors >= 3:
                self._set_status("ERROR", "#cc0000")
            else:
                self._set_status("RETRY", "#b8860b")
            self._append_log(f"Market fetch error ({self.consecutive_errors}): {exc}")
            self.logger.error("HTTP error: %s", exc)

    def _prepare_row(self, book: Dict[str, str], stat: Dict[str, str], latency_ms: float) -> Dict[str, str]:
        bid = float(book["bidPrice"])
        ask = float(book["askPrice"])
        spread = ask - bid
        spread_pct = (spread / bid * 100) if bid else 0.0
        mid_price = (bid + ask) / 2
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        return {
            "bidPrice": f"{bid:.6f}",
            "askPrice": f"{ask:.6f}",
            "midPrice": f"{mid_price:.6f}",
            "spread": f"{spread:.6f}",
            "spread %": f"{spread_pct:.4f}%",
            "lastPrice": f"{float(stat['lastPrice']):.6f}",
            "volume": stat["volume"],
            "quoteVolume": stat["quoteVolume"],
            "priceChangePercent": f"{float(stat['priceChangePercent']):.3f}%",
            "timestamp": ts,
            "latency_ms": f"{latency_ms:.1f}",
        }

    def _update_table(self, values: Dict[str, str]) -> None:
        headers = [
            "bidPrice",
            "askPrice",
            "midPrice",
            "spread",
            "spread %",
            "lastPrice",
            "volume",
            "quoteVolume",
            "priceChangePercent",
            "timestamp",
            "latency_ms",
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
