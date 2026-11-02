import asyncio
import json
import urllib.parse
import httpx
import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, LabeledPrice

BOT_TOKEN = "9010161317:AAEMgcEP5yhgMiyV8OCOwJ5fpxmAUrg0ke8"
WEBAPP_URL = "https://starland-comics.github.io/winstarsbot/"
FIREBASE_DB_URL = "https://winstars-435f1-default-rtdb.firebaseio.com"

app = FastAPI(title="WinStars API")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ограничиваем пул соединений, чтобы не жрало память
limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
http_client = httpx.AsyncClient(timeout=10.0, limits=limits)

@app.on_event("shutdown")
async def shutdown_event():
    await http_client.aclose()

# --- ИСПРАВЛЕННЫЙ МЕНЕДЖЕР ВЕБСОКЕТОВ (БЕЗ УТЕЧЕК ПАМЯТИ) ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            
    async def broadcast(self, message: dict):
        dead_connections = []
        for connection in self.active_connections:
            try: 
                await connection.send_text(json.dumps(message))
            except Exception: 
                dead_connections.append(connection)
        # Удаляем сломанные соединения, чтобы они не висели в памяти
        for conn in dead_connections:
            self.disconnect(conn)

manager = ConnectionManager()

def verify_telegram_data(init_data: str) -> dict:
    if not init_data:
        return {"id": 12345678, "first_name": "Игрок", "username": "user", "photo_url": "https://api.dicebear.com/7.x/bottts/svg?seed=Default"}
    try:
        parsed_qs = urllib.parse.parse_qs(init_data, keep_blank_values=True)
        data_dict = {k: v[0] for k, v in parsed_qs.items() if k != "hash"}
        return json.loads(data_dict.get("user", "{}"))
    except Exception:
        return {"id": 12345678, "first_name": "Игрок", "username": "user", "photo_url": "https://api.dicebear.com/7.x/bottts/svg?seed=Default"}

# --- FIREBASE HELPERS ---
async def firebase_get(path: str):
    try:
        resp = await http_client.get(f"{FIREBASE_DB_URL}/{path}.json")
        return resp.json()
    except: return None

async def firebase_put(path: str, data: dict):
    try: await http_client.put(f"{FIREBASE_DB_URL}/{path}.json", json=data)
    except: pass

async def firebase_patch(path: str, data: dict):
    try: await http_client.patch(f"{FIREBASE_DB_URL}/{path}.json", json=data)
    except: pass

async def firebase_post(path: str, data: dict):
    try: await http_client.post(f"{FIREBASE_DB_URL}/{path}.json", json=data)
    except: pass

@app.on_event("startup")
async def startup_event():
    print("🚀 [Main] Запуск WinStars API (оптимизированный)...")
    promo = await firebase_get("promo_codes/WINSTARS")
    if not promo:
        await firebase_put("promo_codes/WINSTARS", {"reward_stars": 100, "bonus_percent": 10})
    asyncio.create_task(dp.start_polling(bot))

# --- API ЭНДПОИНТЫ (БЕЗ PYDANTIC ДЛЯ ЭКОНОМИИ ПАМЯТИ) ---

@app.get("/api/user/sync")
async def sync_user(initData: str = Query("")):
    user_data = verify_telegram_data(initData)
    user_id = str(user_data.get("id", 12345678))
    existing_user = await firebase_get(f"users/{user_id}")
    if not existing_user:
        default_user = {
            "user_id": user_id, "username": user_data.get("username", "user"),
            "first_name": user_data.get("first_name", "Игрок"),
            "photo_url": user_data.get("photo_url", f"https://api.dicebear.com/7.x/bottts/svg?seed={user_id}"),
            "balance": 0, "total_bets": 0, "wins": 0, "profit": 0
        }
        await firebase_put(f"users/{user_id}", default_user)
        return {"status": "ok", "user": default_user}
    return {"status": "ok", "user": existing_user}

@app.get("/api/matches")
async def get_matches():
    matches = await firebase_get("matches")
    return {"matches": matches or {}}

@app.get("/api/user/bets")
async def get_user_bets(user_id: int):
    bets_dict = await firebase_get(f"users/{user_id}/bets")
    if not bets_dict: return {"bets": []}
    return {"bets": [{"id": k, **v} for k, v in bets_dict.items()]}

@app.post("/api/bets/place")
async def place_bet(request: Request):
    req = await request.json()
    user_id = req.get("user_id")
    amount = req.get("amount")
    
    user = await firebase_get(f"users/{user_id}")
    if not user: raise HTTPException(status_code=404, detail="Пользователь не найден")
    if user.get("balance", 0) < amount: raise HTTPException(status_code=400, detail="Недостаточно звёзд")
    
    new_balance = user.get("balance", 0) - amount
    bet_data = {
        "match_id": req.get("match_id"), "match_title": req.get("match_title"), 
        "outcome": req.get("outcome"), "coefficient": req.get("coefficient"), 
        "amount": amount, "status": "active"
    }
    await firebase_post(f"users/{user_id}/bets", bet_data)
    await firebase_patch(f"users/{user_id}", {"balance": new_balance, "total_bets": user.get("total_bets", 0) + 1})
    return {"status": "ok", "new_balance": new_balance, "message": "Ставка принята!"}

@app.post("/api/cashier/invoice")
async def create_invoice(request: Request):
    req = await request.json()
    user_id = req.get("user_id")
    amount = req.get("amount")
    
    payload = f"topup_{user_id}_{amount}"
    try:
        link = await bot.create_invoice_link(
            title="WinStars", 
            description=f"Пополнение на {amount} ★", 
            payload=payload, 
            currency="XTR", 
            prices=[LabeledPrice(label="Звёзды", amount=amount)]
        )
        return {"status": "ok", "invoice_link": link}
    except Exception as e:
        print(f"❌ ОШИБКА TELEGRAM: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/cashier/promo")
async def activate_promo(request: Request):
    req = await request.json()
    code = req.get("code").upper()
    user_id = req.get("user_id")
    
    promo = await firebase_get(f"promo_codes/{code}")
    if not promo: raise HTTPException(status_code=400, detail="Промокод не найден")
    user = await firebase_get(f"users/{user_id}")
    new_balance = user.get("balance", 0) + promo.get("reward_stars", 50)
    await firebase_patch(f"users/{user_id}", {"balance": new_balance})
    return {"status": "ok", "message": f"Начислено {promo.get('reward_stars', 50)} ★", "new_balance": new_balance}

@app.get("/api/chat/history")
async def get_chat_history():
    messages_dict = await firebase_get("chat_messages")
    return {"messages": list(messages_dict.values())[-50:] if messages_dict else []}

@app.websocket("/ws/chat")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            payload = json.loads(data)
            msg_data = {"user_id": payload["user_id"], "first_name": payload["first_name"], "text": payload["text"]}
            await firebase_post("chat_messages", msg_data)
            await manager.broadcast(msg_data)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)
    finally:
        manager.disconnect(websocket) # Гарантированно удаляем при выходе

# --- TELEGRAM BOT ---
@dp.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: types.PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
    payload = message.successful_payment.invoice_payload
    if payload.startswith("topup_"):
        parts = payload.split("_")
        user_id, amount = parts[1], int(parts[2])
        user = await firebase_get(f"users/{user_id}")
        if user:
            await firebase_patch(f"users/{user_id}", {"balance": user.get("balance", 0) + amount})
            await message.answer(f"✅ Баланс пополнен на {amount} ★!")

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚽ Открыть WinStars", web_app=WebAppInfo(url=WEBAPP_URL))]])
    await message.answer("👋 Добро пожаловать в **WinStars**!", reply_markup=kb, parse_mode="Markdown")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("SERVER_PORT", 8000))
    # Важно: workers=1 и отключение логов uvicorn для экономии памяти
    uvicorn.run(app, host="0.0.0.0", port=port, workers=1, log_level="warning")
