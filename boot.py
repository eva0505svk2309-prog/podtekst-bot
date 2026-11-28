# -*- coding: utf-8 -*-
"""Стартовый обёрт ПОДТЕКСТ: перед запуском бота убирает чужие реферальные
коды и подставляет партнёрскую ссылку владельца.

Зачем: реферальные ссылки Telegram (startapp=ref_*, start=_tgr_*) определяют,
кому идёт комиссия за покупки звёздами. В коде исторически стоял код Миры
(8684637509) — комиссия уходила не владельцу. Здесь он заменяется на ссылку
владельца, без правки самого bot.py.
"""
import os
import runpy
import sys

SRC = "bot.py"
OUT = "bot_runtime.py"

# Партнёрская ссылка владельца (комиссия Telegram -> владельцу).
OWNER_LINK = os.environ.get(
    "OWNER_LINK",
    "https://t.me/podtekst_game_bot?start=_tgr_N0v2TKwzMTUy",
)


def _patch(text):
    # 1) Рекламный креатив bums: убираем чужой реферальный код.
    text = text.replace(
        'os.environ.get("AD_BUMS_URL", "https://t.me/bums/app?startapp=ref_8684637509")',
        'os.environ.get("AD_BUMS_URL", "https://t.me/bums/app")',
    )
    # 2) Fallback партнёрской ссылки в /api/affiliate: вместо чужого кода -
    #    ссылка владельца.
    text = text.replace(
        '            "https://t.me/%s?startapp=ref_%s" % (BOT_USERNAME, MIRA_REF))',
        '            os.environ.get("OWNER_LINK", OWNER_LINK))',
    )
    return text


def main():
    here = os.path.dirname(os.path.abspath(__file__)) or "."
    src = os.path.join(here, SRC)
    out = os.path.join(here, OUT)
    with open(src, "r", encoding="utf-8") as f:
        text = f.read()
    patched = _patch(text)
    with open(out, "w", encoding="utf-8") as f:
        f.write(patched)
    os.environ.setdefault("OWNER_LINK", OWNER_LINK)
    sys.argv = [SRC] + sys.argv[1:]
    runpy.run_path(out, run_name="__main__")


if __name__ == "__main__":
    main()
