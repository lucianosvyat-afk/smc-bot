import os
from pymongo import MongoClient
from datetime import datetime

MONGODB_URL = os.environ.get("MONGODB_URL", "")

client = None
db = None

def connect_db():
    global client, db
    try:
        if not MONGODB_URL:
            print("⚠️ MONGODB_URL не найден — используем локальные файлы")
            return False
        client = MongoClient(MONGODB_URL)
        db = client["smc_bot"]
        print("✅ MongoDB подключена!")
        return True
    except Exception as e:
        print(f"❌ Ошибка MongoDB: {e}")
        return False

def save_trade(trade, bot_name="bot1"):
    if db is None: return
    try:
        trade["bot"] = bot_name
        trade["created_at"] = datetime.now()
        db["trades"].insert_one(trade)
    except Exception as e:
        print(f"Ошибка сохранения сделки: {e}")

def save_state(bot_name, balance, wins, losses, daily_trades, daily_loss, position=None):
    if db is None: return
    try:
        db["states"].update_one(
            {"bot": bot_name},
            {"$set": {
                "bot": bot_name,
                "balance": balance,
                "wins": wins,
                "losses": losses,
                "daily_trades": daily_trades,
                "daily_loss": daily_loss,
                "position": position,
                "updated_at": datetime.now()
            }},
            upsert=True
        )
    except Exception as e:
        print(f"Ошибка сохранения состояния: {e}")

def load_state(bot_name, start_balance=1000.0):
    if db is None:
        return {
            "balance": start_balance,
            "wins": 0, "losses": 0,
            "daily_trades": 0, "daily_loss": 0.0,
            "position": None, "trades": []
        }
    try:
        state = db["states"].find_one({"bot": bot_name})
        trades = list(db["trades"].find(
            {"bot": bot_name},
            {"_id": 0}
        ).sort("created_at", -1).limit(50))
        trades.reverse()

        if state:
            return {
                "balance": state.get("balance", start_balance),
                "wins": state.get("wins", 0),
                "losses": state.get("losses", 0),
                "daily_trades": state.get("daily_trades", 0),
                "daily_loss": state.get("daily_loss", 0.0),
                "position": state.get("position", None),
                "trades": trades
            }
    except Exception as e:
        print(f"Ошибка загрузки состояния: {e}")

    return {
        "balance": start_balance,
        "wins": 0, "losses": 0,
        "daily_trades": 0, "daily_loss": 0.0,
        "position": None, "trades": []
    }

def get_all_states():
    if db is None:
        default = {"balance":1000,"wins":0,"losses":0,"trades":[],"daily_trades":0,"daily_loss":0,"position":None}
        return {"bot1":default.copy(),"bot2":default.copy(),"bot3":default.copy()}

    result = {}
    for bot_name in ["bot1","bot2","bot3"]:
        try:
            state = db["states"].find_one({"bot": bot_name})
            trades = list(db["trades"].find(
                {"bot": bot_name},
                {"_id": 0}
            ).sort("created_at", -1).limit(50))
            trades.reverse()

            if state:
                result[bot_name] = {
                    "balance": state.get("balance", 1000),
                    "wins": state.get("wins", 0),
                    "losses": state.get("losses", 0),
                    "daily_trades": state.get("daily_trades", 0),
                    "daily_loss": state.get("daily_loss", 0.0),
                    "position": state.get("position", None),
                    "trades": trades
                }
            else:
                result[bot_name] = {
                    "balance": 1000, "wins": 0, "losses": 0,
                    "daily_trades": 0, "daily_loss": 0.0,
                    "position": None, "trades": []
                }
        except Exception as e:
            print(f"Ошибка получения {bot_name}: {e}")
            result[bot_name] = {
                "balance": 1000, "wins": 0, "losses": 0,
                "daily_trades": 0, "daily_loss": 0.0,
                "position": None, "trades": []
            }

    return result

connect_db()
