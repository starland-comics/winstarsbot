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

async def firebase_put(path: str, data: any):
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.put(f"{FIREBASE_DB_URL}/{path}.json", json=data)
            return resp.status_code == 200
        except: return False

def get_fonbet_data(url):
    try:
        r = cffi_requests.get(url, headers=HEADERS, impersonate="chrome120", timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.error(f"Ошибка парсинга: {e}")
    return None

async def main():
    logger.info("🔄 Запуск парсера Fonbet...")
    list_url = f"{BASE_URL}/events/listBase?scopeMarket=1600&lang=ru"
    data = await asyncio.to_thread(get_fonbet_data, list_url)
    
    if not data or "events" not in data:
        logger.error("Не удалось получить список матчей.")
        return

    events = data.get("events", [])
    
    # Ищем первый футбольный матч для отладки
    football_events = [e for e in events if e.get("sport") == "Футбол" or e.get("sportId") == 1]
    logger.info(f"Всего событий: {len(events)}. Из них футбольных: {len(football_events)}")

    if football_events:
        sample = football_events[0]
        logger.info(f"Пример футбольного матча (ID {sample.get('id')}):")
        # Выводим ключи, которые есть в матче
        logger.info(f"Доступные ключи: {list(sample.keys())}")
        
        # Проверяем наличие customFactors
        if "customFactors" in sample:
            logger.info(f"customFactors найден! Пример: {json.dumps(sample['customFactors'][:1], ensure_ascii=False)}")
        elif "factors" in sample:
            logger.info(f"factors найден! Пример: {json.dumps(sample['factors'][:1], ensure_ascii=False)}")
        else:
            logger.info("⚠️ В матче НЕТ поля customFactors или factors. Кэфы нужно запрашивать отдельно.")

    matches = {}
    # Проходимся по всем футбольным матчам
    for ev in football_events:
        team1 = ev.get("team1", "Хозяева")
        team2 = ev.get("team2", "Гости")
        
        odds = {"1": None, "X": None, "2": None}
        
        # Пытаемся найти кэфы в customFactors (стандарт Fonbet)
        factors_list = []
        if "customFactors" in ev:
            for cf in ev["customFactors"]:
                factors_list.extend(cf.get("factors", []))
        elif "factors" in ev:
            factors_list = ev["factors"]

        for factor in factors_list:
            f_id = factor.get("f")
            v = factor.get("v")
            if f_id == 921: odds["1"] = v
            elif f_id == 922: odds["X"] = v
            elif f_id == 923: odds["2"] = v

        # Если нет кэфов в списке, может, они в другом формате?
        # Пока сохраняем только те, где есть все 3 кэфа
        if odds["1"] and odds["X"] and odds["2"]:
            matches[f"fon_{ev['id']}"] = {
                "title": f"{team1} vs {team2}",
                "league": ev.get("sport", "Футбол"),
                "status": "LIVE" if ev.get("live") else "Скоро",
                "score": "VS",
                "odds": odds
            }

    logger.info(f"✅ Собрано {len(matches)} матчей с коэффициентами 1X2.")
    
    if matches:
        if await firebase_put("matches", matches):
            logger.info("🔥 Линия Fonbet обновлена в Firebase!")
    else:
        logger.warning("Матчей с кэфами 1X2 не найдено. Смотри отладочную информацию выше.")

if __name__ == "__main__":
    asyncio.run(main())
