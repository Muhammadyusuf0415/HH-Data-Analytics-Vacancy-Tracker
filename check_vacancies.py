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
- GitHub Actions orqali muntazam (har 15 daqiqada) ishga tushiriladi.

TARMOQ XATOLARI HAQIDA: hh.uz ba'zan GitHub Actions kabi datacenter
IP'lardan kelayotgan so'rovlarni (ayniqsa tez-tez so'ralganda) vaqtincha
"osiltirib qo'yishi" mumkin (connect timeout). Shu sababli:
  1) har bir kalit so'z bo'yicha so'rov mustaqil urinish hisoblanadi —
     biri muvaffaqiyatsiz bo'lsa, faqat o'sha so'z o'tkazib yuboriladi,
     qolganlari davom etadi (butun skript yiqilib qolmaydi);
  2) vaqtinchalik xatolarda avtomatik qayta urinish (retry + backoff)
     ishlatiladi;
  3) so'rovlar orasidagi pauza tasodifiy (jitter) qilingan, bot
     sifatida aniqlanish ehtimolini kamaytirish uchun.
"""

import html
import json
import os
import random
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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

RSS_URL = "https://tashkent.hh.uz/search/vacancy/rss"
SEEN_IDS_FILE = "seen_ids.json"
MAX_STORED_IDS = 2000  # fayl cheksiz o'sib ketmasligi uchun
MAX_RESULTS_PER_RUN = 20  # har ishga tushishda faqat eng oxirgi shuncha mos vakansiya ko'rib chiqiladi

# hh.uz ba'zan (ayniqsa GitHub Actions kabi datacenter IP'lardan tez-tez
# so'rov yuborilganda) ulanishni "osilтirib qo'yadi" (connect timeout).
# Shu sababli so'rov vaqti qisqartirildi (tezroq aniqlash uchun) va
# vaqtinchalik xatolarda avtomatik qayta urinish (retry) qo'shildi.
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 20
REQUEST_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)
MAX_RETRIES = 2

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

# Bir nechta real brauzer User-Agent'lari orasida tasodifiy tanlanadi —
# bu so'rovlarni bir xil "signature"ga ega bo'lib, bot sifatida
# aniqlanishi ehtimolini biroz kamaytiradi.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36",
]


def build_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    }


def build_session():
    """Vaqtinchalik tarmoq xatolarida (timeout, 5xx) avtomatik qayta
    urinadigan (retry) sessiya yaratadi, shunda bitta muvaffaqiyatsiz
    so'rov butun skriptni yiqitmaydi."""
    session = requests.Session()
    retry = Retry(
        total=MAX_RETRIES,
        connect=MAX_RETRIES,
        read=MAX_RETRIES,
        backoff_factor=1.5,  # 0s, 1.5s, 3s ...
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


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
    """Sarlavhada SEARCH_KEYWORDS ro'yxatidagi so'zlardan biri bor-yo'qligini
    tekshiradi. hh.uz RSS qidiruvi "fuzzy" ishlaydi (tavsifda yoki mos
    kelmaydigan bo'limda so'z uchrasa ham natija qaytaradi — masalan
    "Project Manager"), shuning uchun faqat SARLAVHADA aynan mos so'z
    bo'lgan vakansiyalar qabul qilinadi. Mos kelgan so'zni qaytaradi,
    aks holda None."""
    title_lower = title.lower()
    for keyword in SEARCH_KEYWORDS:
        if keyword.lower() in title_lower:
            return keyword
    return None


def parse_pub_date(item):
    raw = item.findtext("pubDate", default="")
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None


def fetch_vacancies_for_keyword(session, keyword):
    """Bitta kalit so'z bo'yicha RSS'ni oladi. Tarmoq xatoligi yoki
    noto'g'ri javob bo'lsa, istisno tashlaydi — chaqiruvchi funksiya buni
    ushlab, faqat shu kalit so'zni o'tkazib yuboradi (butun skript
    to'xtamaydi)."""
    params = {
        "text": keyword,
        "area": HH_AREA_ID,
        "order_by": "publication_time",
    }
    resp = session.get(
        RSS_URL,
        params=params,
        headers=build_headers(),
        timeout=REQUEST_TIMEOUT,
    )
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
    boshlab saralaydi va faqat oxirgi MAX_RESULTS_PER_RUN tasini qaytaradi.

    MUHIM: bitta kalit so'z bo'yicha so'rov muvaffaqiyatsiz bo'lsa (masalan,
    hh.uz vaqtincha ulanishni to'xtatib qo'ysa), butun tekshiruv
    to'xtamaydi — shu kalit so'z o'tkazib yuboriladi va qolganlari bilan
    davom etiladi. Aks holda bitta vaqtinchalik tarmoq xatoligi barcha
    vakansiyalarni (hattoki muvaffaqiyatli topilganlarini ham) yo'qotib
    qo'yardi.
    """
    session = build_session()
    seen_links = set()
    all_items = []
    failed_keywords = []

    for keyword in SEARCH_KEYWORDS:
        try:
            for item in fetch_vacancies_for_keyword(session, keyword):
                if item["id"] and item["id"] not in seen_links:
                    seen_links.add(item["id"])
                    all_items.append(item)
        except (requests.exceptions.RequestException, ET.ParseError) as exc:
            failed_keywords.append(keyword)
            print(f"  ⚠️  '{keyword}' uchun so'rov muvaffaqiyatsiz bo'ldi: {exc}")
            continue
        finally:
            # hh.uz serveriga hurmat: so'rovlar orasida tasodifiy pauza
            # (bir xil ritm bot sifatida aniqlanish xavfini oshiradi)
            time.sleep(random.uniform(0.8, 1.8))

    if failed_keywords:
        print(
            f"Jami {len(failed_keywords)}/{len(SEARCH_KEYWORDS)} ta kalit so'z "
            f"bo'yicha so'rov amalga oshmadi (hh.uz vaqtincha ulanmagan bo'lishi "
            f"mumkin): {', '.join(failed_keywords)}"
        )
    if failed_keywords and len(failed_keywords) == len(SEARCH_KEYWORDS):
        print(
            "Barcha so'rovlar muvaffaqiyatsiz bo'ldi — hh.uz ushbu ishga "
            "tushish paytida umuman ulanmagan. Keyingi ishga tushishda "
            "qayta urinib ko'riladi."
        )

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
    failed_count = 0
    for vacancy in vacancies:
        vac_id = vacancy["id"]
        if not vac_id or vac_id in seen_ids:
            continue

        message = format_message(vacancy)
        try:
            send_to_telegram(message)
        except requests.exceptions.RequestException as exc:
            # Telegramga yuborishda xatolik bo'lsa, shu vakansiyani "seen"
            # deb belgilamaymiz — keyingi ishga tushishda qayta uriniladi.
            failed_count += 1
            print(f"  ⚠️  '{vacancy['title']}' yuborilmadi: {exc}")
            continue

        seen_ids.add(vac_id)
        new_count += 1
        time.sleep(1)  # Telegram rate limit uchun kichik pauza

    save_seen_ids(seen_ids)
    print(
        f"Tekshiruv tugadi. Yangi yuborilgan vakansiyalar soni: {new_count}"
        + (f" (yuborilmadi: {failed_count})" if failed_count else "")
    )


if __name__ == "__main__":
    main()
