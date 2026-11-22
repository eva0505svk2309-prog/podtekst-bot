
import asyncio, os, logging, json, hmac, hashlib, time, random, urllib.parse, datetime
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (LabeledPrice, InlineKeyboardMarkup, InlineKeyboardButton,
                           WebAppInfo, PreCheckoutQuery, MenuButtonWebApp, ErrorEvent)
from aiogram.client.default import DefaultBotProperties

BOT_TOKEN  = os.environ["BOT_TOKEN"]
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://podtekst-mz6thc2z.agent.mira.tg").rstrip("/")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "podtekst_game_bot")

# Принудительное обновление Mini App: меняем версию при каждом деплое,
# иначе Telegram WebView отдаёт закэшированную старую страницу.
BUILD = os.environ.get("BUILD_ID", "5")
if "?" not in WEBAPP_URL:
    WEBAPP_URL = f"{WEBAPP_URL}?v={BUILD}"
elif "v=" not in WEBAPP_URL:
    WEBAPP_URL = f"{WEBAPP_URL}&v={BUILD}"
CHANNEL    = os.environ.get("CHANNEL_URL", "https://t.me/podtekst_game")

# ---- Партнёрский блок Mira: игрок получает монеты в игре, мы — токены Миры ----
MIRA_REF   = os.environ.get("MIRA_REF", "8684637509")
MIRA_URL   = f"https://t.me/mira?start=ref_{MIRA_REF}"
PROMO_REWARD = 50          # 🪙 игроку за подключение Mira
MISS_PENALTY_PCT = 10      # % от монет за пропуск дня (фишка Major)
SUB_REWARD = 50            # 🪙 за подписку на канал (фишка Notcoin)
# Deep-link: открыть Mira с уже подставленным вопросом (черновик в поле ввода).
def mira_link(text):
    """Ссылка на Mira: реферальный параметр (даёт токены) + подставленный текст (черновик).
    Реферал обязателен: без start= дохода не будет."""
    import urllib.parse as _up
    return (f"https://t.me/mira?start=ref_{MIRA_REF}"
            f"&text={_up.quote(text[:240])}")

PROMO_TITLE = "🤖 Mira — ИИ, который тебя понимает"
PROMO_TEXT  = ("Mira — твой персональный ИИ в Telegram: отвечает на вопросы, "
               "пишет тексты, разбирает фото и помогает с проектами. "
               "Поставь её рядом с ПОДТЕКСТОМ — и подтексты будут разгадываться вдвое быстрее.")
def html_escape(s: str) -> str:
    """Экранирует спецсимволы HTML — иначе имя игрока ломает разметку."""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


CHAT_ID    = os.environ.get("CHANNEL_CHAT_ID", "-1004469511755")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logging.getLogger("aiogram").setLevel(logging.WARNING)
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp  = Dispatcher()

def webapp_url(src_code=""):
    """URL Mini App с меткой источника: Telegram отдаёт её в init_data как start_param."""
    if not src_code:
        return WEBAPP_URL
    sep = "&" if "?" in WEBAPP_URL else "?"
    return f"{WEBAPP_URL}{sep}startapp={src_code}"


def kb(src_code=""):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Играть", web_app=WebAppInfo(url=webapp_url(src_code)))],
        [InlineKeyboardButton(text="Челлендж дня", web_app=WebAppInfo(url=WEBAPP_URL))],
        # Подписка на канал — главный источник подписчиков. Кнопка ведёт прямо
        # в канал, рядом кнопка проверки: бот сам проверяет подписку и платит.
        [InlineKeyboardButton(text="📢 Подписаться на канал", url=CHANNEL)],
        [InlineKeyboardButton(text=f"✓ Проверить подписку (+{SUB_REWARD} 🪙)",
                              callback_data="check_sub")],
        [InlineKeyboardButton(text="🤖 Mira — твой ИИ в Telegram", url=MIRA_URL)],
    ])

ADMIN_ID = int(os.environ.get("ADMIN_ID", "0") or 0)

PACKS = {
    "test_1star": (1, "Тест 1 звезда"),
    "coins_100": (100, "100 монет"),
    "coins_500": (500, "500 монет"),
    "lives":     (200, "+1 жизнь"),
    "noads":     (350, "Без рекламы"),
    "contest_entry": (100, "5 билетов в розыгрыш"),
}
ALIAS = {"buy": "coins_100", "buy:lives": "lives", "buy:noads": "noads",
          "buy:contest_entry": "contest_entry"}

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
    contest_add_points(d, a1, "ref", 25); bp_add_points(d, a1, "ref")
    a2["coins"] += 20
    # Milestone: every 3 friends -> +100 coins to the inviter
    milestone = a1["invited"] % 3 == 0
    if milestone:
        a1["coins"] += 100
        # каждые 3 приглашённых — ещё и жизнь: рефералка мотивирует не только монетами
        a1["lives"] = min(9, int(a1.get("lives", 5)) + 1)
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
    if payload == "buy:contest_entry":
        await send_invoice(message.chat.id, "contest_entry")
        return
    if payload == "ref":
        await referral(message)
        return
    # Старые рекламные посты вели на /play — открываем игру, а не просто текст.
    # Telegram не даёт web_app-кнопки в постах канала, поэтому реклама всегда
    # ведёт в личку с ботом, и уже бот открывает Mini App с меткой источника.
    if payload in ("play", "game", "start"):
        src_code = ""
        await message.answer(
            "<b>ПОДТЕКСТ</b>\n\nОн сказал «ок» — а что он имел в виду на самом деле?\n\n"
            "Квиз с подтекстом · Три в ряд · Челлендж дня у всех одинаковый.\n\n"
            f"📢 <b>Подпишись на канал</b> — новые челленджи первыми и "
            f"<b>+{SUB_REWARD} 🪙</b> на кошелёк.\n"
            f"🎉 И играй в розыгрыш: 1 место — 100 ⭐, 2 — 50 ⭐, 3 — 25 ⭐. "
            "Каждые 30 очков активности = 1 билет, бесплатно. Финал — 15 декабря.",
            reply_markup=kb(src_code))
        return
    # Рекламная метка канала (ch_xxx) прилетает сюда — запоминаем её,
    # чтобы статистика по каналам считалась (init_data её не содержит).
    src_code = ""
    if payload.startswith("ch_"):
        src_code = payload[:40]
        try:
            d = load_store(); acc = acc_of(d, message.from_user.id)
            acc["pending_src"] = src_code
            save_store(d)
        except Exception as e:
            logging.warning("pending_src save failed: %s", e)
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
        "Играй — и не тормози.\n\n"
        f"📢 <b>Подпишись на канал</b> — новые челленджи первыми и "
        f"<b>+{SUB_REWARD} 🪙</b> на кошелёк.\n"
        f"🎉 И играй в розыгрыш: 1 место — 100 ⭐, 2 — 50 ⭐, 3 — 25 ⭐. "
        f"Каждые 30 очков активности = 1 билет, бесплатно. Финал — 15 декабря.\n\n"
        f"🤖 Нужен ИИ-помощник в Telegram? Забери Mira по ссылке и получи "
        f"<b>+{PROMO_REWARD} 🪙</b> в игре:\n{MIRA_URL}", reply_markup=kb(src_code))

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

async def _channel_sub_check(user_id: int):
    """Проверяет подписку на канал и начисляет бонус один раз.

    Возвращает (status, reward): status in ok|not_sub|already|no_channel.
    """
    if not CHAT_ID:
        return "no_channel", 0
    try:
        member = await bot.get_chat_member(CHAT_ID, user_id)
        st = getattr(member, "status", "")
    except Exception as e:
        logging.info("get_chat_member: %s", e)
        return "not_sub", 0
    if st not in ("member", "administrator", "creator", "restricted"):
        return "not_sub", 0
    d = load_store(); acc = acc_of(d, user_id)
    sub = acc.setdefault("channel_sub", {"claimed": False})
    if sub.get("claimed"):
        return "already", 0
    acc["coins"] = int(acc.get("coins", 0) or 0) + SUB_REWARD
    sub["claimed"] = True; sub["ts"] = int(time.time())
    save_store(d)
    return "ok", SUB_REWARD


@dp.callback_query(F.text.startswith("check_sub"))
async def cb_check_sub(call: types.CallbackQuery):
    """Кнопка «Проверить подписку» — проверяет и платит бонус."""
    status, reward = await _channel_sub_check(call.from_user.id)
    if status == "ok":
        text = f"🎉 Подписка засчитана! <b>+{reward} 🪙</b> начислено."
    elif status == "already":
        text = "Бонус за подписку уже получен 🙂"
    elif status == "no_channel":
        text = "Канал пока не настроен."
    else:
        text = (f"Сначала подпишись на канал: {CHANNEL}\n\n"
                f"Потом нажми «Проверить подписку» ещё раз — и получишь "
                f"<b>+{SUB_REWARD} 🪙</b>.")
    try:
        await call.answer(text[:200], show_alert=False)
    except Exception:
        pass
    try:
        await call.message.answer(text, reply_markup=kb())
    except Exception:
        pass


@dp.message(Command("subscribe"))
async def subscribe_cmd(message: types.Message):
    """Игрок жмёт кнопку — проверяем подписку на канал и платим разово."""
    status, reward = await _channel_sub_check(message.from_user.id)
    if status == "ok":
        await message.answer(f"🎉 Подписка засчитана! <b>+{reward} 🪙</b> начислено.",
                             reply_markup=kb())
        return
    if status == "already":
        await message.answer("Бонус за подписку уже получен 🙂"); return
    if status == "no_channel":
        await message.answer("Канал пока не настроен."); return
    await message.answer(f"Сначала подпишись на канал: {CHANNEL}\n\n"
                         f"Потом нажми кнопку ещё раз — и получишь "
                         f"<b>+{SUB_REWARD} 🪙</b>.")


@dp.message(Command("mira"))
async def mira_cmd(message: types.Message):
    await message.answer(
        f"{PROMO_TITLE}\n\n{PROMO_TEXT}\n\n"
        f"Твоя ссылка:\n{MIRA_URL}\n\n"
        f"Подключи Mira и возвращайся в игру — начислю <b>+{PROMO_REWARD} 🪙</b> "
        "(один раз).", reply_markup=kb())

@dp.message(Command("mira"))
async def mira_cmd2(message: types.Message):
    await message.answer(
        f"{PROMO_TITLE}\n\n{PROMO_TEXT}\n\n"
        f"Твоя ссылка:\n{MIRA_URL}\n\n"
        f"Подключи Mira и возвращайся в игру — начислю <b>+{PROMO_REWARD} 🪙</b> (один раз).\n\n"
        "Разобрать свою фразу: отправь её сюда сообщением — я подскажу, что в ней скрыто.",
        reply_markup=kb())

@dp.message(Command("stars"))
async def stars(message: types.Message):
    await message.answer("Пакеты монет (Telegram Stars):\n\n"
        "100 монет — 100 ⭐\n500 монет — 500 ⭐\n+1 жизнь — 200 ⭐\nБез рекламы — 350 ⭐\n\n"
        "Или просто жми «Купить монеты» внутри игры.", reply_markup=kb())

@dp.message(Command("help"))
async def help_(message: types.Message):
    await message.answer("/start — играть\n/buy — купить монеты\n/stars — цены\n"
                         "/referral — ссылка для друзей\n"
                         "/mira — ИИ-помощник Mira + бонус в игре",
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

# ---------------- Хранилище состояния ----------------
# Раньше каждый API-запрос писал весь JSON на диск: при наплыве аудитории
# это давало полную перезапись состояния на каждый запрос и риск потери
# данных при гонках между хендлерами.
# Теперь состояние живёт в памяти (один объект на процесс), а на диск
# пишется асинхронно с объединением записей: запросы быстрые, данные не теряются.
_STORE_CACHE = None
_STORE_DIRTY = False
_STORE_FLUSH_TASK = None
_STORE_FLUSH_DELAY = 0.25


def _store_dir():
    try:
        os.makedirs(os.path.dirname(STORE) or ".", exist_ok=True)
    except Exception:
        pass


def load_store():
    """Вернуть общее состояние. Объект один на весь процесс, изменения
    видны всем хендлерам сразу — гонок «прочитал-потерял-записал» нет."""
    global _STORE_CACHE
    if _STORE_CACHE is None:
        try:
            _store_dir()
            with open(STORE, encoding="utf-8") as f:
                _STORE_CACHE = json.load(f)
        except Exception:
            _STORE_CACHE = {}
        if not isinstance(_STORE_CACHE, dict):
            _STORE_CACHE = {}
    return _STORE_CACHE


def _flush_store_now():
    global _STORE_DIRTY
    if not _STORE_DIRTY:
        return
    d = _STORE_CACHE
    if d is None:
        return
    _STORE_DIRTY = False
    try:
        _store_dir()
        tmp = STORE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STORE)
    except Exception as e:
        _STORE_DIRTY = True
        logging.warning("store flush failed: %s", e)


async def _flush_soon():
    try:
        await asyncio.sleep(_STORE_FLUSH_DELAY)
        _flush_store_now()
    except Exception:
        pass


def save_store(d):
    """Отметить состояние изменённым и запланировать запись на диск."""
    global _STORE_CACHE, _STORE_DIRTY, _STORE_FLUSH_TASK
    _STORE_CACHE = d
    _STORE_DIRTY = True
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _STORE_FLUSH_TASK is None or _STORE_FLUSH_TASK.done():
        _STORE_FLUSH_TASK = loop.create_task(_flush_soon())


async def store_flusher():
    """Периодически дописываем на диск на случай потери задач."""
    while True:
        try:
            await asyncio.sleep(5)
            _flush_store_now()
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(5)

def grant(user_id, payload):
    d = load_store()
    u = str(user_id)
    acc = d.setdefault(u, {"coins": 0, "lives": 0, "noads": False, "charges": []})
    if payload == "test_1star": acc["coins"] += 1
    elif payload == "coins_100": acc["coins"] += 100
    elif payload == "coins_500": acc["coins"] += 500
    elif payload == "lives": acc["lives"] += 1
    elif payload == "noads": acc["noads"] = True
    elif payload == "contest_entry":
        c = contest_tickets(acc_of(d, user_id))
        c["tickets"] = int(c.get("tickets", 0)) + CONTEST_TICKETS_PAID
        c["paid_tickets"] = int(c.get("paid_tickets", 0)) + CONTEST_TICKETS_PAID
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
    """Публикует результат игрока в канале (если бот — админ) и платит 20 монет.

    Раньше бот обещал 20 монет, но не начислял их — теперь платит, но с
    защитой от накрутки: бонус один раз в сутки на аккаунт.
    """
    if not CHAT_ID:
        return
    name = user.first_name or "Игрок"
    safe = html_escape(str(result_text)[:40])
    text = (f"\U0001F3AF <b>{html_escape(name)}</b> — результат: <b>{safe}</b>\n"
            f"Сыграть так же: {WEBAPP_URL}")

    # Бонус за шеринг: один раз в сутки, чтобы нельзя было фармить кнопкой.
    bonus = 0
    try:
        d = load_store()
        acc = acc_of(d, user.id)
        today = datetime.date.today().isoformat()
        if acc.get("share_bonus_day") != today:
            acc["share_bonus_day"] = today
            acc["coins"] += 20
            bonus = 20
            save_store(d)
    except Exception as e:
        logging.warning("share bonus failed: %s", e)

    try:
        await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="HTML",
                               disable_web_page_preview=True)
    except Exception as e:
        logging.warning("publish_score failed: %s", e)
    return bonus


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

def track_source(parts, body_source=""):
    """Источник перехода: метка рекламного канала.

    Порядок: start_param из initData -> source из тела запроса -> pending_src (сохранён
    при входе в бота по /start ch_xxx). Без этой цепочки метка из рекламы терялась
    и вся статистика показывала "direct".
    """
    src = ""
    try:
        raw = str(parts.get("start_param") or "")
    except Exception:
        raw = ""
    if not raw:
        raw = str(body_source or "")
    if raw:
        raw = raw.strip()
        if raw.startswith("ch_"):
            src = raw[:40]
        elif raw:
            src = raw[:24]
    return src or "direct"


def track_visit_source(d, uid, src):
    """Пишем источник визита: сколько игроков пришло с каждой рекламной площадки."""
    try:
        acc = acc_of(d, uid)
        seen = acc.setdefault("src_seen", [])
        if src and src not in seen and len(seen) < 12:
            seen.append(src)
        acc["src"] = src or acc.get("src", "")
        p = d.setdefault("_promo", {"clicks": 0, "claims": 0, "by_source": {}})
        bs = p.setdefault("by_source", {})
        bs[src] = int(bs.get(src, 0)) + 1
        p["visits_by_source"] = p.get("visits_by_source", {})
        p["visits_by_source"][src] = int(p["visits_by_source"].get(src, 0)) + 1
    except Exception:
        pass

def acc_of(d, uid):
    acc = d.setdefault(str(uid), {"coins": 0, "lives": 5, "noads": False,
                                  "streak": 0, "best": 0, "level": 1, "charges": []})
    acc.setdefault("coins", 0); acc.setdefault("lives", 5); acc.setdefault("noads", False)
    acc.setdefault("streak", 0); acc.setdefault("best", 0); acc.setdefault("level", 1)
    acc.setdefault("charges", [])
    acc.setdefault("invited", 0)
    acc.setdefault("lives_ts", 0)
    acc.setdefault("m3", {"lvl": 1, "score": 0, "moves": 20, "best": 0,
                          "plays": 0, "garland": 0, "combo": 0,
                          "maxcombo": 0, "total": 0, "cleared": 0})
    acc.setdefault("ach", [])
    acc.setdefault("days", {})
    acc.setdefault("day_streak", 0)
    acc.setdefault("last_day", "")
    acc.setdefault("quiz_streak", 0)
    acc.setdefault("cards", {})        # rarity -> {card_id: count}
    acc.setdefault("cards_opened", 0)
    acc.setdefault("tourney", {"score": 0, "week": ""})
    acc.setdefault("bp", {"coins": 0, "level": 1, "claimed": []})
    acc.setdefault("bp_skin", "")
    return acc


# ---------------- Новогодний банк вопросов ----------------
# Тот же формат, что у QUIZ_BANK: q — текст, o — 4 варианта, c — индекс
# правильного, h — подсказка. Добавлен в конец, чтобы индексы старых
# вопросов (уже отвеченных игроками) не сдвинулись.
NY_QUESTIONS = [
 {"q": "31 декабря, 23:50. Он пишет «ты где?» Это…", "o": ["Случайно — праздник", "Проверяет, будешь ли ты с ним", "Он ищет, кого позвать в полночь", "Ему нечего делать"], "c": 1, "h": "Новый год не отменяет подтексты."},
 {"q": "«Не буду ждать 12-го, приду с подарками» — в 23:55", "o": ["Точно придёт", "Точно опоздает", "Придёт, но без подарков", "Случайно врёт"], "c": 2, "h": "Слово «но» — вот где подтекст."},
 {"q": "Фотка ёлки с подписью «одна без тебя»", "o": ["Просто фото", "Намёк на тебя", "Жалуется", "Хвастается"], "c": 1, "h": "«Одна» — ключевое слово."},
 {"q": "«Соскучился» в первый час нового года", "o": ["Правда скучал", "Пьяный и не контролирует", "Просто традиция", "Сарказм"], "c": 0, "h": "Новый год делает людей искренними. Почти."},
 {"q": "Отправил открытку «С Новым годом!» и не написал ни слова", "o": ["Поздравил и всё", "Отправил всем одинаково", "Ждёт ответа именно от тебя", "Забыл текст"], "c": 2, "h": "Открытка без слов — это вопрос."},
 {"q": "Голосовое в 00:03 длиной 40 секунд с смехом", "o": ["Веселился", "Пил", "Записывал приветствие", "Хочет поговорить с тобой"], "c": 3, "h": "Тринадцать минут до Нового года он выбрал звонок тебе."},
 {"q": "«Праздник без тебя — не праздник». В ответ — эмодзи 🎄", "o": ["Искренне", "Сарказм", "Отмахивается", "Не понял"], "c": 1, "h": "Эмодзи после искреннего текста = смягчение."},
 {"q": "Он забронировал ресторан на 31-е. Без твоего имени", "o": ["Для себя и друзей", "Сюрприз для тебя", "Планы с другими", "Ошибся"], "c": 1, "h": "Бронируют и забывают переименовать. Редко."},
 {"q": "«Я в этом году хочу, чтобы ты…» — и замолчал", "o": ["Ушёл", "Случайно", "Ждёт, что ты договоришь", "Проверяет, спросишь ли"], "c": 3, "h": "Пауза в личном сообщении — всегда приглашение говорить."},
 {"q": "Прислал 10 гирлянд из эмодзи. Без текста", "o": ["Просто красиво", "Напоминание про ёлку в вашей переписке", "Случайно", "Заглушка"], "c": 1, "h": "Десять гирлянд — это не «просто красиво»."},
 {"q": "«У нас первый Новый год вместе?» — в 23:58", "o": ["Романтика", "Напоминание о расстании", "Проверка на адекватность", "Он пьян"], "c": 2, "h": "Вопрос про «у нас» в предновогоднюю ночь — это тест."},
 {"q": "Позвонил в 23:59 и не сказал ни слова 5 секунд", "o": ["Забыл, что говорить", "Проверял, спишь ли ты", "Случайно нажал", "Хотел услышать твой голос"], "c": 3, "h": "Молчание в трубке — тоже подтекст."},
 {"q": "«Если будешь одна — я приду»", "o": ["Романтика", "Он уверен, что ты будешь с кем-то", "Просто вежливость", "Намёк на ревность"], "c": 1, "h": "«Если» вместо «когда»."},
 {"q": "В новогоднюю ночь прислал только «🥳»", "o": ["Праздновал", "Тоска один", "Ждал разговора с тобой", "Случайно"], "c": 2, "h": "Минимум символов в максимум внимания."},
 {"q": "«Каждый год будем вместе», сказанное под бой курантов", "o": ["Правда", "Планы на пьяную голову", "Просто пожелание", "Обещание"], "c": 2, "h": "Под бой курантов обещают все."},
 {"q": "Отправил рецепт оливье в 2 часа ночи", "o": ["Делится рецептом", "Не спит и думает о тебе", "Хвастается готовкой", "Случайно"], "c": 1, "h": "Рецепт в 2 ночи — это «мне грустно и одиноко»."},
 {"q": "«Все вокруг смотрят сериал, я один»", "o": ["Жалуется", "Намёк, что позовёт тебя", "Просто делится", "Ищет компанию"], "c": 1, "h": "«Один» в новогоднюю ночь — приглашение."},
 {"q": "«Если не спишь — напиши»", "o": ["Предложение", "Проверка, не спишь ли ты", "Оставлен на всякий случай", "Разговор"], "c": 0, "h": "Это не «на всякий случай»."},
 {"q": "Прислал фото себя в шапке с ёлкой", "o": ["Показать настроение", "Требует внимания", "Случайно", "Хвастается"], "c": 1, "h": "Фото себя без подписи = «посмотри на меня»."},
 {"q": "«В следующем году я хочу, чтобы всё было иначе»", "o": ["Общие планы", "Он имеет в виду вас", "Про настроение", "Ничего конкретного"], "c": 1, "h": "«Иначе» — значит «не как сейчас»."},
 {"q": "Тишина в 00:00. Первое сообщение в новом году — от него", "o": ["Случайно", "Он специально ждал полуночи", "Поздравление с опозданием", "Автоответ"], "c": 1, "h": "Кто-то ждал полуночи."},
 {"q": "«У меня подарок, но я не знаю, дарить ли»", "o": ["Сомневается в подарке", "Сомневается в тебе", "Интригует", "Забыл"], "c": 2, "h": "Интрига — тоже подарок."},
 {"q": "Прислал фото мандарина и «вкусного»", "o": ["Просто ест", "Делится", "Скучает", "Просит прийти"], "c": 2, "h": "Фото еды в 3 часа ночи — это одиночество."},
 {"q": "«С 1 января я другие планы»", "o": ["Отпуск", "Расстание", "Приглашение на новогоднее свидание", "Работа"], "c": 2, "h": "Планы на 1 января без деталей."},
 {"q": "Голосовое: «Слушай, я тут подумал…» и обрыв", "o": ["Он запился", "Забыл мысль", "Намеренно интригует", "Разговор прервали"], "c": 2, "h": "Оборванная фраза в 4 вариантах всегда про «скажи первым»."},
 {"q": "«Лучший подарок — это ты», 00:01", "o": ["Комплимент", "Поздравление по отправке", "Он пьян", "Намёк на секс"], "c": 0, "h": "В 00:01 люди говорят правду."},
 {"q": "«Не обещаю ничего, но план есть»", "o": ["Интрига", "Отказ", "Лень", "Нет плана"], "c": 0, "h": "Плана нет — но он бы нашёл, если бы захотел."},
]


# ---------------- Ретеншен: ежедневная серия ----------------
# Сервер помнит, в какие дни игрок заходил, считает серию подряд и платит
# за каждый день. Это сердце петли удержания: прогресс видно, за него дают.
DAY_REWARD = 20          # монет за простое заличие в игре в день
DAY_BONUS_EVERY = 3     # каждые 3 дня — бонус
DAY_BONUS = 60
DAY_STREAK_BONUS = 150  # бонус на 7-й день серии
DAY_TZ_OFFSET = 3       # сервер считает дни по Москве (UTC+3)

def today_key(offset_h=DAY_TZ_OFFSET):
    """Ключ дня YYYY-MM-DD по московскому времени."""
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + offset_h * 3600))

def day_shift(key, days):
    ts = time.mktime(time.strptime(key, "%Y-%m-%d")) + days * 86400
    return time.strftime("%Y-%m-%d", time.gmtime(ts))

def retention_view(acc):
    """Данные ретеншена для клиента: серия, календарь, всего дней."""
    days = acc.setdefault("days", {})
    streak = int(acc.get("day_streak", 0))
    last = acc.get("last_day", "")
    cal = []
    t = today_key()
    for i in range(6, -1, -1):
        k = day_shift(t, -i)
        cal.append({"d": k, "on": 1 if k in days else 0})
    return {"day_streak": streak, "days_total": len(days),
            "last_day": last, "calendar": cal,
            "day_reward": DAY_REWARD}

def register_visit(acc):
    """Регистрирует визит сегодняшним днём. Возвращает (new, reward, streak)."""
    days = acc.setdefault("days", {})
    t = today_key()
    if t in days:
        return False, 0, int(acc.get("day_streak", 0))
    days[t] = 1
    if len(days) > 120:
        for k in sorted(days)[:40]:
            days.pop(k, None)
    last = acc.get("last_day", "")
    streak = int(acc.get("day_streak", 0))
    penalty = 0
    if last and last == day_shift(t, -1):
        streak += 1
    else:
        # Фишка Major: пропуск дня обнуляет серию и отнимает часть монет.
        if last and streak > 1:
            lost = int(acc.get("coins", 0) or 0) * MISS_PENALTY_PCT // 100
            if lost > 0:
                acc["coins"] = int(acc.get("coins", 0) or 0) - lost
                penalty = lost
        streak = 1
    acc["day_streak"] = streak
    acc["last_day"] = t
    reward = DAY_REWARD
    if streak % DAY_BONUS_EVERY == 0:
        reward += DAY_BONUS
    if streak % 7 == 0:
        reward += DAY_STREAK_BONUS
    acc["coins"] += reward
    if streak >= 7:
        unlock(acc, "streak7")
    return True, reward, streak, penalty

LIFE_REGEN_SEC = 1800
ANSWER_COOLDOWN = 3600  # один и тот же вопрос — раз в час  # одна жизнь каждые 30 минут

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

# Новогодние вопросы добавляются ПОСЛЕ определения QUIZ_BANK,
# иначе NameError при импорте и деплой падает.
QUIZ_BANK = QUIZ_BANK + NY_QUESTIONS



# ---------------- Mini App API: квиз (сервер проверяет ответы) ----------------

_daily_state = {}

def ny_offset():
    """Индекс, с которого новогодние вопросы лежат в общем QUIZ_BANK.

    Новогодние вопросы добавлены в конец банка, поэтому их индексы в
    QUIZ_BANK глобальные — ответы в /api/answer остаются валидными.
    """
    return len(QUIZ_BANK) - len(NY_QUESTIONS)


def daily_pick(uid):
    """Один и тот же вопрос на день для ВСЕХ игроков.

    Индекс зависит только от даты: без uid у каждого игрока был бы свой
    «челлендж дня», хотя обещано одно и то же задание.
    В декабре и январе — новогодний банк, в остальное время — общий.
    """
    d = None
    try:
        d = os.environ.get("DAILY_QUESTION_DATE")
    except Exception:
        d = None
    if not d:
        import datetime
        d = datetime.date.today().isoformat()
    key = f"daily:{d}"
    if key not in _daily_state:
        h = int(hashlib.sha256(d.encode()).hexdigest(), 16)
        try:
            mm = int(d[5:7])
        except Exception:
            mm = 0
        off = ny_offset()
        if mm in (12, 1) and NY_QUESTIONS:
            idx = off + (h % len(NY_QUESTIONS))
        else:
            idx = h % (off if off else len(QUIZ_BANK))
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
    daily = (mode == "daily")
    idx = daily_pick(uid) if daily else random.randrange(len(QUIZ_BANK))
    q = QUIZ_BANK[idx]
    d = load_store(); acc = acc_of(d, uid)
    chal = acc.setdefault("daily", {})
    today = today_key()
    if chal.get("date") != today:
        chal["date"] = today
        chal["done"] = 0
    chal["streak"] = int(chal.get("streak", 0))
    save_store(d)
    return web.json_response({"id": idx, "q": q["q"], "o": q["o"],
                              "daily": daily, "daily_done": int(chal.get("done", 0)),
                              "daily_streak": chal["streak"]})

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
    if chosen < 0 or chosen >= len(q["o"]):
        return web.json_response({"error": "bad choice"}, status=400)
    good = chosen == q["c"]

    d = load_store(); acc = acc_of(d, uid)

    # Защита от повторной отправки ответа: один и тот же вопрос можно
    # засчитать только раз в час, иначе монеты фармятся перезапросом.
    answered = acc.setdefault("answered", {})
    last = answered.get(str(idx))
    now_ts = int(time.time())
    if last and (now_ts - int(last)) < ANSWER_COOLDOWN:
        # Повторный ответ на тот же вопрос — это НЕ поломка. Клиент раньше
        # показывал «Ошибка проверки ответа», потому что любой error читался
        # как сбой. Теперь отдаём явный флаг и правильный ответ.
        return web.json_response({"ok": True, "already_answered": True,
                                  "correct": chosen == q["c"], "answer": q["o"][q["c"]],
                                  "hint": q["h"], **wallet_view(acc)})
    answered[str(idx)] = now_ts
    if len(answered) > 200:
        for k in sorted(answered, key=lambda x: answered[x])[:100]:
            answered.pop(k, None)

    # Челлендж дня засчитывается один раз в сутки
    chal = acc.setdefault("daily", {})
    today = today_key()
    if chal.get("date") != today:
        chal["date"] = today
        chal["done"] = 0
        chal["streak"] = int(chal.get("streak", 0)) + 1 if chal.get("last") == day_shift(today, -1) else 1
    chal["last"] = today

    if good:
        acc["coins"] += 10
        acc["streak"] += 1
        tourney_add(d, acc, 10)
        contest_add_points(d, acc, "quiz", 10); bp_add_points(d, acc, "quiz")
        acc["quiz_streak"] = int(acc.get("quiz_streak", 0)) + 1
        if not chal.get("done"):
            chal["done"] = 1
            acc["coins"] += 30
            if int(chal.get("streak", 0)) >= 3:
                acc["coins"] += 50
        if acc["quiz_streak"] >= 10:
            unlock(acc, "quiz10")
            acc["coins"] += 50
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
        acc["quiz_streak"] = 0
        acc["lives"] = max(1, acc["lives"] - 1)
    save_store(d)
    return web.json_response({"ok": True, "correct": good, "answer": q["o"][q["c"]],
                              "hint": q["h"], **bp_state(acc), **wallet_view(acc)})


# ---------------- Mini App API: прогресс match-3 + профиль ----------------
# Счётчик уровня и очки считает СЕРВЕР: клиент присылает только факт игры,
# а сервер сам решает, засчитать ли уровень и сколько монет дать.
M3_BASE_GOAL = 400
M3_GOAL_GROWTH = 1.32
M3_MAX_MOVES = 20
M3_GARLAND_MAX = 10

def m3_goal(level):
    try:
        lv = max(1, int(level))
    except Exception:
        lv = 1
    return int(round(M3_BASE_GOAL * (M3_GOAL_GROWTH ** (lv - 1))))

ACHIEVEMENTS = [
    ("first_light",  "🎄 Первая гирлянда",  "Зажги 10 ламп за один заход"),
    ("combo5",       "⚡ Комбо ×5",        "Собери цепочку из 5 совпадений"),
    ("combo8",       "🔥 Комбо ×8",        "Собери цепочку из 8 совпадений"),
    ("level5",       "⭐ Уровень 5",       "Пройди 5 уровней"),
    ("level10",      "👑 Уровень 10",      "Пройди 10 уровней"),
    ("m3_10k",       "💎 10 000 очков",    "Набери 10 000 очков в match-3"),
    ("m3_50k",       "🏆 50 000 очков",    "Набери 50 000 очков в match-3"),
    ("all_lamps",    "🌟 Все лампы",       "Собери гирлянду 10 раз"),
    ("quiz10",       "🎯 Квиз ×10",        "10 верных ответов подряд"),
    ("streak7",      "🔥 Неделя",          "7 дней подряд заходи в игру"),
]

def m3_view(acc):
    m = acc.get("m3") or {}
    return {"m3": {"lvl": m.get("lvl", 1), "score": m.get("score", 0),
                   "moves": m.get("moves", M3_MAX_MOVES), "best": m.get("best", 0),
                   "plays": m.get("plays", 0), "garland": m.get("garland", 0),
                   "lamps": m.get("lamps", 0),
                   "combo": m.get("combo", 0), "maxcombo": m.get("maxcombo", 0),
                   "total": m.get("total", 0), "cleared": m.get("cleared", 0)},
            "goal": m3_goal(m.get("lvl", 1)),
            "ach": acc.get("ach", []),
            "unlocked": sorted(ACHIEVEMENTS, key=lambda a: a[0]),
            **retention_view(acc),
            "daily": {"done": int((acc.get("daily") or {}).get("done", 0)),
                      "streak": int((acc.get("daily") or {}).get("streak", 0)),
                      "date": (acc.get("daily") or {}).get("date", "")}}

def unlock(acc, key):
    """Открывает достижение один раз и возвращает True, если оно новое."""
    got = acc.setdefault("ach", [])
    if key in got:
        return False
    got.append(key)
    return True

async def api_m3(request):
    """Сохранение прогресса match-3. Сервер проверяет всё, что можно проверить."""
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

    action = body.get("action", "save")
    d = load_store(); acc = acc_of(d, uid)
    m = acc.setdefault("m3", {"lvl": 1, "score": 0, "moves": M3_MAX_MOVES, "best": 0,
                              "plays": 0, "garland": 0, "combo": 0, "maxcombo": 0,
                              "total": 0, "cleared": 0})

    def num(key, default=0, lo=0, hi=10 ** 9):
        try:
            v = int(body.get(key, default))
        except Exception:
            return default
        return max(lo, min(hi, v))

    if action == "save":
        level = num("level", m.get("lvl", 1), 1, 9999)
        score = num("score", 0, 0, 10 ** 9)
        moves = num("moves", M3_MAX_MOVES, 0, M3_MAX_MOVES)
        garland = num("garland", 0, 0, 10 ** 7)      # всего гирлянд за всё время
        lamps = num("lamps", 0, 0, M3_GARLAND_MAX)    # ламп в текущей гирлянде
        combo = num("combo", 0, 0, 99)
        maxcombo = num("maxcombo", 0, 0, 99)
        plays = num("plays", 0, 0, 10 ** 7)

        # Уровень нельзя пропустить: можно играть только на текущем или уже открытом.
        cur = int(m.get("lvl", 1))
        if level > cur + 1:
            level = cur + 1
        # Очки не могут превышать разумный предел для одного уровня.
        cap = m3_goal(level) * 4
        if score > cap:
            score = cap

        m["score"] = score
        m["moves"] = moves
        m["garland"] = max(int(m.get("garland", 0)), garland)
        m["lamps"] = max(int(m.get("lamps", 0)), lamps)
        m["maxcombo"] = max(int(m.get("maxcombo", 0)), maxcombo)
        m["plays"] = max(int(m.get("plays", 0)), plays)

        # Уровень засчитывается сервером: только если хватает очков.
        goal = m3_goal(cur)
        if score >= goal and cur == level:
            stars = 3 if moves >= 10 else (2 if moves >= 5 else 1)
            acc["coins"] += 30 + stars * 20
            m["cleared"] = int(m.get("cleared", 0)) + 1
            m["total"] = int(m.get("total", 0)) + score
            m["best"] = max(int(m.get("best", 0)), score)
            tourney_add(d, acc, max(0, score) // 10)
            contest_add_points(d, acc, "m3", 5); bp_add_points(d, acc, "m3")
            m["lvl"] = cur + 1
            m["score"] = 0
            m["moves"] = M3_MAX_MOVES
            unlock(acc, "level5") if m["lvl"] >= 5 else None
            unlock(acc, "level10") if m["lvl"] >= 10 else None
            if m["total"] >= 10000: unlock(acc, "m3_10k")
            if m["total"] >= 50000: unlock(acc, "m3_50k")
            if int(m.get("garland", 0)) >= 10: unlock(acc, "all_lamps")
            save_store(d)
            return web.json_response({"ok": True, "levelup": True, "stars": stars,
                                      "reward": 30 + stars * 20, **wallet_view(acc),
                                      **m3_view(acc)})

        if combo >= 5: unlock(acc, "combo5")
        if combo >= 8: unlock(acc, "combo8")
        if lamps >= M3_GARLAND_MAX: unlock(acc, "first_light")
        if int(m.get("garland", 0)) >= 10: unlock(acc, "all_lamps")
        if int(m.get("best", 0)) == 0 and score > 0:
            m["best"] = score
        save_store(d)
        return web.json_response({"ok": True, "levelup": False, **wallet_view(acc), **m3_view(acc)})

    if action == "profile":
        save_store(d)
        return web.json_response({"ok": True, **wallet_view(acc), **m3_view(acc)})

    save_store(d)
    return web.json_response({"ok": False, "error": "unknown action", **wallet_view(acc)})


async def api_referral(request):
    """Статистика рефералки: сколько приглашено и сколько до бонуса."""
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
    d = load_store(); acc = acc_of(d, uid)
    invited = int(acc.get("invited", 0))
    left = 3 - (invited % 3) if invited % 3 else 3
    link = f"https://t.me/{BOT_USERNAME}?start=ref{uid}"
    save_store(d)
    return web.json_response({"ok": True, "invited": invited, "left": left,
                              "link": link, "bonus": 50, "new_bonus": 20,
                              **wallet_view(acc)})


async def api_promo(request):
    """Статистика партнёрского блока Mira + состояние «забрал ли игрок бонус»."""
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
    d = load_store(); acc = acc_of(d, uid)
    promo = acc.setdefault("promo", {})
    d.setdefault("_promo", {"clicks": 0, "claims": 0})
    claimed = bool(promo.get("claimed"))
    return web.json_response({
        "ok": True, "title": PROMO_TITLE, "text": PROMO_TEXT, "url": MIRA_URL,
        "reward": PROMO_REWARD, "claimed": claimed,
        "clicks": int(d["_promo"].get("clicks", 0)),
        "claims": int(d["_promo"].get("claims", 0)),
    })


async def api_promo_click(request):
    """Игрок нажал «подключить Mira» — считаем клик для статистики."""
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
    d = load_store(); acc = acc_of(d, uid)
    d.setdefault("_promo", {"clicks": 0, "claims": 0})
    d["_promo"]["clicks"] = int(d["_promo"].get("clicks", 0)) + 1
    try:
        src = str(body.get("source", "profile"))[:24]
    except Exception:
        src = "profile"
    bs = d["_promo"].setdefault("by_source", {})
    bs[src] = int(bs.get(src, 0)) + 1
    save_store(d)
    return web.json_response({"ok": True})


async def api_promo_claim(request):
    """Игрок вернулся из Mira — выдаём +50 🪙. Один раз за аккаунт."""
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
    d = load_store(); acc = acc_of(d, uid)
    promo = acc.setdefault("promo", {})
    if promo.get("claimed"):
        return web.json_response({"ok": False, "already": True,
                                 "coins": acc.get("coins", 0)})
    acc["coins"] = int(acc.get("coins", 0)) + PROMO_REWARD
    promo["claimed"] = True
    promo["ts"] = int(time.time())
    d.setdefault("_promo", {"clicks": 0, "claims": 0})
    d["_promo"]["claims"] = int(d["_promo"].get("claims", 0)) + 1
    save_store(d)
    return web.json_response({"ok": True, "reward": PROMO_REWARD,
                              "coins": acc.get("coins", 0),
                              "lives": acc.get("lives", 5)})


async def api_mira_hint(request):
    """Игрок просит подсказку/разбор у Mira — отдаём deep-link с подставленным текстом."""
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
        q = str(body.get("q", ""))[:200]
    except Exception:
        q = ""
    d = load_store(); acc = acc_of(d, uid)
    promo = acc.setdefault("promo", {})
    d.setdefault("_promo", {"clicks": 0, "claims": 0, "by_source": {}})
    src = str(body.get("source", "quiz"))[:24]
    bs = d["_promo"].setdefault("by_source", {})
    bs[src] = int(bs.get(src, 0)) + 1
    save_store(d)
    if q:
        prompt = (f"Разбери подтекст этой фразы и объясни, что человек на самом деле имел в виду. "
                  f"Дай 4 варианта ответа, один верный. Фраза: «{q}»")
    else:
        prompt = ("Придумай 5 фраз с подтекстом для игры-квиза про скрытый смысл "
                  "(что человек имел в виду на самом деле). Без пояснений, только фразы.")
    return web.json_response({"ok": True, "url": mira_link(prompt),
                              "reward": PROMO_REWARD, "claimed": bool(promo.get("claimed"))})


async def api_ads_stats(request):
    """Сколько игроков пришло с каждой рекламной площадки (start_param ch_*)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    parts = validate_init_data(body.get("init_data", ""))
    uid = user_from_init(parts) if parts else None
    d = load_store()
    p = d.get("_promo", {}) or {}
    visits = p.get("visits_by_source", {}) or {}
    players = {}
    for u, acc in d.items():
        if not isinstance(acc, dict) or u.startswith("_"):
            continue
        s = str(acc.get("src") or "")
        if s:
            players[s] = players.get(s, 0) + 1
    return web.json_response({"ok": True, "visits_by_source": visits,
                              "players_by_source": players,
                              "total_players": len(players),
                              "uid": str(uid) if uid else None})

async def api_promo_stats(request):
    """Статистика по рекламным местам — какое место приносит больше кликов."""
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
    d = load_store()
    p = d.get("_promo", {}) or {}
    return web.json_response({"ok": True, "clicks": int(p.get("clicks", 0)),
                              "claims": int(p.get("claims", 0)),
                              "by_source": p.get("by_source", {}) or {},
                              "visits_by_source": p.get("visits_by_source", {}) or {}})


def _lb_rows(d, limit=20):
    """Собираем топ игроков по сумме очков: квиз-серия + уровень три-в-ряд + уровень."""
    rows = []
    for uid, acc in d.items():
        if not isinstance(acc, dict) or uid.startswith("_"):
            continue
        try:
            qs = int(acc.get("quiz_streak", 0) or 0)
            best = int(acc.get("best", 0) or 0)
            lvl = int(acc.get("level", 1) or 1)
            m3 = acc.get("m3", {}) or {}
            m3lvl = int(m3.get("lvl", 1) or 1)
            m3best = int(m3.get("best", 0) or 0)
            days = len(acc.get("days", {}) or {})
        except Exception:
            continue
        score = qs * 100 + best + lvl * 20 + m3lvl * 50 + m3best // 10 + days * 30
        if score <= 0:
            continue
        name = acc.get("name") or ""
        if not name:
            name = f"Игрок {str(uid)[-4:]}"
        rows.append({"uid": str(uid), "name": str(name)[:28],
                     "score": score, "streak": qs, "level": lvl,
                     "m3lvl": m3lvl, "days": days})
    rows.sort(key=lambda r: -r["score"])
    return rows[:limit]


async def api_channel_sub(request):
    """Игрок подписался на канал — проверяем через get_chat_member и платим разово."""
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
    if not CHAT_ID:
        return web.json_response({"ok": True, "subscribed": False, "reward": 0,
                                  "already": True})
    # проверяем подписку
    member = None
    try:
        member = await bot.get_chat_member(CHAT_ID, uid)
    except Exception as e:
        # Telegram отдаёт PARTICIPANT_ID_INVALID, если игрок просто не подписан
        # на канал. Это не ошибка проверки, а честный «не подписан».
        logging.info("get_chat_member: %s", e)
        return web.json_response({"ok": True, "subscribed": False, "reward": 0,
                                  "check_failed": False})
    st = getattr(member, "status", "")
    subscribed = st in ("member", "administrator", "creator", "restricted")
    if not subscribed:
        return web.json_response({"ok": True, "subscribed": False, "reward": 0})
    d = load_store(); acc = acc_of(d, uid)
    sub = acc.setdefault("channel_sub", {"claimed": False})
    if sub.get("claimed"):
        return web.json_response({"ok": True, "subscribed": True, "reward": 0,
                                  "already": True, **wallet_view(acc)})
    acc["coins"] = int(acc.get("coins", 0) or 0) + SUB_REWARD
    sub["claimed"] = True
    sub["ts"] = int(time.time())
    save_store(d)
    return web.json_response({"ok": True, "subscribed": True, "reward": SUB_REWARD,
                              **wallet_view(acc)})


async def api_leaderboard(request):
    """Топ игроков — конкуренция и повод вернуться в игру."""
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
    d = load_store()
    rows = _lb_rows(d)
    mine = None
    for i, r in enumerate(rows):
        if r["uid"] == str(uid):
            mine = {**r, "place": i + 1}
            break
    if mine is None:
        acc = acc_of(d, uid)
        mine = {"uid": str(uid), "name": "Ты",
                "score": int(acc.get("quiz_streak", 0) or 0) * 100,
                "streak": int(acc.get("quiz_streak", 0) or 0),
                "level": int(acc.get("level", 1) or 1),
                "m3lvl": int((acc.get("m3", {}) or {}).get("lvl", 1) or 1),
                "days": len(acc.get("days", {}) or {}), "place": None}
    return web.json_response({"ok": True, "top": rows, "me": mine,
                              "total": max(1, len(rows))})


async def api_share_score(request):
    """Игрок отправляет свой результат — бот публикует пост в канал (с пометкой «реклама»/«игра»)."""
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
    d = load_store(); acc = acc_of(d, uid)
    try:
        streak = int(acc.get("quiz_streak", 0) or 0)
        days = int(acc.get("day_streak", 0) or 0)
    except Exception:
        streak = days = 0
    contest_add_points(d, acc, "share", 15); bp_add_points(d, acc, "share")
    d.setdefault("_shares", [])
    shares = d["_shares"]
    shares.append({"uid": str(uid), "streak": streak, "days": days,
                    "ts": int(time.time())})
    del shares[:-200]
    save_store(d)
    txt = (f"🔥 <b>Рекорд в ПОДТЕКСТЕ</b>\n\n"
           f"Серия в квизе: <b>{streak}</b> подряд · заходов: <b>{days}</b> дней\n\n"
           f"А сколько подтекстов разгадаешь ты?\n"
           f"Играть: <a href=\"https://t.me/{BOT_USERNAME}\">@podtekst_game_bot</a>\n\n"
           f"❓ Сомневаешься в трактовке — кидай фразу в бота, разберём.")
    try:
        await bot.send_message(CHAT_ID, txt, parse_mode="HTML",
                               disable_web_page_preview=True)
        return web.json_response({"ok": True, "posted": True})
    except Exception as e:
        logging.warning("share post failed: %s", e)
        return web.json_response({"ok": True, "posted": False})


async def api_visit(request):
    """Регистрация визита: сервер считает серию дней и платит за неё."""
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
    body_source = ""
    try:
        body_source = str(body.get("source") or "")
    except Exception:
        body_source = ""
    d = load_store(); acc = acc_of(d, uid)
    try:
        u = json.loads(parts.get("user", "{}"))
        fn = str(u.get("first_name") or "")[:28]
        if fn and not acc.get("name"):
            acc["name"] = fn
    except Exception:
        pass
    regen_lives(acc)
    try:
        src = track_source(parts, body_source)
        if not src or src == "direct":
            src = str(acc.get("pending_src") or "") or src
        track_visit_source(d, uid, src)
    except Exception:
        pass
    is_new, reward, streak, penalty = register_visit(acc)
    if is_new:
        contest_add_points(d, acc, "day", 20); bp_add_points(d, acc, "day")
    save_store(d)
    return web.json_response({"ok": True, "new_day": is_new, "reward": reward,
                              "streak": streak, "penalty": penalty,
                              **wallet_view(acc),
                              **retention_view(acc), **m3_view(acc)})



# ---------------- Коллекционные карточки ----------------
# Фишка удержания: коллекционирование + редкость. Карточки разблокируются
# за игру, их можно открыть за монеты, а редкие — выпадать случайно.

CARD_RARITY = [
    ("common",  "Обычная",     "⚪", 62),
    ("rare",    "Редкая",      "🔵", 26),
    ("epic",    "Эпическая",   "🟣",  9),
    ("legend",  "Легендарная", "🟡",  3),
]
RARITY_WEIGHT = {k: w for k, _n, _i, w in CARD_RARITY}
RARITY_INFO = {k: {"name": n, "icon": i} for k, n, i, _w in CARD_RARITY}

CARD_BANK = [
    ("ok_2341",   "common", "Он написал «ок» в 23:41",              "Там, где «всё хорошо» не пишут в такое время"),
    ("one_word",  "common", "Ответ в одно слово",                   "«Да» — это ответ и отказ одновременно"),
    ("typo",      "common", "Опечатка в «не пропадай»",             "Он печатал на ходу и торопился к тебе"),
    ("emoji_only","common", "Сообщение из трёх эмодзи",              "Текста нет — значит, слова не нужны"),
    ("read_4h",   "common", "Ответ через 4 часа",                   "Онлайн, но не в приоритете"),
    ("how_are_you","common", "«Как ты?» — «нормально»",             "Утром это правда. В час ночи — нет"),
    ("sorry_later","common", "«Извини, что поздно»",                "Он не планировал писать сегодня"),
    ("voice_long","common", "Голосовое на 4 минуты",                "Ответ голосовым = диалог, а не спор"),
    ("no_answer", "common", "Прочитал и не ответил",                "Ответит позже. Или не ответит"),
    ("meme",      "common", "Мем без подписи",                      "Подпись была бы важнее мема"),
    ("laugh_only","common", "Только «хаха» в ответ",                "Смеялся над чем-то. Не над тобой"),
    ("dont_know", "common", "«Не знаю, что ответить»",              "Вопрос был неудобный"),
    ("morning_tx", "common", "Сообщение в 8:03 без «доброе утро»",   "Утро начинается с него"),
    ("photo_self","common", "Фото себя без подписи",                "«Посмотри на меня»"),
    ("voice_note","common", "Голосовое на 12 секунд",               "Думал, что напишет, но не стал печатать"),
    ("see_later", "common", "«Посмотри потом»",                      "Потом — это никогда"),
    ("double_ok", "common", "Два «ок» подряд",                      "Первое — неловкость, второе — ответ"),
    ("sorry_busy","common", "«Я занят» без причины",                "Причина неудобная"),
    ("music",     "common", "Песня без комментария",                "Песня = сообщение"),
    ("weather",   "common", "«Тут дождь»",                          "Погода вместо «мне скучно»"),
    ("aaa_rare",  "rare",   "«Позвони, если что»",                  "Фраза-отказ и фраза-приглашение одновременно"),
    ("long_gap",  "rare",   "Час тишины, потом «как ты?»",           "Тайминг всё говорит"),
    ("hedge",     "rare",   "«Может, лучше без меня?»",              "Вопрос «может» — это просьба"),
    ("half_yes",  "rare",   "«Ну да, наверное»",                     "То есть нет"),
    ("third_party","rare",   "«Мне Маша сказала, что вы расстались»", "Он знает, но проверяет, знаешь ли ты"),
    ("photo_no_you","rare", "Фото с вечеринки, тебя нет в кадре",    "«А где ты?»"),
    ("apology",   "rare",   "«Не переживай»",                        "Слова утешения = тревога"),
    ("double_text","rare",  "Два сообщения подряд: «слушай» + «ладно»","Зачем отменять начатое"),
    ("night_3am", "rare",   "Сообщение в 3 часа ночи",                "Ночь — время честных ответов"),
    ("reminder",  "rare",   "«А помнишь, ты тогда сказала…»",        "Он помнит больше, чем ты"),
    ("call_plan", "rare",   "«Позвони, когда сможешь»",              "Не «позвони», а «когда сможешь»"),
    ("neutral_t","epic",    "«На нейтральной территории»",           "Зона комфорта, не ваша"),
    ("past_future","epic",  "«В следующем году хочу, чтобы всё было иначе»","«Иначе» = не как сейчас"),
    ("if_single", "epic",   "«Если будешь одна — я приду»",          "«Если» вместо «когда»"),
    ("gift_doubt","epic",   "«У меня подарок, но не знаю, дарить ли»","Интрига — тоже подарок"),
    ("silence_call","epic", "Звонит и молчит 5 секунд",              "Молчание в трубке — тоже подтекст"),
    ("plans_jan", "epic",   "«С 1 января у меня другие планы»",      "Планы без деталей"),
    ("year_q",    "epic",   "«У нас первый Новый год вместе?»",      "Вопрос «у нас» — это тест"),
    ("photo_food","epic",   "Фото ужина в 2 часа ночи",              "Одиночество с едой"),
    ("secret_kept","epic",  "«Только тебе скажу»",                   "И никогда не сказал"),
    ("old_photo", "legend", "Фото с вашей первой встречи",           "Показывает, что помнит"),
    ("one_word_love","legend","Сообщение из одного слова «наша»",    "Достаточно"),
    ("proposal_soft","legend","«Я тут думаю… насчёт нас…»",        "Тянут самую сложную фразу"),
    ("voice_sincerely","legend","Голосовое, где он говорит твоё имя","Просто иначе"),
    ("silent_late","legend", "Сообщение в 4 утра без единого слова",  "Самое сложное сообщение"),
    ("forgive_me","legend", "«Прости. Я знаю, что опоздал»",         "Прозвучало один раз в жизни"),
    ("kept_promise","legend","Он выполнил то, что обещал полгода назад","Редкость не в подарке, а в памяти"),
]
CARD_BY_ID = {c[0]: c for c in CARD_BANK}
CARD_OPEN_COST = 30

def _pick_rarity():
    """Взвешенный выбор редкости: обычных много, легендарных почти нет."""
    total = sum(RARITY_WEIGHT.values())
    x = random.random() * total
    acc = 0
    for k, _n, _i, w in CARD_RARITY:
        acc += w
        if x <= acc:
            return k
    return "common"

def _pick_card(rarity):
    pool = [c for c in CARD_BANK if c[1] == rarity]
    return random.choice(pool) if pool else random.choice(CARD_BANK)

def cards_view(acc):
    """Состояние коллекции для вкладки «Коллекция»."""
    raw = acc.get("cards") or {}
    counts = {}
    items = []
    total = 0
    for cid, n in raw.items():
        c = CARD_BY_ID.get(cid)
        if not c:
            continue
        total += int(n)
        counts[c[1]] = counts.get(c[1], 0) + int(n)
        items.append({"id": cid, "text": c[2], "sub": c[3], "rarity": c[1],
                      "rarity_name": RARITY_INFO[c[1]]["name"],
                      "rarity_icon": RARITY_INFO[c[1]]["icon"], "count": int(n)})
    items.sort(key=lambda x: (["common", "rare", "epic", "legend"].index(x["rarity"]), x["id"]))
    return {"ok": True, "total": total, "counts": counts, "items": items,
            "opened": acc.get("cards_opened", 0),
            "cost": CARD_OPEN_COST,
            "rarities": [{"key": k, "name": n, "icon": i, "weight": w}
                         for k, n, i, w in CARD_RARITY]}

# ---------------- Недельный турнир ----------------
# Очки суммируются из квиза и match-3 за текущую неделю (пн-вс по МСК).

def tourney_week():
    """Ключ недели: ISO-номер недели + год."""
    return time.strftime("%G-W%V", time.gmtime(time.time() + DAY_TZ_OFFSET * 3600))

def tourney_add(d, acc, points):
    """Начислить очки в турнире В ТОМ ЖЕ dict d, который уже в памяти.

    Важно: нельзя перечитывать store с диска — вызывающий код ещё не
    сохранил изменения монет/уровня, и save_store изнутри затёр бы их.
    """
    if points <= 0:
        return
    t = acc.setdefault("tourney", {"score": 0, "week": ""})
    week = tourney_week()
    if t.get("week") != week:
        t["week"] = week
        t["score"] = 0
    t["score"] = int(t.get("score", 0)) + int(points)

async def api_cards(request):
    """Коллекция карточек: состояние и открытие новой за монеты."""
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

    d = load_store(); acc = acc_of(d, uid)
    action = body.get("action", "state")

    if action == "exchange":
        # 3 одинаковые карты -> 15 монет. Синька: дублики не мусор, а ресурс.
        cid = str(body.get("card_id", ""))
        need = 3
        give = 15
        raw = acc.setdefault("cards", {})
        have = int(raw.get(cid, 0))
        if have < need:
            save_store(d)
            return web.json_response({"ok": False, "error": "not_enough_cards",
                                      "have": have, "need": need})
        raw[cid] = have - need
        acc["coins"] += give
        save_store(d)
        return web.json_response({"ok": True, "coins": acc["coins"], "got": give,
                                  "state": cards_view(acc)})

    if action == "open":
        cost = int(body.get("cost") or CARD_OPEN_COST)
        cost = max(1, min(cost, CARD_OPEN_COST))   # клиент не может занизить цену
        if acc["coins"] < cost:
            save_store(d)
            return web.json_response({"ok": False, "error": "not_enough_coins",
                                      "coins": acc["coins"], "cost": cost})
        acc["coins"] -= cost
        rarity = _pick_rarity()
        card = _pick_card(rarity)
        cid = card[0]
        acc.setdefault("cards", {})
        acc["cards"][cid] = acc["cards"].get(cid, 0) + 1
        acc["cards_opened"] = acc.get("cards_opened", 0) + 1
        save_store(d)
        is_new = acc["cards"][cid] == 1
        return web.json_response({
            "ok": True, "is_new": is_new, "coins": acc["coins"],
            "card": {"id": cid, "text": card[2], "sub": card[3], "rarity": card[1],
                     "rarity_name": RARITY_INFO[card[1]]["name"],
                     "rarity_icon": RARITY_INFO[card[1]]["icon"]},
            "state": cards_view(acc)})

    save_store(d)
    return web.json_response(cards_view(acc))

async def api_tourney(request):
    """Таблица турнира: топ игроков недели и место текущего."""
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

    d = load_store()
    week = tourney_week()
    rows = []
    for k, acc in d.items():
        t = acc.get("tourney") or {}
        if t.get("week") != week:
            continue
        sc = int(t.get("score", 0))
        if sc <= 0:
            continue
        rows.append({"uid": k, "name": acc.get("name") or acc.get("first_name") or "Игрок",
                     "score": sc})
    rows.sort(key=lambda r: -r["score"])
    top = [{"place": i + 1, "name": r["name"], "score": r["score"]}
           for i, r in enumerate(rows[:10])]

    my_score = 0
    my_place = 0
    acc = acc_of(d, uid)
    t = acc.get("tourney") or {}
    if t.get("week") == week:
        my_score = int(t.get("score", 0))
    for i, r in enumerate(rows):
        if r["uid"] == str(uid):
            my_place = i + 1
            break
    if not my_place and my_score > 0:
        my_place = len(rows) + 1

    return web.json_response({"ok": True, "week": week, "top": top,
                              "my_score": my_score, "my_place": my_place,
                              "players": len(rows)})

# ============================================================
# ПРИЗОВОЙ КОНКУРС «ПОДТЕКСТ» — бесплатный вход + платный вход
# ============================================================
# Идея: игрок копит очки активности (квиз, match-3, серия дней,
# рефералка) и обменивает их на БИЛЕТЫ в розыгрыш.
#
# Вход БЕСПЛАТНЫЙ: 30 очков активности = 1 билет.
# Вход ПЛАТНЫЙ:  100 Stars = 5 билетов (это же отбивает расход
#                на рекламу и реально мотивирует: «вложился — жди
#                шанс выиграть крупнее»).
#
# ПРИЗ: 100 Stars победителю + игровые бонусы.
# Техническое ограничение Telegram: бот НЕ может перевести Stars
# пользователю (в Bot API нет такого метода — только refundStarPayment
# для возврата его собственной оплаты). Поэтому Stars-приз разыгрывается
# НАТИВНЫМ розыгрышем в канале (создаётся в Telegram, не ботом),
# а бот начисляет внутриигровой пакет: монеты + жизни + без рекламы.

CONTEST_ID = "podtekst_v1"
CONTEST_END = "2026-12-15T23:59:00"      # окончание приёма билетов (МСК)
CONTEST_START = "2026-11-21T00:00:00"
CONTEST_TICKET_POINTS = 30               # очков активности на 1 бесплатный билет
CONTEST_TICKET_STARS = 100              # Stars за платный вход
CONTEST_TICKETS_PAID = 5                # билетов за 100 Stars
CONTEST_WINNERS = 3                      # сколько победителей
# Приз: подарок Telegram + игровой пакет.
# ПОДАРОК вместо 100 Stars: бот умеет отправлять подарки напрямую (sendGift),
# в отличие от Stars, которые перевести пользователю бот НЕ может.
# Подарок даёт ~100 звёзд номинала, виден в профиле, его можно улучшить
# до NFT и переслать — воспринимаемая ценность выше, чем у звёзд.
CONTEST_PRIZE = {"coins": 500, "lives": 3, "noads": True}
CONTEST_PRIZE_GIFT_EMOJI = "💎"      # 100 звёзд номинала
CONTEST_PRIZE_GIFT_FALLBACK = ["💎", "🏆", "💍", "🎂", "🚀"]  # запасные, если нет в каталоге
CONTEST_PRIZE_STARS_EQUIV = 100      # номинал приза в звёздах (для текстов)

# Призовые места: 1 -> 100 Stars, 2 -> 50, 3 -> 25 (эквивалент в подарках Telegram).
# Подарок даёт ~N звёзд номинала. Бот не может перевести Stars пользователю
# (в Bot API нет такого метода), поэтому звёздная часть выдаётся ПОДАРКОМ.
CONTEST_PLACES = [
    {"place": 1, "stars": 100, "coins": 1000, "lives": 5, "gift": "💎"},
    {"place": 2, "stars": 50,  "coins": 500,  "lives": 3, "gift": "🏆"},
    {"place": 3, "stars": 25,  "coins": 300,  "lives": 2, "gift": "🎁"},
]
CONTEST_PRIZE_STARS_EQUIV = CONTEST_PLACES[0]["stars"]  # номинал 1-го места для текстов

# Сколько очков активности даёт каждое действие (тот же вес, что в турнире)
CONTEST_ACTION_POINTS = {"quiz": 10, "m3": 5, "day": 20, "ref": 25, "share": 15}

def _msk():
    """Текущее время по Москве (aware)."""
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3)))

def _contest_dt(s):
    """Разбор даты конкурса. Всегда aware (МСК), иначе нельзя сравнивать."""
    dt = datetime.datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=3)))
    return dt

def contest_state():
    """Текущее состояние конкурса: идёт / закончился / ещё не начался."""
    now = _msk()
    if now < _contest_dt(CONTEST_START):
        return "upcoming"
    if now > _contest_dt(CONTEST_END):
        return "ended"
    return "running"

def contest_tickets(acc):
    """Сколько билетов уже есть у игрока (купленных за Stars)."""
    c = acc.setdefault("contest", {"points": 0, "paid_tickets": 0, "tickets": 0})
    c.setdefault("points", 0)
    c.setdefault("paid_tickets", 0)
    c.setdefault("tickets", 0)
    return c

def contest_add_points(d, acc, kind, amount=None):
    """Начислить очки активности за действие (квиз/match-3/день/рефералка)."""
    if contest_state() != "running":
        return 0
    pts = amount if amount is not None else CONTEST_ACTION_POINTS.get(kind, 0)
    if pts <= 0:
        return 0
    c = contest_tickets(acc)
    c["points"] = int(c.get("points", 0)) + int(pts)
    # Каждые 30 очков автоматически превращаются в бесплатный билет
    gained = c["points"] // CONTEST_TICKET_POINTS
    if gained:
        c["points"] -= gained * CONTEST_TICKET_POINTS
        c["tickets"] = int(c.get("tickets", 0)) + gained
    return pts

async def api_contest(request):
    """Состояние конкурса: очки, билеты, место в рейтинге, лидеры."""
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

    d = load_store(); acc = acc_of(d, uid)
    c = contest_tickets(acc)
    save_store(d)

    # Рейтинг участников по билетам
    rows = []
    for k, a in d.items():
        if not k.isdigit() or not isinstance(a, dict):
            continue
        cc = a.get("contest") or {}
        t = int(cc.get("tickets", 0))
        if t <= 0:
            continue
        rows.append({"uid": k, "name": a.get("name") or a.get("first_name") or "Игрок",
                     "tickets": t})
    rows.sort(key=lambda r: (-r["tickets"], r["name"]))
    top = [{"place": i + 1, "name": r["name"], "tickets": r["tickets"]}
           for i, r in enumerate(rows[:10])]
    my_place = 0
    for i, r in enumerate(rows):
        if r["uid"] == str(uid):
            my_place = i + 1
            break

    return web.json_response({
        "ok": True,
        "state": contest_state(),
        "points": int(c.get("points", 0)),
        "points_needed": CONTEST_TICKET_POINTS,
        "tickets": int(c.get("tickets", 0)),
        "paid_tickets": int(c.get("paid_tickets", 0)),
        "paid_stars": CONTEST_TICKET_STARS,
        "paid_tickets_amount": CONTEST_TICKETS_PAID,
        "winners": CONTEST_WINNERS,
        "prize_coins": CONTEST_PRIZE["coins"],
        "prize_lives": CONTEST_PRIZE["lives"],
        "prize_stars": CONTEST_PRIZE_STARS_EQUIV,
        "prize_gift": CONTEST_PRIZE_GIFT_EMOJI,
        "places": CONTEST_PLACES,
        "ends_at": CONTEST_END,
        "top": top,
        "my_place": my_place,
        "players": len(rows),
        **wallet_view(acc),
    })

async def api_contest_buy(request):
    """Оплатить вход в розыгрыш: 100 Stars -> 5 билетов.

    Платёж идёт обычным инвойсом Telegram (payload contest_entry).
    Проверка платежа — в обработчике successful_payment.
    """
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
    if contest_state() != "running":
        return web.json_response({"ok": False, "error": "contest closed"})
    d = load_store(); acc = acc_of(d, uid)
    contest_tickets(acc); save_store(d)
    return web.json_response({"ok": True, "stars": CONTEST_TICKET_STARS,
                              "tickets": CONTEST_TICKETS_PAID,
                              "method": "stars"})

@dp.message(Command("contest"))
async def contest_cmd(message: types.Message):
    """Команда /contest: правила и статус розыгрыша."""
    d = load_store(); acc = acc_of(d, message.from_user.id)
    c = contest_tickets(acc)
    await message.answer(
        "\U0001F3C6 <b>Призовой конкурс ПОДТЕКСТ</b>\n\n"
        "Приз: <b>100 Stars</b> + 500 монет + 3 жизни + без рекламы\n"
        f"Победителей: {CONTEST_WINNERS}\n"
        f"Дедлайн: {CONTEST_END[:10]}\n\n"
        "<b>Бесплатный вход:</b> играй, копи очки активности — "
        f"каждые {CONTEST_TICKET_POINTS} очков = 1 билет.\n"
        "<b>Платный вход:</b> 100 Stars = 5 билетов.\n\n"
        f"Твои очки: {c.get('points', 0)}/{CONTEST_TICKET_POINTS} · "
        f"билетов: {c.get('tickets', 0)}\n\n"
        "Играй тут:",
        reply_markup=kb())

@dp.message(Command("giveprize"))
async def giveprize(message: types.Message):
    """Админ: выдать игровой пакет prize победителю конкурса.

    /giveprize <user_id> — вручить игровой пакет (500 монет, 3 жизни, без рекламы).
    /giveprize <user_id> stars — дополнительно отправить подарок (если хватает Stars).
    """
    if message.from_user.id != ADMIN_ID:
        await message.answer("Нет доступа."); return
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Формат: /giveprize <user_id> [stars]"); return
    try:
        target = int(parts[1])
    except ValueError:
        await message.answer("user_id должен быть числом."); return
    d = load_store(); acc = acc_of(d, target)
    acc["coins"] += CONTEST_PRIZE["coins"]
    acc["lives"] = min(9, int(acc.get("lives", 5)) + CONTEST_PRIZE["lives"])
    acc["noads"] = True
    save_store(d)

    gift_res = await send_prize_gift(target)
    msg = (f"\U0001F381 Поздравляем! Ты выиграл в конкурсе ПОДТЕКСТ.\n\n"
           f"Твой подарок: {gift_res}\n"
           f"Плюс в игре: +{CONTEST_PRIZE['coins']} 🪙, +{CONTEST_PRIZE['lives']} 💗 жизни, "
           "без рекламы навсегда.\n\n"
           "Подарок уже в твоём профиле — его можно улучшить до NFT и переслать друзьям.")
    try:
        await bot.send_message(target, msg)
        sent = "Сообщение о призе отправлено."
    except Exception as e:
        sent = f"Не удалось отправить сообщение: {e}"

    await message.answer(f"Приз вручён игроку {target}.\n"
                         f"Подарок: {gift_res}\n{sent}")

async def send_prize_gift(user_id: int) -> str:
    """Отправить подарок-победителю. Возвращает описание результата.

    Бот покупает подарок со своего Stars-баланса (он пополняется входящими
    платежами за билеты, поэтому конкурс самоокупается). Если нужного подарка
    нет в каталоге — берём следующий по списку. Если баланса не хватает —
    честно сообщаем, приз выдаётся отдельно.
    """
    try:
        gifts = await bot.get_available_gifts()
    except Exception as e:
        return f"не удалось получить каталог подарков: {e}"
    gl = list(gifts.gifts) if gifts else []
    if not gl:
        return "каталог подарков пуст"
    by_emoji = {}
    for g in gl:
        em = (g.sticker.emoji if g.sticker else "") or ""
        by_emoji.setdefault(em, g)
    # Порядок предпочтения: сначала самый щедрый, но не дороже того,
    # что бот реально может оплатить со своего Stars-баланса.
    try:
        bal = (await bot.get_my_star_balance()).amount
    except Exception:
        bal = 0
    prefs = [by_emoji[em] for em in [CONTEST_PRIZE_GIFT_EMOJI] + CONTEST_PRIZE_GIFT_FALLBACK
             if em in by_emoji]
    chosen = None
    for g in prefs:
        if int(g.star_count or 0) <= bal:
            chosen = g; break
    if chosen is None:
        affordable = [g for g in gl if int(g.star_count or 0) <= bal]
        if not affordable:
            return (f"не хватило Stars на балансе бота ({bal} ⭐) для подарка. "
                    "Нужно пополнить баланс бота или выдать приз вручную.")
        chosen = min(affordable, key=lambda g: int(g.star_count or 0))
    try:
        await bot.send_gift(user_id=user_id, gift_id=chosen.id)
        em = (chosen.sticker.emoji if chosen.sticker else "?")
        return f"подарок {em} отправлен (номинал {chosen.star_count} ⭐)"
    except Exception as e:
        msg = str(e)
        if "not enough" in msg.lower() or "stars" in msg.lower():
            return (f"не хватило Stars на балансе бота для подарка ({msg}). "
                    "Нужно пополнить баланс бота или выдать приз вручную.")
        return f"ошибка отправки подарка: {msg}"

# ---------------- Battle Pass (билет сезона) ----------------
# Серверный прогресс: игрок копит очки активности, уровни открываются
# автоматически, а награду нужно забрать вручную (одна кнопка).
BP_LEVELS = [
    {"coins": 0,     "name": "\u0421\u0442\u0430\u0440\u0442",              "reward": {}},
    {"coins": 500,   "name": "+100 \U0001FA99",              "reward": {"coins": 100}},
    {"coins": 1200,  "name": "+200 \U0001FA99",              "reward": {"coins": 200}},
    {"coins": 2500,  "name": "+1 \U0001F497",                "reward": {"lives": 1}},
    {"coins": 4500,  "name": "+300 \U0001FA99",              "reward": {"coins": 300}},
    {"coins": 7000,  "name": "\U0001F308 \u0420\u0430\u0434\u0443\u0433\u0430", "reward": {"skin": "rainbow"}},
    {"coins": 10000, "name": "+500 \U0001FA99",              "reward": {"coins": 500}},
]
BP_ACTION_POINTS = {"quiz": 30, "m3": 20, "day": 50, "ref": 60, "share": 40}

def bp_acc(acc):
    """Состояние билета сезона для игрока."""
    bp = acc.setdefault("bp", {"coins": 0, "level": 1, "claimed": []})
    bp.setdefault("coins", 0)
    bp.setdefault("level", 1)
    bp.setdefault("claimed", [])
    return bp

def bp_level_for(coins):
    """Номер уровня по набранным очкам (1-based)."""
    lv = 1
    for i, l in enumerate(BP_LEVELS):
        if coins >= l["coins"]:
            lv = i + 1
    return lv

def bp_add_points(d, acc, kind, amount=None):
    """Начислить очки сезона за действие. Возвращает новый уровень, если вырос."""
    pts = amount if amount is not None else BP_ACTION_POINTS.get(kind, 0)
    if pts <= 0:
        return 0
    bp = bp_acc(acc)
    bp["coins"] = int(bp.get("coins", 0)) + int(pts)
    old = int(bp.get("level", 1))
    new = bp_level_for(int(bp["coins"]))
    bp["level"] = new
    return new if new > old else 0

def bp_state(acc):
    """Полное состояние билета для фронта."""
    bp = bp_acc(acc)
    coins = int(bp.get("coins", 0))
    level = int(bp.get("level", 1))
    claimed = set(bp.get("claimed", []))
    levels = []
    for i, l in enumerate(BP_LEVELS):
        n = i + 1
        levels.append({
            "level": n, "coins": l["coins"], "name": l["name"],
            "reward": l.get("reward", {}),
            "unlocked": coins >= l["coins"],
            "claimed": n in claimed,
            "claimable": (coins >= l["coins"] and n > 1 and n not in claimed),
        })
    nxt = BP_LEVELS[level] if level < len(BP_LEVELS) else None
    return {
        "ok": True, "coins": coins, "level": level,
        "max_level": len(BP_LEVELS),
        "next_coins": nxt["coins"] if nxt else coins,
        "next_name": nxt["name"] if nxt else "\u041c\u0430\u043a\u0441\u0438\u043c\u0443\u043c",
        "to_next": max(0, (nxt["coins"] if nxt else coins) - coins),
        "levels": levels,
        "claimed": sorted(claimed),
        "skin": acc.get("bp_skin", ""),
    }

def bp_apply_reward(acc, reward):
    """Применить награду уровня к кошельку игрока."""
    got = []
    if reward.get("coins"):
        acc["coins"] = int(acc.get("coins", 0)) + int(reward["coins"])
        got.append(f"+{reward['coins']} \U0001FA99")
    if reward.get("lives"):
        acc["lives"] = min(9, int(acc.get("lives", 5)) + int(reward["lives"]))
        got.append(f"+{reward['lives']} \U0001F497")
    if reward.get("skin"):
        acc["bp_skin"] = reward["skin"]
        got.append("\U0001F308 \u0441\u043a\u0438\u043d")
    if reward.get("noads"):
        acc["noads"] = True
        got.append("\u0431\u0435\u0437 \u0440\u0435\u043a\u043b\u0430\u043c\u044b")
    return got

async def api_bp(request):
    """\u0411\u0438\u043b\u0435\u0442 \u0441\u0435\u0437\u043e\u043d\u0430: \u043e\u0447\u043a\u0438, \u0443\u0440\u043e\u0432\u0435\u043d\u044c, \u043d\u0430\u0433\u0440\u0430\u0434\u044b."""
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
    d = load_store(); acc = acc_of(d, uid)
    save_store(d)
    return web.json_response({**bp_state(acc), **wallet_view(acc)})

async def api_bp_claim(request):
    """\u0417\u0430\u0431\u0440\u0430\u0442\u044c \u043d\u0430\u0433\u0440\u0430\u0434\u044b \u0437\u0430 \u0432\u0441\u0435 \u0434\u043e\u0441\u0442\u0438\u0436\u043d\u044b\u0435 \u0443\u0440\u043e\u0432\u043d\u0438."""
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
        want = int(body.get("level", 0))
    except Exception:
        return web.json_response({"error": "bad level"}, status=400)
    d = load_store(); acc = acc_of(d, uid)
    bp = bp_acc(acc)
    coins = int(bp.get("coins", 0))
    claimed = set(bp.get("claimed", []))
    if want <= 1:
        return web.json_response({"ok": False, "error": "nothing to claim", **bp_state(acc), **wallet_view(acc)})
    if want in claimed:
        return web.json_response({"ok": False, "error": "already claimed", **bp_state(acc), **wallet_view(acc)})
    if want > len(BP_LEVELS) or coins < BP_LEVELS[want - 1]["coins"]:
        return web.json_response({"ok": False, "error": "level locked", **bp_state(acc), **wallet_view(acc)})
    got = bp_apply_reward(acc, BP_LEVELS[want - 1].get("reward", {}))
    claimed.add(want); bp["claimed"] = sorted(claimed)
    save_store(d)
    return web.json_response({"ok": True, "level": want, "got": got,
                              **bp_state(acc), **wallet_view(acc)})

@dp.message(Command("pass"))
async def pass_cmd(message: types.Message):
    """\u041a\u043e\u043c\u0430\u043d\u0434\u0430 /pass: \u0441\u043e\u0441\u0442\u043e\u044f\u043d\u0438\u0435 \u0431\u0438\u043b\u0435\u0442\u0430 \u0441\u0435\u0437\u043e\u043d\u0430."""
    d = load_store(); acc = acc_of(d, message.from_user.id)
    st = bp_state(acc)
    lines = ["\U0001F39F <b>\u0411\u0438\u043b\u0435\u0442 \u0441\u0435\u0437\u043e\u043d\u0430</b>\n"]
    for l in st["levels"]:
        mark = "\u2714\ufe0f" if l["claimed"] else ("\U0001f3c1" if l["unlocked"] else "\u2022")
        lines.append(f"{mark} \u0423\u0440\u043e\u0432\u0435\u043d\u044c {l['level']}: {l['name']}")
    lines.append(f"\n\u041e\u0447\u043a\u043e\u0432: {st['coins']} \U0001FA99 \u00b7 \u0443\u0440\u043e\u0432\u0435\u043d\u044c {st['level']}")
    if st["to_next"]:
        lines.append(f"\u0414\u043e \u0441\u043b\u0435\u0434\u0443\u044e\u0449\u0435\u0433\u043e \u0443\u0440\u043e\u0432\u043d\u044f: {st['to_next']} \U0001FA99")
    lines.append("\n\u0417\u0430\u0431\u0440\u0430\u0442\u044c \u043d\u0430\u0433\u0440\u0430\u0434\u044b \u043c\u043e\u0436\u043d\u043e \u0432 \u0438\u0433\u0440\u0435 \u0432 \u043f\u0440\u043e\u0444\u0438\u043b\u0435:")
    await message.answer("\n".join(lines), reply_markup=kb())


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

def build_app(include_webhook=False):
    """Единая регистрация маршрутов для обоих режимов (polling и webhook).

    Раньше API-маршруты жили только в ветке webhook, и в режиме polling
    Mini App получал 404 на /api/quiz, /api/wallet и /api/action.
    """
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

    if include_webhook:
        app.router.add_post("/tg/{token}", webhook_handler)
    app.router.add_get("/health", lambda r: web.Response(text="ok"))
    app.router.add_post("/api/wallet", api_wallet)
    app.router.add_post("/api/action", api_action)
    app.router.add_post("/api/quiz", api_quiz)
    app.router.add_post("/api/answer", api_answer)
    app.router.add_post("/api/admin/credit", api_admin_credit)
    app.router.add_post("/api/m3", api_m3)
    app.router.add_post("/api/visit", api_visit)
    app.router.add_post("/api/referral", api_referral)
    app.router.add_post("/api/promo", api_promo)
    app.router.add_post("/api/promo/click", api_promo_click)
    app.router.add_post("/api/promo/claim", api_promo_claim)
    app.router.add_post("/api/mira/hint", api_mira_hint)
    app.router.add_post("/api/promo/stats", api_promo_stats)
    app.router.add_post("/api/ads/stats", api_ads_stats)
    app.router.add_post("/api/leaderboard", api_leaderboard)
    app.router.add_post("/api/share/score", api_share_score)
    app.router.add_post("/api/channel/sub", api_channel_sub)
    app.router.add_post("/api/cards", api_cards)
    app.router.add_post("/api/tourney", api_tourney)
    app.router.add_post("/api/contest", api_contest)
    app.router.add_post("/api/contest/buy", api_contest_buy)
    app.router.add_post("/api/bp", api_bp)
    app.router.add_post("/api/bp/claim", api_bp_claim)

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
    return app


COMMANDS = [
    types.BotCommand(command="start", description="Играть в ПОДТЕКСТ"),
    types.BotCommand(command="buy", description="Купить монеты за Stars"),
    types.BotCommand(command="stars", description="Цены на монеты и бонусы"),
    types.BotCommand(command="referral", description="Ссылка для друзей"),
    types.BotCommand(command="contest", description="Призовой конкурс"),
    types.BotCommand(command="help", description="Список команд"),
    types.BotCommand(command="paysupport", description="Помощь с оплатой"),
]

async def set_commands_once():
    """Регистрация команд в меню бота. Раньше список вообще не выставлялся."""
    for attempt in range(5):
        try:
            await bot.set_my_commands(COMMANDS)
            logging.info("bot commands registered: %s", [c.command for c in COMMANDS])
            return True
        except Exception as e:
            logging.warning("set_my_commands attempt %s failed: %s", attempt, e)
            await asyncio.sleep(5)
    return False



# ============ ЕЖЕДНЕВНЫЙ КОНТЕНТ ДЛЯ КАНАЛА ============
# Канал живёт на контенте: без ежедневных постов подписываться не на что.
# Формат — разбор фразы с голосованием: читатель угадывает подтекст прямо
# в канале, а потом идёт играть в бота. Это и контент, и вовлечение.

DAILY_POSTS = [
    ("\"Мне всё равно\" — и стикер с котом. Что он имел в виду?",
     ["\"Мне всё равно, но я звонил три раза\"",
      "\"Мне всё равно. Скинь на карту, скину завтра\"",
      "\"Мне всё равно, просто я обиделся\""],
     "По контексту — первое. Если бы было всё равно, не звонил бы три раза.",
     "А вы как читаете такие фразы? Проверьте себя в игре — там 4 варианта и только один верный."),

    ("\"Ничего страшного\" — на 3:47 ночи.",
     ["\"Ничего страшного, но я жду звонка\"",
      "\"Ничего страшного, я серьёзно\"",
      "\"Ничего страшного, давай не сегодня\""],
     "Второе. Третье — отмазка, первое — просьба.",
     "Угадали? В игре такие фразы разбираются по уровням — от простых к злым."),

    ("\"Мне не холодно, но куртку можешь забрать\".",
     ["\"Мне не холодно, но куртку можешь забрать\"",
      "\"Мне не холодно. Просто куртку забери\"",
      "\"Мне не холодно, но давай обнимемся\""],
     "Третье. Первое — приглашение, хоть и замаскированное.",
     "Подтекст — это когда фраза говорит одно, а значит совсем другое."),

    ("\"Не переживай, я не обижаюсь\".",
     ["\"Не переживай, я не обижаюсь\"",
      "\"Не переживай, я обижаюсь, но не на тебя\"",
      "\"Не переживай, просто я обиделся и ухожу\""],
     "Второе или третье — тут все врут. В игре за это дают монеты.",
     "Есть ежедневный челлендж: одинаковые задания у всех игроков. Сравни свой результат."),

    ("\"Я просто говорю\".",
     ["\"Я просто говорю, но ты не понял\"",
      "\"Я просто говорю, не обращай внимания\"",
      "\"Я просто говорю. И это неправда\""],
     "Третье. Классика: «просто говорю» = «говорю намеками».",
     "Хотите больше таких фраз? Играйте в ПОДТЕКСТ — каждый день новые."),
]

_poll_state = {}

async def publish_daily_post():
    """Публикует ежедневный пост-разбор фразы с голосованием в канале."""
    if not CHAT_ID:
        return
    idx = int(time.time() // 86400) % len(DAILY_POSTS)
    question, options, answer, teaser = DAILY_POSTS[idx]
    try:
        msg = await bot.send_message(
            chat_id=CHAT_ID,
            text=(f"\U0001F9E0 <b>Что он имел в виду?</b>\n\n"
                  f"{question}\n\n"
                  f"1\. {options[0]}\n"
                  f"2\. {options[1]}\n"
                  f"3\. {options[2]}\n\n"
                  f"\U0001F3AF Голосуйте, правильный ответ — в комментариях завтра.\n\n"
                  f"{teaser}\n\n"
                  f"\U0001F3AE <a href=\"https://t.me/podtekst_game_bot\">Играть: @podtekst_game_bot</a>"),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="1️⃣", callback_data="vote:1"),
                 InlineKeyboardButton(text="2️⃣", callback_data="vote:2"),
                 InlineKeyboardButton(text="3️⃣", callback_data="vote:3")],
                [InlineKeyboardButton(text="\U0001F3AE Играть", url=WEBAPP_URL)],
            ]))
        _poll_state["msg_id"] = msg.message_id
        logging.info("daily post published: %s", msg.message_id)
    except Exception as e:
        logging.warning("publish_daily_post failed: %s", e)

async def daily_poster_loop():
    """Публикует пост раз в сутки в 11:00 МСК + перезапуск при падении."""
    while True:
        try:
            MSK = datetime.timezone(datetime.timedelta(hours=3))
            now = datetime.datetime.now(MSK)
            # ближайшие 11:00 по московскому времени
            target = now.replace(hour=11, minute=0, second=0, microsecond=0)
            if now >= target:
                target += datetime.timedelta(days=1)
            await asyncio.sleep((target - now).total_seconds())
            await publish_daily_post()
            await asyncio.sleep(86400)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logging.warning("daily poster loop: %s", e)
            await asyncio.sleep(300)

@dp.callback_query(F.text.startswith("vote:"))
async def on_vote(call: types.CallbackQuery):
    """Голосование в ежедневном посте (только свои vote:*)."""
    if not call.data or not call.data.startswith("vote:"):
        return
    choice = call.data.split(":")[1]
    try:
        await call.answer(f"\U0001F3AF Твой выбор: {choice}. Ответ завтра в канале!", show_alert=False)
    except Exception as e:
        logging.warning("on_vote failed: %s", e)


async def main():
    await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Играть", web_app=WebAppInfo(url=WEBAPP_URL)))
    # Команды в меню бота выставляем при каждом старте: список должен
    # восстановиться сам, даже если Telegram его сбросил.
    await set_commands_once()
    # Фоновая запись состояния на диск: не блокирует запросы.
    _flusher = asyncio.create_task(store_flusher())
    mode = os.environ.get("MODE", "polling").lower()
    if mode == "webhook":
        port = int(os.environ.get("PORT", "8080"))
        app = build_app(include_webhook=True)
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
            # Тот же набор маршрутов, что и в webhook-режиме: Mini App
            # обязан получать кошелёк и задания и в режиме polling.
            app = build_app(include_webhook=False)
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

        await asyncio.gather(health_app(), run_polling_forever(), daily_poster_loop())

if __name__ == "__main__":
    asyncio.run(main())
