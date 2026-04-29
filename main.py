import ccxt, pandas as pd, numpy as np, time
from datetime import datetime

LTF="15m"; HTF="1h"
LEVERAGE=10; RISK_PERCENT=1.0; TP_RR=2.0
START_BALANCE=1000.0; SWING_LOOKBACK=5
TOP_PAIRS=5  # сколько топ волатильных пар торговать

def connect():
    print("Подключаюсь к OKX...")
    ex=ccxt.okx({"options":{"defaultType":"swap"}})
    ex.load_markets()
    print("Подключён!")
    return ex

def get_top_volatile(ex, top_n=5):
    """Находит топ N самых волатильных пар по объёму и движению за 24ч"""
    try:
        tickers=ex.fetch_tickers()
        pairs=[]
        for symbol, t in tickers.items():
            if not symbol.endswith("/USDT:USDT"): continue
            if not t.get("percentage"): continue
            if not t.get("quoteVolume"): continue
            volatility=abs(t["percentage"])  # % движения за 24ч
            volume=t["quoteVolume"]          # объём в USDT
            if volume < 10_000_000: continue  # минимум $10M объёма
            pairs.append({
                "symbol": symbol,
                "volatility": volatility,
                "volume": volume,
                "change": t["percentage"]
            })
        # Сортируем по волатильности
        pairs.sort(key=lambda x: x["volatility"], reverse=True)
        top=pairs[:top_n]
        print(f"\n📊 Топ {top_n} волатильных пар:")
        for i,p in enumerate(top,1):
            print(f"  {i}. {p['symbol']} | Движение: {p['change']:+.1f}% | Объём: ${p['volume']/1e6:.0f}M")
        return [p["symbol"] for p in top]
    except Exception as e:
        print(f"Ошибка получения пар: {e}")
        return ["BTC/USDT:USDT","ETH/USDT:USDT","SOL/USDT:USDT"]

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

def signal(ex,symbol,trend):
    df=fetch(ex,symbol,LTF,100)
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
            return {"side":"buy","entry":price,"sl":sl,"tp":tp,"symbol":symbol}
        else:
            sl=h.iloc[-1]*1.001; tp=price-(sl-price)*TP_RR
            return {"side":"sell","entry":price,"sl":sl,"tp":tp,"symbol":symbol}
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
        print(f"\n{'='*45}")
        print(f"{'📈' if sig['side']=='buy' else '📉'} ПОЗИЦИЯ: {sig['side'].upper()} | {sig['symbol']}")
        print(f"Вход: {sig['entry']:.4f} | SL: {sig['sl']:.4f} | TP: {sig['tp']:.4f}")
        print(f"Риск: {risk:.2f} USDT")
        print(f"{'='*45}")

    def check(self,price,symbol):
        if not self.pos: return
        if self.pos.get("symbol")!=symbol: return
        s=self.pos["side"]
        hit_tp=(s=="buy" and price>=self.pos["tp"]) or (s=="sell" and price<=self.pos["tp"])
        hit_sl=(s=="buy" and price<=self.pos["sl"]) or (s=="sell" and price>=self.pos["sl"])
        if hit_tp or hit_sl:
            ep=self.pos["tp"] if hit_tp else self.pos["sl"]
            pnl=(ep-self.pos["entry"])*self.pos["qty"] if s=="buy" else (self.pos["entry"]-ep)*self.pos["qty"]
            self.bal+=pnl
            print(f"\n{'✅ ТЕЙК ПРОФИТ' if hit_tp else '❌ СТОП ЛОСС'} | {symbol}")
            print(f"Выход: {ep:.4f} | PnL: {pnl:+.2f} USDT | Баланс: {self.bal:.2f}")
            if hit_tp: self.wins+=1
            else: self.losses+=1
            self.trades.append({"symbol":symbol,"side":s,"entry":self.pos["entry"],
                                 "exit":ep,"pnl":round(pnl,2),
                                 "result":"win" if hit_tp else "loss"})
            self.pos=None

    def status(self):
        total=self.wins+self.losses
        wr=(self.wins/total*100) if total>0 else 0
        print(f"\n{'─'*45}")
        print(f"💰 Баланс:  {self.bal:.2f} USDT")
        if self.pos:
            print(f"📊 Позиция: {self.pos['side'].upper()} {self.pos['symbol']}")
        else:
            print(f"📊 Позиция: Нет")
        print(f"🏆 Счёт:    {self.wins}W/{self.losses}L | WR: {wr:.1f}%")
        print(f"📈 Сделок:  {total}")
        print(f"{'─'*45}")

# ── Запуск ─────────────────────────────────────────
ex=connect()
trader=Trader()
scan=0
symbols=[]
UPDATE_PAIRS_EVERY=30  # обновлять топ пары каждые 30 сканов (30 мин)

print(f"\nSMC BOT запущен! Баланс: {START_BALANCE} USDT")
print(f"Автовыбор топ {TOP_PAIRS} волатильных пар каждые {UPDATE_PAIRS_EVERY} минут\n")

while True:
    try:
        scan+=1

        # Обновляем список волатильных пар каждые N сканов
        if scan==1 or scan%UPDATE_PAIRS_EVERY==0:
            print(f"\n🔄 Обновляю список волатильных пар...")
            symbols=get_top_volatile(ex, TOP_PAIRS)

        # Сканируем каждую пару
        for symbol in symbols:
            try:
                price=get_price(ex,symbol)
                print(f"\n[{datetime.now().strftime('%H:%M:%S')}] {symbol}: {price:.4f}")
                trader.check(price, symbol)
                if trader.pos and trader.pos.get("symbol")==symbol:
                    print(f"  ⏳ {trader.pos['side'].upper()} @ {trader.pos['entry']:.4f} | SL:{trader.pos['sl']:.4f} | TP:{trader.pos['tp']:.4f}")
                else:
                    trend=bos(fetch(ex,symbol,HTF,100))
                    print(f"  HTF: {trend or 'нет тренда'}")
                    if trend and not trader.pos:
                        sig=signal(ex,symbol,trend)
                        if sig: trader.open(sig)
                        else: print(f"  Сигнала нет")
            except Exception as e:
                print(f"  Ошибка {symbol}: {e}")
                continue

        if scan%5==0: trader.status()
        time.sleep(60)

    except KeyboardInterrupt:
        print("\nОстановлен.")
        trader.status()
        break
    except Exception as e:
        print(f"Ошибка: {e}")
        time.sleep(15)
