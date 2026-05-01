import ccxt, pandas as pd, numpy as np, time, requests, json, os
from datetime import datetime

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
        requests.post(url,data={"chat_id":TELEGRAM_CHAT_ID,"text":msg},timeout=5)
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
            volume=t.get("quoteVolume",0) or 0
            if volatility < 1: continue
            pairs.append({
                "symbol": symbol,
                "volatility": volatility,
                "volume": volume,
                "change": t["percentage"]
            })
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
    sfp=sfp_pattern and last["volume"] >= avg_vol*1.5
    if ote or sfp:
        if trend=="bullish":
            sl=l.iloc[-1]*0.999
            tp1=price+(price-sl)*TP1_RR
            tp2=price+(price-sl)*TP2_RR
            return {"side":"buy","entry":price,"sl":sl,"tp1":tp1,"tp2":tp2,"symbol":symbol,"qty_closed":False}
        else:
            sl=h.iloc[-1]*1.001
            tp1=price-(sl-price)*TP1_RR
            tp2=price-(sl-price)*TP2_RR
            return {"side":"sell","entry":price,"sl":sl,"tp1":tp1,"tp2":tp2,"symbol":symbol,"qty_closed":False}
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
            print(f"Загружено | Баланс: {self.bal:.2f} USDT")

    def save(self):
        with open(LOG_FILE,"w") as f:
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
            print("📅 Новый день — сброс дневных лимитов")

    def can_trade(self):
        self.reset_daily()
        if self.daily_trades >= MAX_DAILY_TRADES:
            print(f"⛔ Лимит сделок за день ({MAX_DAILY_TRADES}) достигнут")
            return False
        if self.daily_loss >= self.bal*(DAILY_STOP_LOSS/100):
            print(f"⛔ Дневной стоп-лосс {DAILY_STOP_LOSS}% достигнут")
            return False
        return True

    def open(self,sig):
        if not self.can_trade(): return
        risk=self.bal*(RISK_PERCENT/100)
        sl_d=abs(sig["entry"]-sig["sl"])
        qty=(risk/sl_d)*LEVERAGE if sl_d>0 else 0
        self.pos={**sig,"qty":qty,"qty_closed":False}
        self.daily_trades+=1
        self.save()
        msg=(f"{'📈' if sig['side']=='buy' else '📉'} ПОЗИЦИЯ #{self.daily_trades}: {sig['side'].upper()}\n"
             f"Пара: {sig['symbol']}\n"
             f"Вход: {sig['entry']:.4f}\n"
             f"SL: {sig['sl']:.4f}\n"
             f"TP1: {sig['tp1']:.4f}\n"
             f"TP2: {sig['tp2']:.4f}\n"
             f"Риск: {risk:.2f} USDT")
        print(f"\n{'='*45}\n{msg}\n{'='*45}")
        send_telegram(f"🤖 SMC BOT\n{msg}")

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
                msg=(f"⚡ ЧАСТИЧНОЕ ЗАКРЫТИЕ 50%\n"
                     f"{symbol} | TP1: {tp1:.4f}\n"
                     f"PnL: +{pnl_half:.2f} USDT\n"
                     f"Стоп → безубыток\n"
                     f"Баланс: {self.bal:.2f} USDT")
                print(f"\n{msg}")
                send_telegram(f"🤖 SMC BOT\n{msg}")
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
                "result": "win" if hit_tp2 else "loss"
            })
            msg=(f"{'✅ ТЕЙК ПРОФИТ' if hit_tp2 else '❌ СТОП ЛОСС'}\n"
                 f"{symbol} | Выход: {ep:.4f}\n"
                 f"PnL: {pnl:+.2f} USDT\n"
                 f"Баланс: {self.bal:.2f} USDT")
            print(f"\n{msg}")
            send_telegram(f"🤖 SMC BOT\n{msg}")
            self.pos=None
            self.save()

    def status(self):
        total=self.wins+self.losses
        wr=(self.wins/total*100) if total>0 else 0
        profit=self.bal-START_BALANCE
        print(f"\n{'─'*45}")
        print(f"💰 Баланс:    {self.bal:.2f} USDT ({profit:+.2f})")
        print(f"📊 Позиция:   {'Нет' if not self.pos else self.pos['side'].upper()+' '+self.pos['symbol']}")
        print(f"🏆 Счёт:      {self.wins}W/{self.losses}L | WR: {wr:.1f}%")
        print(f"📈 Сделок:    {total} (сегодня: {self.daily_trades}/{MAX_DAILY_TRADES})")
        print(f"🛡 Дн.потери: {self.daily_loss:.2f} / {self.bal*(DAILY_STOP_LOSS/100):.2f} USDT")
        print(f"{'─'*45}")

ex=connect()
trader=Trader()
scan=0
symbols=[]

msg=f"🤖 SMC BOT запущен!\nБаланс: {START_BALANCE} USDT\nРежим: Paper Trading"
print(f"\n{msg}")
send_telegram(msg)

while True:
    try:
        scan+=1
        if scan==1 or scan%UPDATE_PAIRS_EVERY==0:
            print(f"\n🔄 Обновляю список волатильных пар...")
            symbols=get_top_volatile(ex, TOP_PAIRS)
        for symbol in symbols:
            try:
                price=get_price(ex,symbol)
                print(f"\n[{datetime.now().strftime('%H:%M:%S')}] {symbol}: {price:.4f}")
                trader.check(price,symbol)
                if trader.pos and trader.pos.get("symbol")==symbol:
                    print(f"  ⏳ {trader.pos['side'].upper()} @ {trader.pos['entry']:.4f} | SL:{trader.pos['sl']:.4f} | TP2:{trader.pos['tp2']:.4f}")
                else:
                    df_mtf=fetch(ex,symbol,MTF,100)
                    mtf_trend=bos(df_mtf)
                    df_htf=fetch(ex,symbol,HTF,100)
                    htf_trend=bos(df_htf)
                    print(f"  4H:{mtf_trend or 'нет'} | 1H:{htf_trend or 'нет'}")
                    if htf_trend and not trader.pos:
                        sig=signal(ex,symbol,htf_trend,mtf_trend)
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
