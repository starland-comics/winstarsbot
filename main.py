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
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, LabeledPrice
from curl_cffi import requests as cffi_requests

BOT_TOKEN = "9010161317:AAEMgcEP5yhgMiyV8OCOwJ5fpxmAUrg0ke8"
WEBAPP_URL = "https://starland-comics.github.io/winstarsbot/"
FIREBASE_DB_URL = "https://winstars-435f1-default-rtdb.firebaseio.com"

FONBET_BASE = "https://line-lb51.bk6bba-resources.com"
FONBET_LINE_PATH = "/events/listBase?scopeMarket=1600&lang=ru"
FONBET_LIVE_PATH = "/events/list?scopeMarket=1600&lang=ru&live=true"

FONBET_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.fon.bet/",
    "Origin": "https://www.fon.bet"
}

LIVE_POLL_SEC = 25
SETTLE_POLL_SEC = 60
FINISHED_TTL = 90

FONBET_FACTORS = {
    921: ("Исход", "П1", False), 922: ("Исход", "Х", False), 923: ("Исход", "П2", False),
    924: ("Двойной шанс", "1Х", False), 925: ("Двойной шанс", "12", False), 926: ("Двойной шанс", "Х2", False),
    927: ("Обе забьют", "Нет", False), 928: ("Обе забьют", "Да", False),
    930: ("Тотал", "ТБ", True), 931: ("Тотал", "ТМ", True),
    910: ("Фора", "Ф1", True), 912: ("Фора", "Ф2", True),
    989: ("Фора", "Ф1", True), 991: ("Фора", "Ф2", True),
    1672: ("Фора", "Ф1", True), 1675: ("Фора", "Ф2", True),
    1677: ("Фора", "Ф1", True), 1678: ("Фора", "Ф2", True),
    1680: ("Фора", "Ф1", True), 1681: ("Фора", "Ф2", True),
    1696: ("Инд тотал 1", "ТБ", True), 1697: ("Инд тотал 2", "ТБ", True),
    1727: ("Инд тотал 1", "ТБ", True), 1728: ("Инд тотал 2", "ТБ", True),
    1730: ("Инд тотал 1", "ТБ", True), 1731: ("Инд тотал 2", "ТБ", True),
    1733: ("Инд тотал 1", "ТБ", True), 1734: ("Инд тотал 2", "ТБ", True),
    1736: ("Инд тотал 1", "ТБ", True), 1737: ("Инд тотал 2", "ТБ", True),
    1739: ("Инд тотал 1", "ТБ", True), 1791: ("Инд тотал 2", "ТБ", True),
    1793: ("Инд тотал 1", "ТБ", True), 1794: ("Инд тотал 2", "ТБ", True),
    1796: ("Инд тотал 1", "ТБ", True), 1797: ("Инд тотал 2", "ТБ", True),
    1809: ("Фора", "Ф1", True), 1810: ("Фора", "Ф2", True),
    1812: ("Фора", "Ф1", True), 1813: ("Фора", "Ф2", True),
    1815: ("Фора", "Ф1", True), 1816: ("Фора", "Ф2", True),
    1818: ("Фора", "Ф1", True), 1819: ("Фора", "Ф2", True),
    1821: ("Фора", "Ф1", True), 1822: ("Фора", "Ф2", True),
    1569: ("Тотал", "ТМ", True), 1572: ("Тотал", "ТБ", True),
    1854: ("Тотал", "ТБ", True), 1871: ("Тотал", "ТМ", True),
    1873: ("Тотал", "ТБ", True), 1874: ("Тотал", "ТМ", True),
    1880: ("Тотал", "ТБ", True), 1881: ("Тотал", "ТМ", True),
    1883: ("Тотал", "ТБ", True), 1884: ("Тотал", "ТМ", True),
    1886: ("Тотал", "ТБ", True), 1887: ("Тотал", "ТМ", True),
    4241: ("Обе забьют 1-й тайм", "Нет", False), 4242: ("Обе забьют 1-й тайм", "Да", False),
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
_FIREBASE_BAD_CHARS = re.compile(r'[\.\$#\[\]/]')

app = FastAPI(title="WinStars API")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

http_client = httpx.AsyncClient(timeout=10.0)

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
    try: await http_client.patch(f"{FIREBASE_DB_URL}/{path}.json", json=data)
    except: pass

async def firebase_delete(path: str):
    try: await http_client.delete(f"{FIREBASE_DB_URL}/{path}.json")
    except: pass

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
    print("   ⚠️ Saving per-match (fallback)...")
    existing = await firebase_get("matches") or {}
    if isinstance(existing, dict):
        to_delete = set(existing.keys()) - set(matches_dict.keys())
        for k in to_delete:
            try: await http_client.delete(f"{FIREBASE_DB_URL}/matches/{k}.json")
            except: pass
    
    for mid, mdata in matches_dict.items():
        bad = _find_bad_key(mdata, "")
        if bad:
            print(f"   ❌ {mid}: {bad} — пропускаю")
            continue
        try:
            r = await http_client.put(f"{FIREBASE_DB_URL}/matches/{mid}.json", json=mdata, timeout=15.0)
            if r.status_code != 200:
                print(f"   ⚠️ save {mid}: HTTP {r.status_code} | {r.text[:100]}")
        except Exception as e:
            print(f"   ⚠️ save {mid}: {e}")

async def _save_matches(matches_dict):
    if not matches_dict: return
    bad = _find_bad_key(matches_dict, "")
    if bad:
        print(f"   ❌ [SAVE] Невалидный ключ: {bad}")
        await _save_matches_fallback(matches_dict)
        return
    
    try:
        payload = json.dumps(matches_dict, allow_nan=False, ensure_ascii=False)
        size = len(payload)
    except ValueError as e:
        print(f"   ⚠️ JSON serialization error: {e}")
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
                print(f"   ⚠️ bulk PUT failed: {r.status_code} {r.text[:200]} -> fallback")
                await _save_matches_fallback(matches_dict)
        except Exception as e:
            print(f"   ⚠️ bulk PUT exception: {e} -> fallback")
            await _save_matches_fallback(matches_dict)
    else:
        await _save_matches_fallback(matches_dict)

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

def _normalize_p(p_val):
    try:
        num = float(p_val)
    except (TypeError, ValueError):
        return 0.0
    if num != int(num):
        return num
    if abs(num) >= 10:
        return num / 100.0
    return num

def _extract_number_from_name(name):
    m = re.search(r'([+-]?\d+(?:\.\d+)?)', name)
    if m: return float(m.group(1))
    return 999.0

_LOGGED_FLIPS = set()

def _fix_market_orientation(markets):
    for group_name, outcomes in markets.items():
        if not (group_name == "Тотал" or group_name.startswith("Инд ")):
            continue
        tb_items = []; tm_items = []
        for o in outcomes:
            n = o["name"]
            try:
                if n.startswith("ТБ "): tb_items.append((float(n[3:]), o))
                elif n.startswith("ТМ "): tm_items.append((float(n[3:]), o))
            except (ValueError, IndexError):
                continue
        
        if tb_items and not tm_items and len(tb_items) >= 3:
            tb_items.sort(key=lambda x: x[0])
            violations = sum(1 for i in range(1, len(tb_items)) if tb_items[i][1]["odds"] < tb_items[i-1][1]["odds"])
            if violations >= len(tb_items) - 2:
                for lim, o in tb_items:
                    o["name"] = f"ТМ {lim:g}"
                if group_name not in _LOGGED_FLIPS:
                    _LOGGED_FLIPS.add(group_name)
                    print(f"   🔄 Auto-flip '{group_name}': ТБ→ТМ (violations={violations}/{len(tb_items)-1})")
                continue
        
        if tm_items and not tb_items and len(tm_items) >= 3:
            tm_items.sort(key=lambda x: x[0])
            violations = sum(1 for i in range(1, len(tm_items)) if tm_items[i][1]["odds"] > tm_items[i-1][1]["odds"])
            if violations >= len(tm_items) - 2:
                for lim, o in tm_items:
                    o["name"] = f"ТБ {lim:g}"
                key = group_name + "|tm"
                if key not in _LOGGED_FLIPS:
                    _LOGGED_FLIPS.add(key)
                    print(f"   🔄 Auto-flip '{group_name}': ТМ→ТБ (violations={violations}/{len(tm_items)-1})")

def _parse_markets(factors_list):
    seen = set()
    markets = {}
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
            
            if p_val == 0 and (group == "Тотал" or group.startswith("Инд ")):
                continue
                
            if group == "Тотал" or group.startswith("Инд "):
                name = f"{base} {abs(p_val):g}"
            else:
                if p_val == int(p_val): p_str = str(int(p_val))
                else: p_str = f"{p_val:g}"
                if p_val > 0: p_str = f"+{p_str}"
                name = f"{base} {p_str}"
        
        key = (group, name)
        if key in seen: continue
        seen.add(key)
        if group not in markets: markets[group] = []
        markets[group].append({"name": name, "odds": odds})
    
    _fix_market_orientation(markets)
    
    for g in markets:
        markets[g].sort(key=lambda x: _extract_number_from_name(x['name']))
        
    return markets

def _get_group_score(group_name, s1, s2):
    if group_name == "Тотал": return s1 + s2
    if "Инд тотал 1" in group_name: return s1
    if "Инд тотал 2" in group_name: return s2
    return None

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
        elif g == "Тотал" or g.startswith("Инд "):
            group_score = _get_group_score(g, s1, s2)
            if group_score is None:
                filtered[g] = outs
                continue
            new_outs = _filter_total_group(outs, group_score)
            if new_outs: filtered[g] = new_outs
        else:
            filtered[g] = outs
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
    print(f"⚡ fonbet_scraper_worker запущен (опрос {LIVE_POLL_SEC} сек)...")
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
                
                eid = ev.get("id")
                mid = f"fonbet_{eid}"
                
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
                        # Матч уже начался, но live нет. Если он был LIVE и ещё "свежий" (<2.5ч)
                        # — оставляем замороженный снимок чтобы не пропадал при перерыве.
                        old_m = cached_prev_matches.get(mid)
                        if isinstance(old_m, dict):
                            old_status = old_m.get("status", "")
                            was_live = old_status.startswith("LIVE") or old_status == "ИДЕТ"
                            hours_since = (now_ts - st) / 3600.0
                            if was_live and hours_since < 2.5:
                                cached_markets = old_m.get("markets", {}) or {}
                                if isinstance(cached_markets, dict):
                                    for grp in cached_markets.values():
                                        if isinstance(grp, list):
                                            total_markets_count += len(grp)
                                matches_dict[mid] = {
                                    "title": old_m.get("title", f"{t1} - {t2}"),
                                    "home": t1, "away": t2,
                                    "homeLogo": old_m.get("homeLogo", logo_idx.get(t1, "")),
                                    "awayLogo": old_m.get("awayLogo", logo_idx.get(t2, "")),
                                    "league": league,
                                    "status": "ИДЕТ",
                                    "score": old_m.get("score", "VS"),
                                    "start_time": st,
                                    "odds": old_m.get("odds", {"1": None, "X": None, "2": None}),
                                    "markets": cached_markets
                                }
                                current_ids.add(mid)
                                cached_frozen += 1
                                continue
                        skipped_started_no_data += 1
                        continue
                
                current_ids.add(mid)
                
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
                if is_live:
                    if s1 is not None and s2 is not None: score = f"{s1} : {s2}"
                    status_str = f"LIVE {timer}'" if timer is not None else "LIVE"
                    live_cnt += 1
                else:
                    status_str = time.strftime("%d.%m %H:%M", time.localtime(st))
                
                matches_dict[mid] = {
                    "title": f"{t1} - {t2}", "home": t1, "away": t2,
                    "homeLogo": logo_idx.get(t1, ""), "awayLogo": logo_idx.get(t2, ""),
                    "league": league,
                    "status": status_str, "score": score,
                    "start_time": st,
                    "odds": {"1": p1, "X": px, "2": p2},
                    "markets": markets_data
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
                            await firebase_patch(f"finished_matches/{mid}", {
                                "score1": s1_old, "score2": s2_old, "source": "fonbet_last"
                            })
                        except: pass
            
            prev_event_ids = current_ids
            
            if matches_dict:
                try:
                    approx_size = len(json.dumps(matches_dict, ensure_ascii=False))
                    size_mb = approx_size / 1024 / 1024
                except:
                    size_mb = 0
                
                await _save_matches(matches_dict)
                extra = ""
                if skipped_stuck or skipped_started_no_data or cached_frozen:
                    extra = f" (зависших: {skipped_stuck}, без live: {skipped_started_no_data}, заморожено: {cached_frozen})"
                print(f"🔄 [FONBET] {len(matches_dict)} матчей (LIVE: {live_cnt}) | {total_markets_count} рынков | {size_mb:.1f}MB{extra} за {time.time()-t0:.1f}s")
        
        except Exception as e:
            import traceback
            print(f"⚠️ fonbet_scraper: {e}")
            traceback.print_exc()
        
        cycle += 1
        if cycle % 15 == 0: gc.collect()
        await asyncio.sleep(LIVE_POLL_SEC)

def calculate_outcome(outcome_str, s1, s2):
    o = outcome_str.lower().strip()
    if o in ["п1", "1", "победа 1", "победа хозяев"]: return s1 > s2
    if o in ["х", "x", "ничья"]: return s1 == s2
    if o in ["п2", "2", "победа 2", "победа гостей"]: return s2 > s1
    if o in ["1x", "1х"]: return s1 >= s2
    if o in ["12", "x2"]: return s1 != s2
    if o in ["x2", "х2"]: return s2 >= s1
    if o in ["да", "обе забьют", "оз да"]: return s1 > 0 and s2 > 0
    if o in ["нет", "оз нет"]: return s1 == 0 or s2 == 0
    m_over = re.search(r'(?:тб|тотал больше|больше)\s*([+-]?\d+(?:\.\d+)?)', o)
    if m_over:
        limit = float(m_over.group(1)); total = s1 + s2
        if total > limit: return True
        if total < limit: return False
        return None
    m_under = re.search(r'(?:тм|тотал меньше|меньше)\s*([+-]?\d+(?:\.\d+)?)', o)
    if m_under:
        limit = float(m_under.group(1)); total = s1 + s2
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
    print(f"   ⚠️ Неизвестный тип ставки: {outcome_str}")
    return False

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
                        all_finished = True; results = []; is_void = False
                        for event in events:
                            if not isinstance(event, dict): all_finished = False; break
                            m_id = event.get("match_id")
                            if m_id not in finished: all_finished = False; continue
                            res = finished[m_id]
                            if not isinstance(res, dict): all_finished = False; break
                            if res.get("void"): is_void = True
                            results.append(res)
                        if not all_finished or not results: continue
                        user_id = bet.get("user_id")
                        if is_void:
                            status = "void"
                            user = await firebase_get(f"users/{user_id}")
                            if isinstance(user, dict):
                                refund = bet.get("amount", 0)
                                new_bal = user.get("balance", 0) + refund
                                new_total = max(0, user.get("total_bets", 1) - 1)
                                await firebase_patch(f"users/{user_id}", {"balance": new_bal, "total_bets": new_total})
                        else:
                            is_won = True
                            for i, event in enumerate(events):
                                res = results[i]
                                s1 = res.get("score1", 0); s2 = res.get("score2", 0)
                                oc = event.get("outcome_key") or event.get("outcome", "")
                                ws = calculate_outcome(oc, s1, s2)
                                if ws is False: is_won = False; break
                                elif ws is None: is_won = False; break
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
                        await firebase_patch(f"bets/{bet_id}", {"status": status})
                        if user_id: await firebase_patch(f"users/{user_id}/bets/{bet_id}", {"status": status})
                        await firebase_delete(f"active_bets/{bet_id}")
                    except Exception as e:
                        print(f"⚠️ Ставка {bet_id}: {e}")
        except Exception as e:
            print(f"⚠️ settle_bets: {e}")
        cycle += 1
        if cycle % 15 == 0: gc.collect()
        await asyncio.sleep(SETTLE_POLL_SEC)

@app.on_event("startup")
async def startup_event():
    print("🚀 [Main] Запуск WinStars API...")
    print("   ВЕРСИЯ: p-normalize + auto-orientation + cached-live (не пропадают на перерыве)")
    asyncio.create_task(dp.start_polling(bot))
    asyncio.create_task(fonbet_scraper_worker())
    asyncio.create_task(settle_bets_worker())
    print("✅ Все фоновые воркеры запущены")

@app.get("/api/debug/fonbet/{ev_id}")
async def debug_fonbet(ev_id: str):
    result = {"ev_id": ev_id, "endpoints": {}}
    try:
        r = cffi_requests.get(
            f"{FONBET_BASE}/events/listBase?scopeMarket=1600&lang=ru",
            headers=FONBET_HEADERS, impersonate="chrome120", timeout=30
        )
        if r.status_code != 200:
            result["endpoints"]["listBase"] = {"status": r.status_code}
            return result
        data = r.json()
        events = data.get("events", [])
        custom = data.get("customFactors", [])
        target_ev = None
        for ev in events:
            if str(ev.get("id")) == ev_id:
                target_ev = ev; break
        factors_for_ev = []
        for cf in custom:
            if str(cf.get("e")) == ev_id:
                factors_for_ev.extend(cf.get("factors", []))
        markets = _parse_markets(factors_for_ev)
        result["endpoints"]["listBase"] = {
            "status": r.status_code,
            "event_found": target_ev is not None,
            "factors_count": len(factors_for_ev),
            "markets_summary": {g: len(outs) for g, outs in markets.items()},
        }
    except Exception as e:
        result["endpoints"]["listBase"] = {"error": str(e)[:300]}
    try:
        fb_match = await firebase_get(f"matches/fonbet_{ev_id}")
        if isinstance(fb_match, dict):
            fb_markets = fb_match.get("markets", {})
            result["firebase"] = {
                "exists": True,
                "status": fb_match.get("status"),
                "markets_summary": {g: len(outs) for g, outs in fb_markets.items()} if isinstance(fb_markets, dict) else {},
            }
        else:
            result["firebase"] = {"exists": False}
    except Exception as e:
        result["firebase"] = {"error": str(e)[:200]}
    return result

@app.get("/api/debug/fonbet/{ev_id}/raw")
async def debug_fonbet_raw(ev_id: str):
    """Возвращает сырые факторы события (для отладки ТБ/ТМ ориентации)."""
    results = {}
    for label, url in [("listBase", FONBET_BASE + FONBET_LINE_PATH), ("live", FONBET_BASE + FONBET_LIVE_PATH)]:
        try:
            r = cffi_requests.get(url, headers=FONBET_HEADERS, impersonate="chrome120", timeout=30)
            if r.status_code != 200:
                results[label] = {"status": r.status_code}
                continue
            data = r.json()
            found = []
            for cf in data.get("customFactors", []):
                if str(cf.get("e")) == ev_id:
                    for f in cf.get("factors", []):
                        found.append({"f": f.get("f"), "p": f.get("p"), "v": f.get("v")})
            for f in data.get("factors", []):
                if str(f.get("e")) == ev_id:
                    found.append({"f": f.get("f"), "p": f.get("p"), "v": f.get("v"), "src": "factors"})
            results[label] = {"count": len(found), "factors": found}
        except Exception as e:
            results[label] = {"error": str(e)[:200]}
    return results

@app.get("/api/proxy-image")
async def proxy_image(url: str):
    try:
        resp = await http_client.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5.0)
        if resp.status_code == 200: return Response(content=resp.content, media_type="image/png")
    except: pass
    return Response(status_code=404)

@app.get("/api/user/sync")
async def sync_user(initData: str = Query("")):
    user_data = {"id": 12345678, "first_name": "Игрок", "username": "user"}
    if initData:
        try:
            parsed_qs = urllib.parse.parse_qs(initData, keep_blank_values=True)
            data_dict = {k: v[0] for k, v in parsed_qs.items() if k != "hash"}
            user_data = json.loads(data_dict.get("user", "{}"))
        except: pass
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

@app.api_route("/api/matches", methods=["GET", "HEAD"])
async def get_matches():
    matches = await firebase_get("matches")
    return {"matches": matches if isinstance(matches, dict) else {}}

@app.get("/api/match/{match_id}/markets")
async def get_match_markets(match_id: str):
    match_db = await firebase_get(f"matches/{match_id}")
    markets_data = {}
    if isinstance(match_db, dict):
        if isinstance(match_db.get("markets"), dict) and match_db["markets"]:
            markets_data = match_db["markets"]
        if not markets_data and match_db.get("odds"):
            odds = match_db["odds"]
            p1, px, p2 = odds.get("1"), odds.get("X"), odds.get("2")
            if p1 and px and p2:
                markets_data = {"Исход": [
                    {"name": "П1", "odds": p1}, {"name": "Х", "odds": px}, {"name": "П2", "odds": p2},
                ]}
    return {"markets": markets_data}

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
    
    if amount < 10: raise HTTPException(status_code=400, detail="Минимум 10 звёзд")
    if not bets: raise HTTPException(status_code=400, detail="Нет исходов")
    
    user = await firebase_get(f"users/{user_id}")
    if not isinstance(user, dict): raise HTTPException(status_code=404, detail="Профиль не найден")
    user_balance = user.get("balance", 0)
    if not isinstance(user_balance, (int, float)): user_balance = 0
    if user_balance < amount: raise HTTPException(status_code=400, detail="Недостаточно звёзд")
    
    new_fingerprint = hashlib.md5(
        f"{user_id}|{amount}|{json.dumps(bets, sort_keys=True)}".encode()
    ).hexdigest()
    active_all = await firebase_get("active_bets") or {}
    if isinstance(active_all, dict):
        current_ts = time.time()
        for _bid, _b in active_all.items():
            if not isinstance(_b, dict): continue
            _b_ts = _b.get("timestamp", 0)
            if current_ts - _b_ts > 10: continue
            old_fp = hashlib.md5(
                f"{_b.get('user_id')}|{_b.get('amount')}|{json.dumps(_b.get('events', []), sort_keys=True)}".encode()
            ).hexdigest()
            if old_fp == new_fingerprint:
                raise HTTPException(status_code=429, detail="Дубликат ставки. Подождите 10 секунд.")
    
    total_coeff = 1.0
    for b in bets: total_coeff *= float(b.get("coefficient", 1.0))
    total_coeff = round(total_coeff, 2)
    new_balance = round(user_balance - amount, 2)
    
    bet_data = {
        "user_id": str(user_id), "type": bet_type, "events": bets,
        "amount": amount, "total_coefficient": total_coeff,
        "potential_win": round(amount * total_coeff, 2),
        "status": "active", "timestamp": int(time.time())
    }
    
    bet_ref = await http_client.post(f"{FIREBASE_DB_URL}/bets.json", json=bet_data)
    bet_id = bet_ref.json().get("name") if bet_ref.status_code == 200 else None
    if bet_id:
        await firebase_put(f"users/{user_id}/bets/{bet_id}", bet_data)
        await firebase_put(f"active_bets/{bet_id}", {
            "user_id": str(user_id), "events": bets,
            "amount": amount, "total_coefficient": total_coeff,
            "timestamp": time.time(),
        })
    
    await firebase_patch(f"users/{user_id}", {"balance": new_balance, "total_bets": user.get("total_bets", 0) + 1})
    return {"status": "ok", "new_balance": new_balance, "message": "Прогноз принят!"}

@app.post("/api/cashier/invoice")
async def create_invoice(request: Request):
    req = await request.json()
    user_id = req.get("user_id"); amount = req.get("amount")
    payload = f"topup_{user_id}_{amount}"
    try:
        link = await bot.create_invoice_link(title="WinStars", description=f"Пополнение {amount} ★", payload=payload, currency="XTR", prices=[LabeledPrice(label="Звёзды", amount=amount)])
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
    new_balance = round(cur + promo.get("reward_stars", 50), 2)
    await firebase_patch(f"users/{user_id}", {"balance": new_balance})
    return {"status": "ok", "message": f"Начислено {promo.get('reward_stars', 50)} ★", "new_balance": new_balance}

@app.get("/api/chat/history")
async def get_chat_history():
    msgs = await firebase_get("chat_messages")
    return {"messages": list(msgs.values())[-50:] if isinstance(msgs, dict) else []}

@app.websocket("/ws/chat")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            payload = json.loads(data)
            msg_data = {"user_id": payload["user_id"], "first_name": payload["first_name"], "text": payload["text"]}
            await http_client.post(f"{FIREBASE_DB_URL}/chat_messages.json", json=msg_data)
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