import sys
from datetime import datetime
from typing import Dict

from PySide6.QtCore import QTimer, Qt
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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.binance_api import BinanceAPI, HTTPRequestError
from core.config import load_config, save_config_values


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
        self.cfg = load_config()
        self.api = BinanceAPI()
        saved_endpoint = str(self.cfg.get("current_endpoint", self.api.current_endpoint))
        if saved_endpoint in self.api.endpoints:
            self.api.current_endpoint = saved_endpoint
            self.api.current_idx = self.api.endpoints.index(saved_endpoint)
        self.symbol = str(self.cfg.get("symbol", "EURIUSDT"))

        self.timer = QTimer(self)
        self.timer.setInterval(int(self.cfg.get("refresh_ms", 1000)))
        self.timer.timeout.connect(self.fetch_market_data)

        self.setWindowTitle("SimplB Terminal")
        self.resize(900, 320)
        self._build_ui()
        self._set_status("RED", "#cc0000")

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        top = QHBoxLayout()
        self.status_label = QLabel("STATUS ● RED")
        self.endpoint_label = QLabel(f"ENDPOINT: {self.api.current_endpoint}")
        self.latency_label = QLabel("LATENCY: - ms")
        for w in (self.status_label, self.endpoint_label, self.latency_label):
            top.addWidget(w)
        top.addStretch(1)
        layout.addLayout(top)

        self.market_table = QTableWidget(1, 8)
        self.market_table.setHorizontalHeaderLabels(["bid", "ask", "mid", "spread", "%", "last", "vol", "qVol"])
        self.market_table.verticalHeader().setVisible(False)
        self.market_table.setShowGrid(False)
        self.market_table.setAlternatingRowColors(True)
        self.market_table.setStyleSheet("QTableWidget{font-size:11px;} QHeaderView::section{padding:2px;}")
        self.market_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.market_table.setFocusPolicy(Qt.NoFocus)
        layout.addWidget(self.market_table)

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
        self.status_label.setText(f"STATUS ● {status}")
        self.status_label.setStyleSheet(f"font-weight:600;color:{color};")
        self.endpoint_label.setText(f"ENDPOINT: {self.api.current_endpoint.replace('https://', '')}")
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
        if not self.timer.isActive():
            return
        self.timer.stop()

    def fetch_market_data(self) -> None:
        try:
            book = self.api.get_book_ticker(self.symbol)
            stat = self.api.get_24hr(self.symbol)
            latency = book.latency_ms + stat.latency_ms
            row = self._prepare_row(book.data, stat.data)
            self._update_table(row)
            self.latency_label.setText(f"LATENCY: {latency:.1f} ms")
            if book.switched or stat.switched or "api.binance.com" not in self.api.current_endpoint:
                self._set_status("YELLOW", "#b8860b")
            else:
                self._set_status("GREEN", "#0f8a0f")
        except HTTPRequestError:
            self._set_status("RED", "#cc0000")

    def _prepare_row(self, book: Dict[str, str], stat: Dict[str, str]) -> Dict[str, str]:
        bid = float(book["bidPrice"])
        ask = float(book["askPrice"])
        mid = (bid + ask) / 2
        spread = ask - bid
        spread_pct = (spread / bid * 100) if bid else 0.0
        _ = datetime.now().isoformat(timespec="seconds")
        return {
            "bid": f"{bid:.6f}",
            "ask": f"{ask:.6f}",
            "mid": f"{mid:.6f}",
            "spread": f"{spread:.6f}",
            "%": f"{spread_pct:.4f}%",
            "last": f"{float(stat['lastPrice']):.6f}",
            "vol": stat["volume"],
            "qVol": stat["quoteVolume"],
        }

    def _update_table(self, values: Dict[str, str]) -> None:
        headers = ["bid", "ask", "mid", "spread", "%", "last", "vol", "qVol"]
        for col, key in enumerate(headers):
            self.market_table.setItem(0, col, QTableWidgetItem(values[key]))


def main() -> None:
    app = QApplication(sys.argv)
    window = SimplBWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
