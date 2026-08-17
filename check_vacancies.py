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
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

# ---------- SOZLAMALAR ----------
HH_AREA_ID = 2759  # Toshkent
# hh.uz RSS'i "OR" mantiqini URL ichida to'g'ri qo'llamaydi (filtrsiz natija
# qaytarib yuboradi), shuning uchun har bir so'z alohida so'rov sifatida
# yuboriladi va natijalar keyin birlashtiriladi.
# Bu ro'yxat ataylab keng ildiz so'zlardan ("анализ", "tahlil" kabi) qochadi,
# chunki ular deyarli har qanday vakansiyada uchraydi (masalan "bozorni
# tahlil qilish", "moliyaviy anализ") va noaniq natija beradi. Buning o'rniga
# aynan data analytics kasbiga (va unga yaqin rollarga) tegishli aniq
# iboralar ishlatiladi.
SEARCH_KEYWORDS = [
    # --- Data Analyst / Analytics ---
    "data analyst",
    "data analytics",
    "аналитик данных",
    "аналитик по данным",
    "дата-аналитик",
    "дата аналитик",
    "data analytic",
    # --- Data Scientist / Engineer ---
    "data scientist",
    "data engineer",
    "дата-инженер",
    "инженер данных",
    "data engineering",
    "big data",
    "machine learning engineer",
    "ML engineer",
    # --- BI (Business Intelligence) ---
    "BI аналитик",
    "BI-аналитик",
    "business intelligence",
    "BI developer",
    "Power BI",
    "Tableau",
    "analytics engineer",
    # --- Yaqin/qo'shni rollar ---
    "product analyst",
    "продуктовый аналитик",
    "бизнес-аналитик",
    "business analyst",
    "quantitative analyst",
    "marketing analyst",
    "маркетинговый аналитик",
    "financial analyst",
    "финансовый аналитик",
    "web analytics",
    "веб-аналитик",
    # --- O'zbekcha ---
    "ma'lumotlar tahlilchisi",
]

# Sarlavhada bu "ildiz" so'zlardan (butun so'z sifatida, boshqa harflar
# bilan qo'shilib ketmagan holda) biri uchrasa ham vakansiya qabul
# qilinadi: analitik/analyst/BI/data va ularning turli shakllari.
# \b (so'z chegarasi) tufayli "bilan", "database" kabi so'zlar ichidagi
# tasodifiy moslik hisobga olinmaydi.
# Bular prefiks sifatida qidiriladi (masalan "analitik" so'zi
# "analitikning", "analitikaga" kabi qo'shimchali shakllarni ham qamrab oladi)
ROOT_PREFIXES = [
    "analitik", "analitika",
    "analyst", "analytic", "analytics",
    "аналитик", "аналитика", "аналист",
    "data", "дата",
]
# "bi" juda qisqa bo'lgani uchun faqat ALOHIDA SO'Z sifatida (masalan
# "BI aналитик", "Senior BI") qidiriladi — "biznes", "bilan" kabi
# so'zlar ichidagi tasodifiy moslikni chiqarib tashlash uchun.
ROOT_EXACT_WORDS = ["bi"]

ROOT_PATTERNS = [re.compile(rf"\b{re.escape(w)}\w*", re.IGNORECASE) for w in ROOT_PREFIXES]
ROOT_PATTERNS += [re.compile(rf"\b{re.escape(w)}\b", re.IGNORECASE) for w in ROOT_EXACT_WORDS]

RSS_URL = "https://tashkent.hh.uz/search/vacancy/rss"
SEEN_IDS_FILE = "seen_ids.json"
MAX_STORED_IDS = 2000  # fayl cheksiz o'sib ketmasligi uchun
MAX_RESULTS_PER_RUN = 20  # har ishga tushishda faqat eng oxirgi shuncha mos vakansiya ko'rib chiqiladi

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


def matched_keyword(title):
    """Sarlavhada SEARCH_KEYWORDS ro'yxatidagi to'liq iboralardan yoki
    ROOT_PREFIXES/ROOT_EXACT_WORDS ro'yxatidagi ildiz so'zlardan biri
    bor-yo'qligini tekshiradi. hh.uz RSS qidiruvi "fuzzy" ishlaydi
    (tavsifda yoki mos kelmaydigan bo'limda so'z uchrasa ham natija
    qaytaradi — masalan "Project Manager"), shuning uchun faqat
    SARLAVHADA aynan mos so'z bo'lgan vakansiyalar qabul qilinadi.
    Mos kelgan so'zni qaytaradi, aks holda None."""
    title_lower = title.lower()

    # 1) Avval to'liq/aniq iboralar tekshiriladi (masalan "Power BI", "Tableau")
    for keyword in SEARCH_KEYWORDS:
        if keyword.lower() in title_lower:
            return keyword

    # 2) Keyin ildiz so'zlar tekshiriladi: "analitik", "analyst", "data",
    # "BI" va shu kabi barcha shakllar (masalan "Senior Data Analyst",
    # "Junior Analitik", "BI Developer")
    for pattern in ROOT_PATTERNS:
        match = pattern.search(title_lower)
        if match:
            return match.group(0)

    return None


def parse_pub_date(item):
    raw = item.findtext("pubDate", default="")
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None


def fetch_vacancies_for_keyword(keyword):
    params = {
        "text": keyword,
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
        pub_date = parse_pub_date(item)

        # Faqat sarlavhasi bizning 34 ta kalit so'zdan biriga aynan mos
        # keladigan vakansiyalarni qabul qilamiz (hh.uz'ning aloqasiz
        # natijalar chiqarishining oldini olish uchun, masalan "Project
        # Manager").
        if not matched_keyword(title):
            continue

        items.append({
            "id": link,  # link vakansiya uchun noyob identifikator sifatida ishlatiladi
            "title": title,
            "description": description,
            "link": link,
            "pub_date": pub_date,
        })
    return items


def fetch_vacancies():
    """Har bir kalit so'z uchun alohida so'rov yuboradi, natijalarni
    (link bo'yicha) takrorlanmasdan birlashtiradi, eng yangilaridan
    boshlab saralaydi va faqat oxirgi MAX_RESULTS_PER_RUN tasini qaytaradi."""
    seen_links = set()
    all_items = []
    for keyword in SEARCH_KEYWORDS:
        for item in fetch_vacancies_for_keyword(keyword):
            if item["id"] and item["id"] not in seen_links:
                seen_links.add(item["id"])
                all_items.append(item)
        time.sleep(1)  # hh.uz serveriga hurmat: so'rovlar orasida kichik pauza

    # pub_date bo'yicha eng yangisidan eskisiga saralaymiz (sana topilmasa eng oxiriga tushadi)
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    all_items.sort(key=lambda v: v["pub_date"] or epoch, reverse=True)
    return all_items[:MAX_RESULTS_PER_RUN]


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
