# ПОДТЕКСТ — Telegram Mini App

Игра про то, что человек на самом деле имел в виду.

## Запуск
    pip install -r requirements.txt
    export BOT_TOKEN=...
    export WEBAPP_URL=https://podtekst-mz6thc2z.agent.mira.tg
    export BUILD_ID=7
    python bot.py

## Переменные окружения (Railway, environment production)
- BOT_TOKEN — токен @BotFather (в коде токена НЕТ, только os.environ)
- WEBAPP_URL — https://podtekst-mz6thc2z.agent.mira.tg
- BUILD_ID — 7 (добавляется к ссылке как ?v=)
- CHANNEL_CHAT_ID — -1004469511755
- ADMIN_SECRET — секрет для /api/admin/credit
- DATA_DIR — /data (постоянное хранилище кошельков игроков)

## Структура
- bot.py — бот, 22 API-эндпоинта Mini App, Stars-оплата, планировщик постов
- index.html — Mini App (квиз, три в ряд, ежедневный челлендж, профиль)

## Проверено 20.11.2026
- Railway деплой SUCCESS, /health -> ok
- домены: podtekst-bot-production.up.railway.app (основной)
- гейт 18+, квиз (вердикт + штраф -5), три в ряд (уровень 1, цель 400),
  челлендж дня, профиль (гирлянда, топ, шеринг) — ошибок JS нет
- канал @podtekst_game: 6 постов, описание, закреп
- init_data валидируется HMAC-SHA256 на каждом эндпоинте
