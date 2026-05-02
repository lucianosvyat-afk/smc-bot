import ccxt, pandas as pd, numpy as np, time, requests, json, os
from datetime import datetime

# ══════════════════════════════════════════════════
#   БОТ 2 — Стратегия: Аккумуляция→Манипуляция→FVG
# ══════════════════════════════════════════════════
LTF="15m"; MTF="1h"; HTF="4h"
LEVERAGE=10; RISK_PERCENT=1.0
START_BALANCE=1000.0; SWING_LOOKBACK=5
TOP_PAIRS=5; UPDATE_PAIRS_EVERY=30
MAX_DAILY_TRADES=3
DAILY_STOP_LOSS=3.0
TP1_RR=1.0; TP2_RR=2.0
LOG_FILE2="paper_trades_bot2.json"

TELEGRAM_TOKEN=""
TELEGRAM_CHAT_ID=""

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        url=f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url,data={"chat_id":TELEGRAM_CHAT_ID,"text":f"🤖 БОТ 2\n{msg}"},timeout=5)
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

# ── Фаза 1: Определение аккумуляции (боковик на 4h) ──
def detect_accumulation(df, lookback=20, threshold=0.03):
    """
    Боковик = цена колеблется в диапазоне threshold% последние lookback свечей
    """
    recent=df.tail(lookback)
    high=recent["high"].max()
    low=recent["low"].min()
    range_pct=(high-low)/low
    is_range=range_pct < threshold

    return {
        "is_accumulation": is_range,
        "range_high": high,
        "range_low": low,
        "range_pct": range_pct,
        "mid": (high+low)/2
    }

# ── Фаза 2: Манипуляция (свип под/над зону аккумуляции) ──
def detect_manipulation(df_htf, df_mtf, accum):
    """
    Манипуляция = цена пробила зону аккумуляции но закрылась обратно (SFP)
    """
    if not accum["is_accumulation"]:
        return None

    # Смотрим последние свечи на 1h
    last=df_mtf.iloc[-1]
    prev=df_mtf.iloc[-2]

    # Бычья манипуляция: свип вниз под range_low и закрытие выше
    bull_manip=(last["low"] < accum["range_low"] and
                last["close"] > accum["range_low"])

    # Медвежья манипуляция: свип вверх над range_high и закрытие ниже
    bear_manip=(last["high"] > accum["range_high"] and
                last["close"] < accum["range_high"])

    if bull_manip:
        return {"type": "bullish", "sweep_level": accum["range_low"]}
    if bear_manip:
        return {"type": "bearish", "sweep_level": accum["range_high"]}
    return None

# ── Фаза 3: FVG после манипуляции ──
def find_fvg_after_manipulation(df, manip_type, lookback=10):
    """
    Ищем FVG который появился ПОСЛЕ манипуляции
    Бычий FVG: свеча[i-1].high < свеча[i+1].low
    Медвежий FVG: свеча[i-1].low > свеча[i+1].high
    """
    recent=df.tail(lookback)
    fvgs=[]

    for i in range(1, len(recent)-1):
        p=recent.iloc[i-1]
        n=recent.iloc[i+1]

        if manip_type=="bullish" and p["high"] < n["low"]:
            fvg_top=n["low"]
            fvg_bot=p["high"]
            fvg_mid=(fvg_top+fvg_bot)/2
            fvgs.append({
                "type": "bullish",
                "top": fvg_top,
                "bot": fvg_bot,
                "mid": fvg_mid
            })

        if manip_type=="bearish" and p["low"] > n["high"]:
            fvg_top=p["low"]
            fvg_bot=n["high"]
            fvg_mid=(fvg_top+fvg_bot)/2
            fvgs.append({
                "type": "bearish",
                "top": fvg_top,
                "bot": fvg_bot,
                "mid": fvg_mid
            })

    return fvgs[-1] if fvgs else None

# ── Фаза 4: Дистрибуция (цель тейк профита) ──
def find_distribution_target(df_htf, manip_type, accum):
    """
    Цель = противоположная сторона зоны аккумуляции или следующий свинг
    """
    sp=swings(df_htf, SWING_LOOKBACK)

    if manip_type=="bullish":
        highs=sp["sh"].dropna()
        if len(highs) >= 2:
            return highs.iloc[-1]  # последний хай = зона дистрибуции
        return accum["range_high"]

    if manip_type=="bearish":
        lows=sp["sl"].dropna()
        if len(lows) >= 2:
            return lows.iloc[-1]
        return accum["range_low"]

    return None

# ── Главная функция сигнала ──
def get_signal2(ex, symbol):
    """
    Полная проверка всех 4 фаз:
    1. Аккумуляция на 4h
    2. Манипуляция на 1h
    3. FVG на 15m
    4. Цена в FVG = вход
    """
    try:
        df_htf=fetch(ex, symbol, HTF, 100)  # 4h
        df_mtf=fetch(ex, symbol, MTF, 100)  # 1h
        df_ltf=fetch(ex, symbol, LTF, 100)  # 15m
        price=df_ltf["close"].iloc[-1]

        # Фаза 1: Аккумуляция
        accum=detect_accumulation(df_htf, lookback=20, threshold=0.04)
        if not accum["is_accumulation"]:
            return None, "Нет аккумуляции"

        # Фаза 2: Манипуляция
        manip=detect_manipulation(df_htf, df_mtf, accum)
        if not manip:
            return None, "Нет манипуляции"

        # Фаза 3: FVG
        fvg=find_fvg_after_manipulation(df_ltf, manip["type"], lookback=15)
        if not fvg:
            return None, "Нет FVG"

        # Фаза 4: Цена в FVG?
        price_in_fvg=(fvg["bot"] <= price <= fvg["top"])
        if not price_in_fvg:
            return None, f"Цена не в FVG ({fvg['bot']:.4f}-{fvg['top']:.4f})"

        # Цель (дистрибуция)
        target=find_distribution_target(df_htf, manip["type"], accum)

        if manip["type"]=="bullish":
            sl=accum["range_low"] * 0.999
            tp1=price+(price-sl)*TP1_RR
            tp2=target if target and target > price else price+(price-sl)*TP2_RR
            return {
                "side": "buy",
                "entry": price,
                "sl": sl,
                "tp1": tp1,
                "tp2": tp2,
                "symbol": symbol,
                "qty_closed": False,
                "strategy": "Accum→Manip→FVG"
            }, "✅ Сигнал!"

        elif manip["type"]=="bearish":
            sl=accum["range_high"] * 1.001
            tp1=price-(sl-price)*TP1_RR
            tp2=target if target and target < price else price-(sl-price)*TP2_RR
            return {
                "side": "sell",
                "entry": price,
                "sl": sl,
                "tp1": tp1,
                "tp2": tp2,
                "symbol": symbol,
                "qty_closed": False,
                "strategy": "Accum→Manip→FVG"
            }, "✅ Сигнал!"

    except Exception as e:
        return None, f"Ошибка: {e}"

    return None, "Нет сигнала"


class Trader2:
    def __init__(self):
        self.bal=START_BALANCE; self.pos=None
        self.wins=0; self.losses=0; self.trades=[]
        self.daily_trades=0; self.daily_loss=0.0
        self.last_day=datetime.now().date()
        self._load()

    def _load(self):
        if os.path.exists(LOG_FILE2):
            with open(LOG_FILE2) as f:
                d=json.load(f)
                self.bal=d.get("balance",START_BALANCE)
                self.wins=d.get("wins",0)
                self.losses=d.get("losses",0)
                self.trades=d.get("trades",[])
                self.daily_trades=d.get("daily_trades",0)
                self.daily_loss=d.get("daily_loss",0.0)
            print(f"[БОТ2] Загружено | Баланс: {self.bal:.2f} USDT")

    def save(self):
        with open(LOG_FILE2,"w") as f:
            json.dump({
                "balance": self.bal,
                "wins": self.wins,
                "losses": self.losses,
                "trades": self.trades[-50:],
                "daily_trades": self.daily_trades,
                "daily_loss": self.daily_loss,
                "position": self.pos
            }, f, indent=2)

    def reset_daily(self):
        today=datetime.now().date()
        if today != self.last_day:
            self.daily_trades=0
            self.daily_loss=0.0
            self.last_day=today

    def can_trade(self):
        self.reset_daily()
        if self.daily_trades >= MAX_DAILY_TRADES:
            return False
        if self.daily_loss >= self.bal*(DAILY_STOP_LOSS/100):
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
        print(f"\n[БОТ2] {'='*40}\n{msg}\n{'='*40}")
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
                msg=f"⚡ ЧАСТИЧНОЕ ЗАКРЫТИЕ 50%\n{symbol} | PnL: +{pnl_half:.2f} USDT\nБаланс: {self.bal:.2f}"
                print(f"\n[БОТ2] {msg}")
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
            self.trades.append({
                "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "symbol": symbol, "side": s,
                "entry": entry, "exit": ep,
                "pnl": round(pnl,2),
                "result": "win" if hit_tp2 else "loss",
                "strategy": "Accum→Manip→FVG"
            })
            msg=(f"{'✅ ТЕЙК ПРОФИТ' if hit_tp2 else '❌ СТОП ЛОСС'}\n"
                 f"{symbol} | Выход: {ep:.4f}\n"
                 f"PnL: {pnl:+.2f} USDT\n"
                 f"Баланс: {self.bal:.2f} USDT")
            print(f"\n[БОТ2] {msg}")
            send_telegram(msg)
            self.pos=None
            self.save()

    def status(self):
        total=self.wins+self.losses
        wr=(self.wins/total*100) if total>0 else 0
        profit=self.bal-START_BALANCE
        print(f"\n[БОТ2] {'─'*40}")
        print(f"[БОТ2] 💰 Баланс: {self.bal:.2f} USDT ({profit:+.2f})")
        print(f"[БОТ2] 📊 Позиция: {'Нет' if not self.pos else self.pos['side'].upper()}")
        print(f"[БОТ2] 🏆 {self.wins}W/{self.losses}L | WR: {wr:.1f}%")
        print(f"[БОТ2] {'─'*40}")


def run_bot2(ex, symbols):
    """Главный цикл бота 2 — запускается в отдельном потоке"""
    trader2=Trader2()
    scan=0
    print("\n[БОТ2] 🚀 Запущен! Стратегия: Аккумуляция→Манипуляция→FVG")

    while True:
        try:
            scan+=1
            for symbol in symbols:
                try:
                    price=get_price(ex, symbol)
                    trader2.check(price, symbol)

                    if trader2.pos and trader2.pos.get("symbol")==symbol:
                        print(f"[БОТ2] ⏳ {symbol}: {trader2.pos['side'].upper()} @ {trader2.pos['entry']:.4f}")
                    else:
                        sig, reason=get_signal2(ex, symbol)
                        print(f"[БОТ2] {symbol}: {reason}")
                        if sig and not trader2.pos:
                            trader2.open(sig)

                except Exception as e:
                    print(f"[БОТ2] Ошибка {symbol}: {e}")
                    continue

            if scan%5==0:
                trader2.status()

            time.sleep(60)

        except Exception as e:
            print(f"[БОТ2] Ошибка цикла: {e}")
            time.sleep(15)
