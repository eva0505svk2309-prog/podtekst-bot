
import asyncio, os, logging, json
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (LabeledPrice, InlineKeyboardMarkup, InlineKeyboardButton,
                           WebAppInfo, PreCheckoutQuery, MenuButtonWebApp)
from aiogram.client.default import DefaultBotProperties

BOT_TOKEN  = os.environ["BOT_TOKEN"]
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://podtekst-mz6thc2z.agent.mira.tg")
CHANNEL    = os.environ.get("CHANNEL_URL", "https://t.me/podtekst_game")
CHAT_ID    = os.environ.get("CHANNEL_CHAT_ID", "-1004469511755")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logging.getLogger("aiogram").setLevel(logging.WARNING)
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp  = Dispatcher()

def kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Играть", web_app=WebAppInfo(url=WEBAPP_URL))],
        [InlineKeyboardButton(text="Челлендж дня", web_app=WebAppInfo(url=WEBAPP_URL))],
        [InlineKeyboardButton(text="Канал", url=CHANNEL)],
    ])

PACKS = {
    "test_1star": (1, "Тест 1 звезда"),
    "coins_100": (100, "100 монет"),
    "coins_500": (500, "500 монет"),
    "lives":     (200, "+1 жизнь"),
    "noads":     (350, "Без рекламы"),
}
ALIAS = {"buy": "coins_100", "buy:lives": "lives", "buy:noads": "noads"}

@dp.message(CommandStart())
async def start(message: types.Message):
    await message.answer(
        "<b>ПОДТЕКСТ</b>\n\nОн сказал «ок» — а что он имел в виду на самом деле?\n\n"
        "Квиз с подтекстом · Три в ряд · Челлендж дня у всех одинаковый.\n\n"
        "Играй — и не тормози.", reply_markup=kb())

@dp.message(Command("referral"))
async def referral(message: types.Message):
    me = await bot.me()
    link = f"https://t.me/{me.username}?start=ref{message.from_user.id}"
    await message.answer(f"Твоя ссылка для друзей:\n{link}\n\nКаждому, кто зашёл по ней, — 50 монет.")

@dp.message(Command("stars"))
async def stars(message: types.Message):
    await message.answer("Пакеты монет (Telegram Stars):\n\n"
        "100 монет — 100 ⭐\n500 монет — 500 ⭐\n+1 жизнь — 200 ⭐\nБез рекламы — 350 ⭐\n\n"
        "Или просто жми «Купить монеты» внутри игры.", reply_markup=kb())

@dp.message(Command("help"))
async def help_(message: types.Message):
    await message.answer("/start — играть\n/buy — купить монеты\n/stars — цены\n/referral — ссылка для друзей",
                         reply_markup=kb())

@dp.message(Command("buy"))
async def buy(message: types.Message):
    await send_invoice(message.chat.id, "coins_100")

async def send_invoice(chat_id: int, payload: str):
    amount, title = PACKS.get(payload, PACKS["coins_100"])
    await bot.send_invoice(chat_id=chat_id, title=title,
        description="ПОДТЕКСТ — покупка в игре (Telegram Stars)",
        payload=payload, provider_token="", currency="XTR",
        prices=[LabeledPrice(label=title, amount=amount)])


@dp.message(Command("paysupport"))
async def paysupport(message: types.Message):
    await message.answer(
        "Проблемы с оплатой в ПОДТЕКСТ? Напишите сюда: @mira_support_team\n"
        "Telegram не помогает с покупками внутри ботов — решаем мы.")

@dp.message(Command("testpay"))
async def testpay(message: types.Message):
    await send_invoice(message.chat.id, "test_1star")

@dp.message(lambda m: m.content_type == types.ContentType.REFUNDED_PAYMENT)
async def refunded(message: types.Message):
    logging.info("REFUND user=%s", message.from_user.id)

@dp.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    try:
        await q.answer(ok=True)
    except Exception as e:
        logging.warning("pre_checkout: %s", e)

STORE = os.environ.get("STORE_PATH", "/data/store.json")

def load_store():
    try:
        return json.load(open(STORE, encoding="utf-8"))
    except Exception:
        return {}

def save_store(d):
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STORE)

def grant(user_id, payload):
    d = load_store()
    u = str(user_id)
    acc = d.setdefault(u, {"coins": 0, "lives": 0, "noads": False, "charges": []})
    if payload == "coins_100": acc["coins"] += 100
    elif payload == "coins_500": acc["coins"] += 500
    elif payload == "lives": acc["lives"] += 1
    elif payload == "noads": acc["noads"] = True
    save_store(d)
    return acc

@dp.message(F.successful_payment)
async def paid(message: types.Message):
    p = message.successful_payment
    charge = p.telegram_payment_charge_id
    logging.info("PAYMENT user=%s payload=%s stars=%s charge=%s", message.from_user.id, p.invoice_payload, p.total_amount, charge)
    acc = grant(message.from_user.id, p.invoice_payload)
    d = load_store(); d.setdefault(str(message.from_user.id), {}).setdefault("charges", []).append(charge)
    save_store(d)
    await message.answer(f"Оплата прошла — товар уже в кошельке игры.\n\nБаланс: {acc['coins']} монет · {acc['lives']} жизней.", reply_markup=kb())

@dp.message(Command("balance"))
async def balance(message: types.Message):
    acc = load_store().get(str(message.from_user.id), {"coins": 0, "lives": 0, "noads": False})
    await message.answer(f"Кошелёк:\n{acc['coins']} монет · {acc['lives']} жизней · без рекламы: {'да' if acc.get('noads') else 'нет'}", reply_markup=kb())

@dp.message(Command("refundlast"))
async def refundlast(message: types.Message):
    d = load_store(); acc = d.get(str(message.from_user.id))
    if not acc or not acc.get("charges"):
        await message.answer("Нечего возвращать — нет завершённых платежей."); return
    charge = acc["charges"][-1]
    try:
        await bot.refund_star_payment(user_id=message.from_user.id, telegram_payment_charge_id=charge)
        await message.answer("Звезда возвращена на твой баланс.")
    except Exception as e:
        logging.warning("refund failed: %s", e)
        await message.answer(f"Не получилось вернуть: {e}")

@dp.message(lambda m: m.content_type == types.ContentType.WEB_APP_DATA)
async def app_data(message: types.Message):
    data = (message.web_app.data or "").strip()
    logging.info("APPDATA user=%s data=%s", message.from_user.id, data)
    if data in ALIAS:
        await send_invoice(message.chat.id, ALIAS[data])
    elif data.startswith("buy:"):
        await send_invoice(message.chat.id, data[4:])
    elif data == "ref":
        await referral(message)
    elif data.startswith("score:"):
        await message.answer("Твой результат принят — 20 монет зачислены. Выложи результат в канале.")
        await publish_score(message.from_user, data[6:])


async def publish_score(user, result_text):
    """Публикует результат игрока в канале (если бот — админ)."""
    if not CHAT_ID:
        return
    name = user.first_name or "Игрок"
    text = (f"\U0001F3AF <b>{name}</b> — результат: <b>{result_text}</b>\n"
            f"Сыграть так же: {WEBAPP_URL}")
    try:
        await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="HTML",
                               disable_web_page_preview=True)
    except Exception as e:
        logging.warning("publish_score failed: %s", e)

def detect_base():
    """Определяем публичный адрес сервиса на разных платформах."""
    base = os.environ.get("WEBHOOK_BASE", "").strip().rstrip("/")
    if base:
        return base
    r = os.environ.get("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    if r:
        return r
    d = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
    if d:
        return f"https://{d}"
    return ""


async def main():
    await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Играть", web_app=WebAppInfo(url=WEBAPP_URL)))
    mode = os.environ.get("MODE", "polling").lower()
    if mode == "webhook":
        from aiohttp import web
        port = int(os.environ.get("PORT", "8080"))
        app = web.Application()
        app.router.add_post("/tg/{token}", dp.feed_update)
        app.router.add_get("/health", lambda r: web.Response(text="ok"))
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", port).start()
        logging.info("webhook http server started on port %s", port)

        async def ensure_webhook():
            url = None
            for _ in range(120):
                base = detect_base()
                if base:
                    url = f"{base}/tg/{BOT_TOKEN}"
                    break
                await asyncio.sleep(5)
            if not url:
                logging.error("cannot detect public base url")
                return
            for attempt in range(30):
                try:
                    info = await bot.get_webhook_info()
                    if info.url != url:
                        await bot.set_webhook(url, drop_pending_updates=False)
                        logging.info("webhook set to %s", url)
                    else:
                        logging.info("webhook already correct: %s", url)
                    return
                except Exception as e:
                    logging.warning("webhook attempt %s failed: %s", attempt, e)
                    await asyncio.sleep(10)

        await ensure_webhook()
        # Держим процесс живым и пере-ставляем webhook, если Telegram его сбросил.
        while True:
            await asyncio.sleep(3600)
            try:
                info = await bot.get_webhook_info()
                if not info.url:
                    await ensure_webhook()
            except Exception as e:
                logging.warning("webhook keepalive: %s", e)
    else:
        await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
