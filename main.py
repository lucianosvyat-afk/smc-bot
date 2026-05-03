import ccxt, pandas as pd, numpy as np, time, requests, json, os, threading
from datetime import datetime
from bot2 import run_bot2
from bot3_forex import run_bot3

LTF="15m"; HTF="1h"; MTF="4h"
LEVERAGE=10; RISK_PERCENT=1.0
START_BALANCE=1000.0; SWING_LOOKBACK=5
TOP_PAIRS=5; UPDATE_PAIRS_EVERY=30
MAX_DAILY_TRADES=3
DAILY_STOP_LOSS=3.0
TP1_RR=1.0; TP2_RR=2.0
LOG_FILE="paper_trades.json"

TELEGRAM_TOKEN=""
TELEGRAM_CHAT_ID=""

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        url=f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url,data={"chat_id":TELEGRAM_CHAT_ID,"text":f"🤖 БОТ 1\n{msg}"},timeout=5)
    except: pass

def connect():
    print("Подключаюсь к OKX...")
    ex=ccxt.okx({"options":{"defaultType":"swap"}})
    ex.load_markets()
    print("Подключён!")
    return ex

def fetch(ex,sym,tf,limit=150):
    raw=ex.fetch_ohlcv(sym,tf,limit=limit)
    df=pd.DataFrame(raw,columns=["ts","open","high","low","close","volume"])
    df["ts"]=pd.to_datetime(df["ts"],unit="ms")
    df.set_index("ts",inplace=True)
    return df

def get_price(ex,sym):
    return ex.fetch_ticker(sym)["last"]

def get_top_volatile(ex, top_n=5):
    try:
        tickers=ex.fetch_tickers()
        pairs=[]
        for symbol, t in tickers.items():
            if "USDT:USDT" not in symbol: continue
            if not t.get("percentage"): continue
            volatility=abs(t.get("percentage",0))
            if volatility < 1: continue
            pairs.append({"symbol":symbol,"volatility":volatility,"change":t["percentage"]})
        pairs.sort(key=lambda x: x["volatility"], reverse=True)
        top=pairs[:top_n]
        print(f"  Найдено пар: {len(pairs)}, беру топ {top_n}")
        for i,p in enumerate(top,1):
            print(f"  {i}. {p['symbol']} | {p['change']:+.1f}%")
        if not top:
            return ["BTC/USDT:USDT","ETH/USDT:USDT","SOL/USDT:USDT","BNB/USDT:USDT","XRP/USDT:USDT"]
        return [p["symbol"] for p in top]
    except Exception as e:
        print(f"Ошибка получения пар: {e}")
        return ["BTC/USDT:USDT","ETH/USDT:USDT","SOL/USDT:USDT"]

def swings(df,n=5):
    df=df.copy()
    df["sh"]=df["high"].where(df["high"]==df["high"].rolling(n*2+1,center=True).max())
    df["sl"]=df["low"].where(df["low"]==df["low"].rolling(n*2+1,center=True).min())
    return df

def get_avg_volume(df, period=20):
    return df["volume"].rolling(period).mean().iloc[-1]

def bos(df):
    df=swings(df,SWING_LOOKBACK)
    h=df["sh"].dropna(); l=df["sl"].dropna()
    if len(h)<2 or len(l)<2: return None
    c=df["close"].iloc[-1]
    if c>h.iloc[-2] and l.iloc[-1]>l.iloc[-2]: return "bullish"
    if c<l.iloc[-2] and h.iloc[-1]<h.iloc[-2]: return "bearish"
    return None

def signal(ex,symbol,trend,mtf_trend):
    if mtf_trend and mtf_trend != trend: return None
    df=fetch(ex,symbol,LTF,100)
    price=df["close"].iloc[-1]
    sp=swings(df,SWING_LOOKBACK)
    h=sp["sh"].dropna(); l=sp["sl"].dropna()
    if h.empty or l.empty: return None
    d=h.iloc[-1]-l.iloc[-1]
    if d==0: return None
    if trend=="bullish":
        ote_top=h.iloc[-1]-d*0.62; ote_bot=h.iloc[-1]-d*0.79
    else:
        ote_top=l.iloc[-1]+d*0.79; ote_bot=l.iloc[-1]+d*0.62
    ote=ote_bot<=price<=ote_top
    last=df.iloc[-1]; pprev=df.iloc[-3]
    sfp_pattern=(trend=="bullish" and last["low"]<pprev["low"] and last["close"]>pprev["low"]) or \
                (trend=="bearish" and last["high"]>pprev["high"] and last["close"]<pprev["high"])
    avg_vol=get_avg_volume(df)
    sfp=sfp_pattern and last["volume"]>=avg_vol*1.5
    if ote or sfp:
        if trend=="bullish":
            sl=l.iloc[-1]*0.999
            tp1=price+(price-sl)*TP1_RR
            tp2=price+(price-sl)*TP2_RR
            return {"side":"buy","entry":price,"sl":sl,"tp1":tp1,"tp2":tp2,"symbol":symbol,"qty_closed":False,"strategy":"BOS+OTE+SFP"}
        else:
            sl=h.iloc[-1]*1.001
            tp1=price-(sl-price)*TP1_RR
            tp2=price-(sl-price)*TP2_RR
            return {"side":"sell","entry":price,"sl":sl,"tp1":tp1,"tp2":tp2,"symbol":symbol,"qty_closed":False,"strategy":"BOS+OTE+SFP"}
    return None

class Trader:
    def __init__(self):
        self.bal=START_BALANCE; self.pos=None
        self.wins=0; self.losses=0; self.trades=[]
        self.daily_trades=0; self.daily_loss=0.0
        self.last_day=datetime.now().date()
        self._load()

    def _load(self):
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE) as f:
                d=json.load(f)
                self.bal=d.get("balance",START_BALANCE)
                self.wins=d.get("wins",0)
                self.losses=d.get("losses",0)
                self.trades=d.get("trades",[])
                self.daily_trades=d.get("daily_trades",0)
                self.daily_loss=d.get("daily_loss",0.0)
            print(f"[БОТ1] Загружено | Баланс: {self.bal:.2f} USDT")

    def save(self):
        with open(LOG_FILE,"w") as f:
            json.dump({
                "balance":self.bal,"wins":self.wins,
                "losses":self.losses,"trades":self.trades[-50:],
                "daily_trades":self.daily_trades,
                "daily_loss":self.daily_loss,"position":self.pos
            },f,indent=2)

    def reset_daily(self):
        today=datetime.now().date()
        if today!=self.last_day:
            self.daily_trades=0; self.daily_loss=0.0
            self.last_day=today

    def can_trade(self):
        self.reset_daily()
        if self.daily_trades>=MAX_DAILY_TRADES:
            print(f"[БОТ1] ⛔ Лимит сделок за день")
            return False
        if self.daily_loss>=self.bal*(DAILY_STOP_LOSS/100):
            print(f"[БОТ1] ⛔ Дневной стоп-лосс достигнут")
            return False
        return True

    def open(self,sig):
        if not self.can_trade(): return
        risk=self.bal*(RISK_PERCENT/100)
        sl_d=abs(sig["entry"]-sig["sl"])
        qty=(risk/sl_d)*LEVERAGE if sl_d>0 else 0
        self.pos={**sig,"qty":qty}
        self.daily_trades+=1
        self.save()
        msg=(f"{'📈' if sig['side']=='buy' else '📉'} ПОЗИЦИЯ: {sig['side'].upper()}\n"
             f"Пара: {sig['symbol']}\n"
             f"Стратегия: {sig.get('strategy','')}\n"
             f"Вход: {sig['entry']:.4f}\n"
             f"SL: {sig['sl']:.4f}\n"
             f"TP1: {sig['tp1']:.4f}\n"
             f"TP2: {sig['tp2']:.4f}\n"
             f"Риск: {risk:.2f} USDT")
        print(f"\n[БОТ1] {'='*40}\n{msg}\n{'='*40}")
        send_telegram(msg)

    def check(self,price,symbol):
        if not self.pos: return
        if self.pos.get("symbol")!=symbol: return
        s=self.pos["side"]
        entry=self.pos["entry"]
        sl=self.pos["sl"]
        tp1=self.pos["tp1"]
        tp2=self.pos["tp2"]
        qty=self.pos["qty"]
        if not self.pos["qty_closed"]:
            hit_tp1=(s=="buy" and price>=tp1) or (s=="sell" and price<=tp1)
            if hit_tp1:
                pnl_half=(tp1-entry)*(qty/2) if s=="buy" else (entry-tp1)*(qty/2)
                self.bal+=pnl_
