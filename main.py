import ccxt, pandas as pd, numpy as np, time
from datetime import datetime

SYMBOL="BTC/USDT:USDT"; LTF="15m"; HTF="1h"
LEVERAGE=10; RISK_PERCENT=1.0; TP_RR=2.0
START_BALANCE=1000.0; SWING_LOOKBACK=5

def connect():
    print("Подключаюсь к OKX...")
    ex=ccxt.okx({"options":{"defaultType":"swap"}})
    ex.load_markets()
    price=ex.fetch_ticker(SYMBOL)["last"]
    print(f"Подключён! BTC: {price:.2f}")
    return ex

def fetch(ex,sym,tf,limit=150):
    raw=ex.fetch_ohlcv(sym,tf,limit=limit)
    df=pd.DataFrame(raw,columns=["ts","open","high","low","close","volume"])
    df["ts"]=pd.to_datetime(df["ts"],unit="ms")
    df.set_index("ts",inplace=True)
    return df

def get_price(ex,sym):
    return ex.fetch_ticker(sym)["last"]

def swings(df,n=5):
    df=df.copy()
    df["sh"]=df["high"].where(df["high"]==df["high"].rolling(n*2+1,center=True).max())
    df["sl"]=df["low"].where(df["low"]==df["low"].rolling(n*2+1,center=True).min())
    return df

def bos(df):
    df=swings(df,SWING_LOOKBACK)
    h=df["sh"].dropna(); l=df["sl"].dropna()
    if len(h)<2 or len(l)<2: return None
    c=df["close"].iloc[-1]
    if c>h.iloc[-2] and l.iloc[-1]>l.iloc[-2]: return "bullish"
    if c<l.iloc[-2] and h.iloc[-1]<h.iloc[-2]: return "bearish"
    return None

def signal(ex,trend):
    df=fetch(ex,SYMBOL,LTF,100)
    price=df["close"].iloc[-1]
    sp=swings(df,SWING_LOOKBACK)
    h=sp["sh"].dropna(); l=sp["sl"].dropna()
    if h.empty or l.empty: return None
    d=h.iloc[-1]-l.iloc[-1]
    if trend=="bullish":
        ote_top=h.iloc[-1]-d*0.62; ote_bot=h.iloc[-1]-d*0.79
    else:
        ote_top=l.iloc[-1]+d*0.79; ote_bot=l.iloc[-1]+d*0.62
    ote=ote_bot<=price<=ote_top
    last=df.iloc[-1]; pprev=df.iloc[-3]
    sfp=(trend=="bullish" and last["low"]<pprev["low"] and last["close"]>pprev["low"]) or \
        (trend=="bearish" and last["high"]>pprev["high"] and last["close"]<pprev["high"])
    if ote and sfp:
        if trend=="bullish":
            sl=l.iloc[-1]*0.999; tp=price+(price-sl)*TP_RR
            return {"side":"buy","entry":price,"sl":sl,"tp":tp}
        else:
            sl=h.iloc[-1]*1.001; tp=price-(sl-price)*TP_RR
            return {"side":"sell","entry":price,"sl":sl,"tp":tp}
    return None

class Trader:
    def __init__(self):
        self.bal=START_BALANCE; self.pos=None
        self.wins=0; self.losses=0; self.trades=[]

    def open(self,sig):
        risk=self.bal*(RISK_PERCENT/100)
        sl_d=abs(sig["entry"]-sig["sl"])
        qty=(risk/sl_d)*LEVERAGE if sl_d>0 else 0
        self.pos={**sig,"qty":qty}
        print(f"\n{'='*40}")
        print(f"ПОЗИЦИЯ: {sig['side'].upper()}")
        print(f"Вход: {sig['entry']:.2f} | SL: {sig['sl']:.2f} | TP: {sig['tp']:.2f}")
        print(f"Риск: {risk:.2f} USDT")
        print(f"{'='*40}")

    def check(self,price):
        if not self.pos: return
        s=self.pos["side"]
        hit_tp=(s=="buy" and price>=self.pos["tp"]) or (s=="sell" and price<=self.pos["tp"])
        hit_sl=(s=="buy" and price<=self.pos["sl"]) or (s=="sell" and price>=self.pos["sl"])
        if hit_tp or hit_sl:
            ep=self.pos["tp"] if hit_tp else self.pos["sl"]
            pnl=(ep-self.pos["entry"])*self.pos["qty"] if s=="buy" else (self.pos["entry"]-ep)*self.pos["qty"]
            self.bal+=pnl
            print(f"\n{'ТЕЙК ПРОФИТ' if hit_tp else 'СТОП ЛОСС'}")
            print(f"Выход: {ep:.2f} | PnL: {pnl:+.2f} | Баланс: {self.bal:.2f}")
            if hit_tp: self.wins+=1
            else: self.losses+=1
            self.trades.append({"side":s,"entry":self.pos["entry"],"exit":ep,
                                 "pnl":round(pnl,2),"result":"win" if hit_tp else "loss"})
            self.pos=None

    def status(self,price):
        total=self.wins+self.losses
        wr=(self.wins/total*100) if total>0 else 0
        print(f"\n{'─'*40}")
        print(f"Баланс: {self.bal:.2f} USDT")
        print(f"Позиция: {'Нет' if not self.pos else self.pos['side'].upper()}")
        print(f"Счёт: {self.wins}W/{self.losses}L | WR: {wr:.1f}%")
        print(f"Сделок: {total}")
        print(f"{'─'*40}")

ex=connect()
trader=Trader()
scan=0

print(f"\nSMC BOT запущен! {SYMBOL} | {START_BALANCE} USDT\n")

while True:
    try:
        scan+=1
        price=get_price(ex,SYMBOL)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] #{scan} | BTC: {price:.2f}")
        trader.check(price)
        if trader.pos:
            print(f"Позиция: {trader.pos['side'].upper()} @ {trader.pos['entry']:.2f} | SL:{trader.pos['sl']:.2f} | TP:{trader.pos['tp']:.2f}")
        else:
            trend=bos(fetch(ex,SYMBOL,HTF,100))
            print(f"HTF: {trend or 'нет тренда'}")
            if trend:
                sig=signal(ex,trend)
                if sig: trader.open(sig)
                else: print("Сигнала нет")
        if scan%5==0: trader.status(price)
        time.sleep(60)
    except KeyboardInterrupt:
        print("\nОстановлен.")
        break
    except Exception as e:
        print(f"Ошибка: {e}")
        time.sleep(15)
