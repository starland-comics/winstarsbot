import asyncio
import gc
import json
import urllib.parse
import httpx
import os
import time
import base64
import re
import requests as cffi_requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query, Request
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, LabeledPrice

BOT_TOKEN = "9010161317:AAEMgcEP5yhgMiyV8OCOwJ5fpxmAUrg0ke8"
WEBAPP_URL = "https://starland-comics.github.io/winstarsbot/"
FIREBASE_DB_URL = "https://winstars-435f1-default-rtdb.firebaseio.com"
OLIMP_LIVE_URL = "https://www.olimp.bet/api/v4/0/live/sports-with-competitions-with-events"
OLIMP_LINE_URL = "https://www.olimp.bet/api/v4/0/line/top/sports-with-competitions-with-events"

LIVE_POLL_SEC = 20
SETTLE_POLL_SEC = 60
LOGOS_TTL = 300
MATCHES_TTL = 45
FINISHED_TTL = 90
GC_EVERY = 15

LIVE_BLACKLIST = [
    "люб", "жен", "мол", "до 19", "до 21", "дубл", "резерв", "академ",
    "угл", "жк", "кк", "статистика", "видеопросмотр", "штанги",
    "перекладины", "ауты", "офсайды", "фолы", "голы", "мед. бригад",
    "хозяева", "гости", "тотал", "фора", "обе забьют", "пенальти", "карточк"
]

DEBUG_MINUTE_LOGGED = set()

_LOGOS_CACHE = {"map": {}, "ts": 0}
_MATCHES_CACHE = {"data": {}, "ts": 0}
_FINISHED_CACHE = {"data": {}, "ts": 0}

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

limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
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
        dead = []
        for c in self.active_connections:
            try: await c.send_text(json.dumps(message))
            except Exception: dead.append(c)
        for conn in dead: self.disconnect(conn)

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

async def firebase_delete(path: str):
    try: await http_client.delete(f"{FIREBASE_DB_URL}/{path}.json")
    except: pass

async def firebase_post(path: str, data: dict):
    try:
        resp = await http_client.post(f"{FIREBASE_DB_URL}/{path}.json", json=data)
        return resp
    except: return None

def _build_logo_map(raw_cache):
    result = {}
    if not isinstance(raw_cache, dict): return result
    for k, v in raw_cache.items():
        if not v: continue
        real_key = k
        if k.startswith("b64_"):
            try:
                pad = "=" * (-len(k[4:]) % 4)
                real_key = base64.urlsafe_b64decode(k[4:] + pad).decode("utf-8")
            except: continue
        if "|" in real_key:
            team_part = real_key.split("|")[0]
            if team_part not in result:
                result[team_part] = v
        else:
            result[real_key] = v
    return result

async def get_logos_map():
    now = time.time()
    if now - _LOGOS_CACHE["ts"] < LOGOS_TTL and _LOGOS_CACHE["map"]:
        return _LOGOS_CACHE["map"]
    raw = await firebase_get("team_logos") or {}
    _LOGOS_CACHE["map"] = _build_logo_map(raw)
    _LOGOS_CACHE["ts"] = now
    return _LOGOS_CACHE["map"]

async def get_matches_cached():
    now = time.time()
    if now - _MATCHES_CACHE["ts"] < MATCHES_TTL and _MATCHES_CACHE["data"]:
        return _MATCHES_CACHE["data"]
    raw = await firebase_get("matches") or {}
    if not isinstance(raw, dict): raw = {}
    _MATCHES_CACHE["data"] = raw
    _MATCHES_CACHE["ts"] = now
    return raw

def invalidate_matches_cache():
    _MATCHES_CACHE["ts"] = 0

async def get_finished_cached():
    now = time.time()
    if now - _FINISHED_CACHE["ts"] < FINISHED_TTL and _FINISHED_CACHE["data"]:
        return _FINISHED_CACHE["data"]
    raw = await firebase_get("finished_matches") or {}
    if not isinstance(raw, dict): raw = {}
    _FINISHED_CACHE["data"] = raw
    _FINISHED_CACHE["ts"] = now
    return raw

def _try_extract_minute(val):
    if val is None or isinstance(val, bool): return None
    if isinstance(val, (int, float)):
        return int(val) if 0 <= val <= 130 else None
    if isinstance(val, str):
        s = val.strip(); sl = s.lower()
        if any(x in sl for x in ["перерыв", "halftime", "half time", "ht"]): return "HT"
        if any(x in sl for x in ["завершен", "finished", "ended", "full time", "ft"]): return "FT"
        if any(x in sl for x in ["1st half", "1h", "первый тайм"]): return "1H"
        if any(x in sl for x in ["2nd half", "2h", "второй тайм"]): return "2H"
        m = re.search(r"(\d{1,3})", s)
        if m:
            n = int(m.group(1))
            if 0 <= n <= 130: return n
        return None
    if isinstance(val, dict):
        for sub in ["minute", "current", "elapsed", "value", "display", "text", "seconds", "currentMinute"]:
            if sub in val:
                r = _try_extract_minute(val[sub])
                if r is not None: return r
        return None
    return None

def extract_live_score_and_time(ev):
    score = None; minute = None
    sc = ev.get("score")
    if isinstance(sc, dict):
        s1 = sc.get("team1") or sc.get("home") or sc.get("score1")
        s2 = sc.get("team2") or sc.get("away") or sc.get("score2")
        if s1 is not None and s2 is not None: score = f"{s1} : {s2}"
    elif isinstance(sc, str) and ":" in sc: score = sc
    if not score:
        s1 = ev.get("score1") or ev.get("homeScore")
        s2 = ev.get("score2") or ev.get("awayScore")
        if s1 is not None and s2 is not None: score = f"{s1} : {s2}"

    for field in ["minute", "currentMinute", "liveTime", "liveTimer", "elapsed",
                  "time", "timer", "matchTime", "gameTime", "periodTime",
                  "currentTime", "timeLive", "liveMinute", "scoreTime", "statusTime",
                  "gameMinute", "elapsedTime", "timePlayed", "minutes", "minutePlayed"]:
        if field in ev:
            r = _try_extract_minute(ev[field])
            if r is not None: minute = r; break

    if minute is None:
        for nk in ["score", "status", "live", "progress", "timer", "time"]:
            nested = ev.get(nk)
            if isinstance(nested, dict):
                r = _try_extract_minute(nested)
                if r is not None: minute = r; break

    if minute is None:
        for pf in ["period", "timeStatus", "status", "liveStatus", "matchStatus", "half", "timePeriod"]:
            v = ev.get(pf)
            if isinstance(v, str):
                vl = v.lower()
                if any(x in vl for x in ["ht", "halftime", "перерыв", "half"]): minute = "HT"; break
                if any(x in vl for x in ["1h", "1st", "первый тайм"]): minute = "1H"; break
                if any(x in vl for x in ["2h", "2nd", "второй тайм"]): minute = "2H"; break

    if minute is None:
        start_ts = ev.get("startDateTime")
        if start_ts:
            now = time.time()
            elapsed_mins = (now - start_ts) / 60.0
            if elapsed_mins < 0:
                minute = 0
            elif elapsed_mins < 47:
                minute = int(elapsed_mins)
            elif elapsed_mins < 62:
                minute = "HT"
            elif elapsed_mins < 107:
                minute = int(elapsed_mins) - 15
            else:
                minute = "FT"

    return score, minute

def _status_from_minute(minute):
    if minute is None: return "LIVE"
    if minute in ("HT", "FT", "1H", "2H"): return f"LIVE · {minute}"
    return f"LIVE {minute}'"

async def live_scraper_worker():
    print(f"⚡ live_scraper_worker запущен (опрос {LIVE_POLL_SEC} сек)...")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", "Accept": "application/json"}
    cycle = 0

    while True:
        try:
            resp = await asyncio.to_thread(cffi_requests.get, OLIMP_LIVE_URL, headers=headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                del resp

                live_updates = {}
                team_logo_map = await get_logos_map()

                for sg in data:
                    sport = sg.get("payload", {}).get("sport", {})
                    if str(sport.get("id")) != "1": continue
                    comps = sg.get("payload", {}).get("competitionsWithEvents", [])
                    for comp in comps:
                        comp_name = comp.get("competition", {}).get("name", "Футбол")
                        for ev in comp.get("events", []):
                            t1 = ev.get("team1Name", ""); t2 = ev.get("team2Name", "")
                            if any(w in f"{t1} {t2} {comp_name}".lower() for w in LIVE_BLACKLIST): continue
                            ev_id = ev.get("id")
                            if not ev_id: continue

                            mid = f"olimp_{ev_id}"
                            score, minute = extract_live_score_and_time(ev)
                            status = _status_from_minute(minute)

                            if minute is None and mid not in DEBUG_MINUTE_LOGGED and len(DEBUG_MINUTE_LOGGED) < 100:
                                time_fields = {k: v for k, v in ev.items() if any(x in k.lower() for x in
                                    ["time", "minute", "period", "status", "timer", "half", "live", "progress", "clock", "stage"])}
                                print(f"   🔍 NO MINUTE {mid}: {json.dumps(time_fields, ensure_ascii=False, default=str)[:1200]}")
                                DEBUG_MINUTE_LOGGED.add(mid)

                            p1 = px = p2 = None
                            for out in ev.get("outcomes", []):
                                sn = out.get("shortName", "")
                                if "Исход" in out.get("groupName", "") or "Результат" in out.get("groupName", ""):
                                    if sn in ["П1", "1"]: p1 = out.get("probability")
                                    elif sn in ["X", "Х"]: px = out.get("probability")
                                    elif sn in ["П2", "2"]: p2 = out.get("probability")

                            live_updates[mid] = {
                                "title": f"{t1} - {t2}", "home": t1, "away": t2,
                                "homeLogo": team_logo_map.get(t1, ""),
                                "awayLogo": team_logo_map.get(t2, ""),
                                "league": comp_name,
                                "status": status,
                                "score": score or "VS",
                                "start_time": ev.get("startDateTime"),
                                "odds": {"1": p1, "X": px, "2": p2}
                            }
                            print(f"   ⚽ [LIVE] {t1} — {t2} | {score or 'VS'} | {status} | {comp_name} | odds {p1}/{px}/{p2}")

                del data
                if live_updates:
                    async with httpx.AsyncClient() as client:
                        await client.patch(f"{FIREBASE_DB_URL}/matches.json", json=live_updates, timeout=10.0)
                    invalidate_matches_cache()
                    print(f"🔄 [LIVE] Записано: {len(live_updates)}")
        except Exception as e:
            print(f"⚠️ live_scraper: {e}")

        cycle += 1
        if cycle % GC_EVERY == 0: gc.collect()
        await asyncio.sleep(LIVE_POLL_SEC)

async def settle_bets_worker():
    print("🛠 settle_bets_worker запущен...")
    cycle = 0
    while True:
        try:
            active_bets = await firebase_get("active_bets") or {}
            if isinstance(active_bets, dict) and active_bets:
                finished = await get_finished_cached()
                for bet_id, bet in list(active_bets.items()):
                    try:
                        if not isinstance(bet, dict): continue
                        events = bet.get("events", [])
                        if not isinstance(events, list): continue

                        all_finished = True; results = []; is_void = False; waiting = []
                        for event in events:
                            if not isinstance(event, dict): all_finished = False; break
                            m_id = event.get("match_id")
                            if m_id not in finished:
                                all_finished = False
                                waiting.append(event.get("match_title", m_id))
                                continue
                            res = finished[m_id]
                            if not isinstance(res, dict): all_finished = False; break
                            if res.get("void"): is_void = True
                            results.append(res)

                        if not all_finished or not results:
                            if waiting:
                                print(f"   ⏳ [SETTLE] Ставка {bet_id} ждёт результат: {', '.join(waiting)}")
                            continue
                        user_id = bet.get("user_id")
                        print(f"   🔍 [SETTLE] Проверяю ставку {bet_id}: {len(events)} событий, есть все результаты")

                        if is_void:
                            status = "void"
                            user = await firebase_get(f"users/{user_id}")
                            if isinstance(user, dict):
                                refund = bet.get("amount", 0)
                                new_bal = user.get("balance", 0) + refund
                                new_total = max(0, user.get("total_bets", 1) - 1)
                                await firebase_patch(f"users/{user_id}", {"balance": new_bal, "total_bets": new_total})
                                print(f"   ↩️ Void {bet_id}: возврат {refund} ★")
                        else:
                            is_won = True
                            for i, event in enumerate(events):
                                res = results[i]
                                s1, s2 = res.get("score1", 0), res.get("score2", 0)
                                outcome = event.get("outcome")
                                if outcome == "П1" and not (s1 > s2): is_won = False; break
                                if outcome == "Х" and not (s1 == s2): is_won = False; break
                                if outcome == "П2" and not (s2 > s1): is_won = False; break
                            status = "won" if is_won else "lost"

                            if status == "won":
                                user = await firebase_get(f"users/{user_id}")
                                if isinstance(user, dict):
                                    win_amount = bet.get("amount", 0) * bet.get("total_coefficient", 1)
                                    await firebase_patch(f"users/{user_id}", {
                                        "balance": user.get("balance", 0) + win_amount,
                                        "profit": user.get("profit", 0) + (win_amount - bet.get("amount", 0)),
                                        "wins": user.get("wins", 0) + 1
                                    })
                            print(f"   ✅ Ставка {bet_id}: {status}")

                        await firebase_patch(f"bets/{bet_id}", {"status": status})
                        if user_id:
                            await firebase_patch(f"users/{user_id}/bets/{bet_id}", {"status": status})
                        await firebase_delete(f"active_bets/{bet_id}")
                        print(f"   🗑 active_bets/{bet_id} удалён")
                    except Exception as e:
                        print(f"⚠️ Ставка {bet_id}: {e}")
        except Exception as e:
            print(f"⚠️ settle_bets: {e}")

        cycle += 1
        if cycle % GC_EVERY == 0: gc.collect()
        await asyncio.sleep(SETTLE_POLL_SEC)

@app.on_event("startup")
async def startup_event():
    print("🚀 [Main] Запуск WinStars API...")
    promo = await firebase_get("promo_codes/WINSTARS")
    if not promo: await firebase_put("promo_codes/WINSTARS", {"reward_stars": 100, "bonus_percent": 10})

    try:
        all_bets = await firebase_get("bets") or {}
        if isinstance(all_bets, dict):
            active = {}
            for bet_id, bet in all_bets.items():
                if isinstance(bet, dict) and bet.get("status") == "active":
                    active[bet_id] = {
                        "user_id": bet.get("user_id"),
                        "events": bet.get("events", []),
                        "amount": bet.get("amount", 0),
                        "total_coefficient": bet.get("total_coefficient", 1),
                    }
            if active:
                await firebase_put("active_bets", active)
                print(f"✅ Перенесено активных ставок: {len(active)}")
    except Exception as e:
        print(f"⚠️ migrate: {e}")

    asyncio.create_task(dp.start_polling(bot))
    asyncio.create_task(live_scraper_worker())
    asyncio.create_task(settle_bets_worker())
    print("✅ Все фоновые воркеры запущены (LIVE=20s, SETTLE=60s)")

@app.get("/api/proxy-image")
async def proxy_image(url: str):
    try:
        resp = await http_client.get(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.fon.bet/"}, timeout=5.0)
        if resp.status_code == 200: return Response(content=resp.content, media_type="image/png")
    except Exception: pass
    return Response(status_code=404)

@app.get("/api/user/sync")
async def sync_user(initData: str = Query("")):
    user_data = verify_telegram_data(initData)
    user_id = str(user_data.get("id", 12345678))
    existing_user = await firebase_get(f"users/{user_id}")
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

# ОБНОВЛЕНО: добавлен HEAD для UptimeRobot
@app.api_route("/api/matches", methods=["GET", "HEAD"])
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
    user_id = req.get("user_id"); amount = req.get("amount")
    bets = req.get("bets", []); bet_type = req.get("type", "single")
    if not bets: raise HTTPException(status_code=400, detail="Нет выбранных исходов")

    user = await firebase_get(f"users/{user_id}")
    if not isinstance(user, dict): raise HTTPException(status_code=404, detail="Ошибка профиля. Перезайдите.")
    user_balance = user.get("balance", 0)
    if not isinstance(user_balance, (int, float)): user_balance = 0
    if user_balance < amount: raise HTTPException(status_code=400, detail="Недостаточно звёзд")

    now_ts = time.time()
    for b in bets:
        m_id = b.get("match_id")
        match_db = await firebase_get(f"matches/{m_id}")
        if not match_db: raise HTTPException(status_code=400, detail=f"Матч '{b.get('match_title')}' больше не доступен")
        status = match_db.get("status", "")
        is_live = status.startswith("LIVE")
        start_time = match_db.get("start_time")
        if not is_live and start_time and now_ts > start_time:
            raise HTTPException(status_code=400, detail=f"Матч '{b.get('match_title')}' уже начался. Ставки на линию закрыты.")

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

    if bet_id:
        await firebase_put(f"users/{user_id}/bets/{bet_id}", bet_data)
        await firebase_put(f"active_bets/{bet_id}", {
            "user_id": str(user_id),
            "events": bets,
            "amount": amount,
            "total_coefficient": total_coeff,
        })

    await firebase_patch(f"users/{user_id}", {"balance": new_balance, "total_bets": user.get("total_bets", 0) + 1})
    return {"status": "ok", "new_balance": new_balance, "message": "Прогноз принят!"}

@app.post("/api/cashier/invoice")
async def create_invoice(request: Request):
    req = await request.json()
    user_id = req.get("user_id"); amount = req.get("amount")
    payload = f"topup_{user_id}_{amount}"
    try:
        link = await bot.create_invoice_link(title="WinStars", description=f"Пополнение на {amount} ★", payload=payload, currency="XTR", prices=[LabeledPrice(label="Звёзды", amount=amount)])
        return {"status": "ok", "invoice_link": link}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/cashier/promo")
async def activate_promo(request: Request):
    req = await request.json()
    code = req.get("code").upper(); user_id = req.get("user_id")
    promo = await firebase_get(f"promo_codes/{code}")
    if not promo: raise HTTPException(status_code=400, detail="Промокод не найден")
    user = await firebase_get(f"users/{user_id}")
    if not isinstance(user, dict): raise HTTPException(status_code=400, detail="Ошибка профиля")
    cur = user.get("balance", 0)
    if not isinstance(cur, (int, float)): cur = 0
    new_balance = cur + promo.get("reward_stars", 50)
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
            cur = user.get("balance", 0)
            if not isinstance(cur, (int, float)): cur = 0
            await firebase_patch(f"users/{user_id}", {"balance": cur + amount})
            await message.answer(f"✅ Баланс пополнен на {amount} ★!")

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚽ Открыть WinStars", web_app=WebAppInfo(url=WEBAPP_URL))]])
    await message.answer("👋 Добро пожаловать в **WinStars**!", reply_markup=kb, parse_mode="Markdown")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("SERVER_PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, workers=1, log_level="warning")