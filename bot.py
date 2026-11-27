
import asyncio, os, logging, json, hmac, hashlib, time, random, urllib.parse, urllib.request, datetime, io
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
# TON-кошелёк владельца: сюда идут комиссия партнёрской программы и вывод звёзд.
# Адрес проверен (user-friendly, CRC16 сходится, основная сеть).
TON_WALLET = os.environ.get("TON_WALLET", "UQAjBgYpu4d6sDYIqV7EO7ZO67V5aPw25Yxp0-IGOx4fl-qI").strip()
PROMO_REWARD = 50          # 🪙 игроку за подключение Mira

# ---- Рекламные кампании: клик по креативу ведёт по реферальной ссылке ----
# Каждая кампания: id, название, текст, URL (рефералка), картинка, вес.
# Клики считаются на сервере, чтобы видеть, какая кампания приносит игроков.
# ССЫЛКИ ПРАВИТЬ ЗДЕСЬ — это единственное место, где задаются рефералки.
AD_CAMPAIGNS = [
    {
        "id": "bums",
        "title": "Bums — игра про то, что он имел в виду",
        "text": "Ещё одна игра про подтексты. Открыть и забрать бонус",
        "url": os.environ.get("AD_BUMS_URL", "https://t.me/bums/app?startapp=ref_8684637509"),
        "img": "ad-bums.jpg",
        # Вес 1: api.bums.bot отдаёт 502 (рекламный API упал), но ссылка
        # t.me/bums/app жива — клик по креативу всё равно приводит игрока.
        "weight": 1,
    },
    {
        "id": "notwise",
        "title": "NOT Wise — твоя сова уже ждёт",
        "text": "Корми, качай и выигрывай в боях. Собери свою сову",
        "url": os.environ.get("AD_NOTWISE_URL", "https://t.me/notwise_bot/thegame?startapp=93f765f1a91a45abb0883ab11ba7cf17"),
        "img": "ad-notwise.jpg",
        "weight": 2,
    },
    {
        "id": "mira",
        "title": "Mira — ИИ, который тебя понимает",
        "text": "Твой персональный ИИ в Telegram. Подключи и получи +50 🪙",
        "url": MIRA_URL,
        "img": "house-ad.jpg",
        # Вес 3: Mira платит нам реферальными токенами - это наш
        # единственный партнёрский источник дохода, ставим в приоритет.
        "weight": 3,
    },
]
AD_DEFAULT_IMG = "house-ad.jpg"
AD_CLICK_BONUS = 25      # 🪙 игроку за просмотр рекламы (это же rewarded-слот)
AD_VIEW_SHARDS = 3       # +0.3 звезды осколками за просмотр рекламы
AD_MIRA_SHARDS = 10      # +1 звезда осколками за клик именно по Mira
AD_CLICK_SHARDS = 3      # +0.3 звезды осколками за клик по любой рекламе
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

# Сервер считает дни по Москве (UTC+3). Объявлено здесь, а не рядом с
# блоком ежедневных наград: модуль звёзд ниже использует его в аргументах
# по умолчанию, и Python вычисляет их в момент определения функции.
DAY_TZ_OFFSET = 3

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

# Пакет «Звездопад+»: платный бустер (Stars -> взнос в общий пул осколков).
# Объявляем ДО PACKS: цена нужна и в пакетах, и в логике бустера.
PACK_FALL_BOOST = 200           # 200 Stars — цена бустера «Звездопад+»

# Заморозка серии: пропуск ровно одного дня не обнуляет дневную серию.
# Тоже до PACKS: цена идёт и в пакетах, и в витрине.
FREEZE_MAX = 3             # максимум заморозок в запаре
FREEZE_FREE_EVERY = 7      # одна бесплатная заморозка раз в 7 дней
FREEZE_COST_SHARDS = 100   # докупка за 100 осколков (1 звезда)
FREEZE_COST_STARS = 50     # докупка за 50 Stars

# Пакеты генераций картинок. Себестоимость генерации — 30..120 токенов Mira,
# при PRO-цене это 15..60 звёзд за картинку. Продаём пакетом с запасом:
# 10 генераций за 900 звёзд = 90 звёзд на картинку при себестоимости до 60.
GEN_PACK_STARS = 900
GEN_PACK_COUNT = 10
GEN_PACK_BIG_STARS = 2400
GEN_PACK_BIG_COUNT = 30
# Текстовый вопрос Mira за осколки: 10 осколков = 1 звезда.
# Это сток для осколков — они тратятся, а не просто копятся.
MIRA_ASK_SHARDS = 10
MIRA_ASK_COOLDOWN = 90          # секунд между вопросами одного игрока
# Партнёрская программа Telegram. Создать её может только аккаунт-владелец
# (bots.updateStarRefProgram доступен лишь пользователям), поэтому бот
# только хранит параметры и показывает владельцу, что настроить.
AFF_COMMISSION_PERMILLE = 200   # 20% — программа создана в профиле мини-приложения
AFF_DURATION_MONTHS = 3
# Реферальные ссылки Telegram выдаёт в «Мои звёзды». Сюда вписываются
# ссылки, сгенерированные для балансов: личный / канал / мини-приложение.
AFF_LINKS = {
    "personal": os.environ.get("AFF_LINK_PERSONAL", ""),
    "channel":  os.environ.get("AFF_LINK_CHANNEL", ""),
    "miniapp":  os.environ.get("AFF_LINK_MINIAPP", ""),
}
# ID чата, где бот принимает промпты на генерацию. Задаётся переменной
# окружения: создать группу может только аккаунт человека, бот — не может.
GEN_CHAT_ID = os.environ.get("GEN_CHAT_ID", "").strip()
PACKS = {
    "test_1star": (1, "Тест 1 звезда"),
    "coins_100": (100, "100 монет"),
    "coins_500": (500, "500 монет"),
    "lives":     (200, "+1 жизнь"),
    "noads":     (350, "Без рекламы"),
    "contest_entry": (100, "5 билетов в розыгрыш"),
    # Реферальный пакет: 100 монет за 80 звёзд вместо 100. Купон даётся
    # пригласившему за каждого нового друга и тратится один раз.
    "coins_100_ref": (80, "100 монет (по рефералке)"),
    # Бустер «Звездопад+»: +80% к шансу выпадения осколка и +25% к силе
    # питомца на 3 дня.
    # Цена бустера = PACK_FALL_BOOST. Держим одно число в одном месте,
    # иначе фронт показывает одну цену, а инвойс шлёт другую.
    "fall_boost": (PACK_FALL_BOOST, "Звездопад+ на 3 дня — взнос в общий пул осколков"),
    # Заморозка серии: пропуск ровно одного дня не обнуляет дневную серию.
    "freeze_stars": (FREEZE_COST_STARS, "Заморозка серии"),
    # Пакеты генераций картинок.
    "gen_pack": (GEN_PACK_STARS, "%d генераций картинок" % GEN_PACK_COUNT),
    "gen_pack_big": (GEN_PACK_BIG_STARS, "%d генераций картинок" % GEN_PACK_BIG_COUNT),
}
REF_COUPON_PACK = "coins_100_ref"


# ================= ЗВЁЗДЫ: ОСКОЛКИ, ЗВЕЗДОПАД, ПОДАРКИ =================
# Внутренняя валюта «звезда». Хранится в десятых: shards — целые ×10.
# Telegram Stars — только для покупок (пакеты) и они не конвертируются
# в осколки: игрок платит Stars, чтобы купить подарок за осколки.
STAR_PACK = 10                  # 1 звезда = 10 единиц хранения
SHARD_MIN = 1                   # минимальный осколок — 0.1 звезды
SHARD_MAX = 10                  # максимум — целая звезда
SHARD_MEAN = 3                  # средний дроп за событие (0.3 звезды)

# Звездопад: окно раз в неделю. Пятница 18:00 МСК -> воскресенье 23:59 МСК.
FALL_FROM_HOUR = 18            # пятница, 18:00
FALL_TO_HOUR = 23               # воскресенье, 23:59
FALL_BOOST_MULT = 2             # множитель шанса в окне
FALL_BASE_CHANCE = 0.35         # базовый шанс выпадения осколка за событие

FALL_BOOST_PACK_TTL = 86400 * 3  # 3 дня

# Окно обмена на подарки: раз в месяц, 1-е..7-е число.
GIFT_WINDOW_DAYS = 7
GIFT_COOLDOWN = 86400           # 1 подарок в сутки на аккаунт

# Рефералка -> звезда. Не просто «приведи друга»: нужна подписка на канал
# у приглашённого, иначе это чистый фарм.
REF_SHARD_INVITER = 10          # 1 звезда пригласившему (за живого подписчика)
REF_SHARD_NEW = 5               # 0.5 звезды новому (сразу за переход)
REF_DAILY_CAP = 30              # максимум осколков с рефералки в сутки (3 звезды)

# Рефералка: запрет на «пригласил и сразу отписался» — бот смотрит подписку
# пригласившего в момент начисления и повторно при выдаче.
REF_SUB_REQUIRED = True

# Витрина подарков: id -> (эмодзи, название, номинал в Stars, осколков)
GIFT_CATALOG = {
    "5170145012310081615": ("💝", "Сердце с бантом", 15, 15),
    "5170233102089322756": ("🧸", "Мишка", 15, 15),
    "5170250947678437525": ("🎁", "Коробка с бантом", 25, 25),
    "5168103777563050263": ("🌹", "Красная роза", 25, 25),
    "5170144170496491616": ("🎂", "Торт ко дню рождения", 50, 50),
    "5170314324215857265": ("💐", "Букет", 50, 50),
    "5170564780938756245": ("🚀", "Ракета", 50, 50),
    "6028601630662853006": ("🍾", "Бутылка шампанского", 50, 50),
    "5168043875654172773": ("🏆", "Кубок", 100, 100),
    "5170690322832818290": ("💍", "Кольцо с бриллиантом", 100, 100),
    "5170521118301225164": ("💎", "Алмаз", 100, 100),
}
# Сколько звёзд бот тратит на подарок (номинал Telegram). Столько же нужно
# отдать игроку осколками.
# Маржа 1.0 — сознательно. Игрок конвертирует подарок в Stars с потерей 15%
# (Telegram отдаёт 85% номинала), поэтому цена в осколках равна номиналу: игрок
# получает 0.85 номинала звёздами и выводит их через Fragment в GRAM.
# Проект при этом в плюсе: осколки игрок оплачивает по ~$0.02 за звезду,
# а подарок бот покупает на Stars, выведенных по $0.013.
GIFT_MARGIN = 1.0

# Telegram конвертирует обычный подарок обратно в Stars с потерей 15%:
# gift.convert_stars < gift.stars. Это потолок «гарантированного» выхода
# для игрока, если он не продаёт подарок, а конвертирует его в звёзды.
GIFT_CONVERT_RATE = 0.85

# Курсы для расчёта ликвидности.
STAR_WITHDRAW_USD = 0.013      # $ за звезду при выводе через Fragment
STAR_BUY_USD = 0.020           # $ за звезду при покупке в приложении

# Живые флоры коллекционных подарков (Fragment, ежедневная выгрузка).
# Источник публичный, токены не нужны: при сбое работаем на последнем кэше.
GRAM_FLOOR_URL = ("https://raw.githubusercontent.com/Atlannn/telegram-gifts-floor-price/"
                   "main/data/latest.json")
GRAM_FLOOR_TTL = 6 * 3600

# ============ ПОДПИСКА ПОДТЕКСТ+ ============
# Регулярная подписка за Telegram Stars (Bot API 8.0+). Это основной
# предсказуемый доход: разовая покупка зависит от настроения игрока,
# подписка платит каждый месяц.
#
# Экономика: разработчик получает $0.013 за звезду, игрок платит $0.014-0.020.
# Тариф 100 Stars = $1.30/мес выручки, 250 = $3.25, 500 = $6.50.
# Railway Hobby (~$5/мес) окупается 4 платящими подписчиками на тарифе 250.
SUBSCRIPTION_TIERS = {
    # id: (stars, название, описание, бонус)
    # Обещаем только то, что реально начисляется. Лишние «жизни в неделю»
    # и «бустеры в месяц» убраны: показ невыполнимого бонуса — это отзывы,
    # а не выручка.
    "sub_plus": (100, "ПОДТЕКСТ+",
                 "Без рекламы, +5 осколков в день, приоритет в конкурсе",
                 {"noads": True, "shards_day": 5}),
    "sub_pro": (250, "ПОДТЕКСТ+ ПРО",
                "Всё из PLUS: +15 осколков в день, +1 жизнь в неделю",
                {"noads": True, "shards_day": 15, "lives_week": 1}),
    "sub_max": (500, "ПОДТЕКСТ+ МАКС",
                "Всё из ПРО: +40 осколков в день, 3 жизни в неделю",
                {"noads": True, "shards_day": 40, "lives_week": 3}),
}
# Подписка продлевается на 30 дней. Метаданные о покупке приходят в
# successful_payment; Telegram сам списывает следующий платёж.
SUB_DAYS = 30


def sub_tier(payload):
    return SUBSCRIPTION_TIERS.get(payload)


def ads_disabled(acc):
    """Реклама отключена, если куплена вечная отключка ИЛИ активна подписка."""
    if acc.get("noads"):
        return True
    _sub = acc.get("sub") or {}
    if int(_sub.get("until", 0) or 0) > int(time.time()) and _sub.get("noads"):
        return True
    return False


def sub_state(acc):
    """Текущее состояние подписки игрока: активна ли, до какого, что внутри."""
    st = acc.get("sub") or {}
    until = int(st.get("until", 0) or 0)
    active = until > int(time.time())
    return {"active": active, "until": until,
            "tier": st.get("tier") if active else None,
            "days_left": max(0, (until - int(time.time())) // 86400) if active else 0}


# Новогодняя тематика: подарки с seasonally-меткой (сезонная витрина).
GIFT_SEASON = {"🎂", "🎁", "🚀", "🍾", "💝"}

# ================= ПУЛ ОСКОЛКОВ =================
# Бустер — это не «множитель шанса», а ВЗНОС В ОБЩИЙ ПУЛ. Деньги покупателей
# бустера перетекают к активным игрокам; часть пула остаётся проекту.
# Ключевое требование: и мы в плюсе, и активные игроки в плюсе.
POOL_MARGIN = 0.30              # 30% вклада остаётся проекту (гарантированный плюс)
POOL_ACTIVE_SHARE = 0.70        # 70% -> общий котёл для ВСЕХ игроков
POOL_BUYER_SHARE = 0.30         # 30% -> котёл покупателей бустера
# Гарантированный возврат покупателю: фиксированная доля от взноса,
# начисляется осколками сразу и БЕЗ случайности. Это не лотерея,
# а детерминированный частичный возврат — так требуют правила Telegram
# для 18+ аудитории (никаких обещаний выигрыша).
POOL_BUYER_GUARANTEE = 0.35     # 35% взноса возвращается покупателю осколками
POOL_EVENT_RATE = 0.02          # в среднем 2% остатка пула выплачивается за событие
POOL_RAND_LO = 0.6              # нижняя граница рендома выплаты
POOL_RAND_HI = 1.4              # верхняя граница рендома выплаты
POOL_BUYER_CAP = 1.667          # покупатель может выиграть из пула максимум 1.667x своего взноса
                                 # (потратил 300 -> может заработать до 500)
POOL_MIN_SHARE = 1              # минимальная выплата, единицы = десятые звезды

def _pool_path():
    return os.path.join(os.path.dirname(STORE) or ".", "pool.json")

def _pool_load():
    try:
        with open(_pool_path(), encoding="utf-8") as f:
            p = json.load(f)
        if isinstance(p, dict):
            p.setdefault("active", 0)
            p.setdefault("buyer", 0)
            p.setdefault("margin", 0)
            p.setdefault("contrib", {})
            p.setdefault("earned", {})
            # Миграция со старой однокотловой модели.
            if "distributable" in p:
                d = int(p.pop("distributable", 0) or 0)
                p["active"] = int(p.get("active", 0)) + int(d * POOL_ACTIVE_SHARE)
                p["buyer"] = int(p.get("buyer", 0)) + d - int(d * POOL_ACTIVE_SHARE)
            return p
    except Exception:
        pass
    return {"active": 0, "buyer": 0, "margin": 0, "contrib": {}, "earned": {}}

def _pool_save(p):
    try:
        _store_dir()
        tmp = _pool_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(p, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _pool_path())
    except Exception as e:
        logging.warning("pool save failed: %s", e)

def pool_contribute(uid, stars_paid):
    """Взнос в пул. Расклад: 30% — маржа проекта, 70% — игрокам.

    Из 70%: 35% от взноса возвращается покупателю ГАРАНТИРОВАННО
    (детерминированно, осколками, без RNG), остальное уходит в общий
    котёл активных игроков. Никаких случайных выплат и обещаний
    выигрыша — это критично для правил Telegram и 18+ аудитории.
    """
    units = int(stars_paid) * STAR_PACK
    dist = int(round(units * (1.0 - POOL_MARGIN)))
    margin = units - dist
    guarantee = int(round(units * POOL_BUYER_GUARANTEE))
    to_active = dist - guarantee
    p = _pool_load()
    p["active"] = int(p.get("active", 0)) + to_active
    p["buyer"] = int(p.get("buyer", 0))
    p["margin"] = int(p.get("margin", 0)) + margin
    p["contrib"][str(uid)] = int(p["contrib"].get(str(uid), 0)) + units
    _pool_save(p)
    return dist, margin, guarantee

def _pool_activity(acc):
    """Вес активности: серия дней и активный бустер."""
    a = 1.0 + min(1.0, int(acc.get("streak", 0) or 0) / 10.0)
    st = acc.get("star") or {}
    if int(st.get("boost_until", 0)) > int(time.time()):
        a *= 1.8
    return a

def pool_payout(acc, reason="event"):
    """Выплата из общего пула активному игроку.

    Общий котёл (60%) делится между ВСЕМИ активными игроками — выигрывают и те,
    кто бустер не покупал. Котёл покупателей (40%) — только для купивших, и с
    потолком POOL_BUYER_CAP от взноса. Возвращает выплаченные единицы."""
    p = _pool_load()
    uid = str(acc.get("uid") or "")
    st = acc.setdefault("star", {})
    contributed = int(st.get("contributed", 0))
    earned = int(st.get("pool_earned", 0))
    # Покупатель может получить из общего котла не больше своей гарантии
    # плюс небольшой бонус за активность (потолок POOL_BUYER_CAP).
    allowed_total = int(contributed * POOL_BUYER_CAP) if contributed else 10 ** 9
    allowed = allowed_total - earned
    if allowed <= 0:
        return 0
    mult = random.uniform(POOL_RAND_LO, POOL_RAND_HI)
    activity = _pool_activity(acc)
    total = 0
    act = int(p.get("active", 0))
    if act > 0:
        s1 = int(act * POOL_EVENT_RATE * mult * activity)
        s1 = max(0, min(s1, act, allowed - total))
        if s1 > 0:
            p["active"] = act - s1
            total += s1
    if total <= 0:
        return 0
    p.setdefault("earned", {})[uid] = int(p["earned"].get(uid, 0)) + total
    _pool_save(p)
    st["pool_earned"] = earned + total
    shards_add(acc, total, "pool:" + reason)
    return total

def _now():
    return int(time.time())


def star_weekday(offset_h=DAY_TZ_OFFSET):
    """День недели по московскому времени: 0=пн .. 6=вс."""
    return time.gmtime(time.time() + offset_h * 3600).tm_wday


def fall_window_open(now_ts=None):
    """Звездопад открыт ли сейчас (пятница 18:00 -> воскресенье 23:59 МСК)."""
    now_ts = now_ts or _now()
    tm = time.gmtime(now_ts + DAY_TZ_OFFSET * 3600)
    wd = tm.tm_wday          # 0=пн
    hh = tm.tm_hour
    if wd == 4 and hh >= FALL_FROM_HOUR:      # пятница вечер
        return True
    if wd in (5, 6):                          # сб, вс — весь день
        return True
    if wd == 0 and hh < 8:                    # понедельник до 08:00 — хвост окна
        return True
    return False


def fall_next_open_text():
    """Человеческая дата следующего окна Звездопада."""
    tm = time.gmtime(_now() + DAY_TZ_OFFSET * 3600)
    wd = tm.tm_wday
    days = (4 - wd) % 7
    if wd == 4 and tm.tm_hour >= FALL_FROM_HOUR:
        days = 7
    names = ["понедельник", "вторник", "среда", "четверг", "пятница",
             "суббота", "воскресенье"]
    return names[(4 + days) % 7]


_GRAM_MARKET = {"data": None, "ts": 0}


def gram_market():
    """Живая картинка рынка коллекционных подарков: флоры и дата снимка.

    Нужна игроку, чтобы понимать, во что превращаются его осколки: обычный
    подарок можно конвертировать в Stars (85% номинала), а коллекционные
    подарки продаются за GRAM на маркетплейсе.
    """
    global _GRAM_MARKET
    now = int(time.time())
    if _GRAM_MARKET["data"] and now - _GRAM_MARKET["ts"] < GRAM_FLOOR_TTL:
        return _GRAM_MARKET["data"]
    data = _GRAM_MARKET["data"]
    try:
        req = urllib.request.Request(GRAM_FLOOR_URL, headers={"User-Agent": "podtekst-bot"})
        raw = json.loads(urllib.request.urlopen(req, timeout=15).read().decode("utf-8"))
        floors = raw.get("floors") or {}
        vals = sorted(float(v) for v in floors.values()
                      if isinstance(v, (int, float)) and v > 0)
        if vals:
            data = {"date": raw.get("date"), "count": len(vals),
                    "min_gram": round(vals[0], 2),
                    "median_gram": round(vals[len(vals) // 2], 2),
                    "max_gram": round(vals[-1], 2),
                    "low_gram": round(vals[max(0, len(vals) // 10)], 2),
                    "top_gram": round(vals[min(len(vals) - 1, len(vals) // 10)], 2),
                    "source": raw.get("source") or "fragment.com"}
    except Exception as e:  # noqa: BLE001
        logging.warning("gram market fetch failed: %s", e)
    if data:
        _GRAM_MARKET["data"] = data
        _GRAM_MARKET["ts"] = now
    return data or {}


def gift_window_open(now_ts=None):
    """Окно обмена на подарки: 1-е..7-е число каждого месяца."""
    now_ts = now_ts or _now()
    tm = time.gmtime(now_ts + DAY_TZ_OFFSET * 3600)
    return tm.tm_mday <= GIFT_WINDOW_DAYS


def gift_window_next_text():
    tm = time.gmtime(_now() + DAY_TZ_OFFSET * 3600)
    if tm.tm_mday <= GIFT_WINDOW_DAYS:
        days = GIFT_WINDOW_DAYS - tm.tm_mday
        return f"через {days} дн." if days else "сегодня"
    return "в начале следующего месяца"


def shards_of(acc):
    """Счёт осколков в десятых."""
    return int((acc.get("star") or {}).get("shards", 0))


def shards_add(acc, units, reason=""):
    """Начислить осколки (в десятых). Возвращает фактически начисленное."""
    st = acc.setdefault("star", {})
    st["shards"] = max(0, int(st.get("shards", 0)) + int(units))
    if reason:
        log = st.setdefault("log", [])
        log.append({"u": int(units), "r": reason, "t": _now()})
        if len(log) > 60:
            del log[:-60]
    return int(units)


def shards_spend(acc, units):
    st = acc.setdefault("star", {})
    have = int(st.get("shards", 0))
    if have < units:
        return False
    st["shards"] = have - units
    return True


def _roll_shard_units():
    """Осколок 0.1..1.0 звезды, смещённый к мелким."""
    r = random.random() ** 1.6      # чаще мелкие куски
    units = SHARD_MIN + int(r * (SHARD_MAX - SHARD_MIN + 1))
    return max(SHARD_MIN, min(SHARD_MAX, units))


def star_chance_for(acc):
    """Итоговый шанс выпадения осколка за событие."""
    base = FALL_BASE_CHANCE
    if fall_window_open():
        base *= FALL_BOOST_MULT
    # Антифарм: чем больше уже накоплено, тем ниже шанс.
    held = shards_of(acc)
    if held >= 60:
        base *= 0.25
    elif held >= 30:
        base *= 0.5
    # Бустер «Звездопад+»
    st = acc.setdefault("star", {})
    if int(st.get("boost_until", 0)) > _now():
        base *= 1.8
    return max(0.0, min(0.95, base))


def try_drop_shard(acc, reason="event"):
    """Попытка выпадения осколка. Возвращает (units, reason) или (0, "")."""
    chance = star_chance_for(acc)
    if random.random() >= chance:
        return 0, ""
    units = _roll_shard_units()
    shards_add(acc, units, reason)
    return units, reason


def star_view(acc):
    """Снимок состояния звёзд для клиента."""
    st = acc.setdefault("star", {})
    units = int(st.get("shards", 0))
    return {
        "shards": units,
        "stars": round(units / STAR_PACK, 2),
        "fall_open": fall_window_open(),
        "fall_next": fall_next_open_text(),
        "gift_open": gift_window_open(),
        "gift_next": gift_window_next_text(),
        "boost_until": int(st.get("boost_until", 0)),
        "chance": round(star_chance_for(acc), 3),
        "contributed": int(st.get("contributed", 0)),
        "pool_earned": int(st.get("pool_earned", 0)),
        "pool_cap": int(int(st.get("contributed", 0)) * POOL_BUYER_CAP),
    }


def price_units(stars):
    """Цена подарка в осколках: номинал × 10 единиц × запас на просадку."""
    return int(round(int(stars) * STAR_PACK * GIFT_MARGIN))


def gift_catalog_view():
    """Витрина подарков с ценами в осколках.

    Для каждого подарка показываем, во что он превращается у игрока:
      * конвертация в Stars — Telegram отдаёт 85% номинала (гарантированный
        выход, но звёзды не вывести в фиат);
      * апгрейд до коллекционного — подарок становится NFT, который продаётся
        за GRAM на маркетплейсе (комиссия Telegram 10%).
    """
    out = []
    for gid, (emo, name, stars, shards) in GIFT_CATALOG.items():
        out.append({"id": gid, "emoji": emo, "name": name, "stars": stars,
                    "price": price_units(stars), "season": emo in GIFT_SEASON,
                    "convert_stars": int(round(stars * GIFT_CONVERT_RATE)),
                    "upgrade_stars": int(round(stars * GIFT_CONVERT_RATE)),
                    "note": "Конвертация в звёзды: %d ⭐ (85%%). Коллекционный — продажа за GRAM." %
                            int(round(stars * GIFT_CONVERT_RATE))})
    out.sort(key=lambda x: (x["stars"], x["name"]))
    return out

ALIAS = {"buy": "coins_100", "buy:lives": "lives", "buy:noads": "noads",
          "buy:contest_entry": "contest_entry", "buy:fall_boost": "fall_boost",
          "buy:freeze_stars": "freeze_stars",
          "buy:gen_pack": "gen_pack", "buy:gen_pack_big": "gen_pack_big"}

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
    # Звёздный купон: -20 звёзд на следующую покупку 100 монет.
    a1["ref_coupon"] = True
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
        await send_invoice(message.chat.id, "coins_100", message.from_user.id)
        return
    if payload == "buy:lives":
        await send_invoice(message.chat.id, "lives", message.from_user.id)
        return
    if payload == "buy:noads":
        await send_invoice(message.chat.id, "noads", message.from_user.id)
        return
    if payload == "buy:freeze":
        await send_invoice(message.chat.id, "freeze_stars", message.from_user.id)
        return
    if payload == "buy:contest_entry":
        await send_invoice(message.chat.id, "contest_entry", message.from_user.id)
        return
    if payload == "ref":
        await referral(message)
        return
    # Приглашение в дуэль: t.me/<bot>?start=duel_<код>.
    # Гость жмёт "Принять вызов" — и мы регистрируем его в бою.
    if payload.startswith("duel_"):
        code = payload[5:].strip()
        ok, txt = await accept_duel_invite(message, code)
        await message.answer(txt, parse_mode="HTML")
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
    # Звёздная рефералка: осколки. Начисляются только если обе стороны
    # подписаны на канал и не превышен суточный лимит.
    if payload.startswith("ref"):
        try:
            st_res = await apply_referral_star(message.from_user.id, payload)
            if st_res:
                kind, inviter, g_inv, g_new = st_res
                if kind == "ok" and (g_inv or g_new):
                    try:
                        await bot.send_message(
                            inviter,
                            f"⭐ Твой друг пришёл по ссылке и остался с нами. "
                            f"Осколок звёзды: +{g_inv/10:.1f} ⭐"
                            + (f"\n🎁 Он подписался на канал — получи "
                               f"+{REF_ACTIVATION_SHARDS/10:.1f} ⭐ за активацию."
                               if g_inv else ""))
                    except Exception as e:
                        logging.warning("ref star notify failed: %s", e)
                    await message.answer(
                        f"🎁 Привет! Тебе +20 🪙 и осколок звёзды "
                        f"+{g_new/10:.1f} ⭐ за то, что пришёл по ссылке друга. "
                        "Подпишись на канал — и пригласившему тоже падет звезда.")
                elif kind == "need_sub_new":
                    await message.answer(
                        "⭐ Чтобы получить осколок звёзды, подпишись на канал "
                        "и потом снова нажми «Пригласить друга».")
                elif kind == "need_sub_inv":
                    await message.answer(
                        "⭐ Пригласивший ещё не подписан на канал — осколок "
                        "зачислю, как только он подпишется.")
                elif kind == "cap":
                    await message.answer(
                        "⭐ На сегодня лимит осколков с рефералки исчерпан. "
                        "Завтра продолжим ✨")
        except Exception as e:
            logging.warning("referral star failed: %s", e)
    await message.answer(
        "<b>ПОДТЕКСТ</b>\n\nОн сказал «ок» — а что он имел в виду на самом деле?\n\n"
        "Квиз с подтекстом · Три в ряд · Челлендж дня у всех одинаковый.\n\n"
        "Играй — и не тормози.\n\n"
        f"📢 <b>Подпишись на канал</b> — новые челленджи первыми и "
        f"<b>+{SUB_REWARD} 🪙</b> на кошелёк.\n"
        f"🎉 И играй в розыгрыш: 1 место — 100 ⭐, 2 — 50 ⭐, 3 — 25 ⭐. "
        f"Каждые 30 очков активности = 1 билет, бесплатно. Финал — 15 декабря.\n\n"
        f"🤖 ИИ-помощник Mira даст <b>+{PROMO_REWARD} 🪙</b> — забери бонус "
        f"в профиле, в разделе «Mira».", reply_markup=kb(src_code))

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
        f"⭐ Если друг подпишется на канал — тебе <b>+1 звезда</b> "
        f"(осколками), ему +0.5 звезды сразу.\n"
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
        f"Подключи Mira и вернись в игру — начислю <b>+{PROMO_REWARD} 🪙</b> "
        "(один раз, автоматически).\n\n"
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
                         "/mira — ИИ-помощник Mira + бонус в игре\n"
                         "/starbalance — баланс звёзд (бота и аккаунта)\n"
                         "/transferstars 500 — перевести 500 звёзд боту",
                         reply_markup=kb())

@dp.message(Command("buy"))
async def buy(message: types.Message):
    await send_invoice(message.chat.id, "coins_100", message.from_user.id)


@dp.message(Command("genpack"))
async def genpack_cmd(message: types.Message):
    """Пакет генераций прямо в чате-генераторе."""
    await send_invoice(message.chat.id, "gen_pack", message.from_user.id)


@dp.message(Command("genpackbig"))
async def genpackbig_cmd(message: types.Message):
    """Большой пакет генераций (30 штук)."""
    await send_invoice(message.chat.id, "gen_pack_big", message.from_user.id)

async def send_invoice(chat_id: int, payload: str, user_id=None):
    # Подписка: цена берётся из тарифов, а не из PACKS.
    if payload in SUBSCRIPTION_TIERS:
        stars, title, desc, _bonus = SUBSCRIPTION_TIERS[payload]
        await bot.send_invoice(chat_id=chat_id, title=title,
            description=desc,
            payload=payload, provider_token="", currency="XTR",
            subscription_period=2592000,  # 30 дней в секундах
            prices=[LabeledPrice(label=title, amount=stars)])
        return
    amount, title = PACKS.get(payload, PACKS["coins_100"])
    # Реферальный купон: если у игрока есть непотраченный — подставляем
    # льготный пакет вместо обычного.
    if user_id is not None and payload == "coins_100":
        try:
            d = load_store()
            acc = acc_of(d, user_id)
            if acc.get("ref_coupon"):
                payload = REF_COUPON_PACK
                amount, title = PACKS[REF_COUPON_PACK]
        except Exception:
            pass
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
    elif payload == REF_COUPON_PACK:
        acc["coins"] += 100
        acc["ref_coupon"] = False
    elif payload == "coins_100": acc["coins"] += 100
    elif payload == "coins_500": acc["coins"] += 500
    elif payload == "lives": acc["lives"] += 1
    elif payload == "noads": acc["noads"] = True
    elif payload == "fall_boost":
        acc = acc_of(d, user_id)
        st = acc.setdefault("star", {})
        st["boost_until"] = max(int(st.get("boost_until", 0)), int(time.time()) + FALL_BOOST_PACK_TTL)
        # Взнос в общий пул: 80% — активным игрокам, 20% — проект.
        stars_paid = PACKS.get("fall_boost", (PACK_FALL_BOOST, ""))[0]
        _dist, _margin, _guar = pool_contribute(user_id, stars_paid)
        # Гарантированный возврат: 35% взноса осколками, сразу и без RNG.
        if _guar > 0:
            shards_add(acc, _guar, "boost_guarantee")
        st["guaranteed"] = int(st.get("guaranteed", 0)) + _guar
        st["contributed"] = int(st.get("contributed", 0)) + int(stars_paid) * STAR_PACK
        st["pool_earned"] = int(st.get("pool_earned", 0))
        logging.info("POOL contrib user=%s stars=%s dist=%s margin=%s",
                     user_id, stars_paid, _dist, _margin)
    elif payload in ("gen_pack", "gen_pack_big"):
        acc = acc_of(d, user_id)
        n = GEN_PACK_COUNT if payload == "gen_pack" else GEN_PACK_BIG_COUNT
        acc["gen_left"] = int(acc.get("gen_left", 0)) + n
        acc["gen_total"] = int(acc.get("gen_total", 0)) + n
    elif payload == "freeze_stars":
        acc = acc_of(d, user_id)
        st = acc.setdefault("freeze", {})
        st["tokens"] = int(st.get("tokens", 0)) + 1
    elif payload in SUBSCRIPTION_TIERS:
        # Покупка/продление подписки. Telegram присылает renewed=True при
        # автопродлении — продлеваем от текущего конца, а не от сегодня,
        # чтобы не «съедать» оплаченные дни.
        stars, _name, _desc, bonus = SUBSCRIPTION_TIERS[payload]
        st = acc.setdefault("sub", {})
        now = int(time.time())
        base = int(st.get("until", 0) or 0)
        if base < now:
            base = now
        st["until"] = base + SUB_DAYS * 86400
        st["tier"] = payload
        st["stars_total"] = int(st.get("stars_total", 0)) + stars
        st["shards_day"] = int(bonus.get("shards_day", 0))
        st["noads"] = bool(bonus.get("noads"))
        st["lives_week"] = int(bonus.get("lives_week", 0))
        st["boost_month"] = int(bonus.get("boost_month", 0))
        st["last_pay"] = now
        # ВАЖНО: acc["noads"] — это вечная покупка «Без рекламы» за 350 Stars.
        # Подписка её не даёт, иначе подписчик покупал бы вечную отключку
        # за 100 Stars в месяц. Отключение рекламы по подписке живёт в st["noads"]
        # и отключается вместе с подпиской.
        logging.info("SUB user=%s tier=%s until=%s", user_id, payload, st["until"])
    elif payload == "contest_entry":
        acc = acc_of(d, user_id)
        tier = active_tier(acc)
        if not tier:
            return acc
        c = contest_tickets(acc)
        row = c["tiers"].setdefault(tier["id"], {"points": 0, "paid_tickets": 0, "tickets": 0})
        row["tickets"] = int(row.get("tickets", 0)) + CONTEST_TICKETS_PAID
        row["paid_tickets"] = int(row.get("paid_tickets", 0)) + CONTEST_TICKETS_PAID
    save_store(d)
    return acc

@dp.message(F.successful_payment)
async def paid(message: types.Message):
    p = message.successful_payment
    charge = p.telegram_payment_charge_id
    logging.info("PAYMENT user=%s payload=%s stars=%s charge=%s", message.from_user.id, p.invoice_payload, p.total_amount, charge)
    acc = grant(message.from_user.id, p.invoice_payload)
    d = load_store(); d.setdefault(str(message.from_user.id), {}).setdefault("charges", []).append(
        {"charge": charge, "payload": p.invoice_payload, "at": datetime.now().isoformat()})
    save_store(d)
    await message.answer(f"Оплата прошла — товар уже в кошельке игры.\n\nБаланс: {acc['coins']} монет · {acc['lives']} жизней.", reply_markup=kb())

@dp.message(Command("sub"))
async def sub_cmd(message: types.Message):
    """Тарифы подписки ПОДТЕКСТ+."""
    acc = load_store().get(str(message.from_user.id), {})
    st = sub_state(acc)
    lines = ["ПОДТЕКСТ+ — подписка за Telegram Stars.",
             "Одна подписка заменяет разовые покупки: осколки капают каждый день.",
             ""]
    if st["active"]:
        lines.append(f"✅ Подписка активна ещё {st['days_left']} дн. "
                     f"Уровень: {SUBSCRIPTION_TIERS[st['tier']][1]}")
        lines.append("")
    for tid, (stars, name, desc, bonus) in SUBSCRIPTION_TIERS.items():
        lines.append(f"• {name} — {stars} Stars/мес (~${stars * STAR_WITHDRAW_USD:.2f})")
        lines.append(f"   {desc}")
    lines.append("")
    lines.append("Списание раз в 30 дней, отменить можно в любой момент.")
    rows = [[InlineKeyboardButton(text=f"{SUBSCRIPTION_TIERS[t][1]} · {SUBSCRIPTION_TIERS[t][0]} Stars",
                                  callback_data=f"sub_buy:{t}")] for t in SUBSCRIPTION_TIERS]
    rows.append([InlineKeyboardButton(text="💳 Оплатить", callback_data="sub_pay")])
    await message.answer("\n".join(lines),
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(F.data.startswith("sub_buy:"))
async def sub_buy_cb(q: types.CallbackQuery):
    tid = q.data.split(":", 1)[1]
    if tid not in SUBSCRIPTION_TIERS:
        await q.answer("Тариф не найден", show_alert=True)
        return
    try:
        await send_invoice(q.from_user.id, tid, q.from_user.id)
    except Exception as e:
        logging.warning("sub invoice failed: %s", e)
        await q.answer("Не удалось открыть оплату. Попробуй ещё раз.", show_alert=True)
        return
    await q.answer("Открываю оплату…")


@dp.message(Command("balance"))
async def balance(message: types.Message):
    acc = load_store().get(str(message.from_user.id), {"coins": 0, "lives": 0, "noads": False})
    await message.answer(f"Кошелёк:\n{acc['coins']} монет · {acc['lives']} жизней · без рекламы: {'да' if acc.get('noads') else 'нет'}", reply_markup=kb())

def revoke(user_id, payload):
    """Отбирает товар, выданный за платёж. Возвращает True, если что-то снято."""
    d = load_store(); acc = d.get(str(user_id))
    if not acc:
        return False
    if payload == "test_1star":
        acc["coins"] = max(0, acc.get("coins", 0) - 1); return True
    if payload == REF_COUPON_PACK:
        acc["coins"] = max(0, acc.get("coins", 0) - 100); acc["ref_coupon"] = False; return True
    if payload == "coins_100":
        acc["coins"] = max(0, acc.get("coins", 0) - 100); return True
    if payload == "coins_500":
        acc["coins"] = max(0, acc.get("coins", 0) - 500); return True
    if payload == "lives":
        acc["lives"] = max(0, acc.get("lives", 0) - 1); return True
    if payload == "noads":
        acc["noads"] = False; return True
    if payload == "freeze_stars":
        st = acc.setdefault("freeze", {})
        if int(st.get("tokens", 0)) > 0:
            st["tokens"] = int(st["tokens"]) - 1
            return True
        return False
    if payload == "fall_boost":
        st = acc.setdefault("star", {})
        st["boost_until"] = 0
        # Возвращаем взнос из общего пула: 80% возвращается в пул, 20% — маржа
        # проекта (её Telegram возвращает как Stars на баланс бота, а не нам).
        stars_paid = PACKS.get("fall_boost", (PACK_FALL_BOOST, ""))[0]
        units = int(stars_paid) * STAR_PACK
        st["contributed"] = max(0, int(st.get("contributed", 0)) - units)
        pp = _pool_load()
        back = int(round(units * (1.0 - POOL_MARGIN)))
        guarantee = int(round(units * POOL_BUYER_GUARANTEE))
        to_active = back - guarantee
        pp["active"] = max(0, int(pp.get("active", 0)) - to_active)
        pp["buyer"] = max(0, int(pp.get("buyer", 0)))
        pp["margin"] = max(0, int(pp.get("margin", 0)) - (units - back))
        _g = int(st.get("guaranteed", 0))
        if _g > 0:
            st["guaranteed"] = max(0, _g - guarantee)
            shards_add(acc, -min(_g, guarantee), "boost_revoke")
        _pool_save(pp)
        return True
    if payload == "contest_entry":
        tier = active_tier(acc)
        if tier:
            c = contest_tickets(acc)
            row = c["tiers"].setdefault(tier["id"], {"points": 0, "paid_tickets": 0, "tickets": 0})
            take = min(int(row.get("paid_tickets", 0)), CONTEST_TICKETS_PAID)
            row["paid_tickets"] = max(0, int(row.get("paid_tickets", 0)) - take)
            row["tickets"] = max(0, int(row.get("tickets", 0)) - take)
            return True
        return False
    return False


@dp.message(Command("refundlast"))
async def refundlast(message: types.Message):
    d = load_store(); acc = d.get(str(message.from_user.id))
    if not acc or not acc.get("charges"):
        await message.answer("Нечего возвращать — нет завершённых платежей."); return
    last = acc["charges"][-1]
    # Старые записи — просто строка с charge id, без payload
    if isinstance(last, str):
        charge, payload = last, None
    else:
        charge, payload = last.get("charge"), last.get("payload")
    try:
        await bot.refund_star_payment(user_id=message.from_user.id, telegram_payment_charge_id=charge)
    except Exception as e:
        logging.warning("refund failed: %s", e)
        await message.answer(f"Не получилось вернуть: {e}")
        return
    # Звезда возвращена — товар надо отобрать, иначе игрок получит и звёзды и товар.
    if payload:
        revoke(message.from_user.id, payload)
        d = load_store(); a = d.get(str(message.from_user.id))
        if a: a["charges"].pop()
        save_store(d)
        await message.answer("Звезда возвращена на твой баланс, товар за неё отобран.")
    else:
        await message.answer("Звезда возвращена на твой баланс.")

# ================= БИЗНЕС-ПОДКЛЮЧЕНИЕ: ПЕРЕВОД STARS БОТУ =================
# Telegram не даёт боту забрать Stars с аккаунта пользователя напрямую.
# Единственный путь — бизнес-подключение: пользователь подключает свой
# аккаунт боту (BotFather → Business), и тогда по business_connection_id
# можно перевести Stars с баланса аккаунта на баланс бота.
# Подключение хранится в store.json, чтобы переживать перезапуски.

def biz_conn_key(user_id):
    return "bizconn:" + str(user_id)


def save_biz_conn(user_id, conn_id, rights=None):
    d = load_store()
    d[biz_conn_key(user_id)] = {"conn": conn_id, "rights": rights or {},
                                "ts": int(time.time())}
    save_store(d)


def get_biz_conn(user_id):
    d = load_store()
    rec = d.get(biz_conn_key(user_id)) or {}
    return rec.get("conn")


@dp.business_connection()
async def on_business_connection(event):
    """Telegram прислал бизнес-подключение: сохраняем его id.

    В aiogram 3.x в dp.business_connection() приходит сам объект
    BusinessConnection (не обёртка Update), поэтому берём поля прямо из event.
    """
    bc = getattr(event, "business_connection", None) or event
    try:
        rights = bc.rights
    except Exception:
        rights = None
    can_transfer = False
    can_reply = False
    try:
        can_transfer = bool(rights.can_transfer_stars) if rights else False
        can_reply = bool(rights.can_reply) if rights else False
    except Exception:
        pass
    try:
        uid = bc.user.id
        conn_id = bc.id
    except Exception:
        logging.exception("business_connection: bad payload")
        return
    save_biz_conn(uid, conn_id, {"can_transfer_stars": can_transfer,
                                 "can_reply": can_reply,
                                 "is_enabled": bool(getattr(bc, "is_enabled", True))})
    logging.info("BUSINESS_CONN user=%s id=%s can_transfer=%s can_reply=%s",
                 uid, conn_id, can_transfer, can_reply)
    try:
        await bot.send_message(uid,
            "Бизнес-подключение принято ✅\n"
            "Перевести звёзды: /transferstars 500\n"
            "Проверить баланс: /starbalance")
    except Exception:
        logging.warning("business_connection: notify failed", exc_info=True)


@dp.message(Command("transferstars"))
async def transferstars(message: types.Message):
    """Перевод Stars с аккаунта пользователя на баланс бота (нужно бизнес-подключение)."""
    conn = get_biz_conn(message.from_user.id)
    if not conn:
        await message.answer(
            "Сначала нужно бизнес-подключение.\n\n"
            "В @BotFather: /mybots → @podtekst_game_bot → Business → включи "
            "и подключи аккаунт. После этого вернись сюда и повтори команду.",
            reply_markup=kb())
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Сколько звёзд перевести? Формат: /transferstars 500")
        return
    try:
        count = int(args[1])
    except ValueError:
        await message.answer("Нужно целое число. Формат: /transferstars 500")
        return
    if count < 1 or count > 10000:
        await message.answer("Перевести можно от 1 до 10000 звёзд за раз.")
        return
    try:
        bal = await bot.get_business_account_star_balance(business_connection_id=conn)
        have = int(getattr(bal, "amount", 0) or 0)
    except Exception as e:
        logging.warning("biz balance failed: %s", e)
        await message.answer(f"Не получилось прочитать баланс: {e}")
        return
    if have < count:
        await message.answer(
            f"На аккаунте {have} звёзд, а перевести нужно {count}. "
            "Пополни баланс звёзд и повтори.")
        return
    try:
        ok = await bot.transfer_business_account_stars(
            business_connection_id=conn, star_count=count)
    except Exception as e:
        logging.warning("transfer stars failed: %s", e)
        await message.answer(f"Перевод не прошёл: {e}")
        return
    logging.info("TRANSFER_STARS user=%s count=%s ok=%s", message.from_user.id, count, ok)
    try:
        mine = await bot.get_my_star_balance()
        mine_amt = int(getattr(mine, "amount", 0) or 0)
    except Exception:
        mine_amt = None
    tail = f"\nБаланс бота теперь: {mine_amt} ⭐" if mine_amt is not None else ""
    await message.answer(f"Перевела {count} ⭐ на баланс бота.{tail}", reply_markup=kb())


@dp.message(Command("starbalance"))
async def starbalance(message: types.Message):
    """Баланс Stars: свой, бота и подключения."""
    try:
        mine = await bot.get_my_star_balance()
        mine_amt = int(getattr(mine, "amount", 0) or 0)
    except Exception as e:
        mine_amt = f"ошибка: {e}"
    conn = get_biz_conn(message.from_user.id)
    acc_line = "бизнес-подключения нет"
    if conn:
        try:
            bal = await bot.get_business_account_star_balance(business_connection_id=conn)
            acc_line = f"на аккаунте: {int(getattr(bal, 'amount', 0) or 0)} ⭐"
        except Exception as e:
            acc_line = f"подключение есть, но баланс не прочитать: {e}"
    await message.answer(
        f"Баланс бота: {mine_amt} ⭐\n{acc_line}", reply_markup=kb())


@dp.message(lambda m: m.content_type == types.ContentType.WEB_APP_DATA)
async def app_data(message: types.Message):
    data = (message.web_app.data or "").strip()
    logging.info("APPDATA user=%s data=%s", message.from_user.id, data)
    if data in ALIAS:
        await send_invoice(message.chat.id, ALIAS[data], message.from_user.id)
    elif data.startswith("buy:"):
        await send_invoice(message.chat.id, data[4:], message.from_user.id)
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
        # Метка приглашения в дуэль — не рекламный источник, в статистику не идёт.
        if raw.startswith("duel_"):
            return "direct"
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
    # Осколки звёзд. shards — целые десятые; 10 = одна звезда.
    acc.setdefault("star", {"shards": 0, "boost_until": 0, "contributed": 0, "pool_earned": 0})
    acc["uid"] = str(uid)
    # ВАЖНО: если отметки регенерации не было, выставляем текущее время.
    # Раньше стоял 0, и regen_lives сразу начислял 9 жизней — урон от атак
    # не держался вообще.
    if not acc.get("lives_ts"):
        acc["lives_ts"] = int(time.time())
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
# --- Streak Freeze: заморозка серии, чтобы пропуск дня не обнулял прогресс ---


DAY_REWARD = 20          # монет за простое заличие в игре в день
DAY_BONUS_EVERY = 3     # каждые 3 дня — бонус
DAY_BONUS = 60
DAY_STREAK_BONUS = 150  # бонус на 7-й день серии

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
        # ВАЖНО: возвращаем ровно 4 значения — столько же распаковывает
        # api_visit. Раньше здесь было 3, и КАЖДЫЙ повторный вход в игру
        # в тот же день падал с 500 (ValueError при распаковке).
        return False, 0, int(acc.get("day_streak", 0)), 0
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
    # Бонус подписчика: осколки капают раз в сутки, пока подписка активна.
    # Начисляем здесь же — это единственная точка, где гарантирован один
    # вход в ссутки, значит бонус нельзя накопить и сорвать пачкой.
    _sub = acc.get("sub") or {}
    if int(_sub.get("until", 0) or 0) > int(time.time()):
        sub_bonus = int(_sub.get("shards_day", 0) or 0)
        if sub_bonus > 0:
            shards_add(acc, sub_bonus, "sub_daily")
            acc["sub_shards_total"] = int(acc.get("sub_shards_total", 0)) + sub_bonus
        # Жизни по подписке: раз в 7 дней серии, но не чаще одного раза
        # в неделю (иначе игрок с длинной серией фармил бы жизни каждый день).
        _lw = int(_sub.get("lives_week", 0) or 0)
        if _lw > 0 and streak % 7 == 0:
            _last_lw = int(acc.get("sub_lives_last", 0) or 0)
            _now_ts = int(time.time())
            if _now_ts - _last_lw >= 6 * 86400:
                acc["lives"] = min(9, int(acc.get("lives", 5)) + _lw)
                acc["sub_lives_last"] = _now_ts
    if streak >= 7:
        unlock(acc, "streak7")
    return True, reward, streak, penalty

LIFE_REGEN_SEC = 1800
ANSWER_COOLDOWN = 3600  # один и тот же вопрос — раз в час
ANSWER_MAX_ATTEMPTS = 3  # попыток на вопрос в пределах часа  # одна жизнь каждые 30 минут

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
    v = {"coins": int(acc.get("coins", 0) or 0),
         "lives": int(acc.get("lives", 0) or 0),
         "noads": ads_disabled(acc),
         "streak": int(acc.get("streak", 0) or 0),
         "best": int(acc.get("best", 0) or 0),
         "level": int(acc.get("level", 1) or 1)}
    try:
        v["sub"] = sub_state(acc)
    except Exception:
        pass
    try:
        _fz = streak_freeze_state(acc)
        v["freeze_tokens"] = int(_fz.get("tokens", 0))
        v["freeze_max"] = FREEZE_MAX
        v["freeze_cost_shards"] = FREEZE_COST_SHARDS
    except Exception:
        v["freeze_tokens"] = 0
        v["freeze_max"] = FREEZE_MAX
        v["freeze_cost_shards"] = FREEZE_COST_SHARDS
    try:
        v.update(star_view(acc))
    except Exception:
        pass
    return v

def streak_freeze_state(acc, today=None):
    """Состояние заморозок серии: сколько есть, когда последняя была выдана."""
    today = today or today_key()
    st = acc.setdefault("freeze", {})
    tokens = int(st.get("tokens", 0))
    granted = str(st.get("granted", "") or "")
    # Раз в неделю - бесплатная заморозка (больше FREEZE_MAX не копится).
    if not granted or day_shift(granted, FREEZE_FREE_EVERY) <= today:
        tokens = min(FREEZE_MAX, tokens + 1)
        st["tokens"] = tokens
        st["granted"] = today
    st.setdefault("tokens", tokens)
    return st


def streak_freeze_buy(acc, pay="shards"):
    """Докупить заморозку за осколки. Возвращает (ok, cost, error)."""
    st = streak_freeze_state(acc)
    if int(st.get("tokens", 0)) >= FREEZE_MAX:
        return False, 0, "max"
    cost = FREEZE_COST_SHARDS
    if shards_of(acc) < cost:
        return False, cost, "not enough shards"
    shards_add(acc, -cost, "freeze_buy")
    st["tokens"] = int(st.get("tokens", 0)) + 1
    return True, cost, ""


def add_coins(uid, amount):
    d = load_store(); acc = acc_of(d, uid); acc["coins"] = max(0, acc["coins"] + amount)
    save_store(d); return acc


async def _subscribed(uid):
    """Подписан ли пользователь на канал. None = не удалось проверить."""
    if not CHAT_ID:
        return True
    try:
        member = await bot.get_chat_member(CHAT_ID, uid)
    except Exception as e:
        logging.info("get_chat_member(%s): %s", uid, e)
        return None
    st = getattr(member, "status", "")
    return st in ("member", "administrator", "creator", "restricted")


async def apply_referral_star(user_id, payload):
    """Рефералка со звёздами: 0.5 звезды пригласившему, 0.3 — новому.

    Условия, иначе это чистый фарм:
      * приглашённый должен быть подписан на канал;
      * пригласивший тоже должен быть подписан (иначе «привёл и отписался»);
      * в сутки начисляется не больше REF_DAILY_CAP осколков;
      * осколки начисляются один раз за пару.
    """
    if not payload or not payload.startswith("ref"):
        return None
    try:
        inviter = int(payload[3:])
    except Exception:
        return None
    if inviter == user_id:
        return None
    d = load_store()
    # Своя метка для звёздной рефералки: общая d["ref:uid"] ставится
    # монетной рефералкой раньше, и из-за этого звёздная часть не
    # начислялась вовсе - "приведи друга и получи звезду" не работало.
    if d.get(f"ref_star:{user_id}"):
        return None
    sub_new = await _subscribed(user_id)
    sub_inv = await _subscribed(inviter)

    st_day = acc_of(d, inviter).setdefault("star", {}).setdefault("ref_day", {})
    today = today_key()
    if st_day.get("d") != today:
        st_day["d"] = today
        st_day["u"] = 0
    used = int(st_day.get("u", 0))
    room = max(0, REF_DAILY_CAP - used)
    if room <= 0:
        return ("cap", inviter, 0, 0)

    a1 = acc_of(d, inviter); a2 = acc_of(d, user_id)
    # Новичку осколок идёт СРАЗУ за переход - ради этого он и перешёл
    # по ссылке. Пригласившему - только за живого подписчика канала,
    # иначе это чистый фарм (привёл и отписался).
    # Новичку 0.5 звезды (5 единиц) СРАЗУ за переход по ссылке.
    give_new = min(REF_SHARD_NEW, room)
    give_inv = 0
    if sub_new is True and (not REF_SUB_REQUIRED or sub_inv is True):
        give_inv = min(REF_SHARD_INVITER, max(0, room - give_new))
    d[f"ref_star:{user_id}"] = inviter
    shards_add(a2, give_new, "referral")
    if give_inv:
        shards_add(a1, give_inv, "referral")
    st_day["u"] = used + give_inv + give_new
    if sub_new is not True:
        save_store(d)
        return ("need_sub_new", inviter, 0, give_new)
    if REF_SUB_REQUIRED and sub_inv is not True:
        save_store(d)
        return ("need_sub_inv", inviter, 0, give_new)
    # Прежняя монетная рефералка не отменяется — просто больше осколков.
    a1["coins"] += 50
    a1["invited"] = int(a1.get("invited", 0)) + 1
    a1["ref_coupon"] = True
    # Активация реферала засчитывается, когда приглашённый прошёл 3 квиза
    # (порог активности). Мёртвые регистрации не платят нам Stars.
    a2.setdefault("ref_pending", {})["inviter"] = inviter
    a2.setdefault("ref_pending", {})["quizzes"] = 0
    contest_add_points(d, a1, "ref", 25); bp_add_points(d, a1, "ref")
    a2["coins"] += 20
    milestone = a1["invited"] % 3 == 0
    if milestone:
        a1["coins"] += 100
        a1["lives"] = min(9, int(a1.get("lives", 5)) + 1)
    save_store(d)
    return ("ok", inviter, give_inv, give_new)


async def api_stars(request):
    """Снимок звёзд и витрина подарков."""
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
    _pp = _pool_load()
    return web.json_response({"ok": True, **star_view(acc),
                              "sub": sub_state(acc),
                              "gifts": gift_catalog_view(),
                              "gift_window": gift_window_open(),
                              "fall_open": fall_window_open(),
                              "market": gram_market(),
                              "convert_rate": GIFT_CONVERT_RATE,
                              "pool": {"distributable": int(_pp.get("active", 0)) + int(_pp.get("buyer", 0)),
                                       "stars": round((int(_pp.get("active", 0)) + int(_pp.get("buyer", 0))) / STAR_PACK, 2),
                                       "active": int(_pp.get("active", 0)),
                                       "margin_pct": int(POOL_MARGIN * 100),
                                       "cap_mult": POOL_BUYER_CAP},
                              "boost_price": PACK_FALL_BOOST})


def gift_catalog_clean():
    """Витрина подарков без 18+ маркировки — для рекламных сетей.

    AdsGram и Monetag запрещают 18+ контент в Mini App, а модерация
    проверяет приложение и после старта. Этот экран не содержит ни возрастного
    гейта, ни откровенных формулировок: только витрина подарков и их
    экономика. Используется как публичная страница для рекламных площадок
    и для проверки модерации.
    """
    out = []
    for gid, (emo, name, stars, _shards) in GIFT_CATALOG.items():
        out.append({"id": gid, "emoji": emo, "name": name, "stars": stars,
                    "price": price_units(stars), "season": emo in GIFT_SEASON,
                    "convert_stars": int(round(stars * GIFT_CONVERT_RATE)),
                    "note": "Конвертация в звёзды: %d ⭐ (85%%). Коллекционный — продажа за GRAM."
                            % int(round(stars * GIFT_CONVERT_RATE))})
    out.sort(key=lambda x: (x["stars"], x["name"]))
    return out


def gift_liquidity_view():
    """Витрина ликвидности: во что реально превращаются осколки игрока.

    Показываем три выхода из осколков, и для каждого — честную цифру:
      1) Telegram-подарок напрямую (цена в осколках) — гарантированный выход,
         но осколки надо копить, и обмен открыт только 1-7 числа месяца;
      2) конвертация подарка в Stars (85% номинала) — Telegram удерживает 15%;
      3) продажа коллекционного подарка за GRAM на маркетплейсе — цена
         плавает, поэтому показываем не «цену», а диапазон рынка и честно
         говорим, что точной цены за этот подарок мы не знаем.

    Диапазон GRAM — общий по рынку, а не котировка конкретного подарка.
    Придумывать цену за конкретный подарок нельзя: игрок примет её за
    гарантию и потом получит меньше.
    """
    m = gram_market() or {}
    rows = []
    for gid, (emo, name, stars, _shards) in GIFT_CATALOG.items():
        units = price_units(stars)
        conv = int(round(stars * GIFT_CONVERT_RATE))
        rows.append({
            "id": gid, "emoji": emo, "name": name, "stars": stars,
            "price_units": units,
            "price_shards": round(units / STAR_PACK, 2),
            "convert_stars": conv,
            "convert_shards": round(conv * STAR_PACK / STAR_PACK, 2),
            "market_gram": {"min": m.get("min_gram"), "median": m.get("median_gram"),
                            "max": m.get("max_gram"), "date": m.get("date"),
                            "count": m.get("count")},
            "exit": "85% в Stars" if stars <= 50 else "выгоднее за GRAM",
        })
    rows.sort(key=lambda x: x["stars"])
    return rows


async def api_gifts_public(request):
    """Публичная витрина подарков. Без init_data — для рекламных сетей."""
    return web.json_response({"ok": True, "gifts": gift_catalog_clean(),
                              "liquidity": gift_liquidity_view(),
                              "convert_rate": GIFT_CONVERT_RATE,
                              "market": gram_market()})


async def api_sub(request):
    """Состояние подписки и список тарифов."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    parts = validate_init_data((body or {}).get("init_data", ""))
    if not parts:
        return web.json_response({"error": "bad init_data"}, status=401)
    uid = user_from_init(parts)
    if not uid:
        return web.json_response({"error": "no user"}, status=401)
    d = load_store(); acc = acc_of(d, uid)
    save_store(d)
    tiers = []
    for tid, (stars, name, desc, bonus) in SUBSCRIPTION_TIERS.items():
        tiers.append({"id": tid, "stars": stars, "name": name, "desc": desc,
                      "bonus": bonus, "usd": round(stars * STAR_WITHDRAW_USD, 2)})
    return web.json_response({"ok": True, "sub": sub_state(acc),
                              "tiers": tiers, "days": SUB_DAYS})


async def api_gift_buy(request):
    """Обмен осколков на Telegram-подарок.

    Ограничения:
      * действует только в окно обмена (1-е..7-е число месяца);
      * 1 подарок в сутки на аккаунт;
      * осколки списываются только после успешной отправки подарка;
      * подарок, отправленный ботом, получатель не может обменять на Stars —
        поэтому цена в осколках идёт с запасом (GIFT_MARGIN).
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
    gid = str(body.get("gift_id", ""))
    if gid not in GIFT_CATALOG:
        return web.json_response({"ok": False, "error": "unknown gift"}, status=400)
    if not gift_window_open():
        return web.json_response({"ok": False, "error": "gift window closed",
                                  "next": gift_window_next_text()})
    d = load_store(); acc = acc_of(d, uid)
    st = acc.setdefault("star", {})
    last = int(st.get("last_gift", 0))
    if last and (_now() - last) < GIFT_COOLDOWN:
        left = GIFT_COOLDOWN - (_now() - last)
        save_store(d)
        return web.json_response({"ok": False, "error": "cooldown",
                                  "left": left, **star_view(acc)})
    emo, name, stars, _ = GIFT_CATALOG[gid]
    price = price_units(stars)
    if not shards_spend(acc, price):
        save_store(d)
        return web.json_response({"ok": False, "error": "not enough shards",
                                  "need": price, **star_view(acc)})
    save_store(d)
    try:
        await bot.send_gift(gift_id=gid, user_id=uid)
    except Exception as e:
        logging.warning("send_gift failed gift=%s user=%s: %s", gid, uid, e)
        # Возвращаем осколки — подарок не отправлен.
        d2 = load_store(); a2 = acc_of(d2, uid)
        shards_add(a2, price, "gift_refund")
        save_store(d2)
        return web.json_response({"ok": False, "error": "gift failed",
                                  **star_view(a2)})
    d3 = load_store(); a3 = acc_of(d3, uid)
    a3.setdefault("star", {})["last_gift"] = _now()
    save_store(d3)
    return web.json_response({"ok": True, "gift": {"emoji": emo, "name": name},
                              "spent": price, **star_view(a3)})


async def api_fall_boost(request):
    """Покупка бустера «Звездопад+» за Stars — через инвойс бота."""
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
    await send_invoice(uid, "fall_boost", uid)
    return web.json_response({"ok": True, "sent": True, "pack": "fall_boost"})


async def api_sub_buy(request):
    """Покупка подписки ПОДТЕКСТ+ за Stars — отправляем инвойс бота."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    parts = validate_init_data((body or {}).get("init_data", ""))
    if not parts:
        return web.json_response({"error": "bad init_data"}, status=401)
    uid = user_from_init(parts)
    if not uid:
        return web.json_response({"error": "no user"}, status=401)
    tier = (body or {}).get("tier") or ""
    if tier not in SUBSCRIPTION_TIERS:
        return web.json_response({"error": "unknown tier"}, status=400)
    await send_invoice(uid, tier, uid)
    return web.json_response({"ok": True, "sent": True, "tier": tier,
                              "stars": SUBSCRIPTION_TIERS[tier][0]})


TERMS_VERSION = "1.0"

TERMS_TEXT = {
    "version": TERMS_VERSION,
    "updated": "2026-11-26",
    "sections": [
        {"title": "1. Что такое ПОДТЕКСТ",
         "body": "Это бесплатная игра-викторина в Telegram. Игровые предметы (монеты, осколки, "
                 "питомец, карточки) не имеют реальной денежной стоимости и не могут быть "
                 "обменяны на деньги вне игры. За реальные Telegram Stars в игре "
                 "приобретаются: пакет «Звездопад+», билеты в розыгрыш, пакеты монет, "
                 "жизни, вечное отключение рекламы и подписка ПОДТЕКСТ+."},
        {"title": "2. Покупки в Telegram Stars",
         "body": "Все покупки совершаются через официальный платёж Telegram Stars. "
                 "Транзакции необратимы: возврат Stars через Telegram не предусмотрен, "
                 "за исключением случаев, предусмотренных самим Telegram. "
                 "Цены и состав пакетов указаны в игре и могут изменяться. "
                 "Покупая пакет, вы подтверждаете, что ознакомились с этими условиями."},
        {"title": "3. Пул осколков «Звездопад+»",
         "body": "Покупка пакета «Звездопад+» — это взнос в общий пул осколков. "
                 "70% вклада распределяется между активными игроками пропорционально их активности "
                 "с сохранением случайности: 30% взноса остаётся проекту. "
                 "Покупатель дополнительно получает гарантированный возврат 35% взноса осколками. "
                 "Игрок, купивший пакет, может получить из пула не более 166% от своего взноса "
                 "(потратив 200 Stars, можно получить до 333 Stars осколками); "
                 "излишек возвращается в пул и достаётся другим игрокам. "
                 "Осколки выпадают случайно, их количество не гарантировано."},
        {"title": "4. Обмен осколков на подарки",
         "body": "Обмен доступен с 1-го по 7-е число месяца, не чаще одного подарка в сутки на аккаунт. "
                 "Подарок отправляется ботом в Telegram. Получатель может обменять его на Stars — "
                 "Telegram возвращает 85% номинала, эти звёзды выводятся в GRAM через Fragment. "
                 "Цена в осколках равна номиналу подарка: 10 осколков = 1 звезда."},
        {"title": "5. Конкурсы и розыгрыши",
         "body": "Условия участия в конкурсах, сроки, правила определения победителей и порядок "
                 "вручения призов публикуются в канале @podtekst_game перед началом каждого конкурса. "
                 "Организатор вправе отклонить участие при подозрении на накрутку, "
                 "мультиаккаунты или автоматизированное нажатие."},
        {"title": "6. Персональные данные",
         "body": "Игра обрабатывает только Telegram ID, имя пользователя и игровую статистику, "
                 "которые Telegram передаёт при запуске Mini App. Данные не продаются и не "
                 "передаются третьим лицам, кроме сервисов, необходимых для работы игры."},
        {"title": "7. Ответственность и прекращение доступа",
         "body": "Организатор может изменить правила, приостановить или прекратить работу игры, "
                 "ужесточить антифарм-фильтры и ограничить нетипичную активность. "
                 "Решение организатора по спорным ситуациям является окончательным."},
    ],
}


async def api_terms(request):
    """Условия использования и приём согласия (галочка)."""
    if request.method == "GET":
        return web.json_response({"ok": True, "terms": TERMS_TEXT,
                                  "version": TERMS_VERSION})
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
    if body.get("accept"):
        acc["terms_version"] = TERMS_VERSION
        acc["terms_at"] = int(time.time())
        save_store(d)
    return web.json_response({"ok": True, "accepted": acc.get("terms_version") == TERMS_VERSION,
                              "version": acc.get("terms_version", "")})


def terms_accepted(acc):
    """Принял ли игрок актуальную версию условий."""
    return acc.get("terms_version") == TERMS_VERSION


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
    granted = None
    drop_units = 0
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
        # Клиенту честно сообщаем, выдан бонус или уже был выдан: раньше
        # сервер всегда отвечал ok=true, и игра писала «Бонус дня получен»
        # при каждом нажатии, даже когда монеты не начислялись.
        import datetime
        today = datetime.date.today().isoformat()
        if acc.get("daily_claimed") != today:
            acc["daily_claimed"] = today
            acc["coins"] += 20
            acc["lives"] = min(9, acc["lives"] + 1)
            granted = True
            # Активность засчитывается и в пул: ежедневный вход — событие.
            # Раньше выплата из пула уходила в молоко — игрок не видел осколков.
            _du, _dr = try_drop_shard(acc, "daily")
            drop_units += _du
            _pu = pool_payout(acc, "daily")
            drop_units += _pu
        else:
            granted = False
    else:
        # Начисление очков и монет — только через /api/answer (сверка ответа).
        save_store(d)
        return web.json_response({"ok": False, "error": "action not allowed", **wallet_view(acc)})

    save_store(d)
    resp = {"ok": True, **wallet_view(acc)}
    if granted is not None:
        resp["granted"] = granted
    if drop_units:
        resp["drop"] = int(drop_units)
    return web.json_response(resp)
QUIZ_BANK = [{"q": "Он написал «ок» в 23:41. Это значит…", "o": ["Всё хорошо, спит", "Что-то не так, но он не хочет говорить", "Он в душе", "Просто не заметил"], "c": 1, "h": "23:41 — не время для «ок»."}, {"q": "«Мне всё равно» + стикер с котом. Это…", "o": ["Ему реально всё равно", "Он недоволен, но прячет это", "Он отправляет котят всем подряд", "Он грустный"], "c": 1, "h": "Стикер = смягчение."}, {"q": "Ты спрашиваешь «ты со мной?». Ответ: «да». Это…", "o": ["Точно да", "Скорее да, но с сомнениями", "Он не понял вопрос", "Нет"], "c": 1, "h": "Односложный «да» — классика."}, {"q": "Голосовое на 4 минуты в ответ на «ты где?»", "o": ["Он раздражён", "Он опаздывает и нервничает", "У него проблемы", "Ему нечего сказать"], "c": 1, "h": "Ты спросила про «где», получила эссе."}, {"q": "«Ладно, давай как скажешь». Это…", "o": ["Он реально так думает", "Он раздражён и отдал выбор тебе", "Ему всё равно", "Он в восторге"], "c": 1, "h": "«Как скажешь» = «делай сама, я не хочу»."}, {"q": "Читает сообщения, отвечает через 4 часа", "o": ["Спит", "Прокручивает ленту, но ты не настолько важна", "Он на совещании", "Забыл"], "c": 1, "h": "Онлайн, но не в приоритете."}, {"q": "«Мне нравится твой стиль». Это…", "o": ["Комплимент", "Ирония", "Просто вежливость", "Просьба купить что-то"], "c": 1, "h": "Слишком общо для комплимента."}, {"q": "Фото с вечеринки, 6 подруг, ни тебя, ни подписи", "o": ["Он в восторге", "Намекает на «а где ты»", "Случайно", "Это его сестра"], "c": 1, "h": "Подпись — часть сообщения."}, {"q": "Три часа тишины, потом «как ты?»", "o": ["Ему реально важно", "Вспомнил, чтобы снять вину", "Ему скучно", "Ищет, с кем поговорить"], "c": 1, "h": "Тайминг всё говорит."}, {"q": "«Не переживай»", "o": ["Точно всё будет хорошо", "Есть что скрывать", "Он не знает, что сказать", "Ему всё равно"], "c": 1, "h": "Слова утешения = тревога."}, {"q": "Он выбирает ресторан и шлёт ссылку на 40-е место", "o": ["Случайно", "В восторге от выбора", "Забыл", "Проверяет, полезешь ли ты"], "c": 2, "h": "Сороковое место — не ошибка."}, {"q": "«Позвони, если что»", "o": ["Никогда не позвонит", "Позвонит, если что случилось", "Ждёт звонка прямо сейчас", "Он занят"], "c": 2, "h": "Фраза-отказ и фраза-приглашение одновременно."}, {"q": "На «привет» отвечает через 40 минут «привет-привет»", "o": ["Был занят", "Откладывал разговор", "Ему неинтересно", "Печатал одной рукой"], "c": 1, "h": "Двойное «привет» = неловкость."}, {"q": "Значок 💐 через 2 минуты после «увидимся в 8»", "o": ["Подарок", "Отмена планов", "Просто так", "Наезд"], "c": 1, "h": "Отмена планов редко приходит с цветами."}, {"q": "Не спрашивала — сам прислал фото с отпуска", "o": ["Ему скучно без тебя", "Показать, что там красиво без тебя", "Просто делится", "Хвастается"], "c": 0, "h": "Пишет первым = ты в мыслях."}, {"q": "«Может, лучше без меня?»", "o": ["Точно лучше с ним", "Скрытая просьба остаться", "Он реально думает так", "Нет"], "c": 1, "h": "Вопрос «может» — это просьба."}, {"q": "Пересылает мем про бывших", "o": ["Просто мем", "Намек на тебя", "Грустит", "Флиртует"], "c": 1, "h": "Случайных мемов про бывших не бывает."}, {"q": "Отвечает на голосовое голосовым на 12 минут", "o": ["Есть что сказать", "Бесится", "Хочет поговорить", "Пересказывает историю"], "c": 0, "h": "Ответ голосовым = диалог, а не спор."}, {"q": "«Ты как?» — «нормально». В 2 часа ночи.", "o": ["Всё хорошо", "Не очень", "Не хочет отвечать", "Не в дуге"], "c": 1, "h": "Ночь — время честных ответов."}, {"q": "Скидывает песню без комментария", "o": ["Просто поделился", "Это о тебе", "Хочет поговорить", "Забыл"], "c": 1, "h": "Песня = сообщение."}, {"q": "«А помнишь, ты тогда сказала…» — про то, что ты забыла", "o": ["Хочет напомнить", "Хочет поговорить о вас", "Проверяет память", "Собирается ссориться"], "c": 1, "h": "Он помнит больше, чем ты."}, {"q": "Предложил встретиться «на нейтральной территории»", "o": ["Нейтрально", "Не хочет, чтобы ты шла к нему", "Боится, что будет у тебя", "Ему так удобнее"], "c": 1, "h": "Нейтральная территория = зона комфорта."}]

# Новогодние вопросы добавляются ПОСЛЕ определения QUIZ_BANK,
# иначе NameError при импорте и деплой падает.
QUIZ_BANK = QUIZ_BANK + NY_QUESTIONS

# Смещение новогоднего банка фиксируем ЗДЕСЬ, а не вычисляем от len(QUIZ_BANK)
# позже: дальше банк дополняется пакетами (quiz_pack), и вычисление
# «len(QUIZ_BANK) - len(NY_QUESTIONS)» указывало уже не на новогодние
# вопросы, а на хвост банка. Из-за этого в ноябре «челлендж дня» стабильно
# выпадал на новогоднюю тематику.
NY_OFFSET = len(QUIZ_BANK) - len(NY_QUESTIONS)
NY_END = len(QUIZ_BANK)

# Расширенные пули лежат отдельным модулем: bot.py и так уже большой,
# а контент удобно править не трогая логику.
try:
    from quiz_pack import EXTRA_QUESTIONS
    QUIZ_BANK = QUIZ_BANK + EXTRA_QUESTIONS
except Exception as _qp_e:  # noqa: BLE001
    logging.warning("quiz_pack not loaded: %s", _qp_e)

# EXTRA_POSTS может отсутствовать в старой версии quiz_pack.py —
# импорт делаем мягким, иначе весь пакет вопросов не грузится целиком.
try:
    from quiz_pack import EXTRA_POSTS
except Exception as _qp_e2:  # noqa: BLE001
    EXTRA_POSTS = []
    logging.warning("quiz_pack EXTRA_POSTS missing: %s", _qp_e2)



# ---------------- Mini App API: квиз (сервер проверяет ответы) ----------------

_daily_state = {}

def ny_offset():
    """Индекс, с которого новогодние вопросы лежат в общем QUIZ_BANK.

    Новогодние вопросы добавлены в конец базового банка, поэтому их индексы
    в QUIZ_BANK глобальные — ответы в /api/answer остаются валидными.
    Значение фиксировано при сборке банка (NY_OFFSET): банк потом растёт
    за счёт quiz_pack, и пересчёт по len() давал бы неверную точку.
    """
    return NY_OFFSET


def ny_season():
    """Новогодняя тематика включается только в декабре и январе.

    В остальные месяцы новогодние вопросы не показываем: игрок в ноябре
    не должен получать вопросы про ёлку и полночь.
    """
    try:
        import datetime
        return datetime.date.today().month in (12, 1)
    except Exception:
        return False


def _pool_indices(season=None):
    """Индексы вопросов, которые сейчас уместно показывать.

    Вне сезона новогодние вопросы исключены полностью.
    """
    if season is None:
        season = ny_season()
    total = len(QUIZ_BANK)
    if season:
        return list(range(total))
    return [i for i in range(total)
            if not (NY_OFFSET <= i < NY_END)]


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
        if mm in (12, 1) and NY_QUESTIONS:
            idx = ny_offset() + (h % len(NY_QUESTIONS))
        else:
            # Вне сезона берём из общего пула БЕЗ новогодних вопросов,
            # иначе «челлендж дня» в ноябре был бы про ёлку.
            pool = _pool_indices(season=False)
            idx = pool[h % len(pool)] if pool else (h % len(QUIZ_BANK))
        _daily_state[key] = idx
    return _daily_state[key]

QUIZ_RECENT_KEPT = 40          # сколько последних вопросов помним
QUIZ_BLOCK_RECENT = 20         # столько последних вопросов не показываем снова


def pick_fresh_question(acc):
    """Выбрать вопрос, который игрок недавно не видел.

    Раньше индекс брался чистым random.randrange, поэтому при банке в ~140
    вопросов один и тот же вопрос выпадал трижды подряд. Теперь у каждого
    игрока хранится история последних показов: вопрос из недавних не
    предлагается, пока не проиграно QUIZ_BLOCK_RECENT других.
    """
    total = len(QUIZ_BANK)
    if total <= 1:
        return random.randrange(total)
    st = acc.setdefault("quiz", {})
    hist = [int(x) for x in (st.get("hist") or [])][-QUIZ_RECENT_KEPT:]
    block = set(hist[-QUIZ_BLOCK_RECENT:])
    allowed = _pool_indices()
    pool = [i for i in allowed if i not in block]
    if not pool:
        pool = [i for i in allowed if i != (hist[-1] if hist else -1)]
    if not pool:
        pool = allowed or list(range(total))
    idx = random.choice(pool)
    st["hist"] = (hist + [idx])[-QUIZ_RECENT_KEPT:]
    st["seen"] = int(st.get("seen", 0)) + 1
    return idx


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
    d = load_store(); acc = acc_of(d, uid)
    if daily:
        idx = daily_pick(uid)
    else:
        idx = pick_fresh_question(acc)
    q = QUIZ_BANK[idx]
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

async def api_quiz_public(request):
    """Вопрос для игры вне Telegram (обычный браузер, ссылка).

    Без init_data сервер не может проверить ответ и начислить награду,
    поэтому здесь отдаём вопрос вместе с правильным вариантом, а очки
    считаются на клиенте. Банк — общий и свежий, без новогодних вопросов
    вне сезона, поэтому вопросы не повторяются и не выпадают по кругу.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    pool = _pool_indices()
    if not pool:
        pool = list(range(len(QUIZ_BANK)))
    try:
        last = int((body or {}).get("last", -1))
    except Exception:
        last = -1
    cand = [i for i in pool if i != last] or pool
    idx = random.choice(cand)
    q = QUIZ_BANK[idx]
    return web.json_response({"ok": True, "id": idx, "q": q["q"], "o": q["o"],
                              "c": q["c"], "h": q["h"], "last": last})


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

    # Защита от фарма перезапросом: один и тот же вопрос можно ЗАСЧИТАТЬ
    # (получить награду) только раз в час. Но ошибочный ответ не должен
    # сжигать вопрос на час — иначе игрок ошибся и до конца часа не может
    # исправиться, а серия обнуляется. Поэтому внутри часа разрешено до
    # ANSWER_MAX_ATTEMPTS попыток; после них вопрос блокируется до
    # истечения часа.
    answered = acc.setdefault("answered", {})
    now_ts = int(time.time())
    rec = answered.get(str(idx)) or {}
    if isinstance(rec, int):          # старый формат: только метка времени
        rec = {"t": rec, "n": ANSWER_MAX_ATTEMPTS}
    rec = {"t": int(rec.get("t", 0)), "n": int(rec.get("n", 0))}
    if rec["t"] and (now_ts - rec["t"]) < ANSWER_COOLDOWN:
        if rec["n"] >= ANSWER_MAX_ATTEMPTS:
            # Всё, попытки исчерпаны — отдаём явный флаг и правильный ответ.
            return web.json_response({"ok": True, "already_answered": True,
                                      "correct": chosen == q["c"], "answer": q["o"][q["c"]],
                                      "hint": q["h"], **wallet_view(acc)})
    else:
        rec = {"t": now_ts, "n": 0}
    rec["n"] += 1
    answered[str(idx)] = rec
    if len(answered) > 200:
        for k in sorted(answered, key=lambda x: (answered[x] or {}).get("t", 0))[:100]:
            answered.pop(k, None)

    # Челлендж дня засчитывается один раз в сутки
    chal = acc.setdefault("daily", {})
    today = today_key()
    if chal.get("date") != today:
        chal["date"] = today
        chal["done"] = 0
        _last = chal.get("last")
        if _last == day_shift(today, -1):
            chal["streak"] = int(chal.get("streak", 0)) + 1
        elif _last == day_shift(today, -2):
            # Пропущен ровно один день: если есть заморозка - серия сохраняется.
            _fz = streak_freeze_state(acc)
            if int(_fz.get("tokens", 0)) > 0:
                _fz["tokens"] = int(_fz["tokens"]) - 1
                acc.setdefault("freeze", {})["used_last"] = today
                chal["streak"] = int(chal.get("streak", 0)) + 1
                chal["frozen"] = 1
            else:
                chal["streak"] = 1
        else:
            chal["streak"] = 1
    chal["last"] = today

    drop_units = 0
    if good:
        acc["coins"] += 10
        acc["streak"] += 1
        tourney_add(d, acc, 10)
        contest_add_points(d, acc, "quiz", 10); bp_add_points(d, acc, "quiz")
        pet_add_xp(d, acc, "quiz")
        acc["quiz_streak"] = int(acc.get("quiz_streak", 0)) + 1
        # Порог активации реферала: 3 квиза у приглашённого.
        _rp = acc.get("ref_pending")
        if isinstance(_rp, dict) and _rp.get("inviter"):
            _rp["quizzes"] = int(_rp.get("quizzes", 0)) + 1
            if int(_rp["quizzes"]) >= REF_ACTIVATION_QUIZZES:
                _inv = int(_rp["inviter"])
                acc["ref_pending"] = {}
                # Пишем в ТОТ ЖЕ стор d, что и обработчик квиза: отдельный
                # load_store/save_store здесь затирался последующим
                # save_store(d) основного обработчика, и бонус терялся.
                _a1 = acc_of(d, _inv)
                _a1["ref_activated"] = int(_a1.get("ref_activated", 0)) + 1
                _a1["coins"] = int(_a1.get("coins", 0)) + REF_ACTIVATION_BONUS
                _a1["ref_coupon"] = True
                # Пригласившему - осколок за активацию: друг реально играет.
                shards_add(_a1, REF_ACTIVATION_SHARDS, "ref_activated")
        # Осколок за серию: чем длиннее серия, тем выше шанс и тем крупнее кусок.
        if acc["quiz_streak"] >= 3:
            _u, _r = try_drop_shard(acc, "quiz")
            if _u:
                drop_units = _u
                logging.info("SHARD user=%s units=%s quiz", uid, _u)
        # Выплата из общего пула: вклад бустеров перетекает к активным игрокам.
        _pool_u = pool_payout(acc, "quiz")
        if _pool_u:
            drop_units += _pool_u
            logging.info("POOL payout user=%s units=%s quiz", uid, _pool_u)
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
        acc["coins"] = max(0, acc.get("coins", 0) - 5)
        acc["streak"] = 0
        acc["quiz_streak"] = 0
        acc["lives"] = max(1, acc["lives"] - 1)
    save_store(d)
    return web.json_response({"ok": True, "correct": good, "answer": q["o"][q["c"]],
                              "hint": q["h"], "drop": int(drop_units),
                              **bp_state(acc), **wallet_view(acc)})


# ---------------- Mini App API: прогресс match-3 + профиль ----------------
# Счётчик уровня и очки считает СЕРВЕР: клиент присылает только факт игры,
# а сервер сам решает, засчитать ли уровень и сколько монет дать.
M3_BASE_GOAL = 260
M3_GOAL_GROWTH = 1.30
M3_MAX_MOVES = 20
M3_GARLAND_MAX = 10

M3_LEVELS = [
    # t — сколько базовых фигур, shape — форма поля, blk — тупиков, moves — ходы,
    # need — что ещё обязательно нужно сделать, чтобы закрыть уровень:
    #   type — собрать N фигурок этого типа, blocks — сломать N тупиков,
    #   lamps — зажечь N ламп в гирлянде.
    {"t": 4, "shape": "full",    "blk": 0, "moves": 20, "need": {}},
    {"t": 4, "shape": "full",    "blk": 0, "moves": 20, "need": {"lamps": 3}},
    {"t": 4, "shape": "full",    "blk": 0, "moves": 22, "need": {"lamps": 5}},
    {"t": 5, "shape": "full",    "blk": 0, "moves": 22, "need": {"type": 12, "lamps": 5}},
    {"t": 5, "shape": "full",    "blk": 4, "moves": 24, "need": {"blocks": 4}},
    {"t": 5, "shape": "diamond", "blk": 0, "moves": 24, "need": {"type": 15}},
    {"t": 6, "shape": "full",    "blk": 6, "moves": 26, "need": {"blocks": 6, "lamps": 6}},
    {"t": 6, "shape": "corner",  "blk": 5, "moves": 26, "need": {"blocks": 5, "type": 18, "lamps": 8}},
]

def m3_level_cfg(level):
    """Конфиг уровня: единый источник правды для клиента и сервера."""
    try:
        lv = max(1, int(level))
    except Exception:
        lv = 1
    return M3_LEVELS[min(lv, len(M3_LEVELS)) - 1]

def m3_max_moves(level):
    return int(m3_level_cfg(level).get("moves", M3_MAX_MOVES))

def level_hint(body, m):
    """Уровень, по которому считаем лимиты: текущий серверный, если клиент не указал."""
    try:
        v = int(body.get("level", m.get("lvl", 1)))
    except Exception:
        v = int(m.get("lvl", 1))
    return max(1, v)

def m3_need(level):
    """Требования уровня: что нужно набрать сверх очков."""
    return dict(m3_level_cfg(level).get("need") or {})


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
    lv = int(m.get("lvl", 1))
    return {"m3": {"lvl": lv, "score": m.get("score", 0),
                   "moves": m.get("moves", m3_max_moves(lv)), "best": m.get("best", 0),
                   "plays": m.get("plays", 0), "garland": m.get("garland", 0),
                   "lamps": m.get("lamps", 0),
                   "combo": m.get("combo", 0), "maxcombo": m.get("maxcombo", 0),
                   "total": m.get("total", 0), "cleared": m.get("cleared", 0)},
            "goal": m3_goal(lv),
            "need": m3_need(lv),
            "cfg": {"t": m3_level_cfg(lv)["t"], "shape": m3_level_cfg(lv)["shape"],
                    "blk": m3_level_cfg(lv)["blk"], "moves": m3_max_moves(lv)},
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
    m3_drop = 0
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
        moves = num("moves", m3_max_moves(level_hint(body, m)), 0, M3_MAX_MOVES)
        garland = num("garland", 0, 0, 10 ** 7)      # всего гирлянд за всё время
        lamps = num("lamps", 0, 0, M3_GARLAND_MAX)    # ламп в текущей гирлянде
        combo = num("combo", 0, 0, 99)
        # Прогресс целей уровня: собрано фигурок (по типам), сломано тупиков, зажжено ламп
        need = body.get("need") or {}
        n_types = {}
        try:
            for k, v in (need.get("type") or {}).items():
                n_types[str(k)[:2]] = max(0, min(9999, int(v)))
        except Exception:
            n_types = {}
        n_blocks = num("need_blocks", 0, 0, 999)
        n_lamps  = num("need_lamps", 0, 0, 999)
        maxcombo = num("maxcombo", 0, 0, 99)
        plays = num("plays", 0, 0, 10 ** 7)

        # Уровень нельзя пропустить: можно играть только на текущем или уже открытом.
        cur = int(m.get("lvl", 1))
        if level > cur + 1:
            level = cur + 1
        # Ходы ограничены конфигом уровня (уровень даёт больше ходов).
        mv_max = m3_max_moves(cur)
        if moves > mv_max:
            moves = mv_max
        # Очки не могут превышать разумный предел для одного уровня.
        cap = m3_goal(level) * 4
        if score > cap:
            score = cap

        m["score"] = score
        m["moves"] = moves
        m["garland"] = max(int(m.get("garland", 0)), garland)
        m["lamps"] = max(int(m.get("lamps", 0)), lamps)
        m["maxcombo"] = max(int(m.get("maxcombo", 0)), maxcombo)
        # Жизни списывает сервер. Раньше клиент уменьшал их только у себя,
        # а сервер о жизни не знал — из-за этого жизни обходились бесплатно.
        prev_plays = int(m.get("plays_last", m.get("plays", 0)))
        spent = max(0, int(plays) - prev_plays)
        if spent > 0:
            regen_lives(acc)
            for _ in range(min(spent, 9)):
                if int(acc.get("lives", 5)) > 0:
                    acc["lives"] = int(acc["lives"]) - 1
        m["plays_last"] = int(plays)
        m["plays"] = max(int(m.get("plays", 0)), plays)

        # Уровень засчитывается сервером: хватает очков И выполнены цели уровня.
        goal = m3_goal(cur)
        nd = m3_need(cur)
        need_ok = True
        need_left = []
        if nd.get("type"):
            best = max(n_types.values()) if n_types else 0
            if best < nd["type"]:
                need_ok = False
                need_left.append("собери %d одинаковых фигурок (сейчас %d)" % (nd["type"], best))
        if nd.get("blocks"):
            if n_blocks < nd["blocks"]:
                need_ok = False
                need_left.append("сломай %d тупиков (сейчас %d)" % (nd["blocks"], n_blocks))
        if nd.get("lamps"):
            if n_lamps < nd["lamps"]:
                need_ok = False
                need_left.append("зажги %d ламп (сейчас %d)" % (nd["lamps"], n_lamps))
        if score >= goal and cur == level and need_ok:
            stars = 3 if moves >= 10 else (2 if moves >= 5 else 1)
            acc["coins"] += 30 + stars * 20
            m["cleared"] = int(m.get("cleared", 0)) + 1
            m["total"] = int(m.get("total", 0)) + score
            m["best"] = max(int(m.get("best", 0)), score)
            tourney_add(d, acc, max(0, score) // 10)
            contest_add_points(d, acc, "m3", 5); bp_add_points(d, acc, "m3")
            pet_add_xp(d, acc, "m3")
            _u, _r = try_drop_shard(acc, "m3")
            if _u:
                m3_drop = _u
                logging.info("SHARD user=%s units=%s m3", uid, _u)
            _pool_u = pool_payout(acc, "m3")
            if _pool_u:
                m3_drop += _pool_u
                logging.info("POOL payout user=%s units=%s m3", uid, _pool_u)
            m["lvl"] = cur + 1
            m["score"] = 0
            m["moves"] = m3_max_moves(cur + 1)
            unlock(acc, "level5") if m["lvl"] >= 5 else None
            unlock(acc, "level10") if m["lvl"] >= 10 else None
            if m["total"] >= 10000: unlock(acc, "m3_10k")
            if m["total"] >= 50000: unlock(acc, "m3_50k")
            if int(m.get("garland", 0)) >= 10: unlock(acc, "all_lamps")
            save_store(d)
            return web.json_response({"ok": True, "levelup": True, "stars": stars,
                                      "need": nd,
                                      "reward": 30 + stars * 20, "drop": int(m3_drop),
                                      **wallet_view(acc), **m3_view(acc)})

        if combo >= 5: unlock(acc, "combo5")
        if combo >= 8: unlock(acc, "combo8")
        if lamps >= M3_GARLAND_MAX: unlock(acc, "first_light")
        if int(m.get("garland", 0)) >= 10: unlock(acc, "all_lamps")
        if int(m.get("best", 0)) == 0 and score > 0:
            m["best"] = score
        save_store(d)
        return web.json_response({"ok": True, "levelup": False, "drop": int(m3_drop),
                                  "need": nd, "need_left": need_left,
                                  **wallet_view(acc), **m3_view(acc)})

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
                              "uid": uid, "link": link, "bot": BOT_USERNAME,
                              "coupon": bool(acc.get("ref_coupon")),
                              "coupon_pack": REF_COUPON_PACK,
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


async def api_ask_shards(request):
    """Оплата текстового вопроса Mira осколками — сток для осколков.

    POST /api/ask_shards
      body: {init_data, q}
    Списывает MIRA_ASK_SHARDS осколков и отдаёт deep-link на Mira с
    подставленным вопросом. Это расход осколков: они тратятся, а не копятся.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    parts = validate_init_data(body.get("init_data", ""))
    if not parts:
        return web.json_response({"error": "bad init_data"}, status=401)
    uid = user_from_init(parts)
    if not uid:
        return web.json_response({"error": "no user"}, status=401)
    d = load_store(); acc = acc_of(d, uid)
    st = acc.setdefault("ask", {})
    now = int(time.time())
    last = int(st.get("last", 0) or 0)
    if now - last < MIRA_ASK_COOLDOWN:
        return web.json_response({"ok": False, "error": "cooldown",
                                  "wait": MIRA_ASK_COOLDOWN - (now - last),
                                  **star_view(acc)})
    have = shards_of(acc)
    if have < MIRA_ASK_SHARDS:
        return web.json_response({"ok": False, "error": "not enough shards",
                                  "need": MIRA_ASK_SHARDS, "have": have,
                                  **star_view(acc)})
    try:
        q = str(body.get("q", ""))[:200]
    except Exception:
        q = ""
    shards_add(acc, -MIRA_ASK_SHARDS, "mira_ask")
    st["last"] = now
    st["asked"] = int(st.get("asked", 0)) + 1
    save_store(d)
    prompt = ("Ответь на вопрос игрока: %s" % q) if q else \
             ("Придумай 5 фраз с подтекстом для игры-квиза. Без пояснений.")
    return web.json_response({"ok": True, "url": mira_link(prompt),
                              "spent": MIRA_ASK_SHARDS, **star_view(acc)})


async def api_gen_pack(request):
    """Состояние пакета генераций: сколько осталось и по какой цене докупить.

    POST /api/gen_pack
      body: {init_data}
    Покупка идёт инвойсом бота (payload gen_pack / gen_pack_big) —
    этот эндпоинт только показывает остаток.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    parts = validate_init_data(body.get("init_data", ""))
    if not parts:
        return web.json_response({"error": "bad init_data"}, status=401)
    uid = user_from_init(parts)
    if not uid:
        return web.json_response({"error": "no user"}, status=401)
    d = load_store(); acc = acc_of(d, uid)
    return web.json_response({
        "ok": True,
        "left": int(acc.get("gen_left", 0)),
        "total": int(acc.get("gen_total", 0)),
        "price": GEN_PACK_STARS,
        "price_big": GEN_PACK_BIG_STARS,
        "count": GEN_PACK_COUNT,
        "count_big": GEN_PACK_BIG_COUNT,
    })


async def api_gen_spend(request):
    """Списание одной генерации (звёзды уже собраны ботом).

    POST /api/gen_spend
      body: {init_data, stars}
    Списывается только когда результат выдан. Фактическое списание звёзд
    делает Telegram по инвойсу — здесь мы ведём учёт остатка пак��та.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    parts = validate_init_data(body.get("init_data", ""))
    if not parts:
        return web.json_response({"error": "bad init_data"}, status=401)
    uid = user_from_init(parts)
    if not uid:
        return web.json_response({"error": "no user"}, status=401)
    d = load_store(); acc = acc_of(d, uid)
    left = int(acc.get("gen_left", 0))
    if left <= 0:
        return web.json_response({"ok": False, "error": "no pack",
                                  "price": GEN_PACK_STARS})
    acc["gen_left"] = left - 1
    save_store(d)
    return web.json_response({"ok": True, "left": acc["gen_left"]})


async def api_affiliate(request):
    """Параметры партнёрской программы + ссылка для владельца.

    POST /api/affiliate
      body: {init_data}
    Программу создаёт только аккаунт-владелец мини-приложения:
    Mini App Profile -> Edit -> Affiliate Program. Бот (bots.updateStarRefProgram)
    её создать не может. Здесь храним параметры, которые владелец выставит
    в профиле, и отдаём ссылку, которую он раздаёт.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    parts = validate_init_data(body.get("init_data", ""))
    if not parts:
        return web.json_response({"error": "bad init_data"}, status=401)
    d = load_store()
    prog = d.setdefault("_affiliate", {})
    prog.setdefault("commission_permille", AFF_COMMISSION_PERMILLE)
    prog.setdefault("duration_months", AFF_DURATION_MONTHS)
    prog.setdefault("enabled", True)
    save_store(d)
    # Настоящие affiliate-ссылки Telegram. Если их ещё нет — отдаём
    # start-ссылку бота, чтобы карточка не была пустой.
    link = (AFF_LINKS.get("personal") or AFF_LINKS.get("channel") or
            AFF_LINKS.get("miniapp") or
            "https://t.me/%s?startapp=ref_%s" % (BOT_USERNAME, MIRA_REF))
    # Куда платить комиссию и выводить звёзды — TON-кошелёк владельца.
    payout = TON_WALLET
    return web.json_response({
        "ok": True,
        "links": {k: v for k, v in AFF_LINKS.items() if v},
        "commission_permille": prog["commission_permille"],
        "commission_percent": round(prog["commission_permille"] / 10, 1),
        "duration_months": prog["duration_months"],
        "enabled": prog["enabled"],
        "link": link,
        "payout": payout,
        "payout_url": "https://tonviewer.com/" + TON_WALLET if TON_WALLET else "",
        "where": "Mini App Profile -> Edit -> Affiliate Program",
    })


async def api_freeze(request):
    """Заморозка серии: состояние и покупка.

    POST /api/freeze
      body: {init_data, action: "state"|"buy_shards",}
    Покупка за Stars идёт инвойсом бота (payload freeze_stars) —
    этот эндпоинт только показывает состояние и списывает осколки.
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
    action = str(body.get("action", "state") or "state")
    d = load_store(); acc = acc_of(d, uid)
    st = streak_freeze_state(acc)
    save_store(d)
    if action == "buy_shards":
        ok, cost, err = streak_freeze_buy(acc)
        save_store(d)
        if not ok:
            return web.json_response({"ok": False, "error": err, "need": cost,
                                      "tokens": int(st.get("tokens", 0)),
                                      **star_view(acc)})
        return web.json_response({"ok": True, "spent": cost,
                                  "tokens": int(st.get("tokens", 0)),
                                  **star_view(acc)})
    return web.json_response({
        "ok": True,
        "tokens": int(st.get("tokens", 0)),
        "max": FREEZE_MAX,
        "cost_shards": FREEZE_COST_SHARDS,
        "cost_stars": FREEZE_COST_STARS,
        **star_view(acc),
    })


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
                              "total_players": sum(players.values()),
                              "uid": str(uid) if uid else None})

async def api_promo_return(request):
    """Игрок вернулся в игру после перехода к Mira.

    Раньше бонус +50 выдавался только по нажатию кнопки «+50 🪙» — игрок
    уходил к Mira и терял монеты, потому что вспомнить про кнопку сложно.
    Теперь факт возврата фиксируется сам, и бонус начисляется автоматически,
    один раз за аккаунт.

    Считаем и «возвраты»: сколько игроков реально вернулись.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    parts = validate_init_data(body.get("init_data", ""))
    uid = user_from_init(parts) if parts else None
    if not uid:
        return web.json_response({"error": "bad init_data"}, status=401)
    d = load_store(); acc = acc_of(d, uid)
    promo = acc.setdefault("promo", {})
    p = d.setdefault("_promo", {"clicks": 0, "claims": 0})
    p.setdefault("by_source", {})
    p.setdefault("returns", 0)
    try:
        src = str(body.get("source") or "mira")[:24]
    except Exception:
        src = "mira"
    p["returns"] = int(p.get("returns", 0)) + 1
    rs = p.setdefault("returns_by_source", {})
    rs[src] = int(rs.get(src, 0)) + 1
    already = bool(promo.get("claimed"))
    if already:
        save_store(d)
        return web.json_response({"ok": True, "already": True, "reward": 0,
                                  "coins": acc.get("coins", 0),
                                  "lives": acc.get("lives", 5)})
    acc["coins"] = int(acc.get("coins", 0)) + PROMO_REWARD
    promo["claimed"] = True
    promo["ts"] = int(time.time())
    promo["auto"] = True
    p["claims"] = int(p.get("claims", 0)) + 1
    save_store(d)
    return web.json_response({"ok": True, "reward": PROMO_REWARD, "auto": True,
                              "coins": acc.get("coins", 0),
                              "lives": acc.get("lives", 5)})


async def api_promo_stats(request):
    """Статистика по рекламным местам — какое место приносит больше кликов."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "bad json"}, status=400)
    # Агрегаты по воронке — обезличенные, поэтому доступны без init_data:
    # их можно читать извне (мониторинг), не раскрывая ничьих данных.
    d = load_store()
    p = d.get("_promo", {}) or {}
    return web.json_response({"ok": True,
                              "clicks": int(p.get("clicks", 0)),
                              "claims": int(p.get("claims", 0)),
                              "returns": int(p.get("returns", 0)),
                              "by_source": p.get("by_source", {}) or {},
                              "returns_by_source": p.get("returns_by_source", {}) or {},
                              "visits_by_source": p.get("visits_by_source", {}) or {}})


def _lb_name(d, uid):
    acc = d.get(str(uid)) or {}
    nm = acc.get("name") or ""
    return str(nm)[:28] if nm else f"Игрок {str(uid)[-4:]}"


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


def _ad_pick(campaign_id=None, exclude=None):
    """Выбирает кампанию для показа (взвешенно, без повтора подряд)."""
    pool = [c for c in AD_CAMPAIGNS if c["id"] != exclude] or AD_CAMPAIGNS
    if campaign_id:
        for c in pool:
            if c["id"] == campaign_id:
                return c
    total = sum(int(c.get("weight", 1)) for c in pool) or 1
    x = random.random() * total
    acc = 0
    for c in pool:
        acc += int(c.get("weight", 1))
        if x <= acc:
            return c
    return pool[-1]


async def api_ads_serve(request):
    """Отдаёт клиенту кампанию для показа. Можно попросить конкретную (id)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    parts = validate_init_data((body or {}).get("init_data", ""))
    if not parts:
        return web.json_response({"error": "bad init_data"}, status=401)
    uid = user_from_init(parts)
    if not uid:
        return web.json_response({"error": "no user"}, status=401)
    try:
        want = str((body or {}).get("id", "") or "")[:32]
    except Exception:
        want = ""
    try:
        last = str((body or {}).get("exclude", "") or "")[:32]
    except Exception:
        last = ""
    c = _ad_pick(want or None, last or None)
    if not c:
        return web.json_response({"ok": False, "error": "no campaigns"})
    d = load_store(); acc = acc_of(d, uid)
    ads = d.setdefault("_ads", {})
    shown = ads.setdefault(c["id"], {"shown": 0, "clicks": 0})
    shown["shown"] = int(shown.get("shown", 0)) + 1
    save_store(d)
    return web.json_response({
        "ok": True,
        "ad": {
            "id": c["id"], "title": c["title"], "text": c["text"],
            "url": c["url"], "img": c.get("img") or AD_DEFAULT_IMG,
        },
    })


async def api_ads_click(request):
    """Игрок кликнул по рекламе — считаем переход по каждой кампании."""
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
        cid = str(body.get("id", "") or "")[:32]
    except Exception:
        cid = ""
    d = load_store(); acc = acc_of(d, uid)
    ads = d.setdefault("_ads", {})
    row = ads.setdefault(cid or "unknown", {"shown": 0, "clicks": 0})
    row["clicks"] = int(row.get("clicks", 0)) + 1
    acc.setdefault("ad_clicks", {})
    acc["ad_clicks"][cid] = int(acc["ad_clicks"].get(cid, 0)) + 1
    # Осколки за клик: клик по Mira ценится выше - это наш партнёр.
    _sh = AD_MIRA_SHARDS if cid == "mira" else AD_CLICK_SHARDS
    shards_add(acc, _sh, "ad_click:" + cid)
    save_store(d)
    return web.json_response({"ok": True, "shards": _sh})


async def api_ad_reward(request):
    """Просмотр rewarded-рекламы -> +монеты. Антифрод: лимит в сутки на пользователя."""
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
    if ads_disabled(acc):
        save_store(d)
        return web.json_response({"ok": True, "reward": 0, "skipped": True, **wallet_view(acc)})
    import datetime
    today = datetime.date.today().isoformat()
    seen = acc.setdefault("ad_seen", {})
    n = int(seen.get(today, 0) or 0)
    if n >= 5:
        save_store(d)
        return web.json_response({"ok": False, "error": "ad limit", **wallet_view(acc)})
    seen[today] = n + 1
    acc["coins"] += AD_CLICK_BONUS
    # Осколки за просмотр: монеты живут недолго, осколки копятся
    # и уводят игрока к витрине подарков - там наш настоящий доход.
    shards_add(acc, AD_VIEW_SHARDS, "ad_view")
    contest_add_points(d, acc, "ad", 5); bp_add_points(d, acc, "ad")
    save_store(d)
    return web.json_response({"ok": True, "reward": AD_CLICK_BONUS,
                              "shards": AD_VIEW_SHARDS,
                              "left": 5 - n - 1, **wallet_view(acc)})

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


# ---------------- АТАКА / МЕСТЬ ----------------
ATTACK_COST = 50        # монет за нападение
ATTACK_COOLDOWN = 600   # секунд между нападениями на одного игрока


async def api_attack(request):
    """Нападение на соперника из таблицы лидеров. Жертва может отомстить.

    Клиент может только тратить свои монеты и выбирать цель по uid.
    Урон и начисление — только здесь, на сервере.
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

    action = body.get("action", "attack")
    target = str(body.get("target", "")).strip()
    if not target or target == str(uid):
        return web.json_response({"ok": False, "error": "bad target"}, status=400)

    d = load_store()
    acc = acc_of(d, uid)
    tacc = d.get(target)
    if not isinstance(tacc, dict):
        save_store(d)
        return web.json_response({"ok": False, "error": "player not found"})

    log = d.setdefault("_attacks", [])

    if action == "revenge":
        # Отомстить можно только тому, кто на тебя напал.
        pend = [x for x in log
                if x.get("target") == str(uid)
                and x.get("attacker") == target
                and not x.get("answered")]
        if not pend:
            save_store(d)
            return web.json_response({"ok": False, "error": "no revenge available"})
        pair = sum(1 for x in log
                   if (x.get("attacker") == str(uid) and x.get("target") == target)
                   or (x.get("attacker") == target and x.get("target") == str(uid)))
        if pair >= 6:
            save_store(d)
            return web.json_response({"ok": False, "error": "duel limit reached"})
        pend[-1]["answered"] = True
        # Мститель платит те же монеты.
        if acc["coins"] < ATTACK_COST:
            save_store(d)
            return web.json_response({"ok": False, "error": "not enough coins",
                                      **wallet_view(acc)})
        if int(tacc.get("lives", 5)) <= 0:
            save_store(d)
            return web.json_response({"ok": False, "error": "target is out of lives"})
        acc["coins"] -= ATTACK_COST
        tacc["lives"] = max(0, int(tacc.get("lives", 5)) - 1)
        log.append({"attacker": str(uid), "target": target,
                    "ts": int(time.time()), "revenge": True})
        del log[:-200]
        save_store(d)
        return web.json_response({"ok": True, "revenge": True,
                                  "target_lives": tacc.get("lives", 5),
                                  **wallet_view(acc)})

    # Обычное нападение.
    if acc["coins"] < ATTACK_COST:
        save_store(d)
        return web.json_response({"ok": False, "error": "not enough coins",
                                  **wallet_view(acc)})
    if int(tacc.get("lives", 5)) <= 0:
        save_store(d)
        return web.json_response({"ok": False, "error": "target is out of lives"})
    mine = acc.setdefault("attacks", [])
    for a in mine:
        if a.get("uid") == target and time.time() - int(a.get("ts", 0)) < ATTACK_COOLDOWN:
            save_store(d)
            return web.json_response({"ok": False, "error": "cooldown",
                                      **wallet_view(acc)})

    acc["coins"] -= ATTACK_COST
    mine.append({"uid": target, "ts": int(time.time())})
    del mine[:-50]
    tacc["lives"] = max(0, int(tacc.get("lives", 5)) - 1)
    log.append({"attacker": str(uid), "target": target,
                "ts": int(time.time()), "revenge": False})
    del log[:-200]
    save_store(d)
    return web.json_response({"ok": True, "target_lives": tacc.get("lives", 5),
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
    log = d.get("_attacks", []) or []
    my_rev = []
    for x in log:
        if x.get("target") == str(uid) and not x.get("answered"):
            my_rev.append({"uid": x.get("attacker"), "name": _lb_name(d, x.get("attacker"))})
    for i, r in enumerate(rows):
        r["can_attack"] = (r["uid"] != str(uid))
    return web.json_response({"ok": True, "top": rows, "me": mine,
                              "revenge": my_rev[-5:],
                              "attack_cost": ATTACK_COST,
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
    # Реферальная ссылка приглашённого. Раньше шаринг отдавал ?start=src_share
    # без payload — приглашённый НЕ засчитывался, виральный цикл был разорван.
    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref{uid}"
    share_text = (f"Я набрал {streak} подряд в ПОДТЕКСТЕ — угадываю скрытый смысл "
                  f"в обычных фразах. Сыграй так же: {ref_link}")
    try:
        await bot.send_message(CHAT_ID, txt, parse_mode="HTML",
                               disable_web_page_preview=True)
        return web.json_response({"ok": True, "posted": True,
                                  "link": ref_link, "text": share_text,
                                  "streak": streak, "days": days})
    except Exception as e:
        logging.warning("share post failed: %s", e)
        return web.json_response({"ok": True, "posted": False,
                                  "link": ref_link, "text": share_text,
                                  "streak": streak, "days": days})


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
    visit_drop = 0
    if is_new:
        contest_add_points(d, acc, "day", 20); bp_add_points(d, acc, "day")
        pet_add_xp(d, acc, "day")
        # Вход в игру — событие: осколки + выплата из общего пула.
        _du, _dr = try_drop_shard(acc, "visit")
        visit_drop += _du
        _pu = pool_payout(acc, "visit")
        visit_drop += _pu
    save_store(d)
    return web.json_response({"ok": True, "new_day": is_new, "reward": reward,
                              "streak": streak, "penalty": penalty,
                              "drop": int(visit_drop),
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
    ("cv_not_answered", "common", "Отправил резюме — ни одного ответа", "Тишина после отклика"),
    ("photo_work_late", "common", "Сел в машину в 3 часа ночи", "Работа не спит"),
    ("reply_ok", "common", "Ответил одним «ок»", "Точка вместо разговора"),
    ("delivery_gone", "common", "«Курьер был, но тебя не было»", "Дверь закрылась"),
    ("accidental_call", "common", "Случайно нажал и сразу сбросил", "Трубка сорвалась, но звонок слышали"),
    ("busy_tomorrow", "rare", "«Завтра занят, давай в четверг»", "Четверг — это не «никогда»"),
    ("stayed_home", "rare", "«Я остался дома, ждал звонка»", "Тишина в ожидании"),
    ("sent_link", "rare", "Прислал ссылку вместо ответа", "Ответ, который легко не открыть"),
    ("forgot_anniversary", "rare", "«Я же говорил, что помню»", "Говорил — но не говорил"),
    ("already_said_it", "epic", "Отправил то же самое, что ты писала вчера", "Ответ, который помнит"),
    ("voice_breath", "epic", "Голосовое на 12 секунд: только дыхание", "Тишина, которую слышно"),
    ("third_place", "epic", "«Третье место — это тоже место»", "Проигрыш, поданный как победа"),
    ("waited_lights", "legend", "Два часа в машине у твоего дома, не позвонил", "Молчание на парковке"),
    ("forgave_silence", "legend", "«Я не злюсь. Я просто ждал»", "Прощение без слов"),
    ("night_light", "legend", "Оставил свет включённым, чтобы ты не боялась", "Забота без сообщения"),
]
CARD_BY_ID = {c[0]: c for c in CARD_BANK}
CARD_OPEN_COST = 30

# Подарки за коллекцию. Каждые N карт одной редкости — подарок.
# gifts выдаются один раз за порог, поэтому храним счётчик в acc["gift_stage"].
CARD_GIFTS = [
    ("common", 10, {"coins": 100}, "🪙 100 монет"),
    ("rare", 10, {"lives": 1}, "❤️ +1 жизнь"),
    ("epic", 10, {"coins": 300, "lives": 1}, "🪙 300 монет и ❤️ +1 жизнь"),
    ("legend", 3, {"noads": True, "coins": 500}, "🚫 без рекламы и 🪙 500 монет"),
]
CARD_GIFT_THRESHOLDS = {k: n for k, n, _r, _t in CARD_GIFTS}


def _card_gift_check(acc, card_id):
    """Проверяет пороги подарков по коллекции. Возвращает список подарков.

    Считаем по редкости, а не по конкретной карте: «10 обычных» — это любые
    десять обычных карт в коллекции, а не десять копий одной и той же.
    """
    c = CARD_BY_ID.get(card_id)
    if not c:
        return []
    rarity = c[1]
    th = CARD_GIFT_THRESHOLDS.get(rarity)
    if not th:
        return []
    raw = acc.get("cards") or {}
    have = 0
    for cid, n in raw.items():
        cc = CARD_BY_ID.get(cid)
        if cc and cc[1] == rarity:
            have += int(n or 0)
    if have <= 0:
        return []
    stage = acc.setdefault("gift_stage", {})
    done = int(stage.get(rarity, 0))
    reached = have // th          # 23 обычных = пороги 10 и 20 пройдены
    if reached <= done:
        return []
    gifts = []
    for r, n, reward, title in CARD_GIFTS:
        if r != rarity:
            continue
        for k in range(done + 1, reached + 1):
            g = dict(reward); g["title"] = title; g["stage"] = k
            gifts.append(g)
    stage[rarity] = reached
    return gifts


def _card_gift_apply(acc, gifts):
    """Начисляет подарки игроку."""
    out = []
    for g in gifts:
        if "coins" in g: acc["coins"] = int(acc.get("coins", 0)) + int(g["coins"])
        if "lives" in g: acc["lives"] = min(9, int(acc.get("lives", 5)) + int(g["lives"]))
        if g.get("noads"): acc["noads"] = True
        out.append(g["title"])
    return out

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
    # Прогресс до подарка: сколько карт этой редкости ещё нужно.
    stage = acc.get("gift_stage") or {}
    by_rar = {}
    for cid, n in raw.items():
        cc = CARD_BY_ID.get(cid)
        if cc:
            by_rar[cc[1]] = by_rar.get(cc[1], 0) + int(n or 0)
    for it in items:
        th = CARD_GIFT_THRESHOLDS.get(it["rarity"], 0)
        done = int(stage.get(it["rarity"], 0))
        it["gift_need"] = max(0, (done + 1) * th - by_rar.get(it["rarity"], 0)) if th else 0
        it["gift_next"] = (done + 1) * th if th else 0
        it["gift_total"] = th
        it["gift_have"] = by_rar.get(it["rarity"], 0)
    return {"ok": True, "total": total, "counts": counts, "items": items,
            "opened": acc.get("cards_opened", 0),
            "cost": CARD_OPEN_COST,
            "gifts": [{"rarity": r, "need": n, "title": t} for r, n, _x, t in CARD_GIFTS],
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
        # Подарки за коллекцию: порог по редкости (10 обычных, 10 редких,
        # 10 эпических, 3 легендарных). Порог выдаётся один раз.
        gifts = _card_gift_check(acc, cid)
        gift_titles = _card_gift_apply(acc, gifts)
        save_store(d)
        is_new = acc["cards"][cid] == 1
        return web.json_response({
            "ok": True, "is_new": is_new, "coins": acc["coins"],
            "gifts": gift_titles,
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
        # В хранилище лежат служебные списки (_promo, _attacks, _shares...),
        # а не аккаунты. Без этой проверки acc.get() падал на списке -> 500.
        if str(k).startswith("_") or not isinstance(acc, dict):
            continue
        t = acc.get("tourney") or {}
        if not isinstance(t, dict):
            continue
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
# Призы конкурса выдаются ОСКОЛКАМИ (внутренняя валюта) и монетами.
# Раньше здесь стоял Telegram-подарок на 100 Stars: бот не может оплатить
# подарок со своего баланса (там 1 Star), и такая схема съедала маржу.
# Осколки выдаются мгновенно, без зависимости от баланса бота.
CONTEST_PLACES = [
    {"place": 1, "stars": 100, "coins": 1000, "lives": 5, "shards": 1000, "gift": "💎"},
    {"place": 2, "stars": 50,  "coins": 500,  "lives": 3, "shards": 500,  "gift": "🏆"},
    {"place": 3, "stars": 25,  "coins": 300,  "lives": 2, "shards": 250,  "gift": "🎁"},
]
CONTEST_PRIZE_STARS_EQUIV = CONTEST_PLACES[0]["stars"]  # номинал 1-го места для текстов

# Сколько очков активности даёт каждое действие (тот же вес, что в турнире)
CONTEST_ACTION_POINTS = {"quiz": 10, "m3": 5, "day": 20, "ref": 25, "share": 15}

# --- Рефералка: активация по порогу активности ---
REF_ACTIVATION_QUIZZES = 3    # сколько квизов должен пройти приглашённый
REF_ACTIVATION_BONUS = 100    # бонус пригласившему за активированного друга
REF_ACTIVATION_SHARDS = 5     # +0.5 звезды пригласившему за активацию

# ---------------- ГЕЙТИНГ: конкурсы открываются по турам ----------------
# Три ступени конкурса. Каждая открывается только тем, кто реально прошёл
# предыдущую: без гейтинга в конкурс заходили все подряд после 30 очков,
# и приз разбирали 2-3 человека, а база не росла.
# Требования читаются из профиля игрока (уровень match-3, серии, турнир).
CONTEST_TIERS = [
    {"id": "t1", "name": "Конкурс подтекста", "emoji": "🎯",
     "prize_stars": 25, "prize_coins": 300, "winners": 3,
     "points": 30, "start": "2026-11-21T00:00:00", "end": "2026-12-01T23:59:00",
     "req": {"m3_lvl": 2, "quiz_streak": 3}},
    {"id": "t2", "name": "Ночной конкурс", "emoji": "🌙",
     "prize_stars": 50, "prize_coins": 500, "winners": 1,
     "points": 60, "start": "2026-11-28T00:00:00", "end": "2026-12-08T23:59:00",
     "req": {"m3_lvl": 10, "day_streak": 3}, "after": "t1"},
    {"id": "t3", "name": "Финал месяца", "emoji": "👑",
     "prize_stars": 100, "prize_coins": 1000, "winners": 1,
     "points": 100, "start": "2026-12-08T00:00:00", "end": "2026-12-15T23:59:00",
     "req": {"tourney": 100}, "after": "t2"},
]

def tier_by_id(tid):
    for t in CONTEST_TIERS:
        if t["id"] == tid:
            return t
    return None

def tier_req_met(acc, tier):
    """Проверяет, выполнены ли требования ступени (уровни, серии, турнир)."""
    req = tier.get("req") or {}
    m3 = acc.get("m3") or {}
    if "m3_lvl" in req and int(m3.get("lvl", 1)) < int(req["m3_lvl"]):
        return False
    if "quiz_streak" in req and int(acc.get("quiz_streak", 0)) < int(req["quiz_streak"]):
        return False
    if "day_streak" in req and int(acc.get("day_streak", 0)) < int(req["day_streak"]):
        return False
    if "tourney" in req:
        t = acc.get("tourney") or {}
        if int(t.get("score", 0)) < int(req["tourney"]):
            return False
    return True

def tier_unlocked(acc, tier):
    """Ступень открыта: требования выполнены И предыдущая ступень пройдена."""
    if not tier_req_met(acc, tier):
        return False
    prev = tier.get("after")
    if prev:
        pt = tier_by_id(prev)
        if pt:
            st = contest_tickets(acc)
            # предыдущая ступень должна быть не просто открыта, а пройдена (есть билет)
            if int(st["tiers"].get(prev, {}).get("tickets", 0)) <= 0:
                return False
    return True

def tier_unlocked_ids(acc):
    return [t["id"] for t in CONTEST_TIERS if tier_unlocked(acc, t)]

def tier_state(acc, tier):
    """Состояние одной ступени: открыта ли, прогресс, билеты."""
    st = contest_tickets(acc)
    row = st["tiers"].get(tier["id"], {})
    return {"id": tier["id"], "name": tier["name"], "emoji": tier.get("emoji", ""),
            "prize_stars": tier.get("prize_stars", 0),
            "prize_coins": tier.get("prize_coins", 0),
            "winners": tier.get("winners", 1),
            "points_needed": tier.get("points", CONTEST_TICKET_POINTS),
            "points": int(row.get("points", 0)),
            "tickets": int(row.get("tickets", 0)),
            "paid_tickets": int(row.get("paid_tickets", 0)),
            "unlocked": tier_unlocked(acc, tier),
            "req": tier.get("req", {}),
            "req_met": tier_req_met(acc, tier),
            "after": tier.get("after"),
            "start": tier.get("start"), "end": tier.get("end")}

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
    """Билеты и очки игрока ПО СТУПЕНЯМ конкурса.

    Старая схема (points/paid_tickets/tickets на верхнем уровне) остаётся
    для совместимости со старыми записями: переносим их в первую ступень.
    """
    c = acc.setdefault("contest", {"tiers": {}})
    tiers = c.setdefault("tiers", {})
    for t in CONTEST_TIERS:
        row = tiers.setdefault(t["id"], {"points": 0, "paid_tickets": 0, "tickets": 0})
        row.setdefault("points", 0)
        row.setdefault("paid_tickets", 0)
        row.setdefault("tickets", 0)
    # миграция старых (плоских) данных в первую ступень
    legacy = int(c.get("points", 0) or 0)
    if legacy:
        tiers["t1"]["points"] = int(tiers["t1"].get("points", 0)) + legacy
        c["points"] = 0
    legacy_t = int(c.get("tickets", 0) or 0)
    if legacy_t:
        tiers["t1"]["tickets"] = int(tiers["t1"].get("tickets", 0)) + legacy_t
        c["tickets"] = 0
    legacy_p = int(c.get("paid_tickets", 0) or 0)
    if legacy_p:
        tiers["t1"]["paid_tickets"] = int(tiers["t1"].get("paid_tickets", 0)) + legacy_p
        c["paid_tickets"] = 0
    return c

def active_tier(acc):
    """Активная ступень: самая высокая, которую игрок открыл и которая идёт."""
    now = _msk()
    opened = tier_unlocked_ids(acc)
    best = None
    for t in CONTEST_TIERS:
        if t["id"] not in opened:
            continue
        if now < _contest_dt(t.get("start", CONTEST_START)):
            continue
        if now > _contest_dt(t.get("end", CONTEST_END)):
            continue
        best = t
    return best

def contest_add_points(d, acc, kind, amount=None):
    """Начислить очки активности за действие (квиз/match-3/день/рефералка)."""
    if contest_state() != "running":
        return 0
    pts = amount if amount is not None else CONTEST_ACTION_POINTS.get(kind, 0)
    if pts <= 0:
        return 0
    tier = active_tier(acc)
    if not tier:
        return 0
    c = contest_tickets(acc)
    row = c["tiers"].setdefault(tier["id"], {"points": 0, "paid_tickets": 0, "tickets": 0})
    row["points"] = int(row.get("points", 0)) + int(pts)
    # Каждые N очков автоматически превращаются в бесплатный билет
    need = int(tier.get("points", CONTEST_TICKET_POINTS))
    gained = row["points"] // need
    if gained:
        row["points"] -= gained * need
        row["tickets"] = int(row.get("tickets", 0)) + gained
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

    tier = active_tier(acc)
    opened = tier_unlocked_ids(acc)

    # Рейтинг участников по билетам АКТИВНОЙ ступени
    rows = []
    for k, a in d.items():
        if not k.isdigit() or not isinstance(a, dict):
            continue
        cc = a.get("contest") or {}
        tt = (cc.get("tiers") or {})
        tid = tier["id"] if tier else "t1"
        t = int((tt.get(tid) or {}).get("tickets", 0))
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

    tiers_view = [tier_state(acc, t) for t in CONTEST_TIERS]
    active = tier_state(acc, tier) if tier else None

    return web.json_response({
        "ok": True,
        "state": contest_state(),
        "tiers": tiers_view,
        "active_tier": active,
        "unlocked": opened,
        "points": active["points"] if active else 0,
        "points_needed": active["points_needed"] if active else CONTEST_TICKET_POINTS,
        "tickets": active["tickets"] if active else 0,
        "paid_tickets": active["paid_tickets"] if active else 0,
        "paid_stars": CONTEST_TICKET_STARS,
        "paid_tickets_amount": CONTEST_TICKETS_PAID,
        "winners": (tier.get("winners") if tier else CONTEST_WINNERS),
        "prize_coins": (tier.get("prize_coins") if tier else CONTEST_PRIZE["coins"]),
        "prize_lives": CONTEST_PRIZE["lives"],
        "prize_stars": (tier.get("prize_stars") if tier else CONTEST_PRIZE_STARS_EQUIV),
        "prize_gift": CONTEST_PRIZE_GIFT_EMOJI,
        "places": CONTEST_PLACES,
        "ends_at": (tier.get("end") if tier else CONTEST_END),
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
    tier = active_tier(acc)
    if not tier:
        return web.json_response({"ok": False, "error": "tier locked",
                                  "msg": "Сначала пройди предыдущие туры — конкурс откроется"})
    contest_tickets(acc); save_store(d)
    return web.json_response({"ok": True, "stars": CONTEST_TICKET_STARS,
                              "tickets": CONTEST_TICKETS_PAID,
                              "tier": tier["id"],
                              "method": "stars"})

@dp.message(Command("contest"))
async def contest_cmd(message: types.Message):
    """Команда /contest: правила и статус розыгрыша."""
    d = load_store(); acc = acc_of(d, message.from_user.id)
    c = contest_tickets(acc)
    tier = active_tier(acc)
    lines = ["\U0001F3C6 <b>Призовые конкурсы ПОДТЕКСТ</b>\n\n"]
    for t in CONTEST_TIERS:
        st = tier_state(acc, t)
        mark = "\u2705" if st["unlocked"] else "\U0001F512"
        if tier and t["id"] == tier["id"]:
            mark = "\u2b50"
        need = ""
        r = t.get("req") or {}
        if "m3_lvl" in r: need += f"\n   · match-3 уровень {r['m3_lvl']}"
        if "quiz_streak" in r: need += f"\n   · {r['quiz_streak']} верных ответов подряд"
        if "day_streak" in r: need += f"\n   · {r['day_streak']} дня подряд"
        if "tourney" in r: need += f"\n   · {r['tourney']} очков в турнире недели"
        if t.get("after"):
            need += "\n   · пройден предыдущий конкурс"
        lines.append(f"{mark} <b>{t['emoji']} {t['name']}</b> — приз {t['prize_stars']} Stars"
                     f" + {t['prize_coins']} монет, {t['winners']} победител."
                     f"\n   Билет: каждые {t['points']} очков активности."
                     f"\n   Дедлайн: {t['end'][:10]}" + (need if not st["unlocked"] else ""))
        lines.append("")
    lines.append("<b>Вход:</b> бесплатно — очками активности; платно — 100 Stars = 5 билетов.")
    lines.append(f"Дедлайн финала: {CONTEST_END[:10]}")
    if tier:
        st = tier_state(acc, tier)
        lines.append(f"\nТвоя ступень: <b>{tier['name']}</b> · "
                     f"очки {st['points']}/{st['points_needed']} · билетов {st['tickets']}")
    else:
        lines.append("\nСейчас доступна <b>первая ступень</b> — пройди её, чтобы открыть следующую.")
    lines.append("\nИграй тут:")
    await message.answer("\n".join(lines), reply_markup=kb())

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

# ---------------- ПИТОЕЦ: тамагочи, хранитель билетов и сил ----------------
# Питомец растёт от активности в игре, сам проводит дуэли и держит щит.
PET_SPECIES = [
    {"id": "cat",    "name": "Кот",     "emoji": "\U0001F431", "rar": "common", "pow": 1},
    {"id": "fox",    "name": "Лис",     "emoji": "\U0001F98A", "rar": "rare",   "pow": 2},
    {"id": "dragon", "name": "Дракон",  "emoji": "\U0001F409", "rar": "epic",   "pow": 3},
    {"id": "eagle",  "name": "Орёл",    "emoji": "\U0001F985", "rar": "legend", "pow": 4},
]
PET_STAGES = [
    {"n": 1, "name": "Яйцо",         "emoji": "\U0001F95A", "xp": 0},
    {"n": 2, "name": "Малыш",        "emoji": "\U0001F424", "xp": 60},
    {"n": 3, "name": "Друг",         "emoji": "\U0001F425", "xp": 200},
    {"n": 4, "name": "Хранитель",    "emoji": "\U0001F9D9", "xp": 600},
    {"n": 5, "name": "Легенда",      "emoji": "\U0001F985", "xp": 1500},
]
PET_XP = {"quiz": 12, "m3": 30, "day": 15, "duel": 20}
# Прокачка стадии питомца за осколки: цена в десятых звезды (10 = 1 звезда).
# Стадия 2 -> 3, 3 -> 4, 4 -> 5. Цены растут: чем дальше, тем дороже.
PET_STAGE_UP = {2: 120, 3: 260, 4: 600}   # осколков (десятых) за следующую стадию
PET_STAGE_UP_MAX = 5                       # выше 5-й стадии прокачки нет
PET_DECAY_HOUR = 5
PET_ACTION_GAIN = 40
PET_HATCH_TAPS = 5
PET_ACTIONS = {
    "feed":  {"cost": 10, "need": "hunger",    "emoji": "\U0001F34E", "name": "Кормить"},
    "play":  {"cost": 15, "need": "happiness", "emoji": "\U0001F3AE", "name": "Играть"},
    "wash":  {"cost": 15, "need": "hygiene",   "emoji": "\U0001F9FC", "name": "Мыть"},
    "sleep": {"cost": 0,  "need": "energy",    "emoji": "\U0001F319", "name": "Спать"},
}
PET_ACTION_CD = 1800


def pet_acc(acc):
    """Состояние питомца игрока."""
    p = acc.setdefault("pet", {})
    p.setdefault("hatch", 0)
    p.setdefault("stage", 1)
    p.setdefault("xp", 0)
    p.setdefault("species", "")
    p.setdefault("hunger", 70)
    p.setdefault("happiness", 70)
    p.setdefault("hygiene", 70)
    p.setdefault("energy", 70)
    p.setdefault("ts", int(time.time()))
    p.setdefault("cd", {})
    p.setdefault("shields", 1)
    return p


def pet_decay(p):
    """Потребности падают по времени (5 единиц в час)."""
    now = int(time.time())
    last = int(p.get("ts") or now)
    hours = (now - last) // 3600
    if hours > 0:
        for k in ("hunger", "happiness", "hygiene", "energy"):
            p[k] = max(0, int(p.get(k, 70)) - hours * PET_DECAY_HOUR)
        p["ts"] = last + hours * 3600
    else:
        p["ts"] = now
    return p


def pet_stage_for(xp, hatched=False):
    """Стадия питомца. До вылупления — всегда 1 ("Яйцо").
    После вылупления минимальная стадия 2 ("Малыш"), дальше — по XP."""
    st = 1
    for i, s in enumerate(PET_STAGES):
        if xp >= s["xp"]:
            st = i + 1
    if hatched and st < 2:
        st = 2
    return st


def pet_power(p, acc=None):
    """Боевая сила питомца: вид + стадия.

    Если активен бустер «Звездопад+», сила +25%.
    """
    base = 1
    for s in PET_SPECIES:
        if s["id"] == p.get("species"):
            base = int(s["pow"])
    st = pet_stage_for(int(p.get("xp", 0)), bool(p.get("species")))
    power = base * 5 + st * 3
    if acc is not None:
        try:
            st_acc = acc.get("star") or {}
            if int(st_acc.get("boost_until", 0)) > int(time.time()):
                power = int(power * 1.25)
        except Exception:
            pass
    return power


def pet_view(acc):
    p = pet_acc(acc)
    pet_decay(p)
    stage = pet_stage_for(int(p.get("xp", 0)), bool(p.get("species")))
    p["stage"] = stage
    sp = None
    for s in PET_SPECIES:
        if s["id"] == p.get("species"):
            sp = s
    nxt = PET_STAGES[stage]["xp"] if stage < len(PET_STAGES) else None
    return {"stage": stage,
            "stage_name": PET_STAGES[stage - 1]["name"],
            "stage_emoji": PET_STAGES[stage - 1]["emoji"],
            "xp": int(p.get("xp", 0)),
            "next_xp": nxt,
            "hatched": bool(p.get("species")),
            "hatch": int(p.get("hatch", 0)),
            "need_hatch": PET_HATCH_TAPS,
            "species": sp,
            "hunger": int(p.get("hunger", 70)),
            "happiness": int(p.get("happiness", 70)),
            "hygiene": int(p.get("hygiene", 70)),
            "energy": int(p.get("energy", 70)),
            "shields": int(p.get("shields", 1)),
            "power": pet_power(p, acc),
            "cd": p.get("cd", {}),
            "actions": PET_ACTIONS,
            "stage_up_cost": PET_STAGE_UP.get(stage, 0),
            "stage_up_stage": stage,
            "stage_max": PET_STAGE_UP_MAX,
            "now": int(time.time())}


def pet_add_xp(d, acc, kind, amount=None):
    """Начислить питомцу опыт. Возвращает номер новой стадии, если вырос."""
    pts = amount if amount is not None else PET_XP.get(kind, 0)
    if pts <= 0:
        return 0
    p = pet_acc(acc)
    if not p.get("species"):
        return 0
    old = pet_stage_for(int(p.get("xp", 0)), bool(p.get("species")))
    p["xp"] = int(p.get("xp", 0)) + int(pts)
    new = pet_stage_for(int(p["xp"]), bool(p.get("species")))
    if new > old:
        p["stage"] = new
        p["shields"] = int(p.get("shields", 1)) + 1
        return new
    return 0


def _pet_roll_species():
    r = random.random()
    if r < 0.60:
        rar = "common"
    elif r < 0.85:
        rar = "rare"
    elif r < 0.97:
        rar = "epic"
    else:
        rar = "legend"
    for s in PET_SPECIES:
        if s["rar"] == rar:
            return s
    return PET_SPECIES[0]


def pet_stage_up_cost(p):
    """Цена прокачки текущей стадии питомца в осколках (десятых звезды)."""
    stage = pet_stage_for(int(p.get("xp", 0)), bool(p.get("species")))
    return PET_STAGE_UP.get(stage, 0), stage


async def api_pet(request):
    """Питомец: состояние, вылупление, уход (кормление/игра/мытьё/сон)."""
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
    action = str(body.get("action", "state"))

    d = load_store(); acc = acc_of(d, uid)
    p = pet_acc(acc); pet_decay(p)

    if action == "hatch":
        if p.get("species"):
            save_store(d)
            return web.json_response({"ok": False, "error": "already hatched",
                                      "pet": pet_view(acc)})
        p["hatch"] = int(p.get("hatch", 0)) + 1
        if p["hatch"] >= PET_HATCH_TAPS:
            sp = _pet_roll_species()
            p["species"] = sp["id"]
            p["stage"] = 1
            p["xp"] = 0
            save_store(d)
            return web.json_response({"ok": True, "hatched": True, "species": sp,
                                      "pet": pet_view(acc), **wallet_view(acc)})
        save_store(d)
        return web.json_response({"ok": True, "hatched": False,
                                  "pet": pet_view(acc)})

    if action == "upgrade":
        # Прокачка стадии питомца за осколки. Стадия растёт мгновенно,
        # но только если хватает осколков — и только до 5-й стадии.
        if not p.get("species"):
            save_store(d)
            return web.json_response({"ok": False, "error": "not hatched",
                                      "pet": pet_view(acc)})
        cost, stage = pet_stage_up_cost(p)
        if stage >= PET_STAGE_UP_MAX:
            save_store(d)
            return web.json_response({"ok": False, "error": "max stage",
                                      "pet": pet_view(acc)})
        if cost <= 0:
            save_store(d)
            return web.json_response({"ok": False, "error": "max stage",
                                      "pet": pet_view(acc)})
        have = shards_of(acc)
        if have < cost:
            save_store(d)
            return web.json_response({"ok": False, "error": "not enough shards",
                                      "have": have, "need": cost,
                                      "pet": pet_view(acc), **wallet_view(acc)})
        shards_add(acc, -cost, "pet_stage")
        # Даём XP до следующей стадии с запасом: ровно до порога.
        target = PET_STAGES[stage]["xp"]
        old = pet_stage_for(int(p.get("xp", 0)), True)
        p["xp"] = max(int(p.get("xp", 0)), target)
        new = pet_stage_for(int(p["xp"]), True)
        p["stage"] = new
        if new > old:
            p["shields"] = int(p.get("shields", 1)) + 1
        save_store(d)
        return web.json_response({"ok": True, "stage": new,
                                  "cost": cost, "pet": pet_view(acc),
                                  **wallet_view(acc)})

    if action == "care":
        key = str(body.get("kind", ""))
        a = PET_ACTIONS.get(key)
        if not a:
            save_store(d)
            return web.json_response({"ok": False, "error": "bad action",
                                      "pet": pet_view(acc)})
        if not p.get("species"):
            save_store(d)
            return web.json_response({"ok": False, "error": "not hatched",
                                      "pet": pet_view(acc)})
        now = int(time.time())
        last = int((p.get("cd", {}) or {}).get(key, 0))
        if now - last < PET_ACTION_CD:
            save_store(d)
            return web.json_response({"ok": False, "error": "cooldown",
                                      "wait": PET_ACTION_CD - (now - last),
                                      "pet": pet_view(acc)})
        cost = int(a["cost"])
        if cost and acc["coins"] < cost:
            save_store(d)
            return web.json_response({"ok": False, "error": "not enough coins",
                                      **wallet_view(acc), "pet": pet_view(acc)})
        if cost:
            acc["coins"] -= cost
        need = a["need"]
        p[need] = min(100, int(p.get(need, 70)) + PET_ACTION_GAIN)
        p.setdefault("cd", {})[key] = now
        pet_add_xp(d, acc, "care", 5)
        save_store(d)
        return web.json_response({"ok": True, "kind": key, "need": need,
                                  **wallet_view(acc), "pet": pet_view(acc)})

    save_store(d)
    return web.json_response({"ok": True, "pet": pet_view(acc), **wallet_view(acc)})


# ---------------- АСИНХРОННАЯ ДУЭЛЬ: бой против «тени» ----------------
# Оба соперника НЕ должны быть онлайн одновременно. Сервер хранит последний
# результат игрока («тень»): атакующий играет раунд на сервере, сервер
# сравнивает с сохранённым результатом жертвы.
DUEL_TICKETS = 1          # сколько билетов ставится в бой
DUEL_WIN_TICKETS = 2      # сколько билетов забирает победитель
DUEL_COOLDOWN = 600       # 10 минут между боями с одной жертвой
DUEL_DAILY_LIMIT = 5      # не больше 5 боёв в сутки
DUEL_QUESTIONS = 5        # вопросов в бою


def duel_shadow(d, uid):
    """«Тень» игрока: его последний результат в квизе + сила питомца."""
    a = d.get(str(uid))
    if not isinstance(a, dict):
        return None
    # ВНИМАНИЕ: раньше здесь читалось несуществующее поле "quiz_best", поэтому
    # best всегда было 0, и duel_targets отсеивал ВСЕХ соперников как пустых —
    # дуэли были недоступны вообще. Настоящее поле называется "best".
    best = int(a.get("quiz_best", 0) or a.get("best", 0) or 0)
    streak = int(a.get("quiz_streak", 0) or 0)
    p = a.get("pet") or {}
    power = 0
    if p.get("species"):
        power = pet_power(p, a)
    return {"uid": str(uid),
            "name": a.get("name") or "Игрок",
            "best": best,
            "streak": streak,
            "power": power,
            "shadow": True}


def duel_acc(acc):
    """Состояние дуэлей игрока: счётчики и последние бои."""
    x = acc.setdefault("duel", {"day": "", "count": 0, "log": []})
    today = today_key()
    if x.get("day") != today:
        x["day"] = today
        x["count"] = 0
    x.setdefault("count", 0)
    x.setdefault("log", [])
    if len(x["log"]) > 30:
        del x["log"][:-30]
    return x


def _duel_questions():
    """Набор вопросов для боя (серверная проверка ответов)."""
    idx = random.sample(range(len(QUIZ_BANK)), min(DUEL_QUESTIONS, len(QUIZ_BANK)))
    return [{"id": i, "q": QUIZ_BANK[i]["q"], "o": QUIZ_BANK[i]["o"]} for i in idx]


def duel_targets(d, uid):
    """Список соперников: другие игроки с «тенью» (не сам и не пустые)."""
    rows = []
    for k, a in d.items():
        if not k.isdigit() or k == str(uid) or not isinstance(a, dict):
            continue
        sh = duel_shadow(d, k)
        if not sh:
            continue
        if not sh["best"] and not sh["power"]:
            continue
        rows.append(sh)
    rows.sort(key=lambda r: (-(r["best"] + r["power"] * 5), r["name"]))
    return rows[:20]



# ---------------- Приглашения в дуэль (PvP через Telegram-чат) -------------
# Идея: игрок жмёт "вызвать друга" -> бот пишет другу сообщение со ссылкой
# вида t.me/<bot>?start=duel_<код>. Друг открывает Mini App с этой меткой,
# принимает вызов, и оба играют один набор вопросов каждый со своей
# стороны. Когда оба ответили — сервер сверяет результаты.
INVITE_TTL = 3600 * 24        # приглашение живёт сутки


def _pvp_public(pvp):
    """PvP без технических полей — то, что отдаём клиенту."""
    return {"code": pvp.get("code"), "status": pvp.get("status", "playing"),
            "winner": pvp.get("winner"),
            "host": {"name": pvp["host"]["name"], "score": pvp["host"]["score"],
                     "done": pvp["host"]["done"]},
            "guest": {"name": pvp["guest"]["name"], "score": pvp["guest"]["score"],
                      "done": pvp["guest"]["done"]},
            "qs": pvp.get("qs", [])}


def _invites(d):
    return d.setdefault("_invites", {})


def _invite_code():
    return "d" + os.urandom(5).hex()


def _pvp_store(d):
    return d.setdefault("_pvp", {})


async def accept_duel_invite(message, code):
    """Гость принимает вызов по коду из ссылки. Возвращает (ok, текст)."""
    d = load_store()
    inv = _invites(d).get(code)
    if not inv:
        return False, ("\U0001F614 Это приглашение больше не действует. "
                       "Попроси друга вызвать тебя ещё раз.")
    if int(time.time()) - int(inv.get("ts", 0)) > INVITE_TTL:
        return False, "\U0001F614 Приглашение протухло (оно живёт сутки)."
    uid = message.from_user.id
    if str(inv.get("host")) == str(uid):
        return False, "\U0001F610 Это твой собственный вызов. Вызови друга."
    acc = acc_of(d, uid)
    p = pet_acc(acc)
    if not p.get("species"):
        return False, ("\U0001F422 Сначала вылупи питомца — иначе в бою у тебя "
                       "не будет силы.")
    pvp = _pvp_store(d)
    if code not in pvp:
        pvp[code] = {"code": code,
                     "host": {"uid": str(inv.get("host")),
                              "name": inv.get("host_name", "Игрок"),
                              "score": 0, "done": False, "answers": []},
                     "guest": {"uid": str(uid),
                               "name": acc.get("name") or (message.from_user.first_name or "Игрок"),
                               "score": 0, "done": False, "answers": []},
                     "qs": _duel_questions(), "ts": int(time.time()),
                     "status": "playing"}
    _invites(d).pop(code, None)
    save_store(d)
    host_name = inv.get("host_name", "Игрок")
    return True, ("\U0001F94D <b>Вызов принят!</b>\n\n"
                  f"Ты играешь против {host_name}. 5 вопросов, "
                  "кто больше угадает — тот победил.\n\n"
                  f"\U0001F3AE <a href=\"{WEBAPP_URL}?startapp=duel_{code}\">Начать дуэль</a>")


def _pvp_summary(pvp):
    """Готов ли бой к подведению итогов: оба ответили."""
    return all(pvp.get(k, {}).get("done") for k in ("host", "guest"))


async def _send_invite_to_friend(friend_username, code, host_name):
    """Написать другу приглашение от имени бота."""
    link = f"https://t.me/{BOT_USERNAME}?start=duel_{code}"
    text = ("\U0001F94D <b>Вызов в дуэль!</b>\n\n"
            f"{host_name} вызывает тебя сыграть в ПОДТЕКСТ на равных.\n"
            "5 вопросов, кто больше угадает подтекстов — тот победил.\n\n"
            f"\U0001F3AE <a href=\"{link}\">Принять вызов</a>")
    try:
        await bot.send_message(chat_id=f"@{friend_username}", text=text,
                               parse_mode="HTML")
        return True
    except Exception as e:
        logging.warning("send invite failed: %s", e)
        return False


async def api_duel(request):
    """Дуэль: подготовка боя, ответы и разбор. Всё считает сервер."""
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
    action = str(body.get("action", "state"))

    d = load_store(); acc = acc_of(d, uid)
    x = duel_acc(acc)
    p = pet_acc(acc); pet_decay(p)

    if action == "invite":
        friend = str(body.get("friend", "")).strip().lstrip("@")
        if not friend or len(friend) > 32:
            save_store(d)
            return web.json_response({"ok": False, "error": "bad friend"})
        if not p.get("species"):
            save_store(d)
            return web.json_response({"ok": False, "error": "not hatched",
                                      "msg": "Сначала вылупи питомца"})
        if _duel_ticket_count(acc) < DUEL_TICKETS:
            save_store(d)
            return web.json_response({"ok": False, "error": "not enough tickets",
                                      "tickets": _duel_ticket_count(acc)})
        d2 = load_store()
        code = _invite_code()
        host = acc.get("name") or "Игрок"
        _invites(d2)[code] = {"host": str(uid), "host_name": host,
                              "friend": friend, "ts": int(time.time())}
        save_store(d2)
        sent = await _send_invite_to_friend(friend, code, host)
        if not sent:
            # приглашение не ушло — не жжём билет, чистим запись
            d2 = load_store()
            _invites(d2).pop(code, None)
            save_store(d2)
            return web.json_response({"ok": False, "error": "cannot send invite",
                                      "msg": "Не удалось написать. Проверь, что юзернейм верный и он не заблокировал бота."})
        _duel_spend_ticket(acc)
        save_store(d)
        return web.json_response({"ok": True, "code": code, "friend": friend,
                                  "tickets": _duel_ticket_count(acc)})

    if action == "accept":
        code = str(body.get("code", "")).strip()
        d2 = load_store()
        inv = _invites(d2).get(code)
        if not inv:
            save_store(d2)
            return web.json_response({"ok": False, "error": "invite not found"})
        if int(time.time()) - int(inv.get("ts", 0)) > INVITE_TTL:
            save_store(d2)
            return web.json_response({"ok": False, "error": "invite expired"})
        if str(inv.get("host")) == str(uid):
            save_store(d2)
            return web.json_response({"ok": False, "error": "self invite"})
        if not p.get("species"):
            save_store(d2)
            return web.json_response({"ok": False, "error": "not hatched",
                                      "msg": "Сначала вылупи питомца"})
        # бой начинаем один раз: гость вступает — создаём общий набор вопросов
        pvp = _pvp_store(d2)
        if code not in pvp:
            pvp[code] = {"code": code,
                         "host": {"uid": str(inv.get("host")),
                                  "name": inv.get("host_name", "Игрок"),
                                  "score": 0, "done": False, "answers": []},
                         "guest": {"uid": str(uid),
                                   "name": acc.get("name") or "Игрок",
                                   "score": 0, "done": False, "answers": []},
                         "qs": _duel_questions(), "ts": int(time.time()),
                         "status": "playing"}
        _invites(d2).pop(code, None)
        save_store(d2)
        return web.json_response({"ok": True, "pvp": _pvp_public(pvp[code])})

    if action == "pvp_state":
        code = str(body.get("code", "")).strip()
        d2 = load_store()
        pvp = _pvp_store(d2).get(code)
        if not pvp:
            save_store(d2)
            return web.json_response({"ok": False, "error": "no pvp"})
        return web.json_response({"ok": True, "pvp": _pvp_public(pvp)})

    if action == "pvp_answer":
        code = str(body.get("code", "")).strip()
        try:
            qid = int(body.get("id", -1))
            chosen = int(body.get("choice", -1))
        except Exception:
            return web.json_response({"ok": False, "error": "bad params"}, status=400)
        d2 = load_store()
        pvp = _pvp_store(d2).get(code)
        if not pvp:
            save_store(d2)
            return web.json_response({"ok": False, "error": "no pvp"})
        side = "host" if str(pvp["host"]["uid"]) == str(uid) else (
            "guest" if str(pvp["guest"]["uid"]) == str(uid) else None)
        if side is None:
            save_store(d2)
            return web.json_response({"ok": False, "error": "not participant"})
        if pvp.get("status") != "playing":
            save_store(d2)
            return web.json_response({"ok": True, "pvp": _pvp_public(pvp)})
        if qid < 0 or qid >= len(QUIZ_BANK):
            return web.json_response({"ok": False, "error": "bad question"}, status=400)
        # Вопрос должен быть из набора именно этого боя — иначе можно было бы
        # набивать очки чужими вопросами.
        if qid not in {int(q.get("id")) for q in (pvp.get("qs") or [])}:
            save_store(d2)
            return web.json_response({"ok": False, "error": "question not in this match"})
        q = QUIZ_BANK[qid]
        if chosen < 0 or chosen >= len(q["o"]):
            return web.json_response({"ok": False, "error": "bad choice"}, status=400)
        done_ids = {int(a.get("id")) for a in pvp[side].get("answers", [])}
        if qid in done_ids:
            save_store(d2)
            return web.json_response({"ok": False, "error": "already answered"})
        ok = (chosen == int(q["c"]))
        pvp[side]["answers"].append({"id": qid, "choice": chosen, "ok": ok})
        if ok:
            pvp[side]["score"] = int(pvp[side].get("score", 0)) + 1
        if len(pvp[side]["answers"]) >= len(pvp.get("qs") or []):
            pvp[side]["done"] = True
        if _pvp_summary(pvp):
            pvp["status"] = "finished"
            pvp["winner"] = ("host" if int(pvp["host"]["score"]) >= int(pvp["guest"]["score"])
                             else "guest")
        save_store(d2)
        # correct/score нужны клиенту, чтобы подсветить ответ и показать очки.
        return web.json_response({"ok": True, "correct": ok,
                                  "score": int(pvp[side].get("score", 0)),
                                  "pvp": _pvp_public(pvp)})

    if action == "state":
        save_store(d)
        return web.json_response({"ok": True, "targets": duel_targets(d, uid),
                                  "tickets": _duel_ticket_count(acc),
                                  "cost": DUEL_TICKETS,
                                  "cooldown": DUEL_COOLDOWN,
                                  "daily_limit": DUEL_DAILY_LIMIT,
                                  "used": int(x.get("count", 0)),
                                  "pet": pet_view(acc),
                                  "log": x.get("log", [])[-10:]})

    if action == "start":
        target = str(body.get("target", "")).strip()
        if not target or target == str(uid):
            save_store(d)
            return web.json_response({"ok": False, "error": "bad target"})
        sh = duel_shadow(d, target)
        if not sh:
            save_store(d)
            return web.json_response({"ok": False, "error": "player not found"})
        if not p.get("species"):
            save_store(d)
            return web.json_response({"ok": False, "error": "not hatched",
                                      "msg": "Сначала вылупи питомца"})
        if int(x.get("count", 0)) >= DUEL_DAILY_LIMIT:
            save_store(d)
            return web.json_response({"ok": False, "error": "daily limit"})
        # кулдаун между боями с одной жертвой
        for e in x.get("log", []):
            if e.get("target") == target and e.get("day") == today_key():
                left = DUEL_COOLDOWN - (int(time.time()) - int(e.get("ts", 0)))
                if left > 0:
                    save_store(d)
                    return web.json_response({"ok": False, "error": "cooldown",
                                              "wait": left})
        if _duel_ticket_count(acc) < DUEL_TICKETS:
            save_store(d)
            return web.json_response({"ok": False, "error": "not enough tickets",
                                      "tickets": _duel_ticket_count(acc)})
        _duel_spend_ticket(acc)
        x["count"] = int(x.get("count", 0)) + 1
        battle = {"target": target, "target_name": sh["name"],
                  "shadow_best": sh["best"], "shadow_power": sh["power"],
                  "qs": _duel_questions(), "answers": [], "score": 0,
                  "power": pet_power(p, acc), "ts": int(time.time()),
                  "day": today_key()}
        x.setdefault("battle", {})[target] = battle
        save_store(d)
        return web.json_response({"ok": True, "battle": battle,
                                  "tickets": _duel_ticket_count(acc),
                                  "pet": pet_view(acc)})

    if action == "answer":
        target = str(body.get("target", "")).strip()
        battle = (x.get("battle") or {}).get(target)
        if not battle:
            save_store(d)
            return web.json_response({"ok": False, "error": "no battle"})
        try:
            qid = int(body.get("id", -1))
            chosen = int(body.get("choice", -1))
        except Exception:
            return web.json_response({"ok": False, "error": "bad params"}, status=400)
        done = {int(a.get("id")) for a in battle.get("answers", [])}
        if qid in done:
            save_store(d)
            return web.json_response({"ok": False, "error": "already answered"})
        if qid < 0 or qid >= len(QUIZ_BANK):
            return web.json_response({"ok": False, "error": "bad question"}, status=400)
        q = QUIZ_BANK[qid]
        if chosen < 0 or chosen >= len(q["o"]):
            return web.json_response({"ok": False, "error": "bad choice"}, status=400)
        good = (chosen == q["c"])
        battle.setdefault("answers", []).append({"id": qid, "good": good})
        if good:
            battle["score"] = int(battle.get("score", 0)) + 10
        save_store(d)
        return web.json_response({"ok": True, "correct": good,
                                  "answer": q["o"][q["c"]], "hint": q["h"],
                                  "score": battle["score"],
                                  "left": len(battle.get("answers", []))})

    if action == "finish":
        target = str(body.get("target", "")).strip()
        battle = (x.get("battle") or {}).get(target)
        if not battle:
            save_store(d)
            return web.json_response({"ok": False, "error": "no battle"})
        score = int(battle.get("score", 0))
        shadow_best = int(battle.get("shadow_best", 0))
        my_power = int(battle.get("power", 0))
        sh_power = int(battle.get("shadow_power", 0))
        duel_drop = 0
        # Победа: набрали не меньше «тени» И сила питомца не ниже.
        win = score >= shadow_best and my_power >= sh_power
        gained = 0
        lost = 0
        if win:
            gained = DUEL_WIN_TICKETS
            _duel_add_ticket(acc, DUEL_WIN_TICKETS)
            acc["coins"] += 40
            pet_add_xp(d, acc, "duel")
            # Победа в дуэли — игровое событие: капают осколки и выплата из пула.
            _du, _dr = try_drop_shard(acc, "duel")
            duel_drop += _du
            _pu = pool_payout(acc, "duel")
            duel_drop += _pu
        else:
            # Билет уже списан на старте боя (_duel_spend_ticket в action="start"),
            # повторно списывать нельзя — иначе поражение стоит два билета.
            lost = DUEL_TICKETS
            # Щит питомца полностью компенсирует проигранный билет
            if int(p.get("shields", 0)) > 0:
                p["shields"] = int(p["shields"]) - 1
                gained = DUEL_TICKETS
                _duel_add_ticket(acc, DUEL_TICKETS)
        entry = {"target": target, "target_name": battle.get("target_name"),
                 "win": win, "score": score, "shadow_best": shadow_best,
                 "power": my_power, "shadow_power": sh_power,
                 "gained": gained, "lost": lost,
                 "ts": int(time.time()), "day": today_key()}
        x.setdefault("log", []).append(entry)
        if len(x["log"]) > 30:
            del x["log"][:-30]
        (x.get("battle") or {}).pop(target, None)
        save_store(d)
        return web.json_response({"ok": True, "result": entry,
                                  "tickets": _duel_ticket_count(acc),
                                  "pet": pet_view(acc),
                                  "drop": int(duel_drop),
                                  **wallet_view(acc)})

    save_store(d)
    return web.json_response({"ok": False, "error": "unknown action"})


def _duel_ticket_count(acc):
    """Сколько боевых билетов у игрока (общие билеты конкурса)."""
    c = contest_tickets(acc)
    tiers = c.get("tiers") or {}
    total = 0
    for row in tiers.values():
        total += int(row.get("tickets", 0) or 0) + int(row.get("paid_tickets", 0) or 0)
    return total


def _duel_spend_ticket(acc):
    """Списать боевой билет: сначала бесплатные, потом платные."""
    c = contest_tickets(acc)
    tiers = c.get("tiers") or {}
    for row in tiers.values():
        n = int(row.get("tickets", 0) or 0)
        if n > 0:
            row["tickets"] = n - 1
            return True
    for row in tiers.values():
        n = int(row.get("paid_tickets", 0) or 0)
        if n > 0:
            row["paid_tickets"] = n - 1
            return True
    return False


def _duel_add_ticket(acc, n=1):
    """Начислить боевой билет.

    Раньше билеты капали только в АКТИВНУЮ ступень конкурса. Если ступень
    ещё не открыта (t1 требует match-3 ур.2 + серию из 3 квизов), выигранные
    в дуэли билеты просто исчезали. Теперь начисляем в активную ступень, а
    если её нет — в ступень с наименьшим числом билетов, иначе — в t1.
    """
    c = contest_tickets(acc)
    tiers = c.get("tiers") or {}
    tier = active_tier(acc)
    if not tier:
        if not tiers:
            tiers["t1"] = {"points": 0, "paid_tickets": 0, "tickets": 0}
            tier = {"id": "t1"}
        else:
            tid = min(tiers.items(),
                      key=lambda kv: int(kv[1].get("tickets", 0) or 0)
                      + int(kv[1].get("paid_tickets", 0) or 0))[0]
            tier = {"id": tid}
    row = tiers.setdefault(
        tier["id"], {"points": 0, "paid_tickets": 0, "tickets": 0})
    row["tickets"] = int(row.get("tickets", 0) or 0) + int(n)
    return True


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
    app.router.add_get("/api/terms", api_terms)
    app.router.add_post("/api/terms", api_terms)
    app.router.add_post("/api/wallet", api_wallet)
    app.router.add_post("/api/stars", api_stars)
    app.router.add_post("/api/sub", api_sub)
    app.router.add_post("/api/sub/buy", api_sub_buy)
    app.router.add_get("/api/gifts/public", api_gifts_public)
    app.router.add_get("/api/gifts/liquidity", api_gifts_public)
    app.router.add_post("/api/stars/gift", api_gift_buy)
    app.router.add_post("/api/stars/boost", api_fall_boost)
    app.router.add_post("/api/action", api_action)
    app.router.add_post("/api/quiz", api_quiz)
    app.router.add_post("/api/quiz/public", api_quiz_public)
    app.router.add_post("/api/answer", api_answer)
    app.router.add_post("/api/admin/credit", api_admin_credit)
    app.router.add_post("/api/m3", api_m3)
    app.router.add_post("/api/visit", api_visit)
    app.router.add_post("/api/referral", api_referral)
    app.router.add_post("/api/promo", api_promo)
    app.router.add_post("/api/promo/click", api_promo_click)
    app.router.add_post("/api/promo/claim", api_promo_claim)
    app.router.add_post("/api/mira/hint", api_mira_hint)
    app.router.add_post("/api/ask_shards", api_ask_shards)
    app.router.add_post("/api/gen_pack", api_gen_pack)
    app.router.add_post("/api/gen_spend", api_gen_spend)
    app.router.add_post("/api/affiliate", api_affiliate)
    app.router.add_post("/api/promo/stats", api_promo_stats)
    app.router.add_post("/api/promo/return", api_promo_return)
    app.router.add_post("/api/ads/stats", api_ads_stats)
    app.router.add_post("/api/leaderboard", api_leaderboard)
    app.router.add_post("/api/attack", api_attack)
    app.router.add_post("/api/share/score", api_share_score)
    app.router.add_post("/api/channel/sub", api_channel_sub)
    app.router.add_post("/api/ad/reward", api_ad_reward)
    app.router.add_post("/api/ads/serve", api_ads_serve)
    app.router.add_post("/api/ads/click", api_ads_click)
    app.router.add_post("/api/freeze", api_freeze)

    app.router.add_post("/api/cards", api_cards)
    app.router.add_post("/api/tourney", api_tourney)
    app.router.add_post("/api/contest", api_contest)
    app.router.add_post("/api/contest/buy", api_contest_buy)
    app.router.add_post("/api/bp", api_bp)
    app.router.add_post("/api/bp/claim", api_bp_claim)
    app.router.add_post("/api/pet", api_pet)
    app.router.add_post("/api/duel", api_duel)

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
    types.BotCommand(command="starbalance", description="Баланс звёзд: бота и аккаунта"),
    types.BotCommand(command="transferstars", description="Перевести звёзды с аккаунта боту"),
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

# Дополняем пул постов из quiz_pack (если модуль загрузился выше).
try:
    _seen_posts = {p[0] for p in DAILY_POSTS}
    for _p in EXTRA_POSTS:
        if _p[0] not in _seen_posts:
            DAILY_POSTS.append(_p)
    logging.info("daily posts pool: %d", len(DAILY_POSTS))
except Exception as _dp_e:  # noqa: BLE001
    logging.warning("extra posts not loaded: %s", _dp_e)

_poll_state = {}

# ---------------- Голосование в постах канала ----------------
# Раньше кнопки работали вхолостую: on_vote только отвечал "твой выбор".
# Теперь считаем голоса в store ("_votes") и на следующий день публикуем
# в канал разбор: верный вариант и реальный процент голосов.
_ORD_WORDS = {
    "первое": 0, "первый": 0, "первый вариант": 0, "вариант 1": 0, "первый ответ": 0,
    "второе": 1, "второй": 1, "второй вариант": 1, "вариант 2": 1, "второй ответ": 1,
    "третье": 2, "третий": 2, "третий вариант": 2, "вариант 3": 2, "третий ответ": 2,
    "четвёртое": 3, "четвертое": 3, "четвёртый": 3, "четвертый": 3,
    "четвёртый вариант": 3, "четвертый вариант": 3, "вариант 4": 3, "четвёртый ответ": 3,
}


def _correct_index(options, answer):
    """Индекс правильного варианта по тексту разбора в посте.

    Ответ в DAILY_POSTS написан словами ("Третье. ..."), поэтому ищем
    самое длинное совпавшее порядковое слово. Если разбор вдруг сменит
    формат — ищем текст ответа внутри варианта.
    """
    a = (answer or "").strip().lower()
    if not a:
        return None
    best = None
    for word, idx in _ORD_WORDS.items():
        if word in a and (best is None or len(word) > len(best)):
            best = word
    if best is not None:
        idx = _ORD_WORDS[best]
        if 0 <= idx < len(options):
            return idx
    for i, o in enumerate(options):
        if o.strip().lower() == a:
            return i
    return None


def _votes_store():
    """Все голоса по постам: {post_key: {"counts": [...], "meta": {...}}}."""
    d = load_store()
    v = d.setdefault("_votes", {})
    return d, v

def _post_key(question):
    return hashlib.sha1(question.encode("utf-8")).hexdigest()[:12]

async def publish_previous_answer():
    """Опубликовать в канал разбор вчерашнего поста: ответ + статистика."""
    if not CHAT_ID:
        return
    d, votes = _votes_store()
    prev = votes.get("_last")
    if not prev:
        return
    meta = prev.get("meta") or {}
    q = meta.get("q")
    options = meta.get("o") or []
    correct = meta.get("c")
    if q is None or correct is None or not options:
        return
    counts = [int(x) for x in (prev.get("counts") or [])]
    while len(counts) < len(options):
        counts.append(0)
    total = sum(counts)
    lines = ""
    if total <= 0:
        head = "⏳ Голосов почти не было — отвечаем по смыслу."
    else:
        pct = lambda i: int(round(counts[i] * 100.0 / total))
        lines = "\n".join(
            f"{'✅' if i == correct else '▫️'} {i+1}. {o} — {pct(i)}%"
            for i, o in enumerate(options))
        head = f"🏆 Правильный ответ — {correct+1}. {options[correct]}"
        if counts[correct] == total:
            head += "\n\nВсе угадали. Вот это понимание подтекста."
    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=(f"\U0001F3C6 <b>Разбор поста</b>\n\n"
                  f"«{q}»\n\n"
                  f"{head}\n\n{lines}\n\n"
                  f"\U0001F3AF Голосов: {total}. "
                  f"Хочешь больше таких? \U0001F3AE <a href=\"https://t.me/podtekst_game_bot\">Играть: @podtekst_game_bot</a>"),
            parse_mode="HTML")
        logging.info("previous answer published, votes=%d", total)
    except Exception as e:
        logging.warning("publish_previous_answer failed: %s", e)
    finally:
        votes.pop("_last", None)
        save_store(d)

async def publish_daily_post():
    """Публикует ежедневный пост-разбор фразы с голосованием в канале."""
    if not CHAT_ID:
        return
    idx = int(time.time() // 86400) % len(DAILY_POSTS)
    question, options, answer, teaser = DAILY_POSTS[idx]
    # Сначала закрываем вчерашний пост разбором, иначе голоса потеряются.
    try:
        await publish_previous_answer()
    except Exception as e:
        logging.warning("publish_previous_answer failed: %s", e)
    # Вариантов бывает 3 или 4 — рендерим столько, сколько есть в посте,
    # и кнопок голосования столько же.
    # Верный вариант в DAILY_POSTS задан текстом разбора ("Третье. ...").
    # Разбираем порядковое слово — иначе канал врал бы в разборе.
    correct_idx = _correct_index(options, answer)
    if correct_idx is None:
        correct_idx = 0
    opt_lines = "\n".join(f"{i+1}. {o}" for i, o in enumerate(options))
    nums = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
    vote_row = [InlineKeyboardButton(text=nums[i], callback_data=f"vote:{i+1}")
                for i in range(len(options))]
    try:
        msg = await bot.send_message(
            chat_id=CHAT_ID,
            text=(f"\U0001F9E0 <b>Что он имел в виду?</b>\n\n"
                  f"{question}\n\n"
                  f"{opt_lines}\n\n"
                  f"\U0001F3AF Голосуйте, правильный ответ — в комментариях завтра.\n\n"
                  f"{teaser}\n\n"
                  f"\U0001F3AE <a href=\"https://t.me/podtekst_game_bot\">Играть: @podtekst_game_bot</a>"),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                vote_row,
                [InlineKeyboardButton(text="\U0001F3AE Играть", url=WEBAPP_URL)],
            ]))
        _poll_state["msg_id"] = msg.message_id
        d, votes = _votes_store()
        votes["_last"] = {"meta": {"q": question, "o": list(options), "c": correct_idx},
                          "counts": []}
        save_store(d)
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
        idx = int(choice) - 1
    except Exception:
        return
    d, votes = _votes_store()
    last = votes.get("_last") or {}
    counts = [int(x) for x in (last.get("counts") or [])]
    meta = last.get("meta") or {}
    n_opts = len(meta.get("o") or []) or 3
    while len(counts) < n_opts:
        counts.append(0)
    if not (0 <= idx < n_opts):
        return
    counts[idx] += 1
    last["counts"] = counts
    votes["_last"] = last
    save_store(d)
    total = sum(counts)
    try:
        await call.answer(f"\U0001F3AF Твой выбор: {choice}. Ответ завтра в канале! "
                          f"(\u0421\u0435\u0439\u0447\u0430\u0441: {total})", show_alert=False)
    except Exception as e:
        logging.warning("on_vote failed: %s", e)


# ================= ЧАТ ГЕНЕРАЦИИ =================
# Бот принимает промпт в группе-генераторе и возвращает картинку.
# Генерация идёт через pollinations.ai — ключ не нужен, поэтому себестоимость
# для нас нулевая и пакет звёзд держит маржу целиком.
GEN_API = "https://image.pollinations.ai/prompt/"
GEN_MODEL = "flux"
GEN_TIMEOUT = 180


def gen_chat_id(d):
    """Id чата-генератора. Узнаётся автоматически по первому сообщению."""
    return str(d.get("_gen_chat", "") or "")


async def gen_pollinations(prompt: str):
    """Скачивает картинку по промпту. Возвращает bytes или None."""
    import aiohttp
    q = urllib.parse.quote(prompt[:400])
    url = (f"{GEN_API}{q}?width=1024&height=1024&nologo=true"
           f"&model={GEN_MODEL}&seed={random.randint(1, 999999)}")
    timeout = aiohttp.ClientTimeout(total=GEN_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.get(url) as r:
            if r.status != 200:
                logging.warning("GEN http %s", r.status)
                return None
            data = await r.read()
            if len(data) < 2000:
                return None
            return data


@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def gen_group_handler(message: types.Message):
    """Промпт в группе-генераторе -> картинка. Списывает одну генерацию."""
    chat = message.chat
    # Канал проекта — не генератор, туда не вмешиваемся.
    try:
        if int(chat.id) == int(CHAT_ID):
            return
    except Exception:
        pass
    text = (message.text or message.caption or "").strip()
    if not text:
        return
    if text.startswith("/"):
        return
    d = load_store()
    # Автоопределение: первое сообщение в группе задаёт id генератора.
    d["_gen_chat"] = str(chat.id)
    uid = message.from_user.id if message.from_user else 0
    acc = acc_of(d, uid)
    left = int(acc.get("gen_left", 0))
    if left <= 0:
        save_store(d)
        await message.reply(
            "🎨 <b>Пакет генераций закончился</b>\n\n"
            "Докупи в игре: вкладка «✨ Звёзды» → «Нарисуть картинку».\n"
            f"Или прямо здесь: /genpack — пришлю инвойс.")
        return
    save_store(d)
    status = await message.reply("⏳ Рисую... это 10–30 секунд.")
    try:
        data = await gen_pollinations(text)
    except Exception as e:
        logging.warning("GEN error %s", e)
        data = None
    if not data:
        await status.edit_text("😕 Не получилось нарисовать. Попробуй другой промпт.")
        return
    d = load_store(); acc = acc_of(d, uid)
    acc["gen_left"] = max(0, int(acc.get("gen_left", 0)) - 1)
    acc["gen_used"] = int(acc.get("gen_used", 0)) + 1
    save_store(d)
    try:
        await bot.send_photo(chat_id=chat.id, photo=io.BytesIO(data),
                             caption=f"🎨 <b>{text[:200]}</b>\n\n"
                                     f"Осталось генераций: {acc['gen_left']}",
                             reply_to_message_id=message.message_id)
    except Exception as e:
        logging.warning("GEN send error %s", e)
    try:
        await status.delete()
    except Exception:
        pass


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
