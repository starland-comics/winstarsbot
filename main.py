import asyncio
import hashlib
import hmac
import json
import urllib.parse
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, LabeledPrice

BOT_TOKEN = "9010161317:AAEMgcEP5yhgMiyV8OCOwJ5fpxmAUrg0ke8"  # Укажи токен своего бота
WEBAPP_URL = "https://starland-comics.github.io/winstarsbot/"    # Твой HTTPS URL хостинга
FIREBASE_DB_URL = "https://winstars-435f1-default-rtdb.firebaseio.com"

app = FastAPI()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def serve_index():
    return FileResponse("static/index.html")

# WebSocket Менеджер для внутреннего чата бота
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
        for connection in self.active_connections:
            try:
                await connection.send_text(json.dumps(message))
            except:
                pass

manager = ConnectionManager()

def verify_telegram_data(init_data: str) -> dict:
    if not init_data:
        return {"id": 12345678, "first_name": "Игрок", "username": "user", "photo_url": "https://api.dicebear.com/7.x/bottts/svg?seed=Default"}
    try:
        parsed_qs = urllib.parse.parse_qs(init_data, keep_blank_values=True)
        data_dict = {k: v[0] for k, v in parsed_qs.items() if k != "hash"}
        user_json = data_dict.get("user", "{}")
        return json.loads(user_json)
    except Exception:
        return {"id": 12345678, "first_name": "Игрок", "username": "user", "photo_url": "https://api.dicebear.com/7.x/bottts/svg?seed=Default"}

class PromoRequest(BaseModel):
    user_id: int
    code: str

class BetCreateRequest(BaseModel):
    user_id: int
    match_id: str
    match_title: str
    outcome: str
    coefficient: float
    amount: int

class InvoiceRequest(BaseModel):
    user_id: int
    amount: int

async def firebase_get(path: str):
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{FIREBASE_DB_URL}/{path}.json")
        return resp.json()

async def firebase_put(path: str, data: dict):
    async with httpx.AsyncClient() as client:
        resp = await client.put(f"{FIREBASE_DB_URL}/{path}.json", json=data)
        print(f"🔥 Firebase PUT [{path}]: status {resp.status_code}")
        return resp.json()

async def firebase_patch(path: str, data: dict):
    async with httpx.AsyncClient() as client:
        resp = await client.patch(f"{FIREBASE_DB_URL}/{path}.json", json=data)
        return resp.json()

async def firebase_post(path: str, data: dict):
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{FIREBASE_DB_URL}/{path}.json", json=data)
        return resp.json()

# --- ФОНОВЫЙ СКРАППЕР (МАТЧИ) ---
async def update_matches_in_firebase():
    initial_matches = {
        "match_1": {
            "title": "Реал Мадрид vs Ман Сити",
            "status": "LIVE 68'",
            "score": "2 : 1",
            "odds": {"П1": 1.75, "X": 3.50, "П2": 4.20}
        },
        "match_2": {
            "title": "Арсенал vs Челси",
            "status": "Сегодня 22:00",
            "score": "VS",
            "odds": {"П1": 2.10, "X": 3.10, "П2": 3.50}
        }
    }
    try:
        await firebase_put("matches", initial_matches)
        print("✅ Матчи успешно загружены в Firebase!")
    except Exception as e:
        print(f"❌ Ошибка скраппера при записи матчей: {e}")

async def flashscore_scraper_worker():
    while True:
        await update_matches_in_firebase()
        await asyncio.sleep(30) # Обновление каждые 30 секунд

@app.on_event("startup")
async def startup_event():
    print("🚀 Запуск сервера WinStars...")
    promo = await firebase_get("promo_codes/WINSTARS")
    if not promo:
        await firebase_put("promo_codes/WINSTARS", {"reward_stars": 100, "bonus_percent": 10})
    
    # Сразу заливаем матчи при старте и запускаем фоновый воркер
    asyncio.create_task(update_matches_in_firebase())
    asyncio.create_task(flashscore_scraper_worker())
    asyncio.create_task(dp.start_polling(bot))

# --- API ЭНДПОИНТЫ ---

@app.get("/api/user/sync")
async def sync_user(initData: str = Query("")):
    user_data = verify_telegram_data(initData)
    user_id = str(user_data.get("id", 12345678))
    
    existing_user = await firebase_get(f"users/{user_id}")
    if not existing_user:
        default_user = {
            "user_id": user_id,
            "username": user_data.get("username", "user"),
            "first_name": user_data.get("first_name", "Игрок"),
            "photo_url": user_data.get("photo_url", f"https://api.dicebear.com/7.x/bottts/svg?seed={user_id}"),
            "balance": 250,
            "total_bets": 0,
            "wins": 0,
            "profit": 0
        }
        await firebase_put(f"users/{user_id}", default_user)
        user_info = default_user
    else:
        user_info = existing_user
        
    return {"status": "ok", "user": user_info}

@app.get("/api/matches")
async def get_matches():
    matches = await firebase_get("matches")
    return {"matches": matches or {}}

@app.get("/api/user/bets")
async def get_user_bets(user_id: int):
    bets_dict = await firebase_get(f"users/{user_id}/bets")
    if not bets_dict:
        return {"bets": []}
    return {"bets": [{"id": k, **v} for k, v in bets_dict.items()]}

@app.post("/api/bets/place")
async def place_bet(req: BetCreateRequest):
    user = await firebase_get(f"users/{req.user_id}")
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    balance = user.get("balance", 0)
    if balance < req.amount:
        raise HTTPException(status_code=400, detail="Недостаточно звёзд")
    
    new_balance = balance - req.amount
    total_bets = user.get("total_bets", 0) + 1
    
    bet_data = {
        "match_id": req.match_id,
        "match_title": req.match_title,
        "outcome": req.outcome,
        "coefficient": req.coefficient,
        "amount": req.amount,
        "status": "active"
    }
    
    await firebase_post(f"users/{req.user_id}/bets", bet_data)
    await firebase_patch(f"users/{req.user_id}", {"balance": new_balance, "total_bets": total_bets})
    
    return {"status": "ok", "new_balance": new_balance, "message": "Ставка успешно принята!"}

@app.post("/api/cashier/invoice")
async def create_invoice(req: InvoiceRequest):
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Неверная сумма")
    
    payload = f"topup_{req.user_id}_{req.amount}"
    prices = [LabeledPrice(label="Звёзды WinStars", amount=req.amount)]
    
    try:
        invoice_link = await bot.create_invoice_link(
            title="Пополнение баланса WinStars",
            description=f"Пополнение баланса на {req.amount} Telegram Stars (★)",
            payload=payload,
            currency="XTR",
            prices=prices
        )
        return {"status": "ok", "invoice_link": invoice_link}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/cashier/promo")
async def activate_promo(req: PromoRequest):
    promo = await firebase_get(f"promo_codes/{req.code.upper()}")
    if not promo:
        raise HTTPException(status_code=400, detail="Промокод не найден")
    
    reward = promo.get("reward_stars", 50)
    user = await firebase_get(f"users/{req.user_id}")
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
        
    new_balance = user.get("balance", 0) + reward
    await firebase_patch(f"users/{req.user_id}", {"balance": new_balance})
    return {"status": "ok", "reward": reward, "new_balance": new_balance, "message": f"Начислено {reward} ★"}

@app.get("/api/chat/history")
async def get_chat_history():
    messages_dict = await firebase_get("chat_messages")
    if not messages_dict:
        return {"messages": []}
    return {"messages": list(messages_dict.values())[-50:]}

@app.websocket("/ws/chat")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            payload = json.loads(data)
            msg_data = {
                "user_id": payload["user_id"],
                "first_name": payload["first_name"],
                "text": payload["text"]
            }
            await firebase_post("chat_messages", msg_data)
            await manager.broadcast(msg_data)
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@dp.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: types.PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
    payment = message.successful_payment
    payload = payment.invoice_payload
    
    if payload.startswith("topup_"):
        parts = payload.split("_")
        user_id = parts[1]
        amount = int(parts[2])
        
        user = await firebase_get(f"users/{user_id}")
        if user:
            new_balance = user.get("balance", 0) + amount
            await firebase_patch(f"users/{user_id}", {"balance": new_balance})
            await message.answer(f"✅ Успешно! Баланс пополнен на {amount} ★.")

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚽ Открыть WinStars", web_app=WebAppInfo(url=WEBAPP_URL))]
    ])
    await message.answer("👋 Добро пожаловать в **WinStars**!", reply_markup=kb, parse_mode="Markdown")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
