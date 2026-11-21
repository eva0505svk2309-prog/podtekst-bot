# ПОДТЕКСТ — Telegram Mini App

Игра про то, что человек на самом деле имел в виду.

## Запуск
    pip install -r requirements.txt
    export BOT_TOKEN=...
    export WEBAPP_URL=https://podtekst-mz6thc2z.agent.mira.tg
    export BUILD_ID=7
    python bot.py

## Переменные окружения (Railway)
- BOT_TOKEN — токен от @BotFather (обязательно, в коде токена НЕТ)
- WEBAPP_URL — адрес Mini App
- BUILD_ID — версия фронта, добавляется как ?v= в ссылку

## Структура
- bot.py — бот, API Mini App, Stars-оплата, планировщик постов
- index.html — Mini App (квиз, три в ряд, ежедневный челлендж, профиль)


## Призовой конкурс
- Вход бесплатный: 30 очков активности = 1 билет. Платный: 100 Stars = 5 билетов.
- Приз: подарок Telegram 💎 (~100 Stars) + 500 монет, 3 жизни, без рекламы.
