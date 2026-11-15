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
import hmac
import math
from collections import deque
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query, Request, Header
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, LabeledPrice
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from curl_cffi import requests as cffi_requests

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN env variable is not set!")
if ":" not in BOT_TOKEN or len(BOT_TOKEN) < 40:
    raise RuntimeError(f"BOT_TOKEN looks invalid (len={len(BOT_TOKEN)})")
print(f"✅ BOT_TOKEN loaded from env (len={len(BOT_TOKEN)})")

WEBAPP_URL = "https://starland-comics.github.io/winstarsbot/"
FIREBASE_DB_URL = "https://winstars-435f1-default-rtdb.firebaseio.com"

ADMIN_TELEGRAM_ID = 8425538809
CHANNEL_USERNAME = "winstarschannel"
CHANNEL_ID = "@winstarschannel"
BOT_USERNAME = "WinStarsPlaybot"

WITHDRAW_MIN = 1000
WITHDRAW_COMMISSION = 0.05
PREMATCH_GRACE_SEC = 1800  # 30 минут допуска на задержку старта матча в прематче

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

FONBET_MIRRORS = [
    "https://line-lb51.bk6bba-resources.com",
    "https://line-lb52.bk6bba-resources.com",
    "https://line-lb53.bk6bba-resources.com",
]
FONBET_RESULTS_MIRRORS = [
    "https://clientsapi01w.bk6bba-resources.com",
    "https://clientsapi02w.bk6bba-resources.com",
]
FONBET_LINE_PATH = "/events/listBase?scopeMarket=1600&lang=ru"
FONBET_LIVE_PATH = "/events/list?scopeMarket=1600&lang=ru&live=true"
FONBET_FULL_LINE_PATH = "/events/event?scopeMarket=1600&lang=ru"

FONBET_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.fon.bet/",
    "Origin": "https://www.fon.bet"
}

LIVE_POLL_SEC = 20
SETTLE_POLL_SEC = 45
NOTIFY_POLL_SEC = 60
FINISHED_TTL = 20
FULL_LINE_MAX_PER_CYCLE = 60
SUB_CHECK_TTL = 60
FULL_LINE_CONCURRENCY = 5

TEAM_NAME_FIXES = {}

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
    r'^россия\.\s*(премьер|рпл|фнл|1-я|1 я|первая|вторая|2-я|2 я|футбольная национальная|кубок|суперкубок)',
    r'^фонбет\s*кубок\s*россии',
    r'^англия\.\s*(премьер(-|\s|\.|$)|чемпионшип|кубок англии|fa cup|кубок лиги|efl cup|carabao)',
    r'^испания\.\s*(ла лига|примера|сегунда|кубок|copa)($|\s*дивизион|\s*лига|\s*\.)',
    r'^италия\.\s*(серия а|серия b|серия б|кубок)($|\s*\.|\s*сезон)',
    r'^германия\.\s*(бундеслига|2\.\s*бундеслига|вторая бундеслига|кубок)($|\s*\.|\s*сезон)',
    r'^франция\.\s*(лига 1|лига 2|кубок)($|\s*\.|\s*сезон)',
    r'^(нидерланды|голландия)\.\s*(премьер|эредивизи|eredivisie|кубок|knvb)',
    r'^португалия\.\s*(примейра|primeira|лига португал|кубок|taça)',
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
    "итоги турнира", "специальные ставки", "статистика игроков",
    "статистические показатели", "лучший клуб",
]

BLACKLIST_WORDS = [
    "люб", "жен", "мол", "дубл", "резерв", "академ", "до 15", "до 16", "до 17", "до 18", "до 19", "до 20", "до 21", "до 23",
    "u15", "u16", "u17", "u18", "u19", "u20", "u21", "u23", " b team", " ii", " iii",
]

_FINISHED_CACHE = {"data": {}, "ts": 0}
_SUB_CHECK_CACHE = {}
_SKIP_LOG = deque(maxlen=2000)
_FIREBASE_BAD_CHARS = re.compile(r'[\.\$#\[\]/]')
_WEBAPP_SECRET_KEY = hmac.new(b"WebAppData", BOT_TOKEN.encode("utf-8"), hashlib.sha256).digest()

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


# ==================== TELEGRAM INITDATA AUTH ====================

def verify_telegram_init_data(init_data: str) -> dict | None:
    if not init_data:
        return None
    try:
        parsed = urllib.parse.parse_qs(init_data, keep_blank_values=True)
        hash_val = parsed.get("hash", [None])[0]
        if not hash_val:
            return None
        data_check_arr = []
        for k in sorted(parsed.keys()):
            if k == "hash":
                continue
            data_check_arr.append(f"{k}={parsed[k][0]}")
        data_check_string = "\n".join(data_check_arr)
        computed_hash = hmac.new(_WEBAPP_SECRET_KEY, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(computed_hash, hash_val):
            return None
        user_raw = parsed.get("user", ["{}"])[0]
        return json.loads(user_raw)
    except Exception:
        return None


def get_verified_user_id(init_data: str, fallback_uid=None) -> str:
    user_obj = verify_telegram_init_data(init_data)
    if user_obj and user_obj.get("id"):
        return str(user_obj["id"])
    raise HTTPException(status_code=401, detail="Ошибка авторизации Telegram. Перезапустите Mini App.")


# ==================== WEBSOCKET CHAT ====================

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
            try:
                await c.send_text(json.dumps(message))
            except Exception:
                dead.append(c)
        for conn in dead:
            self.disconnect(conn)


manager = ConnectionManager()


# ==================== FIREBASE HELPERS ====================

async def firebase_get(path: str):
    try:
        resp = await http_client.get(f"{FIREBASE_DB_URL}/{path}.json")
        return resp.json()
    except Exception:
        return None


async def firebase_put(path: str, data: dict):
    try:
        r = await http_client.put(f"{FIREBASE_DB_URL}/{path}.json", json=data, timeout=60.0)
        return r.status_code == 200
    except Exception:
        return False


async def firebase_patch(path: str, data: dict):
    try:
        r = await http_client.patch(f"{FIREBASE_DB_URL}/{path}.json", json=data)
        return r.status_code == 200
    except Exception:
        return False


async def firebase_delete(path: str):
    try:
        await http_client.delete(f"{FIREBASE_DB_URL}/{path}.json")
    except Exception:
        pass


async def log_transaction(user_id: str, tx_type: str, amount: float, label: str = "", status: str = "", reject_reason: str = ""):
    payload = {
        "type": tx_type,
        "amount": round(float(amount), 2),
        "label": label,
        "status": status,
        "reject_reason": reject_reason,
        "created_at": int(time.time())
    }
    try:
        r = await http_client.post(f"{FIREBASE_DB_URL}/transactions/{user_id}.json", json=payload, timeout=10.0)
        if r.status_code == 200:
            return r.json().get("name")
    except Exception:
        pass
    return None


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
    except Exception:
        result = False
    _SUB_CHECK_CACHE[user_id] = (now, result)
    return result


def _find_bad_key(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str) or not k:
                return f"empty key at {path}"
            if _FIREBASE_BAD_CHARS.search(k):
                return f"bad key '{k}' at {path}"
            r = _find_bad_key(v, f"{path}/{k}")
            if r:
                return r
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            r = _find_bad_key(v, f"{path}[{i}]")
            if r:
                return r
    return None


async def _save_matches_fallback(matches_dict):
    existing = await firebase_get("matches") or {}
    if isinstance(existing, dict):
        to_delete = set(existing.keys()) - set(matches_dict.keys())
        for k in to_delete:
            try:
                await http_client.delete(f"{FIREBASE_DB_URL}/matches/{k}.json")
            except Exception:
                pass
    for mid, mdata in matches_dict.items():
        if _find_bad_key(mdata, ""):
            continue
        try:
            await http_client.put(f"{FIREBASE_DB_URL}/matches/{mid}.json", json=mdata, timeout=15.0)
        except Exception:
            pass


async def _save_matches(matches_dict):
    if not matches_dict:
        return
    if _find_bad_key(matches_dict, ""):
        await _save_matches_fallback(matches_dict)
        return
    try:
        payload = json.dumps(matches_dict, allow_nan=False, ensure_ascii=False)
        size = len(payload)
    except ValueError:
        return

    if size < 900_000:
        try:
            r = await http_client.put(
                f"{FIREBASE_DB_URL}/matches.json",
                content=payload,
                headers={"Content-Type": "application/json"},
                timeout=45.0
            )
            if r.status_code != 200:
                await _save_matches_fallback(matches_dict)
        except Exception:
            await _save_matches_fallback(matches_dict)
    else:
        await _save_matches_fallback(matches_dict)


async def _record_finished(mid, s1, s2, source, void=False, retries=3):
    for _ in range(retries):
        try:
            payload = {"score1": s1, "score2": s2, "source": source, "recorded_at": int(time.time())}
            if void:
                payload["void"] = True
            r = await http_client.patch(f"{FIREBASE_DB_URL}/finished_matches/{mid}.json", json=payload, timeout=10.0)
            if r.status_code == 200:
                _FINISHED_CACHE["ts"] = 0
                return True
        except Exception:
            pass
        await asyncio.sleep(1)
    return False


async def get_finished_cached():
    now = time.time()
    if now - _FINISHED_CACHE["ts"] < FINISHED_TTL and _FINISHED_CACHE["data"]:
        return _FINISHED_CACHE["data"]
    raw = await firebase_get("finished_matches") or {}
    if not isinstance(raw, dict):
        raw = {}
    _FINISHED_CACHE["data"] = raw
    _FINISHED_CACHE["ts"] = now
    return raw


# ==================== FONBET PARSING ====================

def _is_allowed_league(league):
    if not league or league == "Футбол":
        return False
    low = league.lower().strip()
    for f in FORBIDDEN_LEAGUE_WORDS:
        if f in low:
            return False
    for pattern in ALLOWED_LEAGUE_PATTERNS:
        try:
            if re.search(pattern, low):
                return True
        except Exception:
            pass
    return False


def _parse_start_ts(ev):
    st = ev.get("startTime")
    if st is None:
        return 0
    try:
        return int(st)
    except Exception:
        return 0


def _extract_score(li):
    """Извлекает точный счет и минуту/статус периода из liveEventInfos Fonbet."""
    if not li or not isinstance(li, dict):
        return None, None, None
    s1 = s2 = None
    scores = li.get("scores")
    if isinstance(scores, list):
        for block in scores:
            if isinstance(block, list) and block:
                first = block[0]
                if isinstance(first, dict):
                    c1, c2 = first.get("c1"), first.get("c2")
                    if c1 is not None and c2 is not None:
                        try:
                            s1, s2 = int(c1), int(c2)
                            break
                        except Exception:
                            pass
            elif isinstance(block, dict):
                c1, c2 = block.get("c1"), block.get("c2")
                if c1 is not None and c2 is not None:
                    try:
                        s1, s2 = int(c1), int(c2)
                        break
                    except Exception:
                        pass

    if s1 is None:
        c1, c2 = li.get("c1"), li.get("c2")
        if c1 is not None and c2 is not None:
            try:
                s1, s2 = int(c1), int(c2)
            except Exception:
                pass

    minute = None
    timer_str = li.get("timer")
    if isinstance(timer_str, str) and ":" in timer_str:
        try:
            minute = int(timer_str.split(":")[0])
        except Exception:
            pass
    if minute is None and li.get("timerSeconds") is not None:
        try:
            minute = max(0, int(int(li["timerSeconds"]) // 60))
        except Exception:
            pass

    return s1, s2, minute


def _extract_score_breakdown(li):
    if not li:
        return []
    scores = li.get("scores")
    if not isinstance(scores, list):
        return []
    result = []
    for idx, block in enumerate(scores):
        if isinstance(block, list) and block:
            for item in block:
                if isinstance(item, dict):
                    c1, c2 = item.get("c1"), item.get("c2")
                    if c1 is not None and c2 is not None:
                        try:
                            period = "first" if idx == 0 else ("second" if idx == 1 else f"period_{idx}")
                            result.append({"period": period, "s1": int(c1), "s2": int(c2)})
                        except Exception:
                            pass
                        break
    return result


def _normalize_p(p_val):
    try:
        num = float(p_val)
    except Exception:
        return 0.0
    if num != int(num):
        return num
    if abs(num) >= 10:
        return num / 100.0
    return num


def _extract_number_from_name(name):
    m = re.search(r'([+-]?\d+(?:\.\d+)?)', name)
    if m:
        return float(m.group(1))
    return 999.0


def _fix_market_orientation(markets):
    outcomes = markets.get("Тотал")
    if not outcomes:
        return
    tb_items, tm_items = [], []
    for o in outcomes:
        n = o["name"]
        try:
            if n.startswith("ТБ "):
                tb_items.append((float(n[3:]), o))
            elif n.startswith("ТМ "):
                tm_items.append((float(n[3:]), o))
        except Exception:
            continue
    if tb_items and not tm_items and len(tb_items) >= 3:
        tb_items.sort(key=lambda x: x[0])
        violations = sum(1 for i in range(1, len(tb_items)) if tb_items[i][1]["odds"] < tb_items[i - 1][1]["odds"])
        if violations >= len(tb_items) - 2:
            for lim, o in tb_items:
                o["name"] = f"ТМ {lim:g}"
            return
    if tm_items and not tb_items and len(tm_items) >= 3:
        tm_items.sort(key=lambda x: x[0])
        violations = sum(1 for i in range(1, len(tm_items)) if tm_items[i][1]["odds"] > tm_items[i - 1][1]["odds"])
        if violations >= len(tm_items) - 2:
            for lim, o in tm_items:
                o["name"] = f"ТБ {lim:g}"
            return


def _check_total_consistency(markets):
    main_total = None
    for o in markets.get("Тотал", []):
        if o["name"].startswith("ТБ ") and o.get("_fid") == 930:
            try:
                main_total = (float(o["name"][3:]), o["odds"])
            except Exception:
                pass
    if main_total is None:
        return {g: outs for g, outs in markets.items()}, {}
    main_limit, main_tb_odds = main_total
    confirmed, suspect = {}, {}
    for g, outs in markets.items():
        if g != "Тотал":
            confirmed[g] = outs
            continue
        conf_outs, susp_outs = [], []
        for o in outs:
            if o.get("_fid") in (930, 931):
                conf_outs.append(o)
                continue
            if o["name"].startswith("ТБ "):
                try:
                    lim = float(o["name"][3:])
                except Exception:
                    susp_outs.append(o)
                    continue
                exp = 1.0 + (main_tb_odds - 1.0) * ((main_limit - lim) * 2 if lim < main_limit else (lim - main_limit) * 0.5)
                if exp < 1.0:
                    exp = 1.0
                if o["odds"] < exp * 0.7 or o["odds"] > exp * 1.3:
                    susp_outs.append(o)
                else:
                    conf_outs.append(o)
            elif o["name"].startswith("ТМ "):
                try:
                    lim = float(o["name"][3:])
                except Exception:
                    susp_outs.append(o)
                    continue
                exp = 1.0 + (1.0 / main_tb_odds) * ((main_limit - lim) * 2 if lim < main_limit else (lim - main_limit) * 0.5)
                if exp < 1.0:
                    exp = 1.0
                if o["odds"] < exp * 0.7 or o["odds"] > exp * 1.3:
                    susp_outs.append(o)
                else:
                    conf_outs.append(o)
            else:
                susp_outs.append(o)
        if conf_outs:
            confirmed[g] = conf_outs
        if susp_outs:
            suspect["Тотал (доп.)"] = susp_outs
    return confirmed, suspect


def _parse_markets(factors_list):
    best = {}
    for f in factors_list:
        fid = f.get("f")
        if fid not in FONBET_FACTORS:
            continue
        group, base, has_param = FONBET_FACTORS[fid]
        odds = f.get("v")
        if odds is None:
            continue
        try:
            odds = float(odds)
        except Exception:
            continue
        if odds <= 1.0:
            continue
        name = base
        if has_param:
            p = f.get("p")
            if p is None:
                continue
            try:
                p_raw = float(p)
            except Exception:
                continue
            p_val = _normalize_p(p_raw)
            if p_val == 0 and group == "Тотал":
                continue
            if group == "Тотал":
                name = f"{base} {abs(p_val):g}"
            else:
                p_str = str(int(p_val)) if p_val == int(p_val) else f"{p_val:g}"
                if p_val > 0:
                    p_str = f"+{p_str}"
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
    for g, outs in suspect.items():
        markets[g] = outs
    for g in markets:
        markets[g].sort(key=lambda x: _extract_number_from_name(x["name"]))
        for o in markets[g]:
            o.pop("_fid", None)
    return markets


def _filter_total_group(outs, current_score):
    new_outs = []
    for o in outs:
        n = o["name"]
        if n.startswith("ТБ "):
            try:
                lim = float(n[3:])
            except Exception:
                new_outs.append(o)
                continue
            if current_score > lim:
                continue
        elif n.startswith("ТМ "):
            try:
                lim = float(n[3:])
            except Exception:
                new_outs.append(o)
                continue
            if current_score >= lim:
                continue
        new_outs.append(o)
    return new_outs


def _filter_decided(markets, s1, s2):
    if s1 is None or s2 is None:
        return markets
    filtered = {}
    for g, outs in markets.items():
        if g == "Обе забьют":
            if s1 > 0 and s2 > 0:
                continue
            filtered[g] = outs
        elif g in ("Тотал", "Тотал (доп.)"):
            new_outs = _filter_total_group(outs, s1 + s2)
            if new_outs:
                filtered[g] = new_outs
        else:
            filtered[g] = outs
    return filtered


def _fetch_fonbet_with_fallback(path_and_query):
    for base in FONBET_MIRRORS:
        url = base + path_and_query
        for _ in range(2):
            try:
                r = cffi_requests.get(url, headers=FONBET_HEADERS, impersonate="chrome120", timeout=15)
                if r.status_code == 200:
                    return r.json()
            except Exception:
                pass
            time.sleep(0.2)
    return None


def _fetch_fonbet_official_results(date_str: str) -> dict:
    """Запрашивает официальную таблицу результатов Fonbet за дату YYYY-MM-DD."""
    results_map = {}
    for base in FONBET_RESULTS_MIRRORS:
        url = f"{base}/results/results.json.php?locale=ru&lineDate={date_str}"
        try:
            r = cffi_requests.get(url, headers=FONBET_HEADERS, impersonate="chrome120", timeout=15)
            if r.status_code == 200:
                data = r.json()
                for ev in data.get("events", []):
                    eid = str(ev.get("id", ""))
                    status = ev.get("status")  # 3 = завершен, 4 = отменен
                    score_str = ev.get("score", "")
                    if not eid:
                        continue
                    if status == 4:
                        results_map[f"fonbet_{eid}"] = {"score1": 0, "score2": 0, "void": True, "source": "fonbet_results"}
                    elif status == 3 and ":" in score_str:
                        main_part = score_str.split("(")[0].strip()
                        parts = main_part.split(":")
                        if len(parts) == 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
                            results_map[f"fonbet_{eid}"] = {
                                "score1": int(parts[0].strip()),
                                "score2": int(parts[1].strip()),
                                "source": "fonbet_results"
                            }
                if results_map:
                    return results_map
        except Exception:
            continue
    return results_map


def _build_logo_index(raw_cache):
    idx = {}
    if not isinstance(raw_cache, dict):
        return idx
    for k, v in raw_cache.items():
        if not v:
            continue
        real_k = k
        if k.startswith("b64_"):
            try:
                pad = "=" * (-len(k[4:]) % 4)
                real_k = base64.urlsafe_b64decode(k[4:] + pad).decode("utf-8")
            except Exception:
                continue
        team = real_k.split("|")[0] if "|" in real_k else real_k
        if team not in idx:
            idx[team] = v
    return idx


def _build_sport_hierarchy(all_sports):
    entry_by_id, parent_by_id = {}, {}
    for sp in all_sports:
        sp_id = sp.get('id')
        if sp_id is None:
            continue
        entry_by_id[sp_id] = sp
        parent = None
        for field in ('parentId', 'parent_id', 'sportId', 'sport_id', 'parent', 'rootId', 'root_id'):
            v = sp.get(field)
            if v is not None and v != sp_id:
                parent = v
                break
        if parent is not None:
            parent_by_id[sp_id] = parent

    def find_top(entry_id):
        cur = entry_id
        visited = set()
        for _ in range(20):
            if cur in visited:
                return None
            visited.add(cur)
            entry = entry_by_id.get(cur)
            if not entry:
                return None
            if entry.get('kind') == 'sport':
                return entry
            p = parent_by_id.get(cur)
            if p is None:
                return None
            cur = p
        return None

    top_by_id = {sp_id: find_top(sp_id) for sp_id in entry_by_id}
    return entry_by_id, parent_by_id, top_by_id


# ==================== REFUND LOGIC ====================

async def refund_bet_by_id(bet_id: str):
    bet = await firebase_get(f"bets/{bet_id}")
    if not isinstance(bet, dict):
        return {"status": "error", "message": "Ставка не найдена"}
    user_id = bet.get("user_id")
    amount = float(bet.get("amount", 0))
    old_status = bet.get("status", "unknown")
    if old_status == "void" and bet.get("refunded"):
        return {"status": "already_refunded", "message": "Уже возвращено"}
    user = await firebase_get(f"users/{user_id}")
    if not isinstance(user, dict):
        return {"status": "error", "message": "Пользователь не найден"}
    cur_balance = float(user.get("balance", 0))
    cur_profit = float(user.get("profit", 0))
    cur_wins = int(user.get("wins", 0))
    balance_delta = profit_delta = 0.0
    wins_delta = 0
    if old_status == "lost":
        balance_delta = amount
        profit_delta = amount
    elif old_status == "won":
        eff_coeff = float(bet.get("effective_coefficient", bet.get("total_coefficient", 1)))
        win_amount = amount * eff_coeff
        balance_delta = amount - win_amount
        profit_delta = -(win_amount - amount)
        wins_delta = -1
    elif old_status == "active":
        balance_delta = amount
        await firebase_delete(f"active_bets/{bet_id}")
    else:
        return {"status": "error", "message": f"Неизвестный статус {old_status}"}

    await firebase_patch(f"users/{user_id}", {
        "balance": round(cur_balance + balance_delta, 2),
        "profit": round(cur_profit + profit_delta, 2),
        "wins": max(0, cur_wins + wins_delta)
    })
    refund_payload = {
        "status": "void",
        "refunded": True,
        "refunded_at": int(time.time()),
        "refund_reason": "manual_refund",
        "previous_status": old_status
    }
    await firebase_patch(f"bets/{bet_id}", refund_payload)
    await firebase_patch(f"users/{user_id}/bets/{bet_id}", refund_payload)
    try:
        await send_user_notification(
            int(user_id),
            f"<b>Прогноз отменён</b>\n\nВозврат <b>{amount} ★</b> на баланс.",
            "Открыть WinStars",
            WEBAPP_URL
        )
    except Exception:
        pass
    return {
        "status": "ok",
        "bet_id": bet_id,
        "user_id": user_id,
        "amount": amount,
        "previous_status": old_status,
        "balance_delta": balance_delta,
        "new_balance": round(cur_balance + balance_delta, 2)
    }


# ==================== ADMIN & DEBUG ENDPOINTS ====================

@app.api_route("/api/admin/force-refresh/{ev_id}", methods=["GET", "POST"])
async def api_admin_force_refresh(ev_id: str, admin_id: int = Query(0)):
    if admin_id != ADMIN_TELEGRAM_ID:
        raise HTTPException(403, "Forbidden")

    async def _do():
        data = await asyncio.to_thread(_fetch_fonbet_with_fallback, f"{FONBET_FULL_LINE_PATH}&eventId={ev_id}")
        if not data:
            return {"status": "error", "message": "Fonbet не отдаёт матч"}
        ev = next((e for e in data.get("events", []) if str(e.get("id")) == str(ev_id)), None)
        if not ev:
            return {"status": "error", "message": "Событие не найдено"}

        all_sports = data.get("sports", [])
        tournament_by_id = {sp['id']: sp['name'] for sp in all_sports if sp.get('kind') != 'sport' and sp.get('id') and sp.get('name')}
        league = tournament_by_id.get(ev.get("sportId"), "")
        factors = next((cf.get("factors", []) for cf in data.get("customFactors", []) if str(cf.get("e")) == str(ev_id)), [])
        live_info = next((li for li in data.get("liveEventInfos", []) if str(li.get("eventId")) == str(ev_id)), None)
        s1, s2, timer = _extract_score(live_info)
        is_live = (ev.get("place") == "live" or live_info is not None) and s1 is not None and s2 is not None
        markets_data = _parse_markets(factors)
        if is_live:
            markets_data = _filter_decided(markets_data, s1, s2)

        t1 = TEAM_NAME_FIXES.get(ev.get("team1", ""), ev.get("team1", ""))
        t2 = TEAM_NAME_FIXES.get(ev.get("team2", ""), ev.get("team2", ""))
        st = _parse_start_ts(ev)
        raw_cache = await firebase_get("team_logos") or {}
        logo_idx = _build_logo_index(raw_cache)

        p1 = px = p2 = None
        for o in markets_data.get("Исход", []):
            if o["name"] == "П1": p1 = o["odds"]
            elif o["name"] == "Х": px = o["odds"]
            elif o["name"] == "П2": p2 = o["odds"]

        if is_live:
            min_label = f"{timer}'" if timer is not None else "HT"
            status_str, score_str = f"LIVE {min_label}", f"{s1} : {s2}"
        else:
            status_str, score_str = time.strftime("%d.%m %H:%M", time.localtime(st)), "VS"

        payload = {
            "title": f"{t1} - {t2}", "home": t1, "away": t2,
            "homeLogo": logo_idx.get(t1, ""), "awayLogo": logo_idx.get(t2, ""),
            "league": league, "status": status_str, "score": score_str,
            "is_live": is_live, "start_time": st,
            "odds": {"1": p1, "X": px, "2": p2},
            "markets": markets_data,
            "score_breakdown": _extract_score_breakdown(live_info) if live_info else []
        }
        r = await http_client.put(f"{FIREBASE_DB_URL}/matches/fonbet_{ev_id}.json", json=payload, timeout=15.0)
        return {"status": "ok" if r.status_code == 200 else "error", "mid": f"fonbet_{ev_id}", "status_str": status_str, "score": score_str}

    try:
        return await asyncio.wait_for(_do(), timeout=20.0)
    except asyncio.TimeoutError:
        return {"status": "timeout"}


@app.get("/api/admin/bad-bets")
async def api_admin_bad_bets(admin_id: int = Query(0)):
    if admin_id != ADMIN_TELEGRAM_ID:
        raise HTTPException(403, "Forbidden")
    all_bets = await firebase_get("bets") or {}
    finished = await firebase_get("finished_matches") or {}
    bad = []
    for bet_id, bet in all_bets.items():
        if not isinstance(bet, dict) or bet.get("status") != "lost":
            continue
        events = bet.get("events", [])
        all_suspect = all(
            isinstance(finished.get(ev.get("match_id")), dict)
            and finished[ev["match_id"]].get("score1") == 0
            and finished[ev["match_id"]].get("score2") == 0
            and finished[ev["match_id"]].get("source") == "fonbet_last"
            for ev in events
        )
        if all_suspect and events:
            bad.append({
                "bet_id": bet_id, "user_id": bet.get("user_id"),
                "amount": bet.get("amount"), "timestamp": bet.get("timestamp"),
                "events": [{"match_id": ev.get("match_id"), "match_title": ev.get("match_title")} for ev in events]
            })
    bad.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
    return {"count": len(bad), "bets": bad}


@app.api_route("/api/admin/refund-bet/{bet_id}", methods=["GET", "POST"])
async def api_admin_refund_bet(bet_id: str, admin_id: int = Query(0)):
    if admin_id != ADMIN_TELEGRAM_ID:
        raise HTTPException(403, "Forbidden")
    return await refund_bet_by_id(bet_id)


@app.get("/api/debug/skipped")
async def debug_skipped(admin_id: int = Query(0)):
    if admin_id != ADMIN_TELEGRAM_ID:
        raise HTTPException(403, "Forbidden")
    return {"count": len(_SKIP_LOG), "items": list(_SKIP_LOG)[-100:]}


# ==================== WORKERS ====================

async def fonbet_scraper_worker():
    print("⚡ fonbet_scraper_worker запущен (v18 clean live & real results)")
    while True:
        try:
            t0 = time.time()
            now_ts = int(time.time())
            seven_days = 7 * 86400

            line_data = await asyncio.to_thread(_fetch_fonbet_with_fallback, FONBET_LINE_PATH)
            live_data = await asyncio.to_thread(_fetch_fonbet_with_fallback, FONBET_LIVE_PATH)
            if not line_data and not live_data:
                await asyncio.sleep(LIVE_POLL_SEC)
                continue

            events_by_id, all_sports, all_live, raw_sources = {}, [], {}, []
            live_feed_eids = set()

            if live_data:
                for ev in live_data.get("events", []):
                    if ev.get("id"):
                        live_feed_eids.add(ev["id"])
                for li in live_data.get("liveEventInfos", []):
                    if li.get("eventId"):
                        live_feed_eids.add(li["eventId"])

            for data in [live_data, line_data]:
                if not data:
                    continue
                all_sports.extend(data.get("sports", []))
                for li in data.get("liveEventInfos", []):
                    eid = li.get("eventId")
                    if eid and eid not in all_live:
                        all_live[eid] = li
                for ev in data.get("events", []):
                    eid = ev.get("id")
                    if eid:
                        if eid not in events_by_id:
                            events_by_id[eid] = dict(ev)
                        elif ev.get("place") == "live":
                            events_by_id[eid]["place"] = "live"
                raw_sources.append({
                    "factors": data.get("factors", []),
                    "customFactors": data.get("customFactors", [])
                })

            _, _, top_by_id = _build_sport_hierarchy(all_sports)
            tournament_by_id = {
                sp['id']: sp['name']
                for sp in all_sports
                if sp.get('kind') != 'sport' and sp.get('id') and sp.get('name')
            }

            candidates, candidate_eids = [], set()
            for ev in events_by_id.values():
                eid, t1, t2 = ev.get("id"), ev.get("team1", ""), ev.get("team2", "")
                if ev.get("rootKind") != 1 or ev.get("kind") != 1 or not t1 or not t2:
                    continue
                st = _parse_start_ts(ev)
                sid = ev.get("sportId")
                top = top_by_id.get(sid)
                league = tournament_by_id.get(sid, "")
                if not st or not top or top.get('name', '').lower() not in ('футбол', 'football', 'soccer'):
                    continue
                if any(w in f"{t1} {t2}".lower() for w in BLACKLIST_WORDS) or not _is_allowed_league(league):
                    continue
                ev["team1"] = TEAM_NAME_FIXES.get(t1, t1)
                ev["team2"] = TEAM_NAME_FIXES.get(t2, t2)
                candidates.append((ev, league))
                candidate_eids.add(eid)

            all_factors = {}
            for src in raw_sources:
                for f in src["factors"]:
                    if f.get("e") in candidate_eids:
                        all_factors.setdefault(f.get("e"), []).append(f)
                for cf in src["customFactors"]:
                    if cf.get("e") in candidate_eids:
                        all_factors.setdefault(cf.get("e"), []).extend(cf.get("factors", []))
            raw_sources, line_data, live_data = None, None, None
            gc.collect()

            candidates.sort(key=lambda x: (0 if x[0].get("id") in live_feed_eids else 1, abs(_parse_start_ts(x[0]) - now_ts)))

            raw_cache = await firebase_get("team_logos") or {}
            logo_idx = _build_logo_index(raw_cache)
            cached_prev = await firebase_get("matches") or {}

            # Предзагрузка полной линии
            preloaded = {}
            sem = asyncio.Semaphore(FULL_LINE_CONCURRENCY)

            async def _load(ev_id):
                async with sem:
                    d = await asyncio.to_thread(_fetch_fonbet_with_fallback, f"{FONBET_FULL_LINE_PATH}&eventId={ev_id}")
                    if d:
                        for cf in d.get("customFactors", []):
                            if cf.get("e") == ev_id:
                                return ev_id, cf.get("factors", [])
                    return ev_id, None

            to_load = [
                ev.get("id")
                for idx, (ev, _) in enumerate(candidates)
                if ev.get("id") in live_feed_eids or idx < FULL_LINE_MAX_PER_CYCLE
            ]
            if to_load:
                for res in await asyncio.gather(*[_load(e) for e in to_load], return_exceptions=True):
                    if isinstance(res, tuple) and res[1]:
                        preloaded[res[0]] = res[1]

            matches_dict, live_cnt, prematch_cnt = {}, 0, 0

            for ev, league in candidates:
                eid = ev.get("id")
                mid = f"fonbet_{eid}"
                t1, t2 = ev.get("team1", ""), ev.get("team2", "")
                st = _parse_start_ts(ev)

                li = all_live.get(eid)
                s1, s2, timer = _extract_score(li)

                # Строгая проверка настоящего LIVE: матч есть в live_data или place == "live" И у него есть счет
                in_live_source = (eid in live_feed_eids) or (ev.get("place") == "live")
                is_live = in_live_source and (s1 is not None and s2 is not None)

                if is_live:
                    # Если матч уже закончился (таймер > 130 мин)
                    if timer is not None and timer > 130:
                        continue
                    factors_for_ev = preloaded.get(eid) or all_factors.get(eid, [])
                    markets_data = _filter_decided(_parse_markets(factors_for_ev), s1, s2)
                    p1 = px = p2 = None
                    for o in markets_data.get("Исход", []):
                        if o["name"] == "П1": p1 = o["odds"]
                        elif o["name"] == "Х": px = o["odds"]
                        elif o["name"] == "П2": p2 = o["odds"]

                    # Если счет 0:0 и таймера нет, проверяем время старта — не фейковый ли это анонс
                    if timer is not None:
                        status_str = f"LIVE {timer}'"
                    else:
                        breakdown = _extract_score_breakdown(li)
                        status_str = "LIVE HT" if len(breakdown) >= 1 and (s1 > 0 or s2 > 0 or st < now_ts - 2700) else "LIVE 1'"

                    matches_dict[mid] = {
                        "title": f"{t1} - {t2}", "home": t1, "away": t2,
                        "homeLogo": logo_idx.get(t1, ""), "awayLogo": logo_idx.get(t2, ""),
                        "league": league, "status": status_str, "score": f"{s1} : {s2}",
                        "is_live": True, "start_time": st,
                        "odds": {"1": p1, "X": px, "2": p2},
                        "markets": markets_data,
                        "score_breakdown": _extract_score_breakdown(li),
                    }
                    live_cnt += 1
                else:
                    # ПРЕМАТЧ: показываем только матчи, которые еще не начались (с допуском 30 мин на задержку)
                    if st > now_ts + seven_days or st < now_ts - PREMATCH_GRACE_SEC:
                        continue
                    factors_for_ev = preloaded.get(eid) or all_factors.get(eid, [])
                    markets_data = _parse_markets(factors_for_ev)
                    p1 = px = p2 = None
                    for o in markets_data.get("Исход", []):
                        if o["name"] == "П1": p1 = o["odds"]
                        elif o["name"] == "Х": px = o["odds"]
                        elif o["name"] == "П2": p2 = o["odds"]

                    if not p1 and not px and not p2:
                        continue

                    matches_dict[mid] = {
                        "title": f"{t1} - {t2}", "home": t1, "away": t2,
                        "homeLogo": logo_idx.get(t1, ""), "awayLogo": logo_idx.get(t2, ""),
                        "league": league,
                        "status": time.strftime("%d.%m %H:%M", time.localtime(st)),
                        "score": "VS",
                        "is_live": False, "start_time": st,
                        "odds": {"1": p1, "X": px, "2": p2},
                        "markets": markets_data,
                        "score_breakdown": [],
                    }
                    prematch_cnt += 1

            # Если живой матч пропал из лайва — сразу проверяем его в результатах Fonbet
            finished_known = await get_finished_cached()
            disappeared_live = [
                mid for mid, old in cached_prev.items()
                if isinstance(old, dict) and old.get("is_live") and mid not in matches_dict and mid not in finished_known
            ]
            if disappeared_live:
                today_str = time.strftime("%Y-%m-%d", time.localtime(now_ts))
                yesterday_str = time.strftime("%Y-%m-%d", time.localtime(now_ts - 86400))
                official_res = await asyncio.to_thread(_fetch_fonbet_official_results, today_str)
                if any(m not in official_res for m in disappeared_live):
                    official_res.update(await asyncio.to_thread(_fetch_fonbet_official_results, yesterday_str))
                for mid in disappeared_live:
                    if mid in official_res:
                        r_info = official_res[mid]
                        await _record_finished(mid, r_info["score1"], r_info["score2"], r_info["source"], r_info.get("void", False))

            if matches_dict:
                await _save_matches(matches_dict)
                print(f"[FONBET] saved={len(matches_dict)} (LIVE: {live_cnt} | PREMATCH: {prematch_cnt}) | {time.time()-t0:.1f}s")

            events_by_id, matches_dict, cached_prev, all_factors = None, None, None, None
            gc.collect()
        except Exception:
            import traceback
            traceback.print_exc()
        gc.collect()
        await asyncio.sleep(LIVE_POLL_SEC)


async def settle_bets_worker():
    print("settle_bets_worker started...")
    while True:
        try:
            active_bets = await firebase_get("active_bets") or {}
            if isinstance(active_bets, dict) and active_bets:
                finished = await get_finished_cached()

                # Подтягиваем официальные результаты Fonbet для всех нерассчитанных матчей в активных ставках
                missing_mids = set()
                for bet in active_bets.values():
                    if isinstance(bet, dict):
                        for ev in bet.get("events", []):
                            mid = ev.get("match_id")
                            if mid and mid not in finished:
                                missing_mids.add(mid)

                if missing_mids:
                    now_ts = int(time.time())
                    official_res = {}
                    for offset in (0, 86400, 172800):
                        d_str = time.strftime("%Y-%m-%d", time.localtime(now_ts - offset))
                        official_res.update(await asyncio.to_thread(_fetch_fonbet_official_results, d_str))
                        if all(m in official_res for m in missing_mids):
                            break
                    for mid in missing_mids:
                        if mid in official_res:
                            r_info = official_res[mid]
                            await _record_finished(mid, r_info["score1"], r_info["score2"], r_info["source"], r_info.get("void", False))
                    finished = await get_finished_cached()

                for bet_id, bet in list(active_bets.items()):
                    try:
                        if not isinstance(bet, dict):
                            continue
                        events = bet.get("events", [])
                        all_finished, results = True, []
                        for event in events:
                            m_id = event.get("match_id")
                            if m_id not in finished:
                                all_finished = False
                                break
                            results.append(finished[m_id])
                        if not all_finished or not results:
                            continue

                        user_id, amount = bet.get("user_id"), float(bet.get("amount", 0))
                        if any(r.get("void") for r in results):
                            status = "void"
                            user = await firebase_get(f"users/{user_id}")
                            if isinstance(user, dict):
                                await firebase_patch(f"users/{user_id}", {
                                    "balance": round(float(user.get("balance", 0)) + amount, 2),
                                    "total_bets": max(0, int(user.get("total_bets", 1)) - 1)
                                })
                            await firebase_patch(f"bets/{bet_id}", {"status": status})
                            if user_id:
                                await firebase_patch(f"users/{user_id}/bets/{bet_id}", {"status": status})
                                await send_user_notification(user_id, f"<b>Возврат по прогнозу</b>\n\nМатч отменён или перенесён. На баланс возвращено <b>{amount} ★</b>.", "Открыть WinStars", WEBAPP_URL)
                            await firebase_delete(f"active_bets/{bet_id}")
                            continue

                        is_lost, is_all_void, eff_coeff, leg_results = False, True, 1.0, []
                        for i, event in enumerate(events):
                            res = results[i]
                            s1, s2 = res.get("score1"), res.get("score2")
                            oc = event.get("outcome_key") or event.get("outcome", "")
                            gn = event.get("group_name", "")
                            coeff = float(event.get("coefficient", 1.0))
                            leg_status = "void"
                            if s1 is not None and s2 is not None and gn != "Тотал (доп.)":
                                ws = calculate_outcome(oc, s1, s2, gn)
                                leg_status = "won" if ws is True else ("lost" if ws is False else "void")
                            leg_results.append({
                                "match_title": event.get("match_title", ""),
                                "outcome": event.get("outcome", ""),
                                "group_name": gn,
                                "coefficient": coeff,
                                "status": leg_status,
                                "score1": s1,
                                "score2": s2
                            })
                            if leg_status == "lost":
                                is_lost = True
                            elif leg_status == "won":
                                is_all_void = False
                                eff_coeff *= coeff

                        if is_lost:
                            status = "lost"
                            user = await firebase_get(f"users/{user_id}")
                            if isinstance(user, dict):
                                await firebase_patch(f"users/{user_id}", {"profit": round(float(user.get("profit", 0)) - amount, 2)})
                            if user_id:
                                await send_user_notification(user_id, f"<b>Прогноз не зашёл</b>\n\nСумма: <b>{amount} ★</b>\nПодробности во вкладке «Ставки».", "Открыть WinStars", WEBAPP_URL)
                        elif is_all_void:
                            status = "void"
                            user = await firebase_get(f"users/{user_id}")
                            if isinstance(user, dict):
                                await firebase_patch(f"users/{user_id}", {
                                    "balance": round(float(user.get("balance", 0)) + amount, 2),
                                    "total_bets": max(0, int(user.get("total_bets", 1)) - 1)
                                })
                            if user_id:
                                await send_user_notification(user_id, f"<b>Возврат по прогнозу</b>\n\nНа баланс возвращено <b>{amount} ★</b>.", "Открыть WinStars", WEBAPP_URL)
                        else:
                            status = "won"
                            win_amount = round(amount * eff_coeff, 2)
                            user = await firebase_get(f"users/{user_id}")
                            if isinstance(user, dict):
                                await firebase_patch(f"users/{user_id}", {
                                    "balance": round(float(user.get("balance", 0)) + win_amount, 2),
                                    "profit": round(float(user.get("profit", 0)) + (win_amount - amount), 2),
                                    "wins": int(user.get("wins", 0)) + 1
                                })
                            if user_id:
                                await send_user_notification(user_id, f"<b>Прогноз сыграл!</b>\n\nВыплата: <b>+{win_amount} ★</b> (кэф {round(eff_coeff, 2)})", "Открыть WinStars", WEBAPP_URL)

                        await firebase_patch(f"bets/{bet_id}", {"status": status, "effective_coefficient": round(eff_coeff, 2), "leg_results": leg_results})
                        if user_id:
                            await firebase_patch(f"users/{user_id}/bets/{bet_id}", {"status": status, "effective_coefficient": round(eff_coeff, 2), "leg_results": leg_results})
                        await firebase_delete(f"active_bets/{bet_id}")
                    except Exception as e:
                        print(f"bet error: {e}")
        except Exception as e:
            print(f"settle error: {e}")
        await asyncio.sleep(SETTLE_POLL_SEC)


async def notify_start_worker():
    while True:
        try:
            active_bets, matches = await firebase_get("active_bets") or {}, await firebase_get("matches") or {}
            now = int(time.time())
            for bet_id, bet in list(active_bets.items()):
                if not isinstance(bet, dict):
                    continue
                notified = bet.get("start_notified_matches") or {}
                to_notify = [
                    (ev.get("match_id"), matches.get(ev.get("match_id")))
                    for ev in bet.get("events", [])
                    if not notified.get(ev.get("match_id"))
                    and isinstance(matches.get(ev.get("match_id")), dict)
                    and 0 <= matches[ev["match_id"]].get("start_time", 0) - now <= 600
                ]
                if to_notify:
                    text = "<b>Матч скоро начнётся</b>\n\n" + "".join(
                        f"• {m.get('title')} — {time.strftime('%H:%M', time.localtime(m.get('start_time')))}\n"
                        for _, m in to_notify[:3]
                    )
                    await send_user_notification(bet.get("user_id"), text, "Мои прогнозы", WEBAPP_URL)
                    new_notified = {**notified, **{m: True for m, _ in to_notify}}
                    await firebase_patch(f"active_bets/{bet_id}", {"start_notified_matches": new_notified})
        except Exception:
            pass
        await asyncio.sleep(NOTIFY_POLL_SEC)


def calculate_outcome(o, s1, s2, gn=""):
    o = o.lower().strip()
    if o in ["п1", "1", "победа 1"]: return s1 > s2
    if o in ["х", "x", "ничья"]: return s1 == s2
    if o in ["п2", "2", "победа 2"]: return s2 > s1
    if o in ["1x", "1х"]: return s1 >= s2
    if o in ["12"]: return s1 != s2
    if o in ["x2", "х2"]: return s2 >= s1
    if o in ["да", "обе забьют"]: return s1 > 0 and s2 > 0
    if o in ["нет", "оз нет"]: return s1 == 0 or s2 == 0
    total = s1 + s2
    m = re.search(r'(?:тб|больше)\s*([+-]?\d+(?:\.\d+)?)', o)
    if m:
        l = float(m.group(1))
        return True if total > l else (False if total < l else None)
    m = re.search(r'(?:тм|меньше)\s*([+-]?\d+(?:\.\d+)?)', o)
    if m:
        l = float(m.group(1))
        return True if total < l else (False if total > l else None)
    m = re.search(r'(?:ф1|фора 1)\s*([+-]?\d+(?:\.\d+)?)', o)
    if m:
        val = float(m.group(1))
        res = s1 + val - s2
        return True if res > 0 else (False if res < 0 else None)
    m = re.search(r'(?:ф2|фора 2)\s*([+-]?\d+(?:\.\d+)?)', o)
    if m:
        val = float(m.group(1))
        res = s2 + val - s1
        return True if res > 0 else (False if res < 0 else None)
    return False


async def _notify_admin_withdrawal(req_id, data):
    text = (
        f"<b>Новая заявка на вывод</b>\n\n"
        f"Игрок: {data.get('first_name','')} (@{data.get('username','')})\n"
        f"ID: <code>{data.get('user_id')}</code>\n"
        f"Сумма: <b>{data.get('amount')} ★</b>\n"
        f"К зачислению: <b>{data.get('amount_after_commission')} ★</b>\n\n"
        f"Telegram ID: <code>{data.get('user_id')}</code>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Одобрить", callback_data=f"wd_approve:{req_id}"),
        InlineKeyboardButton(text="Отклонить", callback_data=f"wd_reject:{req_id}")
    ]])
    try:
        await bot.send_message(ADMIN_TELEGRAM_ID, text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass


@app.on_event("startup")
async def startup_event():
    print("🚀 WinStars API v18 starting...")
    try:
        chat = await bot.get_chat(CHANNEL_ID)
        print(f"   Channel OK: {chat.title}")
    except Exception:
        pass
    asyncio.create_task(dp.start_polling(bot))
    asyncio.create_task(fonbet_scraper_worker())
    asyncio.create_task(settle_bets_worker())
    asyncio.create_task(notify_start_worker())


# ==================== BOT ====================

async def _register_user_if_new(user_id, from_user, referrer_id=None):
    existing = await firebase_get(f"users/{user_id}")
    actual_referrer = referrer_id if referrer_id and str(referrer_id) != str(user_id) else None
    if isinstance(existing, dict):
        # Если юзер уже заходил в WebApp до /start ref_..., но реферер еще не был проставлен
        if actual_referrer and not existing.get("referred_by") and existing.get("total_bets", 0) == 0:
            await firebase_patch(f"users/{user_id}", {"referred_by": str(actual_referrer)})
            ref = await firebase_get(f"users/{actual_referrer}")
            if isinstance(ref, dict):
                await firebase_patch(f"users/{actual_referrer}", {"referral_count": int(ref.get("referral_count", 0)) + 1})
        return False

    await firebase_put(f"users/{user_id}", {
        "user_id": str(user_id), "username": from_user.username or "user", "first_name": from_user.first_name or "Игрок",
        "photo_url": f"https://api.dicebear.com/7.x/bottts/svg?seed={user_id}",
        "balance": 0, "frozen_balance": 0, "total_bets": 0, "wins": 0, "profit": 0,
        "referral_code": str(user_id), "referred_by": actual_referrer, "referral_count": 0, "subscribed": True,
        "agreed_terms": False, "agreed_at": None, "pending_bonus": None, "created_at": int(time.time()),
    })
    if actual_referrer:
        ref = await firebase_get(f"users/{actual_referrer}")
        if isinstance(ref, dict):
            await firebase_patch(f"users/{actual_referrer}", {"referral_count": int(ref.get("referral_count", 0)) + 1})
    return True


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    uid = message.from_user.id
    _SUB_CHECK_CACHE.pop(uid, None)
    args = message.text.split()
    ref_id = args[1][4:] if len(args) > 1 and args[1].startswith("ref_") else None
    if not await is_subscribed(uid):
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Подписаться на канал", url=f"https://t.me/{CHANNEL_USERNAME}")],
            [InlineKeyboardButton(text="Проверить подписку", callback_data=f"check_sub:{ref_id or ''}")]
        ])
        await message.answer(
            f"Добро пожаловать в <b>WinStars</b>!\n\nДля использования подпишитесь на канал @{CHANNEL_USERNAME}.",
            reply_markup=kb, parse_mode="HTML"
        )
        return
    await _register_user_if_new(uid, message.from_user, ref_id)
    user = await firebase_get(f"users/{uid}")
    if isinstance(user, dict) and not user.get("agreed_terms"):
        await message.answer(
            TERMS_TEXT,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Принимаю условия", callback_data="agree_terms")]]),
            parse_mode="HTML"
        )
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть WinStars", web_app=WebAppInfo(url=WEBAPP_URL))]])
    await message.answer("Добро пожаловать в <b>WinStars</b>!", reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data.startswith("check_sub"))
async def on_check_sub(callback: types.CallbackQuery):
    uid = callback.from_user.id
    _SUB_CHECK_CACHE.pop(uid, None)
    parts = callback.data.split(":")
    ref_id = parts[1] if len(parts) > 1 and parts[1] else None
    if not await is_subscribed(uid):
        await callback.answer("Подписка не обнаружена", show_alert=True)
        return
    await _register_user_if_new(uid, callback.from_user, ref_id)
    user = await firebase_get(f"users/{uid}")
    if isinstance(user, dict) and not user.get("agreed_terms"):
        await callback.message.edit_text(
            TERMS_TEXT,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Принимаю условия", callback_data="agree_terms")]]),
            parse_mode="HTML"
        )
    else:
        await callback.message.edit_text(
            "Спасибо за подписку!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть WinStars", web_app=WebAppInfo(url=WEBAPP_URL))]])
        )
    await callback.answer()


@dp.callback_query(F.data == "agree_terms")
async def on_agree(callback: types.CallbackQuery):
    await firebase_patch(f"users/{callback.from_user.id}", {"agreed_terms": True, "agreed_at": int(time.time())})
    await callback.message.edit_text(
        "Спасибо!",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть WinStars", web_app=WebAppInfo(url=WEBAPP_URL))]])
    )
    await callback.answer()


@dp.message(Command("bad_bets"))
async def cmd_bad_bets(message: types.Message):
    if message.from_user.id != ADMIN_TELEGRAM_ID:
        return
    bad = (await api_admin_bad_bets(ADMIN_TELEGRAM_ID))["bets"]
    if not bad:
        await message.answer("Подозрительных ставок нет.")
        return
    lines = [f"<b>Подозрительные:</b> {len(bad)}\n"] + [f"<code>{b['bet_id']}</code> | <code>{b['user_id']}</code> | {b['amount']} ★" for b in bad[:30]] + ["\n<code>/refund BET_ID</code>"]
    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("refund"))
async def cmd_refund(message: types.Message):
    if message.from_user.id != ADMIN_TELEGRAM_ID:
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Использование: <code>/refund BET_ID</code>", parse_mode="HTML")
        return
    res = await refund_bet_by_id(parts[1].strip())
    await message.answer(f"Возврат: {res.get('status')}. Возвращено: {res.get('balance_delta', 0)} ★", parse_mode="HTML")


@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id != ADMIN_TELEGRAM_ID:
        return
    reqs = await firebase_get("withdrawal_requests") or {}
    pending = [(rid, r) for rid, r in reqs.items() if isinstance(r, dict) and r.get("status") == "pending"]
    if not pending:
        await message.answer("Нет заявок")
        return
    rows = [[InlineKeyboardButton(text=f"OK #{rid}", callback_data=f"wd_approve:{rid}"), InlineKeyboardButton(text=f"NO #{rid}", callback_data=f"wd_reject:{rid}")] for rid, _ in pending[:10]]
    await message.answer(f"Активных заявок: {len(pending)}", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@dp.message(Command("promo_create"))
async def cmd_promo_create(message: types.Message):
    if message.from_user.id != ADMIN_TELEGRAM_ID:
        return
    parts = message.text.split()
    if len(parts) < 6:
        await message.answer("Формат: /promo_create CODE fixed 50 100 30")
        return
    try:
        val, uses, days = int(parts[3]), int(parts[4]), int(parts[5])
    except Exception:
        await message.answer("Ошибка в числах")
        return
    data = {
        "code": parts[1].upper(),
        "type": "fixed_stars" if parts[2] == "fixed" else "deposit_percent",
        "max_uses": uses,
        "used_count": 0,
        "expires_at": int(time.time()) + days * 86400,
        "new_users_only": "new" in parts,
        "created_at": int(time.time())
    }
    if parts[2] == "fixed":
        data["reward_stars"] = val
    else:
        data["deposit_percent"] = val
        data["min_deposit"] = int(parts[6]) if len(parts) > 6 and parts[6].isdigit() else 0
    await firebase_put(f"promo_codes/{data['code']}", data)
    await message.answer(f"Промокод {data['code']} создан")


@dp.message(Command("promo_list"))
async def cmd_promo_list(message: types.Message):
    if message.from_user.id != ADMIN_TELEGRAM_ID:
        return
    promos = await firebase_get("promo_codes") or {}
    lines = ["<b>Промокоды:</b>"] + [f"<code>{k}</code> | {v.get('used_count',0)}/{v.get('max_uses',0)}" for k, v in promos.items() if isinstance(v, dict)]
    await message.answer("\n".join(lines) if len(lines) > 1 else "Нет кодов", parse_mode="HTML")


@dp.message(Command("promo_delete"))
async def cmd_promo_delete(message: types.Message):
    if message.from_user.id != ADMIN_TELEGRAM_ID:
        return
    parts = message.text.split()
    if len(parts) < 2:
        return
    await firebase_delete(f"promo_codes/{parts[1].upper()}")
    await message.answer("Удален")


@dp.callback_query(F.data.startswith("wd_approve:"))
async def on_wd_approve(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_TELEGRAM_ID:
        return
    req_id = callback.data.split(":")[1]
    req = await firebase_get(f"withdrawal_requests/{req_id}")
    if not isinstance(req, dict) or req.get("status") != "pending":
        return
    user_id, amount = req["user_id"], req.get("amount", 0)
    await firebase_patch(f"withdrawal_requests/{req_id}", {"status": "approved", "reviewed_at": int(time.time()), "reviewed_by": callback.from_user.id})
    if req.get("tx_id"):
        await firebase_patch(f"transactions/{user_id}/{req['tx_id']}", {"status": "approved"})
    user = await firebase_get(f"users/{user_id}")
    if isinstance(user, dict):
        await firebase_patch(f"users/{user_id}", {"frozen_balance": max(0, float(user.get("frozen_balance", 0)) - float(amount))})
    try:
        await send_user_notification(int(user_id), f"<b>Заявка одобрена!</b>\nСумма: {amount} ★", "Открыть WinStars", WEBAPP_URL)
    except Exception:
        pass
    await callback.answer("Одобрено")


@dp.callback_query(F.data.startswith("wd_reject:"))
async def on_wd_reject(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_TELEGRAM_ID:
        return
    req_id = callback.data.split(":")[1]
    await state.update_data(req_id=req_id)
    await state.set_state(WithdrawReject.waiting_reason)
    await callback.message.answer(f"Введите причину отказа для #{req_id}:")
    await callback.answer()


@dp.message(WithdrawReject.waiting_reason)
async def on_reject_reason(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_TELEGRAM_ID or message.text.startswith("/"):
        return
    req_id = (await state.get_data())["req_id"]
    await state.clear()
    req = await firebase_get(f"withdrawal_requests/{req_id}")
    if not isinstance(req, dict):
        return
    user_id, amount = req["user_id"], float(req.get("amount", 0))
    await firebase_patch(f"withdrawal_requests/{req_id}", {"status": "rejected", "reject_reason": message.text, "reviewed_at": int(time.time())})
    if req.get("tx_id"):
        await firebase_patch(f"transactions/{user_id}/{req['tx_id']}", {"status": "rejected", "reject_reason": message.text})
    user = await firebase_get(f"users/{user_id}")
    if isinstance(user, dict):
        await firebase_patch(f"users/{user_id}", {
            "balance": round(float(user.get("balance", 0)) + amount, 2),
            "frozen_balance": max(0, float(user.get("frozen_balance", 0)) - amount)
        })
    try:
        await send_user_notification(int(user_id), f"<b>Заявка отклонена</b>\nПричина: {message.text}", "Открыть WinStars", WEBAPP_URL)
    except Exception:
        pass
    await message.answer(f"Заявка #{req_id} отклонена.")


@dp.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: types.PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@dp.message(F.successful_payment)
async def process_payment(message: types.Message):
    payload = message.successful_payment.invoice_payload
    if payload.startswith("topup_"):
        parts = payload.split("_")
        user_id, amount = parts[1], int(parts[2])
        user = await firebase_get(f"users/{user_id}")
        if isinstance(user, dict):
            bonus = 0
            pending = user.get("pending_bonus")
            if isinstance(pending, dict) and amount >= pending.get("min_deposit", 0):
                bonus = int(amount * pending.get("percent", 0) / 100)
            await firebase_patch(f"users/{user_id}", {
                "balance": round(float(user.get("balance", 0)) + amount + bonus, 2),
                "pending_bonus": None
            })
            await log_transaction(user_id, "deposit", amount, "Пополнение Telegram Stars")
            if bonus > 0:
                await log_transaction(user_id, "bonus", bonus, f"Бонус +{pending.get('percent', 0)}%")
            await message.answer(f"Баланс пополнен на {amount + bonus} ★!")


# ==================== REST API ====================

@app.get("/api/check-subscription")
async def api_check_sub(user_id: int):
    _SUB_CHECK_CACHE.pop(user_id, None)
    sub = await is_subscribed(user_id)
    if sub:
        u = await firebase_get(f"users/{user_id}")
        if isinstance(u, dict) and not u.get("subscribed"):
            await firebase_patch(f"users/{user_id}", {"subscribed": True})
    return {"subscribed": sub}


@app.get("/api/withdraw/status")
async def api_withdraw_status(user_id: int, x_telegram_init_data: str = Header("")):
    uid = get_verified_user_id(x_telegram_init_data)
    user = await firebase_get(f"users/{uid}") or {}
    active_req = None
    for rid, r in (await firebase_get("withdrawal_requests") or {}).items():
        if isinstance(r, dict) and r.get("user_id") == uid and r.get("status") == "pending":
            active_req = {"id": rid, "amount": r.get("amount")}
    return {
        "balance": user.get("balance", 0) if isinstance(user, dict) else 0,
        "frozen_balance": user.get("frozen_balance", 0) if isinstance(user, dict) else 0,
        "referral_count": user.get("referral_count", 0) if isinstance(user, dict) else 0,
        "referral_link": f"https://t.me/{BOT_USERNAME}?start=ref_{uid}",
        "min_withdraw": WITHDRAW_MIN,
        "commission": WITHDRAW_COMMISSION,
        "active_request": active_req,
        "pending_bonus": user.get("pending_bonus") if isinstance(user, dict) else None,
        "agreed_terms": user.get("agreed_terms", True) if isinstance(user, dict) else True
    }


@app.post("/api/withdraw/request")
async def api_withdraw_request(request: Request, x_telegram_init_data: str = Header("")):
    user_id = get_verified_user_id(x_telegram_init_data)
    req = await request.json()
    amount = int(req.get("amount", 0))
    if amount < WITHDRAW_MIN:
        raise HTTPException(400, f"Минимум {WITHDRAW_MIN} ★")
    if not await is_subscribed(int(user_id)):
        raise HTTPException(403, "Необходима подписка на канал")
    user = await firebase_get(f"users/{user_id}")
    if not isinstance(user, dict) or float(user.get("balance", 0)) < amount:
        raise HTTPException(400, "Недостаточно звёзд")
    if int(user_id) != ADMIN_TELEGRAM_ID and int(user.get("referral_count", 0)) < 5:
        raise HTTPException(400, "Необходимо пригласить 5 друзей")
    for rid, r in (await firebase_get("withdrawal_requests") or {}).items():
        if isinstance(r, dict) and r.get("user_id") == user_id and r.get("status") == "pending":
            raise HTTPException(400, "У вас уже есть активная заявка")

    amount_after = round(amount * (1 - WITHDRAW_COMMISSION), 2)
    new_bal = round(float(user.get("balance", 0)) - amount, 2)
    await firebase_patch(f"users/{user_id}", {
        "balance": new_bal,
        "frozen_balance": round(float(user.get("frozen_balance", 0)) + amount, 2)
    })
    tx_id = await log_transaction(user_id, "withdraw", amount, "Вывод звёзд", status="pending")
    req_data = {
        "user_id": user_id,
        "username": user.get("username", "user"),
        "first_name": user.get("first_name", "Player"),
        "amount": amount,
        "amount_after_commission": amount_after,
        "referral_count": user.get("referral_count", 0),
        "status": "pending",
        "tx_id": tx_id,
        "created_at": int(time.time())
    }
    r = await http_client.post(f"{FIREBASE_DB_URL}/withdrawal_requests.json", json=req_data)
    if r.status_code == 200:
        await _notify_admin_withdrawal(r.json().get("name"), req_data)
        return {
            "status": "ok",
            "amount": amount,
            "new_balance": new_bal,
            "amount_after_commission": amount_after
        }
    raise HTTPException(500, "Ошибка создания заявки")


@app.get("/api/user/transactions")
async def api_tx(user_id: int, x_telegram_init_data: str = Header("")):
    uid = get_verified_user_id(x_telegram_init_data)
    txs = await firebase_get(f"transactions/{uid}") or {}
    return {
        "transactions": sorted(
            [{"id": k, **v} for k, v in txs.items() if isinstance(v, dict)],
            key=lambda x: x.get("created_at", 0),
            reverse=True
        ) if isinstance(txs, dict) else []
    }


@app.get("/api/proxy-image")
async def proxy_image(url: str):
    try:
        r = await http_client.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5.0)
        if r.status_code == 200:
            return Response(content=r.content, media_type="image/png")
    except Exception:
        pass
    return Response(status_code=404)


@app.get("/api/user/sync")
async def sync_user(initData: str = Query("")):
    user_data = verify_telegram_init_data(initData)
    if not user_data or not user_data.get("id"):
        raise HTTPException(status_code=401, detail="Откройте приложение через Telegram бота")
    user_id = str(user_data["id"])
    sub = await is_subscribed(int(user_id))
    existing = await firebase_get(f"users/{user_id}")
    if not isinstance(existing, dict) or "balance" not in existing:
        default = {
            "user_id": user_id,
            "username": user_data.get("username", "user"),
            "first_name": user_data.get("first_name", "Player"),
            "photo_url": user_data.get("photo_url", f"https://api.dicebear.com/7.x/bottts/svg?seed={user_id}"),
            "balance": existing.get("balance", 0) if isinstance(existing, dict) else 0,
            "frozen_balance": 0, "total_bets": 0, "wins": 0, "profit": 0,
            "referral_code": user_id, "referred_by": None, "referral_count": 0,
            "subscribed": sub, "agreed_terms": False, "pending_bonus": None
        }
        await firebase_put(f"users/{user_id}", default)
        return {"status": "ok", "user": default, "subscribed": sub}
    return {"status": "ok", "user": existing, "subscribed": sub}


@app.api_route("/api/matches", methods=["GET", "HEAD"])
async def get_matches():
    return {"matches": await firebase_get("matches") or {}}


@app.get("/api/match/{match_id}/markets")
async def get_markets(match_id: str):
    m = await firebase_get(f"matches/{match_id}")
    if isinstance(m, dict):
        return {
            "markets": m.get("markets") or (
                {"Исход": [{"name": "П1", "odds": m["odds"]["1"]}, {"name": "Х", "odds": m["odds"]["X"]}, {"name": "П2", "odds": m["odds"]["2"]}]}
                if m.get("odds", {}).get("1") else {}
            )
        }
    return {"markets": {}}


@app.get("/api/user/bets")
async def get_bets(user_id: int, x_telegram_init_data: str = Header("")):
    uid = get_verified_user_id(x_telegram_init_data)
    b = await firebase_get(f"users/{uid}/bets")
    if not isinstance(b, dict):
        return {"bets": []}
    items = [{"id": k, **v} for k, v in b.items() if isinstance(v, dict)]
    items.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
    return {"bets": items}


@app.post("/api/bets/place")
async def place_bet(request: Request, x_telegram_init_data: str = Header("")):
    user_id = get_verified_user_id(x_telegram_init_data)
    req = await request.json()
    amount = float(req.get("amount", 0))
    bets = req.get("bets", [])
    if amount < 10 or not bets or len(bets) > 15:
        raise HTTPException(400, "Минимум 10 звёзд, максимум 15 событий")
    if not await is_subscribed(int(user_id)):
        raise HTTPException(403, "Подпишитесь на канал")
    user = await firebase_get(f"users/{user_id}")
    if not isinstance(user, dict) or not user.get("agreed_terms"):
        raise HTTPException(403, "Примите пользовательское соглашение в боте")
    if float(user.get("balance", 0)) < amount:
        raise HTTPException(400, "Недостаточно звёзд")

    # Серверная проверка матчей и актуальных коэффициентов
    live_matches = await firebase_get("matches") or {}
    verified_bets = []
    seen_matches = set()

    for b in bets:
        mid = b.get("match_id")
        if not mid or mid in seen_matches:
            raise HTTPException(400, "Дублирующиеся матчи в экспрессе запрещены")
        seen_matches.add(mid)

        m_obj = live_matches.get(mid)
        if not isinstance(m_obj, dict):
            raise HTTPException(400, f"Матч «{b.get('match_title', '')}» закрыт для приёма прогнозов")

        gn = b.get("group_name", "Исход")
        ok_key = b.get("outcome_key") or b.get("outcome")
        server_odds = None
        for out in (m_obj.get("markets") or {}).get(gn, []):
            if out.get("name") == ok_key:
                server_odds = float(out.get("odds", 0))
                break

        if not server_odds or server_odds <= 1.0:
            raise HTTPException(400, f"Исход «{ok_key}» в матче «{m_obj.get('title')}» больше недоступен")

        verified_bets.append({
            "match_id": mid,
            "match_title": m_obj.get("title", b.get("match_title", "")),
            "outcome": ok_key,
            "outcome_key": ok_key,
            "group_name": gn,
            "coefficient": server_odds
        })

    fp = hashlib.md5(f"{user_id}|{amount}|{json.dumps(verified_bets, sort_keys=True)}".encode()).hexdigest()
    for b in (await firebase_get("active_bets") or {}).values():
        if isinstance(b, dict) and time.time() - b.get("timestamp", 0) <= 10:
            old_fp = hashlib.md5(f"{b.get('user_id')}|{b.get('amount')}|{json.dumps(b.get('events', []), sort_keys=True)}".encode()).hexdigest()
            if old_fp == fp:
                raise HTTPException(429, "Прогноз уже оформлен")

    coeff = round(math.prod(float(b["coefficient"]) for b in verified_bets), 2)
    bet_type = "single" if len(verified_bets) == 1 else "express"
    bet_data = {
        "user_id": user_id,
        "type": bet_type,
        "events": verified_bets,
        "amount": amount,
        "total_coefficient": coeff,
        "potential_win": round(amount * coeff, 2),
        "status": "active",
        "timestamp": int(time.time())
    }
    r = await http_client.post(f"{FIREBASE_DB_URL}/bets.json", json=bet_data)
    bet_id = r.json().get("name") if r.status_code == 200 else None
    if bet_id:
        await firebase_put(f"users/{user_id}/bets/{bet_id}", bet_data)
        await firebase_put(f"active_bets/{bet_id}", {
            "user_id": user_id,
            "events": verified_bets,
            "amount": amount,
            "total_coefficient": coeff,
            "timestamp": time.time()
        })
    new_bal = round(float(user.get("balance", 0)) - amount, 2)
    await firebase_patch(f"users/{user_id}", {
        "balance": new_bal,
        "total_bets": int(user.get("total_bets", 0)) + 1
    })
    return {"status": "ok", "new_balance": new_bal, "total_coefficient": coeff}


@app.post("/api/cashier/invoice")
async def create_invoice(request: Request, x_telegram_init_data: str = Header("")):
    user_id = get_verified_user_id(x_telegram_init_data)
    req = await request.json()
    amount = int(req.get("amount", 0))
    if amount < 1 or amount > 100000:
        raise HTTPException(400, "Некорректная сумма")
    try:
        link = await bot.create_invoice_link(
            title="WinStars",
            description=f"Пополнение {amount} ★",
            payload=f"topup_{user_id}_{amount}",
            currency="XTR",
            prices=[LabeledPrice(label="Звёзды", amount=amount)]
        )
        return {"status": "ok", "invoice_link": link}
    except Exception:
        raise HTTPException(500, "Ошибка создания счёта")


@app.post("/api/cashier/promo")
async def activate_promo(request: Request, x_telegram_init_data: str = Header("")):
    user_id = get_verified_user_id(x_telegram_init_data)
    req = await request.json()
    code = (req.get("code") or "").upper().strip()
    promo = await firebase_get(f"promo_codes/{code}")
    if (
        not isinstance(promo, dict)
        or promo.get("expires_at", 0) < time.time()
        or promo.get("used_count", 0) >= promo.get("max_uses", 0)
        or user_id in (promo.get("used_by") or {})
    ):
        raise HTTPException(400, "Промокод недействителен или уже использован")
    user = await firebase_get(f"users/{user_id}")
    if not isinstance(user, dict) or (promo.get("new_users_only") and int(user.get("total_bets", 0)) > 0):
        raise HTTPException(400, "Промокод доступен только новым игрокам")

    await firebase_patch(f"promo_codes/{code}", {
        "used_count": int(promo.get("used_count", 0)) + 1,
        f"used_by/{user_id}": True
    })
    if promo.get("type") == "fixed_stars":
        reward = int(promo.get("reward_stars", 50))
        new_bal = round(float(user.get("balance", 0)) + reward, 2)
        await firebase_patch(f"users/{user_id}", {"balance": new_bal})
        await log_transaction(user_id, "promo", reward, f"Промокод {code}")
        return {"status": "ok", "new_balance": new_bal, "type": "fixed_stars", "amount": reward}
    else:
        await firebase_patch(f"users/{user_id}", {
            "pending_bonus": {
                "code": code,
                "percent": promo.get("deposit_percent", 10),
                "min_deposit": promo.get("min_deposit", 0)
            }
        })
        return {
            "status": "ok",
            "type": "deposit_percent",
            "percent": promo.get("deposit_percent", 10),
            "min_deposit": promo.get("min_deposit", 0)
        }


@app.get("/api/chat/history")
async def get_chat():
    return {"messages": list((await firebase_get("chat_messages") or {}).values())[-50:]}


@app.websocket("/ws/chat")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = json.loads(await websocket.receive_text())
            user_obj = verify_telegram_init_data(data.get("initData", ""))
            if not user_obj:
                continue
            text = str(data.get("text", "")).strip()[:300]
            if not text:
                continue
            clean_msg = {
                "user_id": str(user_obj["id"]),
                "first_name": str(user_obj.get("first_name", "Игрок"))[:32],
                "text": text,
                "ts": int(time.time())
            }
            await http_client.post(f"{FIREBASE_DB_URL}/chat_messages.json", json=clean_msg)
            await manager.broadcast(clean_msg)
    except Exception:
        manager.disconnect(websocket)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("SERVER_PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, workers=1, log_level="warning")
