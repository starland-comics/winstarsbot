import asyncio
import hashlib
import hmac
import json
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

BOT_TOKEN = "9010161317:AAEMgcEP5yhgMiyV8OCOwJ5fpxmAUrg0ke8"  # Укажи токен своего бота
WEBAPP_URL = "https://starland-comics.github.io/winstarsbot/"    # Твой реальный HTTPS URL хостинга
FIREBASE_DB_URL = "https://winstars-435f1-default-rtdb.firebaseio.com"

app = FastAPI()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Раздача статики и корня сайта
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def serve_index():
    return FileResponse("static/index.html")

# WebSocket менеджер для чата
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            await connection.send_text(json.dumps(message))

manager = ConnectionManager()

# Проверка Telegram initData
def verify_telegram_data(init_data: str) -> dict:
    try:
        parsed_data = dict(qc.split("=") for qc in init_data.split("&"))
        hash_check = parsed_data.pop("hash")
        data_check_string = "\n".join(f"{k}={parsed_data[k]}" for k in sorted(parsed_data.keys()))
        
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if calculated_hash != hash_check:
            raise HTTPException(status_code=403, detail="Invalid hash")
            
        return json.loads(parsed_data.get("user", "{}"))
    except Exception:
        # Резерв для локального теста в браузере без Telegram
        return {"id": 12345678, "first_name": "Тестовый Игрок", "username": "winstar_user", "photo_url": ""}

# Pydantic модели
class PromoRequest(BaseModel):
    user_id: int
    code: str

# --- ФУНКЦИИ РАБОТЫ С FIREBASE RTDB ---
async def firebase_get(path: str):
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{FIREBASE_DB_URL}/{path}.json")
        return resp.json()

async def firebase_put(path: str, data: dict):
    async with httpx.AsyncClient() as client:
        resp = await client.put(f"{FIREBASE_DB_URL}/{path}.json", json=data)
        return resp.json()

async def firebase_patch(path: str, data: dict):
    async with httpx.AsyncClient() as client:
        resp = await client.patch(f"{FIREBASE_DB_URL}/{path}.json", json=data)
        return resp.json()

async def firebase_post(path: str, data: dict):
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{FIREBASE_DB_URL}/{path}.json", json=data)
        return resp.json()

# Инициализация дефолтных промокодов при старте
async def init_firebase_defaults():
    promo = await firebase_get("promo_codes/WINSTARS")
    if not promo:
        await firebase_put("promo_codes/WINSTARS", {"reward_stars": 100, "bonus_percent": 10})

# --- API ЭНДПОИНТЫ ---

@app.on_event("startup")
async def startup_event():
    await init_firebase_defaults()
    asyncio.create_task(dp.start_polling(bot))

@app.get("/api/user/sync")
async def sync_user(initData: str = Query("")):
    user_data = verify_telegram_data(initData)
    user_id = str(user_data.get("id", 12345678))
    
    # Проверяем, есть ли пользователь в Firebase
    existing_user = await firebase_get(f"users/{user_id}")
    
    if not existing_user:
        # Создаем нового пользователя с дефолтными полями
        default_user = {
            "user_id": user_id,
            "username": user_data.get("username", "user"),
            "first_name": user_data.get("first_name", "Игрок"),
            "photo_url": user_data.get("photo_url", ""),
            "balance": 250,
            "total_bets": 0,
            "wins": 0,
            "profit": -255
        }
        await firebase_put(f"users/{user_id}", default_user)
        user_info = default_user
    else:
        user_info = existing_user
        
    return {"status": "ok", "user": user_info}

@app.post("/api/cashier/promo")
async def activate_promo(req: PromoRequest):
    promo = await firebase_get(f"promo_codes/{req.code.upper()}")
    if not promo:
        raise HTTPException(status_code=400, detail="Промокод не найден или недействителен")
    
    reward = promo.get("reward_stars", 50)
    
    # Получаем текущего пользователя и обновляем баланс
    user = await firebase_get(f"users/{req.user_id}")
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
        
    new_balance = user.get("balance", 0) + reward
    await firebase_patch(f"users/{req.user_id}", {"balance": new_balance})
    
    return {"status": "ok", "reward": reward, "new_balance": new_balance, "message": f"Успешно! Начислено {reward} ★"}

# WebSocket чат с сохранением в Firebase
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
            # Сохраняем в Firebase
            await firebase_post("chat_messages", msg_data)
            
            # Рассылаем всем подключенным клиентам
            await manager.broadcast(msg_data)
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# --- TELEGRAM BOT ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚽ Открыть WinStars", web_app=WebAppInfo(url=WEBAPP_URL))]
    ])
    await message.answer(
        "👋 Добро пожаловать в **WinStars**!\n\nПлатформа для ставок звёздами Telegram. Нажми кнопку ниже:",
        reply_markup=kb,
        parse_mode="Markdown"
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
