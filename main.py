import asyncio
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
import uvicorn
from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    PreCheckoutQuery,
    WebAppInfo,
)
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    cffi_requests = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("winstars")

# ================= CONFIGURATION =================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
FIREBASE_URL = os.getenv(
    "FIREBASE_URL", "https://winstars-435f1-default-rtdb.firebaseio.com"
).rstrip("/")
FIREBASE_SECRET = os.getenv("FIREBASE_SECRET", "").strip()

ADMIN_IDS = {
    x.strip()
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip()
}

BOT_USERNAME = os.getenv("BOT_USERNAME", "WinStarsPlaybot").strip()
WEBAPP_URL = os.getenv(
    "WEBAPP_URL", "https://starland-comics.github.io/winstarsbot/"
).strip()
STAR_RATE = float(os.getenv("STAR_RATE", "1.5"))
REFERRAL_BONUS = float(os.getenv("REFERRAL_BONUS", "5.0"))

MSK = timezone(timedelta(hours=3))

FONBET_MIRRORS = [
    "https://line01w.bk6bba-resources.com",
    "https://line02w.bk6bba-resources.com",
    "https://line03w.bk6bba-resources.com",
    "https://line04w.bk6bba-resources.com",
]

# Фильтр заглушек ("Хозяева - Гости", статистика, итоги тура)
BLOCKED_EVENT_PATTERNS = [
    r"^хозяева$",
    r"^гости$",
    r"хозяева\s*[-—]\s*гости",
    r"итоги\s+(дня|тура)",
    r"специальн(ые|ая)\s+ставк",
    r"кто\s+выше",
    r"сравнение",
    r"\((угловые|ж/к|желтые|жёлтые|удары|фолы|офсайды|вбрасывания|штанги|сейвы|гол)\)",
]

# Фильтр нежелательных лиг (молодёжные, женские, кибер, мини/пляж, любители)
BLOCKED_LEAGUE_PATTERNS = [
    r"\bu[-\s]?(15|16|17|18|19|20|21|22|23)\b",
    r"\bдо\s*(15|16|17|18|19|20|21|22|23)\b",
    r"молод[её]ж",
    r"юнош",
    r"юниор",
    r"резерв",
    r"женщин",
    r"\bжен\.",
    r"\(ж\)",
    r"\bwomen\b",
    r"\bfemin",
    r"\bfrauen\b",
    r"\bfemminile\b",
    r"кибер",
    r"esport",
    r"e-?football",
    r"\bfifa\b",
    r"\bfc\s?2[456]\b",
    r"liga\s*pro",
    r"лига\s*про",
    r"short\s*football",
    r"\b[345]x[345]\b",
    r"мини-?футбол",
    r"футзал",
    r"\bfutsal\b",
    r"пляжн",
    r"\bbeach\b",
    r"simulated",
    r"\bsrl\b",
    r"любител",
    r"\bлфл\b",
    r"\blfl\b",
]


def is_clean_event(team1: str, team2: str, league: str) -> bool:
    t1 = (team1 or "").strip()
    t2 = (team2 or "").strip()
    lg = (league or "").strip()
    if not t1 or not t2 or not lg:
        return False

    t1_l, t2_l, lg_l = t1.lower(), t2.lower(), lg.lower()
    pair_l = f"{t1_l} - {t2_l}"

    for pat in BLOCKED_EVENT_PATTERNS:
        if re.search(pat, t1_l) or re.search(pat, t2_l) or re.search(pat, pair_l):
            return False

    for pat in BLOCKED_LEAGUE_PATTERNS:
        if re.search(pat, lg_l) or re.search(pat, pair_l):
            return False

    return True


def fb_key(raw: str) -> str:
    """Санитизация ключей для Firebase Realtime Database."""
    return re.sub(r"[.$#\[\]/]", "_", str(raw).strip())


# ================= FIREBASE RTDB ASYNC CLIENT =================
http_client: Optional[httpx.AsyncClient] = None


def _fb_url(path: str) -> str:
    clean = path.strip("/")
    base = f"{FIREBASE_URL}/{clean}.json"
    if FIREBASE_SECRET:
        return f"{base}?auth={FIREBASE_SECRET}"
    return base


async def fb_get(path: str, default: Any = None) -> Any:
    if http_client is None:
        return default
    try:
        resp = await http_client.get(_fb_url(path))
        if resp.status_code == 200:
            data = resp.json()
            return default if data is None else data
    except Exception as e:
        logger.error("Firebase GET %s error: %s", path, e)
    return default


async def fb_put(path: str, data: Any) -> bool:
    if http_client is None:
        return False
    try:
        resp = await http_client.put(_fb_url(path), json=data)
        return resp.status_code == 200
    except Exception as e:
        logger.error("Firebase PUT %s error: %s", path, e)
        return False


async def fb_patch(path: str, data: Dict[str, Any]) -> bool:
    if http_client is None:
        return False
    try:
        resp = await http_client.patch(_fb_url(path), json=data)
        return resp.status_code == 200
    except Exception as e:
        logger.error("Firebase PATCH %s error: %s", path, e)
        return False


async def fb_delete(path: str) -> bool:
    if http_client is None:
        return False
    try:
        resp = await http_client.delete(_fb_url(path))
        return resp.status_code == 200
    except Exception as e:
        logger.error("Firebase DELETE %s error: %s", path, e)
        return False


async def get_or_create_user(
    user_id: str,
    name: str = "Игрок",
    username: str = "",
    photo_url: str = "",
    referrer_id: Optional[str] = None,
) -> Dict[str, Any]:
    uid = fb_key(user_id)
    existing = await fb_get(f"users/{uid}")
    if isinstance(existing, dict) and existing.get("id"):
        updates: Dict[str, Any] = {}
        if name and name != "Игрок" and existing.get("name") != name:
            updates["name"] = name
        if username and existing.get("username") != username:
            updates["username"] = username
        if photo_url and existing.get("photo_url") != photo_url:
            updates["photo_url"] = photo_url
        if updates:
            await fb_patch(f"users/{uid}", updates)
            existing.update(updates)
        return existing

    new_user: Dict[str, Any] = {
        "id": uid,
        "name": name or "Игрок",
        "username": username or "",
        "photo_url": photo_url or "",
        "balance": 0.0,
        "referrals": 0,
        "referred_by": None,
        "total_bets": 0,
        "won_bets": 0,
        "profit": 0.0,
        "created_at": datetime.now(MSK).isoformat(),
    }

    if referrer_id and fb_key(referrer_id) != uid:
        ref_uid = fb_key(referrer_id)
        ref_user = await fb_get(f"users/{ref_uid}")
        if isinstance(ref_user, dict) and ref_user.get("id"):
            new_user["referred_by"] = ref_uid
            new_ref_bal = round(float(ref_user.get("balance", 0)) + REFERRAL_BONUS, 2)
            new_ref_cnt = int(ref_user.get("referrals", 0)) + 1
            await fb_patch(
                f"users/{ref_uid}",
                {"balance": new_ref_bal, "referrals": new_ref_cnt},
            )
            tx_id = uuid.uuid4().hex[:10].upper()
            await fb_put(
                f"transactions/{tx_id}",
                {
                    "id": tx_id,
                    "user_id": ref_uid,
                    "type": "deposit",
                    "method": "Реферальный бонус",
                    "currency": "STARS",
                    "amount_rub": round(REFERRAL_BONUS * STAR_RATE, 2),
                    "amount_stars": REFERRAL_BONUS,
                    "status": "completed",
                    "details": f"Приглашён игрок {name or uid}",
                    "created_at": datetime.now(MSK).strftime("%d.%m %H:%M"),
                },
            )

    await fb_put(f"users/{uid}", new_user)
    return new_user


# ================= FONBET LINE PARSER & SCORE TRACKER =================
LINE_CACHE: Dict[str, Any] = {
    "live": [],
    "prematch": [],
    "by_id": {},
    "logos": {},
    "updated_at": 0.0,
}

# Память последних зафиксированных счетов для авторасчёта
MATCH_SCORES_MEM: Dict[str, Dict[str, Any]] = {}


def fetch_json_mirror(path: str) -> Optional[Dict[str, Any]]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
    }
    for base in FONBET_MIRRORS:
        url = f"{base}{path}"
        try:
            if cffi_requests is not None:
                resp = cffi_requests.get(
                    url, headers=headers, impersonate="chrome120", timeout=10
                )
                if resp.status_code == 200:
                    return resp.json()
            else:
                with httpx.Client(timeout=10) as client:
                    resp = client.get(url, headers=headers)
                    if resp.status_code == 200:
                        return resp.json()
        except Exception:
            continue
    return None


def parse_fonbet_line(raw: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    sports = {s["id"]: s for s in raw.get("sports", [])}
    events = raw.get("events", [])
    custom_factors = raw.get("customFactors", [])

    factors_by_event: Dict[int, Dict[int, Dict[str, Any]]] = {}
    for cf in custom_factors:
        eid = cf.get("e")
        if eid is None:
            continue
        fmap = factors_by_event.setdefault(eid, {})
        for f in cf.get("factors", []):
            fmap[f.get("f")] = f

    live_list: List[Dict[str, Any]] = []
    prematch_list: List[Dict[str, Any]] = []
    by_id: Dict[str, Dict[str, Any]] = {}
    finished_updates: Dict[str, Dict[str, Any]] = {}
    logos_cache = LINE_CACHE.get("logos", {})
    prev_by_id = LINE_CACHE.get("by_id", {})
    seen_ids = set()

    for ev in events:
        if ev.get("level", 1) != 1:
            continue
        sport_id = ev.get("sportId")
        sport_obj = sports.get(sport_id, {})
        if sport_obj.get("parentId") != 1 and sport_id != 1:
            continue

        league_name = sport_obj.get("name", "Футбол")
        league_name = re.sub(
            r"^Футбол\.\s*", "", league_name, flags=re.IGNORECASE
        ).strip()

        team1 = (ev.get("team1") or "").strip()
        team2 = (ev.get("team2") or "").strip()
        if not is_clean_event(team1, team2, league_name):
            continue

        eid = str(ev.get("id"))
        seen_ids.add(eid)
        fmap = factors_by_event.get(ev.get("id"), {})

        is_live = ev.get("place") == "live"
        score1 = int(ev.get("score1") or 0)
        score2 = int(ev.get("score2") or 0)
        timer_sec = int(ev.get("timerSeconds") or 0)
        minute = max(1, timer_sec // 60) if is_live else 0

        if is_live:
            MATCH_SCORES_MEM[eid] = {
                "score1": score1,
                "score2": score2,
                "minute": minute,
                "is_live": True,
                "finished": False,
                "team1": team1,
                "team2": team2,
                "updated_at": time.time(),
            }

        p1 = fmap.get(921, {}).get("v")
        px = fmap.get(922, {}).get("v")
        p2 = fmap.get(923, {}).get("v")
        if not p1 or not p2:
            continue

        if is_live:
            time_label = f"{minute}'"
        else:
            start_ts = ev.get("startTime", 0)
            dt = (
                datetime.fromtimestamp(start_ts, tz=MSK)
                if start_ts
                else datetime.now(MSK)
            )
            time_label = dt.strftime("%d.%m %H:%M")

        # Роспись рынков
        markets: List[Dict[str, Any]] = []
        main_outcomes = [{"label": "П1", "odd": round(float(p1), 2)}]
        if px and float(px) > 1.0:
            main_outcomes.append({"label": "X", "odd": round(float(px), 2)})
        main_outcomes.append({"label": "П2", "odd": round(float(p2), 2)})
        markets.append({"title": "Основные исходы", "outcomes": main_outcomes})

        dc_1x = fmap.get(924, {}).get("v")
        dc_12 = fmap.get(1571, {}).get("v")
        dc_x2 = fmap.get(925, {}).get("v")
        if dc_1x and dc_x2:
            dc_out = [{"label": "1X", "odd": round(float(dc_1x), 2)}]
            if dc_12:
                dc_out.append({"label": "12", "odd": round(float(dc_12), 2)})
            dc_out.append({"label": "X2", "odd": round(float(dc_x2), 2)})
            markets.append({"title": "Двойной шанс", "outcomes": dc_out})

        tot_over = fmap.get(930)
        tot_under = fmap.get(931)
        if tot_over and tot_under and tot_over.get("v") and tot_under.get("v"):
            pt = tot_over.get("pt", "2.5")
            markets.append(
                {
                    "title": f"Тотал голов ({pt})",
                    "outcomes": [
                        {"label": f"ТБ {pt}", "odd": round(float(tot_over["v"]), 2)},
                        {"label": f"ТМ {pt}", "odd": round(float(tot_under["v"]), 2)},
                    ],
                }
            )

        h1 = fmap.get(927)
        h2 = fmap.get(928)
        if h1 and h2 and h1.get("v") and h2.get("v"):
            pt1 = h1.get("pt", "0")
            pt2 = h2.get("pt", "0")
            markets.append(
                {
                    "title": "Фора",
                    "outcomes": [
                        {"label": f"Ф1 ({pt1})", "odd": round(float(h1["v"]), 2)},
                        {"label": f"Ф2 ({pt2})", "odd": round(float(h2["v"]), 2)},
                    ],
                }
            )

        btts_y = fmap.get(1801, {}).get("v")
        btts_n = fmap.get(1802, {}).get("v")
        if btts_y and btts_n:
            markets.append(
                {
                    "title": "Обе забьют",
                    "outcomes": [
                        {"label": "ОЗ Да", "odd": round(float(btts_y), 2)},
                        {"label": "ОЗ Нет", "odd": round(float(btts_n), 2)},
                    ],
                }
            )

        t1_id = str(ev.get("team1Id", ""))
        t2_id = str(ev.get("team2Id", ""))
        logo1 = logos_cache.get(fb_key(team1.lower())) or (
            f"https://www.fon.bet/images/teams/{t1_id}.png" if t1_id else ""
        )
        logo2 = logos_cache.get(fb_key(team2.lower())) or (
            f"https://www.fon.bet/images/teams/{t2_id}.png" if t2_id else ""
        )

        match_obj = {
            "id": eid,
            "league": league_name,
            "team1": team1,
            "team2": team2,
            "logo1": logo1,
            "logo2": logo2,
            "is_live": is_live,
            "score1": score1,
            "score2": score2,
            "time_label": time_label,
            "start_time": ev.get("startTime", 0),
            "odds": {
                "P1": round(float(p1), 2),
                "X": round(float(px), 2) if px else 0.0,
                "P2": round(float(p2), 2),
            },
            "markets": markets,
        }

        by_id[eid] = match_obj
        if is_live:
            live_list.append(match_obj)
        else:
            prematch_list.append(match_obj)

    # Матчи, которые были в Live, но пропали из ленты после 80+ минуты -> считаем завершёнными
    for old_id, old_m in prev_by_id.items():
        if old_m.get("is_live") and old_id not in seen_ids:
            mem = MATCH_SCORES_MEM.get(old_id)
            if mem and mem.get("minute", 0) >= 80:
                mem["finished"] = True
                mem["is_live"] = False
                finished_updates[old_id] = mem

    prematch_list.sort(key=lambda x: x.get("start_time", 0))
    LINE_CACHE["live"] = live_list
    LINE_CACHE["prematch"] = prematch_list[:250]
    LINE_CACHE["by_id"] = by_id
    LINE_CACHE["updated_at"] = time.time()
    return finished_updates


# ================= BET EVALUATION & SETTLEMENT ENGINE =================
def evaluate_selection(selection: str, s1: int, s2: int) -> Optional[str]:
    """
    Возвращает 'won', 'lost', 'void' или None (если исход нестандартный).
    """
    sel = (selection or "").strip()
    total = s1 + s2

    if sel == "П1":
        return "won" if s1 > s2 else "lost"
    if sel == "X":
        return "won" if s1 == s2 else "lost"
    if sel == "П2":
        return "won" if s2 > s1 else "lost"
    if sel == "1X":
        return "won" if s1 >= s2 else "lost"
    if sel == "12":
        return "won" if s1 != s2 else "lost"
    if sel == "X2":
        return "won" if s2 >= s1 else "lost"
    if sel == "ОЗ Да":
        return "won" if (s1 > 0 and s2 > 0) else "lost"
    if sel == "ОЗ Нет":
        return "won" if (s1 == 0 or s2 == 0) else "lost"

    m_tb = re.match(r"^ТБ\s*([0-9.]+)$", sel)
    if m_tb:
        line = float(m_tb.group(1))
        if total > line:
            return "won"
        if total == line:
            return "void"
        return "lost"

    m_tm = re.match(r"^ТМ\s*([0-9.]+)$", sel)
    if m_tm:
        line = float(m_tm.group(1))
        if total < line:
            return "won"
        if total == line:
            return "void"
        return "lost"

    m_f1 = re.match(r"^Ф1\s*\(([+-]?[0-9.]+)\)$", sel)
    if m_f1:
        handicap = float(m_f1.group(1))
        diff = (s1 + handicap) - s2
        if diff > 0:
            return "won"
        if diff == 0:
            return "void"
        return "lost"

    m_f2 = re.match(r"^Ф2\s*\(([+-]?[0-9.]+)\)$", sel)
    if m_f2:
        handicap = float(m_f2.group(1))
        diff = (s2 + handicap) - s1
        if diff > 0:
            return "won"
        if diff == 0:
            return "void"
        return "lost"

    return None


async def settle_match_in_bets(
    target_match_id: str, s1: int, s2: int, bot_instance: Optional[Bot] = None
) -> int:
    """Рассчитывает все активные ставки, содержащие target_match_id."""
    all_bets = await fb_get("bets", {})
    if not isinstance(all_bets, dict):
        return 0

    settled_count = 0
    final_score_str = f"{s1}:{s2}"

    for bet_id, bet in all_bets.items():
        if not isinstance(bet, dict) or bet.get("status") != "active":
            continue

        events = bet.get("events")
        if not isinstance(events, list) or not events:
            events = [
                {
                    "match_id": str(bet.get("match_id", "")),
                    "match_title": bet.get("match_title", ""),
                    "selection": bet.get("selection", ""),
                    "odd": float(bet.get("odd", 1.0)),
                    "status": "active",
                    "final_score": "",
                }
            ]

        changed = False
        for ev in events:
            if str(ev.get("match_id")) == str(target_match_id) and ev.get("status") == "active":
                res = evaluate_selection(ev.get("selection", ""), s1, s2)
                if res:
                    ev["status"] = res
                    ev["final_score"] = final_score_str
                    changed = True

        if not changed:
            continue

        # Проверяем общий статус купона (ординар или экспресс)
        statuses = [ev.get("status", "active") for ev in events]
        new_coupon_status = "active"
        effective_odd = 1.0

        if "lost" in statuses:
            new_coupon_status = "lost"
        elif all(st in ("won", "void") for st in statuses):
            if all(st == "void" for st in statuses):
                new_coupon_status = "void"
                effective_odd = 1.0
            else:
                new_coupon_status = "won"
                for ev in events:
                    if ev.get("status") == "won":
                        effective_odd *= float(ev.get("odd", 1.0))
                effective_odd = round(effective_odd, 2)

        bet_patch: Dict[str, Any] = {
            "events": events,
            "status": new_coupon_status,
        }

        uid = fb_key(bet.get("user_id", ""))
        stake_stars = float(bet.get("amount_stars", 0))
        stake_disp = float(bet.get("amount", 0))
        cur = bet.get("currency", "STARS")
        sym = "₽" if cur == "RUB" else "★"

        if new_coupon_status == "won":
            payout_stars = round(stake_stars * effective_odd, 2)
            payout_disp = round(stake_disp * effective_odd, 2)
            bet_patch["odd"] = effective_odd
            bet_patch["payout"] = payout_disp
            bet_patch["payout_stars"] = payout_stars

            user = await fb_get(f"users/{uid}")
            if isinstance(user, dict):
                new_bal = round(float(user.get("balance", 0)) + payout_stars, 2)
                new_won = int(user.get("won_bets", 0)) + 1
                new_prof = round(
                    float(user.get("profit", 0)) + (payout_stars - stake_stars), 2
                )
                await fb_patch(
                    f"users/{uid}",
                    {"balance": new_bal, "won_bets": new_won, "profit": new_prof},
                )

            if bot_instance and uid.isdigit():
                try:
                    await bot_instance.send_message(
                        int(uid),
                        f"✅ <b>Купон #{bet_id} выиграл!</b>\n"
                        f"Счёт матча: <b>{final_score_str}</b>\n"
                        f"Выплата: <b>+{payout_disp:.2f} {sym}</b>",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    pass

        elif new_coupon_status == "void":
            bet_patch["odd"] = 1.0
            bet_patch["payout"] = stake_disp
            bet_patch["payout_stars"] = stake_stars

            user = await fb_get(f"users/{uid}")
            if isinstance(user, dict):
                new_bal = round(float(user.get("balance", 0)) + stake_stars, 2)
                await fb_patch(f"users/{uid}", {"balance": new_bal})

            if bot_instance and uid.isdigit():
                try:
                    await bot_instance.send_message(
                        int(uid),
                        f"↩️ <b>Возврат по купону #{bet_id}</b>\n"
                        f"Счёт матча: <b>{final_score_str}</b>\n"
                        f"Возвращено: <b>{stake_disp:.2f} {sym}</b>",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    pass

        elif new_coupon_status == "lost":
            user = await fb_get(f"users/{uid}")
            if isinstance(user, dict):
                new_prof = round(float(user.get("profit", 0)) - stake_stars, 2)
                await fb_patch(f"users/{uid}", {"profit": new_prof})

            if bot_instance and uid.isdigit():
                try:
                    await bot_instance.send_message(
                        int(uid),
                        f"❌ <b>Купон #{bet_id} не сыграл</b>\n"
                        f"Счёт матча: <b>{final_score_str}</b>",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    pass

        await fb_patch(f"bets/{bet_id}", bet_patch)
        settled_count += 1

    return settled_count


async def run_auto_settlement(bot_instance: Optional[Bot] = None) -> int:
    results = await fb_get("match_results", {})
    if not isinstance(results, dict):
        return 0
    total_settled = 0
    for mid, info in results.items():
        if isinstance(info, dict) and info.get("finished"):
            cnt = await settle_match_in_bets(
                str(mid),
                int(info.get("score1", 0)),
                int(info.get("score2", 0)),
                bot_instance=bot_instance,
            )
            total_settled += cnt
    return total_settled


async def line_updater_loop(bot_instance: Optional[Bot] = None) -> None:
    while True:
        try:
            logos = await fb_get("logos", {})
            if isinstance(logos, dict):
                LINE_CACHE["logos"] = logos

            raw = await asyncio.to_thread(
                fetch_json_mirror, "/events/list?lang=ru&scopeMarket=1600"
            )
            if raw and "events" in raw:
                finished_map = parse_fonbet_line(raw)
                for mid, info in finished_map.items():
                    await fb_put(f"match_results/{fb_key(mid)}", info)
                    await settle_match_in_bets(
                        mid, info["score1"], info["score2"], bot_instance=bot_instance
                    )
        except Exception as e:
            logger.error("Line updater error: %s", e)
        await asyncio.sleep(15)


async def settle_bets_worker(bot_instance: Optional[Bot] = None) -> None:
    while True:
        await asyncio.sleep(45)
        try:
            await run_auto_settlement(bot_instance=bot_instance)
        except Exception as e:
            logger.error("Settle worker error: %s", e)


# ================= AIOGRAM 3 TELEGRAM BOT =================
bot: Optional[Bot] = Bot(token=BOT_TOKEN) if BOT_TOKEN else None
dp = Dispatcher()
router = Router()


def is_admin(user_id: int) -> bool:
    return str(user_id) in ADMIN_IDS


def main_webapp_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть WinStars",
                    web_app=WebAppInfo(url=WEBAPP_URL),
                )
            ]
        ]
    )


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    if not message.from_user:
        return
    u = message.from_user
    parts = (message.text or "").split(maxsplit=1)
    ref_id = None
    if len(parts) > 1 and parts[1].startswith("ref_"):
        ref_id = parts[1].replace("ref_", "").strip()

    full_name = " ".join(filter(None, [u.first_name, u.last_name])) or "Игрок"
    await get_or_create_user(
        str(u.id),
        name=full_name,
        username=u.username or "",
        referrer_id=ref_id,
    )

    await message.answer(
        f"Добро пожаловать в <b>WINSTARS</b>, {full_name}!\n\n"
        f"• Актуальная футбольная линия и Live-события\n"
        f"• Одинары и экспрессы с мгновенным расчётом\n"
        f"• Пополнение и вывод в Telegram Stars и по СБП\n\n"
        f"Нажмите кнопку ниже, чтобы запустить приложение:",
        parse_mode=ParseMode.HTML,
        reply_markup=main_webapp_keyboard(),
    )


@router.pre_checkout_query()
async def process_pre_checkout(query: PreCheckoutQuery) -> None:
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def process_successful_payment(message: Message) -> None:
    if not message.successful_payment or not message.from_user:
        return
    sp = message.successful_payment
    payload = sp.invoice_payload or ""
    # Формат payload: topup_<user_id>_<stars>_<ts>
    parts = payload.split("_")
    uid = fb_key(parts[1]) if len(parts) >= 3 else fb_key(str(message.from_user.id))
    stars = float(sp.total_amount)

    user = await get_or_create_user(uid)
    new_bal = round(float(user.get("balance", 0)) + stars, 2)
    await fb_patch(f"users/{uid}", {"balance": new_bal})

    tx_id = uuid.uuid4().hex[:10].upper()
    await fb_put(
        f"transactions/{tx_id}",
        {
            "id": tx_id,
            "user_id": uid,
            "type": "deposit",
            "method": "Telegram Stars",
            "currency": "STARS",
            "amount_rub": round(stars * STAR_RATE, 2),
            "amount_stars": stars,
            "status": "completed",
            "details": "Оплата Telegram Stars",
            "created_at": datetime.now(MSK).strftime("%d.%m %H:%M"),
        },
    )

    await message.answer(
        f"✅ <b>Баланс пополнен на {int(stars)} ★!</b>\n"
        f"Текущий баланс: <b>{new_bal} ★</b>",
        parse_mode=ParseMode.HTML,
    )


# ================= ADMIN COMMANDS & CALLBACKS =================
@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not message.from_user or not is_admin(message.from_user.id):
        return

    users = await fb_get("users", {})
    bets = await fb_get("bets", {})
    txs = await fb_get("transactions", {})

    u_cnt = len(users) if isinstance(users, dict) else 0
    b_cnt = len(bets) if isinstance(bets, dict) else 0
    active_b = (
        sum(1 for b in bets.values() if isinstance(b, dict) and b.get("status") == "active")
        if isinstance(bets, dict)
        else 0
    )
    pending_tx = (
        [t for t in txs.values() if isinstance(t, dict) and t.get("status") == "pending"]
        if isinstance(txs, dict)
        else []
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Заявки кассы ({len(pending_tx)})",
                    callback_data="adm_pending_tx",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Пересчитать ставки (/recalc)",
                    callback_data="adm_recalc",
                )
            ],
        ]
    )

    await message.answer(
        f"<b>Панель администратора WinStars</b>\n\n"
        f"• Игроков: <b>{u_cnt}</b>\n"
        f"• Всего ставок: <b>{b_cnt}</b> (активных: <b>{active_b}</b>)\n"
        f"• Заявок в ожидании: <b>{len(pending_tx)}</b>\n\n"
        f"<b>Команды:</b>\n"
        f"<code>/settle &lt;match_id&gt; &lt;счёт&gt;</code> — закрыть матч (напр. <code>/settle 12345 2:1</code>)\n"
        f"<code>/recalc</code> — авторасчёт по архиву результатов\n"
        f"<code>/addbal &lt;user_id&gt; &lt;stars&gt;</code> — начислить/списать Stars\n"
        f"<code>/promo &lt;КОД&gt; &lt;БОНУС&gt; [ЛИМИТ]</code> — создать промокод\n"
        f"<code>/logo &lt;команда&gt; &lt;url&gt;</code> — задать логотип команды",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


@router.callback_query(F.data == "adm_recalc")
async def cb_adm_recalc(call: CallbackQuery) -> None:
    if not call.from_user or not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    cnt = await run_auto_settlement(bot_instance=bot)
    await call.answer(f"Пересчитано ставок: {cnt}", show_alert=True)


@router.callback_query(F.data == "adm_pending_tx")
async def cb_adm_pending_tx(call: CallbackQuery) -> None:
    if not call.from_user or not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return

    txs = await fb_get("transactions", {})
    pending = (
        [t for t in txs.values() if isinstance(t, dict) and t.get("status") == "pending"]
        if isinstance(txs, dict)
        else []
    )
    if not pending:
        await call.answer("Нет ожидающих заявок", show_alert=True)
        return

    await call.answer()
    for t in pending[:10]:
        tid = t.get("id")
        is_dep = t.get("type") == "deposit"
        title = "Пополнение СБП" if is_dep else f"Вывод ({t.get('method')})"
        amt_str = (
            f"{t.get('amount_rub')} ₽ ({t.get('amount_stars')} ★)"
            if t.get("currency") == "RUB"
            else f"{t.get('amount_stars')} ★"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Одобрить", callback_data=f"tx_ok:{tid}"
                    ),
                    InlineKeyboardButton(
                        text="Отклонить", callback_data=f"tx_no:{tid}"
                    ),
                ]
            ]
        )
        await call.message.answer(
            f"<b>Заявка #{tid}</b> — {title}\n"
            f"Игрок: <code>{t.get('user_id')}</code>\n"
            f"Сумма: <b>{amt_str}</b>\n"
            f"Реквизиты: {t.get('details', '—')}",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )


@router.callback_query(F.data.startswith("tx_ok:"))
async def cb_tx_approve(call: CallbackQuery) -> None:
    if not call.from_user or not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    tid = call.data.split(":", 1)[1]
    tx = await fb_get(f"transactions/{tid}")
    if not isinstance(tx, dict) or tx.get("status") != "pending":
        await call.answer("Заявка уже обработана", show_alert=True)
        return

    uid = fb_key(tx.get("user_id", ""))
    stars = float(tx.get("amount_stars", 0))
    if tx.get("type") == "deposit":
        user = await get_or_create_user(uid)
        new_bal = round(float(user.get("balance", 0)) + stars, 2)
        await fb_patch(f"users/{uid}", {"balance": new_bal})

    await fb_patch(f"transactions/{tid}", {"status": "completed"})
    await call.message.edit_text(
        f"✅ Заявка <b>#{tid}</b> одобрена.", parse_mode=ParseMode.HTML
    )
    if bot and uid.isdigit():
        try:
            msg_txt = (
                f"✅ Пополнение <b>#{tid}</b> на <b>{stars} ★</b> зачислено!"
                if tx.get("type") == "deposit"
                else f"✅ Заявка на вывод <b>#{tid}</b> выполнена!"
            )
            await bot.send_message(int(uid), msg_txt, parse_mode=ParseMode.HTML)
        except Exception:
            pass


@router.callback_query(F.data.startswith("tx_no:"))
async def cb_tx_reject(call: CallbackQuery) -> None:
    if not call.from_user or not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    tid = call.data.split(":", 1)[1]
    tx = await fb_get(f"transactions/{tid}")
    if not isinstance(tx, dict) or tx.get("status") != "pending":
        await call.answer("Заявка уже обработана", show_alert=True)
        return

    uid = fb_key(tx.get("user_id", ""))
    stars = float(tx.get("amount_stars", 0))
    # При отклонении вывода возвращаем списанные Stars пользователю
    if tx.get("type") == "withdraw":
        user = await get_or_create_user(uid)
        new_bal = round(float(user.get("balance", 0)) + stars, 2)
        await fb_patch(f"users/{uid}", {"balance": new_bal})

    await fb_patch(
        f"transactions/{tid}",
        {"status": "rejected", "reject_reason": "Отклонено администратором"},
    )
    await call.message.edit_text(
        f"❌ Заявка <b>#{tid}</b> отклонена.", parse_mode=ParseMode.HTML
    )
    if bot and uid.isdigit():
        try:
            msg_txt = (
                f"❌ Заявка на вывод <b>#{tid}</b> отклонена. Средства (<b>{stars} ★</b>) возвращены на баланс."
                if tx.get("type") == "withdraw"
                else f"❌ Заявка на пополнение <b>#{tid}</b> отклонена."
            )
            await bot.send_message(int(uid), msg_txt, parse_mode=ParseMode.HTML)
        except Exception:
            pass


@router.message(Command("settle"))
async def cmd_settle(message: Message) -> None:
    if not message.from_user or not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 3 or not re.match(r"^\d+[:\-]\d+$", parts[2]):
        await message.answer("Формат: <code>/settle &lt;match_id&gt; 2:1</code>", parse_mode=ParseMode.HTML)
        return

    mid = parts[1].strip()
    s1_str, s2_str = re.split(r"[:\-]", parts[2])
    s1, s2 = int(s1_str), int(s2_str)

    await fb_put(
        f"match_results/{fb_key(mid)}",
        {
            "score1": s1,
            "score2": s2,
            "finished": True,
            "updated_at": time.time(),
        },
    )
    cnt = await settle_match_in_bets(mid, s1, s2, bot_instance=bot)
    await message.answer(
        f"✅ Матч <code>{mid}</code> закрыт со счётом <b>{s1}:{s2}</b>.\n"
        f"Рассчитано купонов: <b>{cnt}</b>",
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("recalc"))
async def cmd_recalc(message: Message) -> None:
    if not message.from_user or not is_admin(message.from_user.id):
        return
    cnt = await run_auto_settlement(bot_instance=bot)
    await message.answer(f"🔄 Перерасчёт завершён. Обновлено купонов: <b>{cnt}</b>", parse_mode=ParseMode.HTML)


@router.message(Command("addbal"))
async def cmd_addbal(message: Message) -> None:
    if not message.from_user or not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer("Формат: <code>/addbal &lt;user_id&gt; &lt;stars&gt;</code>", parse_mode=ParseMode.HTML)
        return
    uid = fb_key(parts[1])
    delta = float(parts[2])
    user = await get_or_create_user(uid)
    new_bal = round(max(0.0, float(user.get("balance", 0)) + delta), 2)
    await fb_patch(f"users/{uid}", {"balance": new_bal})
    await message.answer(
        f"✅ Баланс игрока <code>{uid}</code> обновлён: <b>{new_bal} ★</b>",
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("promo"))
async def cmd_promo(message: Message) -> None:
    if not message.from_user or not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer(
            "Формат: <code>/promo КОД БОНУС [МАКС_ИСПОЛЬЗОВАНИЙ]</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    code = fb_key(parts[1].upper())
    bonus = float(parts[2])
    max_uses = int(parts[3]) if len(parts) >= 4 else 100
    await fb_put(
        f"promos/{code}",
        {"bonus": bonus, "max_uses": max_uses, "used_by": []},
    )
    await message.answer(
        f"🎁 Промокод <code>{code}</code> на <b>{bonus} ★</b> (лимит: {max_uses}) создан.",
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("logo"))
async def cmd_logo(message: Message) -> None:
    if not message.from_user or not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await message.answer(
            "Формат: <code>/logo НазваниеКоманды https://...png</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    team_k = fb_key(parts[1].lower())
    url = parts[2].strip()
    await fb_patch("logos", {team_k: url})
    LINE_CACHE.setdefault("logos", {})[team_k] = url
    await message.answer(f"🖼 Логотип для <b>{parts[1]}</b> сохранён.", parse_mode=ParseMode.HTML)


dp.include_router(router)


# ================= FASTAPI LIFESPAN & APP =================
@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    http_client = httpx.AsyncClient(timeout=12.0)

    tasks = [
        asyncio.create_task(line_updater_loop(bot_instance=bot)),
        asyncio.create_task(settle_bets_worker(bot_instance=bot)),
    ]

    if bot is not None:
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            tasks.append(asyncio.create_task(dp.start_polling(bot)))
            logger.info("Aiogram polling started.")
        except Exception as e:
            logger.error("Failed to start bot polling: %s", e)

    yield

    for t in tasks:
        t.cancel()
    if bot is not None:
        await bot.session.close()
    if http_client is not None:
        await http_client.aclose()


app = FastAPI(title="WinStars API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ================= API SCHEMAS =================
class BetEventItem(BaseModel):
    match_id: str
    match_title: str
    league: Optional[str] = ""
    selection: str
    odd: float


class PlaceBetRequest(BaseModel):
    user_id: str
    match_id: str
    selection: str
    odd: float
    amount: float
    currency: str = "STARS"
    events: List[BetEventItem] = []


class InvoiceRequest(BaseModel):
    user_id: str
    stars: int


class DepositRubRequest(BaseModel):
    user_id: str
    amount: float
    currency: str = "RUB"
    method: str = "sbp"


class WithdrawRequest(BaseModel):
    user_id: str
    amount: float
    currency: str = "STARS"
    method: str = "stars"
    bank: str = ""
    details: str = ""


class PromoRequest(BaseModel):
    user_id: str
    code: str


class ChatSendRequest(BaseModel):
    user_id: str
    user: str
    username: str = ""
    photo_url: str = ""
    text: str


# ================= API ENDPOINTS =================
@app.get("/")
@app.get("/health")
async def healthcheck() -> Dict[str, Any]:
    return {
        "status": "ok",
        "live_count": len(LINE_CACHE["live"]),
        "prematch_count": len(LINE_CACHE["prematch"]),
    }


@app.get("/api/init/{user_id}")
async def api_init(
    user_id: str, name: str = "Игрок", username: str = "", photo_url: str = ""
) -> Dict[str, Any]:
    user = await get_or_create_user(
        user_id, name=name, username=username, photo_url=photo_url
    )
    return {
        "user": user,
        "star_rate": STAR_RATE,
        "bot_username": BOT_USERNAME,
        "live": LINE_CACHE["live"],
        "prematch": LINE_CACHE["prematch"],
    }


@app.get("/api/matches")
async def api_matches() -> Dict[str, Any]:
    return {
        "live": LINE_CACHE["live"],
        "prematch": LINE_CACHE["prematch"],
    }


@app.get("/api/match/{match_id}/markets")
async def api_match_markets(match_id: str) -> Dict[str, Any]:
    m = LINE_CACHE["by_id"].get(str(match_id))
    if not m:
        raise HTTPException(status_code=404, detail="Матч не найден в текущей линии")
    return m


@app.post("/api/bet")
async def api_place_bet(req: PlaceBetRequest) -> Dict[str, Any]:
    uid = fb_key(req.user_id)
    user = await get_or_create_user(uid)
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Сумма ставки должна быть больше 0")

    cur = req.currency.upper()
    stake_stars = (
        round(req.amount / STAR_RATE, 2) if cur == "RUB" else round(req.amount, 2)
    )
    if stake_stars < 1:
        raise HTTPException(status_code=400, detail="Минимальная ставка 1 ★")

    if float(user.get("balance", 0)) < stake_stars:
        raise HTTPException(status_code=400, detail="Недостаточно средств на балансе")

    events_list = (
        [e.model_dump() for e in req.events]
        if req.events
        else [
            {
                "match_id": req.match_id,
                "match_title": f"Матч #{req.match_id}",
                "league": "",
                "selection": req.selection,
                "odd": req.odd,
            }
        ]
    )

    total_odd = 1.0
    for ev in events_list:
        ev["status"] = "active"
        ev["final_score"] = ""
        total_odd *= float(ev["odd"])
    total_odd = round(total_odd, 2)

    new_balance = round(float(user.get("balance", 0)) - stake_stars, 2)
    new_total_bets = int(user.get("total_bets", 0)) + 1
    await fb_patch(
        f"users/{uid}",
        {"balance": new_balance, "total_bets": new_total_bets},
    )

    bet_id = uuid.uuid4().hex[:10].upper()
    bet_record = {
        "id": bet_id,
        "user_id": uid,
        "bet_type": "express" if len(events_list) > 1 else "single",
        "match_id": events_list[0]["match_id"],
        "match_title": events_list[0]["match_title"],
        "selection": events_list[0]["selection"],
        "odd": total_odd,
        "amount": round(req.amount, 2),
        "amount_stars": stake_stars,
        "currency": cur,
        "payout": 0.0,
        "payout_stars": 0.0,
        "status": "active",
        "events": events_list,
        "ts": time.time(),
        "created_at": datetime.now(MSK).strftime("%d.%m.%Y %H:%M"),
    }

    await fb_put(f"bets/{bet_id}", bet_record)
    return {"status": "ok", "bet": bet_record, "balance": new_balance}


@app.get("/api/bets/{user_id}")
async def api_user_bets(user_id: str) -> List[Dict[str, Any]]:
    uid = fb_key(user_id)
    all_bets = await fb_get("bets", {})
    if not isinstance(all_bets, dict):
        return []
    res = [
        b
        for b in all_bets.values()
        if isinstance(b, dict) and fb_key(b.get("user_id", "")) == uid
    ]
    res.sort(key=lambda x: float(x.get("ts", 0)), reverse=True)
    return res


@app.post("/api/invoice")
@app.post("/api/cashier/invoice")
async def api_create_invoice(req: InvoiceRequest) -> Dict[str, Any]:
    if req.stars < 10:
        raise HTTPException(status_code=400, detail="Минимальное пополнение 10 ★")
    if not BOT_TOKEN or http_client is None:
        raise HTTPException(status_code=500, detail="BOT_TOKEN не настроен на сервере")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/createInvoiceLink"
    payload = {
        "title": f"Пополнение WinStars ({req.stars} ★)",
        "description": f"Зачисление {req.stars} Stars на баланс WinStars",
        "payload": f"topup_{fb_key(req.user_id)}_{req.stars}_{int(time.time())}",
        "currency": "XTR",
        "prices": [{"label": "Stars", "amount": req.stars}],
    }
    resp = await http_client.post(url, json=payload)
    data = resp.json()
    if not data.get("ok"):
        raise HTTPException(
            status_code=400, detail=data.get("description", "Ошибка Telegram API")
        )
    return {"invoice_url": data["result"]}


@app.post("/api/cashier/deposit")
async def api_deposit_rub(req: DepositRubRequest) -> Dict[str, Any]:
    if req.amount < 50:
        raise HTTPException(status_code=400, detail="Минимальное пополнение 50 ₽")
    uid = fb_key(req.user_id)
    await get_or_create_user(uid)

    tx_id = uuid.uuid4().hex[:8].upper()
    tx = {
        "id": tx_id,
        "user_id": uid,
        "type": "deposit",
        "method": "СБП",
        "currency": "RUB",
        "amount_rub": round(req.amount, 2),
        "amount_stars": round(req.amount / STAR_RATE, 2),
        "status": "pending",
        "details": "Ожидает проверки перевода СБП",
        "ts": time.time(),
        "created_at": datetime.now(MSK).strftime("%d.%m %H:%M"),
    }
    await fb_put(f"transactions/{tx_id}", tx)

    if bot:
        for adm in ADMIN_IDS:
            if adm.isdigit():
                try:
                    await bot.send_message(
                        int(adm),
                        f"💳 Новая заявка на пополнение СБП <b>#{tx_id}</b>\n"
                        f"Игрок: <code>{uid}</code> | Сумма: <b>{req.amount} ₽</b>\n"
                        f"Откройте /admin для подтверждения.",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    pass

    return {"status": "ok", "message": "Заявка на пополнение по СБП создана", "tx": tx}


@app.post("/api/cashier/withdraw")
async def api_withdraw(req: WithdrawRequest) -> Dict[str, Any]:
    uid = fb_key(req.user_id)
    user = await get_or_create_user(uid)
    cur = req.currency.upper()

    if cur == "RUB":
        if req.amount < 1500:
            raise HTTPException(status_code=400, detail="Минимальный вывод 1500 ₽")
        if not req.details:
            raise HTTPException(status_code=400, detail="Укажите номер телефона для СБП")
        stars_needed = round(req.amount / STAR_RATE, 2)
        amt_rub = round(req.amount, 2)
    else:
        if req.amount < 1000:
            raise HTTPException(status_code=400, detail="Минимальный вывод 1000 ★")
        stars_needed = round(req.amount, 2)
        amt_rub = round(req.amount * STAR_RATE, 2)

    if float(user.get("balance", 0)) < stars_needed:
        raise HTTPException(status_code=400, detail="Недостаточно средств на балансе")

    new_balance = round(float(user.get("balance", 0)) - stars_needed, 2)
    await fb_patch(f"users/{uid}", {"balance": new_balance})

    tx_id = uuid.uuid4().hex[:8].upper()
    tx = {
        "id": tx_id,
        "user_id": uid,
        "type": "withdraw",
        "method": "СБП" if cur == "RUB" else "Telegram Stars",
        "currency": cur,
        "amount_rub": amt_rub,
        "amount_stars": stars_needed,
        "status": "pending",
        "details": f"{req.bank} {req.details}".strip() if cur == "RUB" else "Вывод Stars",
        "ts": time.time(),
        "created_at": datetime.now(MSK).strftime("%d.%m %H:%M"),
    }
    await fb_put(f"transactions/{tx_id}", tx)

    if bot:
        for adm in ADMIN_IDS:
            if adm.isdigit():
                try:
                    await bot.send_message(
                        int(adm),
                        f"📤 Новая заявка на вывод <b>#{tx_id}</b>\n"
                        f"Игрок: <code>{uid}</code> | Сумма: <b>{stars_needed} ★</b>\n"
                        f"Откройте /admin для обработки.",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    pass

    return {"status": "ok", "balance": new_balance, "tx": tx}


@app.post("/api/promo/activate")
async def api_activate_promo(req: PromoRequest) -> Dict[str, Any]:
    uid = fb_key(req.user_id)
    user = await get_or_create_user(uid)
    code = fb_key(req.code.strip().upper())

    promo = await fb_get(f"promos/{code}")
    if not isinstance(promo, dict):
        raise HTTPException(status_code=404, detail="Промокод не найден")

    used_by = promo.get("used_by") or []
    if uid in used_by:
        raise HTTPException(status_code=400, detail="Вы уже активировали этот промокод")
    if len(used_by) >= int(promo.get("max_uses", 100)):
        raise HTTPException(status_code=400, detail="Лимит активаций промокода исчерпан")

    bonus = float(promo.get("bonus", 0))
    used_by.append(uid)
    await fb_patch(f"promos/{code}", {"used_by": used_by})

    new_balance = round(float(user.get("balance", 0)) + bonus, 2)
    await fb_patch(f"users/{uid}", {"balance": new_balance})

    tx_id = uuid.uuid4().hex[:8].upper()
    await fb_put(
        f"transactions/{tx_id}",
        {
            "id": tx_id,
            "user_id": uid,
            "type": "deposit",
            "method": f"Промокод {code}",
            "currency": "STARS",
            "amount_rub": round(bonus * STAR_RATE, 2),
            "amount_stars": bonus,
            "status": "completed",
            "details": "Бонус по промокоду",
            "ts": time.time(),
            "created_at": datetime.now(MSK).strftime("%d.%m %H:%M"),
        },
    )
    return {"status": "ok", "bonus": bonus, "balance": new_balance}


@app.get("/api/transactions/{user_id}")
async def api_transactions(user_id: str) -> List[Dict[str, Any]]:
    uid = fb_key(user_id)
    all_tx = await fb_get("transactions", {})
    if not isinstance(all_tx, dict):
        return []
    res = [
        t
        for t in all_tx.values()
        if isinstance(t, dict) and fb_key(t.get("user_id", "")) == uid
    ]
    res.sort(key=lambda x: float(x.get("ts", 0)), reverse=True)
    return res


# ================= WEBSOCKET & HTTP CHAT =================
class ChatConnectionManager:
    def __init__(self) -> None:
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, payload: Dict[str, Any]) -> None:
        dead: List[WebSocket] = []
        for ws in self.active:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


chat_manager = ChatConnectionManager()


async def get_recent_chat_messages(limit: int = 60) -> List[Dict[str, Any]]:
    raw = await fb_get("chat", {})
    if isinstance(raw, list):
        msgs = [m for m in raw if isinstance(m, dict)]
    elif isinstance(raw, dict):
        msgs = [m for m in raw.values() if isinstance(m, dict)]
    else:
        msgs = []
    msgs.sort(key=lambda x: float(x.get("ts", 0)))
    return msgs[-limit:]


async def save_chat_message(data: Dict[str, Any]) -> Dict[str, Any]:
    msg_id = uuid.uuid4().hex[:10]
    msg = {
        "id": msg_id,
        "user_id": str(data.get("user_id", "")),
        "user": str(data.get("user", "Игрок"))[:40],
        "username": str(data.get("username", ""))[:40],
        "photo_url": str(data.get("photo_url", "")),
        "text": str(data.get("text", "")).strip()[:300],
        "ts": time.time(),
        "time": datetime.now(MSK).strftime("%H:%M"),
    }
    await fb_put(f"chat/{msg_id}", msg)
    return msg


@app.get("/api/chat/history")
async def api_chat_history() -> List[Dict[str, Any]]:
    return await get_recent_chat_messages(60)


@app.post("/api/chat/send")
async def api_chat_send(req: ChatSendRequest) -> Dict[str, Any]:
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Пустое сообщение")
    msg = await save_chat_message(req.model_dump())
    await chat_manager.broadcast({"type": "message", "message": msg})
    return {"status": "ok", "message": msg}


@app.websocket("/ws/chat")
async def ws_chat_endpoint(ws: WebSocket) -> None:
    await chat_manager.connect(ws)
    history = await get_recent_chat_messages(60)
    await ws.send_json({"type": "history", "messages": history})
    try:
        while True:
            raw = await ws.receive_json()
            if str(raw.get("text", "")).strip():
                msg = await save_chat_message(raw)
                await chat_manager.broadcast({"type": "message", "message": msg})
    except WebSocketDisconnect:
        chat_manager.disconnect(ws)
    except Exception:
        chat_manager.disconnect(ws)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
