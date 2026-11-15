import asyncio
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
from curl_cffi.requests import AsyncSession
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("WinStars")

# ================= БОЕВАЯ КОНФИГУРАЦИЯ =================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
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

MSK_TZ = timezone(timedelta(hours=3))

LINE_CACHE: Dict[str, Any] = {
    "live": [],
    "prematch": [],
    "by_id": {},
    "updated_at": 0,
}


# ================= FIREBASE REALTIME DB CLIENT =================
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


db = FirebaseDB(FIREBASE_URL, FIREBASE_SECRET)


# ================= ПАРСЕР OLIMPBET =================
def format_match_time(ev: dict, is_live: bool) -> str:
    """Извлекает точные минуты матча или статус Перерыв / LIVE."""
    if not is_live:
        start_ts = ev.get("startDate") or ev.get("startTime") or ev.get("time") or 0
        if isinstance(start_ts, (int, float)) and start_ts > 1_000_000_000:
            dt = datetime.fromtimestamp(int(start_ts), tz=MSK_TZ)
            return dt.strftime("%d.%m %H:%M")
        return "Скоро"

    status_text = " ".join(
        str(ev.get(k, ""))
        for k in ("periodName", "statusName", "stage", "timeText", "comment")
    ).lower()

    if any(w in status_text for w in ("перерыв", "half", "break", "ht")) or ev.get("isBreak"):
        return "Перерыв"

    minute: Optional[int] = None
    if ev.get("minute") is not None:
        try:
            minute = int(ev["minute"])
        except (ValueError, TypeError):
            minute = None

    if minute is None:
        raw_time = (
            ev.get("time")
            or ev.get("seconds")
            or ev.get("elapsed")
            or ev.get("timer")
        )
        if isinstance(raw_time, (int, float)) and raw_time > 0:
            minute = int(raw_time) // 60 if raw_time > 130 else int(raw_time)
        elif isinstance(raw_time, str):
            m = re.search(r"(\d+)", raw_time)
            if m:
                val = int(m.group(1))
                minute = val // 60 if ":" not in raw_time and val > 130 else val

    if minute and minute > 0:
        if minute > 90:
            return f"90+{minute - 90}'"
        return f"{minute}'"

    return "LIVE"


def extract_1x2_and_markets(
    ev: dict,
) -> Tuple[Dict[str, float], List[Dict[str, Any]]]:
    """Парсит 1X2 и все доступные доп. рынки события."""
    odds_1x2 = {"P1": 0.0, "X": 0.0, "P2": 0.0}
    markets_grouped: List[Dict[str, Any]] = []

    raw_outcomes = ev.get("outcomes") or []
    raw_markets = ev.get("markets") or []

    if raw_outcomes and not raw_markets:
        for o in raw_outcomes:
            name = str(o.get("name") or o.get("shortName") or "").strip()
            val = float(o.get("value") or o.get("odd") or o.get("k") or 0)
            if val <= 1.0:
                continue
            if name in ("1", "П1"):
                odds_1x2["P1"] = round(val, 2)
            elif name in ("X", "Х", "Н"):
                odds_1x2["X"] = round(val, 2)
            elif name in ("2", "П2"):
                odds_1x2["P2"] = round(val, 2)

    for m in raw_markets:
        m_name = str(m.get("name") or m.get("title") or "Исходы").strip()
        items = []
        for o in m.get("outcomes") or m.get("items") or []:
            o_name = str(o.get("name") or o.get("shortName") or "").strip()
            val = float(o.get("value") or o.get("odd") or o.get("k") or 0)
            if val <= 1.0 or not o_name:
                continue
            items.append({"label": o_name, "odd": round(val, 2)})

            if m_name.lower() in ("исход матча", "основное время", "1x2"):
                if o_name in ("1", "П1"):
                    odds_1x2["P1"] = round(val, 2)
                elif o_name in ("X", "Х"):
                    odds_1x2["X"] = round(val, 2)
                elif o_name in ("2", "П2"):
                    odds_1x2["P2"] = round(val, 2)

        if items:
            markets_grouped.append({"title": m_name, "outcomes": items})

    if not markets_grouped and odds_1x2["P1"] > 1.0 and odds_1x2["P2"] > 1.0:
        p1, x, p2 = odds_1x2["P1"], odds_1x2["X"] or 3.2, odds_1x2["P2"]
        markets_grouped.append(
            {
                "title": "Основные исходы",
                "outcomes": [
                    {"label": "П1", "odd": p1},
                    {"label": "X", "odd": x},
                    {"label": "П2", "odd": p2},
                ],
            }
        )
        dc_1x = round(max(1.05, 1 / (1 / p1 + 1 / x) * 0.94), 2)
        dc_12 = round(max(1.05, 1 / (1 / p1 + 1 / p2) * 0.94), 2)
        dc_x2 = round(max(1.05, 1 / (1 / x + 1 / p2) * 0.94), 2)
        markets_grouped.append(
            {
                "title": "Двойной шанс",
                "outcomes": [
                    {"label": "1X", "odd": dc_1x},
                    {"label": "12", "odd": dc_12},
                    {"label": "X2", "odd": dc_x2},
                ],
            }
        )
        markets_grouped.append(
            {
                "title": "Тотал голов",
                "outcomes": [
                    {"label": "ТБ 1.5", "odd": 1.32},
                    {"label": "ТМ 1.5", "odd": 3.15},
                    {"label": "ТБ 2.5", "odd": 1.92},
                    {"label": "ТМ 2.5", "odd": 1.88},
                    {"label": "ТБ 3.5", "odd": 3.10},
                    {"label": "ТМ 3.5", "odd": 1.34},
                ],
            }
        )
        markets_grouped.append(
            {
                "title": "Обе забьют",
                "outcomes": [
                    {"label": "ОЗ Да", "odd": 1.85},
                    {"label": "ОЗ Нет", "odd": 1.90},
                ],
            }
        )

    return odds_1x2, markets_grouped


def parse_score(ev: dict) -> Tuple[int, int]:
    score_str = str(ev.get("score") or "")
    if ":" in score_str:
        parts = score_str.split(":")
        try:
            return int(parts[0].strip()), int(parts[1].strip())
        except ValueError:
            pass
    s1 = ev.get("score1") if ev.get("score1") is not None else ev.get("homeScore", 0)
    s2 = ev.get("score2") if ev.get("score2") is not None else ev.get("awayScore", 0)
    try:
        return int(s1 or 0), int(s2 or 0)
    except (ValueError, TypeError):
        return 0, 0


class OlimpbetScraper:
    BASE = "https://www.olimp.bet/api/v4/0"

    def __init__(self):
        self.headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Referer": "https://www.olimp.bet/",
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 14; Pixel 8) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
            ),
        }

    async def fetch_events(self, is_live: bool) -> List[Dict[str, Any]]:
        endpoint = (
            f"{self.BASE}/live/sports-with-categories-with-events?v=1"
            if is_live
            else f"{self.BASE}/line/sports-with-categories-with-events?v=1"
        )
        matches: List[Dict[str, Any]] = []
        try:
            async with AsyncSession(impersonate="chrome124", timeout=15) as session:
                resp = await session.get(endpoint, headers=self.headers)
                if resp.status_code != 200:
                    logger.warning(f"Olimpbet HTTP {resp.status_code} for {endpoint}")
                    return []
                data = resp.json()
        except Exception as e:
            logger.error(f"Olimpbet fetch error ({'live' if is_live else 'line'}): {e}")
            return []

        sports = data if isinstance(data, list) else data.get("sports", [])
        for sport in sports:
            s_id = sport.get("id")
            s_name = str(sport.get("name", "")).lower()
            if s_id != 1 and "футбол" not in s_name:
                continue

            for cat in sport.get("categories") or sport.get("champs") or []:
                country = str(cat.get("countryName") or cat.get("country") or "").strip()
                league_raw = str(cat.get("name") or cat.get("title") or "Лига").strip()
                league = (
                    f"{country}. {league_raw}"
                    if country and not league_raw.startswith(country)
                    else league_raw
                )

                for ev in cat.get("events") or []:
                    m_id = str(ev.get("id") or "")
                    if not m_id:
                        continue

                    team1 = str(ev.get("team1") or ev.get("homeName") or "Команда 1").strip()
                    team2 = str(ev.get("team2") or ev.get("awayName") or "Команда 2").strip()
                    logo1 = ev.get("team1Logo") or ev.get("homeLogo") or ""
                    logo2 = ev.get("team2Logo") or ev.get("awayLogo") or ""

                    if logo1 and logo1.startswith("/"):
                        logo1 = f"https://www.olimp.bet{logo1}"
                    if logo2 and logo2.startswith("/"):
                        logo2 = f"https://www.olimp.bet{logo2}"

                    s1, s2 = parse_score(ev)
                    time_label = format_match_time(ev, is_live=is_live)
                    odds_1x2, markets = extract_1x2_and_markets(ev)

                    if odds_1x2["P1"] <= 1.0 and odds_1x2["P2"] <= 1.0:
                        continue

                    matches.append(
                        {
                            "id": m_id,
                            "is_live": is_live,
                            "league": league,
                            "team1": team1,
                            "team2": team2,
                            "logo1": logo1,
                            "logo2": logo2,
                            "score1": s1,
                            "score2": s2,
                            "time_label": time_label,
                            "odds": odds_1x2,
                            "markets": markets,
                            "updated_at": int(time.time()),
                        }
                    )
        return matches

    async def fetch_event_details(
        self, match_id: str, is_live: bool = True
    ) -> Optional[Dict[str, Any]]:
        urls = [
            f"{self.BASE}/live/event?id={match_id}",
            f"{self.BASE}/line/event?id={match_id}",
        ]
        if not is_live:
            urls.reverse()

        try:
            async with AsyncSession(impersonate="chrome124", timeout=12) as session:
                for url in urls:
                    resp = await session.get(url, headers=self.headers)
                    if resp.status_code == 200:
                        ev = resp.json()
                        if isinstance(ev, dict) and ev.get("id"):
                            _, markets = extract_1x2_and_markets(ev)
                            s1, s2 = parse_score(ev)
                            return {
                                "score1": s1,
                                "score2": s2,
                                "time_label": format_match_time(ev, is_live=is_live),
                                "markets": markets,
                            }
        except Exception as e:
            logger.error(f"fetch_event_details {match_id} error: {e}")
        return None

    async def fetch_finished_results(self) -> Dict[str, Tuple[int, int]]:
        """Забирает архив завершённых матчей за сегодня и вчера для /recalc."""
        results_map: Dict[str, Tuple[int, int]] = {}
        now = datetime.now(MSK_TZ)
        dates = [
            now.strftime("%Y-%m-%d"),
            (now - timedelta(days=1)).strftime("%Y-%m-%d"),
        ]
        try:
            async with AsyncSession(impersonate="chrome124", timeout=15) as session:
                for d in dates:
                    url = f"{self.BASE}/results/events?sportId=1&date={d}"
                    resp = await session.get(url, headers=self.headers)
                    if resp.status_code != 200:
                        continue
                    data = resp.json()
                    events = data if isinstance(data, list) else data.get("events", [])
                    for ev in events:
                        m_id = str(ev.get("id") or "")
                        if m_id:
                            results_map[m_id] = parse_score(ev)
        except Exception as e:
            logger.error(f"fetch_finished_results error: {e}")
        return results_map


scraper = OlimpbetScraper()


# ================= ДВИЖОК РАСЧЁТА СТАВОК =================
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

    if sel in ("ОЗ ДА", "ОБЕ ЗАБЬЮТ - ДА"):
        return "won" if (s1 > 0 and s2 > 0) else "lost"
    if sel in ("ОЗ НЕТ", "ОБЕ ЗАБЬЮТ - НЕТ"):
        return "won" if (s1 == 0 or s2 == 0) else "lost"

    m_tot = re.match(r"^(ТБ|ТМ)\s*(\d+(?:\.\d+)?)$", sel)
    if m_tot:
        direction, line_str = m_tot.group(1), m_tot.group(2)
        line = float(line_str)
        if total == line:
            return "refund"
        if direction == "ТБ":
            return "won" if total > line else "lost"
        return "won" if total < line else "lost"

    m_hand = re.match(r"^Ф([12])\s*\(?([+-]?\d+(?:\.\d+)?)\)?$", sel)
    if m_hand:
        team_num, h_str = m_hand.group(1), m_hand.group(2)
        h_val = float(h_str)
        diff = (s1 + h_val - s2) if team_num == "1" else (s2 + h_val - s1)
        if diff == 0:
            return "refund"
        return "won" if diff > 0 else "lost"

    return "pending"


async def collect_all_pending_bets() -> List[Tuple[str, str, Dict[str, Any]]]:
    """
    Собирает все нерассчитанные ставки из users/{uid}/bets и корневого узла bets.
    Возвращает список кортежей (firebase_path, user_id, bet_dict).
    """
    found: List[Tuple[str, str, Dict[str, Any]]] = []

    users = await db.get("users")
    if isinstance(users, dict):
        for uid, udata in users.items():
            if not isinstance(udata, dict):
                continue
            ubets = udata.get("bets")
            if isinstance(ubets, dict):
                for b_id, bet in ubets.items():
                    if isinstance(bet, dict) and bet.get("status") == "pending":
                        found.append((f"users/{uid}/bets/{b_id}", str(uid), bet))

    root_bets = await db.get("bets")
    if isinstance(root_bets, dict):
        for b_id, bet in root_bets.items():
            if isinstance(bet, dict) and bet.get("status") == "pending":
                uid = str(bet.get("user_id", ""))
                found.append((f"bets/{b_id}", uid, bet))

    return found


async def settle_match_bets(
    match_id: str, s1: int, s2: int, notify_bot: Optional[Bot] = None
) -> int:
    """Рассчитывает все pending-ставки по match_id и начисляет выигрыши."""
    pending = await collect_all_pending_bets()
    settled_count = 0

    for bet_path, user_id, bet in pending:
        if str(bet.get("match_id")) != str(match_id):
            continue

        selection = bet.get("selection") or bet.get("outcome") or ""
        result = evaluate_outcome(selection, s1, s2)
        if result == "pending":
            continue

        amount = float(bet.get("amount", 0))
        odd = float(bet.get("odd") or bet.get("coef") or 1.0)

        payout = 0.0
        if result == "won":
            payout = round(amount * odd, 2)
        elif result == "refund":
            payout = round(amount, 2)

        await db.patch(
            bet_path,
            {
                "status": result,
                "final_score": f"{s1}:{s2}",
                "payout": payout,
                "settled_at": int(time.time()),
            },
        )

        if user_id:
            user = await db.get(f"users/{user_id}") or {}
            cur_bal = float(user.get("balance", 0))
            won_bets = int(user.get("won_bets", 0)) + (1 if result == "won" else 0)
            net_profit = float(user.get("net_profit", 0)) + (payout - amount)

            await db.patch(
                f"users/{user_id}",
                {
                    "balance": round(cur_bal + payout, 2),
                    "won_bets": won_bets,
                    "net_profit": round(net_profit, 2),
                },
            )

            if notify_bot and result == "won":
                try:
                    await notify_bot.send_message(
                        int(user_id),
                        f"Ставка сыграла! +{payout:.2f} ₽\n"
                        f"{bet.get('match_title')} ({s1}:{s2}) — {selection} @ {odd}",
                    )
                except Exception:
                    pass

        settled_count += 1

    await db.patch(
        f"match_archive/{match_id}",
        {
            "score1": s1,
            "score2": s2,
            "status": "finished",
            "finished_at": int(time.time()),
        },
    )
    return settled_count


async def run_full_recalc(notify_bot: Optional[Bot] = None) -> Dict[str, Any]:
    pending = await collect_all_pending_bets()
    pending_matches: Dict[str, str] = {}

    for _, _, bet in pending:
        m_id = str(bet.get("match_id", ""))
        if m_id:
            pending_matches[m_id] = bet.get("match_title", f"Матч #{m_id}")

    if not pending_matches:
        return {"checked": 0, "settled": 0, "unresolved": {}}

    results_api = await scraper.fetch_finished_results()
    archive = await db.get("match_archive") or {}

    total_settled = 0
    unresolved: Dict[str, str] = {}

    for m_id, title in pending_matches.items():
        if m_id in results_api:
            s1, s2 = results_api[m_id]
            total_settled += await settle_match_bets(m_id, s1, s2, notify_bot)
            continue

        arch_item = archive.get(m_id) if isinstance(archive, dict) else None
        if isinstance(arch_item, dict):
            if arch_item.get("status") == "finished":
                s1 = int(arch_item.get("score1", 0))
                s2 = int(arch_item.get("score2", 0))
                total_settled += await settle_match_bets(m_id, s1, s2, notify_bot)
                continue

            last_seen = int(arch_item.get("updated_at", 0))
            last_min_str = str(arch_item.get("time_label", ""))
            m_num = re.search(r"(\d+)", last_min_str)
            last_min = int(m_num.group(1)) if m_num else 0
            if last_min >= 85 and (time.time() - last_seen) > 1500:
                s1 = int(arch_item.get("score1", 0))
                s2 = int(arch_item.get("score2", 0))
                total_settled += await settle_match_bets(m_id, s1, s2, notify_bot)
                continue

        unresolved[m_id] = title

    return {
        "checked": len(pending_matches),
        "settled": total_settled,
        "unresolved": unresolved,
    }


# ================= ФОНОВЫЙ СИНХРОНИЗАТОР ЛИНИИ =================
async def scraper_loop(bot: Bot):
    prev_live_ids: set[str] = set()
    while True:
        try:
            live_list, prematch_list = await asyncio.gather(
                scraper.fetch_events(is_live=True),
                scraper.fetch_events(is_live=False),
            )

            by_id: Dict[str, Any] = {}
            for m in prematch_list + live_list:
                by_id[m["id"]] = m

            LINE_CACHE["live"] = live_list
            LINE_CACHE["prematch"] = prematch_list
            LINE_CACHE["by_id"] = by_id
            LINE_CACHE["updated_at"] = int(time.time())

            current_live_ids = set()
            for m in live_list:
                current_live_ids.add(m["id"])
                await db.patch(
                    f"match_archive/{m['id']}",
                    {
                        "title": f"{m['team1']} — {m['team2']}",
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

        await asyncio.sleep(12)


# ================= TELEGRAM БОТ (AIOGRAM 3) =================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()


def is_admin(user_id: int) -> bool:
    return not ADMIN_IDS or user_id in ADMIN_IDS


@router.message(Command("start"))
async def cmd_start(message: Message):
    u = message.from_user
    if not u:
        return
    uid = str(u.id)
    existing = await db.get(f"users/{uid}")
    if not existing:
        await db.put(
            f"users/{uid}",
            {
                "id": uid,
                "name": u.first_name or "Игрок",
                "username": u.username or "",
                "balance": 0.0,
                "total_bets": 0,
                "won_bets": 0,
                "net_profit": 0.0,
                "created_at": int(time.time()),
            },
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
        f"Live-линия, мгновенный расчёт купонов и статистика в одном приложении.",
        reply_markup=kb,
        parse_mode="Markdown",
    )


@router.message(Command("pending_matches"))
async def cmd_pending_matches(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return

    pending = await collect_all_pending_bets()
    if not pending:
        await message.answer("Все ставки рассчитаны, зависших матчей нет.")
        return

    grouped: Dict[str, Dict[str, Any]] = {}
    for _, _, bet in pending:
        m_id = str(bet.get("match_id", ""))
        if m_id not in grouped:
            grouped[m_id] = {
                "title": bet.get("match_title", "Неизвестный матч"),
                "count": 0,
                "sum": 0.0,
            }
        grouped[m_id]["count"] += 1
        grouped[m_id]["sum"] += float(bet.get("amount", 0))

    lines = ["**Нерассчитанные матчи:**\n"]
    for m_id, info in grouped.items():
        lines.append(
            f"• `{m_id}` — {info['title']} ({info['count']} ст. на {info['sum']:.0f} ₽)\n"
            f"  Закрыть: `/settle {m_id} 0:0`"
        )
    await message.answer("\n".join(lines), parse_mode="Markdown")


@router.message(Command("recalc"))
async def cmd_recalc(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return

    status_msg = await message.answer(
        "Запускаю сверку с архивом и результатами Olimpbet..."
    )
    res = await run_full_recalc(bot)

    text = (
        f"**Перерасчёт завершён**\n"
        f"Проверено матчей: {res['checked']}\n"
        f"Рассчитано ставок: {res['settled']}"
    )
    if res["unresolved"]:
        text += "\n\n**Не найдены в архиве (закройте вручную через `/settle`):**\n"
        for m_id, title in res["unresolved"].items():
            text += f"• `{m_id}` — {title}\n"

    await status_msg.edit_text(text, parse_mode="Markdown")


@router.message(Command("settle", "settle_bet"))
async def cmd_settle(message: Message):
    """Ручной расчёт любого зависшего матча: /settle 849201 2:1"""
    if not message.from_user or not is_admin(message.from_user.id):
        return

    parts = (message.text or "").split()
    if len(parts) != 3 or ":" not in parts[2]:
        await message.answer(
            "Формат: `/settle <match_id> <счёт1>:<счёт2>`\nПример: `/settle 849201 2:1`",
            parse_mode="Markdown",
        )
        return

    match_id = parts[1].strip()
    try:
        s1_str, s2_str = parts[2].split(":")
        s1, s2 = int(s1_str.strip()), int(s2_str.strip())
    except ValueError:
        await message.answer(
            "Счёт должен быть целыми числами, например `1:0`.", parse_mode="Markdown"
        )
        return

    count = await settle_match_bets(match_id, s1, s2, bot)
    await message.answer(
        f"Матч `{match_id}` закрыт со счётом **{s1}:{s2}**.\nРассчитано ставок: **{count}**.",
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


class CashierRequest(BaseModel):
    user_id: str
    amount: float
    method: str = "sbp"
    details: str = ""


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
        "webapp": WEBAPP_URL,
        "updated_at": LINE_CACHE["updated_at"],
    }


@app.get("/api/init/{user_id}")
async def api_init(user_id: str, name: str = "Игрок"):
    user = await db.get(f"users/{user_id}")
    if not user:
        user = {
            "id": user_id,
            "name": name,
            "balance": 0.0,
            "total_bets": 0,
            "won_bets": 0,
            "net_profit": 0.0,
            "created_at": int(time.time()),
        }
        await db.put(f"users/{user_id}", user)

    return {
        "user": user,
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
    is_live = cached["is_live"] if cached else True
    details = await scraper.fetch_event_details(match_id, is_live=is_live)

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
    if req.amount < 10:
        raise HTTPException(status_code=400, detail="Минимальная ставка 10 ₽")
    if req.odd <= 1.0:
        raise HTTPException(status_code=400, detail="Некорректный коэффициент")

    user = await db.get(f"users/{req.user_id}")
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    balance = float(user.get("balance", 0))
    if balance < req.amount:
        raise HTTPException(status_code=400, detail="Недостаточно средств на балансе")

    match = LINE_CACHE["by_id"].get(req.match_id)
    if not match:
        raise HTTPException(
            status_code=400, detail="Приём ставок на событие приостановлен"
        )

    new_balance = round(balance - req.amount, 2)
    total_bets = int(user.get("total_bets", 0)) + 1

    bet_data = {
        "user_id": req.user_id,
        "match_id": req.match_id,
        "match_title": f"{match['team1']} — {match['team2']}",
        "league": match["league"],
        "selection": req.selection,
        "odd": round(req.odd, 2),
        "amount": round(req.amount, 2),
        "potential_win": round(req.amount * req.odd, 2),
        "status": "pending",
        "created_at": int(time.time()),
    }

    bet_id = await db.push(f"users/{req.user_id}/bets", bet_data)
    await db.patch(
        f"users/{req.user_id}",
        {"balance": new_balance, "total_bets": total_bets},
    )

    return {"ok": True, "bet_id": bet_id, "balance": new_balance}


@app.get("/api/bets/{user_id}")
async def api_user_bets(user_id: str):
    res = []

    ubets = await db.get(f"users/{user_id}/bets")
    if isinstance(ubets, dict):
        for b_id, b in ubets.items():
            if isinstance(b, dict):
                item = dict(b)
                item["id"] = b_id
                res.append(item)

    root_bets = await db.get("bets")
    if isinstance(root_bets, dict):
        for b_id, b in root_bets.items():
            if isinstance(b, dict) and str(b.get("user_id")) == str(user_id):
                item = dict(b)
                item["id"] = b_id
                res.append(item)

    res.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    return res


@app.post("/api/cashier/deposit")
async def api_cashier_deposit(req: CashierRequest):
    if req.amount < 50:
        raise HTTPException(status_code=400, detail="Минимальное пополнение 50 ₽")
    tx_id = await db.push(
        "transactions",
        {
            "type": "deposit",
            "user_id": req.user_id,
            "amount": req.amount,
            "method": req.method,
            "status": "created",
            "created_at": int(time.time()),
        },
    )
    return {
        "ok": True,
        "tx_id": tx_id,
        "payment_url": None,
        "message": "Платёжный шлюз подключается перед релизом",
    }


@app.post("/api/cashier/withdraw")
async def api_cashier_withdraw(req: CashierRequest):
    if req.amount < 100:
        raise HTTPException(status_code=400, detail="Минимальный вывод 100 ₽")
    user = await db.get(f"users/{req.user_id}")
    if not user or float(user.get("balance", 0)) < req.amount:
        raise HTTPException(status_code=400, detail="Недостаточно средств")

    new_bal = round(float(user["balance"]) - req.amount, 2)
    await db.patch(f"users/{req.user_id}", {"balance": new_bal})
    tx_id = await db.push(
        "transactions",
        {
            "type": "withdraw",
            "user_id": req.user_id,
            "amount": req.amount,
            "method": req.method,
            "details": req.details,
            "status": "pending",
            "created_at": int(time.time()),
        },
    )
    for adm in ADMIN_IDS:
        try:
            await bot.send_message(
                adm,
                f"Заявка на вывод #{tx_id}\nИгрок: `{req.user_id}`\nСумма: {req.amount} ₽\nРеквизиты: {req.details}",
                parse_mode="Markdown",
            )
        except Exception:
            pass
    return {"ok": True, "tx_id": tx_id, "balance": new_bal}


@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    await chat_manager.connect(ws)
    try:
        history = await db.get("chat_messages")
        if isinstance(history, dict):
            msgs = list(history.values())[-30:]
            await ws.send_json({"type": "history", "messages": msgs})

        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            text = str(data.get("text", "")).strip()[:300]
            if not text:
                continue
            msg_obj = {
                "user": str(data.get("user", "Игрок"))[:24],
                "text": text,
                "time": datetime.now(MSK_TZ).strftime("%H:%M"),
            }
            await db.push("chat_messages", msg_obj)
            await chat_manager.broadcast({"type": "message", "message": msg_obj})
    except WebSocketDisconnect:
        chat_manager.disconnect(ws)
    except Exception:
        chat_manager.disconnect(ws)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
