import asyncio
import logging
import time
import httpx
from curl_cffi import requests as cffi_requests

# ──────────────────────────────────────────────
# НАСТРОЙКИ
# ──────────────────────────────────────────────
FIREBASE_DB_URL = "https://winstars-435f1-default-rtdb.firebaseio.com"
BASE_URL = "https://line-lb51.bk6bba-resources.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.fon.bet/",
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("fonbet")

# ──────────────────────────────────────────────
# FIREBASE HELPER
# ──────────────────────────────────────────────
async def firebase_put(path: str, data: any):
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.put(f"{FIREBASE_DB_URL}/{path}.json", json=data)
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"Firebase error: {e}")
            return False

# ──────────────────────────────────────────────
# FONBET API HELPERS
# ──────────────────────────────────────────────
def get_fonbet_data(url):
    try:
        r = cffi_requests.get(url, headers=HEADERS, impersonate="chrome120", timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        pass
    return None

async def fetch_event_odds(event_id, sem):
    async with sem:
        detail_url = f"{BASE_URL}/events/event?eventId={event_id}&scopeMarket=1600&lang=ru"
        data = await asyncio.to_thread(get_fonbet_data, detail_url)
        if not data: return None
        
        custom_factors = data.get("customFactors", [])
        for cf in custom_factors:
            factors = cf.get("factors", [])
            odds = {"1": None, "X": None, "2": None}
            for f in factors:
                f_id = f.get("f")
                v = f.get("v")
                if f_id == 921: odds["1"] = v
                elif f_id == 922: odds["X"] = v
                elif f_id == 923: odds["2"] = v
            
            # Возвращаем первый полный набор (это будет основная линия)
            if odds["1"] and odds["X"] and odds["2"]:
                return odds
        return None

# ──────────────────────────────────────────────
# ГЛАВНЫЙ ЦИКЛ
# ──────────────────────────────────────────────
async def main():
    logger.info("🔄 Запуск парсера Fonbet...")
    
    # 1. Загружаем справочник лиг (чтобы вместо ID писать названия)
    logger.info("Загружаем список лиг...")
    sports_url = f"{BASE_URL}/sports?lang=ru"
    sports_data = await asyncio.to_thread(get_fonbet_data, sports_url)
    
    leagues_map = {}
    if sports_data and "sports" in sports_data:
        for sport in sports_data["sports"]:
            for region in sport.get("regions", []):
                for comp in region.get("competitions", []):
                    leagues_map[comp.get("id")] = comp.get("name", "Турнир")
    
    logger.info(f"Загружено лиг: {len(leagues_map)}")

    # 2. Получаем список всех событий
    list_url = f"{BASE_URL}/events/listBase?scopeMarket=1600&lang=ru"
    data = await asyncio.to_thread(get_fonbet_data, list_url)
    
    if not data or "events" not in data:
        logger.error("Не удалось получить список матчей.")
        return

    events = data.get("events", [])
    logger.info(f"Всего событий в базе: {len(events)}")

    # 3. Фильтруем матчи (оставляем только актуальные)
    now = int(time.time())
    valid_events = []
    for ev in events:
        if ev.get("team1") and ev.get("team2"):
            start = ev.get("startTime", 0)
            # Берем те, что начнутся в будущем или начались не более 3 часов назад
            if start > now - 10800: 
                valid_events.append(ev)
                
    logger.info(f"Матчей для парсинга: {len(valid_events)}")

    # 4. Скачиваем кэфы (по 10 штук одновременно, чтобы не забанили)
    sem = asyncio.Semaphore(10)
    tasks = [fetch_event_odds(ev["id"], sem) for ev in valid_events]
    
    logger.info("Скачиваем коэффициенты... Пожалуйста, подождите.")
    results = await asyncio.gather(*tasks)

    # 5. Формируем итоговый JSON
    matches = {}
    for ev, odds in zip(valid_events, results):
        if odds:
            # Генерируем логотипы
            home_logo = f"https://static.fon.bet/team_logos/{ev.get('team1Id')}.png"
            away_logo = f"https://static.fon.bet/team_logos/{ev.get('team2Id')}.png"
            
            # Ищем название лиги по ID
            league_name = leagues_map.get(ev.get("sportId"), "Футбол")

            matches[f"fon_{ev['id']}"] = {
                "title": f"{ev['team1']} - {ev['team2']}",
                "home": ev['team1'],
                "away": ev['team2'],
                "homeLogo": home_logo,
                "awayLogo": away_logo,
                "league": league_name,
                "status": "LIVE" if ev.get("place") == "live" else "Скоро",
                "score": "VS", # Fonbet не отдает счет в базовом списке
                "start_time": ev.get("startTime"),
                "odds": odds
            }

    logger.info(f"✅ Собрано {len(matches)} матчей с кэфами и логотипами.")
    
    # 6. Сохраняем в Firebase
    if matches:
        if await firebase_put("matches", matches):
            logger.info("🔥 Линия WinStars успешно обновлена в Firebase!")
        else:
            logger.error("❌ Ошибка записи в Firebase (проверь правила доступа).")
    else:
        logger.warning("Матчей с кэфами не найдено.")

if __name__ == "__main__":
    asyncio.run(main())
