import asyncio
import logging
import json
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

def get_fonbet_data(url):
    try:
        r = cffi_requests.get(url, headers=HEADERS, impersonate="chrome120", timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.error(f"Ошибка парсинга: {e}")
    return None

async def main():
    logger.info("🔄 Запуск парсера Fonbet (Отладка)...")
    list_url = f"{BASE_URL}/events/listBase?scopeMarket=1600&lang=ru"
    data = await asyncio.to_thread(get_fonbet_data, list_url)
    
    if not data or "events" not in data:
        logger.error("Не удалось получить список матчей.")
        return

    events = data.get("events", [])
    logger.info(f"Всего событий: {len(events)}")

    if events:
        sample = events[0]
        logger.info("🔍 ПРИМЕР ПЕРВОГО СОБЫТИЯ (сырой JSON):")
        # Выводим весь JSON первого матча, чтобы увидеть все поля
        logger.info(json.dumps(sample, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
