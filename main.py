import asyncio
import json
import urllib.parse
import httpx
import os
import time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query, Request
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, LabeledPrice
from curl_cffi import requests as cffi_requests

BOT_TOKEN = "9010161317:AAEMgcEP5yhgMiyV8OCOwJ5fpxmAUrg0ke8"
WEBAPP_URL = "https://starland-comics.github.io/winstarsbot/"
FIREBASE_DB_URL = "https://winstars-435f1-default-rtdb.firebaseio.com"
OLIMP_LIVE_URL = "https://www.olimp.bet/api/v4/0/live/broadcast/sports-with-competitions-with-events?vids%5B%5D=1"

LIVE_BLACKLIST = [
    "люб", "жен", "мол", "до 19", "до 21", "дубл", "резерв", "академ", 
    "угл", "жк", "кк", "статистика", "видеопросмотр", "штанги", 
    "перекладины", "ауты", "офсайды", "фолы", "голы", "мед. бригад", 
    "хозяева", "гости", "тотал", "фора", "обе забьют", "пенальти", "карточк"
]

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

limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
http_client = httpx.AsyncClient(timeout=10.0, limits=limits)

@app.on_event("shutdown")
async def shutdown_event():
    await http_client.aclose()

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
            try: await connection.send_text(json.dumps(message))
            except Exception: dead_connections.append(connection)
        for conn in dead_connections: self.disconnect(conn)

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
    try: 
        resp = await http_client.post(f"{FIREBASE_DB_URL}/{path}.json", json=data)
        return resp
    except: return None

# --- ФОНОВАЯ ЗАДАЧА ДЛЯ РАСЧЕТА СТАВОК ---
async def settle_bets_worker():
    print("🛠 Фоновая задача расчета ставок запущена...")
    while True:
        try:
            finished = await firebase_get("finished_matches") or {}
            if not isinstance(finished, dict): finished = {}
                
            all_bets = await firebase_get("bets") or {}
            if not isinstance(all_bets, dict):
                await asyncio.sleep(60)
                continue
                
            for bet_id, bet in all_bets.items():
                try:
                    if not isinstance(bet, dict): continue
                    if bet.get("status") != "active": continue
                    
                    events = bet.get("events", [])
                    if not isinstance(events, list): continue
                        
                    all_finished = True
                    results = []
                    for event in events:
                        if not isinstance(event, dict): all_finished = False; break
                        m_id = event.get("match_id")
                        if m_id not in finished:
                            all_finished = False; break
                        
                        res = finished[m_id]
                        if not isinstance(res, dict): all_finished = False; break
                        results.append(res)
                    
                    if not all_finished or not results: continue
                    
                    is_won = True
                    for i, event in enumerate(events):
                        res = results[i]
                        s1, s2 = res.get("score1", 0), res.get("score2", 0)
                        outcome = event.get("outcome")
                        
                        if outcome == "П1" and not (s1 > s2): is_won = False; break
                        if outcome == "Х" and not (s1 == s2): is_won = False; break
                        if outcome == "П2" and not (s2 > s1): is_won = False; break
                    
                    status = "won" if is_won else "lost"
                    update_data = {"status": status}
                    
                    if status == "won":
                        user_id = bet.get("user_id")
                        user = await firebase_get(f"users/{user_id}")
                        if user and isinstance(user, dict):
                            win_amount = bet.get("amount", 0) * bet.get("total_coefficient", 1)
                            new_bal = user.get("balance", 0) + win_amount
                            new_profit = user.get("profit", 0) + (win_amount - bet.get("amount", 0))
                            new_wins = user.get("wins", 0) + 1
                            await firebase_patch(f"users/{user_id}", {
                                "balance": new_bal, "profit": new_profit, "wins": new_wins
                            })
                    
                    await firebase_patch(f"bets/{bet_id}", update_data)
                    if bet.get("user_id"):
                        await firebase_patch(f"users/{bet['user_id']}/bets/{bet_id}", update_data)
                    print(f"✅ Ставка {bet_id} рассчитана: {status}")
                except Exception as inner_e:
                    print(f"⚠️ Пропущена ставка {bet_id} из-за ошибки: {inner_e}")
                    continue
                
        except Exception as e:
            print(f"⚠️ Ошибка в settle_bets: {e}")
        
        await asyncio.sleep(60)

# --- ФОНОВАЯ ЗАДАЧА ДЛЯ LIVE ОБНОВЛЕНИЙ (30 СЕКУНД) ---
async def live_scraper_worker():
    print("⚡ Фоновая задача LIVE-обновлений запущена...")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", "Accept": "application/json"}
    while True:
        try:
            resp = await asyncio.to_thread(cffi_requests.get, OLIMP_LIVE_URL, headers=headers, impersonate="chrome120", timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                live_updates = {}
                for sport_group in data:
                    sport = sport_group.get("payload", {}).get("sport", {})
                    if str(sport.get("id")) != "1": continue
                    comps = sport_group.get("payload", {}).get("competitionsWithEvents", [])
                    for comp in comps:
                        comp_name = comp.get("competition", {}).get("name", "")
                        for ev in comp.get("events", []):
                            t1 = ev.get("team1Name", ""); t2 = ev.get("team2Name", "")
                            if any(word in f"{t1} {t2} {comp_name}".lower() for word in LIVE_BLACKLIST): continue
                            ev_id = ev.get("id")
                            if not ev_id: continue
                            
                            score = "VS"
                            sc = ev.get("score")
                            if isinstance(sc, dict):
                                s1 = sc.get("team1") or sc.get("home") or sc.get("score1")
                                s2 = sc.get("team2") or sc.get("away") or sc.get("score2")
                                if s1 is not None and s2 is not None: score = f"{s1} : {s2}"
                            elif isinstance(sc, str) and ":" in sc: score = sc
                            if score == "VS":
                                s1 = ev.get("score1") or ev.get("homeScore")
                                s2 = ev.get("score2") or ev.get("awayScore")
                                if s1 is not None and s2 is not None: score = f"{s1} : {s2}"
                            
                            minute = ""
                            t = ev.get("time")
                            if isinstance(t, dict): minute = t.get("minute") or t.get("current") or t.get("elapsed")
                            elif isinstance(t, (int, str)): minute = t
                            if not minute: minute = ev.get("minute") or ev.get("currentMinute") or ev.get("liveTime")
                            status = f"LIVE {minute}'" if minute else "LIVE"
                            
                            p1 = px = p2 = None
                            for out in ev.get("outcomes", []):
                                short_name = out.get("shortName", "")
                                if "Исход" in out.get("groupName", "") or "Результат" in out.get("groupName", ""):
                                    if short_name in ["П1", "1"]: p1 = out.get("probability")
                                    elif short_name in ["X", "Х"]: px = out.get("probability")
                                    elif short_name in ["П2", "2"]: p2 = out.get("probability")
                            
                            live_updates[f"olimp_{ev_id}"] = {"status": status, "score": score, "odds": {"1": p1, "X": px, "2": p2}}
                
                if live_updates:
                    async with httpx.AsyncClient() as client:
                        await client.patch(f"{FIREBASE_DB_URL}/matches.json", json=live_updates, timeout=10.0)
                    print(f"🔄 [LIVE Worker] Обновлено матчей: {len(live_updates)}")
        except Exception as e:
            print(f"⚠️ Ошибка в live_scraper_worker: {e}")
        await asyncio.sleep(30)

@app.on_event("startup")
async def startup_event():
    print("🚀 [Main] Запуск WinStars API...")
    promo = await firebase_get("promo_codes/WINSTARS")
    if not promo: await firebase_put("promo_codes/WINSTARS", {"reward_stars": 100, "bonus_percent": 10})
    asyncio.create_task(dp.start_polling(bot))
    asyncio.create_task(settle_bets_worker())
    asyncio.create_task(live_scraper_worker())

@app.get("/api/proxy-image")
async def proxy_image(url: str):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": "https://www.fon.bet/"}
        resp = await http_client.get(url, headers=headers, timeout=5.0)
        if resp.status_code == 200: return Response(content=resp.content, media_type="image/png")
    except Exception: pass
    return Response(status_code=404)

# --- ИСПРАВЛЕННЫЙ SYNC_USER (ЛЕЧИТ UNDEFINED) ---
@app.get("/api/user/sync")
async def sync_user(initData: str = Query("")):
    user_data = verify_telegram_data(initData)
    user_id = str(user_data.get("id", 12345678))
    existing_user = await firebase_get(f"users/{user_id}")
    
    # Если юзера нет, или это строка, или в нем нет баланса — создаем/чиним
    if not isinstance(existing_user, dict) or "balance" not in existing_user:
        old_balance = existing_user.get("balance", 0) if isinstance(existing_user, dict) else 0
        default_user = {
            "user_id": user_id, "username": user_data.get("username", "user"),
            "first_name": user_data.get("first_name", "Игрок"),
            "photo_url": user_data.get("photo_url", f"https://api.dicebear.com/7.x/bottts/svg?seed={user_id}"),
            "balance": old_balance if isinstance(old_balance, (int, float)) else 0, 
            "total_bets": existing_user.get("total_bets", 0) if isinstance(existing_user, dict) else 0,
            "wins": existing_user.get("wins", 0) if isinstance(existing_user, dict) else 0,
            "profit": existing_user.get("profit", 0) if isinstance(existing_user, dict) else 0
        }
        await firebase_put(f"users/{user_id}", default_user)
        return {"status": "ok", "user": default_user}
        
    return {"status": "ok", "user": existing_user}

@app.get("/api/matches")
async def get_matches():
    matches = await firebase_get("matches")
    return {"matches": matches if isinstance(matches, dict) else {}}

@app.get("/api/user/bets")
async def get_user_bets(user_id: int):
    bets_dict = await firebase_get(f"users/{user_id}/bets")
    if not isinstance(bets_dict, dict): return {"bets": []}
    return {"bets": [{"id": k, **v} for k, v in bets_dict.items() if isinstance(v, dict)]}

@app.post("/api/bets/place")
async def place_bet(request: Request):
    req = await request.json()
    user_id = req.get("user_id")
    amount = req.get("amount")
    bets = req.get("bets", [])
    bet_type = req.get("type", "single")
    
    if not bets: raise HTTPException(status_code=400, detail="Нет выбранных исходов")

    user = await firebase_get(f"users/{user_id}")
    if not isinstance(user, dict): raise HTTPException(status_code=404, detail="Ошибка профиля. Перезайдите в приложение.")
    
    user_balance = user.get("balance", 0)
    if not isinstance(user_balance, (int, float)): user_balance = 0
    if user_balance < amount: raise HTTPException(status_code=400, detail="Недостаточно звёзд")
    
    total_coeff = 1.0
    for b in bets: total_coeff *= float(b.get("coefficient", 1.0))
    total_coeff = round(total_coeff, 2)

    new_balance = user_balance - amount
    
    bet_data = {
        "user_id": str(user_id), "type": bet_type, "events": bets,
        "amount": amount, "total_coefficient": total_coeff,
        "potential_win": round(amount * total_coeff, 2),
        "status": "active", "timestamp": int(time.time())
    }
    
    bet_ref = await firebase_post("bets", bet_data)
    bet_id = bet_ref.json().get("name") if bet_ref else None
    
    if bet_id: await firebase_put(f"users/{user_id}/bets/{bet_id}", bet_data)
    await firebase_patch(f"users/{user_id}", {"balance": new_balance, "total_bets": user.get("total_bets", 0) + 1})
    return {"status": "ok", "new_balance": new_balance, "message": "Прогноз принят!"}

@app.post("/api/cashier/invoice")
async def create_invoice(request: Request):
    req = await request.json()
    user_id = req.get("user_id")
    amount = req.get("amount")
    payload = f"topup_{user_id}_{amount}"
    try:
        link = await bot.create_invoice_link(title="WinStars", description=f"Пополнение на {amount} ★", payload=payload, currency="XTR", prices=[LabeledPrice(label="Звёзды", amount=amount)])
        return {"status": "ok", "invoice_link": link}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/cashier/promo")
async def activate_promo(request: Request):
    req = await request.json()
    code = req.get("code").upper()
    user_id = req.get("user_id")
    promo = await firebase_get(f"promo_codes/{code}")
    if not promo: raise HTTPException(status_code=400, detail="Промокод не найден")
    
    user = await firebase_get(f"users/{user_id}")
    if not isinstance(user, dict): raise HTTPException(status_code=400, detail="Ошибка профиля")
    
    current_bal = user.get("balance", 0)
    if not isinstance(current_bal, (int, float)): current_bal = 0
    new_balance = current_bal + promo.get("reward_stars", 50)
    
    await firebase_patch(f"users/{user_id}", {"balance": new_balance})
    return {"status": "ok", "message": f"Начислено {promo.get('reward_stars', 50)} ★", "new_balance": new_balance}

@app.get("/api/chat/history")
async def get_chat_history():
    messages_dict = await firebase_get("chat_messages")
    return {"messages": list(messages_dict.values())[-50:] if isinstance(messages_dict, dict) else []}

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
    except WebSocketDisconnect: manager.disconnect(websocket)
    except Exception: manager.disconnect(websocket)
    finally: manager.disconnect(websocket)

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
        if isinstance(user, dict):
            curr_bal = user.get("balance", 0)
            if not isinstance(curr_bal, (int, float)): curr_bal = 0
            await firebase_patch(f"users/{user_id}", {"balance": curr_bal + amount})
            await message.answer(f"✅ Баланс пополнен на {amount} ★!")

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚽ Открыть WinStars", web_app=WebAppInfo(url=WEBAPP_URL))]])
    await message.answer("👋 Добро пожаловать в **WinStars**!", reply_markup=kb, parse_mode="Markdown")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("SERVER_PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, workers=1, log_level="warning")