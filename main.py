import asyncio
import logging
import os
import re
import sqlite3
import uuid
from html import escape
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

BOT_TOKEN = os.getenv("    InlineKeyboardButton,
    CallbackQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

BOT_TOKEN = "9002614932:AAFIf4N2GB1yG-HD3P7B4ZZgfpfmyM4KWqU"
ADMIN_IDS = {8630566465}
    int(x.strip())
    for x in os.getenv("8630566465", "8630566465").split(",")
    if x.strip().isdigit()
}
DB_PATH = os.getenv("DB_PATH", "offers.sqlite3")
", "").strip()
ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
DB_PATH = os.getenv("DB_PATH", "offers.sqlite3")

if not BOT_TOKEN:
    raise RuntimeError("Не задана переменная окружения BOT_TOKEN")

logging.basicConfig(level=logging.INFO)
bot = Bot(BOT_TOKEN)
dp = Dispatcher()

db = sqlite3.connect(DB_PATH)
db.row_factory = sqlite3.Row
db.execute("""
CREATE TABLE IF NOT EXISTS allowed_users (
    user_id INTEGER PRIMARY KEY
)
""")
db.execute("""
CREATE TABLE IF NOT EXISTS offers (
    offer_id TEXT PRIMARY KEY,
    seller_id INTEGER NOT NULL,
    seller_name TEXT,
    nft_url TEXT NOT NULL,
    nft_name TEXT NOT NULL,
    price TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    buyer_id INTEGER,
    buyer_name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")
db.commit()


def is_allowed(user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True
    return db.execute(
        "SELECT 1 FROM allowed_users WHERE user_id = ?", (user_id,)
    ).fetchone() is not None


def parse_nft_url(url: str):
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() not in {
        "t.me", "www.t.me"
    }:
        return None
    match = re.fullmatch(r"/nft/([A-Za-z0-9_-]+)", parsed.path)
    if not match:
        return None
    slug = match.group(1)
    # Example: MousseCake-46483 -> display name "Mousse Cake #46483"
    name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", slug)
    name = re.sub(r"-(\d+)$", r" #\1", name)
    return url, name


def offer_text(row, status_text=None):
    status = status_text or {
        "open": "🟢 Оффер активен",
        "accepted": "🟡 Оффер принят",
        "transferred": "🔵 NFT передан продавцом",
        "completed": "✅ Сделка завершена",
        "declined": "❌ Оффер отклонён",
    }.get(row["status"], row["status"])
    seller = row["seller_name"] or str(row["seller_id"])
    lines = [
        "🎁 <b>NFT OFFER</b>",
        f"NFT: <a href=\"{escape(row['nft_url'], quote=True)}\">{escape(row['nft_name'])}</a>",
        f"Цена: <b>{escape(row['price'])} Stars</b>",
        f"Продавец: {escape(seller)}",
        "",
        status,
    ]
    if row["buyer_name"]:
        lines.append(f"Покупатель: {escape(row['buyer_name'])}")
    return "\n".join(lines)


def keyboard_for(row):
    builder = InlineKeyboardBuilder()
    oid = row["offer_id"]
    status = row["status"]
    if status == "open":
        builder.row(
            InlineKeyboardButton(text="✅ Принять оффер", callback_data=f"accept:{oid}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"decline:{oid}"),
        )
    elif status == "accepted":
        builder.row(
            InlineKeyboardButton(text="📦 Я передал NFT", callback_data=f"sent:{oid}")
        )
    elif status == "transferred":
        builder.row(
            InlineKeyboardButton(text="✅ Подтвердить получение", callback_data=f"received:{oid}")
        )
    return builder.as_markup() if builder.buttons else None


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "Бот создаёт NFT-офферы через Inline Mode.\n\n"
        "Включи его в @BotFather командой /setinline.\n"
        "Затем в любом чате введи:\n"
        "<code>@OficialOfferTelegram_bot https://t.me/nft/MousseCake-46483 250 stars</code>\n\n"
        "Stars здесь только текстовая цена. Бот не принимает оплату и не переводит NFT автоматически."
    )


@dp.message(Command("allow"))
async def allow_user(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.answer("Нет доступа.")
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        return await message.answer("Использование: /allow TELEGRAM_ID")
    uid = int(parts[1])
    db.execute("INSERT OR IGNORE INTO allowed_users(user_id) VALUES(?)", (uid,))
    db.commit()
    await message.answer(f"Пользователь {uid} добавлен.")


@dp.message(Command("deny"))
async def deny_user(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.answer("Нет доступа.")
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        return await message.answer("Использование: /deny TELEGRAM_ID")
    uid = int(parts[1])
    db.execute("DELETE FROM allowed_users WHERE user_id = ?", (uid,))
    db.commit()
    await message.answer(f"Пользователь {uid} удалён из списка.")


@dp.message(Command("users"))
async def list_users(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.answer("Нет доступа.")
    rows = db.execute("SELECT user_id FROM allowed_users ORDER BY user_id").fetchall()
    text = "\n".join(str(r["user_id"]) for r in rows) or "Список пуст."
    await message.answer(text)


@dp.inline_query()
async def inline_offer(query: InlineQuery):
    raw = (query.query or "").strip()
    if not is_allowed(query.from_user.id):
        return await query.answer(
            [],
            cache_time=1,
            is_personal=True,
            switch_pm_text="Нет доступа — обратитесь к администратору",
            switch_pm_parameter="start",
        )

    parts = raw.split()
    if len(parts) < 2:
        return await query.answer(
            [],
            cache_time=1,
            is_personal=True,
            switch_pm_text="Формат: ссылка на NFT и цена, например 250 stars",
            switch_pm_parameter="start",
        )

    parsed = parse_nft_url(parts[0])
    if not parsed:
        return await query.answer(
            [],
            cache_time=1,
            is_personal=True,
            switch_pm_text="Нужна ссылка вида https://t.me/nft/Название-123",
            switch_pm_parameter="start",
        )

    if not parts[1].isdigit() or int(parts[1]) <= 0:
        return await query.answer(
            [],
            cache_time=1,
            is_personal=True,
            switch_pm_text="После ссылки укажи цену числом, например 250 stars",
            switch_pm_parameter="start",
        )

    nft_url, nft_name = parsed
    price = " ".join(parts[1:])
    offer_id = uuid.uuid4().hex[:12]
    username = f"@{query.from_user.username}" if query.from_user.username else query.from_user.full_name

    db.execute(
        """INSERT INTO offers
        (offer_id, seller_id, seller_name, nft_url, nft_name, price)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (offer_id, query.from_user.id, username, nft_url, nft_name, price),
    )
    db.commit()
    row = db.execute("SELECT * FROM offers WHERE offer_id = ?", (offer_id,)).fetchone()

    result = InlineQueryResultArticle(
        id=offer_id,
        title=f"Предложить {nft_name} за {price} Stars",
        description="Отправить NFT-оффер в этот чат",
        input_message_content=InputTextMessageContent(
            message_text=offer_text(row),
            parse_mode="HTML",
            disable_web_page_preview=False,
        ),
        reply_markup=keyboard_for(row),
    )
    await query.answer([result], cache_time=0, is_personal=True)


@dp.callback_query(F.data.startswith(("accept:", "decline:", "sent:", "received:")))
async def offer_action(callback: CallbackQuery):
    action, offer_id = callback.data.split(":", 1)
    row = db.execute("SELECT * FROM offers WHERE offer_id = ?", (offer_id,)).fetchone()
    if not row:
        return await callback.answer("Оффер не найден.", show_alert=True)

    uid = callback.from_user.id
    new_status = None
    if action == "accept":
        if row["status"] != "open":
            return await callback.answer("Этот оффер уже обработан.", show_alert=True)
        if uid == row["seller_id"]:
            return await callback.answer("Нельзя принять собственный оффер.", show_alert=True)
        new_status = "accepted"
        buyer_name = f"@{callback.from_user.username}" if callback.from_user.username else callback.from_user.full_name
        db.execute(
            "UPDATE offers SET status=?, buyer_id=?, buyer_name=? WHERE offer_id=? AND status='open'",
            (new_status, uid, buyer_name, offer_id),
        )
    elif action == "decline":
        if row["status"] != "open":
            return await callback.answer("Этот оффер уже обработан.", show_alert=True)
        if uid == row["seller_id"]:
            return await callback.answer("Продавец не может отклонить свой оффер этой кнопкой.", show_alert=True)
        new_status = "declined"
        db.execute("UPDATE offers SET status=? WHERE offer_id=?", (new_status, offer_id))
    elif action == "sent":
        if uid != row["seller_id"]:
            return await callback.answer("Отметить передачу может только продавец.", show_alert=True)
        if row["status"] != "accepted":
            return await callback.answer("Сначала покупатель должен принять оффер.", show_alert=True)
        new_status = "transferred"
        db.execute("UPDATE offers SET status=? WHERE offer_id=?", (new_status, offer_id))
    elif action == "received":
        if uid != row["buyer_id"]:
            return await callback.answer("Подтвердить получение может только покупатель.", show_alert=True)
        if row["status"] != "transferred":
            return await callback.answer("Продавец ещё не отметил передачу NFT.", show_alert=True)
        new_status = "completed"
        db.execute("UPDATE offers SET status=? WHERE offer_id=?", (new_status, offer_id))

    db.commit()
    updated = db.execute("SELECT * FROM offers WHERE offer_id = ?", (offer_id,)).fetchone()

    if callback.inline_message_id:
        await bot.edit_message_text(
            inline_message_id=callback.inline_message_id,
            text=offer_text(updated),
            parse_mode="HTML",
            reply_markup=keyboard_for(updated),
            disable_web_page_preview=False,
        )
    await callback.answer({
        "accepted": "Оффер принят.",
        "declined": "Оффер отклонён.",
        "transferred": "Отметка о передаче сохранена.",
        "completed": "Получение подтверждено.",
    }.get(new_status, "Готово."))


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
