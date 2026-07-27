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

def _safe_key(symbol):
    """Ключ пары как ключ вложенного поля MongoDB — символы вроде BTC/USDT:USDT
    точки не содержат, но на всякий случай подстраховываемся (точка в Mongo
    означает вложенность)."""
    return symbol.replace(".", "__")

def try_claim_position(bot_name, symbol, position):
    """Атомарно 'застолбить' открытие сделки ИМЕННО по этой паре (бот теперь
    может держать несколько параллельных позиций по разным парам). Сохраняем
    позицию только если по этому символу сейчас ещё нет открытой. Если два
    процесса бота вдруг работают параллельно (например, Railway на секунду
    держит старый и новый деплой одновременно) и оба одновременно решат
    открыть сделку по одному и тому же сигналу на одной паре — эта проверка
    гарантирует, что реально откроется только одна, а не две (что удваивает
    риск и ломает расчёт risk/reward). Возвращает True, если именно этот
    вызов победил. Без подключения к БД (локальный режим) ничего не блокируем."""
    if db is None: return True
    key = _safe_key(symbol)
    try:
        result = db["states"].update_one(
            {"bot": bot_name, f"positions.{key}": {"$exists": False}},
            {
                "$set": {f"positions.{key}": position, "updated_at": datetime.now()},
                "$setOnInsert": {"bot": bot_name},
            },
            upsert=True,
        )
        return (result.modified_count > 0) or (result.upserted_id is not None)
    except Exception as e:
        print(f"Ошибка атомарного захвата позиции: {e}")
        return True

def update_position(bot_name, symbol, position):
    """Обновить уже открытую позицию (например частичное закрытие 50% на TP1:
    меняются qty/sl/qty_closed). В отличие от try_claim_position — без
    проверки на 'ещё нет', просто $set по конкретному ключу. Не трогает
    остальные позиции бота."""
    if db is None: return
    key = _safe_key(symbol)
    try:
        db["states"].update_one(
            {"bot": bot_name},
            {"$set": {f"positions.{key}": position, "updated_at": datetime.now()}},
        )
    except Exception as e:
        print(f"Ошибка обновления позиции: {e}")

def release_position(bot_name, symbol):
    """Убрать позицию из базы при закрытии сделки (SL/TP2)."""
    if db is None: return
    key = _safe_key(symbol)
    try:
        db["states"].update_one(
            {"bot": bot_name},
            {"$unset": {f"positions.{key}": ""}, "$set": {"updated_at": datetime.now()}},
        )
    except Exception as e:
        print(f"Ошибка снятия позиции: {e}")

def _normalize_positions(state):
    """Миграция со старого формата (одна позиция в поле 'position') на новый
    (несколько позиций в 'positions', ключ — пара)."""
    positions = state.get("positions")
    if positions:
        return positions
    old = state.get("position")
    if old and old.get("symbol"):
        return {_safe_key(old["symbol"]): old}
    return {}

def save_stats(bot_name, balance, wins, losses, daily_trades, daily_loss):
    """Сохранить баланс/статистику. НАРОЧНО не трогает поле positions —
    иначе при двух параллельных инстансах бота (во время передеплоя) один
    из них мог бы перезаписать своим (неполным) списком позиций то, что
    только что атомарно застолбил другой инстанс через try_claim_position.
    Открытие/закрытие конкретных позиций — отдельные атомарные операции
    (try_claim_position / update_position / release_position)."""
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
            "positions": {}, "trades": []
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
                "positions": _normalize_positions(state),
                "trades": trades
            }
    except Exception as e:
        print(f"Ошибка загрузки состояния: {e}")

    return {
        "balance": start_balance,
        "wins": 0, "losses": 0,
        "daily_trades": 0, "daily_loss": 0.0,
        "positions": {}, "trades": []
    }

def get_all_states():
    if db is None:
        default = {"balance":1000,"wins":0,"losses":0,"trades":[],"daily_trades":0,"daily_loss":0,"positions":{}}
        return {"bot1":default.copy(),"bot2":default.copy(),"bot3":default.copy(),"bot4":default.copy()}

    result = {}
    for bot_name in ["bot1","bot2","bot3","bot4"]:
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
                    "positions": _normalize_positions(state),
                    "trades": trades
                }
            else:
                result[bot_name] = {
                    "balance": 1000, "wins": 0, "losses": 0,
                    "daily_trades": 0, "daily_loss": 0.0,
                    "positions": {}, "trades": []
                }
        except Exception as e:
            print(f"Ошибка получения {bot_name}: {e}")
            result[bot_name] = {
                "balance": 1000, "wins": 0, "losses": 0,
                "daily_trades": 0, "daily_loss": 0.0,
                "positions": {}, "trades": []
            }

    return result

connect_db()
