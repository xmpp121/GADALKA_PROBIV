# trigger deploy
#!/usr/bin/env python3
import os, logging, requests, json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, ConversationHandler, filters
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CHOOSING, TYPING = range(2)

BREACHKA_API_KEY = os.environ.get("BREACHKA_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not BREACHKA_API_KEY or not TELEGRAM_TOKEN:
    raise RuntimeError("Set BREACHKA_API_KEY and TELEGRAM_TOKEN env vars")

# ---------------- API ----------------
def call_breachka(single_query: str, find_type="Detail", country="RU"):
    url = "https://www.breachka.com/api/v1/find/mass"
    headers = {"X-Api-Key": BREACHKA_API_KEY, "Content-Type": "application/json"}
    payload = {
        "Requests": [single_query],   # с заглавной буквы
        "FindType": find_type,        # Detail = подробный ответ
        "CountryType": country
    }
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()

def fmt(resp: dict) -> str:
    parts = []
    bad = resp.get("NotValidRequests", [])
    if bad:
        parts.append("❗ Запросы не прошли валидацию:\n" + "\n".join(f"- {b}" for b in bad))

    arr = resp.get("Responses", [])
    if not arr:
        parts.append("⚠️ Ничего не найдено по валидным запросам.")
        return "\n".join(parts)

    for e in arr:
        parts.append(f"🔎 *Запрос:* `{e.get('Query','')}`")
        inner = e.get("Responses", [])
        if not inner:
            parts.append("— Нет ответов.")
            continue
        r = inner[0]

        def add(name, key, limit=6):
            vals = r.get(key, [])
            if vals:
                shown = vals[:limit]
                more = len(vals) - len(shown)
                s = "; ".join(map(str, shown)) + (f" (и ещё {more})" if more>0 else "")
                parts.append(f"*{name}:* {s}")

        add("Телефоны","Phone")
        add("Email","Email")
        add("Адреса","Address")
        add("Транспорт","Transport")
        add("Работа","Work")
        add("Паспорт","Passport")
        add("ИНН","Inn")
        add("СНИЛС","Snils")
        add("Долги","Debts")
        add("Родственники","Relatives")
        add("URL","Url")

        parts.append("— — —")
    return "\n".join(parts)

# ---------------- Telegram ----------------
async def start(update: Update, _: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("👤 ФИО + дата/год", callback_data="fio")],
        [InlineKeyboardButton("📱 Телефон", callback_data="phone")]
    ]
    await update.message.reply_text(
        "👋 Привет! Выберите тип поиска:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOOSING

async def choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "fio":
        context.user_data["type"] = "fio"
        await q.edit_message_text(
            "✍ Введите ФИО и дату рождения (например: `Иванов Петр Петрович 06.04.1994`) "
            "или ФИО + год (например: `Иванов Петр Петрович 1994`).",
            parse_mode="Markdown"
        )
    elif q.data == "phone":
        context.user_data["type"] = "phone"
        await q.edit_message_text(
            "✍ Введите номер телефона в формате `79250000000`.",
            parse_mode="Markdown"
        )

    return TYPING

async def text_recv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    await update.message.reply_text("⏳ Ищу данные...")

    try:
        data = call_breachka(text)
        out = fmt(data)
        if len(out) > 3900:
            out = out[:3900] + "\n\n(ответ обрезан)"
        kb = [[InlineKeyboardButton("🔎 Новый поиск", callback_data="newsearch")]]
        await update.message.reply_text(out, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    except requests.HTTPError as e:
        await update.message.reply_text(f"HTTP ошибка: {getattr(e.response,'status_code', '')}")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

    return ConversationHandler.END

async def new_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    kb = [
        [InlineKeyboardButton("👤 ФИО + дата/год", callback_data="fio")],
        [InlineKeyboardButton("📱 Телефон", callback_data="phone")]
    ]
    await q.edit_message_text(
        "🔄 Новый поиск. Выберите тип:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOOSING

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING:[CallbackQueryHandler(choice, pattern="^(fio|phone)$"),
                      CallbackQueryHandler(new_search, pattern="^newsearch$")],
            TYPING:[MessageHandler(filters.TEXT & ~filters.COMMAND, text_recv)]
        },
        fallbacks=[],
    )
    app.add_handler(conv)
    app.run_polling()

if __name__ == "__main__":
    main()
