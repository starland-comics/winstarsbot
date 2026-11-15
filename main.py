import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
import urllib.parse
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple

import httpx
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    WebAppInfo,
)
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

# =====================================================================
# КОНФИГУРАЦИЯ
# =====================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("winstars")

FIREBASE_URL = os.getenv(
    "FIREBASE_URL",
    "https://winstars-435f1-default-rtdb.firebaseio.com",
).rstrip("/")
FIREBASE_SECRET = os.getenv("FIREBASE_SECRET", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://starland-comics.github.io/winstarsbot/")
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "winstars_admin")

LIVE_POLL_SEC = 20
FULL_LINE_MAX_PER_CYCLE = 150
FONBET_LANG = "ru"
FONBET_SCOPE_MARKET = "1600"

FONBET_ENDPOINTS = [
    "https://line01w.bk6bba-resources.com/events/listBase",
    "https://line02w.bk6bba-resources.com/events/listBase",
    "https://line03w.bk6bba-resources.com/events/listBase",
    "https://line04w.bk6bba-resources.com/events/listBase",
]

FONBET_EVENT_VIEW_ENDPOINTS = [
    "https://line01w.bk6bba-resources.com/events/event",
    "https://line02w.bk6bba-resources.com/events/event",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}

# =====================================================================
# ФИЛЬТРЫ ЛИГ И КОМАНД (СТРОГО ПО УТВЕРЖДЁННОМУ СПИСКУ)
# =====================================================================

ALLOWED_LEAGUE_PATTERNS = [
    # 1. Россия (РПЛ, ФНЛ/1-я лига, 2-я лига/дивизион А и Б + все стадии Кубка и Суперкубка)
    r"^россия\.\s*(премьер|рпл|фнл|1-я|1 я|первая|2-я|2 я|вторая|второй дивизион|дивизион|кубок|суперкубок)",
    r"^(?:фонбет\s*)?кубок\s*россии",
    r"^суперкубок\s*россии",

    # 2. Англия (АПЛ, Чемпионшип, Кубок Англии, Кубок лиги, Суперкубок)
    r"^англия\.\s*(премьер(-|\s|\.|$)|чемпионшип|championship|кубок англии|fa cup|кубок лиги|кубок футбольной лиги|efl cup|carabao|суперкубок|community shield)",

    # 3. Испания (Ла Лига/Примера, Сегунда, Кубок, Суперкубок)
    r"^испания\.\s*(ла лига|примера|сегунда|segunda|кубок|copa|суперкубок|supercopa)",

    # 4. Италия (Серия А, Кубок Италии, Суперкубок Италии)
    r"^италия\.\s*(серия а(?:\s|$|\.)|serie a(?:\s|$|\.)|кубок|coppa|суперкубок|supercoppa)",

    # 5. Германия (Бундеслига, Кубок Германии, Суперкубок Германии)
    r"^германия\.\s*(бундеслига(?:\s|$|\.)|bundesliga(?:\s|$|\.)|кубок|dfb|суперкубок)",

    # 6. Франция (Лига 1, Кубок Франции, Суперкубок Франции)
    r"^франция\.\s*(лига 1(?:\s|$|\.)|ligue 1(?:\s|$|\.)|кубок|coupe|суперкубок|troph[eé]e)",

    # 7. Еврокубки
    r"^лига чемпионов",
    r"^лига европы",
    r"^лига конференций",
    r"^суперкубок уефа",

    # 8. Сборные (ЧМ, ЧЕ, Лига наций, Отборы, Копа Америка/КОНМЕБОЛ, Азия/АФК, КОНКАКАФ)
    r"^чемпионат мира",
    r"^чемпионат европы",
    r"^лига наций",
    r"^отборочный",
    r"^сборная",
    r"^(?:кубок|копа)\s*америк",
    r"^copa\s*am[eé]rica",
    r"^конмебол",
    r"^кубок\s*азии",
    r"^(?:кубок|лига наций)\s*конкакаф",
    r"^золотой кубок\s*конкакаф",
    r"^concacaf",

    # 9. Товарищеские матчи (сборных и клубов, если на них есть коэффициенты)
    r"^товарищеск",

    # 10. Саудовская Аравия, США, Турция, Португалия, Нидерланды (+ их кубки и суперкубки)
    r"^саудовская аравия\.\s*(про|премьер|саудовская про|кубок|суперкубок)",
    r"^сша\.\s*(mls|major league soccer|открытый кубок|кубок|суперкубок)",
    r"^кубок лиг(?:\s|$|\.)",
    r"^турция\.\s*(суперлига|s[uü]per|кубок|суперкубок)",
    r"^португалия\.\s*(примейра|primeira|лига португал|кубок|суперкубок|ta[cç]a)",
    r"^(?:нидерланды|голландия)\.\s*(премьер|эредивизи|eredivisie|кубок|суперкубок|knvb)",

    # 11. Беларусь, Казахстан, Украина, Бразилия (+ их кубки и суперкубки) и Кубок Либертадорес
    r"^беларусь\.\s*(высшая|кубок|суперкубок)",
    r"^казахстан\.\s*(премьер|кубок|суперкубок)",
    r"^украина\.\s*(премьер|упл|кубок|суперкубок)",
    r"^бразилия\.\s*(серия a(?:\s|$|\.)|серия а(?:\s|$|\.)|serie a(?:\s|$|\.)|кубок|copa do brasil|суперкубок)",
    r"^кубок либертадорес",
    r"^copa libertadores",
]

ALLOWED_LEAGUE_RES = [re.compile(p, re.IGNORECASE) for p in ALLOWED_LEAGUE_PATTERNS]

FORBIDDEN_LEAGUE_WORDS = [
    # Другие виды спорта и киберспорт
    "гандбол", "handball", "теннис", "tennis", "баскетбол", "basketball",
    "хоккей", "hockey", "волейбол", "volleyball", "регби", "rugby",
    "крикет", "cricket", "американский футбол", "american football", "бейсбол", "baseball",
    "флорбол", "floorball", "пляжный", "кибер", "esports", "e-sports", "csgo", "cs:go", "dota",
    "virtua", "виртуальн", "virtual", "лига про", "h2h",
    "fc 27", "fc27", "fc 26", "fc26", "fc 25", "fc25",
    "futsal", "мини-футбол", "мини футбол",

    # Женские, молодёжные, любительские турниры
    "ncaa", "ncaab", "ncaaf", "женщин", "women", "ladies", "fem", "vrouwen", "дамы", "девуш",
    "юнош", "молодеж", "молодёж", "дубл", "reserve", "academy", "академ",
    "до 15", "до 16", "до 17", "до 18", "до 19", "до 20", "до 21", "до 23",
    "u15", "u16", "u17", "u18", "u19", "u20", "u21", "u23",
    "третий дивизион", "третья лига", "лфк", "amateur", "любител", "лига трофи",
    "региональная лига", "regional league",

    # Неразрешённые низшие дивизионы в разрешённых странах
    "серия b", "серия б", "serie b", "серия c", "серия с", "serie c",
    "2. бундеслига", "2-я бундеслига", "вторая бундеслига", "3. лига", "3-я лига",
    "лига 2", "ligue 2", "национальная лига", "лига 1. англия", "первая лига. англия",
    "вторая лига. англия", "первая лига. украина", "первая лига. беларусь", "первая лига. казахстан",
    "первый дивизион", "второй дивизион. португалия", "сегунда б", "примера рфеф",

    # Долгосрочные аутрайты и спецставки
    "кто выше", "победитель", "призер", "вылет", "квалификация (кто", "кто попадет",
    "выход в", "чемпионство", "выиграет лигу", "финал (кто", "лучший бомбардир",
    "голы за", "регулярный сезон", "плей-офф (кто",
    "итоги турнира", "специальные ставки", "статистика игроков",
    "статистические показатели", "лучший клуб",
]

TEAM_BLACKLIST_RE = re.compile(
    r"(?:^|\s|\()(?:u-?1[5-9]|u-?2[0-3]|до\s*1[5-9]|до\s*2[0-3]|мол|дубль|резерв|жен|ii|iii|b)(?:\s|\)|$)",
    re.IGNORECASE,
)

# =====================================================================
# СЛОВАРИ И АВТОЗАГРУЗКА ЛОГОТИПОВ С УЧЁТОМ СТРАНЫ
# =====================================================================

LOGO_SEARCH_OVERRIDES = {
    ("Алания", "россия"): "Alania Vladikavkaz",
    ("Алания Владикавказ", "россия"): "Alania Vladikavkaz",
    ("Аланьяспор", "турция"): "Alanyaspor",
    ("Торпедо", "россия"): "Torpedo Moscow",
    ("Торпедо Москва", "россия"): "Torpedo Moscow",
    ("Торпедо-БелАЗ", "беларусь"): "Torpedo-BelAZ Zhodino",
    ("Динамо", "россия"): "Dynamo Moscow",
    ("Динамо Москва", "россия"): "Dynamo Moscow",
    ("Динамо Киев", "украина"): "Dynamo Kyiv",
    ("Динамо Минск", "беларусь"): "Dinamo Minsk",
    ("Динамо Махачкала", "россия"): "Dynamo Makhachkala",
    ("Локомотив", "россия"): "Lokomotiv Moscow",
    ("Спартак", "россия"): "Spartak Moscow",
    ("Спартак Кострома", "россия"): "Spartak Kostroma",
    ("ЦСКА", "россия"): "CSKA Moscow",
    ("Арсенал", "россия"): "Arsenal Tula",
    ("Арсенал Тула", "россия"): "Arsenal Tula",
    ("Арсенал", "англия"): "Arsenal",
    ("Ротор", "россия"): "Rotor Volgograd",
    ("Балтика", "россия"): "Baltika Kaliningrad",
    ("Урал", "россия"): "Ural Yekaterinburg",
    ("Сочи", "россия"): "PFC Sochi",
    ("Черноморец", "россия"): "Chernomorets Novorossiysk",
    ("Шинник", "россия"): "Shinnik Yaroslavl",
    ("СКА-Хабаровск", "россия"): "SKA-Khabarovsk",
    ("Енисей", "россия"): "Yenisey Krasnoyarsk",
    ("Нефтехимик", "россия"): "Neftekhimik Nizhnekamsk",
    ("КАМАЗ", "россия"): "KAMAZ Naberezhnye Chelny",
    ("Чайка", "россия"): "Chayka Peschanokopskoye",
    ("Тюмень", "россия"): "FC Tyumen",
    ("Сокол", "россия"): "Sokol Saratov",
    ("Волга Ульяновск", "россия"): "Volga Ulyanovsk",
    ("Челябинск", "россия"): "FC Chelyabinsk",
    ("Велес", "россия"): "Veles Moscow",
    ("Ленинградец", "россия"): "Leningradets",
    ("Машук-КМВ", "россия"): "Mashuk-KMV",
    ("Текстильщик", "россия"): "Tekstilshchik Ivanovo",
    ("Сибирь", "россия"): "Novosibirsk",
    ("Кубань", "россия"): "Kuban Krasnodar",
}

LEAGUE_COUNTRY_HINTS = {
    "россия": "Russia",
    "фонбет кубок россии": "Russia",
    "кубок россии": "Russia",
    "суперкубок россии": "Russia",
    "англия": "England",
    "испания": "Spain",
    "италия": "Italy",
    "германия": "Germany",
    "франция": "France",
    "нидерланды": "Netherlands",
    "голландия": "Netherlands",
    "португалия": "Portugal",
    "бразилия": "Brazil",
    "украина": "Ukraine",
    "казахстан": "Kazakhstan",
    "беларусь": "Belarus",
    "турция": "Turkey",
    "саудовская аравия": "Saudi Arabia",
    "сша": "United States",
}

_LOGO_FETCH_IN_PROGRESS: set = set()
_LOGO_MISS_CACHE: Dict[Tuple[str, str], float] = {}

# =====================================================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ И HTTP-КЛИЕНТ
# =====================================================================

http_client: Optional[httpx.AsyncClient] = None
bot_instance: Optional[Bot] = None

latest_matches_cache: Dict[str, Any] = {
    "status": "ok",
    "ok": True,
    "matches": {},
    "live": [],
    "prematch": [],
    "updated_at": 0,
}
active_websockets: set = set()
chat_websockets: set = set()
force_refresh_event = asyncio.Event()


# =====================================================================
# FIREBASE REST HELPERS
# =====================================================================

def _fb_url(path: str) -> str:
    clean = path.strip("/")
    base = f"{FIREBASE_URL}/{clean}.json"
    if FIREBASE_SECRET:
        return f"{base}?auth={FIREBASE_SECRET}"
    return base


async def firebase_get(path: str) -> Any:
    if not http_client:
        return None
    try:
        r = await http_client.get(_fb_url(path), timeout=10.0)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.warning(f"firebase_get({path}) error: {e}")
    return None


async def firebase_put(path: str, payload: Any) -> bool:
    if not http_client:
        return False
    try:
        r = await http_client.put(_fb_url(path), json=payload, timeout=10.0)
        return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"firebase_put({path}) error: {e}")
        return False


async def firebase_patch(path: str, payload: Dict[str, Any]) -> bool:
    if not http_client:
        return False
    try:
        r = await http_client.patch(_fb_url(path), json=payload, timeout=10.0)
        return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"firebase_patch({path}) error: {e}")
        return False


async def firebase_delete(path: str) -> bool:
    if not http_client:
        return False
    try:
        r = await http_client.delete(_fb_url(path), timeout=10.0)
        return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"firebase_delete({path}) error: {e}")
        return False


# =====================================================================
# АВТОРИЗАЦИЯ TELEGRAM WEBAPP (INITDATA)
# =====================================================================

def _parse_telegram_init_data(init_data_raw: str) -> Optional[Dict[str, Any]]:
    if not init_data_raw:
        return None
    try:
        parsed = urllib.parse.parse_qs(init_data_raw, keep_blank_values=True)
        flat = {k: v[0] for k, v in parsed.items() if v}

        if BOT_TOKEN and "hash" in flat:
            received_hash = flat.pop("hash")
            data_check_string = "\n".join(
                f"{k}={flat[k]}" for k in sorted(flat.keys())
            )
            secret_key = hmac.new(
                b"WebAppData", BOT_TOKEN.encode("utf-8"), hashlib.sha256
            ).digest()
            calculated_hash = hmac.new(
                secret_key, data_check_string.encode("utf-8"), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(calculated_hash, received_hash):
                logger.warning("Несовпадение подписи initData, извлекаем user напрямую")

        user_json = flat.get("user")
        if user_json:
            user_obj = json.loads(user_json)
            if isinstance(user_obj, dict) and user_obj.get("id"):
                user_obj["start_param"] = flat.get("start_param", "")
                return user_obj
    except Exception as e:
        logger.warning(f"Ошибка разбора initData: {e}")
    return None


async def _extract_request_user(request: Request, body: Dict[str, Any]) -> Dict[str, Any]:
    init_data = (
        body.get("initData")
        or body.get("init_data")
        or request.headers.get("X-Telegram-Init-Data")
        or request.query_params.get("initData")
        or ""
    )
    tg_user = _parse_telegram_init_data(str(init_data))
    if tg_user:
        return {
            "id": int(tg_user["id"]),
            "username": str(tg_user.get("username") or ""),
            "first_name": str(tg_user.get("first_name") or ""),
            "photo_url": str(tg_user.get("photo_url") or ""),
            "start_param": str(tg_user.get("start_param") or ""),
        }

    raw_uid = (
        body.get("user_id")
        or body.get("id")
        or request.query_params.get("user_id")
        or request.query_params.get("id")
    )
    if not raw_uid or not str(raw_uid).isdigit():
        raise HTTPException(status_code=400, detail="Не передан user_id или initData")

    return {
        "id": int(raw_uid),
        "username": str(body.get("username") or ""),
        "first_name": str(body.get("first_name") or ""),
        "photo_url": str(body.get("photo_url") or ""),
        "start_param": str(body.get("ref_id") or ""),
    }


def _collect_user_bets(user_data: Dict[str, Any], fallback_bets: Any = None) -> List[Dict[str, Any]]:
    raw_bets = user_data.get("bets")
    if not isinstance(raw_bets, dict):
        raw_bets = fallback_bets if isinstance(fallback_bets, dict) else {}

    bets_list: List[Dict[str, Any]] = []
    for k, v in raw_bets.items():
        if isinstance(v, dict):
            item = {"id": k, **v}
            bets_list.append(item)

    bets_list.sort(
        key=lambda x: int(x.get("timestamp") or x.get("created_at") or 0),
        reverse=True,
    )
    return bets_list


# =====================================================================
# ЛОГИКА ЛОГОТИПОВ
# =====================================================================

def _encode_logo_key(team: str, league: str) -> str:
    raw = f"{team}|{league}".encode("utf-8")
    return "b64_" + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_logo_key(key: str) -> Tuple[str, str]:
    real_k = key
    if key.startswith("b64_"):
        try:
            pad = "=" * (-len(key[4:]) % 4)
            real_k = base64.urlsafe_b64decode(key[4:] + pad).decode("utf-8")
        except Exception:
            return key, ""
    if "|" in real_k:
        team, league = real_k.split("|", 1)
        return team, league
    return real_k, ""


def _detect_country_from_league(league: str) -> str:
    low = (league or "").lower().strip()
    for prefix, country in LEAGUE_COUNTRY_HINTS.items():
        if low.startswith(prefix):
            return country
    return ""


def _build_logo_index(
    raw_cache: Any,
) -> Tuple[Dict[Tuple[str, str], str], Dict[str, Tuple[str, str]]]:
    exact: Dict[Tuple[str, str], str] = {}
    fallback: Dict[str, Tuple[str, str]] = {}
    if not isinstance(raw_cache, dict):
        return exact, fallback

    for k, v in raw_cache.items():
        if not v or not isinstance(v, str) or not v.startswith("http"):
            continue
        team, league = _decode_logo_key(k)
        if not team:
            continue

        if (
            team.lower().startswith("алания")
            and "россия" in league.lower()
            and "alanyaspor" in v.lower()
        ):
            continue

        exact[(team, league)] = v
        if team not in fallback:
            fallback[team] = (v, league)

    return exact, fallback


def _get_team_logo(
    team: str,
    league: str,
    exact_idx: Dict[Tuple[str, str], str],
    fallback_idx: Dict[str, Tuple[str, str]],
) -> str:
    if (team, league) in exact_idx:
        return exact_idx[(team, league)]

    fb = fallback_idx.get(team)
    if fb:
        url, fb_league = fb
        c1 = _detect_country_from_league(league)
        c2 = _detect_country_from_league(fb_league)
        if not c1 or not c2 or c1 == c2:
            return url
    return ""


def _translit_ru_to_en(text: str) -> str:
    table = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
    return "".join(table.get(ch.lower(), ch) for ch in text)


async def _resolve_and_cache_logo(team: str, league: str) -> None:
    if not http_client:
        return
    pair = (team, league)
    if pair in _LOGO_FETCH_IN_PROGRESS:
        return
    if time.time() - _LOGO_MISS_CACHE.get(pair, 0) < 3600:
        return

    _LOGO_FETCH_IN_PROGRESS.add(pair)
    try:
        country_hint = _detect_country_from_league(league)
        country_ru = league.split(".")[0].lower().strip() if "." in league else ""
        query = (
            LOGO_SEARCH_OVERRIDES.get((team, country_ru))
            or LOGO_SEARCH_OVERRIDES.get((team, ""))
            or _translit_ru_to_en(team)
        )

        url = (
            "https://www.thesportsdb.com/api/v1/json/3/searchteams.php?t="
            + urllib.parse.quote(query)
        )
        r = await http_client.get(url, timeout=8.0)
        if r.status_code != 200:
            _LOGO_MISS_CACHE[pair] = time.time()
            return

        teams_list = (r.json() or {}).get("teams") or []
        best_badge = ""
        for item in teams_list:
            if (item.get("strSport") or "").lower() != "soccer":
                continue
            item_country = (item.get("strCountry") or "").strip()
            badge = item.get("strBadge") or item.get("strTeamBadge") or ""
            if not badge:
                continue
            if country_hint and item_country and item_country.lower() != country_hint.lower():
                continue
            best_badge = badge
            break

        if best_badge:
            b64_key = _encode_logo_key(team, league)
            await firebase_patch("team_logos", {b64_key: best_badge})
            logger.info(f"Сохранён логотип: {team} ({league}) -> {best_badge}")
        else:
            _LOGO_MISS_CACHE[pair] = time.time()
    except Exception as e:
        logger.debug(f"Ошибка поиска логотипа для {team} ({league}): {e}")
        _LOGO_MISS_CACHE[pair] = time.time()
    finally:
        _LOGO_FETCH_IN_PROGRESS.discard(pair)


async def cleanup_broken_logos() -> int:
    raw_cache = await firebase_get("team_logos")
    if not isinstance(raw_cache, dict):
        return 0
    removed = 0
    for k, v in raw_cache.items():
        if not isinstance(v, str):
            continue
        team, league = _decode_logo_key(k)
        if (
            team.lower().startswith("алания")
            and "россия" in league.lower()
            and "alanyaspor" in v.lower()
        ):
            await firebase_delete(f"team_logos/{k}")
            removed += 1
    if removed:
        logger.info(f"Очищено ошибочных логотипов в Firebase: {removed}")
    return removed


# =====================================================================
# ПАРСИНГ ЛИНИЙ И LIVE-ДАННЫХ FONBET
# =====================================================================

def _is_allowed_league(league_name: str) -> bool:
    if not league_name:
        return False
    low = league_name.lower().strip()
    for bad in FORBIDDEN_LEAGUE_WORDS:
        if bad in low:
            return False
    return any(rx.search(low) for rx in ALLOWED_LEAGUE_RES)


def _extract_1x2_odds(factors: List[Dict[str, Any]]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    p1: Optional[float] = None
    px: Optional[float] = None
    p2: Optional[float] = None
    for f in factors:
        fid = f.get("f")
        val = f.get("v")
        if not isinstance(val, (int, float)) or val <= 1.0:
            continue
        if fid == 921:
            p1 = round(float(val), 2)
        elif fid == 922:
            px = round(float(val), 2)
        elif fid == 923:
            p2 = round(float(val), 2)
    return p1, px, p2


def _build_grouped_markets(factors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    p1, px, p2 = _extract_1x2_odds(factors)
    dc_1x: Optional[float] = None
    dc_12: Optional[float] = None
    dc_x2: Optional[float] = None
    btts_yes: Optional[float] = None
    btts_no: Optional[float] = None
    totals_over: Dict[str, float] = {}
    totals_under: Dict[str, float] = {}
    handicaps: List[Dict[str, Any]] = []

    for f in factors:
        fid = f.get("f")
        val = f.get("v")
        pt = f.get("pt")
        if not isinstance(val, (int, float)) or val <= 1.0:
            continue
        coef = round(float(val), 2)
        if fid == 924:
            dc_1x = coef
        elif fid == 925:
            dc_12 = coef
        elif fid == 926:
            dc_x2 = coef
        elif fid == 1571:
            btts_yes = coef
        elif fid == 1572:
            btts_no = coef
        elif fid == 930 and pt is not None:
            totals_over[str(pt)] = coef
        elif fid == 931 and pt is not None:
            totals_under[str(pt)] = coef
        elif fid in (927, 928) and pt is not None:
            side = "Ф1" if fid == 927 else "Ф2"
            sign = "+" if float(pt) > 0 else ""
            label = f"{side} ({sign}{pt})"
            handicaps.append({"name": label, "outcome_key": label, "coefficient": coef})

    groups: List[Dict[str, Any]] = []

    main_outcomes = []
    if p1:
        main_outcomes.append({"name": "П1", "outcome_key": "П1", "coefficient": p1})
    if px:
        main_outcomes.append({"name": "Х", "outcome_key": "Х", "coefficient": px})
    if p2:
        main_outcomes.append({"name": "П2", "outcome_key": "П2", "coefficient": p2})
    if main_outcomes:
        groups.append({"group_name": "Исход", "outcomes": main_outcomes})

    dc_outcomes = []
    if dc_1x:
        dc_outcomes.append({"name": "1Х", "outcome_key": "1Х", "coefficient": dc_1x})
    if dc_12:
        dc_outcomes.append({"name": "12", "outcome_key": "12", "coefficient": dc_12})
    if dc_x2:
        dc_outcomes.append({"name": "Х2", "outcome_key": "Х2", "coefficient": dc_x2})
    if dc_outcomes:
        groups.append({"group_name": "Двойной шанс", "outcomes": dc_outcomes})

    all_pts = sorted(
        set(list(totals_over.keys()) + list(totals_under.keys())),
        key=lambda x: float(x) if x.replace(".", "", 1).isdigit() else 999,
    )
    total_outcomes = []
    for pt in all_pts:
        if pt in totals_over:
            total_outcomes.append({"name": f"ТБ {pt}", "outcome_key": f"ТБ {pt}", "coefficient": totals_over[pt]})
        if pt in totals_under:
            total_outcomes.append({"name": f"ТМ {pt}", "outcome_key": f"ТМ {pt}", "coefficient": totals_under[pt]})
    if total_outcomes:
        groups.append({"group_name": "Тотал", "outcomes": total_outcomes})

    if handicaps:
        groups.append({"group_name": "Фора", "outcomes": handicaps[:8]})

    btts_outcomes = []
    if btts_yes:
        btts_outcomes.append({"name": "Да", "outcome_key": "Да", "coefficient": btts_yes})
    if btts_no:
        btts_outcomes.append({"name": "Нет", "outcome_key": "Нет", "coefficient": btts_no})
    if btts_outcomes:
        groups.append({"group_name": "Обе забьют", "outcomes": btts_outcomes})

    return groups


async def _fetch_fonbet_list_base() -> Optional[Dict[str, Any]]:
    if not http_client:
        return None
    params = {
        "lang": FONBET_LANG,
        "scopeMarket": FONBET_SCOPE_MARKET,
    }
    for url in FONBET_ENDPOINTS:
        try:
            r = await http_client.get(url, params=params, timeout=12.0)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict) and data.get("events"):
                    return data
        except Exception as e:
            logger.debug(f"Fonbet mirror {url} failed: {e}")
    return None


async def _fetch_fonbet_event_full(event_id: int) -> Optional[Dict[str, Any]]:
    if not http_client:
        return None
    params = {
        "lang": FONBET_LANG,
        "eventId": event_id,
        "scopeMarket": FONBET_SCOPE_MARKET,
    }
    for url in FONBET_EVENT_VIEW_ENDPOINTS:
        try:
            r = await http_client.get(url, params=params, timeout=8.0)
            if r.status_code == 200:
                return r.json()
        except Exception:
            continue
    return None


def _parse_live_status_and_score(
    event: Dict[str, Any],
    misc: Dict[str, Any],
) -> Tuple[int, int, str]:
    score1 = 0
    score2 = 0

    sc1 = misc.get("score1")
    sc2 = misc.get("score2")
    if isinstance(sc1, int) and isinstance(sc2, int):
        score1, score2 = sc1, sc2
    elif isinstance(event.get("score"), str) and ":" in event["score"]:
        parts = event["score"].split(":", 1)
        if parts[0].strip().isdigit() and parts[1].strip().isdigit():
            score1, score2 = int(parts[0].strip()), int(parts[1].strip())

    timer_sec = misc.get("timerSeconds")
    timer_dir = misc.get("timerDirection", 1)
    comment = (misc.get("comment") or "").strip()

    if "перерыв" in comment.lower() or "ht" in comment.lower():
        status_str = "LIVE · HT"
    elif isinstance(timer_sec, (int, float)) and timer_sec >= 0 and timer_dir != 0:
        mins = int(timer_sec // 60) + 1
        status_str = f"LIVE · {mins}'"
    elif comment:
        status_str = f"LIVE · {comment}"
    else:
        status_str = "LIVE"

    return score1, score2, status_str


async def scrape_once() -> Dict[str, Any]:
    raw_data = await _fetch_fonbet_list_base()
    if not raw_data:
        logger.warning("Не удалось получить listBase от Fonbet")
        return latest_matches_cache

    sports_map: Dict[int, Dict[str, Any]] = {}
    for sp in raw_data.get("sports") or []:
        if sp.get("kind") == "segment":
            sports_map[sp["id"]] = sp

    base_factors_by_event: Dict[int, List[Dict[str, Any]]] = {}
    for cf in raw_data.get("customFactors") or []:
        eid = cf.get("e")
        if eid is not None:
            base_factors_by_event[eid] = cf.get("factors") or []

    misc_by_event: Dict[int, Dict[str, Any]] = {}
    for em in raw_data.get("eventMiscs") or []:
        eid = em.get("id")
        if eid is not None:
            misc_by_event[eid] = em

    raw_logo_cache = await firebase_get("team_logos")
    exact_logo_idx, fallback_logo_idx = _build_logo_index(raw_logo_cache)
    missing_logos: List[Tuple[str, str]] = []
    seen_missing: set = set()

    filtered_events: List[Tuple[Dict[str, Any], str, str, str, bool]] = []

    for ev in raw_data.get("events") or []:
        if ev.get("level") != 1:
            continue

        sport_id = ev.get("sportId")
        sport_obj = sports_map.get(sport_id)
        if not sport_obj:
            continue

        if sport_obj.get("parentId") != 1:
            continue

        league_name = (sport_obj.get("name") or "").strip()
        if not _is_allowed_league(league_name):
            continue

        t1 = (ev.get("team1") or "").strip()
        t2 = (ev.get("team2") or "").strip()
        if not t1 or not t2:
            continue

        if TEAM_BLACKLIST_RE.search(t1) or TEAM_BLACKLIST_RE.search(t2):
            continue

        is_live = (ev.get("place") == "live")
        filtered_events.append((ev, league_name, t1, t2, is_live))

    filtered_events.sort(key=lambda x: (0 if x[4] else 1, x[0].get("startTime") or 0))

    events_needing_full_line: List[int] = []
    for ev, _, _, _, _ in filtered_events:
        eid = ev["id"]
        factors = base_factors_by_event.get(eid, [])
        p1, px, p2 = _extract_1x2_odds(factors)
        if not (p1 and px and p2) and len(events_needing_full_line) < FULL_LINE_MAX_PER_CYCLE:
            events_needing_full_line.append(eid)

    if events_needing_full_line:
        sem = asyncio.Semaphore(12)

        async def _load_full(eid: int) -> Tuple[int, Optional[Dict[str, Any]]]:
            async with sem:
                return eid, await _fetch_fonbet_event_full(eid)

        full_results = await asyncio.gather(
            *[_load_full(eid) for eid in events_needing_full_line],
            return_exceptions=True,
        )
        for res in full_results:
            if isinstance(res, tuple) and res[1]:
                eid, full_payload = res
                for cf in full_payload.get("customFactors") or []:
                    if cf.get("e") == eid:
                        base_factors_by_event[eid] = cf.get("factors") or []
                for em in full_payload.get("eventMiscs") or []:
                    if em.get("id") == eid:
                        misc_by_event[eid] = em

    live_matches: List[Dict[str, Any]] = []
    prematch_matches: List[Dict[str, Any]] = []
    matches_by_id: Dict[str, Dict[str, Any]] = {}

    for ev, league_name, t1, t2, is_live in filtered_events:
        eid = ev["id"]
        full_id = f"fonbet_{eid}"
        factors = base_factors_by_event.get(eid, [])
        p1, px, p2 = _extract_1x2_odds(factors)

        if not (p1 or px or p2):
            continue

        misc = misc_by_event.get(eid, {})
        h_logo = _get_team_logo(t1, league_name, exact_logo_idx, fallback_logo_idx)
        a_logo = _get_team_logo(t2, league_name, exact_logo_idx, fallback_logo_idx)

        if not h_logo and (t1, league_name) not in seen_missing and len(missing_logos) < 20:
            seen_missing.add((t1, league_name))
            missing_logos.append((t1, league_name))
        if not a_logo and (t2, league_name) not in seen_missing and len(missing_logos) < 20:
            seen_missing.add((t2, league_name))
            missing_logos.append((t2, league_name))

        start_ts = int(ev.get("startTime") or 0)
        if is_live:
            s1, s2, status_str = _parse_live_status_and_score(ev, misc)
            score_str = f"{s1}:{s2}"
        else:
            s1, s2 = 0, 0
            status_str = "PREMATCH"
            score_str = "VS"

        safe_t1 = t1.replace("'", "’")
        safe_t2 = t2.replace("'", "’")
        safe_league = league_name.replace("'", "’")

        match_obj: Dict[str, Any] = {
            "id": full_id,
            "raw_id": eid,
            "sport": "football",
            "league": safe_league,
            "home": safe_t1,
            "away": safe_t2,
            "title": f"{safe_t1} - {safe_t2}",
            "homeLogo": h_logo,
            "awayLogo": a_logo,
            "team1": safe_t1,
            "team2": safe_t2,
            "logo1": h_logo,
            "logo2": a_logo,
            "p1": p1,
            "px": px,
            "p2": p2,
            "odds": {
                "1": p1,
                "X": px,
                "2": p2,
            },
            "start_time": start_ts,
            "is_live": is_live,
            "status": status_str,
            "score1": s1,
            "score2": s2,
            "score": score_str,
        }

        matches_by_id[full_id] = match_obj
        if is_live:
            live_matches.append(match_obj)
        else:
            prematch_matches.append(match_obj)

    for t, lg in missing_logos:
        asyncio.create_task(_resolve_and_cache_logo(t, lg))

    payload = {
        "status": "ok",
        "ok": True,
        "matches": matches_by_id,
        "live": live_matches,
        "prematch": prematch_matches,
        "updated_at": int(time.time()),
    }

    latest_matches_cache.update(payload)
    await firebase_put("matches", matches_by_id)
    await firebase_put("matches_feed", payload)
    await _broadcast_ws(active_websockets, {"type": "matches_update", "data": payload})

    logger.info(
        f"Цикл завершён: Live={len(live_matches)}, Prematch={len(prematch_matches)}, "
        f"новых лого в очереди={len(missing_logos)}"
    )
    return payload


async def _broadcast_ws(pool: set, message: Dict[str, Any]) -> None:
    if not pool:
        return
    dead = []
    text = json.dumps(message, ensure_ascii=False)
    for ws in list(pool):
        try:
            await ws.send_text(text)
        except Exception:
            dead.append(ws)
    for ws in dead:
        pool.discard(ws)


async def fonbet_scraper_worker() -> None:
    await cleanup_broken_logos()
    while True:
        try:
            await scrape_once()
        except Exception as e:
            logger.exception(f"Ошибка в fonbet_scraper_worker: {e}")

        force_refresh_event.clear()
        try:
            await asyncio.wait_for(force_refresh_event.wait(), timeout=LIVE_POLL_SEC)
        except asyncio.TimeoutError:
            pass


# =====================================================================
# TELEGRAM БОТ (AIOGRAM 3: /start И TELEGRAM STARS)
# =====================================================================

bot_router = Router()


async def _ensure_user_in_db(
    user_id: int,
    username: str = "",
    first_name: str = "",
    photo_url: str = "",
    ref_id: Optional[int] = None,
) -> Dict[str, Any]:
    uid = str(user_id)
    user_data = await firebase_get(f"users/{uid}")
    now = int(time.time())

    if not isinstance(user_data, dict):
        user_data = {
            "id": user_id,
            "user_id": uid,
            "username": username,
            "first_name": first_name,
            "photo_url": photo_url,
            "balance": 0.0,
            "referrals": 0,
            "referred_by": None,
            "total_bets": 0,
            "won_bets": 0,
            "wins": 0,
            "winrate": 0,
            "profit": 0.0,
            "subscribed": True,
            "created_at": now,
            "last_seen": now,
        }
        if ref_id and ref_id != user_id:
            ref_user = await firebase_get(f"users/{ref_id}")
            if isinstance(ref_user, dict):
                user_data["referred_by"] = ref_id
                new_refs = int(ref_user.get("referrals", 0)) + 1
                await firebase_patch(f"users/{ref_id}", {"referrals": new_refs})
        await firebase_put(f"users/{uid}", user_data)
    else:
        updates: Dict[str, Any] = {"last_seen": now}
        if username:
            updates["username"] = username
        if first_name:
            updates["first_name"] = first_name
        if photo_url:
            updates["photo_url"] = photo_url
        user_data.update(updates)
        await firebase_patch(f"users/{uid}", updates)

    return user_data


@bot_router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    user = message.from_user
    if not user:
        return

    ref_id: Optional[int] = None
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) > 1 and parts[1].isdigit():
        ref_id = int(parts[1])

    await _ensure_user_in_db(
        user_id=user.id,
        username=user.username or "",
        first_name=user.first_name or "",
        ref_id=ref_id,
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚽ Открыть WinStars",
                    web_app=WebAppInfo(url=WEBAPP_URL),
                )
            ]
        ]
    )
    await message.answer(
        f"Привет, <b>{user.first_name}</b>!\n\n"
        "Добро пожаловать в <b>WinStars</b> — виртуальные спортивные прогнозы на Telegram Stars.\n"
        "Нажми кнопку ниже, чтобы открыть линию матчей:",
        parse_mode="HTML",
        reply_markup=kb,
    )


@bot_router.pre_checkout_query()
async def on_pre_checkout(pre_checkout_query: PreCheckoutQuery) -> None:
    await pre_checkout_query.answer(ok=True)


@bot_router.message(F.successful_payment)
async def on_successful_payment(message: Message) -> None:
    sp = message.successful_payment
    user = message.from_user
    if not sp or not user:
        return

    amount = round(float(sp.total_amount), 2)
    uid = str(user.id)
    user_data = await _ensure_user_in_db(user.id, user.username or "", user.first_name or "")
    new_balance = round(float(user_data.get("balance", 0.0)) + amount, 2)

    await firebase_patch(f"users/{uid}", {"balance": new_balance})
    tx_id = str(uuid.uuid4())[:8]
    await firebase_put(
        f"transactions/{uid}/{tx_id}",
        {
            "id": tx_id,
            "type": "deposit",
            "title": "Пополнение Telegram Stars",
            "amount": amount,
            "currency": sp.currency,
            "payload": sp.invoice_payload,
            "created_at": int(time.time()),
        },
    )
    logger.info(f"Пополнение Stars: user={uid}, +{amount} XTR, баланс={new_balance}")
    await message.answer(f"✅ Баланс пополнен на <b>{amount} ⭐</b>!", parse_mode="HTML")


async def run_telegram_bot() -> None:
    global bot_instance
    if not BOT_TOKEN:
        logger.warning("BOT_TOKEN не задан — Telegram-бот не запущен!")
        return

    bot_instance = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(bot_router)
    try:
        await bot_instance.delete_webhook(drop_pending_updates=False)
        logger.info("Telegram-бот (polling) запущен")
        await dp.start_polling(bot_instance, handle_signals=False)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.exception(f"Ошибка в Telegram-боте: {e}")
    finally:
        if bot_instance:
            await bot_instance.session.close()


# =====================================================================
# FASTAPI ПРИЛОЖЕНИЕ И ЭНДПОИНТЫ MINI APP
# =====================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    http_client = httpx.AsyncClient(headers=HEADERS, follow_redirects=True)
    scraper_task = asyncio.create_task(fonbet_scraper_worker())
    bot_task = asyncio.create_task(run_telegram_bot())
    logger.info("WinStars backend запущен")
    try:
        yield
    finally:
        scraper_task.cancel()
        bot_task.cancel()
        if http_client:
            await http_client.aclose()


app = FastAPI(title="WinStars API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "ok": True,
        "bot_active": bot_instance is not None,
        "live_count": len(latest_matches_cache.get("live", [])),
        "prematch_count": len(latest_matches_cache.get("prematch", [])),
        "updated_at": latest_matches_cache.get("updated_at", 0),
    }


@app.api_route("/api/user/sync", methods=["GET", "POST"])
async def sync_user(request: Request) -> Dict[str, Any]:
    body: Dict[str, Any] = {}
    if request.method == "POST":
        try:
            body = await request.json()
        except Exception:
            body = {}

    tg_info = await _extract_request_user(request, body)
    uid_int = tg_info["id"]
    ref_raw = tg_info.get("start_param", "")
    ref_id = int(ref_raw) if ref_raw.isdigit() else None

    user_data = await _ensure_user_in_db(
        user_id=uid_int,
        username=tg_info["username"],
        first_name=tg_info["first_name"],
        photo_url=tg_info["photo_url"],
        ref_id=ref_id,
    )

    fallback_bets = None
    if not isinstance(user_data.get("bets"), dict):
        fallback_bets = await firebase_get(f"bets/{uid_int}")

    bets_list = _collect_user_bets(user_data, fallback_bets)
    total_bets = len(bets_list)
    won_bets = sum(1 for b in bets_list if b.get("status") == "won")
    winrate = (
        int(round((won_bets / total_bets) * 100))
        if total_bets > 0
        else int(user_data.get("winrate", 0))
    )
    bal = round(float(user_data.get("balance", 0.0)), 2)

    user_data["balance"] = bal
    user_data["stars"] = bal
    user_data["total_bets"] = total_bets
    user_data["won_bets"] = won_bets
    user_data["wins"] = won_bets
    user_data["winrate"] = winrate

    return {
        "status": "ok",
        "ok": True,
        "user": user_data,
        "id": uid_int,
        "balance": bal,
        "stars": bal,
        "referrals": int(user_data.get("referrals", 0)),
        "winrate": winrate,
        "total_bets": total_bets,
        "won_bets": won_bets,
        "bets": bets_list,
    }


@app.get("/api/matches")
async def get_matches() -> Dict[str, Any]:
    if latest_matches_cache.get("updated_at", 0) > 0:
        return latest_matches_cache
    fb_data = await firebase_get("matches")
    if isinstance(fb_data, dict):
        return {"status": "ok", "ok": True, "matches": fb_data}
    return latest_matches_cache


@app.get("/api/match/{match_id}/markets")
async def get_match_markets(match_id: str) -> Dict[str, Any]:
    full_id = match_id if match_id.startswith("fonbet_") else f"fonbet_{match_id.replace('fon_', '')}"
    clean_id = full_id.replace("fonbet_", "")
    match_obj = (latest_matches_cache.get("matches") or {}).get(full_id)
    if not match_obj:
        match_obj = await firebase_get(f"matches/{full_id}") or {}

    factors: List[Dict[str, Any]] = []
    if clean_id.isdigit():
        full_payload = await _fetch_fonbet_event_full(int(clean_id))
        if full_payload:
            for cf in full_payload.get("customFactors") or []:
                if str(cf.get("e")) == str(clean_id):
                    factors = cf.get("factors") or []

    grouped_markets = _build_grouped_markets(factors)
    if not grouped_markets and isinstance(match_obj.get("odds"), dict):
        odds = match_obj["odds"]
        fallback_outcomes = []
        for k, label in (("1", "П1"), ("X", "Х"), ("2", "П2")):
            if odds.get(k):
                fallback_outcomes.append({"name": label, "outcome_key": label, "coefficient": odds[k]})
        if fallback_outcomes:
            grouped_markets = [{"group_name": "Исход", "outcomes": fallback_outcomes}]

    return {
        "status": "ok",
        "ok": True,
        "match_id": full_id,
        "match": match_obj,
        "markets": grouped_markets,
    }


@app.get("/api/user/bets")
async def get_user_bets(request: Request) -> Dict[str, Any]:
    tg_info = await _extract_request_user(request, {})
    uid = str(tg_info["id"])
    user_data = await firebase_get(f"users/{uid}") or {}
    fallback_bets = None if isinstance(user_data.get("bets"), dict) else await firebase_get(f"bets/{uid}")
    bets_list = _collect_user_bets(user_data if isinstance(user_data, dict) else {}, fallback_bets)
    return {"status": "ok", "ok": True, "bets": bets_list}


@app.get("/api/user/transactions")
async def get_user_transactions(request: Request) -> Dict[str, Any]:
    tg_info = await _extract_request_user(request, {})
    uid = str(tg_info["id"])
    tx_raw = await firebase_get(f"transactions/{uid}") or {}
    tx_list = list(tx_raw.values()) if isinstance(tx_raw, dict) else []
    tx_list.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    return {"status": "ok", "ok": True, "transactions": tx_list}


@app.post("/api/bets/place")
async def place_bet(request: Request) -> Dict[str, Any]:
    body = await request.json()
    tg_info = await _extract_request_user(request, body)
    uid = str(tg_info["id"])

    amount = round(float(body.get("amount") or 0), 2)
    events = body.get("events")
    bet_type = str(body.get("type") or ("express" if isinstance(events, list) and len(events) > 1 else "single"))

    raw_mid = str(body.get("match_id") or "")
    full_id = raw_mid if raw_mid.startswith("fonbet_") else (f"fonbet_{raw_mid.replace('fon_', '')}" if raw_mid else "")
    outcome = str(body.get("outcome") or body.get("pick") or "")
    odd = round(float(body.get("total_coefficient") or body.get("odd") or body.get("coef") or 0), 2)

    if amount <= 0 or odd <= 1.0:
        raise HTTPException(status_code=400, detail="Некорректная сумма или коэффициент")

    user_data = await firebase_get(f"users/{uid}")
    if not isinstance(user_data, dict):
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    balance = round(float(user_data.get("balance", 0.0)), 2)
    if balance < amount:
        raise HTTPException(status_code=400, detail="Недостаточно звёзд на балансе")

    match_info = (latest_matches_cache.get("matches") or {}).get(full_id) or await firebase_get(f"matches/{full_id}") or {}

    t1 = match_info.get("home", body.get("team1", ""))
    t2 = match_info.get("away", body.get("team2", ""))
    match_title = body.get("match_title") or (f"{t1} - {t2}" if t1 and t2 else "")

    new_balance = round(balance - amount, 2)
    await firebase_patch(f"users/{uid}", {"balance": new_balance})

    now_ts = int(time.time())
    bet_id = f"-W{uuid.uuid4().hex[:14]}"
    bet_obj: Dict[str, Any] = {
        "id": bet_id,
        "user_id": uid,
        "type": bet_type,
        "match_id": full_id,
        "match_title": match_title,
        "team1": t1,
        "team2": t2,
        "league": match_info.get("league", body.get("league", "")),
        "outcome": outcome,
        "coefficient": odd,
        "total_coefficient": odd,
        "amount": amount,
        "potential_win": round(amount * odd, 2),
        "status": "active",
        "timestamp": now_ts,
        "created_at": now_ts,
    }
    if isinstance(events, list) and events:
        bet_obj["events"] = events

    await firebase_put(f"users/{uid}/bets/{bet_id}", bet_obj)
    return {"status": "ok", "ok": True, "balance": new_balance, "stars": new_balance, "bet": bet_obj}


@app.post("/api/cashier/invoice")
async def create_stars_invoice(request: Request) -> Dict[str, Any]:
    if not bot_instance:
        raise HTTPException(status_code=503, detail="Бот не инициализирован (проверьте BOT_TOKEN)")
    body = await request.json()
    tg_info = await _extract_request_user(request, body)
    uid = str(tg_info["id"])
    amount = int(round(float(body.get("amount") or 0)))
    if amount < 1:
        raise HTTPException(status_code=400, detail="Некорректная сумма")

    link = await bot_instance.create_invoice_link(
        title=f"Пополнение баланса WinStars ({amount} ⭐)",
        description=f"Зачисление {amount} Telegram Stars на игровой баланс",
        payload=f"topup_{uid}_{amount}_{int(time.time())}",
        currency="XTR",
        prices=[LabeledPrice(label=f"{amount} Stars", amount=amount)],
    )
    return {"status": "ok", "ok": True, "invoice_link": link, "url": link}


@app.post("/api/withdraw/request")
@app.post("/api/cashier/withdraw")
async def withdraw_stars(request: Request) -> Dict[str, Any]:
    body = await request.json()
    tg_info = await _extract_request_user(request, body)
    uid = str(tg_info["id"])
    amount = round(float(body.get("amount") or 0), 2)
    if amount < 1000:
        raise HTTPException(status_code=400, detail="Минимальный вывод — 1000 звёзд")

    user_data = await firebase_get(f"users/{uid}")
    if not isinstance(user_data, dict):
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    if int(user_data.get("referrals", 0)) < 5:
        raise HTTPException(status_code=400, detail="Для вывода нужно пригласить минимум 5 друзей")

    balance = round(float(user_data.get("balance", 0.0)), 2)
    if balance < amount:
        raise HTTPException(status_code=400, detail="Недостаточно средств")

    new_balance = round(balance - amount, 2)
    payout = round(amount * 0.95, 2)
    await firebase_patch(f"users/{uid}", {"balance": new_balance})

    req_id = str(uuid.uuid4())[:8]
    await firebase_put(
        f"withdrawals/{req_id}",
        {
            "id": req_id,
            "user_id": int(uid),
            "username": user_data.get("username", ""),
            "amount": amount,
            "payout": payout,
            "status": "pending",
            "created_at": int(time.time()),
        },
    )
    await firebase_put(
        f"transactions/{uid}/{req_id}",
        {
            "id": req_id,
            "type": "withdraw",
            "title": "Заявка на вывод Stars",
            "amount": -amount,
            "created_at": int(time.time()),
        },
    )
    return {"status": "ok", "ok": True, "balance": new_balance, "stars": new_balance, "payout": payout}


@app.post("/api/cashier/promo")
@app.post("/api/promo/activate")
async def activate_promo(request: Request) -> Dict[str, Any]:
    body = await request.json()
    tg_info = await _extract_request_user(request, body)
    uid = str(tg_info["id"])
    code = (body.get("code") or "").strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="Введите промокод")

    promo = await firebase_get(f"promos/{code}")
    if not isinstance(promo, dict) or int(promo.get("uses", 0)) <= 0:
        raise HTTPException(status_code=400, detail="Промокод недействителен или закончился")

    used = await firebase_get(f"promo_used/{uid}/{code}")
    if used:
        raise HTTPException(status_code=400, detail="Вы уже активировали этот промокод")

    bonus = round(float(promo.get("bonus", 0)), 2)
    user_data = await _ensure_user_in_db(int(uid))
    new_balance = round(float(user_data.get("balance", 0.0)) + bonus, 2)

    await firebase_patch(f"users/{uid}", {"balance": new_balance})
    await firebase_patch(f"promos/{code}", {"uses": int(promo.get("uses", 1)) - 1})
    await firebase_put(f"promo_used/{uid}/{code}", int(time.time()))
    return {"status": "ok", "ok": True, "bonus": bonus, "balance": new_balance, "stars": new_balance}


@app.get("/api/chat/history")
async def get_chat_history() -> Dict[str, Any]:
    raw = await firebase_get("chat_messages") or {}
    msgs = list(raw.values()) if isinstance(raw, dict) else []
    msgs.sort(key=lambda x: x.get("ts", 0))
    return {"status": "ok", "ok": True, "messages": msgs[-50:]}


@app.post("/api/admin/force-refresh")
async def admin_force_refresh(request: Request) -> Dict[str, Any]:
    secret = request.headers.get("X-Admin-Secret") or request.query_params.get("secret")
    if ADMIN_SECRET and secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    await cleanup_broken_logos()
    force_refresh_event.set()
    return {"status": "refresh_triggered"}


@app.post("/api/admin/set-logo")
async def admin_set_logo(request: Request) -> Dict[str, Any]:
    secret = request.headers.get("X-Admin-Secret") or request.query_params.get("secret")
    if ADMIN_SECRET and secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    body = await request.json()
    team = (body.get("team") or "").strip()
    league = (body.get("league") or "").strip()
    logo_url = (body.get("url") or "").strip()
    if not team or not logo_url.startswith("http"):
        raise HTTPException(status_code=400, detail="Invalid team or url")
    key = _encode_logo_key(team, league)
    ok = await firebase_patch("team_logos", {key: logo_url})
    force_refresh_event.set()
    return {"status": "ok", "ok": ok, "key": key}


@app.websocket("/ws/live")
async def websocket_live(ws: WebSocket) -> None:
    await ws.accept()
    active_websockets.add(ws)
    try:
        if latest_matches_cache.get("updated_at", 0) > 0:
            await ws.send_text(
                json.dumps({"type": "matches_update", "data": latest_matches_cache}, ensure_ascii=False)
            )
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        active_websockets.discard(ws)


@app.websocket("/ws/chat")
async def websocket_chat(ws: WebSocket) -> None:
    await ws.accept()
    chat_websockets.add(ws)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            msg["ts"] = int(time.time())
            msg_id = f"{msg['ts']}_{uuid.uuid4().hex[:6]}"
            await firebase_put(f"chat_messages/{msg_id}", msg)
            await _broadcast_ws(chat_websockets, {"type": "chat_message", "data": msg})
    except WebSocketDisconnect:
        pass
    finally:
        chat_websockets.discard(ws)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
