import sys
from typing import Any, Dict

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton, QVBoxLayout, QWidget

from core.binance_api import BinanceAPI, HTTPRequestError
from core.config import load_config, save_config_values
from core.logger import setup_logger
from core.trading_utils import round_price_to_tick, round_qty_to_step, validate_min_notional

# keep MarketWorker/SettingsDialog unchanged minimal
class MarketWorker(QObject):
    data_ready = Signal(dict); account_ready = Signal(dict); error = Signal(str); account_error = Signal(str)
    def __init__(self, symbol:str, endpoint:str, api_key:str="", secret:str=""):
        super().__init__(); self.symbol=symbol; self.api_key=api_key; self.secret=secret; self.api=BinanceAPI();
        if endpoint in self.api.endpoints: self.api.current_endpoint=endpoint; self.api.current_idx=self.api.endpoints.index(endpoint)
        self.running=False; self.consecutive_errors=0; self.had_error=False; self.market_timer=None; self.account_timer=None
    @Slot()
    def start(self):
        if self.running:return
        self.running=True; self.market_timer=QTimer(self); self.market_timer.setInterval(1000); self.market_timer.timeout.connect(self.poll_market); self.market_timer.start();
        self.account_timer=QTimer(self); self.account_timer.setInterval(5000); self.account_timer.timeout.connect(self.poll_account); self.account_timer.start(); self.poll_market(); self.poll_account()
    @Slot()
    def stop(self):
        self.running=False
        for t in [self.market_timer,self.account_timer]:
            if t is not None: t.stop(); t.deleteLater()
        self.market_timer=None; self.account_timer=None
    @Slot(str,str,str)
    def update_settings(self,symbol:str,api_key:str,secret:str): self.symbol=symbol; self.api_key=api_key; self.secret=secret
    @Slot()
    def poll_market(self):
        if not self.running:return
        try:
            prev=self.api.current_endpoint; book=self.api.get_book_ticker(self.symbol); stat=self.api.get_24hr(self.symbol); latency=book.latency_ms+stat.latency_ms
            status="YELLOW" if (book.retries_used>0 or stat.retries_used>0 or book.switched or stat.switched) else "GREEN"; self.consecutive_errors=0; recovered=self.had_error; self.had_error=False
            self.data_ready.emit({"symbol":self.symbol,"endpoint":self.api.current_endpoint,"latency_ms":latency,"status":status,"consecutive_errors":0,"recovered":recovered,"endpoint_changed":prev!=self.api.current_endpoint,"book":book.data,"stat":stat.data})
        except HTTPRequestError as exc:
            self.had_error=True; self.consecutive_errors += 1; self.error.emit(str(exc))
    @Slot()
    def poll_account(self):
        if not self.running:return
        if not self.api_key or not self.secret: self.account_ready.emit({"status":"NO KEYS","balances":{}}); return
        try: self.account_ready.emit({"status":"CONNECTED","balances":self.api.get_balances(self.api_key,self.secret,["USDT","EURI"])})
        except HTTPRequestError as exc: self.account_error.emit(str(exc))

class SettingsDialog(QDialog):
    def __init__(self,parent,cfg,api): super().__init__(parent); self.cfg=cfg; self.api=api; self.setWindowTitle('Settings'); l=QVBoxLayout(self); f=QFormLayout(); self.api_key_input=QLineEdit(str(cfg.get('api_key',''))); self.secret_input=QLineEdit(str(cfg.get('secret',''))); self.secret_input.setEchoMode(QLineEdit.Password); self.symbol_input=QLineEdit(str(cfg.get('symbol','EURIUSDT'))); f.addRow('API KEY',self.api_key_input); f.addRow('SECRET',self.secret_input); f.addRow('SYMBOL',self.symbol_input); l.addLayout(f); b=QHBoxLayout(); s=QPushButton('SAVE'); s.clicked.connect(self.handle_save); t=QPushButton('TEST CONNECTION'); t.clicked.connect(self.handle_test); b.addWidget(s); b.addWidget(t); l.addLayout(b)
    def handle_save(self): self.cfg['api_key']=self.api_key_input.text().strip(); self.cfg['secret']=self.secret_input.text().strip(); self.cfg['symbol']=self.symbol_input.text().strip().upper() or 'EURIUSDT'; save_config_values(self.cfg); self.accept()
    def handle_test(self):
        try: r=self.api.test_connection(self.symbol_input.text().strip().upper() or 'EURIUSDT',self.api_key_input.text().strip(),self.secret_input.text().strip()); QMessageBox.information(self,'Connection test',f"{r['status']}\nendpoint: {r['endpoint']}\nlatency: {r['latency_ms']} ms\naccount: {r.get('account','SKIPPED')}")
        except Exception as exc: QMessageBox.critical(self,'Connection test',f'ERROR\n{exc}')

class SimplBWindow(QMainWindow):
    stop_worker_signal=Signal()
    def __init__(self):
        super().__init__(); self.logger=setup_logger(); self.cfg=load_config(); self.api=BinanceAPI(); self.symbol=str(self.cfg.get('symbol','EURIUSDT')); self.worker_thread=None; self.worker=None; self.last_book={}; self.last_balances={}
        self.trading_state='IDLE'; self.active_order_id='-'; self.entry_price=0.0; self.exit_price=0.0; self.qty=0.0; self.filled_qty=0.0; self.realized_pnl=0.0; self.current_cycle=0; self.last_action='-'; self.filters={}; self.tick_timer=QTimer(self); self.tick_timer.setInterval(1000); self.tick_timer.timeout.connect(self._trading_tick)
        self._build_ui(); self._set_status('RED')
    def _build_ui(self):
        self.setWindowTitle('SimplB Terminal'); self.resize(1000,620); root=QWidget(); layout=QVBoxLayout(root); mono=QFont('Consolas',10); self.top_status=QLabel(); layout.addWidget(self.top_status)
        self.market_labels={}; self.stats_labels={}; self.account_labels={}; self.trading_labels={}
        row=QHBoxLayout(); row.addWidget(self._build_group('MARKET',['BID','ASK','MID','SPREAD','SPREAD %'],self.market_labels,mono)); row.addWidget(self._build_group('24H',['LAST','VOLUME','QUOTE VOL','CHANGE %'],self.stats_labels,mono)); layout.addLayout(row)
        layout.addWidget(self._build_group('ACCOUNT',['USDT free','USDT locked','USDT total','EURI free','EURI locked','EURI total','API STATUS'],self.account_labels,mono))
        layout.addWidget(self._build_trading_group(mono))
        btns=QHBoxLayout(); self.start_btn=QPushButton('START'); self.stop_btn=QPushButton('STOP'); self.settings_btn=QPushButton('SETTINGS'); self.test_btn=QPushButton('TEST API');
        self.start_trade_btn=QPushButton('START TRADING'); self.stop_trade_btn=QPushButton('STOP TRADING'); self.emergency_btn=QPushButton('EMERGENCY STOP')
        self.start_btn.clicked.connect(self.start_monitoring); self.stop_btn.clicked.connect(self.stop_monitoring); self.settings_btn.clicked.connect(self.open_settings); self.test_btn.clicked.connect(self.test_api)
        self.start_trade_btn.clicked.connect(self.start_trading); self.stop_trade_btn.clicked.connect(self.stop_trading); self.emergency_btn.clicked.connect(self.emergency_stop)
        for b in [self.start_btn,self.stop_btn,self.settings_btn,self.test_btn,self.start_trade_btn,self.stop_trade_btn,self.emergency_btn]: btns.addWidget(b)
        layout.addLayout(btns); self.setCentralWidget(root); self._update_trading_ui('IDLE')
    def _build_group(self,title,rows,target,mono):
        box=QGroupBox(title); g=QGridLayout(box)
        for i,r in enumerate(rows): key=r.lower().replace(' ','_'); g.addWidget(QLabel(r),i,0); v=QLabel('-'); v.setFont(mono); v.setAlignment(Qt.AlignRight|Qt.AlignVCenter); g.addWidget(v,i,1); target[key]=v
        return box
    def _build_trading_group(self,mono):
        rows=['Mode','Budget USDT','Order size USDT','Tick size','Step ticks','Status','Current cycle','Last action','Last order id','Entry price','Exit price','Realized PnL','FSM state','Active order id','Buy price','Sell price','Qty','Filled qty','Errors']
        box=self._build_group('TRADING',rows,self.trading_labels,mono)
        self.trading_labels['mode'].setText('LIVE'); self.trading_labels['budget_usdt'].setText('100'); self.trading_labels['order_size_usdt'].setText('10'); self.trading_labels['step_ticks'].setText('1')
        return box
    def _set_status(self,status,latency='-'): self.top_status.setText(f'STATUS | {status} | SYMBOL | {self.symbol} | LATENCY | {latency}')
    def _update_trading_ui(self,state,error=''):
        self.trading_state=state; self.trading_labels['status'].setText(state); self.trading_labels['fsm_state'].setText(state); self.trading_labels['current_cycle'].setText(str(self.current_cycle)); self.trading_labels['last_action'].setText(self.last_action); self.trading_labels['last_order_id'].setText(str(self.active_order_id)); self.trading_labels['active_order_id'].setText(str(self.active_order_id)); self.trading_labels['entry_price'].setText(f'{self.entry_price:.6f}' if self.entry_price else '-'); self.trading_labels['exit_price'].setText(f'{self.exit_price:.6f}' if self.exit_price else '-'); self.trading_labels['buy_price'].setText(self.trading_labels['entry_price'].text()); self.trading_labels['sell_price'].setText(self.trading_labels['exit_price'].text()); self.trading_labels['qty'].setText(f'{self.qty:.6f}' if self.qty else '-'); self.trading_labels['filled_qty'].setText(f'{self.filled_qty:.6f}' if self.filled_qty else '-'); self.trading_labels['realized_pnl'].setText(f'{self.realized_pnl:.6f}'); self.trading_labels['errors'].setText(error or '-')
    def start_trading(self):
        try:
            self._check_safety(); self.current_cycle += 1; self.last_action='START_TRADING'; self.logger.info('START_TRADING')
            bid=float(self.last_book.get('bidPrice','0')); self.entry_price=round_price_to_tick(bid,self.filters['tick_size']); self.qty=round_qty_to_step(float(self.trading_labels['order_size_usdt'].text())/self.entry_price,self.filters['step_size'])
            if self.qty <= 0 or not validate_min_notional(self.qty,self.entry_price,self.filters['min_notional']): raise RuntimeError('minNotional violation after rounding')
            self._place_buy(); self.tick_timer.start()
        except Exception as exc:
            self.logger.error('ERROR %s',exc); self._update_trading_ui('ERROR',str(exc))
    def _check_safety(self):
        self._update_trading_ui('CHECKING'); api_key=str(self.cfg.get('api_key','')); secret=str(self.cfg.get('secret',''))
        if not api_key or not secret: raise RuntimeError('API keys missing')
        if self.symbol != 'EURIUSDT': raise RuntimeError('Only EURIUSDT allowed')
        if self.account_labels['api_status'].text() != 'CONNECTED': raise RuntimeError('Account not connected')
        if float(self.account_labels['usdt_free'].text() or 0) < float(self.trading_labels['order_size_usdt'].text()): raise RuntimeError('USDT balance too low')
        info=self.api.get_exchange_info(self.symbol).data['symbols'][0];
        for f in info['filters']:
            if f['filterType']=='PRICE_FILTER': self.filters['tick_size']=float(f['tickSize'])
            if f['filterType']=='LOT_SIZE': self.filters['step_size']=float(f['stepSize'])
            if f['filterType']=='MIN_NOTIONAL': self.filters['min_notional']=float(f['minNotional'])
        self.trading_labels['tick_size'].setText(str(self.filters.get('tick_size','-')))
    def _place_buy(self):
        r=self.api.place_limit_buy(self.symbol,self.qty,self.entry_price,str(self.cfg.get('api_key','')),str(self.cfg.get('secret',''))).data; self.active_order_id=r.get('orderId','-'); self.last_action='PLACE_BUY'; self.logger.info('PLACE_BUY order_id=%s',self.active_order_id); self._update_trading_ui('WAIT_BUY_FILL')
    def _place_sell(self):
        r=self.api.place_limit_sell(self.symbol,self.qty,self.exit_price,str(self.cfg.get('api_key','')),str(self.cfg.get('secret',''))).data; self.active_order_id=r.get('orderId','-'); self.last_action='PLACE_SELL'; self.logger.info('PLACE_SELL order_id=%s',self.active_order_id); self._update_trading_ui('WAIT_SELL_FILL')
    def _trading_tick(self):
        if self.trading_state not in {'WAIT_BUY_FILL','WAIT_SELL_FILL'}: return
        o=self.api.get_order(self.symbol,int(self.active_order_id),str(self.cfg.get('api_key','')),str(self.cfg.get('secret',''))).data
        self.filled_qty=float(o.get('executedQty','0') or 0); self._update_trading_ui(self.trading_state)
        if o.get('status')!='FILLED': return
        if self.trading_state=='WAIT_BUY_FILL':
            quote=float(o.get('cummulativeQuoteQty','0') or 0); self.entry_price=quote/max(self.filled_qty,1e-12); self.exit_price=round_price_to_tick(self.entry_price+self.filters['tick_size']*int(self.trading_labels['step_ticks'].text()),self.filters['tick_size']); self.logger.info('BUY_FILLED order_id=%s',self.active_order_id); self._place_sell()
        else:
            quote=float(o.get('cummulativeQuoteQty','0') or 0); self.realized_pnl=quote-(self.entry_price*self.filled_qty); self.last_action='SELL_FILLED'; self.logger.info('SELL_FILLED order_id=%s',self.active_order_id); self.logger.info('PNL %.8f',self.realized_pnl); self.tick_timer.stop(); self._update_trading_ui('DONE')
    def stop_trading(self):
        self.tick_timer.stop();
        if str(self.active_order_id).isdigit():
            self.api.cancel_order(self.symbol,int(self.active_order_id),str(self.cfg.get('api_key','')),str(self.cfg.get('secret',''))); self.logger.info('CANCEL order_id=%s',self.active_order_id)
        self.logger.info('STOP'); self._update_trading_ui('STOPPED')
    def emergency_stop(self):
        self.tick_timer.stop();
        try:
            orders=self.api.get_open_orders(self.symbol,str(self.cfg.get('api_key','')),str(self.cfg.get('secret',''))).data
            for o in orders: self.api.cancel_order(self.symbol,int(o['orderId']),str(self.cfg.get('api_key','')),str(self.cfg.get('secret',''))); self.logger.info('CANCEL order_id=%s',o['orderId'])
        finally:
            self._update_trading_ui('EMERGENCY_STOPPED'); self.logger.error('STOP')
    # existing monitoring handlers omitted for brevity but functional below
    def open_settings(self):
        d=SettingsDialog(self,self.cfg,self.api)
        if d.exec(): self.cfg=load_config(); self.symbol=str(self.cfg.get('symbol','EURIUSDT'))
    def start_monitoring(self):
        if self.worker_thread and self.worker_thread.isRunning(): return
        self.worker_thread=QThread(self); self.worker=MarketWorker(self.symbol,self.api.current_endpoint,str(self.cfg.get('api_key','')),str(self.cfg.get('secret',''))); self.worker.moveToThread(self.worker_thread); self.worker_thread.started.connect(self.worker.start); self.stop_worker_signal.connect(self.worker.stop); self.worker.data_ready.connect(self.on_market_data); self.worker.account_ready.connect(self.on_account_data); self.worker.error.connect(self.on_worker_error); self.worker.account_error.connect(self.on_account_error); self.worker_thread.start()
    def stop_monitoring(self):
        if self.worker_thread and self.worker: self.stop_worker_signal.emit(); self.worker_thread.quit(); self.worker_thread.wait(2000); self.worker_thread=None; self.worker=None
    def on_market_data(self,payload): self.last_book=payload['book']
    def on_account_data(self,payload):
        status=payload.get('status','UNKNOWN'); bal=payload.get('balances',{})
        usdt=bal.get('USDT',{}); euri=bal.get('EURI',{})
        self.account_labels['usdt_free'].setText(f"{usdt.get('free',0.0):.4f}"); self.account_labels['usdt_locked'].setText(f"{usdt.get('locked',0.0):.4f}"); self.account_labels['usdt_total'].setText(f"{usdt.get('total',0.0):.4f}"); self.account_labels['euri_free'].setText(f"{euri.get('free',0.0):.4f}"); self.account_labels['euri_locked'].setText(f"{euri.get('locked',0.0):.4f}"); self.account_labels['euri_total'].setText(f"{euri.get('total',0.0):.4f}"); self.account_labels['api_status'].setText(status)
    def on_worker_error(self,m): self.logger.error(m)
    def on_account_error(self,m): self.logger.error(m)
    def test_api(self):
        QMessageBox.information(self, "TEST API", "Use SETTINGS -> TEST CONNECTION")

if __name__=='__main__':
    app=QApplication(sys.argv); w=SimplBWindow(); w.show(); sys.exit(app.exec())
