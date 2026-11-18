# ПОДТЕКСТ — бот

Telegram-бот Mini App "ПОДТЕКСТ" (квиз + три в ряд 18+).
Mini App: https://podtekst-mz6thc2z.agent.mira.tg

## Переменные окружения (обязательно)
- BOT_TOKEN — токен от @BotFather (сделай /revoke, старый засвечен)
- WEBAPP_URL — https://podtekst-mz6thc2z.agent.mira.tg
- MODE — polling (Railway/фоновый воркер) или webhook (Render/веб-сервис)
- PORT — порт (Render проставляет сам)
- WEBHOOK_BASE — публичный адрес сервиса без слеша (для MODE=webhook)

## Railway (Hobby, $5/мес, не засыпает) — рекомендуется
1. railway.app -> Login with GitHub -> New Project -> Deploy from GitHub Repo (или Empty Service)
2. Settings -> Variables: BOT_TOKEN, WEBAPP_URL=https://podtekst-mz6thc2z.agent.mira.tg, MODE=polling
3. Settings -> Build & Deploy: Root=/, Build=`pip install -r requirements.txt`, Start=`python bot.py`
4. Deploy -> в логах "Run polling for bot @podtekst_game_bot"

## Render (free, засыпает после 15 мин без трафика)
1. render.com -> New -> Web Service -> Connect repo
2. Environment: Python, Build `pip install -r requirements.txt`, Start `python bot.py`
3. Environment Variables: BOT_TOKEN, WEBAPP_URL, MODE=webhook, WEBHOOK_BASE=https://<service>.onrender.com
4. Deploy -> в логах "webhook mode on port"
5. После деплоя проверь getWebhookInfo: url должен совпадать с WEBHOOK_BASE/tg/<token>

## Локально
    pip install -r requirements.txt
    BOT_TOKEN=... WEBAPP_URL=... MODE=polling python bot.py
