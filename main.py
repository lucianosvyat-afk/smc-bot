import ccxt, pandas as pd, numpy as np, time, requests, json, os, threading
from datetime import datetime
from bot2 import run_bot2
from bot3_forex import run_bot3
from bot4 import run_bot4
from database import load_state, save_stats, save_trade, connect_db, try_claim_position, update_position, release_position

LTF="15m"; HTF="1h"; MTF="4h"
LEVERAGE=10; RISK_PERCENT=1.0
START_BALANCE=1000.0; SWING_LOOKBACK=5
TOP_PAIRS=5; UPDATE_PAIRS_EVERY=30
MAX_DAILY_TRADES=3; DAILY_STOP_LOSS=3.0
MAX_CONCURRENT_POSITIONS=3  # сколько сделок по разным парам можно держать одновременно
TP1_RR=1.0; TP2_RR=2.0

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
        state=load_state("bot1", START_BALANCE)
        self.bal=state["balance"]
        self.wins=state["wins"]
        self.losses=state["losses"]
        self.trades=state["trades"]
        self.daily_trades=state["daily_trades"]
        self.daily_loss=state["daily_loss"]
        self.positions=state["positions"]  # symbol -> позиция; несколько параллельных сделок
        self.last_day=datetime.now().date()
        print(f"[БОТ1] Загружено | Баланс: {self.bal:.2f} USDT | Сделок: {len(self.trades)} | Открыто сейчас: {len(self.positions)}")

    def save_stats(self):
        save_stats("bot1", self.bal, self.wins, self.losses, self.daily_trades, self.daily_loss)

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
        if len(self.positions)>=MAX_CONCURRENT_POSITIONS:
            print(f"[БОТ1] ⛔ Лимит одновременных позиций ({MAX_CONCURRENT_POSITIONS})")
            return False
        return True

    def open(self,sig):
        symbol=sig["symbol"]
        if symbol in self.positions: return  # уже держим сделку по этой паре
        if not self.can_trade(): return
        risk=self.bal*(RISK_PERCENT/100)
        sl_d=abs(sig["entry"]-sig["sl"])
        qty=(risk/sl_d)*LEVERAGE if sl_d>0 else 0
        new_pos={**sig,"qty":qty,"opened_at":datetime.now().strftime("%Y-%m-%d %H:%M")}
        if not try_claim_position("bot1", symbol, new_pos):
            print(f"[БОТ1] ⚠️ {symbol}: позиция уже открыта другим инстансом бота — пропускаю дубль")
            return
        self.positions[symbol]=new_pos
        self.daily_trades+=1
        self.save_stats()
        msg=(f"{'📈' if sig['side']=='buy' else '📉'} ПОЗИЦИЯ: {sig['side'].upper()}\n"
             f"Пара: {sig['symbol']}\n"
             f"Стратегия: {sig.get('strategy','')}\n"
             f"Вход: {sig['entry']:.4f}\n"
             f"SL: {sig['sl']:.4f}\n"
             f"TP1: {sig['tp1']:.4f}\n"
             f"TP2: {sig['tp2']:.4f}\n"
             f"Риск: {risk:.2f} USDT\n"
             f"Открыто сейчас: {len(self.positions)}/{MAX_CONCURRENT_POSITIONS}")
        print(f"\n[БОТ1] {'='*40}\n{msg}\n{'='*40}")
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
        if not pos["qty_closed"]:
            hit_tp1=(s=="buy" and price>=tp1) or (s=="sell" and price<=tp1)
            if hit_tp1:
                pnl_half=(tp1-entry)*(qty/2) if s=="buy" else (entry-tp1)*(qty/2)
                self.bal+=pnl_half
                pos["qty_closed"]=True
                pos["sl"]=entry
                pos["qty"]=qty/2
                update_position("bot1", symbol, pos)
                self.save_stats()
                msg=f"⚡ ЧАСТИЧНОЕ ЗАКРЫТИЕ 50%\n{symbol} | PnL: +{pnl_half:.2f} USDT\nБаланс: {self.bal:.2f}"
                print(f"\n[БОТ1] {msg}")
                send_telegram(msg)
                return
        hit_tp2=(s=="buy" and price>=tp2) or (s=="sell" and price<=tp2)
        hit_sl=(s=="buy" and price<=sl) or (s=="sell" and price>=sl)
        if hit_tp2 or hit_sl:
            ep=tp2 if hit_tp2 else sl
            pnl=(ep-entry)*pos["qty"] if s=="buy" else (entry-ep)*pos["qty"]
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
                "strategy": pos.get("strategy","BOS+OTE+SFP"),
                "opened_at": pos.get("opened_at")
            }
            save_trade(trade, "bot1")
            msg=(f"{'✅ ТЕЙК ПРОФИТ' if hit_tp2 else '❌ СТОП ЛОСС'}\n"
                 f"{symbol} | Выход: {ep:.4f}\n"
                 f"PnL: {pnl:+.2f} USDT\n"
                 f"Баланс: {self.bal:.2f} USDT")
            print(f"\n[БОТ1] {msg}")
            send_telegram(msg)
            del self.positions[symbol]
            release_position("bot1", symbol)
            self.save_stats()

    def status(self):
        total=self.wins+self.losses
        wr=(self.wins/total*100) if total>0 else 0
        profit=self.bal-START_BALANCE
        pos_desc=", ".join(f"{s}:{p['side'].upper()}" for s,p in self.positions.items()) or "Нет"
        print(f"\n[БОТ1] {'─'*40}")
        print(f"[БОТ1] 💰 Баланс: {self.bal:.2f} USDT ({profit:+.2f})")
        print(f"[БОТ1] 📊 Позиции ({len(self.positions)}/{MAX_CONCURRENT_POSITIONS}): {pos_desc}")
        print(f"[БОТ1] 🏆 {self.wins}W/{self.losses}L | WR: {wr:.1f}%")
        print(f"[БОТ1] {'─'*40}")

def run_dashboard():
    try:
        from dashboard import app
        port=int(os.environ.get('PORT', 8080))
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    except Exception as e:
        print(f"Ошибка дашборда: {e}")

ex=connect()
symbols=get_top_volatile(ex, TOP_PAIRS)

threading.Thread(target=run_dashboard, daemon=True).start()
print("✅ Дашборд запущен!")

threading.Thread(target=run_bot2, args=(ex,), daemon=True).start()
print("✅ Бот 2 запущен!")

threading.Thread(target=run_bot3, args=(ex,), daemon=True).start()
print("✅ Бот 3 запущен!")

threading.Thread(target=run_bot4, args=(ex,), daemon=True).start()
print("✅ Бот 4 запущен!")

trader=Trader()
scan=0

print(f"\n✅ Бот 1 запущен! Все 3 бота работают!\n")
send_telegram(f"🚀 Все 4 бота запущены!\nБот1: BOS+OTE+SFP\nБот2: Аккум→FVG\nБот3: Форекс\nБот4: Mean Reversion\nБаланс: {START_BALANCE} USDT")

while True:
    try:
        scan+=1
        if scan==1 or scan%UPDATE_PAIRS_EVERY==0:
            print(f"\n🔄 Обновляю список волатильных пар...")
            symbols=get_top_volatile(ex, TOP_PAIRS)

        # ВАЖНО: если открытая позиция держится по паре, которая выпала из топ-5
        # волатильных, её всё равно нужно продолжать мониторить (SL/TP), иначе
        # эта позиция никогда не закроется. Теперь позиций может быть несколько
        # параллельно (по разным парам) — добавляем в мониторинг все их символы.
        active_symbols = symbols[:]
        for sym in trader.positions:
            if sym not in active_symbols:
                active_symbols.append(sym)

        for symbol in active_symbols:
            try:
                price=get_price(ex,symbol)
                print(f"\n[БОТ1] [{datetime.now().strftime('%H:%M:%S')}] {symbol}: {price:.4f}")
                trader.check(price,symbol)
                if symbol in trader.positions:
                    p=trader.positions[symbol]
                    print(f"[БОТ1] ⏳ {p['side'].upper()} @ {p['entry']:.4f} | SL:{p['sl']:.4f} | TP2:{p['tp2']:.4f}")
                else:
                    df_mtf=fetch(ex,symbol,MTF,100)
                    mtf_trend=bos(df_mtf)
                    df_htf=fetch(ex,symbol,HTF,100)
                    htf_trend=bos(df_htf)
                    print(f"[БОТ1] 4H:{mtf_trend or 'нет'} | 1H:{htf_trend or 'нет'}")
                    if htf_trend:
                        sig=signal(ex,symbol,htf_trend,mtf_trend)
                        if sig: trader.open(sig)
                        else: print(f"[БОТ1] Сигнала нет")
            except Exception as e:
                print(f"[БОТ1] Ошибка {symbol}: {e}")
                continue
        if scan%5==0: trader.status()
        time.sleep(60)
    except KeyboardInterrupt:
        print("\nОстановлен.")
        trader.status()
        break
    except Exception as e:
        print(f"[БОТ1] Ошибка: {e}")
        time.sleep(15)
