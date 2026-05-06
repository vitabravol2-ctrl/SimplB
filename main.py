from __future__ import annotations

import subprocess
import sys
import time

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
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
    QDoubleSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.algorithms.base import AlgoContext
from core.algorithms.basic_scalper import BasicScalper
from core.binance_http import BinanceHTTP
from core.config import load_config, save_config
from core.logger import LOG_PATH, setup_logger
from core.ws_client import WSClient


class AccountWorker(QObject):
    balances = Signal(dict)

    def __init__(self, http: BinanceHTTP) -> None:
        super().__init__()
        self.http = http

    def poll(self) -> None:
        try:
            b = self.http.get_balances(["USDT", "BTC"])
            self.balances.emit({"status": "OK", "balances": b})
        except Exception as exc:
            self.balances.emit({"status": f"ERROR: {exc}", "balances": {}})


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.logger = setup_logger()
        self.config = load_config()
        self.http = BinanceHTTP(self.config["http_endpoint"], self.config.get("api_key", ""), self.config.get("api_secret", ""))
        self.algo = BasicScalper(self.config["algorithms"]["BasicScalper"])
        self.last_tick = {}
        self.last_algo_status = self.algo.get_status()
        self.balance_data = {}

        self.setWindowTitle("BTC Smart Scalper v0.2")
        self.resize(980, 640)
        self._build_ui()
        self.ws_client = None

        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self.refresh_dashboard)
        self.ui_timer.start(int(self.config["ui_refresh_ms"]))

        self.account_thread = QThread(self)
        self.account_worker = AccountWorker(self.http)
        self.account_worker.moveToThread(self.account_thread)
        self.account_worker.balances.connect(self.on_balances)
        self.account_thread.start()
        self.account_timer = QTimer(self)
        self.account_timer.timeout.connect(lambda: self.account_worker.poll())
        self.account_timer.start(int(self.config.get("account_poll_ms", 3000)))

        self.start_ws()
        self.logger.info("app start")

    def _build_ui(self):
        tabs = QTabWidget()
        self.setCentralWidget(tabs)

        self.tab_dashboard = QWidget(); self.tab_settings = QWidget(); self.tab_algo = QWidget(); self.tab_logs = QWidget()
        tabs.addTab(self.tab_dashboard, "Dashboard"); tabs.addTab(self.tab_settings, "Settings"); tabs.addTab(self.tab_algo, "Algorithms"); tabs.addTab(self.tab_logs, "Logs")

        self._build_dashboard(); self._build_settings(); self._build_algo(); self._build_logs()

    def _build_dashboard(self):
        l = QVBoxLayout(self.tab_dashboard)
        g = QGridLayout()
        self.db = {k: QLabel("-") for k in ["bid","ask","mid","spread","ws","acc","usdt","btc","algo","state","act","size","tp","sl","mstep","last"]}
        labels = ["bid","ask","mid","spread","ws","acc","usdt","btc","algo","state","act","size","tp","sl","mstep","last"]
        for i,k in enumerate(labels): g.addWidget(QLabel(k.upper()), i, 0); g.addWidget(self.db[k], i, 1)
        self.signal_lamp = QLabel("●")
        g.addWidget(QLabel("SIGNAL"), len(labels), 0); g.addWidget(self.signal_lamp, len(labels), 1)
        l.addLayout(g)

    def _build_settings(self):
        l = QVBoxLayout(self.tab_settings)
        api = QGroupBox("API"); f=QFormLayout(api)
        self.ed_key = QLineEdit(self.config.get("api_key","")); self.ed_sec = QLineEdit(self.config.get("api_secret","")); self.ed_sec.setEchoMode(QLineEdit.Password)
        f.addRow("API KEY", self.ed_key); f.addRow("SECRET", self.ed_sec)
        bsave=QPushButton("SAVE"); btest=QPushButton("TEST API"); bsave.clicked.connect(self.save_settings); btest.clicked.connect(self.test_api)
        hb=QHBoxLayout(); hb.addWidget(bsave); hb.addWidget(btest); f.addRow(hb)

        market = QGroupBox("Market"); mf=QFormLayout(market)
        self.ed_symbol=QLineEdit(self.config["symbol"]); self.ed_http=QLineEdit(self.config["http_endpoint"]); self.ed_ws=QLineEdit(self.config["ws_url"])
        self.sp_ui=QSpinBox(); self.sp_ui.setRange(50,5000); self.sp_ui.setValue(int(self.config["ui_refresh_ms"]))
        mf.addRow("Symbol", self.ed_symbol); mf.addRow("HTTP endpoint", self.ed_http); mf.addRow("WS url", self.ed_ws); mf.addRow("UI refresh ms", self.sp_ui)

        account = QGroupBox("Account"); af=QFormLayout(account)
        self.sp_poll=QSpinBox(); self.sp_poll.setRange(500,20000); self.sp_poll.setValue(int(self.config.get("account_poll_ms",3000)))
        self.cb_show=QCheckBox(); self.cb_show.setChecked(bool(self.config.get("show_balances",True)))
        af.addRow("account poll ms", self.sp_poll); af.addRow("show balances", self.cb_show)
        l.addWidget(api); l.addWidget(market); l.addWidget(account); l.addStretch()

    def _build_algo(self):
        l=QVBoxLayout(self.tab_algo); f=QFormLayout()
        cfg=self.config["algorithms"]["BasicScalper"]
        self.al_en=QCheckBox(); self.al_en.setChecked(cfg["enabled"])
        self.al_fields={}
        for key, val, dec in [("order_size_usdt",cfg["order_size_usdt"],2),("take_profit_usdt",cfg["take_profit_usdt"],2),("stop_loss_usdt",cfg["stop_loss_usdt"],2),("max_cycles",cfg["max_cycles"],0),("cooldown_sec",cfg["cooldown_sec"],0),("fee_bps",cfg["fee_bps"],2),("martingale_multiplier",cfg["martingale_multiplier"],2),("max_martingale_steps",cfg["max_martingale_steps"],0)]:
            w = QDoubleSpinBox() if dec else QSpinBox();
            (w.setDecimals(dec) if isinstance(w,QDoubleSpinBox) else None)
            w.setValue(val); self.al_fields[key]=w
        self.al_marti=QCheckBox(); self.al_marti.setChecked(cfg["allow_martingale"])
        f.addRow("Algorithm", QLabel("BasicScalper")); f.addRow("enabled", self.al_en)
        for k in ["order_size_usdt","take_profit_usdt","stop_loss_usdt","max_cycles","cooldown_sec","fee_bps"]: f.addRow(k, self.al_fields[k])
        f.addRow("allow_martingale", self.al_marti); f.addRow("martingale_multiplier", self.al_fields["martingale_multiplier"]); f.addRow("max_martingale_steps", self.al_fields["max_martingale_steps"])
        l.addLayout(f)
        hb=QHBoxLayout();
        for t,fn in [("SAVE ALGO SETTINGS",self.save_algo),("START ALGO",self.start_algo),("STOP ALGO",self.stop_algo)]:
            b=QPushButton(t); b.clicked.connect(fn); hb.addWidget(b)
        l.addLayout(hb)

    def _build_logs(self):
        l=QVBoxLayout(self.tab_logs); self.logbox=QTextEdit(); self.logbox.setReadOnly(True)
        b1=QPushButton("CLEAR"); b2=QPushButton("OPEN LOG FILE"); b1.clicked.connect(self.logbox.clear); b2.clicked.connect(lambda: subprocess.Popen(["xdg-open", str(LOG_PATH)]))
        hb=QHBoxLayout(); hb.addWidget(b1); hb.addWidget(b2)
        l.addWidget(self.logbox); l.addLayout(hb)

    def save_settings(self):
        self.config.update({"api_key":self.ed_key.text().strip(),"api_secret":self.ed_sec.text().strip(),"symbol":self.ed_symbol.text().strip().upper(),"http_endpoint":self.ed_http.text().strip(),"ws_url":self.ed_ws.text().strip(),"ui_refresh_ms":self.sp_ui.value(),"account_poll_ms":self.sp_poll.value(),"show_balances":self.cb_show.isChecked()})
        save_config(self.config); self.http = BinanceHTTP(self.config["http_endpoint"], self.config["api_key"], self.config["api_secret"])

    def save_algo(self):
        cfg=self.config["algorithms"]["BasicScalper"]; cfg["enabled"]=self.al_en.isChecked(); cfg["allow_martingale"]=self.al_marti.isChecked()
        for k,v in self.al_fields.items(): cfg[k]=v.value()
        save_config(self.config); self.algo = BasicScalper(cfg)

    def start_algo(self): self.algo.start(); self.logger.info("algo start")
    def stop_algo(self): self.algo.stop(); self.logger.info("algo stop")

    def test_api(self):
        try:
            self.http.set_credentials(self.ed_key.text().strip(), self.ed_sec.text().strip())
            self.http.test_connection(); self.logger.info("api test ok"); QMessageBox.information(self, "API", "API test OK")
        except Exception as exc:
            self.logger.error("api test error: %s", exc); QMessageBox.warning(self, "API", f"API test error: {exc}")

    def start_ws(self):
        self.ws_client = WSClient(self.config["ws_url"], self.config["symbol"], self.logger)
        self.ws_client.tick.connect(self.on_tick)
        self.ws_client.start()

    def on_tick(self, payload):
        self.last_tick = payload
        ctx = AlgoContext(market_data=self.last_tick, balances=self.balance_data, config=self.config, logger=self.logger)
        new_status = self.algo.on_tick(ctx)
        if new_status.get("planned_action") != self.last_algo_status.get("planned_action"):
            self.logger.info("algo signal changed | %s", new_status.get("planned_action"))
        self.last_algo_status = new_status

    def on_balances(self, payload):
        self.balance_data = payload.get("balances", {})
        if payload.get("status","").startswith("ERROR"):
            self.logger.error("balances update error | %s", payload["status"])

    def refresh_dashboard(self):
        t = self.last_tick
        for k in ["bid","ask","mid","spread"]: self.db[k].setText(f"{float(t.get(k,0.0)):.6f}")
        self.db["ws"].setText("CONNECTED" if t.get("connected") else "DISCONNECTED")
        self.db["acc"].setText("OK" if self.balance_data else "NO DATA")
        self.db["usdt"].setText(str(self.balance_data.get("USDT",{}).get("total","-")))
        self.db["btc"].setText(str(self.balance_data.get("BTC",{}).get("total","-")))
        s = self.last_algo_status
        self.db["algo"].setText("BasicScalper"); self.db["state"].setText(s.get("state","-")); self.db["act"].setText(s.get("planned_action","-"))
        self.db["size"].setText(str(s.get("current_order_size","-"))); self.db["tp"].setText(str(s.get("tp","-"))); self.db["sl"].setText(str(s.get("sl","-")))
        self.db["mstep"].setText(str(s.get("martingale_step","-"))); self.db["last"].setText(str(s.get("last_result","-")))
        if s.get("state") in ("READY_TO_BUY","READY_TO_SELL"): self.signal_lamp.setStyleSheet("color: yellow;")
        elif self.balance_data and t.get("connected"): self.signal_lamp.setStyleSheet("color: green;")
        else: self.signal_lamp.setStyleSheet("color: red;")
        try:
            lines = LOG_PATH.read_text(encoding="utf-8").splitlines()[-100:]
            self.logbox.setPlainText("\n".join(lines))
        except OSError:
            pass


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
