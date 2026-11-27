"""
Бот: приём заявок на обучение + ручное одобрение + доска реальных покупок для сайта.

Как это работает:
  1. Покупатель открывает бота (кнопка на сайте) -> /start basic.
  2. Он платит звёздами (счёт от бота) ИЛИ переводит вам звёзды сам и жмёт «Я оплатил(а)».
  3. Вам (ADMIN_ID) приходит заявка с кнопками «Одобрить» / «Отклонить».
  4. Только после вашего «Одобрить» покупка попадает на доску на сайте.

Установка:  pip install "python-telegram-bot>=21"
Запуск:     BOT_TOKEN=123:ABC ADMIN_ID=ВАШ_ЧИСЛОВОЙ_ID python bot.py
Токен — у @BotFather. Свой числовой ID — у @userinfobot.
Сайт читает http://ВАШ_СЕРВЕР:8080/purchases (поставьте за HTTPS и впишите в PURCHASES_API).
Команды админа: /pending — список заявок, /add @ник — добавить одобренную покупку вручную.
"""
import json, os, threading, itertools, datetime as dt
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, LabeledPrice, InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb
from telegram.ext import (Application, CommandHandler, PreCheckoutQueryHandler, CallbackQueryHandler,
                          MessageHandler, ContextTypes, filters)

TOKEN = os.environ["9174954512:AAEiw1A5IpFyngqNiLzrfz0IKasMFF1bQ9A"]
ADMIN_ID = 8689175149
PORT = int(os.environ.get("PORT", "8080"))
FILE = "purchases.json"
PLANS = {"basic": {"title": "Базовое обучение", "stars": 44}}
LOCK = threading.Lock()

def load():
    try:
        with open(FILE, encoding="utf-8") as f: return json.load(f)
    except Exception: return []

def save(items):
    with open(FILE, "w", encoding="utf-8") as f: json.dump(items[:1000], f, ensure_ascii=False)

def mask(name):
    name = (name or "аноним").lstrip("@")
    return "@" + name[:2] + "***"

def public_today():
    today = dt.date.today().isoformat()
    return [{k: p[k] for k in ("time", "user", "plan", "stars")}
            for p in load() if p["status"] == "approved" and p["date"] == today]

def new_id():
    return str(int(dt.datetime.now().timestamp() * 1000))

async def create_pending(update, plan_id, username, buyer_id, ctx, paid_stars=False):
    plan = PLANS[plan_id]
    now = dt.datetime.now()
    rec = {"id": new_id(), "date": now.date().isoformat(), "time": now.strftime("%H:%M"),
           "user": mask(username), "real_user": username or "—", "buyer_id": buyer_id,
           "plan": plan["title"], "stars": plan["stars"], "status": "pending"}
    with LOCK:
        items = load(); items.insert(0, rec); save(items)
    await ctx.bot.send_message(
        ADMIN_ID,
        f"🆕 Заявка: {plan['title']} — {plan['stars']} ⭐️\n"
        f"Покупатель: @{username or '—'} (id {buyer_id})\n"
        f"{'Оплачено счётом бота' if paid_stars else 'Говорит, что перевёл(а) вам звёзды — проверьте'}",
        reply_markup=Kb([[Btn("✅ Одобрить", callback_data=f"ok:{rec['id']}"),
                          Btn("❌ Отклонить", callback_data=f"no:{rec['id']}")]]))

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    plan_id = ctx.args[0] if ctx.args and ctx.args[0] in PLANS else "basic"
    plan = PLANS[plan_id]
    await update.message.reply_invoice(
        title=plan["title"], description="Оплата в Telegram Stars", payload=plan_id,
        currency="XTR", provider_token="", prices=[LabeledPrice(plan["title"], plan["stars"])])
    await update.message.reply_text(
        "Если переводишь звёзды напрямую, а не счётом выше — после перевода нажми кнопку.",
        reply_markup=Kb([[Btn("Я оплатил(а)", callback_data=f"paid:{plan_id}")]]))

async def precheckout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def paid_invoice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pay = update.message.successful_payment
    plan_id = pay.invoice_payload if pay.invoice_payload in PLANS else "basic"
    await create_pending(update, plan_id, update.effective_user.username, update.effective_user.id, ctx, True)
    await update.message.reply_text("Оплата получена. Заявка ушла на проверку, скоро с тобой свяжутся ⭐️")

async def buttons(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    action, _, arg = q.data.partition(":")
    if action == "paid":
        await q.answer("Заявка отправлена")
        await q.edit_message_text("Заявка отправлена на проверку. Как только оплата подтвердится — свяжемся ⭐️")
        await create_pending(update, arg if arg in PLANS else "basic",
                             q.from_user.username, q.from_user.id, ctx)
        return
    if q.from_user.id != ADMIN_ID:          # одобрять может только владелец
        await q.answer("Нет доступа", show_alert=True); return
    with LOCK:
        items = load()
        rec = next((p for p in items if p["id"] == arg), None)
        if not rec or rec["status"] != "pending":
            await q.answer("Уже обработано"); return
        rec["status"] = "approved" if action == "ok" else "rejected"
        now = dt.datetime.now(); rec["date"], rec["time"] = now.date().isoformat(), now.strftime("%H:%M")
        save(items)
    mark = "✅ Одобрено" if action == "ok" else "❌ Отклонено"
    await q.edit_message_text(f"{q.message.text}\n\n{mark}")
    if rec.get("buyer_id"):
        txt = "Оплата подтверждена, спасибо! Скоро напишу ⭐️" if action == "ok" else "Оплату не удалось подтвердить. Напиши мне в личные сообщения."
        try: await ctx.bot.send_message(rec["buyer_id"], txt)
        except Exception: pass

async def pending(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    for p in [p for p in load() if p["status"] == "pending"][:20]:
        await update.message.reply_text(
            f"{p['plan']} — {p['stars']} ⭐️, @{p['real_user']}",
            reply_markup=Kb([[Btn("✅ Одобрить", callback_data=f"ok:{p['id']}"),
                              Btn("❌ Отклонить", callback_data=f"no:{p['id']}")]]))
    await update.message.reply_text("Это все ожидающие заявки.")

async def add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    name = ctx.args[0] if ctx.args else None
    now = dt.datetime.now(); plan = PLANS["basic"]
    with LOCK:
        items = load()
        items.insert(0, {"id": new_id(), "date": now.date().isoformat(), "time": now.strftime("%H:%M"),
                         "user": mask(name), "real_user": name or "—", "buyer_id": None,
                         "plan": plan["title"], "stars": plan["stars"], "status": "approved"})
        save(items)
    await update.message.reply_text("Покупка добавлена на доску ✅")

class Api(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(public_today(), ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass

if __name__ == "__main__":
    threading.Thread(target=lambda: HTTPServer(("0.0.0.0", PORT), Api).serve_forever(), daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pending", pending))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, paid_invoice))
    app.add_handler(CallbackQueryHandler(buttons))
    app.run_polling()
