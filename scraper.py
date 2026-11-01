import asyncio
import logging
import httpx
from curl_cffi import requests as cffi_requests
from datetime import datetime

FIREBASE_DB_URL = "https://winstars-435f1-default-rtdb.firebaseio.com"
BASE_URL = "https://line-lb51.bk6bba-resources.com" # Зеркало Fonbet

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
    """Синхронный запрос через curl_cffi для обхода защиты"""
    try:
        r = cffi_requests.get(url, headers=HEADERS, impersonate="chrome120", timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.error(f"Ошибка парсинга: {e}")
    return None

async def main():
    logger.info("🔄 Запуск парсера Fonbet (РПЛ и другие лиги)...")
    
    # 1. Получаем список ВСЕХ событий (live + линия)
    list_url = f"{BASE_URL}/events/listBase?scopeMarket=1600&lang=ru"
    data = await asyncio.to_thread(get_fonbet_data, list_url)
    
    if not data or "events" not in data:
        logger.error("Не удалось получить список матчей Fonbet.")
        return

    events = data.get("events", [])
    logger.info(f"Найдено событий в базе: {len(events)}")

    matches = {}
    
    # 2. Проходимся по событиям (берем первые 100 для теста, чтобы не спамить)
    for ev in events[:150]:
        # Нас интересуют только футбольные матчи
        # В Fonbet ID видов спорта: 1 - футбол (обычно)
        sport_id = ev.get("sportId")
        if sport_id != 1: continue

        # Извлекаем названия команд (в Fonbet они лежат в team1 и team2)
        team1 = ev.get("team1", "Хозяева")
        team2 = ev.get("team2", "Гости")
        
        # Извлекаем коэффициенты из customFactors
        odds = {"1": None, "X": None, "2": None}
        for cf in ev.get("customFactors", []):
            for factor in cf.get("factors", []):
                f_id = factor.get("f")
                v = factor.get("v")
                # 921 (П1), 922 (X), 923 (П2) - стандартные ID Fonbet для 1X2
                if f_id == 921: odds["1"] = v
                elif f_id == 922: odds["X"] = v
                elif f_id == 923: odds["2"] = v

        # Сохраняем только если есть все 3 кэфа
        if odds["1"] and odds["X"] and odds["2"]:
            matches[f"fon_{ev['id']}"] = {
                "title": f"{team1} vs {team2}",
                "league": ev.get("sport", "Футбол"), # Название турнира
                "status": "LIVE" if ev.get("live") else "Скоро",
                "score": "VS", # Счет в listBase может не приходить, нужно уточнять
                "odds": odds
            }

    logger.info(f"✅ Собрано {len(matches)} матчей с коэффициентами.")
    
    # 3. Пушим в Firebase
    if matches:
        if await firebase_put("matches", matches):
            logger.info("🔥 Линия Fonbet успешно обновлена в Firebase!")
    else:
        logger.warning("Ничего не найдено.")

if __name__ == "__main__":
    asyncio.run(main())
