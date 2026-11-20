"""
Telegram NFT offer bot — offers only, no Stars payments or balance.
Python 3.10+
Install: pip install -U aiogram
Environment:
  BOT_TOKEN="9002614932:AAFIf4N2GB1yG-HD3P7B4ZZgfpfmyM4KWqU"
  ADMIN_IDS="8630566465,8630566465"
Run:
  python3 gift_offer_bot.py

The bot only coordinates an offer. Telegram collectible gifts/NFTs must be
transferred manually by users in Telegram. The bot cannot transfer them itself.
"""

import asyncio
import os
import sqlite3
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)

TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
DB_PATH = os.getenv("DB_PATH", "offers.db")

if not TOKEN:
    raise SystemExit("Не задан BOT_TOKEN.")
if not ADMIN_IDS:
    raise SystemExit("Не задан ADMIN_IDS. Укажи Telegram ID администратора.")

bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with connect() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            allowed INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id INTEGER NOT NULL,
            buyer_id INTEGER NOT NULL,
            gift TEXT NOT NULL,
            gift_url TEXT NOT NULL DEFAULT '',
            stars_text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL
        );
        """)
        columns = {row["name"] for row in con.execute("PRAGMA table_info(offers)")}
        if "gift_url" not in columns:
            con.execute("ALTER TABLE offers ADD COLUMN gift_url TEXT NOT NULL DEFAULT ''")


def esc(value):
    return (str(value).replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def register(user):
    with connect() as con:
        con.execute("""
            INSERT INTO users(user_id, username, first_name, allowed)
            VALUES(?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
              username=excluded.username,
              first_name=excluded.first_name
        """, (user.id, (user.username or "").lower(), user.first_name or "",
              1 if user.id in ADMIN_IDS else 0))


def allowed(uid):
    if uid in ADMIN_IDS:
        return True
    with connect() as con:
        row = con.execute("SELECT allowed FROM users WHERE user_id=?", (uid,)).fetchone()
        return bool(row and row["allowed"])


def find_buyer(username):
    username = username.lstrip("@").lower()
    with connect() as con:
        return con.execute(
            "SELECT * FROM users WHERE username=? AND allowed=1",
            (username,)
        ).fetchone()


def get_offer(offer_id):
    with connect() as con:
        return con.execute("SELECT * FROM offers WHERE id=?", (offer_id,)).fetchone()


def update_status(offer_id, status):
    with connect() as con:
        cur = con.execute("UPDATE offers SET status=? WHERE id=?", (status, offer_id))
        return cur.rowcount == 1


def status_text(status):
    return {
        "pending": "Ожидает ответа покупателя",
        "accepted": "Принято — ожидается передача NFT",
        "seller_sent": "Продавец отметил передачу — ждём подтверждения",
        "completed": "Завершено",
        "rejected": "Отклонено",
    }.get(status, status)


def offer_card(o):
    return (
        f"🎁 <b>Оффер #{o['id']}</b>\n\n"
        f"Подарок: <b>{esc(o['gift'])}</b>\n"
        f"Цена: ⭐ <b>{esc(o['stars_text'])}</b>\n"
        f"Статус: <b>{status_text(o['status'])}</b>\n\n"
        f"<i>Stars указаны только в описании оффера. Бот не принимает "
        f"и не переводит оплату.</i>"
    )


def buyer_offer_kb(offer_id):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data=f"accept:{offer_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{offer_id}")
    ]])


def seller_transfer_kb(offer_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Как передать NFT", callback_data=f"how:{offer_id}")],
        [InlineKeyboardButton(text="✅ Я передал NFT", callback_data=f"sent:{offer_id}")]
    ])


def buyer_confirm_kb(offer_id):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Я получил подарок", callback_data=f"got:{offer_id}")
    ]])


async def require_access(message: Message):
    if allowed(message.from_user.id):
        return True
    await message.answer(
        "⛔ <b>Ваш доступ закрыт.</b>\n"
        "Запрос отправлен администратору. Дождитесь открытия доступа."
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"🔔 Запрос доступа\n"
                f"Пользователь: {esc(message.from_user.full_name)}\n"
                f"ID: <code>{message.from_user.id}</code>\n"
                f"Username: @{esc(message.from_user.username or 'нет')}\n"
                f"Открыть: <code>/allow {message.from_user.id}</code>"
            )
        except Exception:
            pass
    return False


@dp.message(CommandStart())
async def start(message: Message):
    register(message.from_user)
    if not allowed(message.from_user.id):
        await message.answer(
            "⛔ <b>Доступ к боту закрыт.</b>\n"
            "Запрос на доступ отправлен администратору."
        )
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"Новый пользователь: {esc(message.from_user.full_name)}\n"
                    f"ID: <code>{message.from_user.id}</code>\n"
                    f"Разрешить: <code>/allow {message.from_user.id}</code>"
                )
            except Exception:
                pass
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать оффер", callback_data="create_help")],
        [InlineKeyboardButton(text="📋 Мои офферы", callback_data="myoffers")]
    ])
    await message.answer(
        f"👋 Привет, <b>{esc(message.from_user.first_name)}</b>!\n\n"
        "Бот помогает отправлять предложения по NFT-подаркам.\n"
        "Stars отображаются только как текстовая цена — бот не принимает "
        "и не начисляет их.\n\n"
        "Создать оффер:\n"
        "<code>/offer @username 250 https://t.me/nft/MousseCake-46483</code>",
        reply_markup=kb
    )


@dp.message(Command("help"))
async def help_cmd(message: Message):
    if not await require_access(message):
        return
    await message.answer(
        "<b>Команды</b>\n"
        "/offer @username 250 https://t.me/nft/MousseCake-46483 — создать оффер\n"
        "/my — мои офферы\n\n"
        "<b>Администратор</b>\n"
        "/allow USER_ID — открыть доступ\n"
        "/deny USER_ID — закрыть доступ\n"
        "/users — список пользователей"
    )


@dp.message(Command("allow"))
async def allow_cmd(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    p = (message.text or "").split()
    if len(p) != 2 or not p[1].isdigit():
        await message.answer("Формат: /allow USER_ID")
        return
    uid = int(p[1])
    with connect() as con:
        row = con.execute("SELECT 1 FROM users WHERE user_id=?", (uid,)).fetchone()
        if row:
            con.execute("UPDATE users SET allowed=1 WHERE user_id=?", (uid,))
    if not row:
        await message.answer("Пользователь ещё не запускал бота.")
        return
    await message.answer(f"✅ Доступ открыт: <code>{uid}</code>.")
    try:
        await bot.send_message(uid, "✅ Администратор открыл вам доступ. Теперь можно создавать и принимать офферы.")
    except Exception:
        pass


@dp.message(Command("deny"))
async def deny_cmd(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    p = (message.text or "").split()
    if len(p) != 2 or not p[1].isdigit():
        await message.answer("Формат: /deny USER_ID")
        return
    uid = int(p[1])
    with connect() as con:
        con.execute("UPDATE users SET allowed=0 WHERE user_id=?", (uid,))
    await message.answer(f"⛔ Доступ закрыт: <code>{uid}</code>.")


@dp.message(Command("users"))
async def users_cmd(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    with connect() as con:
        rows = con.execute("SELECT * FROM users ORDER BY user_id DESC LIMIT 50").fetchall()
    if not rows:
        await message.answer("Пользователей пока нет.")
        return
    lines = ["<b>Пользователи</b>"]
    for u in rows:
        lines.append(
            f"• {esc(u['first_name'])} (@{esc(u['username'] or 'нет')}) "
            f"<code>{u['user_id']}</code> — {'доступ открыт' if u['allowed'] else 'закрыт'}"
        )
    await message.answer("\n".join(lines))


@dp.message(Command("offer"))
async def create_offer(message: Message):
    if not await require_access(message):
        return
    p = (message.text or "").split(maxsplit=3)
    if len(p) != 4 or not p[2].isdigit() or int(p[2]) < 0:
        await message.answer(
            "Формат:\\n"
            "<code>/offer @username 250 https://t.me/nft/MousseCake-46483</code>\\n"
            "Укажи ссылку на конкретный NFT — в оффере будет показан именно он.\\n"
            "Покупатель должен сначала запустить бота и получить доступ."
        )
        return

    buyer = find_buyer(p[1])
    if not buyer:
        await message.answer(
            "Покупатель не найден. Пусть он запустит бота командой /start "
            "и получит доступ от администратора."
        )
        return
    if buyer["user_id"] == message.from_user.id:
        await message.answer("Нельзя отправить оффер самому себе.")
        return

    price_text = f"{int(p[2])} ⭐"
    gift_url = p[3].strip().rstrip(".,)")
    if not (gift_url.startswith("https://t.me/nft/") and len(gift_url) <= 300):
        await message.answer(
            "Нужна ссылка именно такого вида:\\n"
            "<code>https://t.me/nft/MousseCake-46483</code>"
        )
        return

    slug = gift_url.rsplit("/", 1)[-1]
    gift = slug.replace("-", " #", 1)
    if not gift or len(gift) > 120:
        await message.answer("Ссылка на NFT некорректна.")
        return

    with connect() as con:
        cur = con.execute(
            "INSERT INTO offers(seller_id,buyer_id,gift,gift_url,stars_text,status,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (message.from_user.id, buyer["user_id"], gift, gift_url, price_text,
             "pending", datetime.now(timezone.utc).isoformat())
        )
        offer_id = cur.lastrowid

    await bot.send_message(
        buyer["user_id"],
        "💌 <b>Вам поступил оффер</b>\\n\\n"
        f"От: @{esc(message.from_user.username or str(message.from_user.id))}\\n\\n"
        f"🎁 NFT: <b>{esc(gift)}</b>\\n"
        f"🔗 <a href=\"{esc(gift_url)}\">Открыть именно этот NFT в Telegram</a>\\n"
        f"⭐ Цена: <b>{esc(price_text)}</b>\\n"
        f"Номер: <code>#{offer_id}</code>\\n\\n"
        "Примите или отклоните предложение.",
        reply_markup=buyer_offer_kb(offer_id)
    )
    await message.answer(
        f"✅ Оффер <code>#{offer_id}</code> отправлен.\\n"
        f"Добавлена только указанная ссылка: "
        f"<a href=\"{esc(gift_url)}\">{esc(gift)}</a>"
    )


@dp.message(Command("my"))
async def my_cmd(message: Message):
    if not await require_access(message):
        return
    await show_my(message.from_user.id, message)


async def show_my(uid, target):
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM offers WHERE seller_id=? OR buyer_id=? ORDER BY id DESC LIMIT 25",
            (uid, uid)
        ).fetchall()
    if not rows:
        txt = "Офферов пока нет."
    else:
        txt = "\n\n".join(
            f"#{o['id']} — <b>{esc(o['gift'])}</b>\n"
            f"{esc(o['stars_text'])} · {status_text(o['status'])}"
            for o in rows
        )
    if isinstance(target, Message):
        await target.answer(txt)
    else:
        await target.message.answer(txt)


@dp.callback_query(F.data == "create_help")
async def create_help(call: CallbackQuery):
    await call.message.answer(
        "Отправь команду:\n"
        "<code>/offer @username 250 https://t.me/nft/MousseCake-46483</code>\n\n"
        "Число Stars — только текстовая цена оффера."
    )
    await call.answer()


@dp.callback_query(F.data == "myoffers")
async def myoffers_cb(call: CallbackQuery):
    if not allowed(call.from_user.id):
        await call.answer("Доступ закрыт", show_alert=True)
        return
    await show_my(call.from_user.id, call)
    await call.answer()


@dp.callback_query(F.data.startswith("accept:"))
async def accept(call: CallbackQuery):
    oid = int(call.data.split(":")[1])
    o = get_offer(oid)
    if not o or o["buyer_id"] != call.from_user.id or o["status"] != "pending":
        await call.answer("Оффер уже недоступен.", show_alert=True)
        return
    update_status(oid, "accepted")
    await call.message.edit_text(
        offer_card(get_offer(oid)) +
        "\n\n✅ Вы приняли оффер. Продавец должен самостоятельно передать "
        "подарок через Telegram."
    )
    await bot.send_message(
        o["seller_id"],
        f"✅ Покупатель принял оффер <code>#{oid}</code>.\n\n"
        f"Подарок: <b>{esc(o['gift'])}</b>\n"
        f"Передайте его покупателю вручную через Telegram.",
        reply_markup=seller_transfer_kb(oid)
    )
    await call.answer("Оффер принят.")


@dp.callback_query(F.data.startswith("reject:"))
async def reject(call: CallbackQuery):
    oid = int(call.data.split(":")[1])
    o = get_offer(oid)
    if not o or o["buyer_id"] != call.from_user.id or o["status"] != "pending":
        await call.answer("Оффер уже недоступен.", show_alert=True)
        return
    update_status(oid, "rejected")
    await call.message.edit_text("❌ Вы отклонили оффер.")
    await bot.send_message(o["seller_id"], f"❌ Покупатель отклонил оффер <code>#{oid}</code>.")
    await call.answer("Отклонено.")


@dp.callback_query(F.data.startswith("how:"))
async def how_transfer(call: CallbackQuery):
    oid = int(call.data.split(":")[1])
    o = get_offer(oid)
    if not o or o["seller_id"] != call.from_user.id or o["status"] != "accepted":
        await call.answer("Эта кнопка уже недоступна.", show_alert=True)
        return
    buyer = await bot.get_chat(o["buyer_id"])
    buyer_name = f"@{buyer.username}" if buyer.username else f"пользователю с ID {o['buyer_id']}"
    await call.message.answer(
        f"📦 <b>Передача подарка по офферу #{oid}</b>\n\n"
        f"1. Откройте свой профиль Telegram и раздел «Подарки».\n"
        f"2. Выберите нужный коллекционный подарок.\n"
        f"3. Используйте функцию передачи подарка.\n"
        f"4. Передайте подарок {esc(buyer_name)}.\n\n"
        "Бот не выполняет передачу за вас. После реальной передачи нажмите "
        "«Я передал NFT»."
    )
    await call.answer()


@dp.callback_query(F.data.startswith("sent:"))
async def seller_sent(call: CallbackQuery):
    oid = int(call.data.split(":")[1])
    o = get_offer(oid)
    if not o or o["seller_id"] != call.from_user.id or o["status"] != "accepted":
        await call.answer("Оффер уже недоступен.", show_alert=True)
        return
    update_status(oid, "seller_sent")
    await call.message.edit_reply_markup(reply_markup=None)
    await bot.send_message(
        o["buyer_id"],
        f"📦 Продавец отметил, что передал NFT по офферу <code>#{oid}</code>.\n\n"
        "Проверьте свой Telegram. Если подарок действительно поступил, "
        "подтвердите получение.",
        reply_markup=buyer_confirm_kb(oid)
    )
    await call.answer("Покупателю отправлено уведомление.")


@dp.callback_query(F.data.startswith("got:"))
async def buyer_got(call: CallbackQuery):
    oid = int(call.data.split(":")[1])
    o = get_offer(oid)
    if not o or o["buyer_id"] != call.from_user.id or o["status"] != "seller_sent":
        await call.answer("Оффер уже недоступен.", show_alert=True)
        return
    update_status(oid, "completed")
    await call.message.edit_text(
        f"✅ <b>Оффер #{oid} завершён.</b>\n"
        "Вы подтвердили получение подарка.\n\n"
        "Stars были указаны только как текстовая цена. Бот не переводит "
        "и не начисляет их."
    )
    await bot.send_message(
        o["seller_id"],
        f"✅ Покупатель подтвердил получение подарка по офферу <code>#{oid}</code>.\n"
        "Оффер завершён."
    )
    await call.answer("Оффер завершён.")


async def main():
    init_db()
    print("NFT offer bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
