"""
hh.uz (Toshkent) saytidan "data analytics"ga oid yangi vakansiyalarni
topib, Telegram botga yuboradi.

MUHIM: hh.ru/hh.uz 2026-yil aprelidan boshlab api.hh.ru JSON API'sini
autentifikatsiyasiz so'rovlar uchun yopib qo'ygan (403/400 xato beradi).
Shu sababli bu skript hali ham ochiq bo'lgan qidiruv RSS-lentasidan
foydalanadi: https://tashkent.hh.uz/search/vacancy/rss

Ishlash printsipi:
- Har safar ishga tushganda RSS-lentadan Toshkentdagi data analytics
  vakansiyalarini oladi.
- seen_ids.json faylida avval yuborilgan vakansiyalar linkini saqlaydi,
  shu bois har vakansiya faqat BIR MARTA yuboriladi.
- GitHub Actions orqali muntazam (masalan har 30 daqiqada) ishga tushiriladi.
"""

import html
import json
import os
import re
import time
import xml.etree.ElementTree as ET

import requests

# ---------- SOZLAMALAR ----------
HH_AREA_ID = 2759  # Toshkent
SEARCH_QUERY = "data analyst OR data analytics OR аналитик данных OR дата-аналитик"
RSS_URL = "https://tashkent.hh.uz/search/vacancy/rss"
SEEN_IDS_FILE = "seen_ids.json"
MAX_STORED_IDS = 2000  # fayl cheksiz o'sib ketmasligi uchun

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


def load_seen_ids():
    if os.path.exists(SEEN_IDS_FILE):
        with open(SEEN_IDS_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen_ids(seen_ids):
    ids_list = list(seen_ids)[-MAX_STORED_IDS:]
    with open(SEEN_IDS_FILE, "w", encoding="utf-8") as f:
        json.dump(ids_list, f)


def strip_html(text):
    """<p>...</p> kabi HTML teglarini olib tashlaydi va bo'sh joylarni tozalaydi."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_vacancies():
    params = {
        "text": SEARCH_QUERY,
        "area": HH_AREA_ID,
        "order_by": "publication_time",
    }
    resp = requests.get(RSS_URL, params=params, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    items = []
    for item in root.findall("./channel/item"):
        link = item.findtext("link", default="").strip()
        title = item.findtext("title", default="Noma'lum lavozim").strip()
        description = strip_html(item.findtext("description", default=""))
        items.append({
            "id": link,  # link vakansiya uchun noyob identifikator sifatida ishlatiladi
            "title": title,
            "description": description,
            "link": link,
        })
    return items


def format_message(vacancy):
    return (
        f"📊 <b>{vacancy['title']}</b>\n"
        f"{vacancy['description']}\n"
        f"🔗 {vacancy['link']}"
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
        if not vac_id or vac_id in seen_ids:
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
