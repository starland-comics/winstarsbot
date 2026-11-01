import asyncio
import hashlib
import hmac
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

import database as db

BOT_TOKEN = "9010161317:AAEMgcEP5yhgMiyV8OCOwJ5fpxmAUrg0ke8"
WEBAPP_URL = "https://starland-comics.github.io/winstarsbot/"  # HTTPS ссылка на твой хостинг

app = FastAPI()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# CORS настройки
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Подключение статических файлов (HTML фронтенда)
app.mount("/app", StaticFiles(directory="static", html=True), name="static")

# WebSocket менеджер для Live Чат
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

# Валидация Telegram WebApp InitData (Защита от подделки ID)
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
        # Для тестовой отладки локально без Telegram
        return {"id": 12345678, "first_name": "TestUser", "username": "tester", "photo_url": ""}

# Pydantic Модели
class PromoRequest(BaseModel):
    user_id: int
    code: str

class BetRequest(BaseModel):
    user_id: int
    match_title: str
    outcome: str
    coefficient: float
    amount: int

# --- REST API REST ЭНДПОИНТЫ ---

@app.on_event("startup")
async def startup_event():
    await db.init_db()
    # Запуск бота параллельно с FastAPI
    asyncio.create_task(dp.start_polling(bot))

@app.get("/api/user/sync")
async def sync_user(initData: str = Query(...)):
    user_data = verify_telegram_data(initData)
    user = await db.get_or_create_user(
        user_id=user_data["id"],
        username=user_data.get("username", ""),
        first_name=user_data.get("first_name", "User"),
        photo_url=user_data.get("photo_url", "")
    )
    return {"status": "ok", "user": user}

@app.post("/api/cashier/promo")
async def activate_promo(req: PromoRequest):
    async with db.aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = db.aiosqlite.Row
        async with conn.execute("SELECT * FROM promo_codes WHERE code = ?", (req.code.upper(),)) as cursor:
            promo = await cursor.fetchone()
            if not promo:
                raise HTTPException(status_code=400, detail="Промокод не найден")
            
            reward = promo["reward_stars"]
            await db.update_balance(req.user_id, reward)
            return {"status": "ok", "reward": reward, "message": f"Начислено {reward} ★"}

@app.get("/api/chat/history")
async def get_chat():
    messages = await db.get_chat_history()
    return {"messages": messages}

# WebSocket для чата
@app.websocket("/ws/chat")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            payload = json.loads(data)
            await db.add_chat_message(payload["user_id"], payload["first_name"], payload["text"])
            await manager.broadcast({
                "first_name": payload["first_name"],
                "text": payload["text"]
            })
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# --- TELEGRAM BOT LOGIC ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚽ Открыть WinStars", web_app=WebAppInfo(url=f"{WEBAPP_URL}/app"))]
    ])
    await message.answer(
        "👋 Добро пожаловать в **WinStars**!\n\nСтавки на футбол звёздами Telegram. Нажми кнопку ниже, чтобы открыть приложение:",
        reply_markup=kb,
        parse_mode="Markdown"
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
