import asyncio
import html
import logging
import re
import sqlite3
import uuid
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


# ============================================================
# ВСТАВЬ СЮДА НОВЫЙ ТОКЕН ОТ @BotFather (после /revoke старого)
# ============================================================
BOT_TOKEN = "ВСТАВЬ_СЮДА_НОВЫЙ_ТОКЕН"

# Твой Telegram ID. Можно добавить несколько ID через запятую:
# ADMIN_IDS = {111111111, 222222222}
ADMIN_IDS = {8630566465}

DB_PATH = "offers.sqlite3"

if not BOT_TOKEN or BOT_TOKEN == "ВСТАВЬ_СЮДА_НОВЫЙ_ТОКЕН":
    raise RuntimeError("Вставь новый BOT_TOKEN в начало файла main.py")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
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
    seller_name TEXT NOT NULL,
    nft_url TEXT NOT NULL,
    nft_name TEXT NOT NULL,
    price TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    buyer_id INTEGER,
    buyer_name TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
""")
db.commit()


def is_allowed(user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True
    return db.execute(
        "SELECT 1 FROM allowed_users WHERE user_id = ?",
        (user_id,),
    ).fetchone() is not None


def parse_nft_link(value: str):
    """Accept only Telegram collectible gift links like https://t.me/nft/Slug-123."""
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or parsed.netloc.lower() not in {"t.me", "www.t.me"}:
        return None

    match = re.fullmatch(r"/nft/([A-Za-z0-9_-]+)", parsed.path)
    if not match:
        return None

    slug = match.group(1)
    display_name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", slug)
    display_name = re.sub(r"-(\d+)$", r" #\1", display_name)
    return f"https://t.me/nft/{slug}", display_name


def get_offer(offer_id: str):
    return db.execute(
        "SELECT * FROM offers WHERE offer_id = ?",
        (offer_id,),
    ).fetchone()


def make_offer_text(row):
    status_labels = {
        "open": "🟢 Оффер активен",
        "accepted": "🟡 Оффер принят",
        "transferred": "🔵 Продавец отметил передачу NFT",
        "completed": "✅ Получение подтверждено",
        "declined": "❌ Оффер отклонён",
    }
    seller = html.escape(row["seller_name"])
    nft_name = html.escape(row["nft_name"])
    nft_url = html.escape(row["nft_url"], quote=True)
    price = html.escape(row["price"])
    lines = [
        "🎁 <b>NFT OFFER</b>",
        f'NFT: <a href="{nft_url}">{nft_name}</a>',
        f"Цена: <b>{price}</b>",
        f"Продавец: {seller}",
        "",
        status_labels.get(row["status"], row["status"]),
    ]
    if row["buyer_name"]:
        lines.append(f"Покупатель: {html.escape(row['buyer_name'])}")
    return "\n".join(lines)


def make_keyboard(row) -> InlineKeyboardMarkup | None:
    builder = InlineKeyboardBuilder()
    offer_id = row["offer_id"]

    if row["status"] == "open":
        builder.row(
            InlineKeyboardButton(
                text="✅ Принять оффер",
                callback_data=f"accept:{offer_id}",
            ),
            InlineKeyboardButton(
                text="❌ Отклонить",
                callback_data=f"decline:{offer_id}",
            ),
        )
    elif row["status"] == "accepted":
        builder.row(
            InlineKeyboardButton(
                text="📦 Продавец передал NFT",
                callback_data=f"sent:{offer_id}",
            )
        )
    elif row["status"] == "transferred":
        builder.row(
            InlineKeyboardButton(
                text="✅ Подтвердить получение",
                callback_data=f"received:{offer_id}",
            )
        )

    return builder.as_markup() if builder.buttons else None


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "Я создаю NFT-офферы через Inline Mode.\n\n"
        "Включи Inline Mode у @BotFather командой /setinline.\n"
        "Затем в любом чате введи:\n"
        "<code>@OficialOfferTelegram_bot https://t.me/nft/MousseCake-46483 250 stars</code>\n\n"
        "Цена в Stars здесь только текстовая. Бот не принимает оплату "
        "и не переводит NFT автоматически."
    )


@dp.message(Command("allow"))
async def allow_user(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Нет доступа.")
        return

    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат: /allow TELEGRAM_ID")
        return

    user_id = int(parts[1])
    db.execute(
        "INSERT OR IGNORE INTO allowed_users(user_id) VALUES(?)",
        (user_id,),
    )
    db.commit()
    await message.answer(f"Пользователь {user_id} добавлен в список.")


@dp.message(Command("deny"))
async def deny_user(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Нет доступа.")
        return

    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат: /deny TELEGRAM_ID")
        return

    user_id = int(parts[1])
    db.execute("DELETE FROM allowed_users WHERE user_id = ?", (user_id,))
    db.commit()
    await message.answer(f"Пользователь {user_id} удалён из списка.")


@dp.message(Command("users"))
async def users(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Нет доступа.")
        return

    rows = db.execute(
        "SELECT user_id FROM allowed_users ORDER BY user_id"
    ).fetchall()
    text = "\n".join(str(row["user_id"]) for row in rows) or "Список пуст."
    await message.answer(text)


@dp.inline_query()
async def create_inline_offer(query: InlineQuery):
    if not is_allowed(query.from_user.id):
        await query.answer([], cache_time=1, is_personal=True)
        return

    parts = (query.query or "").strip().split()
    if len(parts) < 2:
        await query.answer([], cache_time=1, is_personal=True)
        return

    parsed_link = parse_nft_link(parts[0])
    if not parsed_link:
        await query.answer([], cache_time=1, is_personal=True)
        return

    if not parts[1].isdigit() or int(parts[1]) <= 0:
        await query.answer([], cache_time=1, is_personal=True)
        return

    nft_url, nft_name = parsed_link
    price = f"{int(parts[1])} Stars"

    seller_name = (
        f"@{query.from_user.username}"
        if query.from_user.username
        else query.from_user.full_name
    )
    offer_id = uuid.uuid4().hex[:12]

    db.execute(
        """
        INSERT INTO offers
        (offer_id, seller_id, seller_name, nft_url, nft_name, price)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            offer_id,
            query.from_user.id,
            seller_name,
            nft_url,
            nft_name,
            price,
        ),
    )
    db.commit()

    row = get_offer(offer_id)
    result = InlineQueryResultArticle(
        id=offer_id,
        title=f"Предложить {nft_name} за {price}",
        description="Нажми, чтобы отправить оффер в этот чат",
        input_message_content=InputTextMessageContent(
            message_text=make_offer_text(row),
            parse_mode="HTML",
        ),
        reply_markup=make_keyboard(row),
    )
    await query.answer([result], cache_time=0, is_personal=True)


@dp.callback_query(F.data.startswith(("accept:", "decline:", "sent:", "received:")))
async def handle_offer_button(callback: CallbackQuery):
    action, offer_id = callback.data.split(":", 1)
    row = get_offer(offer_id)

    if row is None:
        await callback.answer("Оффер не найден.", show_alert=True)
        return

    user_id = callback.from_user.id
    new_status = None

    if action == "accept":
        if row["status"] != "open":
            await callback.answer("Оффер уже обработан.", show_alert=True)
            return
        if user_id == row["seller_id"]:
            await callback.answer("Нельзя принять собственный оффер.", show_alert=True)
            return

        buyer_name = (
            f"@{callback.from_user.username}"
            if callback.from_user.username
            else callback.from_user.full_name
        )
        db.execute(
            """
            UPDATE offers
            SET status = 'accepted', buyer_id = ?, buyer_name = ?
            WHERE offer_id = ? AND status = 'open'
            """,
            (user_id, buyer_name, offer_id),
        )
        new_status = "accepted"

    elif action == "decline":
        if row["status"] != "open":
            await callback.answer("Оффер уже обработан.", show_alert=True)
            return
        if user_id == row["seller_id"]:
            await callback.answer("Нельзя отклонить собственный оффер этой кнопкой.", show_alert=True)
            return

        db.execute(
            "UPDATE offers SET status = 'declined' WHERE offer_id = ? AND status = 'open'",
            (offer_id,),
        )
        new_status = "declined"

    elif action == "sent":
        if user_id != row["seller_id"]:
            await callback.answer("Отметить передачу может только продавец.", show_alert=True)
            return
        if row["status"] != "accepted":
            await callback.answer("Сначала покупатель должен принять оффер.", show_alert=True)
            return

        db.execute(
            "UPDATE offers SET status = 'transferred' WHERE offer_id = ? AND status = 'accepted'",
            (offer_id,),
        )
        new_status = "transferred"

    elif action == "received":
        if user_id != row["buyer_id"]:
            await callback.answer("Подтвердить получение может только покупатель.", show_alert=True)
            return
        if row["status"] != "transferred":
            await callback.answer("Продавец ещё не отметил передачу NFT.", show_alert=True)
            return

        db.execute(
            "UPDATE offers SET status = 'completed' WHERE offer_id = ? AND status = 'transferred'",
            (offer_id,),
        )
        new_status = "completed"

    db.commit()
    updated = get_offer(offer_id)

    try:
        if callback.inline_message_id:
            await bot.edit_message_text(
                inline_message_id=callback.inline_message_id,
                text=make_offer_text(updated),
                parse_mode="HTML",
                reply_markup=make_keyboard(updated),
            )
    except Exception:
        logging.exception("Не удалось обновить сообщение оффера")

    messages = {
        "accepted": "Оффер принят.",
        "declined": "Оффер отклонён.",
        "transferred": "Передача отмечена.",
        "completed": "Получение подтверждено.",
    }
    await callback.answer(messages.get(new_status, "Готово."))


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
