import asyncio
import logging
import time
import httpx
from curl_cffi import requests as cffi_requests

FIREBASE_DB_URL = "https://winstars-435f1-default-rtdb.firebaseio.com"
BASE_URL = "https://line-lb51.bk6bba-resources.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.fon.bet/",
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("fonbet")

async def firebase_put(path: str, data: any):
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.put(f"{FIREBASE_DB_URL}/{path}.json", json=data)
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"Firebase error: {e}")
            return False

def get_fonbet_data(url):
    try:
        r = cffi_requests.get(url, headers=HEADERS, impersonate="chrome120", timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None

async def fetch_event_odds(event_id, sem):
    async with sem:
        detail_url = f"{BASE_URL}/events/event?eventId={event_id}&scopeMarket=1600&lang=ru"
        data = await asyncio.to_thread(get_fonbet_data, detail_url)
        if not data: return None
        
        # Ищем первый валидный набор 1X2
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
            
            # Если есть все три кэфа, возвращаем их (берем первую линию)
            if odds["1"] and odds["X"] and odds["2"]:
                return odds
        return None

async def main():
    logger.info("🔄 Запуск финального парсера Fonbet...")
    
    # 1. Получаем список всех событий
    list_url = f"{BASE_URL}/events/listBase?scopeMarket=1600&lang=ru"
    data = await asyncio.to_thread(get_fonbet_data, list_url)
    
    if not data or "events" not in data:
        logger.error("Не удалось получить список матчей.")
        return

    events = data.get("events", [])
    logger.info(f"Всего событий в базе: {len(events)}")

    # 2. Фильтруем только те, где есть команды и матч не завершен давно
    now = int(time.time())
    valid_events = []
    for ev in events:
        if ev.get("team1") and ev.get("team2"):
            start = ev.get("startTime", 0)
            # Берем матчи, которые начнутся в будущем или начались не более 3 часов назад
            if start > now - 10800: 
                valid_events.append(ev)
                
    logger.info(f"Матчей для парсинга (актуальные): {len(valid_events)}")

    # 3. Скачиваем кэфы для каждого матча параллельно
    sem = asyncio.Semaphore(10) # 10 запросов одновременно
    tasks = [fetch_event_odds(ev["id"], sem) for ev in valid_events]

    logger.info("Скачиваем коэффициенты... Это может занять 1-2 минуты.")
    results = await asyncio.gather(*tasks)

    # 4. Формируем итоговый JSON
    matches = {}
    for ev, odds in zip(valid_events, results):
        if odds:
            matches[f"fon_{ev['id']}"] = {
                "title": f"{ev['team1']} vs {ev['team2']}",
                "league": "Спорт", # Если нужно, можем потом вытащить точное название
                "status": "LIVE" if ev.get("place") == "live" else "Скоро",
                "start_time": ev.get("startTime"),
                "odds": odds
            }

    logger.info(f"✅ Собрано {len(matches)} матчей с коэффициентами 1X2.")
    
    # 5. Сохраняем в Firebase
    if matches:
        if await firebase_put("matches", matches):
            logger.info("🔥 Линия WinStars успешно обновлена в Firebase!")
    else:
        logger.warning("Матчей с кэфами не найдено.")

if __name__ == "__main__":
    asyncio.run(main())
