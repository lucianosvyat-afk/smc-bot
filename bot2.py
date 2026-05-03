import ccxt, pandas as pd, numpy as np, time, requests, json, os
from datetime import datetime
from database import load_state, save_state, save_trade

FOREX_PAIRS = [
    "EUR/USDT:USDT",
    "GBP/USDT:USDT",
    "AUD/USDT:USDT",
]

LTF="15m"; MTF="1h"; HTF="4h"
LEVERAGE=10; RISK_PERCENT=0.5
START_BALANCE=1000.0; SWING_LOOKBACK=5
MAX_DAILY_TRADES=2; DAILY_STOP_LOSS=2.0
TP1_RR=1.5; TP2_RR=3.0

TELEGRAM_TOKEN=""
TELEGRAM_CHAT_ID=""

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        url=f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url,data={"chat_id":TELEGRAM_CHAT_ID,"text":f"💱 БОТ 3 ФОРЕКС\n{msg}"},timeout=5)
    except: pass

def fetch(ex,sym,tf,limit=200):
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

def detect_accumulation(df, lookback=20, threshold=0.03):
    recent=df.tail(lookback)
    high=recent["high"].max()
    low=recent["low"].min()
    range_pct=(high-low)/low
    return {
        "is_accumulation": range_pct<threshold,
        "range_high": high,
        "range_low": low,
        "range_pct": range_pct,
        "mid": (high+low)/2
    }

def detect_manipulation(df_mtf, accum):
    if not accum["is_accumulation"]: return None
    last=df_mtf.iloc[-1]
    bull=(last["low"]<accum["range_low"] and last["close"]>accum["range_low"])
    bear=(last["high"]>accum["range_high"] and last["close"]<accum["range_high"])
    if bull: return {"type":"bullish","sweep":accum["range_low"]}
    if bear: return {"type":"bearish","sweep":accum["range_high"]}
    return None

def find_fvg(df, manip_type, lookback=15):
    recent=df.tail(lookback)
    fvgs=[]
    for i in range(1,len(recent)-1):
        p=recent.iloc[i-1]; n=recent.iloc[i+1]
        if manip_type=="bullish" and p["high"]<n["low"]:
            fvgs.append({"type":"bullish","top":n["low"],"bot":p["high"],"mid":(n["low"]+p["high"])/2})
        if manip_type=="bearish" and p["low"]>n["high"]:
            fvgs.append({"type":"bearish","top":p["low"],"bot":n["high"],"mid":(p["low"]+n["high"])/2})
    return fvgs[-1] if fvgs else None

def is_forex_session():
    hour=datetime.utcnow().hour
    day=datetime.utcnow().weekday()
    if day>=5: return False
    london=(8<=hour<17)
    newyork=(13<=hour<22)
    return london or newyork

def get_signal3(ex, symbol):
    try:
        if not is_forex_session():
            return None, "Не торговая сессия"
        df_htf=fetch(ex,symbol,HTF,100)
        df_mtf=fetch(ex,symbol,MTF,100)
        df_ltf=fetch(ex,symbol,LTF,100)
        price=df_ltf["close"].iloc[-1]
        accum=detect_accumulation(df_htf,lookback=20,threshold=0.04)
        if not accum["is_accumulation"]:
            return None, "Нет аккумуляции"
        manip=detect_manipulation(df_mtf,accum)
        if not manip:
            return None, "Нет манипуляции"
        fvg=find_fvg(df_ltf,manip["type"])
        if not fvg:
            return None, "Нет FVG"
        if not (fvg["bot"]<=price<=fvg["top"]):
            return None, "Цена не в FVG"
        if manip["type"]=="bullish":
            sl=accum["range_low"]*0.999
            tp1=price+(price-sl)*TP1_RR
            tp2=price+(price-sl)*TP2_RR
            return {
                "side":"buy","entry":price,"sl":sl,
                "tp1":tp1,"tp2":tp2,"symbol":symbol,
                "qty_closed":False,"strategy":"Forex:Accum→FVG"
            }, "✅ Сигнал ЛОНГ!"
        else:
            sl=accum["range_high"]*1.001
            tp1=price-(sl-price)*TP1_RR
            tp2=price-(sl-price)*TP2_RR
            return {
                "side":"sell","entry":price,"sl":sl,
                "tp1":tp1,"tp2":tp2,"symbol":symbol,
                "qty_closed":False,"strategy":"Forex:Accum→FVG"
            }, "✅ Сигнал ШОРТ!"
    except Exception as e:
        return None, f"Ошибка: {e}"

class Trader3:
    def __init__(self):
        state=load_state("bot3", START_BALANCE)
        self.bal=state["balance"]
        self.wins=state["wins"]
        self.losses=state["losses"]
        self.trades=state["trades"]
        self.daily_trades=state["daily_trades"]
        self.daily_loss=state["daily_loss"]
        self.pos=state["position"]
        self.last_day=datetime.now().date()
        print(f"[БОТ3] Загружено | Баланс: {self.bal:.2f} USDT | Сделок: {len(self.trades)}")

    def save(self):
        save_state("bot3", self.bal, self.wins, self.losses,
                   self.daily_trades, self.daily_loss, self.pos)

    def reset_daily(self):
        today=datetime.now().date()
        if today!=self.last_day:
            self.daily_trades=0; self.daily_loss=0.0
            self.last_day=today
            print("[БОТ3] 📅 Новый день — сброс лимитов")

    def can_trade(self):
        self.reset_daily()
        if self.daily_trades>=MAX_DAILY_TRADES:
            print("[БОТ3] ⛔ Лимит сделок достигнут")
            return False
        if self.daily_loss>=self.bal*(DAILY_STOP_LOSS/100):
            print("[БОТ3] ⛔ Дневной стоп достигнут")
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
        msg=(f"{'📈' if sig['side']=='buy' else '📉'} {sig['side'].upper()}\n"
             f"Пара: {sig['symbol']}\n"
             f"Стратегия: {sig.get('strategy','')}\n"
             f"Вход: {sig['entry']:.5f}\n"
             f"SL: {sig['sl']:.5f}\n"
             f"TP1: {sig['tp1']:.5f}\n"
             f"TP2: {sig['tp2']:.5f}\n"
             f"Риск: {risk:.2f} USDT")
        print(f"\n[БОТ3] {'='*40}\n{msg}\n{'='*40}")
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
                self.bal+=pnl_half
                self.pos["qty_closed"]=True
                self.pos["sl"]=entry
                self.pos["qty"]=qty/2
                self.save()
                msg=f"⚡ 50% закрыто\n{symbol}\nTP1: {tp1:.5f}\nPnL: +{pnl_half:.2f} USDT\nБаланс: {self.bal:.2f}"
                print(f"\n[БОТ3] {msg}")
                send_telegram(msg)
                return
        hit_tp2=(s=="buy" and price>=tp2) or (s=="sell" and price<=tp2)
        hit_sl=(s=="buy" and price<=sl) or (s=="sell" and price>=sl)
        if hit_tp2 or hit_sl:
            ep=tp2 if hit_tp2 else sl
            pnl=(ep-entry)*self.pos["qty"] if s=="buy" else (entry-ep)*self.pos["qty"]
            self.bal+=pnl
            if pnl<0: self.daily_loss+=abs(pnl)
            if hit_tp2: self.wins+=1
            else: self.losses+=1
            trade={
                "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "symbol": symbol, "side": s,
                "entry": entry, "exit": ep,
                "sl": self.pos.get("sl",0),
                "tp1": tp1, "tp2": tp2,
                "pnl": round(pnl,2),
                "result": "win" if hit_tp2 else "loss",
                "strategy": self.pos.get("strategy","Forex:Accum→FVG")
            }
            save_trade(trade, "bot3")
            msg=(f"{'✅ ТЕЙК' if hit_tp2 else '❌ СТОП'}\n"
                 f"{symbol} | Выход: {ep:.5f}\n"
                 f"PnL: {pnl:+.2f} USDT\n"
                 f"Баланс: {self.bal:.2f}")
            print(f"\n[БОТ3] {msg}")
            send_telegram(msg)
            self.pos=None
            self.save()

    def status(self):
        total=self.wins+self.losses
        wr=(self.wins/total*100) if total>0 else 0
        profit=self.bal-START_BALANCE
        session="🟢 Активная" if is_forex_session() else "🔴 Закрыта"
        print(f"\n[БОТ3] {'─'*40}")
        print(f"[БОТ3] 💱 ФОРЕКС | Сессия: {session}")
        print(f"[БОТ3] 💰 Баланс: {self.bal:.2f} USDT ({profit:+.2f})")
        print(f"[БОТ3] 📊 Позиция: {'Нет' if not self.pos else self.pos['side'].upper()}")
        print(f"[БОТ3] 🏆 {self.wins}W/{self.losses}L | WR: {wr:.1f}%")
        print(f"[БОТ3] {'─'*40}")

def run_bot2(ex, symbols):
    trader3=Trader3()
    scan=0
    print("\n[БОТ3] 🚀 Форекс бот запущен!")
    print(f"[БОТ3] Пары: {FOREX_PAIRS}")
    print(f"[БОТ3] Сессии: Лондон (08-17 UTC) + Нью-Йорк (13-22 UTC)")
    while True:
        try:
            scan+=1
            if not is_forex_session():
                if scan%10==0:
                    print(f"[БОТ3] 🔴 Форекс закрыт — жду сессии...")
                time.sleep(300)
                continue
            for symbol in FOREX_PAIRS:
                try:
                    price=get_price(ex,symbol)
                    name=symbol.replace("/USDT:USDT","")
                    print(f"\n[БОТ3] [{datetime.now().strftime('%H:%M:%S')}] {name}: {price:.5f}")
                    trader3.check(price,symbol)
                    if trader3.pos and trader3.pos.get("symbol")==symbol:
                        print(f"[БОТ3] ⏳ {trader3.pos['side'].upper()} @ {trader3.pos['entry']:.5f} | SL:{trader3.pos['sl']:.5f} | TP2:{trader3.pos['tp2']:.5f}")
                    else:
                        sig,reason=get_signal3(ex,symbol)
                        print(f"[БОТ3] {name}: {reason}")
                        if sig and not trader3.pos:
                            trader3.open(sig)
                except Exception as e:
                    print(f"[БОТ3] Ошибка {symbol}: {e}")
                    continue
            if scan%5==0: trader3.status()
            time.sleep(60)
        except Exception as e:
            print(f"[БОТ3] Ошибка: {e}")
            time.sleep(15)
