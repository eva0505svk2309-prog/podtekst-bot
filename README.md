# ПОДТЕКСТ — Telegram Mini App бот

Игра-квиз «подтекст» + match-3 18+ в виде Telegram Mini App.
Mini App: https://podtekst-mz6thc2z.agent.mira.tg
Бот: @podtekst_game_bot

## Деплой на Railway (один клик)

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template?template=https://github.com/eva0505svk2309-prog/podtekst-bot)

Либо вручную: New Project -> Deploy from GitHub Repo -> выбрать этот репозиторий.
Railway прочитает railway.json сам (start command, healthcheck, restart policy).

## Переменные окружения (обязательно задать в Railway -> Variables)

| Ключ | Значение |
|---|---|
| BOT_TOKEN | токен от @BotFather |
| WEBAPP_URL | https://podtekst-mz6thc2z.agent.mira.tg |
| MODE | polling |
| STORE_PATH | /data/store.json |

Volume: создайте volume на /data, иначе кошелёк игроков сбросится при передеплое.

## Локально

    pip install -r requirements.txt
    export BOT_TOKEN=...
    export WEBAPP_URL=...
    export MODE=polling
    python bot.py

## Команды бота

/start, /buy, /stars, /balance, /referral, /testpay, /refundlast, /paysupport, /help

## Оплата

Telegram Stars (XTR), provider_token пустой — цифровой товар, внешний провайдер не нужен.
