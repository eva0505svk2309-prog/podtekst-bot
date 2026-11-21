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


## Призовой конкурс (добавлено 21.11.2026)
- Вход БЕСПЛАТНЫЙ: каждые 30 очков активности = 1 билет.
  Очки: квиз +10, match-3 +5, ежедневный заход +20, рефералка +25, шеринг +15.
- Вход ПЛАТНЫЙ: 100 Stars = 5 билетов (пакет contest_entry).
- Приз: ПОДАРОК Telegram 💎 (~100 Stars номинала) + игровой пакет 500 монет, 3 жизни,
  без рекламы.
  Почему подарок, а не Stars: бот НЕ может перевести Stars пользователю
  (в Bot API нет такого метода), но умеет отправлять подарки через sendGift.
  Подарок виден в профиле, его можно улучшить до NFT и переслать —
  воспринимаемая ценность выше, чем у звёзд.
- Эндпоинты: POST /api/contest, POST /api/contest/buy
- Команды: /contest (правила), /giveprize <user_id> [stars] (вручение приза, ADMIN_ID)
- ADMIN_ID = 8684637509
