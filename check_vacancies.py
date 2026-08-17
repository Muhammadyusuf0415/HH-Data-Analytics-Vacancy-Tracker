"""
hh.uz (Toshkent) saytidan "data analytics"ga oid yangi vakansiyalarni
topib, Telegram botga yuboradi.

Ishlash printsipi:
- Har safar ishga tushganda hh.ru API orqali Toshkent (area=2759) bo'yicha
  data analytics kalit so'zlariga mos vakansiyalarni so'raydi.
- seen_ids.json faylida avval yuborilgan vakansiyalar ID'sini saqlaydi,
  shu bois har vakansiya faqat BIR MARTA yuboriladi.
- GitHub Actions orqali muntazam (masalan har 30 daqiqada) ishga tushiriladi.
"""

import json
import os
import time
import requests

# ---------- SOZLAMALAR ----------
HH_AREA_ID = 2759  # Toshkent
# Qidiruv so'zlari (xohlasangiz qo'shishingiz/o'zgartirishingiz mumkin)
SEARCH_QUERY = "data analyst OR data analytics OR аналитик данных OR дата-аналитик OR data analyst"
SEEN_IDS_FILE = "seen_ids.json"
MAX_STORED_IDS = 2000  # fayl cheksiz o'sib ketmasligi uchun

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

HH_API_URL = "https://api.hh.ru/vacancies"
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"


def load_seen_ids():
    if os.path.exists(SEEN_IDS_FILE):
        with open(SEEN_IDS_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen_ids(seen_ids):
    # eng oxirgi MAX_STORED_IDS tasini saqlaymiz
    ids_list = list(seen_ids)[-MAX_STORED_IDS:]
    with open(SEEN_IDS_FILE, "w", encoding="utf-8") as f:
        json.dump(ids_list, f)


def fetch_vacancies():
    params = {
        "text": SEARCH_QUERY,
        "search_field": "name",  # faqat vakansiya nomida qidiradi (aniqroq natija)
        "area": HH_AREA_ID,
        "order_by": "publication_time",
        "per_page": 50,
    }
    resp = requests.get(HH_API_URL, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("items", [])


def format_message(vacancy):
    name = vacancy.get("name", "Noma'lum lavozim")
    employer = vacancy.get("employer", {}).get("name", "Noma'lum kompaniya")
    url = vacancy.get("alternate_url", "")

    salary = vacancy.get("salary")
    if salary:
        s_from = salary.get("from")
        s_to = salary.get("to")
        currency = salary.get("currency", "")
        if s_from and s_to:
            salary_text = f"{s_from:,} - {s_to:,} {currency}"
        elif s_from:
            salary_text = f"{s_from:,}+ {currency}"
        elif s_to:
            salary_text = f"{s_to:,} gacha {currency}"
        else:
            salary_text = "Ko'rsatilmagan"
    else:
        salary_text = "Ko'rsatilmagan"

    return (
        f"📊 <b>{name}</b>\n"
        f"🏢 {employer}\n"
        f"💰 {salary_text}\n"
        f"🔗 {url}"
    )


def send_to_telegram(text):
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    resp = requests.post(TELEGRAM_API_URL, data=payload, timeout=30)
    if not resp.ok:
        print(f"Telegramga yuborishda xatolik: {resp.status_code} {resp.text}")
    resp.raise_for_status()


def main():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN va TELEGRAM_CHAT_ID environment variable "
            "sifatida berilishi kerak."
        )

    seen_ids = load_seen_ids()
    vacancies = fetch_vacancies()

    new_count = 0
    for vacancy in vacancies:
        vac_id = vacancy["id"]
        if vac_id in seen_ids:
            continue

        message = format_message(vacancy)
        send_to_telegram(message)
        seen_ids.add(vac_id)
        new_count += 1
        time.sleep(1)  # Telegram rate limit uchun kichik pauza

    save_seen_ids(seen_ids)
    print(f"Tekshiruv tugadi. Yangi yuborilgan vakansiyalar soni: {new_count}")


if __name__ == "__main__":
    main()
