# hh.uz Data Analytics vakansiya boti

Bu skript har 30 daqiqada tashkent.hh.uz (hh.ru API orqali) dagi "data analyst /
data analytics" bo'yicha yangi vakansiyalarni tekshiradi va Telegram botga yuboradi.

## 1-qadam: Telegram bot yaratish

1. Telegramda **@BotFather** ga yozing.
2. `/newbot` buyrug'ini yuboring, botga ism va username bering.
3. BotFather sizga **token** beradi (masalan `123456:ABC-DEF...`). Buni saqlab qo'ying.
4. Yangi botingizga o'zingiz `/start` deb yozing (bot sizga xabar yubora olishi uchun
   avval siz u bilan suhbatni boshlashingiz kerak).

## 2-qadam: chat_id ni topish

1. Brauzerda quyidagi manzilga o'ting (TOKEN o'rniga o'z tokeningizni qo'ying):
   `https://api.telegram.org/botTOKEN/getUpdates`
2. Natijada `"chat":{"id": 123456789, ...}` ko'rinishida raqam chiqadi — shu sizning
   `chat_id`ingiz.

## 3-qadam: GitHub repo tayyorlash

1. GitHub'da yangi **private** repo yarating (masalan `hh-vacancy-bot`).
2. Ushbu papkadagi barcha fayllarni (shu jumladan `.github` papkasini) repo'ga
   yuklang (push qiling).

## 4-qadam: Maxfiy kalitlarni (secrets) qo'shish

Repo sahifasida: **Settings → Secrets and variables → Actions → New repository secret**

- `TELEGRAM_BOT_TOKEN` — BotFather bergan token
- `TELEGRAM_CHAT_ID` — 2-qadamda topgan chat_id

## 5-qadam: Ishga tushirish

- Workflow avtomatik ravishda har 30 daqiqada ishlaydi.
- Qo'lda sinab ko'rish uchun: repo'da **Actions** bo'limiga o'ting → 
  "Check hh.uz data analytics vacancies" → **Run workflow**.

## Sozlamalarni o'zgartirish

- `check_vacancies.py` faylidagi `SEARCH_QUERY` o'zgaruvchisi orqali qidiruv
  so'zlarini o'zgartirishingiz mumkin.
- Tekshirish chastotasini o'zgartirish uchun `.github/workflows/check_vacancies.yml`
  faylidagi `cron` qatorini tahrirlang (masalan `*/15 * * * *` = har 15 daqiqada).
