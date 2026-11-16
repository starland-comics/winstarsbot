import asyncio
import base64
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    WebAppInfo,
)
from curl_cffi.requests import AsyncSession
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("WinStars")

# ================= КОНФИГУРАЦИЯ =================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "WinStarsPlaybot")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://starland-comics.github.io/winstarsbot/")
FIREBASE_URL = os.getenv(
    "FIREBASE_URL", "https://winstars-435f1-default-rtdb.firebaseio.com"
).rstrip("/")
FIREBASE_SECRET = os.getenv("FIREBASE_SECRET", "")
ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

STAR_TO_RUB = 1.5  # 1 ★ = 1.5 ₽
MSK_TZ = timezone(timedelta(hours=3))

LINE_CACHE: Dict[str, Any] = {
    "live": [],
    "prematch": [],
    "by_id": {},
    "updated_at": 0,
}

LOGO_CACHE: Dict[str, str] = {}
LOGO_FAIL_TS: Dict[str, float] = {}
ADMIN_REJECT_STATE: Dict[int, str] = {}

# ЖЁСТКИЙ ФИЛЬТР МУСОРНЫХ, ЖЕНСКИХ, МОЛОДЁЖНЫХ И КИБЕР-ЛИГ
FORBIDDEN_LEAGUE_WORDS = (
    "жен",
    "женщин",
    "women",
    "ladies",
    "fem",
    "кибер",
    "esports",
    "e-football",
    "efootball",
    "fifa",
    "fc 24",
    "fc 25",
    "u14",
    "u15",
    "u16",
    "u17",
    "u18",
    "u19",
    "u20",
    "u21",
    "u22",
    "u23",
    "u-17",
    "u-18",
    "u-19",
    "u-20",
    "u-21",
    "u-23",
    "до 15",
    "до 16",
    "до 17",
    "до 18",
    "до 19",
    "до 20",
    "до 21",
    "до 23",
    "молодеж",
    "молодёж",
    "youth",
    "junior",
    "резерв",
    "дубл",
    "любител",
    "amateur",
    "3x3",
    "4x4",
    "5x5",
    "6x6",
    "7x7",
    "8x8",
    "футзал",
    "futsal",
    "мини-футбол",
    "пляжн",
    "beach",
    "статистика",
    "углов",
    "карточк",
    "штрафн",
    "удары",
    "офсайд",
    "фолы",
    "ауты",
    "сейвы",
    "пенальти",
    "штанги",
)

FORBIDDEN_TEAM_WORDS = (
    "(ж)",
    "(жен)",
    "women",
    "u15",
    "u16",
    "u17",
    "u18",
    "u19",
    "u20",
    "u21",
    "u23",
    "(мол)",
    "молодеж",
    "молодёж",
)

TEAM_EN_DICT = {
    "россия": "Russia",
    "казахстан": "Kazakhstan",
    "словакия": "Slovakia",
    "судан": "Sudan",
    "сенегал": "Senegal",
    "зимбабве": "Zimbabwe",
    "экваториальная гвинея": "Equatorial Guinea",
    "испания": "Spain",
    "франция": "France",
    "германия": "Germany",
    "италия": "Italy",
    "англия": "England",
    "португалия": "Portugal",
    "нидерланды": "Netherlands",
    "бельгия": "Belgium",
    "хорватия": "Croatia",
    "бразилия": "Brazil",
    "аргентина": "Argentina",
    "сербия": "Serbia",
    "турция": "Turkey",
    "польша": "Poland",
    "украина": "Ukraine",
    "беларусь": "Belarus",
    "узбекистан": "Uzbekistan",
    "армения": "Armenia",
    "грузия": "Georgia",
    "азербайджан": "Azerbaijan",
    "зенит": "Zenit",
    "спартак москва": "Spartak Moscow",
    "цска москва": "CSKA Moscow",
    "локомотив москва": "Lokomotiv Moscow",
    "динамо москва": "Dynamo Moscow",
    "краснодар": "Krasnodar",
    "ростов": "Rostov",
    "рубин": "Rubin Kazan",
    "реал мадрид": "Real Madrid",
    "барселона": "Barcelona",
    "атлетико мадрид": "Atletico Madrid",
    "манчестер сити": "Manchester City",
    "манчестер юнайтед": "Manchester United",
    "ливерпуль": "Liverpool",
    "арсенал": "Arsenal",
    "челси": "Chelsea",
    "тоттенхэм": "Tottenham",
    "бавария": "Bayern Munich",
    "боруссия дортмунд": "Borussia Dortmund",
    "байер": "Bayer Leverkusen",
    "псж": "PSG",
    "ювентус": "Juventus",
    "интер": "Inter Milan",
    "милан": "AC Milan",
    "наполи": "Napoli",
    "рома": "Roma",
}


def b64_key(raw: str) -> str:
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def is_forbidden_event(league_name: str, t1: str, t2: str) -> bool:
    l_low = league_name.lower()
    if any(w in l_low for w in FORBIDDEN_LEAGUE_WORDS):
        return True
    t1_low = t1.lower()
    t2_low = t2.lower()
    if any(w in t1_low or w in t2_low for w in FORBIDDEN_TEAM_WORDS):
        return True
    return False


# ================= FIREBASE REALTIME DB =================
class FirebaseDB:
    def __init__(self, base_url: str, secret: str = ""):
        self.base_url = base_url
        self.secret = secret
        self.client = httpx.AsyncClient(timeout=15.0)

    def _url(self, path: str) -> str:
        clean = path.strip("/")
        url = f"{self.base_url}/{clean}.json"
        if self.secret:
            url += f"?auth={self.secret}"
        return url

    async def get(self, path: str) -> Any:
        try:
            r = await self.client.get(self._url(path))
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"Firebase GET {path} error: {e}")
            return None

    async def put(self, path: str, data: Any) -> bool:
        try:
            r = await self.client.put(self._url(path), json=data)
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Firebase PUT {path} error: {e}")
            return False

    async def patch(self, path: str, data: Dict[str, Any]) -> bool:
        try:
            r = await self.client.patch(self._url(path), json=data)
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Firebase PATCH {path} error: {e}")
            return False

    async def push(self, path: str, data: Any) -> Optional[str]:
        try:
            r = await self.client.post(self._url(path), json=data)
            r.raise_for_status()
            res = r.json()
            return res.get("name") if isinstance(res, dict) else None
        except Exception as e:
            logger.error(f"Firebase PUSH {path} error: {e}")
            return None

    async def delete(self, path: str) -> bool:
        try:
            r = await self.client.delete(self._url(path))
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Firebase DELETE {path} error: {e}")
            return False


db = FirebaseDB(FIREBASE_URL, FIREBASE_SECRET)


# ================= СИСТЕМА ЛОГОТИПОВ (FOTMOB + THESPORTSDB + FIREBASE) =================
async def load_logo_cache_from_db():
    saved = await db.get("team_logos")
    if isinstance(saved, dict):
        for k, v in saved.items():
            if isinstance(v, dict) and v.get("url"):
                name = v.get("name") or k
                LOGO_CACHE[name] = v["url"]
            elif isinstance(v, str) and v.startswith("http"):
                LOGO_CACHE[k] = v
    logger.info(f"Loaded {len(LOGO_CACHE)} cached team logos from Firebase")


async def translate_team_to_en(clean_name: str) -> str:
    low = clean_name.lower().strip()
    if low in TEAM_EN_DICT:
        return TEAM_EN_DICT[low]

    if re.fullmatch(r"[A-Za-z0-9\s\.\-&']+", clean_name):
        return clean_name

    try:
        url = (
            "https://translate.googleapis.com/translate_a/single"
            f"?client=gtx&sl=ru&tl=en&dt=t&q={quote(clean_name)}"
        )
        r = await db.client.get(url, timeout=4.0)
        if r.status_code == 200:
            arr = r.json()
            if isinstance(arr, list) and arr and isinstance(arr[0], list):
                translated = "".join(seg[0] for seg in arr[0] if seg and seg[0])
                if translated:
                    return translated.strip()
    except Exception:
        pass
    return clean_name


async def fetch_fotmob_logo(en_name: str) -> str:
    try:
        url = f"https://www.fotmob.com/api/search/suggest?term={quote(en_name)}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
            "Accept": "application/json",
        }
        r = await db.client.get(url, headers=headers, timeout=5.0)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                for group in data:
                    for sug in group.get("suggestions") or []:
                        if sug.get("type") == "team" and sug.get("id"):
                            tid = sug["id"]
                            return f"https://images.fotmob.com/image_resources/logo/teamlogo/{tid}.png"
    except Exception:
        pass
    return ""


async def fetch_sportsdb_logo(en_name: str) -> str:
    try:
        url = f"https://www.thesportsdb.com/api/v1/json/3/searchteams.php?t={quote(en_name)}"
        r = await db.client.get(url, timeout=5.0)
        if r.status_code == 200:
            data = r.json()
            teams = data.get("teams") if isinstance(data, dict) else None
            if teams and isinstance(teams, list):
                return teams[0].get("strBadge") or teams[0].get("strTeamBadge") or ""
    except Exception:
        pass
    return ""


async def resolve_team_logo(team_name: str) -> str:
    clean = re.sub(r"\s*\([^)]*\)", "", team_name).strip()
    if not clean:
        return ""
    if clean in LOGO_CACHE:
        return LOGO_CACHE[clean]

    last_fail = LOGO_FAIL_TS.get(clean, 0)
    if time.time() - last_fail < 600:
        return ""

    en_name = await translate_team_to_en(clean)
    logo_url = await fetch_fotmob_logo(en_name)
    if not logo_url:
        logo_url = await fetch_sportsdb_logo(en_name)
    if not logo_url and en_name != clean:
        logo_url = await fetch_fotmob_logo(clean)

    if logo_url:
        LOGO_CACHE[clean] = logo_url
        await db.put(
            f"team_logos/{b64_key(clean)}",
            {"name": clean, "en": en_name, "url": logo_url},
        )
        return logo_url

    LOGO_FAIL_TS[clean] = time.time()
    return ""


# ================= ПАРСЕР FONBET (МИНУТЫ + РЫНКИ) =================
def format_fonbet_time(ev: dict, misc: dict, is_live: bool) -> str:
    start_ts = ev.get("startTime") or 0
    if not is_live:
        if isinstance(start_ts, (int, float)) and start_ts > 1_000_000_000:
            dt = datetime.fromtimestamp(int(start_ts), tz=MSK_TZ)
            return dt.strftime("%d.%m %H:%M")
        return "Скоро"

    comment = str(
        misc.get("comment") or ev.get("comment") or misc.get("liveStatus") or ""
    ).lower()
    if any(w in comment for w in ("перерыв", "half", "break", "ht")):
        return "Перерыв"

    # 1. timerSeconds из misc или ev
    timer_sec = misc.get("timerSeconds")
    if timer_sec is None:
        timer_sec = ev.get("timerSeconds")
    if isinstance(timer_sec, (int, float)) and timer_sec > 0:
        minute = int(timer_sec) // 60 + 1
        return f"90+{minute - 90}'" if minute > 90 else f"{minute}'"

    # 2. Строковое поле timer ("42:15" или "45+1")
    timer_str = str(misc.get("timer") or ev.get("timer") or "").strip()
    if timer_str:
        m_plus = re.match(r"^(\d+\+\d+)", timer_str)
        if m_plus:
            return f"{m_plus.group(1)}'"
        m_mmss = re.match(r"^(\d+):(\d+)$", timer_str)
        if m_mmss:
            minute = int(m_mmss.group(1)) + 1
            return f"90+{minute - 90}'" if minute > 90 else f"{minute}'"

    # 3. Упоминание минут в комментарии ("1-й тайм 34 мин")
    m_comment = re.search(r"(\d+)\s*мин", comment)
    if m_comment:
        return f"{m_comment.group(1)}'"

    # 4. Расчёт по startTime, если Fonbet не отдал timerSeconds в listBase
    if isinstance(start_ts, (int, float)) and start_ts > 1_000_000_000:
        elapsed_min = int((time.time() - int(start_ts)) // 60)
        if 0 <= elapsed_min <= 46:
            return f"{max(1, elapsed_min)}'"
        if 47 <= elapsed_min <= 61:
            return "Перерыв"
        if 62 <= elapsed_min <= 112:
            game_min = elapsed_min - 15
            return f"90+{game_min - 90}'" if game_min > 90 else f"{game_min}'"

    return "1'"


def parse_fonbet_factors(
    factors_list: List[dict],
) -> Tuple[Dict[str, float], List[Dict[str, Any]]]:
    odds_1x2 = {"P1": 0.0, "X": 0.0, "P2": 0.0}
    dc_outcomes: List[Dict[str, Any]] = []
    totals_outcomes: List[Dict[str, Any]] = []
    handicap_outcomes: List[Dict[str, Any]] = []
    btts_full_outcomes: List[Dict[str, Any]] = []
    btts_1h_outcomes: List[Dict[str, Any]] = []
    other_outcomes: List[Dict[str, Any]] = []
    seen_labels: set[str] = set()

    def add_unique(target: List[Dict[str, Any]], label: str, odd: float):
        if label not in seen_labels:
            seen_labels.add(label)
            target.append({"label": label, "odd": odd})

    for f_obj in factors_list:
        fid = f_obj.get("f")
        val = f_obj.get("v")
        pt = str(f_obj.get("pt") or "").strip()
        if val is None:
            continue
        try:
            odd = round(float(val), 2)
        except (ValueError, TypeError):
            continue
        if odd <= 1.0:
            continue

        if fid == 921:
            odds_1x2["P1"] = odd
        elif fid == 922:
            odds_1x2["X"] = odd
        elif fid == 923:
            odds_1x2["P2"] = odd
        elif fid == 924:
            add_unique(dc_outcomes, "1X", odd)
        elif fid == 1571:
            add_unique(dc_outcomes, "12", odd)
        elif fid == 925:
            add_unique(dc_outcomes, "X2", odd)
        elif fid == 930 and pt:
            add_unique(totals_outcomes, f"ТБ {pt}", odd)
        elif fid == 931 and pt:
            add_unique(totals_outcomes, f"ТМ {pt}", odd)
        elif fid in (927, 937) and pt:
            add_unique(handicap_outcomes, f"Ф1 ({pt})", odd)
        elif fid in (928, 938) and pt:
            add_unique(handicap_outcomes, f"Ф2 ({pt})", odd)
        elif fid == 1805:
            add_unique(btts_full_outcomes, "ОЗ Да", odd)
        elif fid == 1806:
            add_unique(btts_full_outcomes, "ОЗ Нет", odd)
        elif fid == 4241:
            add_unique(btts_1h_outcomes, "1Т: ОЗ Да", odd)
        elif fid == 4242:
            add_unique(btts_1h_outcomes, "1Т: ОЗ Нет", odd)
        else:
            name = str(f_obj.get("name") or "").strip()
            if name:
                label = f"{name} {pt}".strip()
                add_unique(other_outcomes, label, odd)

    markets: List[Dict[str, Any]] = []
    if odds_1x2["P1"] > 1.0 and odds_1x2["P2"] > 1.0:
        main_list = [{"label": "П1", "odd": odds_1x2["P1"]}]
        if odds_1x2["X"] > 1.0:
            main_list.append({"label": "X", "odd": odds_1x2["X"]})
        main_list.append({"label": "П2", "odd": odds_1x2["P2"]})
        markets.append({"title": "Основные исходы", "outcomes": main_list})

    if dc_outcomes:
        markets.append({"title": "Двойной шанс", "outcomes": dc_outcomes})
    if totals_outcomes:
        markets.append({"title": "Тотал голов", "outcomes": totals_outcomes[:8]})
    if handicap_outcomes:
        markets.append({"title": "Фора", "outcomes": handicap_outcomes[:6]})
    if btts_full_outcomes:
        markets.append({"title": "Обе забьют", "outcomes": btts_full_outcomes})
    if btts_1h_outcomes:
        markets.append({"title": "Обе забьют (1-й тайм)", "outcomes": btts_1h_outcomes})
    if other_outcomes:
        markets.append({"title": "Дополнительные рынки", "outcomes": other_outcomes[:10]})

    return odds_1x2, markets


class FonbetScraper:
    MIRRORS = [
        "https://line-lb51.bk6bba-resources.com",
        "https://line-lb52.bk6bba-resources.com",
        "https://line-lb53.bk6bba-resources.com",
        "https://line01w.bk6bba-resources.com",
        "https://line02w.bk6bba-resources.com",
        "https://line-lb51.bkfon-resources.com",
        "https://line-lb52.bkfon-resources.com",
    ]

    def __init__(self):
        self._mirror_idx = 0
        self.headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 14; Pixel 8) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
            ),
        }

    def _ordered_mirrors(self) -> List[str]:
        n = len(self.MIRRORS)
        return [self.MIRRORS[(self._mirror_idx + i) % n] for i in range(n)]

    async def _fetch_json(self, path: str) -> Optional[dict]:
        async with AsyncSession(
            impersonate="chrome120", verify=False, timeout=15
        ) as session:
            for idx, base in enumerate(self._ordered_mirrors()):
                url = f"{base}{path}"
                try:
                    resp = await session.get(url, headers=self.headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        if isinstance(data, dict) and (
                            data.get("events") or data.get("sports")
                        ):
                            self._mirror_idx = (self._mirror_idx + idx) % len(self.MIRRORS)
                            return data
                except Exception as e:
                    logger.warning(f"Mirror {base}{path} failed: {e}")
        return None

    async def fetch_full_line(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        data = await self._fetch_json("/events/listBase?lang=ru&scopeMarket=1600")
        if not data or not data.get("events"):
            data = await self._fetch_json("/events/list?lang=ru&scopeMarket=1600")
        if not data:
            return [], []

        sports_map: Dict[int, dict] = {}
        football_comp_ids: set[int] = {1}

        for s in data.get("sports") or []:
            sid = s.get("id")
            if sid is None:
                continue
            sid_int = int(sid)
            sports_map[sid_int] = s

            parent_id = s.get("parentId")
            parent_ids = s.get("parentIds") or []
            s_name = str(s.get("name") or "").lower()
            if (
                sid_int == 1
                or parent_id == 1
                or 1 in parent_ids
                or s.get("sportCategoryId") == 1
                or s_name == "футбол"
            ):
                football_comp_ids.add(sid_int)

        for s in data.get("sports") or []:
            sid = s.get("id")
            if sid is not None and s.get("parentId") in football_comp_ids:
                football_comp_ids.add(int(sid))

        misc_map: Dict[int, dict] = {}
        for m in data.get("eventMiscs") or []:
            eid = m.get("id")
            if eid is not None:
                misc_map[int(eid)] = m

        factors_map: Dict[int, List[dict]] = {}
        for cf in data.get("customFactors") or []:
            eid = cf.get("e")
            if eid is not None:
                factors_map[int(eid)] = cf.get("factors") or []

        live_matches: List[Dict[str, Any]] = []
        prematch_matches: List[Dict[str, Any]] = []
        now_ts = int(time.time())

        for ev in data.get("events") or []:
            if ev.get("parentId"):
                continue

            sport_id = ev.get("sportId")
            if sport_id is None or int(sport_id) not in football_comp_ids:
                continue

            t1 = str(ev.get("team1") or "").strip()
            t2 = str(ev.get("team2") or "").strip()
            if not t1 or not t2:
                continue

            comp = sports_map.get(int(sport_id))
            league_name = str(comp.get("name") if comp else "Футбол").strip()

            # Жёсткий отсев женских, молодёжных (U15-U23), киберлиг и т.д.
            if is_forbidden_event(league_name, t1, t2):
                continue

            eid = int(ev["id"])
            place = str(ev.get("place") or "line").lower()
            is_live = place == "live"

            f_list = factors_map.get(eid) or []
            odds_1x2, markets = parse_fonbet_factors(f_list)
            if odds_1x2["P1"] <= 1.0 or odds_1x2["P2"] <= 1.0:
                continue

            misc = misc_map.get(eid) or {}
            s1 = int(misc.get("score1") or 0)
            s2 = int(misc.get("score2") or 0)
            time_label = format_fonbet_time(ev, misc, is_live=is_live)

            clean1 = re.sub(r"\s*\([^)]*\)", "", t1).strip()
            clean2 = re.sub(r"\s*\([^)]*\)", "", t2).strip()

            match_obj = {
                "id": str(eid),
                "is_live": is_live,
                "league": league_name,
                "team1": t1,
                "team2": t2,
                "logo1": LOGO_CACHE.get(clean1, ""),
                "logo2": LOGO_CACHE.get(clean2, ""),
                "score1": s1,
                "score2": s2,
                "time_label": time_label,
                "start_time": int(ev.get("startTime") or 0),
                "odds": odds_1x2,
                "markets": markets,
                "updated_at": now_ts,
            }

            if is_live:
                live_matches.append(match_obj)
            else:
                prematch_matches.append(match_obj)

        prematch_matches.sort(key=lambda x: x.get("start_time", 0))
        logger.info(
            f"Fonbet parsed: {len(live_matches)} live, {len(prematch_matches)} prematch"
        )
        return live_matches, prematch_matches[:80]

    async def fetch_event_details(self, match_id: str) -> Optional[Dict[str, Any]]:
        data = await self._fetch_json(
            f"/events/event?lang=ru&eventId={match_id}&scopeMarket=1600"
        )
        if not data:
            return None

        events = data.get("events") or []
        main_ev = next((e for e in events if str(e.get("id")) == str(match_id)), None)
        miscs = data.get("eventMiscs") or []
        misc = next((m for m in miscs if str(m.get("id")) == str(match_id)), {})

        all_factors: List[dict] = []
        for cf in data.get("customFactors") or []:
            if str(cf.get("e")) == str(match_id):
                all_factors.extend(cf.get("factors") or [])

        _, markets = parse_fonbet_factors(all_factors)
        is_live = (main_ev or {}).get("place") == "live"

        return {
            "score1": int(misc.get("score1") or 0),
            "score2": int(misc.get("score2") or 0),
            "time_label": format_fonbet_time(main_ev or {}, misc, is_live=is_live),
            "markets": markets,
        }


scraper = FonbetScraper()


# ================= ДВИЖОК СТАВОК И РАСЧЁТОВ =================
def normalize_bet(bet: dict) -> dict:
    events = bet.get("events")
    first_ev = events[0] if isinstance(events, list) and events else {}

    match_id = str(bet.get("match_id") or first_ev.get("match_id") or "")
    match_title = (
        bet.get("match_title")
        or first_ev.get("match_title")
        or first_ev.get("match")
        or "Событие"
    )
    league = bet.get("league") or first_ev.get("league") or ""
    selection = (
        bet.get("selection")
        or bet.get("outcome")
        or first_ev.get("outcome")
        or first_ev.get("selection")
        or "Исход"
    )
    odd = float(
        bet.get("total_coefficient")
        or bet.get("coefficient")
        or bet.get("odd")
        or bet.get("coef")
        or first_ev.get("coefficient")
        or first_ev.get("odd")
        or 1.0
    )
    amount = float(bet.get("amount") or 0)
    currency = str(bet.get("currency") or "STARS").upper()
    amount_stars = float(
        bet.get("amount_stars")
        or (round(amount / STAR_TO_RUB, 2) if currency == "RUB" else amount)
    )
    status = str(bet.get("status") or "active")
    if status == "pending":
        status = "active"
    if status == "refund":
        status = "void"

    payout = float(bet.get("payout") or 0)
    payout_stars = float(
        bet.get("payout_stars")
        or (round(payout / STAR_TO_RUB, 2) if currency == "RUB" else payout)
    )

    return {
        "match_id": match_id,
        "match_title": match_title,
        "league": league,
        "selection": selection,
        "odd": round(odd, 2),
        "amount": amount,
        "amount_stars": amount_stars,
        "currency": currency,
        "status": status,
        "final_score": bet.get("final_score", ""),
        "payout": payout,
        "payout_stars": payout_stars,
        "created_at": bet.get("created_at") or bet.get("timestamp") or 0,
    }


def evaluate_outcome(selection: str, s1: int, s2: int) -> str:
    sel = selection.strip().upper()
    total = s1 + s2

    if sel in ("П1", "1"):
        return "won" if s1 > s2 else "lost"
    if sel in ("X", "Х"):
        return "won" if s1 == s2 else "lost"
    if sel in ("П2", "2"):
        return "won" if s2 > s1 else "lost"

    if sel == "1X":
        return "won" if s1 >= s2 else "lost"
    if sel == "12":
        return "won" if s1 != s2 else "lost"
    if sel == "X2":
        return "won" if s2 >= s1 else "lost"

    if sel in ("ОЗ ДА", "ОБЕ ЗАБЬЮТ - ДА", "ДА"):
        return "won" if (s1 > 0 and s2 > 0) else "lost"
    if sel in ("ОЗ НЕТ", "ОБЕ ЗАБЬЮТ - НЕТ", "НЕТ"):
        return "won" if (s1 == 0 or s2 == 0) else "lost"

    m_tot = re.match(r"^(ТБ|ТМ)\s*(\d+(?:\.\d+)?)$", sel)
    if m_tot:
        direction, line = m_tot.group(1), float(m_tot.group(2))
        if total == line:
            return "void"
        return (
            ("won" if total > line else "lost")
            if direction == "ТБ"
            else ("won" if total < line else "lost")
        )

    m_hand = re.match(r"^Ф([12])\s*\(?([+-]?\d+(?:\.\d+)?)\)?$", sel)
    if m_hand:
        team_num, h_val = m_hand.group(1), float(m_hand.group(2))
        diff = (s1 + h_val - s2) if team_num == "1" else (s2 + h_val - s1)
        if diff == 0:
            return "void"
        return "won" if diff > 0 else "lost"

    return "active"


async def collect_all_active_bets() -> List[Tuple[str, str, str, Dict[str, Any]]]:
    found: List[Tuple[str, str, str, Dict[str, Any]]] = []
    seen_ids = set()

    users = await db.get("users")
    if isinstance(users, dict):
        for uid, udata in users.items():
            if not isinstance(udata, dict):
                continue
            ubets = udata.get("bets")
            if isinstance(ubets, dict):
                for b_id, raw_bet in ubets.items():
                    if not isinstance(raw_bet, dict):
                        continue
                    norm = normalize_bet(raw_bet)
                    if norm["status"] == "active":
                        found.append((f"users/{uid}/bets/{b_id}", b_id, str(uid), norm))
                        seen_ids.add(b_id)

    active_node = await db.get("active_bets")
    if isinstance(active_node, dict):
        for b_id, raw_bet in active_node.items():
            if b_id in seen_ids or not isinstance(raw_bet, dict):
                continue
            norm = normalize_bet(raw_bet)
            if norm["status"] == "active":
                uid = str(raw_bet.get("user_id", ""))
                path = f"users/{uid}/bets/{b_id}" if uid else f"active_bets/{b_id}"
                found.append((path, b_id, uid, norm))

    return found


async def settle_match_bets(
    target_query: str, s1: int, s2: int, notify_bot: Optional[Bot] = None
) -> int:
    active_list = await collect_all_active_bets()
    q_low = target_query.strip().lower()
    settled_count = 0

    for bet_path, b_id, user_id, norm in active_list:
        by_id = norm["match_id"] == target_query.strip()
        by_name = len(q_low) >= 3 and q_low in norm["match_title"].lower()
        if not (by_id or by_name):
            continue

        result = evaluate_outcome(norm["selection"], s1, s2)
        if result == "active":
            continue

        amount = norm["amount"]
        odd = norm["odd"]
        payout = (
            round(amount * odd, 2)
            if result == "won"
            else (round(amount, 2) if result == "void" else 0.0)
        )

        payout_stars = (
            round(payout / STAR_TO_RUB, 2) if norm["currency"] == "RUB" else payout
        )
        stake_stars = norm["amount_stars"]

        patch_payload = {
            "status": result,
            "final_score": f"{s1}:{s2}",
            "payout": payout,
            "payout_stars": payout_stars,
            "settled_at": int(time.time()),
        }
        await db.patch(bet_path, patch_payload)
        if user_id:
            await db.patch(f"users/{user_id}/bets/{b_id}", patch_payload)
        await db.delete(f"active_bets/{b_id}")

        if user_id:
            user = await db.get(f"users/{user_id}") or {}
            cur_bal = float(user.get("balance", 0))
            won_bets = int(user.get("won_bets", 0)) + (1 if result == "won" else 0)
            net_profit = float(user.get("profit", user.get("net_profit", 0))) + (
                payout_stars - stake_stars
            )

            await db.patch(
                f"users/{user_id}",
                {
                    "balance": round(cur_bal + payout_stars, 2),
                    "won_bets": won_bets,
                    "profit": round(net_profit, 2),
                    "net_profit": round(net_profit, 2),
                },
            )

            if notify_bot and result == "won":
                try:
                    await notify_bot.send_message(
                        int(user_id),
                        f"Ставка сыграла! +{payout_stars:.2f} ★ ({payout_stars * STAR_TO_RUB:.2f} ₽)\n"
                        f"{norm['match_title']} ({s1}:{s2}) — {norm['selection']} @ {odd}",
                    )
                except Exception:
                    pass

        settled_count += 1

    if target_query.isdigit():
        await db.patch(
            f"match_archive/{target_query}",
            {
                "score1": s1,
                "score2": s2,
                "status": "finished",
                "finished_at": int(time.time()),
            },
        )
    return settled_count


async def run_full_recalc(notify_bot: Optional[Bot] = None) -> Dict[str, Any]:
    active_list = await collect_all_active_bets()
    pending_matches: Dict[str, str] = {}

    for _, _, _, norm in active_list:
        key = norm["match_id"] or norm["match_title"]
        if key:
            pending_matches[key] = norm["match_title"]

    if not pending_matches:
        return {"checked": 0, "settled": 0, "unresolved": {}}

    archive = await db.get("match_archive") or {}
    total_settled = 0
    unresolved: Dict[str, str] = {}

    for key, title in pending_matches.items():
        arch_item = archive.get(key) if isinstance(archive, dict) else None
        if isinstance(arch_item, dict):
            if arch_item.get("status") == "finished":
                s1 = int(arch_item.get("score1", 0))
                s2 = int(arch_item.get("score2", 0))
                total_settled += await settle_match_bets(key, s1, s2, notify_bot)
                continue

            last_seen = int(arch_item.get("updated_at", 0))
            last_min_str = str(arch_item.get("time_label", ""))
            m_num = re.search(r"(\d+)", last_min_str)
            last_min = int(m_num.group(1)) if m_num else 0
            if last_min >= 85 and (time.time() - last_seen) > 1500:
                s1 = int(arch_item.get("score1", 0))
                s2 = int(arch_item.get("score2", 0))
                total_settled += await settle_match_bets(key, s1, s2, notify_bot)
                continue

        unresolved[key] = title

    return {
        "checked": len(pending_matches),
        "settled": total_settled,
        "unresolved": unresolved,
    }


# ================= ФОНОВЫЙ ПАРСЕР И ДОГРУЗЧИК ЛОГОТИПОВ =================
async def logo_background_loader(matches: List[Dict[str, Any]]):
    for m in matches[:60]:
        for t_key, l_key in (("team1", "logo1"), ("team2", "logo2")):
            if not m.get(l_key):
                url = await resolve_team_logo(m[t_key])
                if url:
                    m[l_key] = url
                    await asyncio.sleep(0.15)


async def scraper_loop(bot: Bot):
    await load_logo_cache_from_db()
    prev_live_ids: set[str] = set()
    while True:
        try:
            live_list, prematch_list = await scraper.fetch_full_line()

            by_id: Dict[str, Any] = {}
            for m in prematch_list + live_list:
                by_id[m["id"]] = m

            LINE_CACHE["live"] = live_list
            LINE_CACHE["prematch"] = prematch_list
            LINE_CACHE["by_id"] = by_id
            LINE_CACHE["updated_at"] = int(time.time())

            asyncio.create_task(logo_background_loader(live_list + prematch_list))

            current_live_ids = set()
            for m in live_list:
                current_live_ids.add(m["id"])
                await db.patch(
                    f"match_archive/{m['id']}",
                    {
                        "title": f"{m['team1']} - {m['team2']}",
                        "score1": m["score1"],
                        "score2": m["score2"],
                        "time_label": m["time_label"],
                        "updated_at": m["updated_at"],
                    },
                )

            disappeared = prev_live_ids - current_live_ids
            if disappeared:
                await run_full_recalc(bot)

            prev_live_ids = current_live_ids
        except Exception as e:
            logger.error(f"Scraper loop error: {e}")

        await asyncio.sleep(10)


# ================= TELEGRAM БОТ (AIOGRAM 3) =================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()


def is_admin(user_id: int) -> bool:
    return not ADMIN_IDS or user_id in ADMIN_IDS


def withdraw_admin_kb(tx_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Одобрить", callback_data=f"wd_approve:{tx_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить", callback_data=f"wd_reject:{tx_id}"
                ),
            ]
        ]
    )


@router.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject):
    u = message.from_user
    if not u:
        return
    uid = str(u.id)
    existing = await db.get(f"users/{uid}")

    if not existing:
        ref_id = ""
        if command.args and command.args.startswith("ref_"):
            cand = command.args.replace("ref_", "").strip()
            if cand.isdigit() and cand != uid:
                ref_id = cand

        await db.put(
            f"users/{uid}",
            {
                "id": uid,
                "name": u.first_name or "Игрок",
                "username": u.username or "",
                "balance": 0.0,
                "referrals": 0,
                "invited_by": ref_id,
                "total_bets": 0,
                "won_bets": 0,
                "profit": 0.0,
                "created_at": int(time.time()),
            },
        )

        if ref_id:
            inviter = await db.get(f"users/{ref_id}")
            if isinstance(inviter, dict):
                new_refs = int(inviter.get("referrals", 0)) + 1
                bonus_stars = 5.0
                new_bal = round(float(inviter.get("balance", 0)) + bonus_stars, 2)
                await db.patch(
                    f"users/{ref_id}",
                    {"referrals": new_refs, "balance": new_bal},
                )
                try:
                    await bot.send_message(
                        int(ref_id),
                        f"Новый реферал: **{u.first_name}**! Начислено +{bonus_stars} ★",
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass
    else:
        await db.patch(
            f"users/{uid}",
            {"name": u.first_name or existing.get("name", "Игрок"), "username": u.username or ""},
        )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть WinStars",
                    web_app=WebAppInfo(url=WEBAPP_URL),
                )
            ]
        ]
    )
    await message.answer(
        f"Добро пожаловать в **WINSTARS**, {u.first_name}!\n"
        f"Live-линия, ставки в Telegram Stars и рублях, мгновенный расчёт купонов.",
        reply_markup=kb,
        parse_mode="Markdown",
    )


@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(message: Message):
    sp = message.successful_payment
    if not sp or not message.from_user:
        return
    uid = str(message.from_user.id)
    stars_paid = float(sp.total_amount)

    user = await db.get(f"users/{uid}") or {"id": uid, "balance": 0.0}
    new_bal = round(float(user.get("balance", 0)) + stars_paid, 2)
    await db.patch(f"users/{uid}", {"balance": new_bal})

    await db.push(
        "transactions",
        {
            "type": "deposit",
            "method": "Telegram Stars",
            "currency": "STARS",
            "user_id": uid,
            "amount_stars": stars_paid,
            "amount_rub": round(stars_paid * STAR_TO_RUB, 2),
            "status": "completed",
            "created_at": int(time.time()),
        },
    )
    await message.answer(
        f"Баланс пополнен на **{stars_paid:.0f} ★** ({stars_paid * STAR_TO_RUB:.2f} ₽)!",
        parse_mode="Markdown",
    )


# --- МОДЕРАЦИЯ ЗАЯВОК НА ВЫВОД В АДМИН-ПАНЕЛИ ---
@router.message(Command("withdraws"))
async def cmd_withdraws(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    raw = await db.get("transactions")
    if not isinstance(raw, dict):
        await message.answer("Активных заявок на вывод нет.")
        return

    pending = [
        (tid, t)
        for tid, t in raw.items()
        if isinstance(t, dict)
        and t.get("type") == "withdraw"
        and t.get("status") == "pending"
    ]
    if not pending:
        await message.answer("Все заявки на вывод обработаны.")
        return

    for tid, t in pending[:15]:
        uid = str(t.get("user_id", ""))
        cur = str(t.get("currency", "STARS")).upper()
        sum_str = (
            f"{t.get('amount_stars', 0)} ★"
            if cur == "STARS"
            else f"{t.get('amount_rub', 0)} ₽ ({t.get('amount_stars', 0)} ★)"
        )
        await message.answer(
            f"Заявка на вывод `{tid}`\n"
            f"Игрок ID: `{uid}`\n"
            f"Способ: {t.get('method', '')}\n"
            f"Сумма: **{sum_str}**\n"
            f"Реквизиты: {t.get('details', '')}",
            reply_markup=withdraw_admin_kb(tid),
            parse_mode="Markdown",
        )


@router.callback_query(F.data.startswith("wd_approve:"))
async def cb_withdraw_approve(query: CallbackQuery):
    if not query.from_user or not is_admin(query.from_user.id):
        await query.answer("Нет доступа", show_alert=True)
        return

    tx_id = query.data.split(":", 1)[1]
    tx = await db.get(f"transactions/{tx_id}")
    if not isinstance(tx, dict):
        await query.answer("Заявка не найдена", show_alert=True)
        return
    if tx.get("status") != "pending":
        await query.answer(f"Уже обработано ({tx.get('status')})", show_alert=True)
        return

    await db.patch(
        f"transactions/{tx_id}",
        {"status": "completed", "processed_at": int(time.time())},
    )

    uid = str(tx.get("user_id", ""))
    cur = str(tx.get("currency", "STARS")).upper()
    sum_str = (
        f"{tx.get('amount_stars', 0)} ★"
        if cur == "STARS"
        else f"{tx.get('amount_rub', 0)} ₽"
    )

    if uid.isdigit():
        try:
            await bot.send_message(
                int(uid),
                f"✅ Ваша заявка на вывод **{sum_str}** ({tx.get('method', '')}) одобрена и выполнена!",
                parse_mode="Markdown",
            )
        except Exception:
            pass

    if query.message:
        await query.message.edit_text(
            f"{query.message.text}\n\n✅ **ОДОБРЕНО**",
            parse_mode="Markdown",
        )
    await query.answer("Вывод одобрен")


@router.callback_query(F.data.startswith("wd_reject:"))
async def cb_withdraw_reject(query: CallbackQuery):
    if not query.from_user or not is_admin(query.from_user.id):
        await query.answer("Нет доступа", show_alert=True)
        return

    tx_id = query.data.split(":", 1)[1]
    tx = await db.get(f"transactions/{tx_id}")
    if not isinstance(tx, dict) or tx.get("status") != "pending":
        await query.answer("Заявка уже обработана", show_alert=True)
        return

    ADMIN_REJECT_STATE[query.from_user.id] = tx_id
    await query.answer("Напишите причину отклонения")
    if query.message:
        await query.message.reply(
            f"Введите причину отклонения для заявки `{tx_id}` (средства вернутся игроку на баланс):",
            parse_mode="Markdown",
        )


@router.message(Command("set_logo"))
async def cmd_set_logo(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer(
            "Формат: `/set_logo <Название команды> <URL картинки>`",
            parse_mode="Markdown",
        )
        return
    url = parts[-1].strip()
    team_name = " ".join(parts[1:-1]).strip()
    LOGO_CACHE[team_name] = url
    await db.put(
        f"team_logos/{b64_key(team_name)}",
        {"name": team_name, "url": url},
    )
    await message.answer(f"Логотип для **{team_name}** сохранён.", parse_mode="Markdown")


# --- ОСТАЛЬНЫЕ АДМИН-КОМАНДЫ ---
@router.message(Command("add_balance"))
async def cmd_add_balance(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer("Формат: `/add_balance <user_id> <звёзды>`", parse_mode="Markdown")
        return
    uid, amt = parts[1], float(parts[2])
    user = await db.get(f"users/{uid}") or {"id": uid, "balance": 0.0}
    new_bal = round(float(user.get("balance", 0)) + amt, 2)
    await db.patch(f"users/{uid}", {"balance": new_bal})
    await message.answer(f"Игроку `{uid}` добавлено {amt} ★. Баланс: **{new_bal} ★**", parse_mode="Markdown")


@router.message(Command("set_balance"))
async def cmd_set_balance(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer("Формат: `/set_balance <user_id> <звёзды>`", parse_mode="Markdown")
        return
    uid, amt = parts[1], round(float(parts[2]), 2)
    await db.patch(f"users/{uid}", {"balance": amt})
    await message.answer(f"Баланс `{uid}` установлен: **{amt} ★**", parse_mode="Markdown")


@router.message(Command("add_refs"))
async def cmd_add_refs(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer("Формат: `/add_refs <user_id> <кол-во>`", parse_mode="Markdown")
        return
    uid, cnt = parts[1], int(parts[2])
    user = await db.get(f"users/{uid}") or {}
    new_refs = int(user.get("referrals", 0)) + cnt
    await db.patch(f"users/{uid}", {"referrals": new_refs})
    await message.answer(f"Рефералы `{uid}`: **{new_refs}**", parse_mode="Markdown")


@router.message(Command("promo_create"))
async def cmd_promo_create(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) < 4:
        await message.answer(
            "Формат: `/promo_create <КОД> <бонус_в_звёздах> <активаций>`",
            parse_mode="Markdown",
        )
        return
    code = parts[1].strip().upper()
    bonus = float(parts[2])
    uses = int(parts[3])
    await db.put(
        f"promos/{code}",
        {"code": code, "bonus": bonus, "uses_left": uses, "created_at": int(time.time())},
    )
    await message.answer(
        f"Промокод `{code}` создан: **+{bonus} ★** ({uses} активаций).",
        parse_mode="Markdown",
    )


@router.message(Command("promo_list"))
async def cmd_promo_list(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    promos = await db.get("promos")
    if not isinstance(promos, dict) or not promos:
        await message.answer("Активных промокодов нет.")
        return
    lines = ["**Промокоды:**\n"]
    for code, p in promos.items():
        if isinstance(p, dict):
            lines.append(f"• `{code}` — +{p.get('bonus')} ★ (осталось: {p.get('uses_left')})")
    await message.answer("\n".join(lines), parse_mode="Markdown")


@router.message(Command("promo_delete"))
async def cmd_promo_delete(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        return
    code = parts[1].strip().upper()
    await db.delete(f"promos/{code}")
    await message.answer(f"Промокод `{code}` удалён.", parse_mode="Markdown")


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    text = (message.text or "").partition(" ")[2].strip()
    if not text:
        await message.answer("Формат: `/broadcast Текст рассылки`", parse_mode="Markdown")
        return
    users = await db.get("users") or {}
    sent = 0
    for uid in users.keys():
        if uid.isdigit():
            try:
                await bot.send_message(int(uid), text)
                sent += 1
                await asyncio.sleep(0.05)
            except Exception:
                pass
    await message.answer(f"Рассылка завершена. Доставлено: **{sent}**.", parse_mode="Markdown")


@router.message(Command("pending_matches"))
async def cmd_pending_matches(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    active_list = await collect_all_active_bets()
    if not active_list:
        await message.answer("Все ставки рассчитаны.")
        return
    grouped: Dict[str, Dict[str, Any]] = {}
    for _, _, _, norm in active_list:
        key = norm["match_id"] or norm["match_title"].split()[0]
        if key not in grouped:
            grouped[key] = {"title": norm["match_title"], "count": 0}
        grouped[key]["count"] += 1

    lines = ["**Активные нерассчитанные матчи:**\n"]
    for key, info in grouped.items():
        lines.append(f"• {info['title']} ({info['count']} ст.) → `/settle {key} 0:0`")
    await message.answer("\n".join(lines), parse_mode="Markdown")


@router.message(Command("recalc"))
async def cmd_recalc(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    res = await run_full_recalc(bot)
    text = f"Проверено: {res['checked']} | Рассчитано: {res['settled']}"
    if res["unresolved"]:
        text += "\nНе закрыты:\n" + "\n".join(
            f"• `{k}` — {v}" for k, v in res["unresolved"].items()
        )
    await message.answer(text, parse_mode="Markdown")


@router.message(Command("settle", "settle_bet"))
async def cmd_settle(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) < 3 or ":" not in parts[-1]:
        await message.answer("Пример: `/settle Албания 1:3`", parse_mode="Markdown")
        return
    score_part = parts[-1].strip()
    query = " ".join(parts[1:-1]).strip()
    s1_str, s2_str = score_part.split(":")
    s1, s2 = int(s1_str), int(s2_str)
    count = await settle_match_bets(query, s1, s2, bot)
    await message.answer(
        f"Матч **{query}** ({s1}:{s2}) рассчитан. Ставок: **{count}**.",
        parse_mode="Markdown",
    )


@router.message(F.text)
async def handle_admin_reject_reason(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    admin_id = message.from_user.id
    tx_id = ADMIN_REJECT_STATE.pop(admin_id, None)
    if not tx_id:
        return

    reason = (message.text or "").strip()
    tx = await db.get(f"transactions/{tx_id}")
    if not isinstance(tx, dict) or tx.get("status") != "pending":
        await message.answer("Заявка уже была обработана.")
        return

    uid = str(tx.get("user_id", ""))
    refund_stars = float(tx.get("amount_stars", 0))

    await db.patch(
        f"transactions/{tx_id}",
        {
            "status": "rejected",
            "reject_reason": reason,
            "processed_at": int(time.time()),
        },
    )

    if uid:
        user = await db.get(f"users/{uid}") or {"id": uid, "balance": 0.0}
        new_bal = round(float(user.get("balance", 0)) + refund_stars, 2)
        await db.patch(f"users/{uid}", {"balance": new_bal})

        if uid.isdigit():
            try:
                await bot.send_message(
                    int(uid),
                    f"❌ Заявка на вывод отклонена.\n"
                    f"Причина: **{reason}**\n"
                    f"Средства (**{refund_stars:.2f} ★**) возвращены на ваш баланс.",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

    await message.answer(
        f"❌ Заявка `{tx_id}` отклонена. Игроку возвращено **{refund_stars:.2f} ★**.",
        parse_mode="Markdown",
    )


dp.include_router(router)


# ================= FASTAPI + WEBSOCKET =================
@asynccontextmanager
async def lifespan(app: FastAPI):
    task_scraper = asyncio.create_task(scraper_loop(bot))
    task_bot = asyncio.create_task(dp.start_polling(bot))
    yield
    task_scraper.cancel()
    task_bot.cancel()
    await bot.session.close()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class BetRequest(BaseModel):
    user_id: str
    match_id: str
    selection: str
    odd: float
    amount: float
    currency: str = "STARS"


class InvoiceRequest(BaseModel):
    user_id: str
    stars: int


class CashierRequest(BaseModel):
    user_id: str
    amount: float
    currency: str = "STARS"
    method: str = "sbp"
    bank: str = ""
    details: str = ""


class PromoRequest(BaseModel):
    user_id: str
    code: str


class ChatMessageRequest(BaseModel):
    user_id: str = ""
    user: str = "Игрок"
    username: str = ""
    photo_url: str = ""
    text: str


class ChatConnectionManager:
    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, payload: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


chat_manager = ChatConnectionManager()


@app.get("/")
async def health_check():
    return {
        "status": "ok",
        "service": "WinStars API",
        "live": len(LINE_CACHE["live"]),
        "prematch": len(LINE_CACHE["prematch"]),
        "updated_at": LINE_CACHE["updated_at"],
    }


@app.get("/api/init/{user_id}")
async def api_init(
    user_id: str,
    name: str = "Игрок",
    username: str = "",
    photo_url: str = "",
):
    user = await db.get(f"users/{user_id}")
    if not user:
        user = {
            "id": user_id,
            "name": name,
            "username": username,
            "photo_url": photo_url,
            "balance": 0.0,
            "referrals": 0,
            "total_bets": 0,
            "won_bets": 0,
            "profit": 0.0,
            "created_at": int(time.time()),
        }
        await db.put(f"users/{user_id}", user)
    else:
        patch_u: Dict[str, Any] = {}
        if name and name != "Игрок":
            patch_u["name"] = name
        if username:
            patch_u["username"] = username
        if photo_url:
            patch_u["photo_url"] = photo_url
        if patch_u:
            await db.patch(f"users/{user_id}", patch_u)
            user.update(patch_u)

    return {
        "user": user,
        "star_rate": STAR_TO_RUB,
        "bot_username": BOT_USERNAME,
        "live": LINE_CACHE["live"],
        "prematch": LINE_CACHE["prematch"],
        "updated_at": LINE_CACHE["updated_at"],
    }


@app.get("/api/matches")
async def api_matches():
    return {
        "live": LINE_CACHE["live"],
        "prematch": LINE_CACHE["prematch"],
        "updated_at": LINE_CACHE["updated_at"],
    }


@app.get("/api/match/{match_id}/markets")
async def api_match_markets(match_id: str):
    cached = LINE_CACHE["by_id"].get(match_id)
    details = await scraper.fetch_event_details(match_id)

    if details and cached:
        cached["score1"] = details["score1"]
        cached["score2"] = details["score2"]
        cached["time_label"] = details["time_label"]
        if details["markets"]:
            cached["markets"] = details["markets"]

    if not cached:
        raise HTTPException(status_code=404, detail="Матч не найден")
    return cached


@app.post("/api/bet")
async def api_place_bet(req: BetRequest):
    if req.odd <= 1.0:
        raise HTTPException(status_code=400, detail="Некорректный коэффициент")

    cur = req.currency.upper()
    stake_stars = (
        round(req.amount / STAR_TO_RUB, 2) if cur == "RUB" else round(req.amount, 2)
    )
    if stake_stars < 1:
        raise HTTPException(status_code=400, detail="Минимальная ставка 1 ★ (1.5 ₽)")

    user = await db.get(f"users/{req.user_id}")
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    balance = float(user.get("balance", 0))
    if balance < stake_stars:
        raise HTTPException(status_code=400, detail="Недостаточно средств на балансе")

    match = LINE_CACHE["by_id"].get(req.match_id)
    if not match:
        raise HTTPException(status_code=400, detail="Приём ставок приостановлен")

    new_balance = round(balance - stake_stars, 2)
    total_bets = int(user.get("total_bets", 0)) + 1
    match_title = f"{match['team1']} - {match['team2']}"
    potential_win = round(req.amount * req.odd, 2)

    bet_data = {
        "user_id": req.user_id,
        "match_id": req.match_id,
        "match_title": match_title,
        "league": match["league"],
        "selection": req.selection,
        "outcome": req.selection,
        "total_coefficient": round(req.odd, 2),
        "odd": round(req.odd, 2),
        "amount": round(req.amount, 2),
        "amount_stars": stake_stars,
        "currency": cur,
        "potential_win": potential_win,
        "status": "active",
        "created_at": int(time.time()),
    }

    bet_id = await db.push(f"users/{req.user_id}/bets", bet_data)
    if bet_id:
        await db.put(f"active_bets/{bet_id}", bet_data)

    await db.patch(
        f"users/{req.user_id}",
        {"balance": new_balance, "total_bets": total_bets},
    )
    return {
        "ok": True,
        "bet_id": bet_id,
        "balance": new_balance,
        "bet": bet_data,
    }


@app.get("/api/bets/{user_id}")
async def api_user_bets(user_id: str):
    res = []
    seen = set()

    ubets = await db.get(f"users/{user_id}/bets")
    if isinstance(ubets, dict):
        for b_id, raw in ubets.items():
            if isinstance(raw, dict):
                item = normalize_bet(raw)
                item["id"] = b_id
                res.append(item)
                seen.add(b_id)

    root_bets = await db.get("bets")
    if isinstance(root_bets, dict):
        for b_id, raw in root_bets.items():
            if (
                b_id not in seen
                and isinstance(raw, dict)
                and str(raw.get("user_id")) == str(user_id)
            ):
                item = normalize_bet(raw)
                item["id"] = b_id
                res.append(item)

    res.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    return res


@app.post("/api/invoice")
async def api_create_stars_invoice(req: InvoiceRequest):
    if req.stars < 10 or req.stars > 50000:
        raise HTTPException(status_code=400, detail="Минимальное пополнение 10 ★")
    try:
        link = await bot.create_invoice_link(
            title=f"Пополнение WINSTARS ({req.stars} ★)",
            description=f"Зачисление {req.stars} Telegram Stars на игровой баланс",
            payload=f"stars_{req.user_id}_{req.stars}_{int(time.time())}",
            currency="XTR",
            prices=[LabeledPrice(label=f"{req.stars} Stars", amount=req.stars)],
        )
        return {"ok": True, "invoice_url": link}
    except Exception as e:
        logger.error(f"create_invoice_link error: {e}")
        raise HTTPException(status_code=500, detail="Ошибка создания счёта Stars")


@app.post("/api/cashier/deposit")
async def api_cashier_deposit(req: CashierRequest):
    if req.amount < 50:
        raise HTTPException(status_code=400, detail="Минимальное пополнение 50 ₽")
    tx_id = await db.push(
        "transactions",
        {
            "type": "deposit",
            "method": "Рубли",
            "currency": "RUB",
            "user_id": req.user_id,
            "amount_rub": req.amount,
            "amount_stars": round(req.amount / STAR_TO_RUB, 2),
            "status": "pending",
            "created_at": int(time.time()),
        },
    )
    return {
        "ok": True,
        "tx_id": tx_id,
        "message": "Шлюз СБП (Rolly Pay) подключается после активации ключа",
    }


@app.post("/api/cashier/withdraw")
async def api_cashier_withdraw(req: CashierRequest):
    cur = req.currency.upper()
    user = await db.get(f"users/{req.user_id}")
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    if cur == "STARS":
        if req.amount < 1000:
            raise HTTPException(status_code=400, detail="Минимальный вывод 1000 ★")
        stars_amt = round(req.amount, 2)
        rub_amt = round(stars_amt * STAR_TO_RUB, 2)
        method_label = "Telegram Stars"
        tg_username = user.get("username", "")
        details_text = f"@{tg_username}" if tg_username else f"ID {req.user_id}"
        sum_display = f"{stars_amt:.0f} ★"
    else:
        if req.amount < 1500:
            raise HTTPException(status_code=400, detail="Минимальный вывод 1500 ₽")
        if not req.details.strip():
            raise HTTPException(status_code=400, detail="Укажите номер телефона")
        if not req.bank.strip():
            raise HTTPException(status_code=400, detail="Выберите банк для вывода")
        rub_amt = round(req.amount, 2)
        stars_amt = round(rub_amt / STAR_TO_RUB, 2)
        method_label = "Рубли (СБП)"
        details_text = f"{req.bank} • {req.details.strip()}"
        sum_display = f"{rub_amt:.0f} ₽"

    if float(user.get("balance", 0)) < stars_amt:
        raise HTTPException(status_code=400, detail="Недостаточно средств на балансе")

    new_bal = round(float(user["balance"]) - stars_amt, 2)
    await db.patch(f"users/{req.user_id}", {"balance": new_bal})

    tx_id = await db.push(
        "transactions",
        {
            "type": "withdraw",
            "method": method_label,
            "currency": cur,
            "user_id": req.user_id,
            "amount_stars": stars_amt,
            "amount_rub": rub_amt,
            "bank": req.bank,
            "details": details_text,
            "status": "pending",
            "created_at": int(time.time()),
        },
    )

    u_name = user.get("name", "Игрок")
    u_tag = f" (@{user['username']})" if user.get("username") else ""
    admin_sum_str = (
        f"{stars_amt:.0f} ★"
        if cur == "STARS"
        else f"{rub_amt:.0f} ₽ ({stars_amt:.1f} ★)"
    )

    for adm in ADMIN_IDS:
        try:
            await bot.send_message(
                adm,
                f"Заявка на вывод `{tx_id}`\n"
                f"Игрок: {u_name}{u_tag} (`{req.user_id}`)\n"
                f"Способ: {method_label}\n"
                f"Сумма: **{admin_sum_str}**\n"
                f"Реквизиты: {details_text}",
                reply_markup=withdraw_admin_kb(tx_id or ""),
                parse_mode="Markdown",
            )
        except Exception:
            pass

    return {
        "ok": True,
        "tx_id": tx_id or "",
        "balance": new_bal,
        "method": method_label,
        "amount_display": sum_display,
        "details": details_text,
    }


@app.get("/api/transactions/{user_id}")
async def api_transactions(user_id: str):
    raw = await db.get("transactions")
    res = []
    if isinstance(raw, dict):
        for tid, t in raw.items():
            if isinstance(t, dict) and str(t.get("user_id")) == str(user_id):
                item = dict(t)
                item["id"] = tid
                res.append(item)
    res.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    return res[:30]


@app.post("/api/promo/activate")
async def api_promo_activate(req: PromoRequest):
    code = req.code.strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="Введите промокод")

    promo = await db.get(f"promos/{code}")
    if not isinstance(promo, dict):
        raise HTTPException(status_code=404, detail="Промокод не найден")

    uses_left = int(promo.get("uses_left", 0))
    if uses_left <= 0:
        raise HTTPException(status_code=400, detail="Лимит активаций исчерпан")

    used = await db.get(f"promo_used/{req.user_id}/{code}")
    if used:
        raise HTTPException(status_code=400, detail="Вы уже активировали этот промокод")

    bonus = float(promo.get("bonus", 0))
    user = await db.get(f"users/{req.user_id}") or {"id": req.user_id, "balance": 0.0}
    new_bal = round(float(user.get("balance", 0)) + bonus, 2)

    await db.patch(f"users/{req.user_id}", {"balance": new_bal})
    await db.patch(f"promos/{code}", {"uses_left": uses_left - 1})
    await db.put(f"promo_used/{req.user_id}/{code}", int(time.time()))
    await db.push(
        "transactions",
        {
            "type": "deposit",
            "method": "Telegram Stars",
            "currency": "STARS",
            "user_id": req.user_id,
            "amount_stars": bonus,
            "amount_rub": round(bonus * STAR_TO_RUB, 2),
            "status": "completed",
            "created_at": int(time.time()),
        },
    )
    return {"ok": True, "bonus": bonus, "balance": new_bal}


def normalize_chat_msg(m: dict) -> dict:
    user = (
        m.get("user")
        or m.get("username")
        or m.get("first_name")
        or m.get("name")
        or "Игрок"
    )
    text = m.get("text") or m.get("message") or ""
    photo_url = m.get("photo_url") or ""
    raw_t = m.get("time") or m.get("timestamp") or m.get("created_at") or ""
    if isinstance(raw_t, (int, float)) and raw_t > 1_000_000_000:
        t_str = datetime.fromtimestamp(int(raw_t), tz=MSK_TZ).strftime("%H:%M")
    else:
        t_str = str(raw_t)
    return {
        "user_id": str(m.get("user_id") or ""),
        "user": str(user),
        "username": str(m.get("username") or ""),
        "photo_url": str(photo_url),
        "text": str(text),
        "time": t_str,
    }


async def save_and_broadcast_chat(data: dict) -> Optional[dict]:
    text = str(data.get("text", "")).strip()[:300]
    if not text:
        return None
    now_str = datetime.now(MSK_TZ).strftime("%H:%M")
    user_str = str(data.get("user") or "Игрок")[:32]
    username_str = str(data.get("username") or "")[:32]
    photo_url = str(data.get("photo_url") or "")
    user_id = str(data.get("user_id") or "")

    msg_obj = {
        "user_id": user_id,
        "user": user_str,
        "username": username_str,
        "photo_url": photo_url,
        "text": text,
        "time": now_str,
        "timestamp": int(time.time()),
    }
    await db.push("chat_messages", msg_obj)
    clean_msg = {
        "user_id": user_id,
        "user": user_str,
        "username": username_str,
        "photo_url": photo_url,
        "text": text,
        "time": now_str,
    }
    await chat_manager.broadcast({"type": "message", "message": clean_msg})
    return clean_msg


@app.get("/api/chat/history")
async def api_chat_history():
    history = await db.get("chat_messages")
    if not isinstance(history, dict):
        return []
    return [
        normalize_chat_msg(v)
        for v in list(history.values())[-35:]
        if isinstance(v, dict)
    ]


@app.post("/api/chat/send")
async def api_chat_send(req: ChatMessageRequest):
    msg = await save_and_broadcast_chat(req.model_dump())
    if not msg:
        raise HTTPException(status_code=400, detail="Пустое сообщение")
    return {"ok": True, "message": msg}


@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    await chat_manager.connect(ws)
    try:
        history = await db.get("chat_messages")
        if isinstance(history, dict):
            msgs = [
                normalize_chat_msg(v)
                for v in list(history.values())[-35:]
                if isinstance(v, dict)
            ]
            await ws.send_json({"type": "history", "messages": msgs})

        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            await save_and_broadcast_chat(data)
    except WebSocketDisconnect:
        chat_manager.disconnect(ws)
    except Exception:
        chat_manager.disconnect(ws)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
