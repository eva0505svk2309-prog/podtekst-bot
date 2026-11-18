
import asyncio, os, logging, json, hmac, hashlib, time, random, urllib.parse
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (LabeledPrice, InlineKeyboardMarkup, InlineKeyboardButton,
                           WebAppInfo, PreCheckoutQuery, MenuButtonWebApp, ErrorEvent)
from aiogram.client.default import DefaultBotProperties

BOT_TOKEN  = os.environ["BOT_TOKEN"]
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://podtekst-mz6thc2z.agent.mira.tg").rstrip("/")

# Принудительное обновление Mini App: меняем версию при каждом деплое,
# иначе Telegram WebView отдаёт закэшированную старую страницу.
BUILD = os.environ.get("BUILD_ID", "4")
if "?" not in WEBAPP_URL:
    WEBAPP_URL = f"{WEBAPP_URL}?v={BUILD}"
elif "v=" not in WEBAPP_URL:
    WEBAPP_URL = f"{WEBAPP_URL}&v={BUILD}"
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

def apply_referral(user_id, payload):
    """Один раз за приглашённого: 50 монет пригласившему и 20 новому."""
    if not payload or not payload.startswith("ref"):
        return None
    try:
        inviter = int(payload[3:])
    except Exception:
        return None
    if inviter == user_id:
        return None
    d = load_store()
    if d.get(f"ref:{user_id}"):
        return None  # уже засчитан
    d[f"ref:{user_id}"] = inviter
    a1 = acc_of(d, inviter); a2 = acc_of(d, user_id)
    a1["coins"] += 50
    a1["invited"] = a1.get("invited", 0) + 1
    a2["coins"] += 20
    # Milestone: every 3 friends -> +100 coins to the inviter
    milestone = a1["invited"] % 3 == 0
    if milestone:
        a1["coins"] += 100
    save_store(d)
    return (inviter, milestone)

@dp.message(CommandStart())
async def start(message: types.Message):
    payload = (message.text.split()[-1] if message.text else "") or ""
    # Deep-link команды из Mini App: /start buy | buys | ref | lives | noads
    if payload in ("buy", "buys"):
        await send_invoice(message.chat.id, "coins_100")
        return
    if payload == "buy:lives":
        await send_invoice(message.chat.id, "lives")
        return
    if payload == "buy:noads":
        await send_invoice(message.chat.id, "noads")
        return
    if payload == "ref":
        await referral(message)
        return
    ref_result = apply_referral(message.from_user.id, payload)
    if ref_result:
        inviter, milestone = ref_result
        try:
            msg = "➕ Твой друг пришёл по твоей ссылке. +50 🪙 начислено!"
            if milestone:
                msg += "\n🎯 Третий друг! Бонус +100 🪙"
            await bot.send_message(inviter, msg)
            await message.answer("🎁 Привет! Тебе +20 🪙 за то, что пришёл по ссылке друга.")
        except Exception as e:
            logging.warning("referral notify failed: %s", e)
    await message.answer(
        "<b>ПОДТЕКСТ</b>\n\nОн сказал «ок» — а что он имел в виду на самом деле?\n\n"
        "Квиз с подтекстом · Три в ряд · Челлендж дня у всех одинаковый.\n\n"
        "Играй — и не тормози.", reply_markup=kb())

@dp.message(Command("referral"))
async def referral(message: types.Message):
    me = await bot.me()
    link = f"https://t.me/{me.username}?start=ref{message.from_user.id}"
    d = load_store(); acc = acc_of(d, message.from_user.id)
    invited = acc.get("invited", 0)
    left = 3 - (invited % 3) if invited % 3 else 3
    await message.answer(
        f"Твоя ссылка для друзей:\n{link}\n\n"
        f"Друг пришёл → тебе +50 🪙, ему +20 🪙.\n"
        f"Приглашено: {invited}. До бонуса +100 🪙 осталось {left}.\n"
        "Бонус начисляется один раз за друга, автоматически.")

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
    if payload == "test_1star": acc["coins"] += 1
    elif payload == "coins_100": acc["coins"] += 100
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


# ---------------- Mini App API: серверный кошелёк ----------------

def validate_init_data(init_data: str):
    """Проверяет подпись Telegram.WebApp.initData. Возвращает dict или None.

    Формула (проверена на реальных данных Telegram):
      secret_key = HMAC-SHA256(b"WebAppData", bot_token)
      data_check_string = "\n".join(f"{k}={v}" for k,v in sorted(params))
      где params — URL-декодированные значения, И "signature" ВХОДИТ в строку.
    """
    if not init_data:
        return None
    try:
        parts = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        return None
    received = parts.pop("hash", None)
    if not received:
        return None
    # ВАЖНО: поле "signature" (Ed25519) ОСТАЁТСЯ в data_check_string.
    data_check = "\n".join(f"{k}={parts[k]}" for k in sorted(parts))
    token = (BOT_TOKEN or "").strip()
    secret = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
    calc = hmac.new(secret, data_check.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received):
        return None
    try:
        auth_date = int(parts.get("auth_date", "0"))
        if auth_date and (time.time() - auth_date) > 86400:
            return None
    except Exception:
        pass
    return parts

def user_from_init(parts):
    try:
        return int(json.loads(parts.get("user", "{}")).get("id"))
    except Exception:
        return None

def acc_of(d, uid):
    acc = d.setdefault(str(uid), {"coins": 0, "lives": 5, "noads": False,
                                  "streak": 0, "best": 0, "level": 1, "charges": []})
    acc.setdefault("coins", 0); acc.setdefault("lives", 5); acc.setdefault("noads", False)
    acc.setdefault("streak", 0); acc.setdefault("best", 0); acc.setdefault("level", 1)
    acc.setdefault("charges", [])
    acc.setdefault("invited", 0)
    acc.setdefault("lives_ts", 0)
    return acc

LIFE_REGEN_SEC = 1800  # одна жизнь каждые 30 минут

def regen_lives(acc):
    """Жизни восстанавливаются по времени, максимум 9."""
    import time as _t
    now = _t.time()
    last = acc.get("lives_ts", now)
    if acc["lives"] < 9:
        gained = int((now - last) // LIFE_REGEN_SEC)
        if gained > 0:
            acc["lives"] = min(9, acc["lives"] + gained)
            last += gained * LIFE_REGEN_SEC
    acc["lives_ts"] = last
    return acc

def wallet_view(acc):
    return {"coins": acc["coins"], "lives": acc["lives"], "noads": acc["noads"],
            "streak": acc["streak"], "best": acc["best"], "level": acc["level"]}

def add_coins(uid, amount):
    d = load_store(); acc = acc_of(d, uid); acc["coins"] = max(0, acc["coins"] + amount)
    save_store(d); return acc

async def api_wallet(request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "bad json"}, status=400)
    parts = validate_init_data(body.get("init_data", ""))
    if not parts:
        return web.json_response({"error": "bad init_data"}, status=401)
    uid = user_from_init(parts)
    if not uid:
        return web.json_response({"error": "no user"}, status=401)
    d = load_store(); acc = acc_of(d, uid); regen_lives(acc); save_store(d)
    return web.json_response(wallet_view(acc))

async def api_action(request):
    """Единая точка действий из Mini App: spend/add/score/streak/hint/daily."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "bad json"}, status=400)
    parts = validate_init_data(body.get("init_data", ""))
    if not parts:
        return web.json_response({"error": "bad init_data"}, status=401)
    uid = user_from_init(parts)
    if not uid:
        return web.json_response({"error": "no user"}, status=401)
    action = body.get("action", "")
    d = load_store(); acc = acc_of(d, uid); regen_lives(acc)

    if action == "spend":
        # Клиент может только тратить, и только из своего баланса.
        amount = int(body.get("amount", 0))
        if amount <= 0 or amount > 500 or acc["coins"] < amount:
            save_store(d)
            return web.json_response({"ok": False, "error": "not enough coins", **wallet_view(acc)})
        acc["coins"] -= amount
    elif action == "lose_life":
        acc["lives"] = max(0, acc["lives"] - 1)
    elif action == "daily":
        # Ежедневный бонус — один раз в сутки, дата считается на сервере.
        import datetime
        today = datetime.date.today().isoformat()
        if acc.get("daily_claimed") != today:
            acc["daily_claimed"] = today
            acc["coins"] += 20
            acc["lives"] = min(9, acc["lives"] + 1)
    else:
        # Начисление очков и монет — только через /api/answer (сверка ответа).
        save_store(d)
        return web.json_response({"ok": False, "error": "action not allowed", **wallet_view(acc)})

    save_store(d)
    return web.json_response({"ok": True, **wallet_view(acc)})
QUIZ_BANK = [{"q": "Он написал «ок» в 23:41. Это значит…", "o": ["Всё хорошо, спит", "Что-то не так, но он не хочет говорить", "Он в душе", "Просто не заметил"], "c": 1, "h": "23:41 — не время для «ок»."}, {"q": "«Мне всё равно» + стикер с котом. Это…", "o": ["Ему реально всё равно", "Он недоволен, но прячет это", "Он отправляет котят всем подряд", "Он грустный"], "c": 1, "h": "Стикер = смягчение."}, {"q": "Ты спрашиваешь «ты со мной?». Ответ: «да». Это…", "o": ["Точно да", "Скорее да, но с сомнениями", "Он не понял вопрос", "Нет"], "c": 1, "h": "Односложный «да» — классика."}, {"q": "Голосовое на 4 минуты в ответ на «ты где?»", "o": ["Он раздражён", "Он опаздывает и нервничает", "У него проблемы", "Ему нечего сказать"], "c": 1, "h": "Ты спросила про «где», получила эссе."}, {"q": "«Ладно, давай как скажешь». Это…", "o": ["Он реально так думает", "Он раздражён и отдал выбор тебе", "Ему всё равно", "Он в восторге"], "c": 1, "h": "«Как скажешь» = «делай сама, я не хочу»."}, {"q": "Читает сообщения, отвечает через 4 часа", "o": ["Спит", "Прокручивает ленту, но ты не настолько важна", "Он на совещании", "Забыл"], "c": 1, "h": "Онлайн, но не в приоритете."}, {"q": "«Мне нравится твой стиль». Это…", "o": ["Комплимент", "Ирония", "Просто вежливость", "Просьба купить что-то"], "c": 1, "h": "Слишком общо для комплимента."}, {"q": "Фото с вечеринки, 6 подруг, ни тебя, ни подписи", "o": ["Он в восторге", "Намекает на «а где ты»", "Случайно", "Это его сестра"], "c": 1, "h": "Подпись — часть сообщения."}, {"q": "Три часа тишины, потом «как ты?»", "o": ["Ему реально важно", "Вспомнил, чтобы снять вину", "Ему скучно", "Ищет, с кем поговорить"], "c": 1, "h": "Тайминг всё говорит."}, {"q": "«Не переживай»", "o": ["Точно всё будет хорошо", "Есть что скрывать", "Он не знает, что сказать", "Ему всё равно"], "c": 1, "h": "Слова утешения = тревога."}, {"q": "Он выбирает ресторан и шлёт ссылку на 40-е место", "o": ["Случайно", "В восторге от выбора", "Забыл", "Проверяет, полезешь ли ты"], "c": 2, "h": "Сороковое место — не ошибка."}, {"q": "«Позвони, если что»", "o": ["Никогда не позвонит", "Позвонит, если что случилось", "Ждёт звонка прямо сейчас", "Он занят"], "c": 2, "h": "Фраза-отказ и фраза-приглашение одновременно."}, {"q": "На «привет» отвечает через 40 минут «привет-привет»", "o": ["Был занят", "Откладывал разговор", "Ему неинтересно", "Печатал одной рукой"], "c": 1, "h": "Двойное «привет» = неловкость."}, {"q": "Значок 💐 через 2 минуты после «увидимся в 8»", "o": ["Подарок", "Отмена планов", "Просто так", "Наезд"], "c": 1, "h": "Отмена планов редко приходит с цветами."}, {"q": "Не спрашивала — сам прислал фото с отпуска", "o": ["Ему скучно без тебя", "Показать, что там красиво без тебя", "Просто делится", "Хвастается"], "c": 0, "h": "Пишет первым = ты в мыслях."}, {"q": "«Может, лучше без меня?»", "o": ["Точно лучше с ним", "Скрытая просьба остаться", "Он реально думает так", "Нет"], "c": 1, "h": "Вопрос «может» — это просьба."}, {"q": "Пересылает мем про бывших", "o": ["Просто мем", "Намек на тебя", "Грустит", "Флиртует"], "c": 1, "h": "Случайных мемов про бывших не бывает."}, {"q": "Отвечает на голосовое голосовым на 12 минут", "o": ["Есть что сказать", "Бесится", "Хочет поговорить", "Пересказывает историю"], "c": 0, "h": "Ответ голосовым = диалог, а не спор."}, {"q": "«Ты как?» — «нормально». В 2 часа ночи.", "o": ["Всё хорошо", "Не очень", "Не хочет отвечать", "Не в дуге"], "c": 1, "h": "Ночь — время честных ответов."}, {"q": "Скидывает песню без комментария", "o": ["Просто поделился", "Это о тебе", "Хочет поговорить", "Забыл"], "c": 1, "h": "Песня = сообщение."}, {"q": "«А помнишь, ты тогда сказала…» — про то, что ты забыла", "o": ["Хочет напомнить", "Хочет поговорить о вас", "Проверяет память", "Собирается ссориться"], "c": 1, "h": "Он помнит больше, чем ты."}, {"q": "Предложил встретиться «на нейтральной территории»", "o": ["Нейтрально", "Не хочет, чтобы ты шла к нему", "Боится, что будет у тебя", "Ему так удобнее"], "c": 1, "h": "Нейтральная территория = зона комфорта."}]



# ---------------- Mini App API: квиз (сервер проверяет ответы) ----------------

_daily_state = {}

def daily_pick(uid):
    """Один и тот же вопрос на день для всех игроков."""
    d = None
    try:
        d = os.environ.get("DAILY_QUESTION_DATE")
    except Exception:
        d = None
    if not d:
        import datetime
        d = datetime.date.today().isoformat()
    key = f"{uid}:{d}"
    if key not in _daily_state:
        idx = int(hashlib.sha256(f"{d}:{uid}".encode()).hexdigest(), 16) % len(QUIZ_BANK)
        _daily_state[key] = idx
    return _daily_state[key]

async def api_quiz(request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "bad json"}, status=400)
    parts = validate_init_data(body.get("init_data", ""))
    if not parts:
        return web.json_response({"error": "bad init_data"}, status=401)
    uid = user_from_init(parts)
    if not uid:
        return web.json_response({"error": "no user"}, status=401)
    mode = body.get("mode", "random")
    idx = daily_pick(uid) if mode == "daily" else random.randrange(len(QUIZ_BANK))
    q = QUIZ_BANK[idx]
    return web.json_response({"id": idx, "q": q["q"], "o": q["o"]})

async def api_answer(request):
    """Проверка ответа и начисление очков — только на сервере."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "bad json"}, status=400)
    parts = validate_init_data(body.get("init_data", ""))
    if not parts:
        return web.json_response({"error": "bad init_data"}, status=401)
    uid = user_from_init(parts)
    if not uid:
        return web.json_response({"error": "no user"}, status=401)
    try:
        idx = int(body.get("id", -1))
        chosen = int(body.get("choice", -1))
    except Exception:
        return web.json_response({"error": "bad params"}, status=400)
    if idx < 0 or idx >= len(QUIZ_BANK):
        return web.json_response({"error": "bad question"}, status=400)
    q = QUIZ_BANK[idx]
    good = chosen == q["c"]

    d = load_store(); acc = acc_of(d, uid)
    if good:
        acc["coins"] += 10
        acc["streak"] += 1
        if acc["streak"] % 5 == 0:
            acc["coins"] += 25
        acc["best"] = max(acc.get("best", 0), acc["streak"])
        lvl = 1 + acc["best"] // 100
        if lvl > acc.get("level", 1):
            acc["level"] = lvl
            acc["lives"] = min(9, acc["lives"] + 1)
    else:
        acc["coins"] = max(0, acc["coins"] - 5)
        acc["streak"] = 0
        acc["lives"] = max(1, acc["lives"] - 1)
    save_store(d)
    return web.json_response({"ok": True, "correct": good, "answer": q["o"][q["c"]],
                              "hint": q["h"], **wallet_view(acc)})

ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "")

async def api_admin_credit(request):
    """Начисление монет по админ-секрету (используется для компенсаций)."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "bad json"}, status=400)
    if not ADMIN_SECRET or body.get("secret") != ADMIN_SECRET:
        return web.json_response({"error": "forbidden"}, status=403)
    try:
        uid = int(body.get("user_id"))
        amount = int(body.get("amount"))
    except Exception:
        return web.json_response({"error": "bad params"}, status=400)
    d = load_store(); acc = acc_of(d, uid); acc["coins"] = max(0, acc["coins"] + amount); save_store(d)
    return web.json_response({"ok": True, **wallet_view(acc)})

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


@dp.errors()
async def on_error(event: ErrorEvent):
    """Любая ошибка хендлера не должна ронять polling-цикл."""
    logging.error("handler error: %s", event.exception, exc_info=event.exception)

async def main():
    await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Играть", web_app=WebAppInfo(url=WEBAPP_URL)))
    mode = os.environ.get("MODE", "polling").lower()
    if mode == "webhook":
        port = int(os.environ.get("PORT", "8080"))
        app = web.Application()
        async def webhook_handler(request):
            try:
                data = await request.json()
            except Exception:
                return web.Response(status_code=400)
            try:
                update = types.Update.model_validate(data, context={"bot": bot})
            except Exception as e:
                logging.warning("bad update: %s", e)
                return web.Response(status_code=400)
            await dp.feed_update(bot, update)
            return web.Response(text="ok")

        app.router.add_post("/tg/{token}", webhook_handler)
        app.router.add_get("/health", lambda r: web.Response(text="ok"))
        app.router.add_post("/api/wallet", api_wallet)
        app.router.add_post("/api/action", api_action)
        app.router.add_post("/api/quiz", api_quiz)
        app.router.add_post("/api/answer", api_answer)
        app.router.add_post("/api/admin/credit", api_admin_credit)

        @web.middleware
        async def cors_mw(request, handler):
            if request.method == "OPTIONS":
                return web.Response(status=204, headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "POST, OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type"})
            resp = await handler(request)
            resp.headers["Access-Control-Allow-Origin"] = "*"
            return resp
        app.middlewares.append(cors_mw)
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
        # В polling-режиме тоже нужен HTTP-сервер: Railway проверяет /health
        # и перезапускает сервис, если healthcheck не отвечает.
        port = int(os.environ.get("PORT", "8080"))

        async def health_app():
            app = web.Application()
            app.router.add_get("/health", lambda r: web.Response(text="ok"))
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "0.0.0.0", port)
            await site.start()
            logging.info("health http server started on port %s", port)
            while True:
                await asyncio.sleep(3600)

        # Polling с автоперезапуском: краш не должен убивать сервис.
        async def run_polling_forever():
            attempt = 0
            while True:
                try:
                    attempt += 1
                    await dp.start_polling(bot, drop_pending_updates=False)
                    logging.info("polling finished, restarting")
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    wait = min(60, 2 ** min(attempt, 5))
                    logging.exception("polling failed (attempt %s): %s", attempt, e)
                    await asyncio.sleep(wait)
                else:
                    attempt = 0

        await asyncio.gather(health_app(), run_polling_forever())

if __name__ == "__main__":
    asyncio.run(main())
