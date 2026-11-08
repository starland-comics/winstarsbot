import asyncio
import gc
import json
import urllib.parse
import httpx
import os
import time
import base64
import re
import hashlib
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query, Request
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, LabeledPrice
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from curl_cffi import requests as cffi_requests

BOT_TOKEN = "9010161317:AAEMgcEP5yhgMiyV8OCOwJ5fpxmAUrg0ke8"
WEBAPP_URL = "https://starland-comics.github.io/winstarsbot/"
FIREBASE_DB_URL = "https://winstars-435f1-default-rtdb.firebaseio.com"

ADMIN_TELEGRAM_ID = 8425538809
CHANNEL_USERNAME = "winstarschannel"
CHANNEL_ID = "@winstarschannel"
BOT_USERNAME = "WinStarsPlaybot"

WITHDRAW_MIN = 1000
WITHDRAW_COMMISSION = 0.05

TERMS_TEXT = (
    "<b>ПОЛЬЗОВАТЕЛЬСКОЕ СОГЛАШЕНИЕ WINSTARS</b>\n\n"
    "1. WinStars — сервис прогнозов на спортивные события через Telegram.\n\n"
    "2. Доступен только лицам старше 18 лет.\n\n"
    "3. Прогнозы добровольные. Пользователь несёт полную финансовую ответственность за свои решения и возможные потери.\n\n"
    "4. Расчёт прогнозов производится по публичным данным Fonbet на момент размещения прогноза.\n\n"
    "5. Вывод доступен при условиях: 5 рефералов, подписка на канал @winstarschannel, минимум 1000★. Комиссия 5%. Срок обработки до 5 рабочих дней.\n\n"
    "6. Запрещено создание мультиаккаунтов, использование ботов, мошенничество. При обнаружении — блокировка без возврата средств.\n\n"
    "7. Промокоды, бонусы и акции предоставляются на усмотрение сервиса и могут быть отменены без объяснения причин.\n\n"
    "8. Сервис не является букмекерской конторой. Прогнозы не подлежат исполнению при технических сбоях или форс-мажоре.\n\n"
    "9. Правила могут быть изменены без предварительного уведомления.\n\n"
    "10. Используя сервис, вы подтверждаете, что согласны с условиями и вам исполнилось 18 лет."
)

FONBET_BASE = "https://line-lb51.bk6bba-resources.com"
FONBET_LINE_PATH = "/events/listBase?scopeMarket=1600&lang=ru"
FONBET_LIVE_PATH = "/events/list?scopeMarket=1600&lang=ru&live=true"
FONBET_FULL_LINE_PATH = "/events/event?scopeMarket=1600&lang=ru"

FONBET_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.fon.bet/",
    "Origin": "https://www.fon.bet"
}

LIVE_POLL_SEC = 25
SETTLE_POLL_SEC = 60
NOTIFY_POLL_SEC = 60
FINISHED_TTL = 20
FULL_LINE_TTL = 120
FULL_LINE_MAX_PER_CYCLE = 30
SUB_CHECK_TTL = 60

FONBET_FACTORS = {
    921: ("Исход", "П1", False), 922: ("Исход", "Х", False), 923: ("Исход", "П2", False),
    924: ("Двойной шанс", "1Х", False), 925: ("Двойной шанс", "12", False), 926: ("Двойной шанс", "Х2", False),
    927: ("Обе забьют", "Нет", False), 928: ("Обе забьют", "Да", False),
    930: ("Тотал", "ТБ", True), 931: ("Тотал", "ТМ", True),
    1854: ("Тотал", "ТБ", True), 1871: ("Тотал", "ТМ", True),
    1873: ("Тотал", "ТБ", True), 1874: ("Тотал", "ТМ", True),
    1569: ("Тотал", "ТМ", True), 1572: ("Тотал", "ТБ", True),
    1880: ("Тотал", "ТБ", True), 1881: ("Тотал", "ТМ", True),
    1883: ("Тотал", "ТБ", True), 1884: ("Тотал", "ТМ", True),
    1886: ("Тотал", "ТБ", True), 1887: ("Тотал", "ТМ", True),
    1893: ("Тотал", "ТБ", True), 1894: ("Тотал", "ТМ", True),
    1896: ("Тотал", "ТБ", True), 1897: ("Тотал", "ТМ", True),
    910: ("Фора", "Ф1", True), 912: ("Фора", "Ф2", True),
    989: ("Фора", "Ф1", True), 991: ("Фора", "Ф2", True),
    1672: ("Фора", "Ф1", True), 1675: ("Фора", "Ф2", True),
    1677: ("Фора", "Ф1", True), 1678: ("Фора", "Ф2", True),
    1680: ("Фора", "Ф1", True), 1681: ("Фора", "Ф2", True),
    1809: ("Фора", "Ф1", True), 1810: ("Фора", "Ф2", True),
    1812: ("Фора", "Ф1", True), 1813: ("Фора", "Ф2", True),
    1815: ("Фора", "Ф1", True), 1816: ("Фора", "Ф2", True),
    1818: ("Фора", "Ф1", True), 1819: ("Фора", "Ф2", True),
    1821: ("Фора", "Ф1", True), 1822: ("Фора", "Ф2", True),
}

FACTOR_PRIORITY = {
    921: 1, 922: 1, 923: 1, 924: 1, 925: 1, 926: 1, 927: 1, 928: 1,
    930: 1, 931: 1,
    1854: 2, 1871: 2, 1873: 2, 1874: 2,
    1569: 2, 1572: 2, 1880: 2, 1881: 2,
    1883: 2, 1884: 2, 1886: 2, 1887: 2,
    1893: 2, 1894: 2, 1896: 2, 1897: 2,
    910: 1, 912: 1, 989: 1, 991: 1,
    1672: 1, 1675: 1, 1677: 1, 1678: 1, 1680: 1, 1681: 1,
    1809: 1, 1810: 1, 1812: 1, 1813: 1, 1815: 1, 1816: 1, 1818: 1, 1819: 1, 1821: 1, 1822: 1,
}

ALLOWED_LEAGUE_PATTERNS = [
    r'^россия\.\s*(премьер|рпл|фнл|первая лига|кубок)',
    r'^англия\.\s*(премьер|чемпионшип|кубок англии|fa cup|кубок лиги|efl cup|carabao)($|\s*\.|\s*сезон)',
    r'^испания\.\s*(ла лига|примера|сегунда|кубок|copa)($|\s*дивизион|\s*лига|\s*\.)',
    r'^италия\.\s*(серия а|серия b|серия б|кубок)($|\s*\.|\s*сезон)',
    r'^германия\.\s*(бундеслига|2\.\s*бундеслига|вторая бундеслига|кубок)($|\s*\.|\s*сезон)',
    r'^франция\.\s*(лига 1|лига 2|кубок)($|\s*\.|\s*сезон)',
    r'^португалия\.\s*(примейра|primeira|лига португал|кубок|taça)',
    r'^нидерланды\.\s*(эредивизи|eredivisie|кубок|knvb)',
    r'^бразилия\.\s*(серия a|серия а|кубок бразилии|copa do brasil)($|\s*\.|\s*сезон)',
    r'^украина\.\s*(премьер|упл|кубок)',
    r'^казахстан\.\s*(премьер|кубок)',
    r'^саудовская аравия\.\s*(про|премьер|кубок)',
    r'^турция\.\s*(суперлига|süper|кубок)',
    r'^сша\.\s*(mls|major league soccer)',
    r'^лига чемпионов(\s+уефа)?(\.|\s+(общий|группов|плей|финал|квалиф)|$)',
    r'^лига европы(\s+уефа)?(\.|\s+(общий|группов|плей|финал|квалиф)|$)',
    r'^лига конференций(\s+уефа)?', r'^чемпионат европы', r'^чемпионат мира',
    r'^лига наций', r'^отборочный', r'^сборная', r'^суперкубок уефа', r'^товарищеск',
]

FORBIDDEN_LEAGUE_WORDS = [
    "гандбол", "handball", "теннис", "tennis", "баскетбол", "basketball",
    "хоккей", "hockey", "волейбол", "volleyball", "регби", "rugby",
    "крикет", "cricket", "американский футбол", "american football", "бейсбол", "baseball",
    "флорбол", "floorball", "пляжный", "кибер", "esports", "e-sports", "csgo", "cs:go", "dota",
    "virtua", "виртуальн", "virtual", "лига про", "h2h", "fc 27", "fc27", "fc 26", "fc26", "fc 25", "fc25",
    "ncaa", "ncaab", "ncaaf", "жен", "women", "ladies", "fem", "vrouwen", "дамы", "девуш",
    "юнош", "юн ", "молодеж", "молодёж", "молодежн", "дубл", "reserve", "academy", "академ",
    "до 15", "до 16", "до 17", "до 18", "до 19", "до 20", "до 21", "до 23",
    "u15", "u16", "u17", "u18", "u19", "u20", "u21", "u23", "пфл", "третий дивизион",
    "третья лига", "третий див", "amateur", "любител", "лига трофи", "региональн", "regional",
    "a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8", "b1", "b2", "b3", "b4", "c1", "c2", "c3",
    "rfef", "feb", "futsal", "мини-футбол", "мини футбол", "кариока", "гойяно", "паулиста",
    "минейро", "баияно", "catarinense", "gaúcho", "gaucho", "парана", "paranaense", "cearense",
    "pernambucano", "азии", "азия", "afc", "африки", "африка", "caf", "конкакаф", "concacaf",
    "южной америки", "conmebol", "олимпийск", "olympic",
    "кто выше", "победитель", "призер", "вылет", "квалификация", "кто попадет",
    "выход в", "чемпионство", "выиграет лигу", "финал (кто", "лучший бомбардир",
    "голы за", "регулярный сезон", "плей-офф (кто",
]

BLACKLIST_WORDS = [
    "люб", "жен", "мол", "дубл", "резерв", "академ", "до 15", "до 16", "до 17", "до 18", "до 19", "до 20", "до 21", "до 23",
    "u15", "u16", "u17", "u18", "u19", "u20", "u21", "u23", " b team", " ii", " iii",
]

_FINISHED_CACHE = {"data": {}, "ts": 0}
_FULL_LINE_CACHE = {}
_SUB_CHECK_CACHE = {}
_PENDING_FINISHED = {}
_FIREBASE_BAD_CHARS = re.compile(r'[\.\$#\[\]/]')

app = FastAPI(title="WinStars API")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

http_client = httpx.AsyncClient(timeout=10.0)

class WithdrawReject(StatesGroup):
    waiting_reason = State()

@app.on_event("shutdown")
async def shutdown_event():
    await http_client.aclose()

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
    async def connect(self, websocket: WebSocket):
        await websocket.accept(); self.active_connections.append(websocket)
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections: self.active_connections.remove(websocket)
    async def broadcast(self, message: dict):
        dead = []
        for c in self.active_connections:
            try: await c.send_text(json.dumps(message))
            except: dead.append(c)
        for conn in dead: self.disconnect(conn)

manager = ConnectionManager()

async def firebase_get(path: str):
    try:
        resp = await http_client.get(f"{FIREBASE_DB_URL}/{path}.json")
        return resp.json()
    except: return None

async def firebase_put(path: str, data: dict):
    try:
        r = await http_client.put(f"{FIREBASE_DB_URL}/{path}.json", json=data, timeout=60.0)
        return r.status_code == 200
    except: return False

async def firebase_patch(path: str, data: dict):
    try:
        r = await http_client.patch(f"{FIREBASE_DB_URL}/{path}.json", json=data)
        return r.status_code == 200
    except: return False

async def firebase_delete(path: str):
    try: await http_client.delete(f"{FIREBASE_DB_URL}/{path}.json")
    except: pass

async def send_user_notification(user_id, text, button_text=None, button_url=None):
    try:
        kb = None
        if button_text and button_url:
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=button_text, web_app=WebAppInfo(url=button_url))
            ]])
        await bot.send_message(int(user_id), text, reply_markup=kb, parse_mode="HTML")
        return True
    except Exception as e:
        print(f"   notify fail {user_id}: {e}")
        return False

async def is_subscribed(user_id: int) -> bool:
    if user_id == ADMIN_TELEGRAM_ID:
        return True
    now = time.time()
    cached = _SUB_CHECK_CACHE.get(user_id)
    if cached and now - cached[0] < SUB_CHECK_TTL:
        return cached[1]
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        result = member.status in ("member", "administrator", "creator")
    except Exception as e:
        print(f"Sub check fail {user_id}: {e}")
        result = False
    _SUB_CHECK_CACHE[user_id] = (now, result)
    return result

def _find_bad_key(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str) or not k: return f"empty key at {path}"
            if _FIREBASE_BAD_CHARS.search(k): return f"bad key '{k}' at {path}"
            r = _find_bad_key(v, f"{path}/{k}")
            if r: return r
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            r = _find_bad_key(v, f"{path}[{i}]")
            if r: return r
    return None

async def _save_matches_fallback(matches_dict):
    existing = await firebase_get("matches") or {}
    if isinstance(existing, dict):
        to_delete = set(existing.keys()) - set(matches_dict.keys())
        for k in to_delete:
            try: await http_client.delete(f"{FIREBASE_DB_URL}/matches/{k}.json")
            except: pass
    for mid, mdata in matches_dict.items():
        bad = _find_bad_key(mdata, "")
        if bad: continue
        try:
            await http_client.put(f"{FIREBASE_DB_URL}/matches/{mid}.json", json=mdata, timeout=15.0)
        except: pass

async def _save_matches(matches_dict):
    if not matches_dict: return
    bad = _find_bad_key(matches_dict, "")
    if bad:
        await _save_matches_fallback(matches_dict)
        return
    try:
        payload = json.dumps(matches_dict, allow_nan=False, ensure_ascii=False)
        size = len(payload)
    except ValueError: return
    if size < 900_000:
        try:
            r = await http_client.put(
                f"{FIREBASE_DB_URL}/matches.json", content=payload,
                headers={"Content-Type": "application/json"}, timeout=45.0
            )
            if r.status_code != 200:
                await _save_matches_fallback(matches_dict)
        except:
            await _save_matches_fallback(matches_dict)
    else:
        await _save_matches_fallback(matches_dict)

async def _record_finished(mid, s1, s2, source, retries=3):
    for attempt in range(retries):
        try:
            r = await http_client.patch(
                f"{FIREBASE_DB_URL}/finished_matches/{mid}.json",
                json={"score1": s1, "score2": s2, "source": source},
                timeout=10.0,
            )
            if r.status_code == 200: return True
        except Exception: pass
        await asyncio.sleep(1)
    return False

async def get_finished_cached():
    now = time.time()
    if now - _FINISHED_CACHE["ts"] < FINISHED_TTL and _FINISHED_CACHE["data"]:
        return _FINISHED_CACHE["data"]
    raw = await firebase_get("finished_matches") or {}
    if not isinstance(raw, dict): raw = {}
    _FINISHED_CACHE["data"] = raw
    _FINISHED_CACHE["ts"] = now
    return raw

def _is_allowed_league(league):
    if not league or league == "Футбол": return False
    low = league.lower().strip()
    for f in FORBIDDEN_LEAGUE_WORDS:
        if f in low: return False
    for pattern in ALLOWED_LEAGUE_PATTERNS:
        try:
            if re.search(pattern, low): return True
        except: pass
    return False

def _parse_start_ts(ev):
    st = ev.get("startTime")
    if st is None: return 0
    try: return int(st)
    except: return 0

def _extract_score(li):
    if not li: return None, None, None
    timer_str = li.get("timer")
    scores = li.get("scores")
    s1 = s2 = None
    if isinstance(scores, list):
        for block in scores:
            if isinstance(block, list) and block:
                first = block[0]
                if isinstance(first, dict):
                    c1 = first.get("c1"); c2 = first.get("c2")
                    if c1 is not None and c2 is not None:
                        try: s1 = int(c1); s2 = int(c2); break
                        except: pass
    if s1 is None:
        c1 = li.get("c1"); c2 = li.get("c2")
        if c1 is not None and c2 is not None:
            try: s1 = int(c1); s2 = int(c2)
            except: pass
    minute = None
    if timer_str and isinstance(timer_str, str) and ":" in timer_str:
        try: minute = int(timer_str.split(":")[0])
        except: pass
    return s1, s2, minute

def _extract_score_breakdown(li):
    if not li: return []
    scores = li.get("scores")
    if not isinstance(scores, list): return []
    result = []
    for idx, block in enumerate(scores):
        if isinstance(block, list) and block:
            for item in block:
                if isinstance(item, dict):
                    c1 = item.get("c1"); c2 = item.get("c2")
                    if c1 is not None and c2 is not None:
                        try:
                            period = "first" if idx == 0 else ("second" if idx == 1 else f"period_{idx}")
                            result.append({"period": period, "s1": int(c1), "s2": int(c2)})
                        except: pass
                        break
    return result

def _normalize_p(p_val):
    try: num = float(p_val)
    except (TypeError, ValueError): return 0.0
    if num != int(num): return num
    if abs(num) >= 10: return num / 100.0
    return num

def _extract_number_from_name(name):
    m = re.search(r'([+-]?\d+(?:\.\d+)?)', name)
    if m: return float(m.group(1))
    return 999.0

def _fix_market_orientation(markets):
    outcomes = markets.get("Тотал")
    if not outcomes: return
    tb_items = []; tm_items = []
    for o in outcomes:
        n = o["name"]
        try:
            if n.startswith("ТБ "): tb_items.append((float(n[3:]), o))
            elif n.startswith("ТМ "): tm_items.append((float(n[3:]), o))
        except (ValueError, IndexError): continue
    if tb_items and not tm_items and len(tb_items) >= 3:
        tb_items.sort(key=lambda x: x[0])
        violations = sum(1 for i in range(1, len(tb_items)) if tb_items[i][1]["odds"] < tb_items[i-1][1]["odds"])
        if violations >= len(tb_items) - 2:
            for lim, o in tb_items: o["name"] = f"ТМ {lim:g}"
            return
    if tm_items and not tb_items and len(tm_items) >= 3:
        tm_items.sort(key=lambda x: x[0])
        violations = sum(1 for i in range(1, len(tm_items)) if tm_items[i][1]["odds"] > tm_items[i-1][1]["odds"])
        if violations >= len(tm_items) - 2:
            for lim, o in tm_items: o["name"] = f"ТБ {lim:g}"
            return

def _check_total_consistency(markets):
    main_total = None
    for o in markets.get("Тотал", []):
        if o["name"].startswith("ТБ ") and o.get("_fid") == 930:
            try: main_total = (float(o["name"][3:]), o["odds"])
            except: pass
    if main_total is None:
        return {g: outs for g, outs in markets.items()}, {}
    main_limit, main_tb_odds = main_total
    confirmed = {}; suspect = {}
    for g, outs in markets.items():
        if g != "Тотал":
            confirmed[g] = outs; continue
        conf_outs = []; susp_outs = []
        for o in outs:
            if o.get("_fid") in (930, 931):
                conf_outs.append(o); continue
            if o["name"].startswith("ТБ "):
                try: lim = float(o["name"][3:])
                except: susp_outs.append(o); continue
                if lim < main_limit: exp = 1.0 + (main_tb_odds - 1.0) * (main_limit - lim) * 2
                else: exp = 1.0 + (main_tb_odds - 1.0) * (lim - main_limit) * 0.5
                if exp < 1.0: exp = 1.0
                if o["odds"] < exp * 0.7 or o["odds"] > exp * 1.3: susp_outs.append(o)
                else: conf_outs.append(o)
            elif o["name"].startswith("ТМ "):
                try: lim = float(o["name"][3:])
                except: susp_outs.append(o); continue
                if lim < main_limit: exp = 1.0 + (1.0 / main_tb_odds) * (main_limit - lim) * 2
                else: exp = 1.0 + (1.0 / main_tb_odds) * (lim - main_limit) * 0.5
                if exp < 1.0: exp = 1.0
                if o["odds"] < exp * 0.7 or o["odds"] > exp * 1.3: susp_outs.append(o)
                else: conf_outs.append(o)
            else: susp_outs.append(o)
        if conf_outs: confirmed[g] = conf_outs
        if susp_outs: suspect["Тотал (доп.)"] = susp_outs
    return confirmed, suspect

def _parse_markets(factors_list):
    best = {}
    for f in factors_list:
        fid = f.get("f")
        if fid not in FONBET_FACTORS: continue
        group, base, has_param = FONBET_FACTORS[fid]
        odds = f.get("v")
        if odds is None: continue
        try: odds = float(odds)
        except: continue
        if odds <= 1.0: continue
        name = base
        if has_param:
            p = f.get("p")
            if p is None: continue
            try: p_raw = float(p)
            except: continue
            p_val = _normalize_p(p_raw)
            if p_val == 0 and group == "Тотал": continue
            if group == "Тотал": name = f"{base} {abs(p_val):g}"
            else:
                if p_val == int(p_val): p_str = str(int(p_val))
                else: p_str = f"{p_val:g}"
                if p_val > 0: p_str = f"+{p_str}"
                name = f"{base} {p_str}"
        key = (group, name)
        prio = FACTOR_PRIORITY.get(fid, 50)
        prev = best.get(key)
        if prev is None or prio < prev["prio"]:
            best[key] = {"prio": prio, "odds": odds, "fid": fid}
    markets = {}
    for (group, name), data in best.items():
        markets.setdefault(group, []).append({"name": name, "odds": data["odds"], "_fid": data["fid"]})
    _fix_market_orientation(markets)
    confirmed, suspect = _check_total_consistency(markets)
    markets = confirmed
    for g, outs in suspect.items(): markets[g] = outs
    for g in markets:
        markets[g].sort(key=lambda x: _extract_number_from_name(x["name"]))
        for o in markets[g]: o.pop("_fid", None)
    return markets

def _filter_total_group(outs, current_score):
    new_outs = []
    for o in outs:
        n = o["name"]
        if n.startswith("ТБ "):
            try: lim = float(n[3:])
            except: new_outs.append(o); continue
            if current_score > lim: continue
        elif n.startswith("ТМ "):
            try: lim = float(n[3:])
            except: new_outs.append(o); continue
            if current_score >= lim: continue
        new_outs.append(o)
    return new_outs

def _filter_decided(markets, s1, s2):
    if s1 is None or s2 is None: return markets
    filtered = {}
    for g, outs in markets.items():
        if g == "Обе забьют":
            if s1 > 0 and s2 > 0: continue
            filtered[g] = outs
        elif g in ("Тотал", "Тотал (доп.)"):
            new_outs = _filter_total_group(outs, s1 + s2)
            if new_outs: filtered[g] = new_outs
        else: filtered[g] = outs
    return filtered

def _fetch_fonbet(url):
    for _ in range(2):
        try:
            r = cffi_requests.get(url, headers=FONBET_HEADERS, impersonate="chrome120", timeout=30)
            if r.status_code == 200:
                try: return r.json()
                except: return None
        except: time.sleep(1)
    return None

def _fetch_full_line_sync(ev_id):
    url = f"{FONBET_BASE}{FONBET_FULL_LINE_PATH}&eventId={ev_id}"
    data = _fetch_fonbet(url)
    if not data: return []
    for cf in data.get("customFactors", []):
        if cf.get("e") == ev_id: return cf.get("factors", [])
    return []

async def _fetch_full_line(ev_id):
    now = time.time()
    cached = _FULL_LINE_CACHE.get(ev_id)
    if cached and now - cached["ts"] < FULL_LINE_TTL: return cached["factors"]
    factors = await asyncio.to_thread(_fetch_full_line_sync, ev_id)
    if factors:
        _FULL_LINE_CACHE[ev_id] = {"factors": factors, "ts": now}
        return factors
    if cached: return cached["factors"]
    return []

def _build_logo_index(raw_cache):
    idx = {}
    if not isinstance(raw_cache, dict): return idx
    for k, v in raw_cache.items():
        if not v: continue
        real_k = k
        if k.startswith("b64_"):
            try:
                pad = "=" * (-len(k[4:]) % 4)
                real_k = base64.urlsafe_b64decode(k[4:] + pad).decode("utf-8")
            except: continue
        team = real_k.split("|")[0] if "|" in real_k else real_k
        if team not in idx: idx[team] = v
    return idx

def _build_sport_hierarchy(all_sports):
    entry_by_id = {}; parent_by_id = {}
    for sp in all_sports:
        sp_id = sp.get('id')
        if sp_id is None: continue
        entry_by_id[sp_id] = sp
        parent = None
        for field in ('parentId', 'parent_id', 'sportId', 'sport_id', 'parent', 'rootId', 'root_id'):
            v = sp.get(field)
            if v is not None and v != sp_id:
                parent = v; break
        if parent is not None: parent_by_id[sp_id] = parent
    def find_top(entry_id):
        cur = entry_id; visited = set()
        for _ in range(20):
            if cur in visited: return None
            visited.add(cur)
            entry = entry_by_id.get(cur)
            if not entry: return None
            if entry.get('kind') == 'sport': return entry
            p = parent_by_id.get(cur)
            if p is None: return None
            cur = p
        return None
    top_by_id = {}
    for sp_id in entry_by_id: top_by_id[sp_id] = find_top(sp_id)
    return entry_by_id, parent_by_id, top_by_id

async def fonbet_scraper_worker():
    print(f"fonbet_scraper_worker started (poll {LIVE_POLL_SEC}s)...")
    cycle = 0
    prev_event_ids = set()
    while True:
        try:
            t0 = time.time()
            now_ts = int(time.time())
            seven_days = 7 * 86400
            line_data = _fetch_fonbet(FONBET_BASE + FONBET_LINE_PATH)
            live_data = _fetch_fonbet(FONBET_BASE + FONBET_LIVE_PATH)
            if not line_data and not live_data:
                await asyncio.sleep(LIVE_POLL_SEC); continue
            events_by_id = {}
            all_factors = {}
            all_sports = []
            all_live = {}
            for data in [line_data, live_data]:
                if not data: continue
                events = data.get("events", [])
                factors_raw = data.get("factors", [])
                custom_factors_raw = data.get("customFactors", [])
                sps = data.get("sports", [])
                live_infos = data.get("liveEventInfos", [])
                all_sports.extend(sps)
                for li in live_infos:
                    eid = li.get("eventId")
                    if eid: all_live[eid] = li
                for f in factors_raw:
                    eid = f.get("e")
                    if eid: all_factors.setdefault(eid, []).append(f)
                for cf in custom_factors_raw:
                    eid = cf.get("e")
                    if eid: all_factors.setdefault(eid, []).extend(cf.get("factors", []))
                for ev in events:
                    eid = ev.get("id")
                    if eid and eid not in events_by_id:
                        events_by_id[eid] = dict(ev)
            entry_by_id, parent_by_id, top_by_id = _build_sport_hierarchy(all_sports)
            tournament_by_id = {}
            for sp in all_sports:
                if sp.get('kind') == 'sport': continue
                sid = sp.get('id'); nm = sp.get('name', '')
                if sid and nm: tournament_by_id[sid] = nm
            raw_cache = await firebase_get("team_logos") or {}
            logo_idx = _build_logo_index(raw_cache)
            cached_prev_matches = await firebase_get("matches") or {}
            if not isinstance(cached_prev_matches, dict):
                cached_prev_matches = {}
            matches_dict = {}
            current_ids = set()
            live_cnt = 0
            skipped_stuck = 0
            skipped_started_no_data = 0
            cached_frozen = 0
            total_markets_count = 0
            full_line_fetched = 0
            candidates = []
            for ev in events_by_id.values():
                if ev.get("rootKind") != 1 or ev.get("kind") != 1: continue
                t1 = ev.get("team1", ""); t2 = ev.get("team2", "")
                if not t1 or not t2: continue
                st = _parse_start_ts(ev)
                if st <= 0: continue
                sid = ev.get("sportId")
                top_sport = top_by_id.get(sid)
                if not top_sport: continue
                sport_name = (top_sport.get('name') or '').lower()
                if sport_name not in ('футбол', 'football', 'soccer'): continue
                league = tournament_by_id.get(sid, "")
                text = f"{t1} {t2}".lower()
                if any(w in text for w in BLACKLIST_WORDS): continue
                if not _is_allowed_league(league): continue
                candidates.append((ev, league))
            candidates.sort(key=lambda x: (0 if x[0].get("id") in all_live else 1, abs(_parse_start_ts(x[0]) - now_ts)))
            for idx, (ev, league) in enumerate(candidates):
                eid = ev.get("id")
                mid = f"fonbet_{eid}"
                t1 = ev.get("team1", ""); t2 = ev.get("team2", "")
                st = _parse_start_ts(ev)
                s1, s2, timer = _extract_score(all_live.get(eid))
                is_live = s1 is not None and s2 is not None and timer is not None
                if is_live and timer is not None and isinstance(timer, int):
                    elapsed_real_min = (now_ts - st) / 60.0
                    if elapsed_real_min > timer + 45:
                        skipped_stuck += 1
                        continue
                if not is_live:
                    if st < now_ts - int(3.5 * 3600): continue
                    if st > now_ts + seven_days: continue
                    if st < now_ts:
                        old_m = cached_prev_matches.get(mid)
                        if isinstance(old_m, dict):
                            old_status = old_m.get("status", "")
                            was_live = old_status.startswith("LIVE") or old_status == "ИДЕТ"
                            hours_since = (now_ts - st) / 3600.0
                            if was_live and hours_since < 2.5:
                                cached_markets = old_m.get("markets", {}) or {}
                                if isinstance(cached_markets, dict):
                                    for grp in cached_markets.values():
                                        if isinstance(grp, list): total_markets_count += len(grp)
                                matches_dict[mid] = {
                                    "title": old_m.get("title", f"{t1} - {t2}"),
                                    "home": t1, "away": t2,
                                    "homeLogo": old_m.get("homeLogo", logo_idx.get(t1, "")),
                                    "awayLogo": old_m.get("awayLogo", logo_idx.get(t2, "")),
                                    "league": league, "status": "ИДЕТ",
                                    "score": old_m.get("score", "VS"), "start_time": st,
                                    "odds": old_m.get("odds", {"1": None, "X": None, "2": None}),
                                    "markets": cached_markets,
                                    "score_breakdown": old_m.get("score_breakdown", []),
                                }
                                current_ids.add(mid)
                                cached_frozen += 1
                                continue
                        skipped_started_no_data += 1
                        continue
                current_ids.add(mid)
                factors_for_ev = None
                if idx < FULL_LINE_MAX_PER_CYCLE:
                    full_factors = await _fetch_full_line(eid)
                    if full_factors:
                        factors_for_ev = full_factors
                        full_line_fetched += 1
                if not factors_for_ev:
                    factors_for_ev = all_factors.get(eid, [])
                markets_data = _parse_markets(factors_for_ev)
                if is_live and s1 is not None and s2 is not None:
                    markets_data = _filter_decided(markets_data, s1, s2)
                for grp in markets_data.values():
                    total_markets_count += len(grp)
                p1 = px = p2 = None
                if "Исход" in markets_data:
                    for o in markets_data["Исход"]:
                        if o["name"] == "П1": p1 = o["odds"]
                        elif o["name"] == "Х": px = o["odds"]
                        elif o["name"] == "П2": p2 = o["odds"]
                score = "VS"
                score_breakdown = []
                if is_live:
                    if s1 is not None and s2 is not None: score = f"{s1} : {s2}"
                    status_str = f"LIVE {timer}'" if timer is not None else "LIVE"
                    live_cnt += 1
                    score_breakdown = _extract_score_breakdown(all_live.get(eid))
                else:
                    status_str = time.strftime("%d.%m %H:%M", time.localtime(st))
                matches_dict[mid] = {
                    "title": f"{t1} - {t2}", "home": t1, "away": t2,
                    "homeLogo": logo_idx.get(t1, ""), "awayLogo": logo_idx.get(t2, ""),
                    "league": league, "status": status_str, "score": score,
                    "start_time": st,
                    "odds": {"1": p1, "X": px, "2": p2},
                    "markets": markets_data,
                    "score_breakdown": score_breakdown,
                }
            disappeared = prev_event_ids - current_ids
            if disappeared:
                cached_old = await firebase_get("matches") or {}
                finished_known = await firebase_get("finished_matches") or {}
                for mid in disappeared:
                    if mid in finished_known: continue
                    old_m = cached_old.get(mid)
                    if not isinstance(old_m, dict): continue
                    score_str = old_m.get("score", "")
                    if ":" in score_str:
                        try:
                            s1_old, s2_old = [int(x.strip()) for x in score_str.split(":")]
                            ok = await _record_finished(mid, s1_old, s2_old, "fonbet_last")
                            if not ok:
                                _PENDING_FINISHED[mid] = {"score1": s1_old, "score2": s2_old, "source": "fonbet_last"}
                        except: pass
                    # Если счёта не было (VS), значит матч даже не начался. НЕ пишем 0:0! Просто игнорируем.
            for mid in list(_PENDING_FINISHED.keys()):
                d = _PENDING_FINISHED[mid]
                if await _record_finished(mid, d["score1"], d["score2"], d["source"]):
                    _PENDING_FINISHED.pop(mid, None)
            prev_event_ids = current_ids
            if len(_FULL_LINE_CACHE) > 500:
                cutoff = now_ts - 3600
                for _eid in list(_FULL_LINE_CACHE.keys()):
                    if _FULL_LINE_CACHE[_eid]["ts"] < cutoff:
                        _FULL_LINE_CACHE.pop(_eid, None)
            if matches_dict:
                try:
                    approx_size = len(json.dumps(matches_dict, ensure_ascii=False))
                    size_mb = approx_size / 1024 / 1024
                except: size_mb = 0
                await _save_matches(matches_dict)
                print(f"[FONBET] {len(matches_dict)} matches (LIVE: {live_cnt}) | {total_markets_count} mkts | {time.time()-t0:.1f}s")
        except Exception as e:
            import traceback
            traceback.print_exc()
        cycle += 1
        if cycle % 15 == 0: gc.collect()
        await asyncio.sleep(LIVE_POLL_SEC)

def calculate_outcome(outcome_str, s1, s2, group_name=""):
    o = outcome_str.lower().strip()
    if o in ["п1", "1", "победа 1", "победа хозяев"]: return s1 > s2
    if o in ["х", "x", "ничья"]: return s1 == s2
    if o in ["п2", "2", "победа 2", "победа гостей"]: return s2 > s1
    if o in ["1x", "1х"]: return s1 >= s2
    if o in ["12"]: return s1 != s2
    if o in ["x2", "х2"]: return s2 >= s1
    if o in ["да", "обе забьют", "оз да"]: return s1 > 0 and s2 > 0
    if o in ["нет", "оз нет"]: return s1 == 0 or s2 == 0
    total = s1 + s2
    m_over = re.search(r'(?:тб|тотал больше|больше)\s*([+-]?\d+(?:\.\d+)?)', o)
    if m_over:
        limit = float(m_over.group(1))
        if total > limit: return True
        if total < limit: return False
        return None
    m_under = re.search(r'(?:тм|тотал меньше|меньше)\s*([+-]?\d+(?:\.\d+)?)', o)
    if m_under:
        limit = float(m_under.group(1))
        if total < limit: return True
        if total > limit: return False
        return None
    m_f1 = re.search(r'(?:ф1|фора 1|фора хозяев)\s*([+-]?\d+(?:\.\d+)?)', o)
    if m_f1:
        val = float(m_f1.group(1)); res = s1 + val - s2
        if res > 0: return True
        if res < 0: return False
        return None
    m_f2 = re.search(r'(?:ф2|фора 2|фора гостей)\s*([+-]?\d+(?:\.\d+)?)', o)
    if m_f2:
        val = float(m_f2.group(1)); res = s2 + val - s1
        if res > 0: return True
        if res < 0: return False
        return None
    return False

async def settle_bets_worker():
    print("settle_bets_worker started...")
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
                        all_finished = True; results = []
                        for event in events:
                            if not isinstance(event, dict): all_finished = False; break
                            m_id = event.get("match_id")
                            if m_id not in finished: all_finished = False; break
                            res = finished[m_id]
                            if not isinstance(res, dict): all_finished = False; break
                            results.append(res)
                        if not all_finished or not results: continue
                        user_id = bet.get("user_id")
                        amount = bet.get("amount", 0)
                        bet_type = bet.get("type", "single")
                        
                        if any(r.get("void") for r in results):
                            user = await firebase_get(f"users/{user_id}")
                            if isinstance(user, dict):
                                await firebase_patch(f"users/{user_id}", {
                                    "balance": user.get("balance", 0) + amount,
                                    "total_bets": max(0, user.get("total_bets", 1) - 1),
                                })
                            await firebase_patch(f"bets/{bet_id}", {"status": "void"})
                            if user_id: await firebase_patch(f"users/{user_id}/bets/{bet_id}", {"status": "void"})
                            await firebase_delete(f"active_bets/{bet_id}")
                            try:
                                await send_user_notification(user_id,
                                    f"<b>Возврат ставки</b>\n\nСумма <b>{amount} ★</b> возвращена на баланс.",
                                    "Мои прогнозы", WEBAPP_URL)
                            except: pass
                            continue
                        
                        is_lost = False
                        is_all_void = True
                        eff_coeff = 1.0
                        leg_results = []
                        for i, event in enumerate(events):
                            res = results[i]
                            s1 = res.get("score1"); s2 = res.get("score2")
                            oc = event.get("outcome_key") or event.get("outcome", "")
                            gn = event.get("group_name", "")
                            match_title = event.get("match_title", "")
                            outcome_label = event.get("outcome", "")
                            coeff = float(event.get("coefficient", 1.0))
                            
                            leg_status = "void"
                            if s1 is not None and s2 is not None:
                                if gn == "Тотал (доп.)":
                                    leg_status = "void"
                                else:
                                    ws = calculate_outcome(oc, s1, s2, gn)
                                    if ws is True: leg_status = "won"
                                    elif ws is False: leg_status = "lost"
                                    else: leg_status = "void"
                            
                            leg_results.append({
                                "match_title": match_title,
                                "outcome": outcome_label,
                                "group_name": gn,
                                "coefficient": coeff,
                                "status": leg_status,
                                "score1": s1, "score2": s2,
                            })
                            
                            if leg_status == "lost": is_lost = True
                            elif leg_status == "won":
                                is_all_void = False
                                eff_coeff *= coeff
                        
                        if is_lost:
                            status = "lost"
                            # Вычитаем проигрыш из профита!
                            user = await firebase_get(f"users/{user_id}")
                            if isinstance(user, dict):
                                await firebase_patch(f"users/{user_id}", {
                                    "profit": user.get("profit", 0) - amount
                                })
                        elif is_all_void:
                            status = "void"
                            user = await firebase_get(f"users/{user_id}")
                            if isinstance(user, dict):
                                await firebase_patch(f"users/{user_id}", {
                                    "balance": user.get("balance", 0) + amount,
                                    "total_bets": max(0, user.get("total_bets", 1) - 1),
                                })
                        else:
                            status = "won"
                            user = await firebase_get(f"users/{user_id}")
                            if isinstance(user, dict):
                                win_amount = amount * eff_coeff
                                await firebase_patch(f"users/{user_id}", {
                                    "balance": user.get("balance", 0) + win_amount,
                                    "profit": user.get("profit", 0) + (win_amount - amount),
                                    "wins": user.get("wins", 0) + 1,
                                })
                        
                        await firebase_patch(f"bets/{bet_id}", {
                            "status": status,
                            "effective_coefficient": round(eff_coeff, 2),
                            "leg_results": leg_results,
                        })
                        if user_id:
                            await firebase_patch(f"users/{user_id}/bets/{bet_id}", {
                                "status": status,
                                "effective_coefficient": round(eff_coeff, 2),
                                "leg_results": leg_results,
                            })
                        await firebase_delete(f"active_bets/{bet_id}")
                        
                        try:
                            if status == "won":
                                win_amount = round(amount * eff_coeff, 2)
                                await send_user_notification(user_id,
                                    f"<b>Ставка выиграла!</b>\n\nВыплата: <b>+{win_amount} ★</b>",
                                    "Забрать приз", WEBAPP_URL)
                            elif status == "lost":
                                await send_user_notification(user_id,
                                    f"<b>Ставка не сыграла</b>\n\nПопробуйте ещё раз!",
                                    "Попробовать снова", WEBAPP_URL)
                            elif status == "void":
                                await send_user_notification(user_id,
                                    f"<b>Возврат ставки</b>\n\nСумма <b>{amount} ★</b> возвращена.",
                                    "Мои прогнозы", WEBAPP_URL)
                        except Exception as ne: print(f"notify bet fail: {ne}")
                    except Exception as e: print(f"bet {bet_id} error: {e}")
        except Exception as e: print(f"settle_bets: {e}")
        cycle += 1
        if cycle % 15 == 0: gc.collect()
        await asyncio.sleep(SETTLE_POLL_SEC)

async def notify_start_worker():
    print("notify_start_worker started...")
    while True:
        try:
            active_bets = await firebase_get("active_bets") or {}
            if not isinstance(active_bets, dict) or not active_bets:
                await asyncio.sleep(NOTIFY_POLL_SEC); continue
            matches = await firebase_get("matches") or {}
            now = int(time.time())
            for bet_id, bet in list(active_bets.items()):
                if not isinstance(bet, dict): continue
                user_id = bet.get("user_id")
                events = bet.get("events", [])
                if not isinstance(events, list): continue
                notified = bet.get("start_notified_matches") or {}
                if not isinstance(notified, dict): notified = {}
                to_notify = []
                for event in events:
                    mid = event.get("match_id")
                    if not mid or notified.get(mid): continue
                    match = matches.get(mid) if isinstance(matches, dict) else None
                    if not isinstance(match, dict): continue
                    st = match.get("start_time", 0)
                    if 0 <= st - now <= 600:
                        to_notify.append((mid, match))
                if to_notify:
                    text = "<b>Матч скоро начнётся</b>\n\n"
                    for mid, m in to_notify[:3]:
                        t = time.strftime("%H:%M", time.localtime(m.get("start_time", 0)))
                        text += f"• {m.get('title', '')} — {t}\n"
                    text += "\nПроверьте свои прогнозы."
                    await send_user_notification(user_id, text, "Мои прогнозы", WEBAPP_URL)
                    new_notified = dict(notified)
                    for mid, _ in to_notify: new_notified[mid] = True
                    await firebase_patch(f"active_bets/{bet_id}", {"start_notified_matches": new_notified})
        except Exception as e: print(f"notify_start: {e}")
        await asyncio.sleep(NOTIFY_POLL_SEC)

async def _notify_admin_withdrawal(req_id, data):
    text = (
        f"<b>Новая заявка на вывод</b>\n\n"
        f"Игрок: {data.get('first_name','')} (@{data.get('username','')})\n"
        f"ID: <code>{data.get('user_id')}</code>\n"
        f"Сумма: <b>{data.get('amount')} ★</b>\n"
        f"К зачислению: <b>{data.get('amount_after_commission')} ★</b> (комиссия 5%)\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Одобрить", callback_data=f"wd_approve:{req_id}"),
        InlineKeyboardButton(text="Отклонить", callback_data=f"wd_reject:{req_id}"),
    ]])
    try: await bot.send_message(ADMIN_TELEGRAM_ID, text, reply_markup=kb, parse_mode="HTML")
    except: pass

@app.on_event("startup")
async def startup_event():
    print("WinStars API starting... VERSION: v8 (fix 0:0 bug, negative profit, promo modal)")
    asyncio.create_task(dp.start_polling(bot))
    asyncio.create_task(fonbet_scraper_worker())
    asyncio.create_task(settle_bets_worker())
    asyncio.create_task(notify_start_worker())
    print("All workers started")

# ==================== BOT HANDLERS ====================

async def _register_user_if_new(user_id, from_user, referrer_id=None):
    existing = await firebase_get(f"users/{str(user_id)}")
    if isinstance(existing, dict): return False
    pending_ref = await firebase_get(f"pending_referrals/{user_id}")
    actual_referrer = None
    if referrer_id and referrer_id != str(user_id): actual_referrer = referrer_id
    elif isinstance(pending_ref, dict):
        rid = pending_ref.get("referrer_id")
        if rid and rid != str(user_id): actual_referrer = rid
    await firebase_put(f"users/{str(user_id)}", {
        "user_id": str(user_id), "username": from_user.username or "user",
        "first_name": from_user.first_name or "Игрок",
        "photo_url": from_user.photo_url or f"https://api.dicebear.com/7.x/bottts/svg?seed={user_id}",
        "balance": 0, "frozen_balance": 0, "total_bets": 0, "wins": 0, "profit": 0,
        "referral_code": str(user_id), "referred_by": actual_referrer,
        "referral_count": 0, "subscribed": True, "agreed_terms": False, "agreed_at": None,
        "pending_bonus": None, "created_at": int(time.time()),
    })
    if actual_referrer:
        referrer = await firebase_get(f"users/{actual_referrer}")
        if isinstance(referrer, dict):
            cnt = referrer.get("referral_count", 0) + 1
            await firebase_patch(f"users/{actual_referrer}", {"referral_count": cnt})
    await firebase_delete(f"pending_referrals/{user_id}")
    return True

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    _SUB_CHECK_CACHE.pop(user_id, None)
    args = message.text.split()
    referrer_id = None
    if len(args) > 1 and args[1].startswith("ref_"): referrer_id = args[1][4:]
    subscribed = await is_subscribed(user_id)
    existing = await firebase_get(f"users/{str(user_id)}")
    if not existing and referrer_id and referrer_id != str(user_id):
        await firebase_put(f"pending_referrals/{user_id}", {"referrer_id": referrer_id})
    if not subscribed:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Подписаться на канал", url=f"https://t.me/{CHANNEL_USERNAME}")],
            [InlineKeyboardButton(text="Проверить подписку", callback_data="check_sub")],
        ])
        await message.answer(f"Добро пожаловать!\n\nПодпишитесь на @{CHANNEL_USERNAME}.", reply_markup=kb, parse_mode="HTML")
        return
    await _register_user_if_new(user_id, message.from_user, referrer_id)
    user = await firebase_get(f"users/{str(user_id)}")
    if isinstance(user, dict) and not user.get("agreed_terms"):
        await message.answer(TERMS_TEXT, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Принимаю условия", callback_data="agree_terms")
        ]]), parse_mode="HTML")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Открыть WinStars", web_app=WebAppInfo(url=WEBAPP_URL))
    ]])
    await message.answer("Добро пожаловать в <b>WinStars</b>!", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "check_sub")
async def on_check_sub(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    _SUB_CHECK_CACHE.pop(user_id, None)
    subscribed = await is_subscribed(user_id)
    if not subscribed:
        await callback.answer("Подписка не обнаружена", show_alert=True); return
    await _register_user_if_new(user_id, callback.from_user)
    user = await firebase_get(f"users/{str(user_id)}")
    if isinstance(user, dict) and not user.get("agreed_terms"):
        try:
            await callback.message.edit_text(TERMS_TEXT, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Принимаю условия", callback_data="agree_terms")
            ]]), parse_mode="HTML")
        except: pass
        await callback.answer(); return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Открыть WinStars", web_app=WebAppInfo(url=WEBAPP_URL))
    ]])
    try: await callback.message.edit_text("Спасибо за подписку!", reply_markup=kb, parse_mode="HTML")
    except: pass
    await callback.answer("Подписка подтверждена")

@dp.callback_query(F.data == "agree_terms")
async def on_agree_terms(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    await firebase_patch(f"users/{str(user_id)}", {"agreed_terms": True, "agreed_at": int(time.time())})
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Открыть WinStars", web_app=WebAppInfo(url=WEBAPP_URL))
    ]])
    try: await callback.message.edit_text("Спасибо! Теперь можно пользоваться сервисом.", reply_markup=kb)
    except: pass
    await callback.answer("Согласие принято")

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id != ADMIN_TELEGRAM_ID: return
    reqs = await firebase_get("withdrawal_requests") or {}
    if not isinstance(reqs, dict): return
    pending = [(rid, r) for rid, r in reqs.items() if isinstance(r, dict) and r.get("status") == "pending"]
    pending.sort(key=lambda x: x[1].get("created_at", 0), reverse=True)
    if not pending:
        await message.answer("Нет активных заявок"); return
    lines = ["<b>Активные заявки:</b>\n"]
    for rid, r in pending[:20]:
        lines.append(f"#{rid}\n{r.get('first_name')} (@{r.get('username')}) | ID: <code>{r.get('user_id')}</code>\n{r.get('amount')} ★\n")
    rows = [[InlineKeyboardButton(text=f"OK #{rid}", callback_data=f"wd_approve:{rid}"), InlineKeyboardButton(text=f"NO #{rid}", callback_data=f"wd_reject:{rid}")] for rid, _ in pending[:10]]
    await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

@dp.message(Command("promo_create"))
async def cmd_promo_create(message: types.Message):
    if message.from_user.id != ADMIN_TELEGRAM_ID: return
    parts = message.text.split()
    if len(parts) < 6:
        await message.answer("/promo_create CODE fixed 50 100 30\n/promo_create CODE bonus 10 100 30 50\n(Опционально new в конце)", parse_mode="HTML")
        return
    code = parts[1].upper(); ptype = parts[2]
    if ptype not in ("fixed", "bonus"): return
    try: val = int(parts[3]); max_uses = int(parts[4]); days = int(parts[5])
    except: return
    extras = parts[6:] if len(parts) > 6 else []
    min_dep = 0; new_only = False
    for e in extras:
        if e == "new": new_only = True
        elif e.isdigit(): min_dep = int(e)
    expires_at = int(time.time()) + days * 86400
    data = {"code": code, "type": "fixed_stars" if ptype == "fixed" else "deposit_percent", "max_uses": max_uses, "used_count": 0, "expires_at": expires_at, "new_users_only": new_only, "created_at": int(time.time())}
    if ptype == "fixed": data["reward_stars"] = val
    else:
        data["deposit_percent"] = val
        data["min_deposit"] = min_dep
    await firebase_put(f"promo_codes/{code}", data)
    await message.answer(f"Промокод {code} создан.")

@dp.message(Command("promo_list"))
async def cmd_promo_list(message: types.Message):
    if message.from_user.id != ADMIN_TELEGRAM_ID: return
    promos = await firebase_get("promo_codes") or {}
    if not isinstance(promos, dict) or not promos: return
    lines = ["<b>Промокоды:</b>\n"]
    for code, p in list(promos.items())[:30]:
        ptype = p.get("type", "fixed_stars")
        val = p.get("reward_stars") if ptype == "fixed_stars" else p.get("deposit_percent")
        used = p.get("used_count", 0); max_u = p.get("max_uses", 0) or "∞"
        status = "истёк" if p.get("expires_at", 0) and p.get("expires_at", 0) < int(time.time()) else "активен"
        lines.append(f"<code>{code}</code> — {val}{'★' if ptype=='fixed_stars' else '%'} | {used}/{max_u} | {status}")
    await message.answer("\n".join(lines), parse_mode="HTML")

@dp.message(Command("promo_delete"))
async def cmd_promo_delete(message: types.Message):
    if message.from_user.id != ADMIN_TELEGRAM_ID: return
    parts = message.text.split()
    if len(parts) < 2: return
    await firebase_delete(f"promo_codes/{parts[1].upper()}")
    await message.answer(f"Промокод {parts[1].upper()} удалён")

@dp.callback_query(F.data.startswith("wd_approve:"))
async def on_wd_approve(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_TELEGRAM_ID: return
    req_id = callback.data.split(":", 1)[1]
    req = await firebase_get(f"withdrawal_requests/{req_id}")
    if not isinstance(req, dict) or req.get("status") != "pending": return
    user_id = req.get("user_id"); amount = req.get("amount", 0)
    await firebase_patch(f"withdrawal_requests/{req_id}", {"status": "approved", "reviewed_at": int(time.time()), "reviewed_by": callback.from_user.id})
    user = await firebase_get(f"users/{user_id}")
    if isinstance(user, dict):
        await firebase_patch(f"users/{user_id}", {"frozen_balance": max(0, user.get("frozen_balance", 0) - amount)})
    txs = await firebase_get(f"transactions/{user_id}") or {}
    if isinstance(txs, dict):
        for tx_id, tx in txs.items():
            if isinstance(tx, dict) and tx.get("request_id") == req_id:
                await firebase_patch(f"transactions/{user_id}/{tx_id}", {"status": "approved"})
    try: await send_user_notification(int(user_id), f"<b>Заявка на вывод одобрена!</b>", "Открыть WinStars", WEBAPP_URL)
    except: pass
    await callback.message.edit_text(f"ОДОБРЕНО #{req_id}. Переведите {req.get('amount_after_commission')} ★ игроку {user_id}")
    await callback.answer()

@dp.callback_query(F.data.startswith("wd_reject:"))
async def on_wd_reject_start(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_TELEGRAM_ID: return
    req_id = callback.data.split(":", 1)[1]
    req = await firebase_get(f"withdrawal_requests/{req_id}")
    if not isinstance(req, dict) or req.get("status") != "pending": return
    await state.update_data(req_id=req_id, user_id=req.get("user_id"), amount=req.get("amount", 0))
    await state.set_state(WithdrawReject.waiting_reason)
    await bot.send_message(ADMIN_TELEGRAM_ID, f"Введите причину отказа для #{req_id} (или /cancel):")
    await callback.answer()

@dp.message(Command("cancel"), WithdrawReject.waiting_reason)
async def cmd_cancel_reject(message: types.Message, state: FSMContext):
    await state.clear(); await message.answer("Отменено.")

@dp.message(WithdrawReject.waiting_reason)
async def on_wd_reject_reason(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_TELEGRAM_ID or not message.text or message.text.startswith("/"): return
    reason = message.text.strip()
    if len(reason) < 3: return
    data = await state.get_data(); await state.clear()
    req_id = data["req_id"]; user_id = data["user_id"]; amount = data["amount"]
    await firebase_patch(f"withdrawal_requests/{req_id}", {"status": "rejected", "reviewed_at": int(time.time()), "reviewed_by": message.from_user.id, "reject_reason": reason})
    user = await firebase_get(f"users/{user_id}")
    if isinstance(user, dict):
        await firebase_patch(f"users/{user_id}", {"balance": user.get("balance", 0) + amount, "frozen_balance": max(0, user.get("frozen_balance", 0) - amount)})
    txs = await firebase_get(f"transactions/{user_id}") or {}
    if isinstance(txs, dict):
        for tx_id, tx in txs.items():
            if isinstance(tx, dict) and tx.get("request_id") == req_id:
                await firebase_patch(f"transactions/{user_id}/{tx_id}", {"status": "rejected", "reject_reason": reason})
    try: await send_user_notification(int(user_id), f"<b>Вывод отклонён</b>\nПричина: {reason}\nЗвёзды возвращены.", "Открыть WinStars", WEBAPP_URL)
    except: pass
    await message.answer(f"Заявка #{req_id} отклонена.")

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
            bonus_amount = 0; pending = user.get("pending_bonus"); pending_label = None
            if isinstance(pending, dict) and amount >= (pending.get("min_deposit", 0) or 0):
                bonus_amount = int(amount * pending.get("percent", 0) / 100)
                pending_label = f"Бонус {pending.get('percent')}%"
            final_amount = amount + bonus_amount
            await firebase_patch(f"users/{user_id}", {"balance": cur + final_amount, "pending_bonus": None})
            try:
                await http_client.post(f"{FIREBASE_DB_URL}/transactions/{user_id}.json", json={"type": "deposit", "amount": amount, "status": "success", "created_at": int(time.time()), "request_id": None, "reject_reason": None, "label": None})
                if bonus_amount > 0:
                    await http_client.post(f"{FIREBASE_DB_URL}/transactions/{user_id}.json", json={"type": "bonus", "amount": bonus_amount, "status": "success", "created_at": int(time.time()), "request_id": None, "reject_reason": None, "label": pending_label})
            except: pass
            if bonus_amount > 0: await message.answer(f"Баланс пополнен на {amount} ★ + бонус {bonus_amount} ★!")
            else: await message.answer(f"Баланс пополнен на {amount} ★!")

# ==================== REST API ====================

@app.get("/api/check-subscription")
async def api_check_subscription(user_id: int):
    _SUB_CHECK_CACHE.pop(user_id, None)
    subscribed = await is_subscribed(user_id)
    return {"subscribed": subscribed}

@app.get("/api/withdraw/status")
async def api_withdraw_status(user_id: int):
    user = await firebase_get(f"users/{user_id}") or {}
    active_request = None
    all_reqs = await firebase_get("withdrawal_requests") or {}
    if isinstance(all_reqs, dict):
        for rid, r in all_reqs.items():
            if isinstance(r, dict) and r.get("user_id") == str(user_id) and r.get("status") == "pending":
                active_request = {"id": rid, "amount": r.get("amount"), "created_at": r.get("created_at")}
                break
    return {
        "balance": user.get("balance", 0), "frozen_balance": user.get("frozen_balance", 0),
        "referral_count": user.get("referral_count", 0), "referral_link": f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}",
        "min_withdraw": WITHDRAW_MIN, "commission": WITHDRAW_COMMISSION,
        "active_request": active_request, "pending_bonus": user.get("pending_bonus"),
        "agreed_terms": user.get("agreed_terms", True),
    }

@app.post("/api/withdraw/request")
async def api_withdraw_request(request: Request):
    req = await request.json()
    user_id = str(req.get("user_id") or "")
    try: amount = int(req.get("amount", 0))
    except: raise HTTPException(400, "Invalid amount")
    if amount < WITHDRAW_MIN: raise HTTPException(400, f"Minimum {WITHDRAW_MIN}")
    try: uid_int = int(user_id)
    except: raise HTTPException(400, "Bad user_id")
    if not await is_subscribed(uid_int): raise HTTPException(403, "Subscribe to channel")
    user = await firebase_get(f"users/{user_id}")
    if not isinstance(user, dict): raise HTTPException(404, "Profile not found")
    if amount > user.get("balance", 0): raise HTTPException(400, "Insufficient stars")
    if uid_int != ADMIN_TELEGRAM_ID and user.get("referral_count", 0) < 5: raise HTTPException(400, "Need 5 friends")
    all_reqs = await firebase_get("withdrawal_requests") or {}
    if isinstance(all_reqs, dict):
        for rid, r in all_reqs.items():
            if isinstance(r, dict) and r.get("user_id") == user_id and r.get("status") == "pending": raise HTTPException(400, "Already active")
    amount_after = round(amount * (1 - WITHDRAW_COMMISSION), 2)
    new_balance = round(user.get("balance", 0) - amount, 2)
    frozen_old = user.get("frozen_balance", 0)
    await firebase_patch(f"users/{user_id}", {"balance": new_balance, "frozen_balance": frozen_old + amount})
    now = int(time.time())
    req_data = {"user_id": user_id, "username": user.get("username", "user"), "first_name": user.get("first_name", "Player"), "amount": amount, "amount_after_commission": amount_after, "referral_count": user.get("referral_count", 0), "status": "pending", "created_at": now, "reviewed_at": None, "reject_reason": None, "reviewed_by": None}
    r = await http_client.post(f"{FIREBASE_DB_URL}/withdrawal_requests.json", json=req_data)
    req_id = r.json().get("name") if r.status_code == 200 else None
    if not req_id:
        await firebase_patch(f"users/{user_id}", {"balance": user.get("balance", 0), "frozen_balance": frozen_old})
        raise HTTPException(500, "Error")
    try: await http_client.post(f"{FIREBASE_DB_URL}/transactions/{user_id}.json", json={"type": "withdrawal", "amount": amount, "amount_after_commission": amount_after, "status": "pending", "created_at": now, "request_id": req_id, "reject_reason": None, "label": None})
    except: pass
    await _notify_admin_withdrawal(req_id, req_data)
    return {"status": "ok", "request_id": req_id, "amount": amount, "amount_after_commission": amount_after, "new_balance": new_balance}

@app.get("/api/user/transactions")
async def api_user_transactions(user_id: int):
    txs = await firebase_get(f"transactions/{user_id}") or {}
    if not isinstance(txs, dict): return {"transactions": []}
    items = [{"id": tid, **tx} for tid, tx in txs.items() if isinstance(tx, dict)]
    items.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    return {"transactions": items}

@app.get("/api/debug/market-audit/{ev_id}")
async def debug_market_audit(ev_id: str):
    raw = []
    for label, url in [("listBase", FONBET_BASE + FONBET_LINE_PATH), ("live", FONBET_BASE + FONBET_LIVE_PATH), ("full", f"{FONBET_BASE}{FONBET_FULL_LINE_PATH}&eventId={ev_id}")]:
        try:
            r = cffi_requests.get(url, headers=FONBET_HEADERS, impersonate="chrome120", timeout=30)
            if r.status_code != 200: continue
            data = r.json()
            for f in data.get("factors", []):
                if str(f.get("e")) == ev_id: raw.append({"src": label, "f": f.get("f"), "p": f.get("p"), "v": f.get("v"), "mapped": FONBET_FACTORS.get(f.get("f"))})
            for cf in data.get("customFactors", []):
                if str(cf.get("e")) == ev_id:
                    for f in cf.get("factors", []): raw.append({"src": label + "/custom", "f": f.get("f"), "p": f.get("p"), "v": f.get("v"), "mapped": FONBET_FACTORS.get(f.get("f"))})
        except: pass
    by_fid = {}; seen = set()
    for r in raw:
        if "f" not in r or r.get("f") is None: continue
        key = (r["f"], r.get("p"))
        if key in seen: continue
        seen.add(key)
        by_fid.setdefault(r["f"], []).append({"p": r.get("p"), "v": r.get("v"), "src": r.get("src")})
    computed = _parse_markets([{"f": r["f"], "p": r.get("p"), "v": r.get("v")} for r in raw if "f" in r and r.get("f") is not None])
    fb = await firebase_get(f"matches/fonbet_{ev_id}") or {}
    return {"ev_id": ev_id, "unmapped_fids": sorted({r["f"] for r in raw if "f" in r and r.get("mapped") is None}), "by_fid": by_fid, "computed_markets": computed, "firebase_snapshot": {"exists": bool(fb), "markets": fb.get("markets") if isinstance(fb, dict) else None}}

@app.get("/api/proxy-image")
async def proxy_image(url: str):
    try:
        resp = await http_client.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5.0)
        if resp.status_code == 200: return Response(content=resp.content, media_type="image/png")
    except: pass
    return Response(status_code=404)

@app.get("/api/user/sync")
async def sync_user(initData: str = Query("")):
    user_data = {"id": 12345678, "first_name": "Player", "username": "user"}
    if initData:
        try:
            parsed_qs = urllib.parse.parse_qs(initData, keep_blank_values=True)
            user_data = json.loads({k: v[0] for k, v in parsed_qs.items() if k != "hash"}.get("user", "{}"))
        except: pass
    user_id = str(user_data.get("id", 12345678))
    subscribed = await is_subscribed(int(user_id)) if initData else True
    existing_user = await firebase_get(f"users/{user_id}")
    if not isinstance(existing_user, dict) or "balance" not in existing_user:
        default_user = {
            "user_id": user_id, "username": user_data.get("username", "user"),
            "first_name": user_data.get("first_name", "Player"),
            "photo_url": user_data.get("photo_url", f"https://api.dicebear.com/7.x/bottts/svg?seed={user_id}"),
            "balance": existing_user.get("balance", 0) if isinstance(existing_user, dict) else 0,
            "frozen_balance": 0, "total_bets": existing_user.get("total_bets", 0) if isinstance(existing_user, dict) else 0,
            "wins": existing_user.get("wins", 0) if isinstance(existing_user, dict) else 0,
            "profit": existing_user.get("profit", 0) if isinstance(existing_user, dict) else 0,
            "referral_code": user_id, "referred_by": None, "referral_count": 0, "subscribed": subscribed,
            "agreed_terms": False, "agreed_at": None, "pending_bonus": None, "created_at": int(time.time()),
        }
        await firebase_put(f"users/{user_id}", default_user)
        return {"status": "ok", "user": default_user, "subscribed": subscribed}
    if existing_user.get("subscribed") != subscribed:
        await firebase_patch(f"users/{user_id}", {"subscribed": subscribed})
        existing_user["subscribed"] = subscribed
    return {"status": "ok", "user": existing_user, "subscribed": subscribed}

@app.api_route("/api/matches", methods=["GET", "HEAD"])
async def get_matches():
    matches = await firebase_get("matches")
    return {"matches": matches if isinstance(matches, dict) else {}}

@app.get("/api/match/{match_id}/markets")
async def get_match_markets(match_id: str):
    match_db = await firebase_get(f"matches/{match_id}")
    markets_data = {}
    if isinstance(match_db, dict):
        if isinstance(match_db.get("markets"), dict) and match_db["markets"]: markets_data = match_db["markets"]
        if not markets_data and match_db.get("odds"):
            odds = match_db["odds"]; p1, px, p2 = odds.get("1"), odds.get("X"), odds.get("2")
            if p1 and px and p2: markets_data = {"Исход": [{"name": "П1", "odds": p1}, {"name": "Х", "odds": px}, {"name": "П2", "odds": p2}]}
    return {"markets": markets_data}

@app.get("/api/user/bets")
async def get_user_bets(user_id: int):
    bets_dict = await firebase_get(f"users/{user_id}/bets")
    if not isinstance(bets_dict, dict): return {"bets": []}
    return {"bets": [{"id": k, **v} for k, v in bets_dict.items() if isinstance(v, dict)]}

@app.post("/api/bets/place")
async def place_bet(request: Request):
    req = await request.json()
    user_id = req.get("user_id"); amount = req.get("amount"); bets = req.get("bets", []); bet_type = req.get("type", "single")
    if amount is None or amount < 10: raise HTTPException(400, "Min 10 stars")
    if not bets: raise HTTPException(400, "No outcomes")
    try: uid_int = int(user_id)
    except: raise HTTPException(400, "Bad user_id")
    if not await is_subscribed(uid_int): raise HTTPException(403, "Subscribe to channel")
    user = await firebase_get(f"users/{user_id}")
    if not isinstance(user, dict): raise HTTPException(404, "Profile not found")
    if not user.get("agreed_terms"): raise HTTPException(403, "Terms not accepted")
    if user.get("balance", 0) < amount: raise HTTPException(400, "Insufficient stars")
    new_fp = hashlib.md5(f"{user_id}|{amount}|{json.dumps(bets, sort_keys=True)}".encode()).hexdigest()
    active_all = await firebase_get("active_bets") or {}
    if isinstance(active_all, dict):
        ts = time.time()
        for _bid, _b in active_all.items():
            if not isinstance(_b, dict) or ts - _b.get("timestamp", 0) > 10: continue
            if hashlib.md5(f"{_b.get('user_id')}|{_b.get('amount')}|{json.dumps(_b.get('events', []), sort_keys=True)}".encode()).hexdigest() == new_fp:
                raise HTTPException(429, "Duplicate")
    total_coeff = 1.0
    for b in bets: total_coeff *= float(b.get("coefficient", 1.0))
    total_coeff = round(total_coeff, 2)
    new_balance = round(user.get("balance", 0) - amount, 2)
    bet_data = {"user_id": str(user_id), "type": bet_type, "events": bets, "amount": amount, "total_coefficient": total_coeff, "potential_win": round(amount * total_coeff, 2), "status": "active", "timestamp": int(time.time())}
    bet_ref = await http_client.post(f"{FIREBASE_DB_URL}/bets.json", json=bet_data)
    bet_id = bet_ref.json().get("name") if bet_ref.status_code == 200 else None
    if bet_id:
        await firebase_put(f"users/{user_id}/bets/{bet_id}", bet_data)
        await firebase_put(f"active_bets/{bet_id}", {"user_id": str(user_id), "events": bets, "amount": amount, "total_coefficient": total_coeff, "timestamp": time.time()})
    await firebase_patch(f"users/{user_id}", {"balance": new_balance, "total_bets": user.get("total_bets", 0) + 1})
    return {"status": "ok", "new_balance": new_balance}

@app.post("/api/cashier/invoice")
async def create_invoice(request: Request):
    req = await request.json()
    try:
        link = await bot.create_invoice_link(title="WinStars", description=f"Пополнение {req.get('amount')} ★", payload=f"topup_{req.get('user_id')}_{req.get('amount')}", currency="XTR", prices=[LabeledPrice(label="Звёзды", amount=req.get("amount"))])
        return {"status": "ok", "invoice_link": link}
    except Exception as e: raise HTTPException(500, str(e))

@app.post("/api/cashier/promo")
async def activate_promo(request: Request):
    req = await request.json()
    code = (req.get("code") or "").upper().strip(); user_id = str(req.get("user_id") or "")
    promo = await firebase_get(f"promo_codes/{code}")
    if not isinstance(promo, dict): raise HTTPException(400, "Promo not found")
    now = int(time.time())
    if (promo.get("expires_at", 0) and promo.get("expires_at", 0) < now) or (promo.get("max_uses", 0) and promo.get("used_count", 0) >= promo.get("max_uses", 0)) or (promo.get("used_by") and user_id in promo.get("used_by")):
        raise HTTPException(400, "Promo expired or limit reached")
    user = await firebase_get(f"users/{user_id}")
    if not isinstance(user, dict): raise HTTPException(400, "Profile not found")
    if promo.get("new_users_only") and user.get("total_bets", 0) > 0: raise HTTPException(400, "For new users only")
    ptype = promo.get("type", "fixed_stars")
    if ptype == "fixed_stars":
        stars = promo.get("reward_stars", 50)
        await firebase_patch(f"users/{user_id}", {"balance": user.get("balance", 0) + stars})
        await firebase_patch(f"promo_codes/{code}", {"used_count": promo.get("used_count", 0) + 1, f"used_by/{user_id}": True})
        try: await http_client.post(f"{FIREBASE_DB_URL}/transactions/{user_id}.json", json={"type": "promo", "amount": stars, "status": "success", "created_at": now, "request_id": None, "reject_reason": None, "label": f"Промокод {code}"})
        except: pass
        return {"status": "ok", "message": f"Начислено {stars} ★", "new_balance": user.get("balance", 0) + stars, "type": "fixed_stars", "amount": stars}
    elif ptype == "deposit_percent":
        percent = promo.get("deposit_percent", 10); min_dep = promo.get("min_deposit", 0) or 0
        await firebase_patch(f"users/{user_id}", {"pending_bonus": {"code": code, "percent": percent, "min_deposit": min_dep, "applied_at": now}})
        await firebase_patch(f"promo_codes/{code}", {"used_count": promo.get("used_count", 0) + 1, f"used_by/{user_id}": True})
        return {"status": "ok", "message": f"+{percent}% к пополнению (мин. {min_dep}★)", "type": "deposit_percent", "percent": percent, "min_deposit": min_dep}

@app.get("/api/chat/history")
async def get_chat_history():
    msgs = await firebase_get("chat_messages")
    return {"messages": list(msgs.values())[-50:] if isinstance(msgs, dict) else []}

@app.websocket("/ws/chat")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text(); payload = json.loads(data)
            msg_data = {"user_id": payload["user_id"], "first_name": payload["first_name"], "text": payload["text"]}
            await http_client.post(f"{FIREBASE_DB_URL}/chat_messages.json", json=msg_data)
            await manager.broadcast(msg_data)
    except WebSocketDisconnect: manager.disconnect(websocket)
    except Exception: manager.disconnect(websocket)
    finally: manager.disconnect(websocket)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("SERVER_PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, workers=1, log_level="warning")