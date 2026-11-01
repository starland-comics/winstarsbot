import asyncio
import time
import httpx
import json
from curl_cffi import requests as cffi_requests

BASE_URL = "https://line-lb51.bk6bba-resources.com"
FIREBASE_DB_URL = "https://winstars-435f1-default-rtdb.firebaseio.com"
FONBET_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.fon.bet/",
}

LEAGUES_CACHE = {}
LEAGUES_LAST_UPDATE = 0

def get_fonbet_data_sync(url):
    try:
        r = cffi_requests.get(url, headers=FONBET_HEADERS, impersonate="chrome120", timeout=10)
        if r.status_code == 200:
            return r.json()
    except:
        pass
    return None

async def update_leagues_cache():
    global LEAGUES_CACHE, LEAGUES_LAST_UPDATE
    url = f"{BASE_URL}/sports?lang=ru"
    data = await asyncio.to_thread(get_fonbet_data_sync, url)
    
    if data and "sports" in data:
        new_cache = {}
        for sport in data["sports"]:
            if sport.get("name") != "Футбол": continue # Только футбол
            for region in sport.get("regions", []):
                for comp in region.get("competitions", []):
                    cid = comp.get("id")
                    if cid:
                        new_cache[int(cid)] = comp.get("name", "Турнир")
        if new_cache:
            LEAGUES_CACHE = new_cache
            LEAGUES_LAST_UPDATE = time.time()
            print(f"✅ [Scraper] Лиги обновлены: {len(LEAGUES_CACHE)}")

async def fetch_odds_and_score(event_id, sem):
    async with sem:
        url = f"{BASE_URL}/events/event?eventId={event_id}&scopeMarket=1600&lang=ru"
        data = await asyncio.to_thread(get_fonbet_data_sync, url)
        if not data: return None
        
        odds = {"1": None, "X": None, "2": None}
        for cf in data.get("customFactors", []):
            for f in cf.get("factors", []):
                fid, v = f.get("f"), f.get("v")
                if fid == 921: odds["1"] = v
                elif fid == 922: odds["X"] = v
                elif fid == 923: odds["2"] = v
            if odds["1"] and odds["X"] and odds["2"]:
                break
        
        if not odds["1"]: return None
        
        event_info = data.get("event", {})
        score1 = event_info.get("score1")
        score2 = event_info.get("score2")
        minute = event_info.get("time", "")
        place = event_info.get("place", "line")
        
        score_str = f"{score1} : {score2}" if score1 is not None and place == "live" else "VS"
        status_str = f"LIVE {minute}'" if place == "live" else "Скоро"
        
        return odds, score_str, status_str

async def firebase_put(path: str, data: any):
    async with httpx.AsyncClient() as client:
        try:
            await client.put(f"{FIREBASE_DB_URL}/{path}.json", json=data)
        except Exception as e:
            print(f"[Scraper] Firebase Error: {e}")

async def fonbet_worker():
    print("🔄 [Scraper] Запуск фонового парсера...")
    global LEAGUES_LAST_UPDATE
    
    while True:
        try:
            if not LEAGUES_CACHE or (time.time() - LEAGUES_LAST_UPDATE > 3600):
                await update_leagues_cache()

            list_url = f"{BASE_URL}/events/listBase?scopeMarket=1600&lang=ru"
            data = await asyncio.to_thread(get_fonbet_data_sync, list_url)
            
            if data and "events" in data:
                events = data.get("events", [])
                now = time.time()
                valid_events = []
                for ev in events:
                    if int(ev.get("sportId", 0)) in LEAGUES_CACHE and ev.get("team1") and ev.get("team2"):
                        start = ev.get("startTime", 0)
                        if start > now - 10800:
                            valid_events.append(ev)
                
                sem = asyncio.Semaphore(20)
                tasks = [fetch_odds_and_score(ev["id"], sem) for ev in valid_events]
                results = await asyncio.gather(*tasks)
                
                matches = {}
                for ev, res in zip(valid_events, results):
                    if res:
                        odds, score, status = res
                        league_id = int(ev.get("sportId", 0))
                        league_name = LEAGUES_CACHE.get(league_id, "Футбол")
                        
                        matches[f"fon_{ev['id']}"] = {
                            "title": f"{ev['team1']} - {ev['team2']}",
                            "home": ev['team1'],
                            "away": ev['team2'],
                            "homeLogo": f"https://static.fon.bet/team_logos/{ev.get('team1Id')}.png",
                            "awayLogo": f"https://static.fon.bet/team_logos/{ev.get('team2Id')}.png",
                            "league": league_name,
                            "status": status,
                            "score": score,
                            "start_time": ev.get("startTime"),
                            "odds": odds
                        }
                
                if matches:
                    await firebase_put("matches", matches)
                    print(f"✅ [Scraper] Обновлено {len(matches)} матчей")
                    
        except Exception as e:
            print(f"[Scraper] Ошибка: {e}")
            
        await asyncio.sleep(30)
