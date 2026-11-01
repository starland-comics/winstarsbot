import asyncio
import json
import urllib.parse
import httpx
import time
from datetime import datetime
from curl_cffi import requests as cffi_requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, LabeledPrice
import os

# --- НАСТРОЙКИ ---
BOT_TOKEN = "9010161317:AAEMgcEP5yhgMiyV8OCOwJ5fpxmAUrg0ke8"
WEBAPP_URL = "https://starland-comics.github.io/winstarsbot/"
FIREBASE_DB_URL = "https://winstars-435f1-default-rtdb.firebaseio.com"

app = FastAPI(title="WinStars Platform", version="20.0")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- НОВЫЙ БЫСТРЫЙ СКРАППЕР FONBET ---
BASE_URL = "https://line-lb51.bk6bba-resources.com"
FONBET_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.fon.bet/",
}

# Кэш лиг для правильных названий (РПЛ, АПЛ и т.д.)
LEAGUES_CACHE = {}
LEAGUES_LAST_UPDATE = 0

def get_fonbet_data_sync(url):
    try:
        r = cffi_requests.get(url, headers=FONBET_HEADERS, impersonate="chrome120", timeout=10)
        if r.status_code == 200:
            return r.json()
    except:
        pass
    return None

async def update_leagues_cache():
    global LEAGUES_CACHE, LEAGUES_LAST_UPDATE
    url = f"{BASE_URL}/sports?lang=ru"
    data = await asyncio.to_thread(get_fonbet_data_sync, url)
    
    if data and "sports" in data:
        new_cache = {}
        for sport in data["sports"]:
            # ЖЕСТКИЙ ФИЛЬТР: Только футбол. Американский футбол и регби отсекаются.
            if sport.get("name") != "Футбол": continue
            for region in sport.get("regions", []):
                for comp in region.get("competitions", []):
                    cid = comp.get("id")
                    if cid:
                        new_cache[int(cid)] = comp.get("name", "Турнир")
        if new_cache:
            LEAGUES_CACHE = new_cache
            LEAGUES_LAST_UPDATE = time.time()
            print(f"✅ Справочник лиг обновлен. Загружено {len(LEAGUES_CACHE)} турниров.")

async def fetch_odds_and_score(event_id, sem):
    async with sem:
        url = f"{BASE_URL}/events/event?eventId={event_id}&scopeMarket=1600&lang=ru"
        data = await asyncio.to_thread(get_fonbet_data_sync, url)
        if not data: return None
        
        odds = {"1": None, "X": None, "2": None}
        for cf in data.get("customFactors", []):
            for f in cf.get("factors", []):
                fid, v = f.get("f"), f.get("v")
                if fid == 921: odds["1"] = v
                elif fid == 922: odds["X"] = v
                elif fid == 923: odds["2"] = v
            # Берем первый полный набор (основная линия)
            if odds["1"] and odds["X"] and odds["2"]:
                break
        
        if not odds["1"]: return None
        
        # Достаем счет и минуту для Live (динамика)
        event_info = data.get("event", {})
        score1 = event_info.get("score1")
        score2 = event_info.get("score2")
        minute = event_info.get("time", "")
        place = event_info.get("place", "line")
        
        score_str = f"{score1} : {score2}" if score1 is not None and place == "live" else "VS"
        status_str = f"LIVE {minute}'" if place == "live" else "Скоро"
        
        return odds, score_str, status_str

async def firebase_put(path: str, data: any):
    async with httpx.AsyncClient() as client:
        try:
            await client.put(f"{FIREBASE_DB_URL}/{path}.json", json=data)
        except Exception as e:
            print(f"Firebase PUT Error: {e}")

async def fonbet_worker():
    print("🔄 Запуск быстрого воркера Fonbet...")
    global LEAGUES_LAST_UPDATE
    
    while True:
        try:
            # Обновляем справочник лиг раз в час
            if not LEAGUES_CACHE or (time.time() - LEAGUES_LAST_UPDATE > 3600):
                await update_leagues_cache()

            list_url = f"{BASE_URL}/events/listBase?scopeMarket=1600&lang=ru"
            data = await asyncio.to_thread(get_fonbet_data_sync, list_url)
            
            if data and "events" in data:
                events = data.get("events", [])
                now = time.time()
                valid_events = []
                for ev in events:
                    # Фильтр: только лиги из кэша (футбол)
                    if int(ev.get("sportId", 0)) in LEAGUES_CACHE and ev.get("team1") and ev.get("team2"):
                        start = ev.get("startTime", 0)
                        # Берем матчи, которые начнутся в будущем или начались не более 3 часов назад
                        if start > now - 10800:
                            valid_events.append(ev)
                
                # Асинхронный сбор кэфов (20 потоков) для максимальной скорости
                sem = asyncio.Semaphore(20)
                tasks = [fetch_odds_and_score(ev["id"], sem) for ev in valid_events]
                results = await asyncio.gather(*tasks)
                
                matches = {}
                for ev, res in zip(valid_events, results):
                    if res:
                        odds, score, status = res
                        league_id = int(ev.get("sportId", 0))
                        league_name = LEAGUES_CACHE.get(league_id, "Футбол")
                        
                        matches[f"fon_{ev['id']}"] = {
                            "title": f"{ev['team1']} - {ev['team2']}",
                            "home": ev['team1'],
                            "away": ev['team2'],
                            "homeLogo": f"https://static.fon.bet/team_logos/{ev.get('team1Id')}.png",
                            "awayLogo": f"https://static.fon.bet/team_logos/{ev.get('team2Id')}.png",
                            "league": league_name,
                            "status": status,
                            "score": score,
                            "start_time": ev.get("startTime"),
                            "odds": odds
                        }
                
                if matches:
                    await firebase_put("matches", matches)
                    print(f"✅ Обновлено {len(matches)} матчей (Live и Линия)")
                    
        except Exception as e:
            print(f"Ошибка воркера: {e}")
            
        await asyncio.sleep(30) # Обновление раз в 30 секунд

# --- WEB SOCKET ЧАТ ---
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
        return json.loads(data_dict.get("user", "{}"))
    except Exception:
        return {"id": 12345678, "first_name": "Игрок", "username": "user", "photo_url": "https://api.dicebear.com/7.x/bottts/svg?seed=Default"}

# --- МОДЕЛИ ДАННЫХ ---
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

class PromoRequest(BaseModel):
    user_id: int
    code: str

# --- FIREBASE HELPERS ---
async def firebase_get(path: str):
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{FIREBASE_DB_URL}/{path}.json")
        return resp.json()

async def firebase_patch(path: str, data: dict):
    async with httpx.AsyncClient() as client:
        resp = await client.patch(f"{FIREBASE_DB_URL}/{path}.json", json=data)
        return resp.json()

async def firebase_post(path: str, data: dict):
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{FIREBASE_DB_URL}/{path}.json", json=data)
        return resp.json()

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
            "balance": 0, # СТАРТОВЫЙ БАЛАНС 0
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

# --- ПЛАТЕЖИ TELEGRAM STARS ---
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
            await message.answer(f"✅ Баланс успешно пополнен на {amount} Telegram Stars (★)!")

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚽ Открыть WinStars", web_app=WebAppInfo(url=WEBAPP_URL))]
    ])
    await message.answer("👋 Добро пожаловать в букмекерскую контору **WinStars**!", reply_markup=kb, parse_mode="Markdown")

@app.on_event("startup")
async def startup_event():
    print("🚀 Запуск WinStars Platform...")
    # Создаем базовый промокод, если его нет
    promo = await firebase_get("promo_codes/WINSTARS")
    if not promo:
        await firebase_put("promo_codes/WINSTARS", {"reward_stars": 100, "bonus_percent": 10})
    
    # Запускаем бота и фоновый скраппер
    asyncio.create_task(dp.start_polling(bot))
    asyncio.create_task(fonbet_worker())

if __name__ == "__main__":
    import uvicorn
    # Берем порт из переменной окружения (обязательно для bot-hosting.net)
    port = int(os.environ.get("SERVER_PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
