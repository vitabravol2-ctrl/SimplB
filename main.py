from __future__ import annotations

import sys
import time

from PySide6.QtCore import QTimer, Qt
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
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.binance_http import BinanceHTTP
from core.config import load_config, save_config
from core.logger import setup_logger
from core.ws_client import WSClient


class SettingsDialog(QDialog):
    def __init__(self, config: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)

        self.symbol = QLineEdit(config["symbol"])
        self.ws_url = QLineEdit(config["ws_url"])
        self.http_endpoint = QLineEdit(config["http_endpoint"])
        self.ui_refresh = QSpinBox()
        self.ui_refresh.setRange(50, 5000)
        self.ui_refresh.setValue(int(config["ui_refresh_ms"]))

        form = QFormLayout()
        form.addRow("Symbol", self.symbol)
        form.addRow("WS URL", self.ws_url)
        form.addRow("HTTP endpoint", self.http_endpoint)
        form.addRow("Refresh UI ms", self.ui_refresh)

        btn_ok = QPushButton("Save")
        btn_cancel = QPushButton("Cancel")
        btn_ok.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(btn_ok)
        buttons.addWidget(btn_cancel)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)

    def values(self) -> dict:
        return {
            "symbol": self.symbol.text().strip().upper() or "BTCUSDT",
            "ws_url": self.ws_url.text().strip(),
            "http_endpoint": self.http_endpoint.text().strip(),
            "ui_refresh_ms": int(self.ui_refresh.value()),
        }


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.logger = setup_logger()
        self.config = load_config()
        self.http = BinanceHTTP(self.config["http_endpoint"])

        self.ws_client: WSClient | None = None
        self.last_tick: dict = {}
        self.last_tick_local_ms = 0
        self.connected = False
        self.reconnect_count = 0
        self.last_error = ""

        self.setWindowTitle("BTC Smart Scalper v0.1")
        self.setFixedSize(760, 420)
        self._build_ui()
        self._apply_theme()

        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self.refresh_ui)
        self.ui_timer.start(int(self.config["ui_refresh_ms"]))

        self.http_timer = QTimer(self)
        self.http_timer.timeout.connect(self.update_24h_panel)
        self.http_timer.start(5000)

        self.logger.info("app start")
        self.start_ws()
        self.update_24h_panel()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)

        top = QGroupBox("TOP")
        top_layout = QHBoxLayout(top)
        self.lbl_status = QLabel("DISCONNECTED")
        self.lbl_symbol = QLabel(self.config["symbol"])
        self.lbl_ws = QLabel("WS DISCONNECTED")
        self.lbl_age = QLabel("AGE 0 ms")
        for w in (self.lbl_status, self.lbl_symbol, self.lbl_ws, self.lbl_age):
            w.setFont(mono)
            top_layout.addWidget(w)
        top_layout.addStretch()

        market = QGroupBox("MARKET")
        mgrid = QGridLayout(market)
        self.m_labels = {}
        fields = ["BID", "ASK", "MID", "SPREAD", "SPREAD %", "BID QTY", "ASK QTY"]
        for idx, key in enumerate(fields):
            title = QLabel(key)
            value = QLabel("-")
            value.setFont(mono)
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            mgrid.addWidget(title, idx, 0)
            mgrid.addWidget(value, idx, 1)
            self.m_labels[key] = value

        panel24 = QGroupBox("24H")
        tgrid = QGridLayout(panel24)
        self.t_labels = {}
        tfields = ["LAST", "CHANGE %", "VOLUME", "QUOTE VOLUME"]
        for idx, key in enumerate(tfields):
            title = QLabel(key)
            value = QLabel("-")
            value.setFont(mono)
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            tgrid.addWidget(title, idx, 0)
            tgrid.addWidget(value, idx, 1)
            self.t_labels[key] = value

        system = QGroupBox("SYSTEM")
        sgrid = QGridLayout(system)
        self.s_reconnect = QLabel("0")
        self.s_error = QLabel("-")
        self.s_latency = QLabel("0 ms")
        for label in (self.s_reconnect, self.s_error, self.s_latency):
            label.setFont(mono)
        sgrid.addWidget(QLabel("WS reconnect count"), 0, 0)
        sgrid.addWidget(self.s_reconnect, 0, 1)
        sgrid.addWidget(QLabel("Last error"), 1, 0)
        sgrid.addWidget(self.s_error, 1, 1)
        sgrid.addWidget(QLabel("Latency / age"), 2, 0)
        sgrid.addWidget(self.s_latency, 2, 1)

        mid_row = QHBoxLayout()
        mid_row.addWidget(market, 2)
        right_col = QVBoxLayout()
        right_col.addWidget(panel24)
        right_col.addWidget(system)
        mid_row.addLayout(right_col, 1)

        btn_start = QPushButton("START")
        btn_stop = QPushButton("STOP")
        btn_settings = QPushButton("SETTINGS")
        btn_start.clicked.connect(self.start_ws)
        btn_stop.clicked.connect(self.stop_ws)
        btn_settings.clicked.connect(self.open_settings)
        bottom = QHBoxLayout()
        bottom.addStretch()
        bottom.addWidget(btn_start)
        bottom.addWidget(btn_stop)
        bottom.addWidget(btn_settings)

        root.addWidget(top)
        root.addLayout(mid_row)
        root.addStretch()
        root.addLayout(bottom)

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            """
            QWidget { background-color: #121417; color: #D7DCE2; font-size: 12px; }
            QGroupBox { border: 1px solid #2D3138; border-radius: 6px; margin-top: 10px; padding: 8px; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: #8BB6FF; }
            QPushButton { background-color: #1E2530; border: 1px solid #3A4658; border-radius: 4px; padding: 6px 12px; }
            QPushButton:hover { background-color: #253042; }
            QLineEdit, QSpinBox { background-color: #1A1E24; border: 1px solid #3A4658; }
            """
        )

    def start_ws(self) -> None:
        self.stop_ws()
        self.ws_client = WSClient(
            ws_url=self.config["ws_url"],
            symbol=self.config["symbol"],
            logger=self.logger,
        )
        self.ws_client.tick.connect(self.on_tick)
        self.ws_client.status.connect(self.on_status)
        self.ws_client.start()

    def stop_ws(self) -> None:
        if self.ws_client:
            self.ws_client.stop()
            self.ws_client = None
        self.connected = False
        self.lbl_status.setText("DISCONNECTED")
        self.lbl_ws.setText("WS DISCONNECTED")

    def on_tick(self, payload: dict) -> None:
        self.last_tick = payload
        self.last_tick_local_ms = int(time.time() * 1000)
        self.connected = True

    def on_status(self, payload: dict) -> None:
        self.connected = bool(payload.get("connected", False))
        self.reconnect_count = int(payload.get("reconnect_count", self.reconnect_count))
        self.last_error = payload.get("last_error", self.last_error)

    def refresh_ui(self) -> None:
        now_ms = int(time.time() * 1000)
        if self.last_tick:
            age = max(0, now_ms - int(self.last_tick.get("tick_ts", now_ms)))
            self.lbl_age.setText(f"AGE {age} ms")
            self.s_latency.setText(f"{age} ms")
            self.m_labels["BID"].setText(f"{self.last_tick['bid']:.2f}")
            self.m_labels["ASK"].setText(f"{self.last_tick['ask']:.2f}")
            self.m_labels["MID"].setText(f"{self.last_tick['mid']:.2f}")
            self.m_labels["SPREAD"].setText(f"{self.last_tick['spread']:.2f}")
            self.m_labels["SPREAD %"].setText(f"{self.last_tick['spread_pct']:.5f}")
            self.m_labels["BID QTY"].setText(f"{self.last_tick['bid_qty']:.5f}")
            self.m_labels["ASK QTY"].setText(f"{self.last_tick['ask_qty']:.5f}")

        self.lbl_symbol.setText(self.config["symbol"])
        if self.connected and (self.last_tick_local_ms and now_ms - self.last_tick_local_ms < 4000):
            self.lbl_status.setText("CONNECTED")
            self.lbl_status.setStyleSheet("color:#6BE28C;")
            self.lbl_ws.setText("WS CONNECTED")
            self.lbl_ws.setStyleSheet("color:#6BE28C;")
        else:
            self.lbl_status.setText("DISCONNECTED")
            self.lbl_status.setStyleSheet("color:#FF6B6B;")
            self.lbl_ws.setText("WS DISCONNECTED")
            self.lbl_ws.setStyleSheet("color:#FF6B6B;")

        self.s_reconnect.setText(str(self.reconnect_count))
        self.s_error.setText(self.last_error[:60] if self.last_error else "-")

    def update_24h_panel(self) -> None:
        try:
            self.http.get_server_time()
            data = self.http.get_ticker_24h(self.config["symbol"])
            self.t_labels["LAST"].setText(f"{float(data.get('lastPrice', 0.0)):.2f}")
            self.t_labels["CHANGE %"].setText(f"{float(data.get('priceChangePercent', 0.0)):.3f}")
            self.t_labels["VOLUME"].setText(f"{float(data.get('volume', 0.0)):.2f}")
            self.t_labels["QUOTE VOLUME"].setText(f"{float(data.get('quoteVolume', 0.0)):.2f}")
        except Exception as exc:
            self.last_error = f"HTTP: {exc}"

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.config, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.config = dialog.values()
            save_config(self.config)
            self.http = BinanceHTTP(self.config["http_endpoint"])
            self.ui_timer.start(int(self.config["ui_refresh_ms"]))
            self.start_ws()

    def closeEvent(self, event) -> None:
        self.logger.info("app stop")
        self.stop_ws()
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
