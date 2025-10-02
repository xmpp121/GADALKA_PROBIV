# trigger deploy
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import logging
from collections import defaultdict

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, ConversationHandler, filters
)

# ---------- Логирование ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Константы/окружение ----------
CHOOSING, TYPING = range(2)

BREACHKA_API_KEY = os.environ.get("BREACHKA_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not BREACHKA_API_KEY or not TELEGRAM_TOKEN:
    raise RuntimeError("Set BREACHKA_API_KEY and TELEGRAM_TOKEN env vars")

# ---------- Вспомогательное ----------
_rx_date = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
_rx_year = re.compile(r"^\d{4}$")
_rx_fio = re.compile(r"^[А-ЯЁ][а-яё]+ [А-ЯЁ][а-яё]+ [А-ЯЁ][а-яё]+$")  # Фам Имя Отч
_rx_phone = re.compile(r"^\+?7\d{10}$|^8\d{10}$|^7\d{10}$|^\d{11}$")

def is_fio_query(text: str) -> bool:
    """
    Форматы:
      - 'Иванов Петр Петрович 06.04.1994'
      - 'Иванов Петр Петрович 1994'
    """
    parts = text.strip().split()
    if len(parts) != 4:
        return False
    fio = " ".join(parts[:3])
    tail = parts[3]
    return bool(_rx_fio.match(fio) and (_rx_date.match(tail) or _rx_year.match(tail)))

def is_phone_query(text: str) -> bool:
    t = re.sub(r"\D", "", text)
    if t.startswith("8"):
        t = "7" + t[1:]
    return bool(_rx_phone.match(t if t.startswith("7") else "7"+t[-10:]))

def normalize_phone(text: str) -> str:
    t = re.sub(r"\D", "", text)
    if t.startswith("8"):
        t = "7" + t[1:]
    if not t.startswith("7"):
        t = "7" + t[-10:]
    return t

def lkeys(d: dict) -> dict:
    """ключи в нижний регистр (для нечувствительности к регистру от Breachka)."""
    return {(k.lower() if isinstance(k, str) else k): v for k, v in d.items()}

# ---------- Вызов API ----------
def call_breachka(single_query: str, need_country: bool) -> dict:
    """
    Формирует корректный JSON c правильным регистром полей.
    FindType — Detail (подробно).
    CountryType добавляем только если нужен (ФИО-запрос).
    """
    url = "https://www.breachka.com/api/v1/find/mass"
    headers = {"X-Api-Key": BREACHKA_API_KEY, "Content-Type": "application/json"}
    payload = {
        "Requests": [single_query],
        "FindType": "Detail",
    }
    if need_country:
        payload["CountryType"] = "RU"

    r = requests.post(url, headers=headers, json=payload, timeout=40)
    r.raise_for_status()
    return r.json()

# ---------- Форматирование ответа ----------
def fmt(resp: dict) -> str:
    parts = []

    # Отметим невалидные запросы
    bad = resp.get("NotValidRequests") or resp.get("notValidRequests") or []
    if bad:
        parts.append("❗ Запросы не прошли валидацию:\n" + "\n".join(f"- {b}" for b in bad))

    outer = resp.get("Responses") or resp.get("responses") or []
    if not outer:
        parts.append("⚠️ Ничего не найдено по валидным запросам.")
        return "\n".join(parts)

    for block in outer:
        b = lkeys(block)
        q = b.get("query") or b.get("Query") or ""
        parts.append(f"🔎 *Запрос:* `{q}`")

        inner = b.get("responses", [])
        if not inner:
            parts.append("— Нет ответов.")
            continue

        # агрегируем все внутренние записи
        agg = defaultdict(list)
        sources_acc = []

        for one in inner:
            o = lkeys(one)
            for k, v in o.items():
                if k == "sources" and isinstance(v, list):
                    sources_acc.extend(v)
                elif isinstance(v, list):
                    for val in v:
                        if val is None or val == "":
                            continue
                        if val not in agg[k]:
                            agg[k].append(val)

        def add(name: str, key: str, limit: int = 12):
            vals = agg.get(key, [])
            if vals:
                shown = vals[:limit]
                more = len(vals) - len(shown)
                s = "; ".join(map(str, shown)) + (f" (и ещё {more})" if more > 0 else "")
                parts.append(f"*{name}:* {s}")

        # поля
        add("Телефоны", "phone")
        add("Оператор/Регион", "opsos")
        add("ФИО", "fio")
        add("Имена/Псевдонимы", "names")
        add("Дата рождения", "born")
        add("Адреса", "address")
        add("Транспорт", "transport")
        add("Email", "email")
        add("Пароли", "password")
        add("URL/Профили", "url")
        add("Юзернеймы", "username")
        add("ICQ", "icq")
        add("Skype", "skype")
        add("Telegram", "telegram")
        add("Работа", "work")
        add("Адреса работы", "workaddress")  # у некоторых ответов camelCase
        add("Паспорта", "passport")
        add("ИНН", "inn")
        add("СНИЛС", "snils")
        add("Долги", "debts")
        add("Родственники", "relatives")

        # источники
        if sources_acc:
            labels = []
            for s in sources_acc:
                s = lkeys(s)
                label = s.get("name") or s.get("url") or "Источник"
                if label not in labels:
                    labels.append(label)
            parts.append(f"*Источники:* {', '.join(labels[:8])}" + ("…" if len(labels) > 8 else ""))

        parts.append("— — —")

    return "\n".join(parts)

# ---------- Telegram ----------
def main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧑‍💼 ФИО + дата/год", callback_data="fio")],
        [InlineKeyboardButton("📱 Телефон", callback_data="phone")],
    ])

def again_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔎 Новый поиск", callback_data="newsearch")]])

async def start(update: Update, _: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Выберите тип поиска:",
        reply_markup=main_kb()
    )
    return CHOOSING

async def new_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("Выберите тип поиска:", reply_markup=main_kb())
    return CHOOSING

async def choose_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "fio":
        context.user_data["type"] = "fio"
        await q.edit_message_text(
            "✍ Введите запрос в ОДНОЙ строке:\n"
            "• `Иванов Петр Петрович 06.04.1994`\n"
            "• `Иванов Петр Петрович 1994`",
            parse_mode="Markdown"
        )
    else:
        context.user_data["type"] = "phone"
        await q.edit_message_text(
            "✍ Введите номер телефона в формате `79250000000` (можно с +7 или 8).",
            parse_mode="Markdown"
        )
    return TYPING

async def text_recv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()

    # определяем тип
    t = context.user_data.get("type")
    need_country = False
    query = raw

    if t == "fio":
        if not is_fio_query(raw):
            await update.message.reply_text(
                "⚠️ Формат не распознан. Примеры:\n"
                "`Иванов Петр Петрович 06.04.1994` или `Иванов Петр Петрович 1994`",
                parse_mode="Markdown", reply_markup=again_kb()
            )
            return ConversationHandler.END
        need_country = True
    else:  # phone
        if not is_phone_query(raw):
            await update.message.reply_text(
                "⚠️ Укажи номер как `79250000000` (можно +7/8).",
                parse_mode="Markdown", reply_markup=again_kb()
            )
            return ConversationHandler.END
        query = normalize_phone(raw)

    await update.message.reply_text("⏳ Ищу данные…")

    try:
        data = call_breachka(query, need_country=need_country)
        logger.info("BREACHKA RAW RESPONSE: %s", json.dumps(data, ensure_ascii=False))
        out = fmt(data)
        if len(out) > 3900:
            out = out[:3900] + "\n\n(ответ обрезан)"
        await update.message.reply_text(out, parse_mode="Markdown", reply_markup=again_kb())
    except requests.HTTPError as e:
        code = getattr(e.response, "status_code", "")
        await update.message.reply_text(f"HTTP ошибка: {code}", reply_markup=again_kb())
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}", reply_markup=again_kb())

    return ConversationHandler.END

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING: [
                CallbackQueryHandler(choose_type, pattern="^(fio|phone)$"),
                CallbackQueryHandler(new_search, pattern="^newsearch$")
            ],
            TYPING: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, text_recv),
                CallbackQueryHandler(new_search, pattern="^newsearch$")
            ],
        },
        fallbacks=[CallbackQueryHandler(new_search, pattern="^newsearch$")],
        name="main_conv",
        persistent=False,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("new", start))  # /new — как альтернатива

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
