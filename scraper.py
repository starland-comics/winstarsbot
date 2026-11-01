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
    except Exception as e:
        logger.error(f"Ошибка: {e}")
    return None

async def main():
    event_id = "69126853" 
    logger.info(f"🔄 Запрашиваем детали матча {event_id}...")
    
    detail_url = f"{BASE_URL}/events/event?eventId={event_id}&scopeMarket=1600&lang=ru"
    data = await asyncio.to_thread(get_fonbet_data, detail_url)
    
    if data:
        logger.info("✅ Данные получены! Ищем коэффициенты...")
        
        # Проверяем наличие кэфов
        found_odds = []
        
        # 1. Смотрим customFactors
        custom_factors = data.get("customFactors", [])
        for cf in custom_factors:
            factors = cf.get("factors", [])
            for f in factors:
                f_id = f.get("f")
                v = f.get("v")
                # 921, 922, 923 - это обычно 1, X, 2
                if f_id in [921, 922, 923]:
                    found_odds.append(f"ID {f_id}: {v}")
                    
        # 2. Смотрим обычные factors (иногда лежат тут)
        factors = data.get("factors", [])
        for f in factors:
            f_id = f.get("f")
            v = f.get("v")
            if f_id in [921, 922, 923]:
                found_odds.append(f"ID {f_id}: {v}")

        if found_odds:
            logger.info(f"🎯 НАЙДЕНЫ КОЭФФИЦИЕНТЫ: {', '.join(found_odds)}")
        else:
            logger.warning("⚠️ Кэфов с ID 921, 922, 923 нет. Вот какие ID вообще есть в матче:")
            # Выводим список всех ID кэфов, которые есть
            all_ids = set()
            for cf in custom_factors:
                for f in cf.get("factors", []):
                    all_ids.add(f.get("f"))
            for f in factors:
                all_ids.add(f.get("f"))
                
            logger.info(f"Доступные ID факторов: {sorted(list(all_ids))}")
            
            # Выводим первый блок customFactors для понимания структуры
            if custom_factors:
                logger.info(f"Пример структуры (первые 300 символов): {json.dumps(custom_factors[:1], ensure_ascii=False)[:300]}...")
    else:
        logger.error("Не удалось получить детали.")

if __name__ == "__main__":
    asyncio.run(main())
