import asyncio
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


# ================= ПОИСК ЛОГОТИПОВ КОМАНД =================
async def resolve_team_logo(team_name: str) -> str:
    clean = re.sub(r"\s*\([^)]*\)", "", team_name).strip()
    if not clean:
        return ""
    if clean in LOGO_CACHE:
        return LOGO_CACHE[clean]

    try:
        url = f"https://www.thesportsdb.com/api/v1/json/3/searchteams.php?t={quote(clean)}"
        r = await db.client.get(url, timeout=4.0)
        if r.status_code == 200:
            data = r.json()
            teams = data.get("teams") if isinstance(data, dict) else None
            if teams and isinstance(teams, list):
                badge = teams[0].get("strBadge") or teams[0].get("strTeamBadge") or ""
                if badge:
                    LOGO_CACHE[clean] = badge
                    return badge
    except Exception:
        pass

    LOGO_CACHE[clean] = ""
    return ""


# ================= ПАРСЕР FONBET (ЗЕРКАЛА LINE-LB51..53) =================
def format_fonbet_time(ev: dict, misc: dict, is_live: bool) -> str:
    if not is_live:
        start_ts = ev.get("startTime") or 0
        if isinstance(start_ts, (int, float)) and start_ts > 1_000_000_000:
            dt = datetime.fromtimestamp(int(start_ts), tz=MSK_TZ)
            return dt.strftime("%d.%m %H:%M")
        return "Скоро"

    comment = str(misc.get("comment") or ev.get("comment") or "").lower()
    if any(w in comment for w in ("перерыв", "half", "break", "ht")):
        return "Перерыв"

    timer_sec = misc.get("timerSeconds")
    if isinstance(timer_sec, (int, float)) and timer_sec > 0:
        minute = int(timer_sec) // 60 + 1
        if minute > 90:
            return f"90+{minute - 90}'"
        return f"{minute}'"

    m_comment = re.search(r"(\d+)\s*мин", comment)
    if m_comment:
        return f"{m_comment.group(1)}'"

    return "LIVE"


def parse_fonbet_factors(
    factors_list: List[dict],
) -> Tuple[Dict[str, float], List[Dict[str, Any]]]:
    odds_1x2 = {"P1": 0.0, "X": 0.0, "P2": 0.0}
    dc_outcomes: List[Dict[str, Any]] = []
    totals_outcomes: List[Dict[str, Any]] = []
    handicap_outcomes: List[Dict[str, Any]] = []
    btts_outcomes: List[Dict[str, Any]] = []
    other_outcomes: List[Dict[str, Any]] = []

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
            dc_outcomes.append({"label": "1X", "odd": odd})
        elif fid == 1571:
            dc_outcomes.append({"label": "12", "odd": odd})
        elif fid == 925:
            dc_outcomes.append({"label": "X2", "odd": odd})
        elif fid == 930 and pt:
            totals_outcomes.append({"label": f"ТБ {pt}", "odd": odd})
        elif fid == 931 and pt:
            totals_outcomes.append({"label": f"ТМ {pt}", "odd": odd})
        elif fid in (927, 937) and pt:
            handicap_outcomes.append({"label": f"Ф1 ({pt})", "odd": odd})
        elif fid in (928, 938) and pt:
            handicap_outcomes.append({"label": f"Ф2 ({pt})", "odd": odd})
        elif fid in (4241, 1805):
            btts_outcomes.append({"label": "ОЗ Да", "odd": odd})
        elif fid in (4242, 1806):
            btts_outcomes.append({"label": "ОЗ Нет", "odd": odd})
        else:
            name = str(f_obj.get("name") or "").strip()
            if name:
                label = f"{name} {pt}".strip()
                other_outcomes.append({"label": label, "odd": odd})

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
    if btts_outcomes:
        markets.append({"title": "Обе забьют", "outcomes": btts_outcomes})
    if other_outcomes:
        markets.append({"title": "Дополнительные рынки", "outcomes": other_outcomes[:10]})

    return odds_1x2, markets


class FonbetScraper:
    MIRRORS = [
        "https://line-lb51.bkfon-resources.com",
        "https://line-lb52.bkfon-resources.com",
        "https://line-lb53.bkfon-resources.com",
        "https://line01w.bkfon-resources.com",
        "https://line02w.bkfon-resources.com",
    ]

    def __init__(self):
        self._mirror_idx = 0
        self.headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Origin": "https://www.fon.bet",
            "Referer": "https://www.fon.bet/",
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 14; Pixel 8) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
            ),
        }

    def _ordered_mirrors(self) -> List[str]:
        n = len(self.MIRRORS)
        return [self.MIRRORS[(self._mirror_idx + i) % n] for i in range(n)]

    async def _fetch_json(self, path: str) -> Optional[dict]:
        async with AsyncSession(impersonate="chrome120", timeout=15) as session:
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
                    logger.warning(f"Mirror {base} failed: {e}")
        return None

    async def fetch_full_line(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        data = await self._fetch_json("/events/listBase?lang=ru&scopeMarket=1600")
        if not data:
            return [], []

        sports_map: Dict[int, dict] = {}
        for s in data.get("sports") or []:
            sid = s.get("id")
            if sid is not None:
                sports_map[int(sid)] = s

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
            if ev.get("level") != 1:
                continue

            sport_id = ev.get("sportId")
            comp = sports_map.get(int(sport_id)) if sport_id is not None else None
            root_sport_id = comp.get("parentId") if comp else None
            if sport_id != 1 and root_sport_id != 1:
                continue

            t1 = str(ev.get("team1") or "").strip()
            t2 = str(ev.get("team2") or "").strip()
            if not t1 or not t2:
                continue

            # Исключаем киберфутбол / виртуальные лиги при необходимости, оставляя реальный футбол
            league_name = str(comp.get("name") if comp else "Футбол").strip()
            if "кибер" in league_name.lower() or "esports" in league_name.lower():
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
    currency = str(bet.get("currency") or "RUB").upper()
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
        "currency": currency,
        "status": status,
        "final_score": bet.get("final_score", ""),
        "payout": float(bet.get("payout") or 0),
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

        # Перевод выплаты в базовую валюту баланса (Stars)
        payout_stars = (
            round(payout / STAR_TO_RUB, 2) if norm["currency"] == "RUB" else payout
        )
        stake_stars = (
            round(amount / STAR_TO_RUB, 2) if norm["currency"] == "RUB" else amount
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


# ================= ФОНОВЫЙ ПАРСЕР =================
async def logo_background_loader(matches: List[Dict[str, Any]]):
    for m in matches[:40]:
        for t_key, l_key in (("team1", "logo1"), ("team2", "logo2")):
            if not m.get(l_key):
                url = await resolve_team_logo(m[t_key])
                if url:
                    m[l_key] = url


async def scraper_loop(bot: Bot):
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
        f"Live-линия Fonbet, ставки в Telegram Stars и рублях, мгновенный расчёт купонов.",
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


# --- АДМИН-КОМАНДЫ ---
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
    currency: str = "STARS"  # STARS или RUB


class InvoiceRequest(BaseModel):
    user_id: str
    stars: int


class CashierRequest(BaseModel):
    user_id: str
    amount: float
    currency: str = "STARS"
    method: str = "sbp"
    details: str = ""


class PromoRequest(BaseModel):
    user_id: str
    code: str


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
async def api_init(user_id: str, name: str = "Игрок"):
    user = await db.get(f"users/{user_id}")
    if not user:
        user = {
            "id": user_id,
            "name": name,
            "balance": 0.0,
            "referrals": 0,
            "total_bets": 0,
            "won_bets": 0,
            "profit": 0.0,
            "created_at": int(time.time()),
        }
        await db.put(f"users/{user_id}", user)

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
        "currency": cur,
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


@app.post("/api/invoice")
async def api_create_stars_invoice(req: InvoiceRequest):
    if req.stars < 1 or req.stars > 50000:
        raise HTTPException(status_code=400, detail="Укажите от 1 до 50000 ★")
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
            "method": "СБП (Rolly Pay)",
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
    stars_amt = (
        round(req.amount / STAR_TO_RUB, 2) if cur == "RUB" else round(req.amount, 2)
    )
    if stars_amt < 50:
        raise HTTPException(status_code=400, detail="Минимальный вывод 50 ★ (75 ₽)")

    user = await db.get(f"users/{req.user_id}")
    if not user or float(user.get("balance", 0)) < stars_amt:
        raise HTTPException(status_code=400, detail="Недостаточно средств")

    new_bal = round(float(user["balance"]) - stars_amt, 2)
    await db.patch(f"users/{req.user_id}", {"balance": new_bal})

    tx_id = await db.push(
        "transactions",
        {
            "type": "withdraw",
            "method": req.method,
            "user_id": req.user_id,
            "amount_stars": stars_amt,
            "amount_rub": round(stars_amt * STAR_TO_RUB, 2),
            "details": req.details,
            "status": "pending",
            "created_at": int(time.time()),
        },
    )
    for adm in ADMIN_IDS:
        try:
            await bot.send_message(
                adm,
                f"Вывод #{tx_id}\nИгрок: `{req.user_id}`\nСумма: {stars_amt} ★ ({stars_amt * STAR_TO_RUB:.0f} ₽)\nРеквизиты: {req.details}",
                parse_mode="Markdown",
            )
        except Exception:
            pass
    return {"ok": True, "balance": new_bal}


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
            "type": "promo",
            "method": f"Промокод {code}",
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
