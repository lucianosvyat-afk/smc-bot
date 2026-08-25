import ccxt, pandas as pd, numpy as np, time, requests, os
from datetime import datetime
from database import load_state, save_stats, save_trade, try_claim_position, update_position, release_position

# ============================================================
# БОТ 4 — Mean Reversion + фильтр по старшему тренду
#
# Идея: остальные три бота ловят пробои структуры (BOS) — ставка на
# продолжение движения. У них нет ничего для спокойного/бокового рынка,
# где пробои превращаются в серию ложных стопов. Этот бот делает
# противоположное: покупает откат в восходящем тренде (никогда не против
# тренда — это классическая ошибка mean-reversion стратегий) и продаёт
# отскок в нисходящем, когда RSI на локальном экстремуме и цена коснулась
# полосы Боллинджера. Стоп/тейк считаются от ATR (реальной волатильности
# конкретной пары), а не от фиксированного процента.
# ============================================================

HTF="4h"; LTF="15m"
LEVERAGE=5; RISK_PERCENT=3.75  # x5 от базового 0.75
START_BALANCE=1000.0
TOP_PAIRS=15; UPDATE_PAIRS_EVERY=30
MAX_DAILY_TRADES=4; DAILY_STOP_LOSS=3.0
MAX_CONCURRENT_POSITIONS=4

RSI_PERIOD=14; RSI_OVERSOLD=32; RSI_OVERBOUGHT=68
BB_PERIOD=20; BB_STD=2.0
ATR_PERIOD=14
SL_ATR_MULT=1.5; TP2_ATR_MULT=3.0
TAKER_FEE=0.0005
FUNDING_RATE_8H=0.0001

TELEGRAM_TOKEN=""
TELEGRAM_CHAT_ID=""

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        url=f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url,data={"chat_id":TELEGRAM_CHAT_ID,"text":f"🟣 БОТ 4\n{msg}"},timeout=5)
    except: pass

def fetch(ex,sym,tf,limit=150):
    raw=ex.fetch_ohlcv(sym,tf,limit=limit)
    df=pd.DataFrame(raw,columns=["ts","open","high","low","close","volume"])
    df["ts"]=pd.to_datetime(df["ts"],unit="ms")
    df.set_index("ts",inplace=True)
    return df

def get_price(ex,sym):
    return ex.fetch_ticker(sym)["last"]

def get_liquid_pairs(ex, top_n=15):
    """Ликвидные пары по объёму — mean-reversion нужна статистическая
    надёжность, а не самые дёрганые альткоины."""
    try:
        tickers=ex.fetch_tickers()
        pairs=[]
        for symbol, t in tickers.items():
            if "USDT:USDT" not in symbol: continue
            vol=t.get("quoteVolume") or t.get("baseVolume") or 0
            if not vol: continue
            pairs.append({"symbol":symbol,"volume":vol})
        pairs.sort(key=lambda x: x["volume"], reverse=True)
        top=pairs[:top_n]
        print(f"[БОТ4]   Найдено ликвидных пар: {len(pairs)}, беру топ {top_n}")
        if not top:
            return ["BTC/USDT:USDT","ETH/USDT:USDT","SOL/USDT:USDT","BNB/USDT:USDT","XRP/USDT:USDT"]
        return [p["symbol"] for p in top]
    except Exception as e:
        print(f"[БОТ4] Ошибка получения пар: {e}")
        return ["BTC/USDT:USDT","ETH/USDT:USDT","SOL/USDT:USDT"]

def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def atr(df, period=14):
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def bollinger(series, period=20, n_std=2.0):
    mid = series.rolling(period).mean()
    std = series.rolling(period).std()
    return mid + n_std*std, mid, mid - n_std*std

def htf_trend(df_htf):
    if len(df_htf) < 200: return None
    e50 = ema(df_htf["close"], 50)
    e200 = ema(df_htf["close"], 200)
    if e50.iloc[-1] > e200.iloc[-1]: return "bullish"
    if e50.iloc[-1] < e200.iloc[-1]: return "bearish"
    return None

def get_signal4(ex, symbol):
    try:
        df_htf=fetch(ex,symbol,HTF,250)
        trend=htf_trend(df_htf)
        if not trend: return None, "Нет чёткого тренда 4H"

        df_ltf=fetch(ex,symbol,LTF,150)
        if len(df_ltf) < BB_PERIOD+5: return None, "Мало данных"
        price=df_ltf["close"].iloc[-1]
        r=rsi(df_ltf["close"], RSI_PERIOD).iloc[-1]
        upper, mid, lower = bollinger(df_ltf["close"], BB_PERIOD, BB_STD)
        a=atr(df_ltf, ATR_PERIOD).iloc[-1]
        if pd.isna(r) or pd.isna(a) or a==0: return None, "Индикаторы не готовы"

        if trend=="bullish":
            oversold = r <= RSI_OVERSOLD
            touched_lower = price <= lower.iloc[-1]*1.002
            if not (oversold and touched_lower):
                return None, f"Ждём откат (тренд↑, RSI {r:.0f})"
            sl = price - a*SL_ATR_MULT
            tp1 = mid.iloc[-1]
            tp2 = price + a*TP2_ATR_MULT
            if tp1 <= price: tp1 = price + a*0.5
            return {
                "side":"buy","entry":price,"sl":sl,"tp1":tp1,"tp2":tp2,
                "symbol":symbol,"qty_closed":False,"strategy":"MeanRev+TrendFilter"
            }, f"✅ ЛОНГ: откат в аптренде, RSI {r:.0f}"
        else:
            overbought = r >= RSI_OVERBOUGHT
            touched_upper = price >= upper.iloc[-1]*0.998
            if not (overbought and touched_upper):
                return None, f"Ждём отскок (тренд↓, RSI {r:.0f})"
            sl = price + a*SL_ATR_MULT
            tp1 = mid.iloc[-1]
            tp2 = price - a*TP2_ATR_MULT
            if tp1 >= price: tp1 = price - a*0.5
            return {
                "side":"sell","entry":price,"sl":sl,"tp1":tp1,"tp2":tp2,
                "symbol":symbol,"qty_closed":False,"strategy":"MeanRev+TrendFilter"
            }, f"✅ ШОРТ: отскок в даунтренде, RSI {r:.0f}"
    except Exception as e:
        return None, f"Ошибка: {e}"

class Trader4:
    def __init__(self):
        state=load_state("bot4", START_BALANCE)
        self.bal=state["balance"]
        self.wins=state["wins"]
        self.losses=state["losses"]
        self.trades=state["trades"]
        self.daily_trades=state["daily_trades"]
        self.daily_loss=state["daily_loss"]
        self.positions=state["positions"]
        self.last_day=datetime.now().date()
        print(f"[БОТ4] Загружено | Баланс: {self.bal:.2f} USDT | Сделок: {len(self.trades)} | Открыто сейчас: {len(self.positions)}")

    def save_stats(self):
        save_stats("bot4", self.bal, self.wins, self.losses, self.daily_trades, self.daily_loss)

    def reset_daily(self):
        today=datetime.now().date()
        if today!=self.last_day:
            self.daily_trades=0; self.daily_loss=0.0
            self.last_day=today
            print("[БОТ4] 📅 Новый день — сброс лимитов")

    def can_trade(self):
        self.reset_daily()
        if self.daily_trades>=MAX_DAILY_TRADES:
            print("[БОТ4] ⛔ Лимит сделок достигнут")
            return False
        if self.daily_loss>=self.bal*(DAILY_STOP_LOSS/100):
            print("[БОТ4] ⛔ Дневной стоп достигнут")
            return False
        if len(self.positions)>=MAX_CONCURRENT_POSITIONS:
            print(f"[БОТ4] ⛔ Лимит одновременных позиций ({MAX_CONCURRENT_POSITIONS})")
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
        if not try_claim_position("bot4", symbol, new_pos):
            print(f"[БОТ4] ⚠️ {symbol}: позиция уже открыта другим инстансом бота — пропускаю дубль")
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
        print(f"\n[БОТ4] {'='*40}\n{msg}\n{'='*40}")
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
                update_position("bot4", symbol, pos)
                self.save_stats()
                msg=f"⚡ 50% закрыто\n{symbol}\nTP1: {tp1:.5f}\nPnL: +{pnl_half:.2f} USDT (комиссия учтена)\nБаланс: {self.bal:.2f}"
                print(f"\n[БОТ4] {msg}")
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
                "strategy": pos.get("strategy","MeanRev+TrendFilter"),
                "opened_at": pos.get("opened_at")
            }
            save_trade(trade, "bot4")
            msg=(f"{'✅ ТЕЙК' if hit_tp2 else '❌ СТОП'}\n"
                 f"{symbol} | Выход: {ep:.5f}\n"
                 f"PnL: {pnl:+.2f} USDT\n"
                 f"Баланс: {self.bal:.2f}")
            print(f"\n[БОТ4] {msg}")
            send_telegram(msg)
            del self.positions[symbol]
            release_position("bot4", symbol)
            self.save_stats()

    def status(self):
        total=self.wins+self.losses
        wr=(self.wins/total*100) if total>0 else 0
        profit=self.bal-START_BALANCE
        pos_desc=", ".join(f"{s}:{p['side'].upper()}" for s,p in self.positions.items()) or "Нет"
        print(f"\n[БОТ4] {'─'*40}")
        print(f"[БОТ4] 💰 Баланс: {self.bal:.2f} USDT ({profit:+.2f})")
        print(f"[БОТ4] 📊 Позиции ({len(self.positions)}/{MAX_CONCURRENT_POSITIONS}): {pos_desc}")
        print(f"[БОТ4] 🏆 {self.wins}W/{self.losses}L | WR: {wr:.1f}%")
        print(f"[БОТ4] {'─'*40}")

def run_bot4(ex):
    trader4=Trader4()
    scan=0
    symbols=get_liquid_pairs(ex, TOP_PAIRS)
    print("\n[БОТ4] 🚀 Бот запущен! Стратегия: Mean Reversion + фильтр тренда")
    print(f"[БОТ4] Пары: {symbols}")

    while True:
        try:
            scan+=1
            if scan==1 or scan%UPDATE_PAIRS_EVERY==0:
                print(f"\n[БОТ4] 🔄 Обновляю список ликвидных пар...")
                symbols=get_liquid_pairs(ex, TOP_PAIRS)

            active_symbols = symbols[:]
            for sym in trader4.positions:
                if sym not in active_symbols:
                    active_symbols.append(sym)

            for symbol in active_symbols:
                try:
                    price=get_price(ex,symbol)
                    trader4.check(price,symbol)
                    if symbol in trader4.positions:
                        p=trader4.positions[symbol]
                        print(f"[БОТ4] ⏳ {symbol}: {p['side'].upper()} @ {p['entry']:.5f} | SL:{p['sl']:.5f} | TP2:{p['tp2']:.5f}")
                    else:
                        sig,reason=get_signal4(ex,symbol)
                        print(f"[БОТ4] {symbol}: {reason}")
                        if sig:
                            trader4.open(sig)
                except Exception as e:
                    print(f"[БОТ4] Ошибка {symbol}: {e}")
                    continue

            if scan%5==0: trader4.status()
            time.sleep(60)
        except Exception as e:
            print(f"[БОТ4] Ошибка: {e}")
            time.sleep(15)
