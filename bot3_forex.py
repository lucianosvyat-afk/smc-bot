import ccxt, pandas as pd, numpy as np, time, requests, os
from datetime import datetime
from database import load_state, save_state, save_trade

FOREX_PAIRS = [
    "EUR/USDT:USDT",   # Евро
    "GBP/USDT:USDT",   # Фунт
    "AUD/USDT:USDT",   # Австралийский доллар
    "JPY/USDT:USDT",   # Японская йена
    "CAD/USDT:USDT",   # Канадский доллар
    "CHF/USDT:USDT",   # Швейцарский франк
    "NZD/USDT:USDT",   # Новозеландский доллар
    "XAU/USDT:USDT",   # Золото
    "XAG/USDT:USDT",   # Серебро
]

LTF="5m"; MTF="15m"; HTF="1h"
LEVERAGE=10; RISK_PERCENT=0.5
START_BALANCE=1000.0; SWING_LOOKBACK=5
MAX_DAILY_TRADES=3; DAILY_STOP_LOSS=2.0
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

def detect_bos(df):
    """BOS — определяем тренд"""
    df=swings(df, SWING_LOOKBACK)
    h=df["sh"].dropna(); l=df["sl"].dropna()
    if len(h)<2 or len(l)<2: return None
    c=df["close"].iloc[-1]
    if c>h.iloc[-2] and l.iloc[-1]>l.iloc[-2]: return "bullish"
    if c<l.iloc[-2] and h.iloc[-1]<h.iloc[-2]: return "bearish"
    return None

def find_fvg(df, trend, lookback=20):
    """Ищем FVG в направлении тренда"""
    recent=df.tail(lookback)
    fvgs=[]
    for i in range(1,len(recent)-1):
        p=recent.iloc[i-1]; n=recent.iloc[i+1]
        if trend=="bullish" and p["high"]<n["low"]:
            fvgs.append({
                "type":"bullish",
                "top":n["low"],
                "bot":p["high"],
                "mid":(n["low"]+p["high"])/2
            })
        if trend=="bearish" and p["low"]>n["high"]:
            fvgs.append({
                "type":"bearish",
                "top":p["low"],
                "bot":n["high"],
                "mid":(p["low"]+n["high"])/2
            })
    # Возвращаем самый свежий FVG
    return fvgs[-1] if fvgs else None

def get_signal3(ex, symbol):
    try:
        if not is_forex_session():
            return None, "Не торговая сессия"

        # Шаг 1: HTF тренд (1h)
        df_htf=fetch(ex,symbol,HTF,100)
        htf_trend=detect_bos(df_htf)
        if not htf_trend:
            return None, "Нет HTF тренда"

        # Шаг 2: MTF подтверждение (15m)
        df_mtf=fetch(ex,symbol,MTF,100)
        mtf_trend=detect_bos(df_mtf)

        # MTF должен совпадать с HTF или быть нейтральным
        if mtf_trend and mtf_trend != htf_trend:
            return None, f"MTF против HTF тренда"

        # Шаг 3: FVG на LTF (5m)
        df_ltf=fetch(ex,symbol,LTF,100)
        price=df_ltf["close"].iloc[-1]
        fvg=find_fvg(df_ltf, htf_trend, lookback=30)

        if not fvg:
            return None, "Нет FVG"

        # Шаг 4: Цена в FVG?
        if not (fvg["bot"]<=price<=fvg["top"]):
            return None, f"Цена не в FVG ({fvg['bot']:.5f}-{fvg['top']:.5f})"

        # Генерируем сигнал
        df_swings=swings(df_ltf, SWING_LOOKBACK)
        highs=df_swings["sh"].dropna()
        lows=df_swings["sl"].dropna()

        if htf_trend=="bullish":
            sl=lows.iloc[-1]*0.999 if not lows.empty else fvg["bot"]*0.999
            tp1=price+(price-sl)*TP1_RR
            tp2=price+(price-sl)*TP2_RR
            return {
                "side":"buy","entry":price,"sl":sl,
                "tp1":tp1,"tp2":tp2,"symbol":symbol,
                "qty_closed":False,"strategy":"Forex:BOS+FVG"
            }, f"✅ ЛОНГ! HTF:{htf_trend}"
        else:
            sl=highs.iloc[-1]*1.001 if not highs.empty else fvg["top"]*1.001
            tp1=price-(sl-price)*TP1_RR
            tp2=price-(sl-price)*TP2_RR
            return {
                "side":"sell","entry":price,"sl":sl,
                "tp1":tp1,"tp2":tp2,"symbol":symbol,
                "qty_closed":False,"strategy":"Forex:BOS+FVG"
            }, f"✅ ШОРТ! HTF:{htf_trend}"

    except Exception as e:
        return None, f"Ошибка: {e}"

def is_forex_session():
    hour=datetime.utcnow().hour
    day=datetime.utcnow().weekday()
    if day>=5: return False
    london=(8<=hour<17)
    newyork=(13<=hour<22)
    tokyo=(0<=hour<9)
    sydney=(21<=hour<24) or (0<=hour<7)
    return london or newyork or tokyo or sydney

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
                "strategy": self.pos.get("strategy","Forex:BOS+FVG")
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
        print(f"[БОТ3] 💱 ФОРЕКС BOS+FVG | Сессия: {session}")
        print(f"[БОТ3] 💰 Баланс: {self.bal:.2f} USDT ({profit:+.2f})")
        print(f"[БОТ3] 📊 Позиция: {'Нет' if not self.pos else self.pos['side'].upper()}")
        print(f"[БОТ3] 🏆 {self.wins}W/{self.losses}L | WR: {wr:.1f}%")
        print(f"[БОТ3] Пары: {len(FOREX_PAIRS)}")
        print(f"[БОТ3] {'─'*40}")

def run_bot3(ex):
    trader3=Trader3()
    scan=0
    print("\n[БОТ3] 🚀 Форекс бот запущен! Стратегия: BOS+FVG")
    print(f"[БОТ3] Пары: {[p.replace('/USDT:USDT','') for p in FOREX_PAIRS]}")
    print(f"[БОТ3] HTF:{HTF} MTF:{MTF} LTF:{LTF}")

    while True:
        try:
            scan+=1
            if not is_forex_session():
                if scan%5==0:
                    print(f"[БОТ3] 🔴 Форекс закрыт — жду сессии...")
                time.sleep(60)
                continue

            for symbol in FOREX_PAIRS:
                try:
                    price=get_price(ex,symbol)
                    name=symbol.replace("/USDT:USDT","")
                    trader3.check(price,symbol)
                    if trader3.pos and trader3.pos.get("symbol")==symbol:
                        print(f"[БОТ3] ⏳ {name}: {trader3.pos['side'].upper()} @ {trader3.pos['entry']:.5f}")
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
