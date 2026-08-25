import ccxt, pandas as pd, numpy as np, time, requests, os
from datetime import datetime
from database import load_state, save_stats, save_trade, try_claim_position, update_position, release_position

# ВАЖНО: у OKX физически нет валютных своп-пар (EUR/GBP/AUD/JPY/CAD/CHF/NZD) —
# только крипто и XAU/XAG (золото/серебро). Поэтому для реальных валют бот
# подключается ко второй бирже — BitMEX, где с апреля 2026 есть настоящие
# FX-перпетуалы. Точные названия символов в ccxt узнаём динамически при
# старте (resolve_fx_symbol), т.к. это новый продукт биржи и формат мог не
# совпасть с ожиданиями — детали смотри в логах "[БОТ3] Форекс-пара...".
FX_MAJOR_PAIRS = [
    ("EUR", "USD"),   # Евро
    ("GBP", "USD"),   # Фунт
    ("AUD", "USD"),   # Австралийский доллар
    ("USD", "JPY"),   # Японская йена
    ("USD", "CHF"),   # Швейцарский франк
    ("USD", "CAD"),   # Канадский доллар
    # NZD/USD на BitMEX и OKX не найден — новозеландский доллар недоступен
]

# Золото/серебро остаются на OKX — там они реально существуют и уже работали
METAL_PAIRS = [
    "XAU/USDT:USDT",   # Золото
    "XAG/USDT:USDT",   # Серебро
]

def resolve_fx_symbol(ex, cur_a, cur_b):
    """Ищем реальный unified-символ валютной пары в маркетах биржи (ccxt может
    называть его в любом порядке/формате — проверяем несколько вариантов, а
    затем ищем по вхождению обеих валют среди своп-рынков."""
    candidates = [
        f"{cur_a}/{cur_b}:{cur_b}", f"{cur_a}/{cur_b}:{cur_a}", f"{cur_a}/{cur_b}",
        f"{cur_b}/{cur_a}:{cur_a}", f"{cur_b}/{cur_a}:{cur_b}", f"{cur_b}/{cur_a}",
    ]
    for c in candidates:
        if c in ex.markets:
            return c
    for sym, m in ex.markets.items():
        if cur_a in sym and cur_b in sym:
            return sym
    return None

LTF="5m"; MTF="15m"; HTF="1h"
LEVERAGE=10; RISK_PERCENT=2.5  # x5 от базового 0.5
START_BALANCE=1000.0; SWING_LOOKBACK=5
MAX_DAILY_TRADES=3; DAILY_STOP_LOSS=2.0
MAX_CONCURRENT_POSITIONS=3  # сколько сделок по разным парам можно держать одновременно
TP1_RR=1.5; TP2_RR=3.0
TAKER_FEE=0.00075  # BitMEX/форекс-своп обычно чуть дороже крипто-своп OKX (~0.075% за сторону)
FUNDING_RATE_8H=0.0001

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
        self.positions=state["positions"]  # symbol -> позиция; несколько параллельных сделок
        self.last_day=datetime.now().date()
        print(f"[БОТ3] Загружено | Баланс: {self.bal:.2f} USDT | Сделок: {len(self.trades)} | Открыто сейчас: {len(self.positions)}")

    def save_stats(self):
        save_stats("bot3", self.bal, self.wins, self.losses, self.daily_trades, self.daily_loss)

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
        if len(self.positions)>=MAX_CONCURRENT_POSITIONS:
            print(f"[БОТ3] ⛔ Лимит одновременных позиций ({MAX_CONCURRENT_POSITIONS})")
            return False
        return True

    def open(self,sig):
        symbol=sig["symbol"]
        if symbol in self.positions: return
        if not self.can_trade(): return
        risk=self.bal*(RISK_PERCENT/100)
        sl_d=abs(sig["entry"]-sig["sl"])
        qty=(risk/sl_d) if sl_d>0 else 0  # без *LEVERAGE — иначе риск на сделку в LEVERAGE раз больше заявленного
        new_pos={**sig,"qty":qty,"qty_full":qty,"opened_at":datetime.now().strftime("%Y-%m-%d %H:%M")}
        if not try_claim_position("bot3", symbol, new_pos):
            print(f"[БОТ3] ⚠️ {symbol}: позиция уже открыта другим инстансом бота — пропускаю дубль")
            return
        self.positions[symbol]=new_pos
        self.daily_trades+=1
        self.save_stats()
        msg=(f"{'📈' if sig['side']=='buy' else '📉'} {sig['side'].upper()}\n"
             f"Пара: {sig['symbol']}\n"
             f"Стратегия: {sig.get('strategy','')}\n"
             f"Вход: {sig['entry']:.5f}\n"
             f"SL: {sig['sl']:.5f}\n"
             f"TP1: {sig['tp1']:.5f}\n"
             f"TP2: {sig['tp2']:.5f}\n"
             f"Риск: {risk:.2f} USDT\n"
             f"Открыто сейчас: {len(self.positions)}/{MAX_CONCURRENT_POSITIONS}")
        print(f"\n[БОТ3] {'='*40}\n{msg}\n{'='*40}")
        send_telegram(msg)

    def check(self,price,symbol):
        pos=self.positions.get(symbol)
        if not pos: return
        s=pos["side"]
        entry=pos["entry"]
        sl=pos["sl"]
        tp1=pos["tp1"]
        tp2=pos["tp2"]
        qty=pos["qty"]
        qty_full=pos.get("qty_full", qty)
        entry_fee_total=qty_full*entry*TAKER_FEE
        if not pos["qty_closed"]:
            hit_tp1=(s=="buy" and price>=tp1) or (s=="sell" and price<=tp1)
            if hit_tp1:
                pnl_half=(tp1-entry)*(qty/2) if s=="buy" else (entry-tp1)*(qty/2)
                fee_half=(entry_fee_total/2)+((qty/2)*tp1*TAKER_FEE)
                pnl_half-=fee_half
                self.bal+=pnl_half
                pos["qty_closed"]=True
                pos["sl"]=entry
                pos["qty"]=qty/2
                update_position("bot3", symbol, pos)
                self.save_stats()
                msg=f"⚡ 50% закрыто\n{symbol}\nTP1: {tp1:.5f}\nPnL: +{pnl_half:.2f} USDT (комиссия учтена)\nБаланс: {self.bal:.2f}"
                print(f"\n[БОТ3] {msg}")
                send_telegram(msg)
                return
        hit_tp2=(s=="buy" and price>=tp2) or (s=="sell" and price<=tp2)
        hit_sl=(s=="buy" and price<=sl) or (s=="sell" and price>=sl)
        if hit_tp2 or hit_sl:
            ep=tp2 if hit_tp2 else sl
            pnl=(ep-entry)*pos["qty"] if s=="buy" else (entry-ep)*pos["qty"]
            entry_fee_remaining=entry_fee_total/2 if pos["qty_closed"] else entry_fee_total
            exit_fee=pos["qty"]*ep*TAKER_FEE
            try:
                opened_dt=datetime.strptime(pos.get("opened_at",""), "%Y-%m-%d %H:%M")
                hours_held=max((datetime.now()-opened_dt).total_seconds()/3600, 0)
            except Exception:
                hours_held=0
            funding_cost=qty_full*entry*FUNDING_RATE_8H*(hours_held/8)
            pnl-=(entry_fee_remaining+exit_fee+funding_cost)
            self.bal+=pnl
            if pnl<0: self.daily_loss+=abs(pnl)
            if hit_tp2: self.wins+=1
            else: self.losses+=1
            trade={
                "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "symbol": symbol, "side": s,
                "entry": entry, "exit": ep,
                "sl": pos.get("sl",0),
                "tp1": tp1, "tp2": tp2,
                "pnl": round(pnl,2),
                "result": "win" if hit_tp2 else "loss",
                "strategy": pos.get("strategy","Forex:BOS+FVG"),
                "opened_at": pos.get("opened_at")
            }
            save_trade(trade, "bot3")
            msg=(f"{'✅ ТЕЙК' if hit_tp2 else '❌ СТОП'}\n"
                 f"{symbol} | Выход: {ep:.5f}\n"
                 f"PnL: {pnl:+.2f} USDT\n"
                 f"Баланс: {self.bal:.2f}")
            print(f"\n[БОТ3] {msg}")
            send_telegram(msg)
            del self.positions[symbol]
            release_position("bot3", symbol)
            self.save_stats()

    def status(self):
        total=self.wins+self.losses
        wr=(self.wins/total*100) if total>0 else 0
        profit=self.bal-START_BALANCE
        session="🟢 Активная" if is_forex_session() else "🔴 Закрыта"
        pos_desc=", ".join(f"{s}:{p['side'].upper()}" for s,p in self.positions.items()) or "Нет"
        print(f"\n[БОТ3] {'─'*40}")
        print(f"[БОТ3] 💱 ФОРЕКС BOS+FVG | Сессия: {session}")
        print(f"[БОТ3] 💰 Баланс: {self.bal:.2f} USDT ({profit:+.2f})")
        print(f"[БОТ3] 📊 Позиции ({len(self.positions)}/{MAX_CONCURRENT_POSITIONS}): {pos_desc}")
        print(f"[БОТ3] 🏆 {self.wins}W/{self.losses}L | WR: {wr:.1f}%")
        print(f"[БОТ3] {'─'*40}")

def build_pairs(ex_crypto):
    """Собираем итоговый список торгуемых инструментов: реальный форекс на
    BitMEX + золото/серебро на OKX. Возвращает список (exchange, symbol, имя)."""
    pairs = []

    ex_fx = None
    try:
        ex_fx = ccxt.bitmex()
        ex_fx.load_markets()
        print("[БОТ3] ✅ Подключено к BitMEX (для реального форекса)")
    except Exception as e:
        print(f"[БОТ3] ⚠️ Не удалось подключиться к BitMEX: {e} — форекс-пары пропущены, торгуем только золото/серебро")

    if ex_fx:
        for a, b in FX_MAJOR_PAIRS:
            sym = resolve_fx_symbol(ex_fx, a, b)
            if sym:
                pairs.append((ex_fx, sym, f"{a}/{b}"))
                print(f"[БОТ3] Форекс-пара найдена: {a}/{b} → {sym}")
            else:
                print(f"[БОТ3] ⚠️ Пара {a}/{b} не найдена на BitMEX, пропускаю")

    for m in METAL_PAIRS:
        pairs.append((ex_crypto, m, m.replace("/USDT:USDT","")))

    return pairs

def run_bot3(ex):
    trader3=Trader3()
    scan=0
    pairs=build_pairs(ex)
    print("\n[БОТ3] 🚀 Форекс бот запущен! Стратегия: BOS+FVG")
    print(f"[БОТ3] Итоговые пары: {[name for _,_,name in pairs]}")
    print(f"[БОТ3] HTF:{HTF} MTF:{MTF} LTF:{LTF}")

    if not pairs:
        print("[БОТ3] ⛔ Нет ни одной доступной пары — бот остановлен")
        return

    while True:
        try:
            scan+=1
            if not is_forex_session():
                if scan%5==0:
                    print(f"[БОТ3] 🔴 Форекс закрыт — жду сессии...")
                time.sleep(60)
                continue

            for pair_ex, symbol, name in pairs:
                try:
                    price=get_price(pair_ex,symbol)
                    trader3.check(price,symbol)
                    if symbol in trader3.positions:
                        p=trader3.positions[symbol]
                        print(f"[БОТ3] ⏳ {name}: {p['side'].upper()} @ {p['entry']:.5f}")
                    else:
                        sig,reason=get_signal3(pair_ex,symbol)
                        print(f"[БОТ3] {name}: {reason}")
                        if sig:
                            trader3.open(sig)
                except Exception as e:
                    print(f"[БОТ3] Ошибка {name}: {e}")
                    continue

            if scan%5==0: trader3.status()
            time.sleep(60)

        except Exception as e:
            print(f"[БОТ3] Ошибка: {e}")
            time.sleep(15)
