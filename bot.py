import asyncio
import hashlib
import logging
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yt_dlp
from cryptography.fernet import Fernet, InvalidToken

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "297496514"))

DB_PATH = os.getenv("DB_PATH", "/data/bot.db")

MAX_USER_FILE_MB = int(os.getenv("MAX_FILE_MB", "50"))
MAX_USER_DURATION = int(os.getenv("MAX_DURATION_SECONDS", "21600"))
DOWNLOAD_TIMEOUT = int(os.getenv("DOWNLOAD_TIMEOUT", "600"))

DATA_ENCRYPTION_KEY = os.getenv(
    "DATA_ENCRYPTION_KEY", ""
).strip()


if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not configured")

if not DATA_ENCRYPTION_KEY:
    raise RuntimeError("DATA_ENCRYPTION_KEY is not configured")

try:
    FERNET = Fernet(DATA_ENCRYPTION_KEY.encode())
except Exception as exc:
    raise RuntimeError("DATA_ENCRYPTION_KEY is invalid") from exc


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("video_downloader")


# ============================================================
# DATABASE
# ============================================================

Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)


def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def user_hash(telegram_id: int):
    return hashlib.sha256(
        f"telegram:{telegram_id}".encode()
    ).hexdigest()


def encrypt(value: Optional[str]):
    if value is None:
        return None

    return FERNET.encrypt(
        value.encode("utf-8")
    ).decode("utf-8")


def decrypt(value: Optional[str]):
    if not value:
        return None

    try:
        return FERNET.decrypt(
            value.encode("utf-8")
        ).decode("utf-8")
    except InvalidToken:
        return None


def init_db():
    with db_connect() as conn:

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_hash TEXT UNIQUE NOT NULL,
                telegram_id_encrypted TEXT NOT NULL,
                username_encrypted TEXT,
                first_name_encrypted TEXT,
                language TEXT NOT NULL DEFAULT 'ru',
                setup_complete INTEGER NOT NULL DEFAULT 0,
                terms_accepted INTEGER NOT NULL DEFAULT 0,
                is_blocked INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                last_activity TEXT NOT NULL,
                downloads_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                total_bytes INTEGER NOT NULL DEFAULT 0
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_hash TEXT NOT NULL,
                title_encrypted TEXT,
                url_encrypted TEXT,
                quality TEXT,
                size_bytes INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

        conn.commit()


def get_user(telegram_id: int):
    with db_connect() as conn:
        return conn.execute(
            """
            SELECT *
            FROM users
            WHERE user_hash = ?
            """,
            (user_hash(telegram_id),),
        ).fetchone()


def create_or_update_user(
    telegram_id: int,
    username: Optional[str],
    first_name: Optional[str],
):
    h = user_hash(telegram_id)
    current = now_iso()

    with db_connect() as conn:

        row = conn.execute(
            "SELECT id FROM users WHERE user_hash = ?",
            (h,),
        ).fetchone()

        if row:

            conn.execute(
                """
                UPDATE users
                SET
                    username_encrypted = ?,
                    first_name_encrypted = ?,
                    last_activity = ?
                WHERE user_hash = ?
                """,
                (
                    encrypt(username),
                    encrypt(first_name),
                    current,
                    h,
                ),
            )

        else:

            conn.execute(
                """
                INSERT INTO users (
                    user_hash,
                    telegram_id_encrypted,
                    username_encrypted,
                    first_name_encrypted,
                    language,
                    setup_complete,
                    terms_accepted,
                    is_blocked,
                    created_at,
                    last_activity
                )
                VALUES (?, ?, ?, ?, 'ru', 0, 0, 0, ?, ?)
                """,
                (
                    h,
                    encrypt(str(telegram_id)),
                    encrypt(username),
                    encrypt(first_name),
                    current,
                    current,
                ),
            )

        conn.commit()


def set_language(telegram_id: int, language: str):
    with db_connect() as conn:
        conn.execute(
            """
            UPDATE users
            SET language = ?
            WHERE user_hash = ?
            """,
            (language, user_hash(telegram_id)),
        )
        conn.commit()


def complete_setup(telegram_id: int):
    with db_connect() as conn:
        conn.execute(
            """
            UPDATE users
            SET
                setup_complete = 1,
                terms_accepted = 1
            WHERE user_hash = ?
            """,
            (user_hash(telegram_id),),
        )
        conn.commit()


def delete_user_data(telegram_id: int):
    h = user_hash(telegram_id)

    with db_connect() as conn:
        conn.execute(
            "DELETE FROM downloads WHERE user_hash = ?",
            (h,),
        )

        conn.execute(
            "DELETE FROM users WHERE user_hash = ?",
            (h,),
        )

        conn.commit()


def set_blocked(target_id: int, blocked: bool):
    with db_connect() as conn:
        conn.execute(
            """
            UPDATE users
            SET is_blocked = ?
            WHERE user_hash = ?
            """,
            (
                1 if blocked else 0,
                user_hash(target_id),
            ),
        )
        conn.commit()


def add_download(
    telegram_id: int,
    title: str,
    url: str,
    quality: str,
    size_bytes: int,
    status: str,
):
    with db_connect() as conn:

        conn.execute(
            """
            INSERT INTO downloads (
                user_hash,
                title_encrypted,
                url_encrypted,
                quality,
                size_bytes,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_hash(telegram_id),
                encrypt(title),
                encrypt(url),
                quality,
                size_bytes,
                status,
                now_iso(),
            ),
        )

        if status == "success":

            conn.execute(
                """
                UPDATE users
                SET
                    downloads_count = downloads_count + 1,
                    total_bytes = total_bytes + ?
                WHERE user_hash = ?
                """,
                (
                    size_bytes,
                    user_hash(telegram_id),
                ),
            )

        else:

            conn.execute(
                """
                UPDATE users
                SET failed_count = failed_count + 1
                WHERE user_hash = ?
                """,
                (user_hash(telegram_id),),
            )

        conn.commit()


# ============================================================
# TRANSLATIONS
# ============================================================

TEXT = {
    "ru": {
        "welcome":
            "👋 <b>Добро пожаловать в Video Downloader</b>\n\n"
            "Выберите язык интерфейса:",

        "accepted":
            "✅ Всё готово!\n\n"
            "Отправьте мне публичную ссылку на видео.",

        "terms":
            "📄 <b>Условия использования</b>\n\n"
            "Используя бот, вы подтверждаете, что имеете "
            "необходимые права или разрешение на загрузку "
            "и использование отправленного контента.\n\n"
            "Бот не предназначен для нарушения авторских прав "
            "или иных прав третьих лиц.\n\n"
            "Временный видеофайл удаляется с сервера после "
            "завершения обработки.",

        "privacy":
            "🔐 <b>Конфиденциальность</b>\n\n"
            "Бот хранит минимальные данные, необходимые для работы: "
            "Telegram ID, имя/username, выбранный язык и техническую "
            "историю скачиваний.\n\n"
            "Сами видео не хранятся постоянно на сервере. "
            "После обработки временный файл удаляется.",

        "send_url":
            "🔗 Отправьте публичную ссылку на видео.",

        "quality":
            "🎬 <b>Выберите качество:</b>\n\n"
            "Чем выше качество, тем больше размер файла.",

        "downloading":
            "⏳ Скачиваю и обрабатываю видео…",

        "sending":
            "📤 Отправляю видео в Telegram…",

        "done":
            "✅ Готово.\n\n"
            "Временная копия на сервере удалена.",

        "failed":
            "❌ Не удалось обработать видео.\n\n"
            "Попробуйте другую ссылку или другое качество.",

        "too_large":
            "⚠️ Файл получился слишком большим для текущего "
            "способа отправки через Telegram.\n\n"
            "Попробуйте выбрать более низкое качество.",

        "busy":
            "⏳ У вас уже выполняется одна загрузка.",

        "history":
            "📁 <b>История скачиваний</b>",

        "history_empty":
            "📁 История пока пуста.",

        "settings":
            "⚙️ <b>Настройки</b>",

        "blocked":
            "⛔ Доступ к сервису ограничен.",

        "deleted":
            "✅ Ваши данные удалены.",

        "not_url":
            "Отправьте корректную ссылку, начинающуюся с http:// "
            "или https://.",
    },

    "en": {
        "welcome":
            "👋 <b>Welcome to Video Downloader</b>\n\n"
            "Choose your interface language:",

        "accepted":
            "✅ Ready!\n\n"
            "Send me a public video URL.",

        "terms":
            "📄 <b>Terms of Use</b>\n\n"
            "By using this bot, you confirm that you have the "
            "necessary rights or permission to download and use "
            "the submitted content.\n\n"
            "The bot is not intended for copyright infringement "
            "or violation of third-party rights.\n\n"
            "Temporary video files are deleted from the server "
            "after processing.",

        "privacy":
            "🔐 <b>Privacy</b>\n\n"
            "The bot stores only minimal information required "
            "to operate: Telegram ID, name/username, selected "
            "language and technical download history.\n\n"
            "Videos are not permanently stored on the server. "
            "Temporary files are deleted after processing.",

        "send_url":
            "🔗 Send a public video URL.",

        "quality":
            "🎬 <b>Choose quality:</b>\n\n"
            "Higher quality usually means a larger file.",

        "downloading":
            "⏳ Downloading and processing video…",

        "sending":
            "📤 Sending video to Telegram…",

        "done":
            "✅ Done.\n\n"
            "The temporary server copy has been deleted.",

        "failed":
            "❌ Unable to process this video.\n\n"
            "Try another URL or another quality.",

        "too_large":
            "⚠️ The resulting file is too large for the current "
            "Telegram delivery method.\n\n"
            "Try a lower quality.",

        "busy":
            "⏳ You already have one active download.",

        "history":
            "📁 <b>Download history</b>",

        "history_empty":
            "📁 Download history is empty.",

        "settings":
            "⚙️ <b>Settings</b>",

        "blocked":
            "⛔ Access to the service is restricted.",

        "deleted":
            "✅ Your data has been deleted.",

        "not_url":
            "Send a valid URL beginning with http:// or https://.",
    },
}


def lang_of(telegram_id: int):
    row = get_user(telegram_id)
    return row["language"] if row else "ru"


def txt(telegram_id: int, key: str):
    return TEXT[lang_of(telegram_id)].get(
        key,
        TEXT["ru"][key],
    )


# ============================================================
# KEYBOARDS
# ============================================================

def language_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🇷🇺 Русский",
                    callback_data="lang:ru",
                ),
                InlineKeyboardButton(
                    text="🇬🇧 English",
                    callback_data="lang:en",
                ),
            ]
        ]
    )


def consent_keyboard(language: str):
    accept = (
        "✅ Принять и продолжить"
        if language == "ru"
        else "✅ Accept & continue"
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=accept,
                    callback_data="consent:accept",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📄 Terms",
                    callback_data="info:terms",
                ),
                InlineKeyboardButton(
                    text="🔐 Privacy",
                    callback_data="info:privacy",
                ),
            ],
        ]
    )


def main_keyboard(language: str):
    history = (
        "📁 Загруженные видео"
        if language == "ru"
        else "📁 Downloaded videos"
    )

    settings = (
        "⚙️ Настройки"
        if language == "ru"
        else "⚙️ Settings"
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Скачать видео"
                    if language == "ru"
                    else "➕ Download video",
                    callback_data="menu:download",
                )
            ],
            [
                InlineKeyboardButton(
                    text=history,
                    callback_data="menu:history",
                ),
                InlineKeyboardButton(
                    text=settings,
                    callback_data="menu:settings",
                ),
            ],
        ]
    )


def quality_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="360p",
                    callback_data="quality:360",
                ),
                InlineKeyboardButton(
                    text="480p",
                    callback_data="quality:480",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="720p",
                    callback_data="quality:720",
                ),
                InlineKeyboardButton(
                    text="1080p",
                    callback_data="quality:1080",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⭐ Best",
                    callback_data="quality:best",
                ),
            ],
        ]
    )


def settings_keyboard(language: str):
    delete_text = (
        "🗑 Удалить мои данные"
        if language == "ru"
        else "🗑 Delete my data"
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🇷🇺 Русский",
                    callback_data="settings_lang:ru",
                ),
                InlineKeyboardButton(
                    text="🇬🇧 English",
                    callback_data="settings_lang:en",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📄 Terms",
                    callback_data="info:terms",
                ),
                InlineKeyboardButton(
                    text="🔐 Privacy",
                    callback_data="info:privacy",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=delete_text,
                    callback_data="account:delete",
                )
            ],
        ]
    )


# ============================================================
# STATE
# ============================================================

pending_urls = {}
active_downloads = set()

URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)


def is_admin(telegram_id: int):
    return telegram_id == ADMIN_ID


def is_blocked(telegram_id: int):
    row = get_user(telegram_id)
    return bool(row and row["is_blocked"])


def is_ready(telegram_id: int):
    row = get_user(telegram_id)

    return bool(
        row
        and row["setup_complete"]
        and row["terms_accepted"]
    )


def format_bytes(size: int):
    if size < 1024 ** 2:
        return f"{size / 1024:.1f} KB"

    if size < 1024 ** 3:
        return f"{size / 1024**2:.1f} MB"

    return f"{size / 1024**3:.2f} GB"


# ============================================================
# BOT
# ============================================================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    ),
)

dp = Dispatcher()


# ============================================================
# START
# ============================================================

@dp.message(Command("start"))
async def start(message: Message):
    user = message.from_user

    create_or_update_user(
        user.id,
        user.username,
        user.first_name,
    )

    if is_blocked(user.id):
        await message.answer(txt(user.id, "blocked"))
        return

    row = get_user(user.id)

    if not row["setup_complete"]:
        await message.answer(
            TEXT["ru"]["welcome"],
            reply_markup=language_keyboard(),
        )
        return

    await message.answer(
        txt(user.id, "send_url"),
        reply_markup=main_keyboard(lang_of(user.id)),
    )


# ============================================================
# LANGUAGE / CONSENT
# ============================================================

@dp.callback_query(F.data.startswith("lang:"))
async def choose_language(callback: CallbackQuery):
    language = callback.data.split(":")[1]

    set_language(callback.from_user.id, language)

    await callback.message.edit_text(
        TEXT[language]["terms"],
        reply_markup=consent_keyboard(language),
    )

    await callback.answer()


@dp.callback_query(F.data == "consent:accept")
async def accept_terms(callback: CallbackQuery):
    complete_setup(callback.from_user.id)

    language = lang_of(callback.from_user.id)

    await callback.message.edit_text(
        TEXT[language]["accepted"],
        reply_markup=main_keyboard(language),
    )

    await callback.answer()


# ============================================================
# INFO
# ============================================================

@dp.callback_query(F.data == "info:terms")
async def terms_callback(callback: CallbackQuery):
    await callback.message.answer(
        txt(callback.from_user.id, "terms")
    )
    await callback.answer()


@dp.callback_query(F.data == "info:privacy")
async def privacy_callback(callback: CallbackQuery):
    await callback.message.answer(
        txt(callback.from_user.id, "privacy")
    )
    await callback.answer()


@dp.message(Command("terms"))
async def terms_command(message: Message):
    await message.answer(txt(message.from_user.id, "terms"))


@dp.message(Command("privacy"))
async def privacy_command(message: Message):
    await message.answer(txt(message.from_user.id, "privacy"))


# ============================================================
# MENU
# ============================================================

@dp.callback_query(F.data == "menu:download")
async def download_menu(callback: CallbackQuery):
    await callback.message.answer(
        txt(callback.from_user.id, "send_url")
    )
    await callback.answer()


@dp.callback_query(F.data == "menu:settings")
async def settings_menu(callback: CallbackQuery):
    language = lang_of(callback.from_user.id)

    await callback.message.answer(
        TEXT[language]["settings"],
        reply_markup=settings_keyboard(language),
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("settings_lang:"))
async def settings_language(callback: CallbackQuery):
    language = callback.data.split(":")[1]

    set_language(callback.from_user.id, language)

    await callback.message.edit_text(
        TEXT[language]["settings"],
        reply_markup=settings_keyboard(language),
    )

    await callback.answer(
        "Язык изменён"
        if language == "ru"
        else "Language changed"
    )


# ============================================================
# DELETE ACCOUNT DATA
# ============================================================

@dp.callback_query(F.data == "account:delete")
async def delete_account(callback: CallbackQuery):
    telegram_id = callback.from_user.id
    language = lang_of(telegram_id)

    delete_user_data(telegram_id)

    pending_urls.pop(telegram_id, None)
    active_downloads.discard(telegram_id)

    await callback.message.edit_text(
        TEXT[language]["deleted"]
    )

    await callback.answer()


# ============================================================
# HISTORY
# ============================================================

@dp.callback_query(F.data == "menu:history")
async def history(callback: CallbackQuery):
    telegram_id = callback.from_user.id
    language = lang_of(telegram_id)

    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM downloads
            WHERE user_hash = ?
              AND status = 'success'
            ORDER BY id DESC
            LIMIT 15
            """,
            (user_hash(telegram_id),),
        ).fetchall()

    if not rows:
        await callback.message.answer(
            TEXT[language]["history_empty"]
        )
        await callback.answer()
        return

    buttons = []
    lines = [TEXT[language]["history"], ""]

    for row in rows:
        title = decrypt(row["title_encrypted"]) or "Video"

        lines.append(
            f"🎬 <b>{title[:70]}</b>\n"
            f"{row['quality']} • "
            f"{format_bytes(row['size_bytes'])}\n"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"🗑 {title[:28]}",
                    callback_data=f"history_delete:{row['id']}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="🗑 Удалить всю историю"
                if language == "ru"
                else "🗑 Delete all history",
                callback_data="history:clear",
            )
        ]
    )

    await callback.message.answer(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("history_delete:"))
async def delete_history_item(callback: CallbackQuery):
    download_id = int(callback.data.split(":")[1])
    telegram_id = callback.from_user.id

    with db_connect() as conn:
        conn.execute(
            """
            DELETE FROM downloads
            WHERE id = ?
              AND user_hash = ?
            """,
            (
                download_id,
                user_hash(telegram_id),
            ),
        )
        conn.commit()

    await callback.answer(
        "Удалено"
        if lang_of(telegram_id) == "ru"
        else "Deleted"
    )

    await callback.message.delete()


@dp.callback_query(F.data == "history:clear")
async def clear_history(callback: CallbackQuery):
    telegram_id = callback.from_user.id

    with db_connect() as conn:
        conn.execute(
            """
            DELETE FROM downloads
            WHERE user_hash = ?
            """,
            (user_hash(telegram_id),),
        )
        conn.commit()

    await callback.message.edit_text(
        txt(telegram_id, "history_empty")
    )

    await callback.answer()


# ============================================================
# RECEIVE URL
# ============================================================

@dp.message()
async def receive_url(message: Message):
    user = message.from_user

    create_or_update_user(
        user.id,
        user.username,
        user.first_name,
    )

    if is_blocked(user.id):
        await message.answer(txt(user.id, "blocked"))
        return

    if not is_ready(user.id):
        await message.answer(
            TEXT["ru"]["welcome"],
            reply_markup=language_keyboard(),
        )
        return

    value = (message.text or "").strip()

    if not URL_RE.match(value):
        await message.answer(txt(user.id, "not_url"))
        return

    pending_urls[user.id] = value

    await message.answer(
        txt(user.id, "quality"),
        reply_markup=quality_keyboard(),
    )


# ============================================================
# YT-DLP
# ============================================================

def format_selector(quality: str):
    if quality == "best":
        return "bv*+ba/b"

    height = int(quality)

    return (
        f"bv*[height<={height}][ext=mp4]"
        f"+ba[ext=m4a]/"
        f"bv*[height<={height}]"
        f"+ba/"
        f"b[height<={height}]"
    )


def yt_download(
    url: str,
    quality: str,
    temp_dir: str,
):
    output = str(
        Path(temp_dir) / "%(title)s_%(id)s.%(ext)s"
    )

    options = {
        "format": format_selector(quality),
        "outtmpl": output,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 2,
        "socket_timeout": DOWNLOAD_TIMEOUT,
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)

    return (
        info.get("title") or "Video",
        info.get("duration"),
    )


async def process_download(
    telegram_id: int,
    url: str,
    quality: str,
):
    temp_dir = tempfile.mkdtemp(
        prefix="vd_"
    )

    try:
        title, duration = await asyncio.to_thread(
            yt_download,
            url,
            quality,
            temp_dir,
        )

        if (
            not is_admin(telegram_id)
            and duration
            and duration > MAX_USER_DURATION
        ):
            raise RuntimeError("Duration limit")

        files = [
            p
            for p in Path(temp_dir).iterdir()
            if p.is_file()
        ]

        if not files:
            raise RuntimeError("No downloaded file")

        # After merging, normally the largest file is the final media file.
        media_file = max(
            files,
            key=lambda p: p.stat().st_size,
        )

        return (
            str(media_file),
            title,
            media_file.stat().st_size,
            temp_dir,
        )

    except Exception:
        shutil.rmtree(
            temp_dir,
            ignore_errors=True,
        )
        raise


# ============================================================
# QUALITY / SEND
# ============================================================

@dp.callback_query(F.data.startswith("quality:"))
async def quality_selected(callback: CallbackQuery):
    telegram_id = callback.from_user.id
    quality = callback.data.split(":")[1]

    url = pending_urls.get(telegram_id)

    if not url:
        await callback.answer(
            "Ссылка устарела. Отправьте её ещё раз."
            if lang_of(telegram_id) == "ru"
            else "URL expired. Please send it again.",
            show_alert=True,
        )
        return

    if (
        telegram_id in active_downloads
        and not is_admin(telegram_id)
    ):
        await callback.answer(
            txt(telegram_id, "busy"),
            show_alert=True,
        )
        return

    if not is_admin(telegram_id):
        active_downloads.add(telegram_id)

    await callback.answer()

    status = await callback.message.answer(
        txt(telegram_id, "downloading")
    )

    temp_dir = None

    try:
        (
            file_path,
            title,
            size_bytes,
            temp_dir,
        ) = await process_download(
            telegram_id,
            url,
            quality,
        )

        # Application restriction for ordinary users.
        if (
            not is_admin(telegram_id)
            and size_bytes > MAX_USER_FILE_MB * 1024 * 1024
        ):
            add_download(
                telegram_id,
                title,
                url,
                quality,
                size_bytes,
                "failed",
            )

            await status.edit_text(
                txt(telegram_id, "too_large")
            )
            return

        await status.edit_text(
            txt(telegram_id, "sending")
        )

        document = FSInputFile(
            file_path,
            filename=Path(file_path).name,
        )

        await bot.send_document(
            chat_id=telegram_id,
            document=document,
            caption=(
                f"🎬 <b>{title}</b>\n"
                f"Quality: {quality}"
            ),
        )

        add_download(
            telegram_id,
            title,
            url,
            quality,
            size_bytes,
            "success",
        )

        await status.edit_text(
            txt(telegram_id, "done")
        )

    except Exception:
        logger.exception(
            "Download failed for user %s",
            telegram_id,
        )

        try:
            add_download(
                telegram_id,
                "",
                url,
                quality,
                0,
                "failed",
            )
        except Exception:
            logger.exception("Database error")

        await status.edit_text(
            txt(telegram_id, "failed")
        )

    finally:
        # IMPORTANT:
        # the actual video is removed from the Railway server.
        if temp_dir:
            shutil.rmtree(
                temp_dir,
                ignore_errors=True,
            )

        pending_urls.pop(
            telegram_id,
            None,
        )

        if not is_admin(telegram_id):
            active_downloads.discard(
                telegram_id
            )


# ============================================================
# ADMIN
# ============================================================

@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if not is_admin(message.from_user.id):
        return

    await message.answer(
        "🛠 <b>ADMIN PANEL</b>\n\n"
        "You are running in unrestricted application mode.\n\n"
        "📊 /stats — statistics\n"
        "👥 /users — users\n"
        "👤 /user ID — user information\n"
        "🧾 /recent — recent downloads\n"
        "⛔ /block ID — block user\n"
        "✅ /unblock ID — unblock user\n"
        "📣 /broadcast TEXT — broadcast\n\n"
        "Application limits such as MAX_FILE_MB, duration "
        "and one-download-at-a-time do not apply to your "
        "Telegram ID.\n\n"
        "External Telegram/Railway limits still apply."
    )


@dp.message(Command("stats"))
async def stats(message: Message):
    if not is_admin(message.from_user.id):
        return

    with db_connect() as conn:
        users = conn.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        success = conn.execute(
            """
            SELECT COUNT(*)
            FROM downloads
            WHERE status = 'success'
            """
        ).fetchone()[0]

        failures = conn.execute(
            """
            SELECT COUNT(*)
            FROM downloads
            WHERE status = 'failed'
            """
        ).fetchone()[0]

        total = conn.execute(
            """
            SELECT COALESCE(SUM(size_bytes), 0)
            FROM downloads
            WHERE status = 'success'
            """
        ).fetchone()[0]

    await message.answer(
        "📊 <b>Statistics</b>\n\n"
        f"Users: {users}\n"
        f"Downloads: {success}\n"
        f"Errors: {failures}\n"
        f"Transferred: {format_bytes(total)}"
    )


@dp.message(Command("users"))
async def users(message: Message):
    if not is_admin(message.from_user.id):
        return

    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM users
            ORDER BY id DESC
            LIMIT 50
            """
        ).fetchall()

    if not rows:
        await message.answer("No users.")
        return

    lines = ["👥 <b>Users</b>", ""]

    for row in rows:
        telegram_id = (
            decrypt(row["telegram_id_encrypted"])
            or "?"
        )

        username = (
            decrypt(row["username_encrypted"])
            or "-"
        )

        first_name = (
            decrypt(row["first_name_encrypted"])
            or "-"
        )

        lines.append(
            f"👤 <b>{first_name}</b>\n"
            f"ID: <code>{telegram_id}</code>\n"
            f"@{username}\n"
            f"Downloads: {row['downloads_count']}\n"
            f"Blocked: {'YES' if row['is_blocked'] else 'NO'}\n"
        )

    text = "\n".join(lines)

    # Telegram message size protection.
    for start in range(0, len(text), 3500):
        await message.answer(
            text[start:start + 3500]
        )


@dp.message(Command("user"))
async def user_info(message: Message):
    if not is_admin(message.from_user.id):
        return

    parts = (message.text or "").split(maxsplit=1)

    if len(parts) != 2:
        await message.answer("/user TELEGRAM_ID")
        return

    try:
        target = int(parts[1])
    except ValueError:
        await message.answer("Invalid ID")
        return

    row = get_user(target)

    if not row:
        await message.answer("User not found")
        return

    username = decrypt(row["username_encrypted"]) or "-"
    first_name = decrypt(row["first_name_encrypted"]) or "-"

    await message.answer(
        "👤 <b>User</b>\n\n"
        f"ID: <code>{target}</code>\n"
        f"Name: {first_name}\n"
        f"Username: @{username}\n"
        f"Language: {row['language']}\n"
        f"Downloads: {row['downloads_count']}\n"
        f"Errors: {row['failed_count']}\n"
        f"Transferred: {format_bytes(row['total_bytes'])}\n"
        f"Blocked: {bool(row['is_blocked'])}\n"
        f"Created: {row['created_at']}\n"
        f"Last activity: {row['last_activity']}"
    )


@dp.message(Command("recent"))
async def recent(message: Message):
    if not is_admin(message.from_user.id):
        return

    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM downloads
            ORDER BY id DESC
            LIMIT 20
            """
        ).fetchall()

    if not rows:
        await message.answer("No downloads.")
        return

    lines = ["🧾 <b>Recent downloads</b>", ""]

    for row in rows:
        title = decrypt(row["title_encrypted"]) or "Video"

        lines.append(
            f"🎬 {title[:60]}\n"
            f"Quality: {row['quality']}\n"
            f"Size: {format_bytes(row['size_bytes'])}\n"
            f"Status: {row['status']}\n"
            f"{row['created_at']}\n"
        )

    await message.answer("\n".join(lines))


@dp.message(Command("block"))
async def block_user(message: Message):
    if not is_admin(message.from_user.id):
        return

    parts = (message.text or "").split(maxsplit=1)

    if len(parts) != 2:
        await message.answer("/block TELEGRAM_ID")
        return

    try:
        target = int(parts[1])
    except ValueError:
        await message.answer("Invalid ID")
        return

    if target == ADMIN_ID:
        await message.answer(
            "Admin cannot be blocked."
        )
        return

    set_blocked(target, True)

    await message.answer("⛔ User blocked.")


@dp.message(Command("unblock"))
async def unblock_user(message: Message):
    if not is_admin(message.from_user.id):
        return

    parts = (message.text or "").split(maxsplit=1)

    if len(parts) != 2:
        await message.answer("/unblock TELEGRAM_ID")
        return

    try:
        target = int(parts[1])
    except ValueError:
        await message.answer("Invalid ID")
        return

    set_blocked(target, False)

    await message.answer("✅ User unblocked.")


@dp.message(Command("broadcast"))
async def broadcast(message: Message):
    if not is_admin(message.from_user.id):
        return

    parts = (message.text or "").split(maxsplit=1)

    if len(parts) != 2:
        await message.answer("/broadcast TEXT")
        return

    broadcast_text = parts[1]

    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT telegram_id_encrypted
            FROM users
            WHERE is_blocked = 0
            """
        ).fetchall()

    sent = 0
    failed = 0

    for row in rows:
        raw_id = decrypt(
            row["telegram_id_encrypted"]
        )

        if not raw_id:
            continue

        try:
            await bot.send_message(
                int(raw_id),
                broadcast_text,
            )
            sent += 1

        except Exception:
            failed += 1

        await asyncio.sleep(0.05)

    await message.answer(
        "📣 Broadcast finished\n\n"
        f"Sent: {sent}\n"
        f"Failed: {failed}"
    )


# ============================================================
# MAIN
# ============================================================

async def main():
    init_db()

    logger.info("Video Downloader started")

    await bot.delete_webhook(
        drop_pending_updates=True
    )

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
