#!/usr/bin/env python3
# requirements: python-telegram-bot==20.* requests
import os, logging, requests, json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, ConversationHandler, filters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CHOOSING, TYPING = range(2)

BREACHKA_API_KEY = os.environ.get("BREACHKA_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not BREACHKA_API_KEY or not TELEGRAM_TOKEN:
    raise RuntimeError("Set BREACHKA_API_KEY and TELEGRAM_TOKEN env vars")

def call_breachka(single_query: str, find_type="Summary", country="RU"):
    url = "https://www.breachka.com/api/v1/find/mass"
    headers = {"X-Api-Key": BREACHKA_API_KEY, "Content-Type": "application/json"}
    payload = {"requests": [single_query], "findType": find_type, "countryType": country}
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()

def fmt(resp: dict) -> str:
    parts = []
    bad = resp.get("NotValidRequests", [])
    if bad: parts.append("❗Не прошли валидацию: " + ", ".join(bad))
    arr = resp.get("Responses", [])
    if not arr: 
        parts.append("Ничего не найдено.")
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
                more = len(vals)-len(shown)
                s = "; ".join(map(str, shown)) + (f" (и ещё {more})" if more>0 else "")
                parts.append(f"*{name}:* {s}")
        add("Телефоны","Phone"); add("Email","Email"); add("Адреса","Address")
        add("Транспорт","Transport"); add("Работа","Work"); add("Паспорт","Passport")
        add("ИНН","Inn"); add("СНИЛС","Snils"); add("Долги","Debts"); add("Родственники","Relatives"); add("URL","Url")
        parts.append("---")
    return "\n".join(parts)

async def start(update: Update, _: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("По ФИО+дате рождения", callback_data="fio")],
          [InlineKeyboardButton("По номеру телефона", callback_data="phone")],
          [InlineKeyboardButton("Отмена", callback_data="cancel")]]
    await update.message.reply_text("Выберите тип поиска:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING

async def choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data == "cancel":
        await q.edit_message_text("Отменено."); return ConversationHandler.END
    context.user_data["type"] = q.data
    if q.data == "fio":
        await q.edit_message_text("Отправьте: `Иванов Иван Иванович 01.01.1990`")
    else:
        await q.edit_message_text("Отправьте номер: `7925...` или `+7925...`")
    return TYPING

async def text_recv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stype = context.user_data.get("type"); text = update.message.text.strip()
    if not stype: 
        await update.message.reply_text("Используйте /start"); return ConversationHandler.END
    query = "".join(ch for ch in text if ch.isdigit() or ch=="+") if stype=="phone" else text
    await update.message.reply_text("Ищу…")
    try:
        data = call_breachka(query)
        out = fmt(data)
        if len(out)>3900: out = out[:3900] + "\n\n(обрезано)"
        await update.message.reply_text(out, parse_mode="Markdown")
    except requests.HTTPError as e:
        await update.message.reply_text(f"HTTP ошибка: {getattr(e.response,'status_code', '')}")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")
    return ConversationHandler.END

async def help_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Команды: /start /find /help")

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start), CommandHandler("find", start)],
        states={CHOOSING:[CallbackQueryHandler(choice)], TYPING:[MessageHandler(filters.TEXT & ~filters.COMMAND, text_recv)]},
        fallbacks=[],
    )
    app.add_handler(conv); app.add_handler(CommandHandler("help", help_cmd))
    app.run_polling()

if __name__ == "__main__":
    main()
