import ccxt, pandas as pd, numpy as np, time, requests, os
from datetime import datetime
from database import load_state, save_stats, save_trade, try_claim_position, update_position, release_position

LTF="15m"; MTF="1h"; HTF="4h"
LEVERAGE=10; RISK_PERCENT=2.5  # x5 от базового 0.5
START_BALANCE=1000.0; SWING_LOOKBACK=5
TOP_PAIRS=20; UPDATE_PAIRS_EVERY=30  # шире пул пар — аккумуляция редко бывает у топ-5 по волатильности
MAX_DAILY_TRADES=2; DAILY_STOP_LOSS=2.0
MAX_CONCURRENT_POSITIONS=2  # сколько сделок по разным парам можно держать одновременно
TP1_RR=1.5; TP2_RR=3.0
TAKER_FEE=0.0005
FUNDING_RATE_8H=0.0001

TELEGRAM_TOKEN=""
TELEGRAM_CHAT_ID=""

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        url=f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url,data={"chat_id":TELEGRAM_CHAT_ID,"text":f"🟢 БОТ 2\n{msg}"},timeout=5)
    except: pass

def fetch(ex,sym,tf,limit=200):
    raw=ex.fetch_ohlcv(sym,tf,limit=limit)
    df=pd.DataFrame(raw,columns=["ts","open","high","low","close","volume"])
    df["ts"]=pd.to_datetime(df["ts"],unit="ms")
    df.set_index("ts",inplace=True)
    return df

def get_price(ex,sym):
    return ex.fetch_ticker(sym)["last"]

def get_liquid_pairs(ex, top_n=20):
    """Широкий пул пар по объёму торгов, БЕЗ фильтра по волатильности за 24ч.
    Для стратегии аккумуляции это важно: если пара уже сильно двигалась
    последние сутки — она по определению не в тихом боковике, топ-5 самых
    волатильных пар почти никогда не совпадают с аккумуляцией."""
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
        print(f"[БОТ2]   Найдено ликвидных пар: {len(pairs)}, беру топ {top_n}")
        if not top:
            return ["BTC/USDT:USDT","ETH/USDT:USDT","SOL/USDT:USDT","BNB/USDT:USDT","XRP/USDT:USDT"]
        return [p["symbol"] for p in top]
    except Exception as e:
        print(f"[БОТ2] Ошибка получения пар: {e}")
        return ["BTC/USDT:USDT","ETH/USDT:USDT","SOL/USDT:USDT"]

def swings(df,n=5):
    df=df.copy()
    df["sh"]=df["high"].where(df["high"]==df["high"].rolling(n*2+1,center=True).max())
    df["sl"]=df["low"].where(df["low"]==df["low"].rolling(n*2+1,center=True).min())
    return df

def detect_accumulation(df, lookback=20, threshold=0.04):
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

def get_signal2(ex, symbol):
    try:
        df_htf=fetch(ex,symbol,HTF,100)
        df_mtf=fetch(ex,symbol,MTF,100)
        df_ltf=fetch(ex,symbol,LTF,100)
        price=df_ltf["close"].iloc[-1]
        accum=detect_accumulation(df_htf,lookback=20,threshold=0.07)  # было 0.04 — почти никогда не срабатывал
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
                "qty_closed":False,"strategy":"Accum→Manip→FVG"
            }, "✅ Сигнал ЛОНГ!"
        else:
            sl=accum["range_high"]*1.001
            tp1=price-(sl-price)*TP1_RR
            tp2=price-(sl-price)*TP2_RR
            return {
                "side":"sell","entry":price,"sl":sl,
                "tp1":tp1,"tp2":tp2,"symbol":symbol,
                "qty_closed":False,"strategy":"Accum→Manip→FVG"
            }, "✅ Сигнал ШОРТ!"
    except Exception as e:
        return None, f"Ошибка: {e}"

class Trader2:
    def __init__(self):
        state=load_state("bot2", START_BALANCE)
        self.bal=state["balance"]
        self.wins=state["wins"]
        self.losses=state["losses"]
        self.trades=state["trades"]
        self.daily_trades=state["daily_trades"]
        self.daily_loss=state["daily_loss"]
        self.positions=state["positions"]  # symbol -> позиция; несколько параллельных сделок
        self.last_day=datetime.now().date()
        print(f"[БОТ2] Загружено | Баланс: {self.bal:.2f} USDT | Сделок: {len(self.trades)} | Открыто сейчас: {len(self.positions)}")

    def save_stats(self):
        save_stats("bot2", self.bal, self.wins, self.losses, self.daily_trades, self.daily_loss)

    def reset_daily(self):
        today=datetime.now().date()
        if today!=self.last_day:
            self.daily_trades=0; self.daily_loss=0.0
            self.last_day=today
            print("[БОТ2] 📅 Новый день — сброс лимитов")

    def can_trade(self):
        self.reset_daily()
        if self.daily_trades>=MAX_DAILY_TRADES:
            print("[БОТ2] ⛔ Лимит сделок достигнут")
            return False
        if self.daily_loss>=self.bal*(DAILY_STOP_LOSS/100):
            print("[БОТ2] ⛔ Дневной стоп достигнут")
            return False
        if len(self.positions)>=MAX_CONCURRENT_POSITIONS:
            print(f"[БОТ2] ⛔ Лимит одновременных позиций ({MAX_CONCURRENT_POSITIONS})")
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
        if not try_claim_position("bot2", symbol, new_pos):
            print(f"[БОТ2] ⚠️ {symbol}: позиция уже открыта другим инстансом бота — пропускаю дубль")
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
        print(f"\n[БОТ2] {'='*40}\n{msg}\n{'='*40}")
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
                update_position("bot2", symbol, pos)
                self.save_stats()
                msg=f"⚡ 50% закрыто\n{symbol}\nTP1: {tp1:.5f}\nPnL: +{pnl_half:.2f} USDT (комиссия учтена)\nБаланс: {self.bal:.2f}"
                print(f"\n[БОТ2] {msg}")
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
                "strategy": pos.get("strategy","Accum→Manip→FVG"),
                "opened_at": pos.get("opened_at")
            }
            save_trade(trade, "bot2")
            msg=(f"{'✅ ТЕЙК' if hit_tp2 else '❌ СТОП'}\n"
                 f"{symbol} | Выход: {ep:.5f}\n"
                 f"PnL: {pnl:+.2f} USDT\n"
                 f"Баланс: {self.bal:.2f}")
            print(f"\n[БОТ2] {msg}")
            send_telegram(msg)
            del self.positions[symbol]
            release_position("bot2", symbol)
            self.save_stats()

    def status(self):
        total=self.wins+self.losses
        wr=(self.wins/total*100) if total>0 else 0
        profit=self.bal-START_BALANCE
        pos_desc=", ".join(f"{s}:{p['side'].upper()}" for s,p in self.positions.items()) or "Нет"
        print(f"\n[БОТ2] {'─'*40}")
        print(f"[БОТ2] 💰 Баланс: {self.bal:.2f} USDT ({profit:+.2f})")
        print(f"[БОТ2] 📊 Позиции ({len(self.positions)}/{MAX_CONCURRENT_POSITIONS}): {pos_desc}")
        print(f"[БОТ2] 🏆 {self.wins}W/{self.losses}L | WR: {wr:.1f}%")
        print(f"[БОТ2] {'─'*40}")

def run_bot2(ex):
    trader2=Trader2()
    scan=0
    symbols=get_liquid_pairs(ex, TOP_PAIRS)
    print("\n[БОТ2] 🚀 Крипто-бот запущен! Стратегия: Аккумуляция→Манипуляция→FVG")
    print(f"[БОТ2] Пары: {symbols}")

    while True:
        try:
            scan+=1
            if scan==1 or scan%UPDATE_PAIRS_EVERY==0:
                print(f"\n[БОТ2] 🔄 Обновляю список волатильных пар...")
                symbols=get_liquid_pairs(ex, TOP_PAIRS)

            # Если открытая позиция держится по паре, выпавшей из топ-N — всё равно
            # продолжаем её мониторить (SL/TP), иначе она никогда не закроется.
            # Позиций теперь может быть несколько параллельно (разные пары).
            active_symbols = symbols[:]
            for sym in trader2.positions:
                if sym not in active_symbols:
                    active_symbols.append(sym)

            for symbol in active_symbols:
                try:
                    price=get_price(ex,symbol)
                    trader2.check(price,symbol)
                    if symbol in trader2.positions:
                        p=trader2.positions[symbol]
                        print(f"[БОТ2] ⏳ {symbol}: {p['side'].upper()} @ {p['entry']:.5f} | SL:{p['sl']:.5f} | TP2:{p['tp2']:.5f}")
                    else:
                        sig,reason=get_signal2(ex,symbol)
                        print(f"[БОТ2] {symbol}: {reason}")
                        if sig:
                            trader2.open(sig)
                except Exception as e:
                    print(f"[БОТ2] Ошибка {symbol}: {e}")
                    continue

            if scan%5==0: trader2.status()
            time.sleep(60)
        except Exception as e:
            print(f"[БОТ2] Ошибка: {e}")
            time.sleep(15)
