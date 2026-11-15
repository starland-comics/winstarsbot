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
from pydantic import BaseModel

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("WinStars")

# ================= КОНФИГУРАЦИЯ =================
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


# ================= ПАРСЕР OLIMPBET =================
def format_match_time(ev: dict, is_live: bool) -> str:
    """Точные минуты матча, 'Перерыв' или время старта прематча."""
    if not is_live:
        start_ts = (
            ev.get("startDate")
            or ev.get("startTime")
            or ev.get("beginTime")
            or ev.get("time")
            or 0
        )
        if isinstance(start_ts, (int, float)) and start_ts > 1_000_000_000:
            if start_ts > 10_000_000_000:
                start_ts = start_ts / 1000
            dt = datetime.fromtimestamp(int(start_ts), tz=MSK_TZ)
            return dt.strftime("%d.%m %H:%M")
        return "Скоро"

    status_text = " ".join(
        str(ev.get(k, ""))
        for k in (
            "periodName",
            "statusName",
            "stage",
            "timeText",
            "comment",
            "stateName",
        )
    ).lower()

    if (
        any(w in status_text for w in ("перерыв", "half", "break", "ht"))
        or ev.get("isBreak")
        or ev.get("period") == 0
    ):
        return "Перерыв"

    minute: Optional[int] = None
    for key in ("minute", "minutes", "matchMinute"):
        if ev.get(key) is not None:
            try:
                val = int(ev[key])
                if val > 0:
                    minute = val
                    break
            except (ValueError, TypeError):
                pass

    if minute is None:
        raw_time = (
            ev.get("time")
            or ev.get("seconds")
            or ev.get("elapsed")
            or ev.get("timer")
            or ev.get("timeSec")
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


def _extract_odd_val(o: dict) -> float:
    for k in ("value", "odd", "k", "coef", "coefficient", "price"):
        v = o.get(k)
        if v is not None:
            try:
                f = float(v)
                if f > 1.0:
                    return round(f, 2)
            except (ValueError, TypeError):
                pass
    return 0.0


def extract_1x2_and_markets(
    ev: dict,
) -> Tuple[Dict[str, float], List[Dict[str, Any]]]:
    odds_1x2 = {"P1": 0.0, "X": 0.0, "P2": 0.0}
    markets_grouped: List[Dict[str, Any]] = []

    raw_outcomes = ev.get("outcomes") or []
    raw_markets = ev.get("markets") or ev.get("groups") or []

    # 1. Плоский список исходов в общей линии
    if raw_outcomes:
        for o in raw_outcomes:
            name = str(
                o.get("shortName") or o.get("name") or o.get("title") or ""
            ).strip()
            val = _extract_odd_val(o)
            if val <= 1.0:
                continue
            t_type = str(o.get("tableType") or o.get("type") or "")
            if name in ("1", "П1") or t_type == "1":
                odds_1x2["P1"] = val
            elif name in ("X", "Х", "Н") or t_type in ("X", "0"):
                odds_1x2["X"] = val
            elif name in ("2", "П2") or t_type == "2":
                odds_1x2["P2"] = val

    # 2. Структурированные рынки (полная роспись)
    for m in raw_markets:
        m_name = str(m.get("name") or m.get("title") or "Исходы").strip()
        items = []
        for o in m.get("outcomes") or m.get("items") or []:
            o_name = str(
                o.get("shortName") or o.get("name") or o.get("title") or ""
            ).strip()
            val = _extract_odd_val(o)
            if val <= 1.0 or not o_name:
                continue
            items.append({"label": o_name, "odd": val})

            if m_name.lower() in ("исход матча", "основное время", "1x2", "исход"):
                if o_name in ("1", "П1"):
                    odds_1x2["P1"] = val
                elif o_name in ("X", "Х"):
                    odds_1x2["X"] = val
                elif o_name in ("2", "П2"):
                    odds_1x2["P2"] = val

        if items:
            markets_grouped.append({"title": m_name, "outcomes": items})

    # 3. Базовая роспись из 1X2, если доп. рынки ещё не подгружены
    if not markets_grouped and odds_1x2["P1"] > 1.0 and odds_1x2["P2"] > 1.0:
        p1, x, p2 = odds_1x2["P1"], odds_1x2["X"] or 3.25, odds_1x2["P2"]
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
        endpoints = (
            [
                f"{self.BASE}/live/sports-with-competitions-with-events?v=1",
                f"{self.BASE}/live/sports-with-categories-with-events?v=1",
            ]
            if is_live
            else [
                f"{self.BASE}/line/sports-with-competitions-with-events?v=1",
                f"{self.BASE}/line/top/sports-with-competitions-with-events?v=1",
                f"{self.BASE}/line/sports-with-categories-with-events?v=1",
            ]
        )

        data = None
        try:
            async with AsyncSession(impersonate="chrome124", timeout=15) as session:
                for ep in endpoints:
                    resp = await session.get(ep, headers=self.headers)
                    if resp.status_code == 200:
                        payload = resp.json()
                        if payload:
                            data = payload
                            break
        except Exception as e:
            logger.error(f"Olimpbet fetch error ({'live' if is_live else 'line'}): {e}")
            return []

        if not data:
            return []

        matches: List[Dict[str, Any]] = []
        sports = data if isinstance(data, list) else data.get("sports", [])

        for sport in sports:
            s_id = sport.get("id")
            s_name = str(sport.get("name", "")).lower()
            if s_id != 1 and "футбол" not in s_name:
                continue

            # Olimpbet использует ключ competitions (иногда categories / champs)
            comps = (
                sport.get("competitions")
                or sport.get("categories")
                or sport.get("champs")
                or []
            )
            for comp in comps:
                country = str(
                    comp.get("countryName") or comp.get("country") or ""
                ).strip()
                league_raw = str(
                    comp.get("name") or comp.get("title") or "Лига"
                ).strip()
                league = (
                    f"{country}. {league_raw}"
                    if country and not league_raw.startswith(country)
                    else league_raw
                )

                for ev in comp.get("events") or []:
                    m_id = str(ev.get("id") or "")
                    if not m_id:
                        continue

                    team1 = str(
                        ev.get("team1Name")
                        or ev.get("team1")
                        or ev.get("homeName")
                        or "Команда 1"
                    ).strip()
                    team2 = str(
                        ev.get("team2Name")
                        or ev.get("team2")
                        or ev.get("awayName")
                        or "Команда 2"
                    ).strip()
                    logo1 = (
                        ev.get("team1Logo")
                        or ev.get("homeLogo")
                        or ev.get("logo1")
                        or ""
                    )
                    logo2 = (
                        ev.get("team2Logo")
                        or ev.get("awayLogo")
                        or ev.get("logo2")
                        or ""
                    )

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


# ================= ДВИЖОК РАСЧЁТА СТАВОК (ПОД ТВОЮ СТРУКТУРУ FIREBASE) =================
def normalize_bet(bet: dict) -> dict:
    """Приводит старые (events[]) и новые купоны к единому виду."""
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
    status = str(bet.get("status") or "active")
    if status == "pending":
        status = "active"
    if status == "refund":
        status = "void"

    return {
        "match_id": match_id,
        "match_title": match_title,
        "league": league,
        "selection": selection,
        "odd": round(odd, 2),
        "amount": amount,
        "status": status,
        "final_score": bet.get("final_score", ""),
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
    """
    Собирает все активные ставки (status == 'active' или 'pending')
    из users/{uid}/bets/{bet_id} и active_bets/{bet_id}.
    Возвращает список (firebase_path, bet_id, user_id, normalized_bet).
    """
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
    """
    Рассчитывает ставки по match_id ИЛИ по части названия команды (например, 'Албания').
    """
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

        patch_payload = {
            "status": result,
            "final_score": f"{s1}:{s2}",
            "payout": payout,
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

    results_api = await scraper.fetch_finished_results()
    archive = await db.get("match_archive") or {}

    total_settled = 0
    unresolved: Dict[str, str] = {}

    for key, title in pending_matches.items():
        if key in results_api:
            s1, s2 = results_api[key]
            total_settled += await settle_match_bets(key, s1, s2, notify_bot)
            continue

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


# ================= ФОНОВЫЙ ПАРСЕР =================
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


# ================= TELEGRAM БОТ =================
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

    active_list = await collect_all_active_bets()
    if not active_list:
        await message.answer("Все ставки рассчитаны, зависших матчей нет.")
        return

    grouped: Dict[str, Dict[str, Any]] = {}
    for _, _, _, norm in active_list:
        key = norm["match_id"] or norm["match_title"].split()[0]
        if key not in grouped:
            grouped[key] = {
                "title": norm["match_title"],
                "count": 0,
                "sum": 0.0,
            }
        grouped[key]["count"] += 1
        grouped[key]["sum"] += norm["amount"]

    lines = ["**Активные нерассчитанные матчи:**\n"]
    for key, info in grouped.items():
        lines.append(
            f"• {info['title']} ({info['count']} ст. на {info['sum']:.0f} ₽)\n"
            f"  Закрыть: `/settle {key} 0:0`"
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
        text += "\n\n**Закройте вручную командой `/settle <слово_или_id> <счёт>`:**\n"
        for key, title in res["unresolved"].items():
            text += f"• `{key}` — {title}\n"

    await status_msg.edit_text(text, parse_mode="Markdown")


@router.message(Command("settle", "settle_bet"))
async def cmd_settle(message: Message):
    """Работает и по ID, и по названию команды: /settle Албания 1:3"""
    if not message.from_user or not is_admin(message.from_user.id):
        return

    parts = (message.text or "").split()
    if len(parts) < 3 or ":" not in parts[-1]:
        await message.answer(
            "Формат: `/settle <команда_или_id> <счёт1>:<счёт2>`\n"
            "Пример для Албании: `/settle Албания 1:3`",
            parse_mode="Markdown",
        )
        return

    score_part = parts[-1].strip()
    query = " ".join(parts[1:-1]).strip()

    try:
        s1_str, s2_str = score_part.split(":")
        s1, s2 = int(s1_str.strip()), int(s2_str.strip())
    except ValueError:
        await message.answer(
            "Счёт должен быть целыми числами, например `1:3`.", parse_mode="Markdown"
        )
        return

    count = await settle_match_bets(query, s1, s2, bot)
    await message.answer(
        f"Матч **{query}** закрыт со счётом **{s1}:{s2}**.\nРассчитано ставок: **{count}**.",
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
        "live_count": len(LINE_CACHE["live"]),
        "prematch_count": len(LINE_CACHE["prematch"]),
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
        "match_title": f"{match['team1']} - {match['team2']}",
        "league": match["league"],
        "selection": req.selection,
        "outcome": req.selection,
        "total_coefficient": round(req.odd, 2),
        "odd": round(req.odd, 2),
        "amount": round(req.amount, 2),
        "potential_win": round(req.amount * req.odd, 2),
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

    return {"ok": True, "bet_id": bet_id, "balance": new_balance}


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


def normalize_chat_msg(m: dict) -> dict:
    user = m.get("user") or m.get("username") or m.get("first_name") or m.get("name") or "Игрок"
    text = m.get("text") or m.get("message") or ""
    raw_t = m.get("time") or m.get("timestamp") or m.get("created_at") or ""
    if isinstance(raw_t, (int, float)) and raw_t > 1_000_000_000:
        t_str = datetime.fromtimestamp(int(raw_t), tz=MSK_TZ).strftime("%H:%M")
    else:
        t_str = str(raw_t)
    return {"user": str(user), "text": str(text), "time": t_str}


@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    await chat_manager.connect(ws)
    try:
        history = await db.get("chat_messages")
        if isinstance(history, dict):
            msgs = [
                normalize_chat_msg(v)
                for v in list(history.values())[-30:]
                if isinstance(v, dict)
            ]
            await ws.send_json({"type": "history", "messages": msgs})

        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            text = str(data.get("text", "")).strip()[:300]
            if not text:
                continue
            now_str = datetime.now(MSK_TZ).strftime("%H:%M")
            user_str = str(data.get("user", "Игрок"))[:24]
            msg_obj = {
                "user": user_str,
                "username": user_str,
                "text": text,
                "time": now_str,
                "timestamp": int(time.time()),
            }
            await db.push("chat_messages", msg_obj)
            await chat_manager.broadcast(
                {
                    "type": "message",
                    "message": {"user": user_str, "text": text, "time": now_str},
                }
            )
    except WebSocketDisconnect:
        chat_manager.disconnect(ws)
    except Exception:
        chat_manager.disconnect(ws)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
