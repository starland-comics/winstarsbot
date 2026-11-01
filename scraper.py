import asyncio
import logging
import json
from curl_cffi import requests as cffi_requests

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
        else:
            logger.error(f"HTTP {r.status_code}")
    except Exception as e:
        logger.error(f"Ошибка: {e}")
    return None

async def main():
    event_id = "69126853" # ID матча Крылья Советов - Зенит
    logger.info(f"🔄 Запрашиваем детали матча {event_id}...")
    
    detail_url = f"{BASE_URL}/events/event?eventId={event_id}&scopeMarket=1600&lang=ru"
    data = await asyncio.to_thread(get_fonbet_data, detail_url)
    
    if data:
        logger.info("🔍 ОТВЕТ СЕРВЕРА (Детали матча):")
        logger.info(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        logger.error("Не удалось получить детали.")

if __name__ == "__main__":
    asyncio.run(main())
