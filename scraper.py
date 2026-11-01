эimport json
from curl_cffi import requests as cffi_requests

BASE_URL = "https://line-lb51.bk6bba-resources.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.fon.bet/",
}

def check(url):
    print(f"\n🔍 Проверяем: {url}")
    try:
        r = cffi_requests.get(url, headers=HEADERS, impersonate="chrome120", timeout=10)
        print(f"Статус: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            print(f"Ключи в ответе: {list(data.keys())}")
            
            # Если есть список событий, смотрим, какие поля у первого события
            if "events" in data and len(data["events"]) > 0:
                first_ev = data["events"][0]
                print(f"Пример полей события: {list(first_ev.keys())}")
                print(f"Пример события: {json.dumps({k: first_ev[k] for k in list(first_ev.keys())[:10]}, ensure_ascii=False)}")
                
    except Exception as e:
        print(f"Ошибка: {e}")

# Проверяем разные варианты эндпоинтов, где могут быть лиги
check(f"{BASE_URL}/events/list?scopeMarket=1600&lang=ru")
check(f"{BASE_URL}/events/listBase?scopeMarket=1600&lang=ru")
check(f"{BASE_URL}/sports")
check(f"{BASE_URL}/events/competitions")
