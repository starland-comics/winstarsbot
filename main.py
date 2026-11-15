import asyncio
import base64
import json
import logging
import os
import re
import time
import urllib.parse
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple

import httpx
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
    "https://winstars-default-rtdb.europe-west1.firebasedatabase.app",
).rstrip("/")
FIREBASE_SECRET = os.getenv("FIREBASE_SECRET", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
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
# ФИЛЬТРЫ ЛИГ И КОМАНД (ИСПРАВЛЕНЫ ЛИГА НАЦИЙ И СБОРНЫЕ)
# =====================================================================

ALLOWED_LEAGUE_PATTERNS = [
    r"^россия\.\s*(премьер|рпл|фнл|1-я|1 я|первая|вторая|2-я|2 я|футбольная национальная|кубок|суперкубок)",
    r"^фонбет\s*кубок\s*россии",
    r"^англия\.\s*(премьер(-|\s|\.|$)|чемпионшип|кубок англии|fa cup|кубок лиги|efl cup|carabao)",
    r"^испания\.\s*(ла лига|примера|сегунда|кубок|copa)",
    r"^италия\.\s*(серия а|серия b|серия б|кубок)",
    r"^германия\.\s*(бундеслига|2\.\s*бундеслига|вторая бундеслига|кубок)",
    r"^франция\.\s*(лига 1|лига 2|кубок)",
    r"^(нидерланды|голландия)\.\s*(премьер|эредивизи|eredivisie|кубок|knvb)",
    r"^португалия\.\s*(примейра|primeira|лига португал|кубок|taça)",
    r"^бразилия\.\s*(серия a|серия а|кубок бразилии|copa do brasil)",
    r"^украина\.\s*(премьер|упл|кубок)",
    r"^казахстан\.\s*(премьер|кубок)",
    r"^беларусь\.\s*(высшая|кубок)",
    r"^саудовская аравия\.\s*(про|премьер|кубок)",
    r"^турция\.\s*(суперлига|süper|кубок)",
    r"^сша\.\s*(mls|major league soccer)",
    r"^лига чемпионов",
    r"^лига европы",
    r"^лига конференций",
    r"^чемпионат европы",
    r"^чемпионат мира",
    r"^лига наций",
    r"^отборочный",
    r"^сборная",
    r"^суперкубок уефа",
    r"^товарищеск",
]

ALLOWED_LEAGUE_RES = [re.compile(p, re.IGNORECASE) for p in ALLOWED_LEAGUE_PATTERNS]

# Убраны короткие куски вроде "a1", "b2", "c3", которые вырезали Лигу наций
FORBIDDEN_LEAGUE_WORDS = [
    "гандбол", "handball", "теннис", "tennis", "баскетбол", "basketball",
    "хоккей", "hockey", "волейбол", "volleyball", "регби", "rugby",
    "крикет", "cricket", "американский футбол", "american football", "бейсбол", "baseball",
    "флорбол", "floorball", "пляжный", "кибер", "esports", "e-sports", "csgo", "cs:go", "dota",
    "virtua", "виртуальн", "virtual", "лига про", "h2h",
    "fc 27", "fc27", "fc 26", "fc26", "fc 25", "fc25",
    "ncaa", "ncaab", "ncaaf", "женщин", "women", "ladies", "fem", "vrouwen", "дамы", "девуш",
    "юнош", "молодеж", "молодёж", "дубл", "reserve", "academy", "академ",
    "до 15", "до 16", "до 17", "до 18", "до 19", "до 20", "до 21", "до 23",
    "u15", "u16", "u17", "u18", "u19", "u20", "u21", "u23",
    "третий дивизион", "третья лига", "amateur", "любител", "лига трофи", "региональн", "regional",
    "futsal", "мини-футбол", "мини футбол",
    "кто выше", "победитель", "призер", "вылет", "квалификация", "кто попадет",
    "выход в", "чемпионство", "выиграет лигу", "финал (кто", "лучший бомбардир",
    "голы за", "регулярный сезон", "плей-офф (кто",
    "итоги турнира", "специальные ставки", "статистика игроков",
    "статистические показатели", "лучший клуб",
]

# Проверка строго по границам слов, чтобы "мол" не банил Молдову, а "жен" — Женеву
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
latest_matches_cache: Dict[str, Any] = {
    "live": [],
    "prematch": [],
    "updated_at": 0,
}
active_websockets: set = set()
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

        # Игнорируем ошибочную привязку турецкого Alanyaspor к российской Алании
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
    """Удаляет из Firebase кривые записи (например, Алания -> Alanyaspor)."""
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


def _extract_totals_and_btts(factors: List[Dict[str, Any]]) -> Dict[str, Any]:
    markets: Dict[str, Any] = {}
    for f in factors:
        fid = f.get("f")
        val = f.get("v")
        pt = f.get("pt")
        if not isinstance(val, (int, float)) or val <= 1.0:
            continue
        if fid == 930 and pt is not None:
            markets[f"to_{pt}"] = round(float(val), 2)
        elif fid == 931 and pt is not None:
            markets[f"tu_{pt}"] = round(float(val), 2)
        elif fid == 1571:
            markets["btts_yes"] = round(float(val), 2)
        elif fid == 1572:
            markets["btts_no"] = round(float(val), 2)
    return markets


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


def _parse_live_timer_and_score(
    event: Dict[str, Any],
    misc: Dict[str, Any],
) -> Tuple[int, int, str, str]:
    score1 = 0
    score2 = 0
    minute_str = ""
    status_str = ""

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
        minute_str = "Перерыв"
        status_str = "HT"
    elif isinstance(timer_sec, (int, float)) and timer_sec >= 0 and timer_dir != 0:
        mins = int(timer_sec // 60) + 1
        minute_str = f"{mins}'"
        status_str = "LIVE"
    elif comment:
        minute_str = comment
        status_str = "LIVE"
    else:
        minute_str = "LIVE"
        status_str = "LIVE"

    return score1, score2, minute_str, status_str


async def scrape_once() -> Dict[str, Any]:
    raw_data = await _fetch_fonbet_list_base()
    if not raw_data:
        logger.warning("Не удалось получить listBase от Fonbet")
        return latest_matches_cache

    sports_map: Dict[int, Dict[str, Any]] = {}
    for sp in raw_data.get("sports") or []:
        if sp.get("kind") == "segment":
            sports_map[sp["id"]] = sp

    # Индекс базовых факторов из listBase
    base_factors_by_event: Dict[int, List[Dict[str, Any]]] = {}
    for cf in raw_data.get("customFactors") or []:
        eid = cf.get("e")
        if eid is not None:
            base_factors_by_event[eid] = cf.get("factors") or []

    # Индекс live-состояний (счёт, таймер, комментарий)
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
        # Только основные матчи (level == 1), без угловых/жёлтых карточек/статистики
        if ev.get("level") != 1:
            continue

        sport_id = ev.get("sportId")
        sport_obj = sports_map.get(sport_id)
        if not sport_obj:
            continue

        # Проверяем, что вид спорта — футбол (parentId == 1)
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

    # Сортируем: сначала Live, затем ближайшие по времени старта
    filtered_events.sort(key=lambda x: (0 if x[4] else 1, x[0].get("startTime") or 0))

    # Определяем, для каких событий нужно догрузить полную линию (если в listBase нет 1X2)
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

    for ev, league_name, t1, t2, is_live in filtered_events:
        eid = ev["id"]
        factors = base_factors_by_event.get(eid, [])
        p1, px, p2 = _extract_1x2_odds(factors)

        # Пропускаем матчи без котировок даже после догрузки полной линии
        if not (p1 or px or p2):
            continue

        extra_markets = _extract_totals_and_btts(factors)
        misc = misc_by_event.get(eid, {})

        h_logo = _get_team_logo(t1, league_name, exact_logo_idx, fallback_logo_idx)
        a_logo = _get_team_logo(t2, league_name, exact_logo_idx, fallback_logo_idx)

        if not h_logo and (t1, league_name) not in seen_missing and len(missing_logos) < 20:
            seen_missing.add((t1, league_name))
            missing_logos.append((t1, league_name))
        if not a_logo and (t2, league_name) not in seen_missing and len(missing_logos) < 20:
            seen_missing.add((t2, league_name))
            missing_logos.append((t2, league_name))

        match_obj: Dict[str, Any] = {
            "id": eid,
            "league": league_name,
            "home": t1,
            "away": t2,
            "home_logo": h_logo,
            "away_logo": a_logo,
            "start_time": ev.get("startTime") or 0,
            "is_live": is_live,
            "odds": {
                "1": p1,
                "X": px,
                "2": p2,
                **extra_markets,
            },
        }

        if is_live:
            s1, s2, minute_str, status_str = _parse_live_timer_and_score(ev, misc)
            match_obj["score1"] = s1
            match_obj["score2"] = s2
            match_obj["minute"] = minute_str
            match_obj["status"] = status_str
            live_matches.append(match_obj)
        else:
            match_obj["score1"] = 0
            match_obj["score2"] = 0
            match_obj["minute"] = ""
            match_obj["status"] = "PREMATCH"
            prematch_matches.append(match_obj)

    if missing_logos:
        asyncio.create_task(
            asyncio.gather(*[_resolve_and_cache_logo(t, lg) for t, lg in missing_logos])
        )

    payload = {
        "live": live_matches,
        "prematch": prematch_matches,
        "updated_at": int(time.time()),
    }

    latest_matches_cache.update(payload)
    await firebase_put("matches_feed", payload)
    await _broadcast_ws({"type": "matches_update", "data": payload})

    logger.info(
        f"Цикл завершён: Live={len(live_matches)}, Prematch={len(prematch_matches)}, "
        f"новых лого в очереди={len(missing_logos)}"
    )
    return payload


async def _broadcast_ws(message: Dict[str, Any]) -> None:
    if not active_websockets:
        return
    dead = []
    text = json.dumps(message, ensure_ascii=False)
    for ws in list(active_websockets):
        try:
            await ws.send_text(text)
        except Exception:
            dead.append(ws)
    for ws in dead:
        active_websockets.discard(ws)


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
# FASTAPI ПРИЛОЖЕНИЕ И ЭНДПОИНТЫ
# =====================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    http_client = httpx.AsyncClient(headers=HEADERS, follow_redirects=True)
    worker_task = asyncio.create_task(fonbet_scraper_worker())
    logger.info("WinStars backend запущен")
    try:
        yield
    finally:
        worker_task.cancel()
        if http_client:
            await http_client.aclose()


app = FastAPI(title="WinStars API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "live_count": len(latest_matches_cache.get("live", [])),
        "prematch_count": len(latest_matches_cache.get("prematch", [])),
        "updated_at": latest_matches_cache.get("updated_at", 0),
    }


@app.get("/api/matches")
async def get_matches() -> Dict[str, Any]:
    if latest_matches_cache.get("updated_at", 0) > 0:
        return latest_matches_cache
    fb_data = await firebase_get("matches_feed")
    if isinstance(fb_data, dict):
        return fb_data
    return latest_matches_cache


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
    """Ручная установка/перезапись логотипа: {"team": "Алания", "league": "Россия. 2-й дивизион", "url": "https://..."}"""
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
    return {"ok": ok, "key": key}


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


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main1:app", host="0.0.0.0", port=port, reload=False)
