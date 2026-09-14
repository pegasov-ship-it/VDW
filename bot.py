import asyncio
import hashlib
import html
import logging
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import asyncpg
import yt_dlp
from cryptography.fernet import Fernet, InvalidToken

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
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

BOT_TOKEN = os.getenv(
    "BOT_TOKEN",
    "",
).strip()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "",
).strip()

DATA_ENCRYPTION_KEY = os.getenv(
    "DATA_ENCRYPTION_KEY",
    "",
).strip()

COBALT_API_URL = os.getenv(
    "COBALT_API_URL",
    "",
).strip().rstrip("/")

BGUTIL_POT_SERVER = os.getenv(
    "BGUTIL_POT_SERVER",
    "",
).strip().rstrip("/")

ADMIN_ID = int(
    os.getenv(
        "ADMIN_ID",
        "0",
    )
)

MAX_FILE_MB = int(
    os.getenv(
        "MAX_FILE_MB",
        "50",
    )
)

TELEGRAM_MAX_FILE_MB = int(
    os.getenv(
        "TELEGRAM_MAX_FILE_MB",
        "50",
    )
)

MAX_DURATION_SECONDS = int(
    os.getenv(
        "MAX_DURATION_SECONDS",
        "21600",
    )
)

DOWNLOAD_TIMEOUT = int(
    os.getenv(
        "DOWNLOAD_TIMEOUT",
        "600",
    )
)

SAFE_FILE_BYTES = int(
    min(
        MAX_FILE_MB,
        TELEGRAM_MAX_FILE_MB,
    )
    * 1024
    * 1024
    * 0.98
)


# ============================================================
# VALIDATION
# ============================================================

required_env = {
    "BOT_TOKEN": BOT_TOKEN,
    "DATABASE_URL": DATABASE_URL,
    "DATA_ENCRYPTION_KEY": DATA_ENCRYPTION_KEY,
    "COBALT_API_URL": COBALT_API_URL,
}

for name, value in required_env.items():
    if not value:
        raise RuntimeError(
            f"{name} is not configured"
        )

try:
    FERNET = Fernet(
        DATA_ENCRYPTION_KEY.encode(
            "utf-8"
        )
    )
except Exception as exc:
    raise RuntimeError(
        "DATA_ENCRYPTION_KEY is invalid"
    ) from exc


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(name)s | "
        "%(message)s"
    ),
)

logger = logging.getLogger(
    "vdw"
)


# ============================================================
# BOT
# ============================================================

bot = Bot(
    BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    ),
)

dp = Dispatcher()


# ============================================================
# STATE
# ============================================================

DB_POOL: Optional[asyncpg.Pool] = None

PENDING: dict[int, dict] = {}

ACTIVE: set[int] = set()

URL_RE = re.compile(
    r"^https?://\S+$",
    re.IGNORECASE,
)

YOUTUBE_RE = re.compile(
    r"(youtube\.com|youtu\.be|youtube-nocookie\.com)",
    re.IGNORECASE,
)

QUALITY_ORDER = (
    2160,
    1440,
    1080,
    720,
    480,
    360,
)


# ============================================================
# TEXT
# ============================================================

TEXT = {
    "ru": {
        "welcome":
            "👋 <b>Добро пожаловать в Video Downloader</b>\n\n"
            "Выберите язык:",

        "terms":
            "📄 <b>Условия использования</b>\n\n"
            "Используйте сервис только для контента, "
            "который вы вправе загружать и использовать.\n\n"
            "Временные файлы удаляются после обработки.",

        "privacy":
            "🔐 <b>Конфиденциальность</b>\n\n"
            "Храним минимально необходимую информацию: "
            "Telegram ID, имя/username, язык и историю загрузок.\n\n"
            "Видео постоянно на сервере не хранится. "
            "Временная копия удаляется после обработки.",

        "ready":
            "✅ <b>Готово!</b>\n\n"
            "Отправьте публичную ссылку на видео.",

        "menu":
            "Главное меню:",

        "send_url":
            "🔗 Отправьте публичную ссылку на видео.",

        "analyzing":
            "🔎 Анализирую доступные качества…",

        "quality":
            "🎬 <b>Выберите качество</b>\n\n"
            "Бот рекомендует максимальное качество, "
            "которое помещается в текущий лимит Telegram.",

        "downloading":
            "⏳ Скачиваю видео…",

        "sending":
            "📤 Отправляю видео в Telegram…",

        "done":
            "✅ <b>Готово!</b>\n\n"
            "Временная копия на сервере удалена.",

        "too_large":
            "❌ <b>Видео невозможно скачать</b>\n\n"
            "Даже самое низкое доступное качество "
            "превышает лимит 50 МБ.\n\n"
            "Попробуйте более короткое видео.",

        "quality_too_large":
            "⚠️ Это качество превышает лимит 50 МБ. "
            "Выберите более низкое качество.",

        "failed":
            "❌ Не удалось обработать видео.\n\n"
            "Попробуйте другую ссылку или другое качество.",

        "busy":
            "⏳ У вас уже выполняется загрузка.",

        "history":
            "📁 <b>Загруженные видео</b>",

        "history_empty":
            "📁 История загрузок пуста.",

        "deleted":
            "✅ Запись удалена.",

        "all_deleted":
            "✅ История удалена.",

        "settings":
            "⚙️ <b>Настройки</b>",

        "language_changed":
            "✅ Язык изменён.",

        "blocked":
            "⛔ Доступ к сервису ограничен.",

        "invalid_url":
            "❌ Отправьте корректную ссылку, "
            "начинающуюся с http:// или https://.",

        "deleted_account":
            "✅ Ваши данные удалены.",

        "no_url":
            "⚠️ Сначала отправьте ссылку на видео.",

        "analysis_failed":
            "❌ Не удалось получить информацию о видео.",

        "download":
            "➕ Скачать видео",

        "history_btn":
            "📁 Загруженные видео",

        "settings_btn":
            "⚙️ Настройки",

        "delete_data":
            "🗑 Удалить мои данные",

        "delete_history":
            "🗑 Удалить всю историю",

        "back":
            "⬅️ В меню",

        "auto":
            "🎯 Авто",

        "best":
            "⭐ Максимум",
    },

    "en": {
        "welcome":
            "👋 <b>Welcome to Video Downloader</b>\n\n"
            "Choose your language:",

        "terms":
            "📄 <b>Terms of Use</b>\n\n"
            "Use the service only for content you are "
            "entitled to download and use.\n\n"
            "Temporary files are deleted after processing.",

        "privacy":
            "🔐 <b>Privacy</b>\n\n"
            "We store only the minimum information required: "
            "Telegram ID, name/username, language and download history.\n\n"
            "Videos are not permanently stored on the server. "
            "The temporary copy is deleted after processing.",

        "ready":
            "✅ <b>Ready!</b>\n\n"
            "Send a public video URL.",

        "menu":
            "Main menu:",

        "send_url":
            "🔗 Send a public video URL.",

        "analyzing":
            "🔎 Checking available qualities…",

        "quality":
            "🎬 <b>Choose quality</b>\n\n"
            "The bot recommends the highest quality "
            "that fits the current Telegram limit.",

        "downloading":
            "⏳ Downloading video…",

        "sending":
            "📤 Sending video to Telegram…",

        "done":
            "✅ <b>Done!</b>\n\n"
            "The temporary server copy has been deleted.",

        "too_large":
            "❌ <b>This video cannot be downloaded</b>\n\n"
            "Even the lowest available quality exceeds "
            "the 50 MB limit.\n\n"
            "Please try a shorter video.",

        "quality_too_large":
            "⚠️ This quality exceeds the 50 MB limit. "
            "Choose a lower quality.",

        "failed":
            "❌ Could not process the video.\n\n"
            "Try another URL or another quality.",

        "busy":
            "⏳ You already have an active download.",

        "history":
            "📁 <b>Downloaded videos</b>",

        "history_empty":
            "📁 Download history is empty.",

        "deleted":
            "✅ Entry deleted.",

        "all_deleted":
            "✅ History deleted.",

        "settings":
            "⚙️ <b>Settings</b>",

        "language_changed":
            "✅ Language changed.",

        "blocked":
            "⛔ Access to the service is restricted.",

        "invalid_url":
            "❌ Send a valid URL beginning with "
            "http:// or https://.",

        "deleted_account":
            "✅ Your data has been deleted.",

        "no_url":
            "⚠️ Send a video URL first.",

        "analysis_failed":
            "❌ Could not get information about the video.",

        "download":
            "➕ Download video",

        "history_btn":
            "📁 Downloaded videos",

        "settings_btn":
            "⚙️ Settings",

        "delete_data":
            "🗑 Delete my data",

        "delete_history":
            "🗑 Delete all history",

        "back":
            "⬅️ Main menu",

        "auto":
            "🎯 Auto",

        "best":
            "⭐ Best",
    },
}


# ============================================================
# DATABASE
# ============================================================

async def init_db():

    global DB_POOL

    DB_POOL = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=5,
        command_timeout=60,
    )

    async with DB_POOL.acquire() as conn:

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id BIGSERIAL PRIMARY KEY,
                user_hash TEXT UNIQUE NOT NULL,
                telegram_id_encrypted TEXT NOT NULL,
                username_encrypted TEXT,
                first_name_encrypted TEXT,
                language TEXT NOT NULL DEFAULT 'ru',
                setup_complete BOOLEAN NOT NULL DEFAULT FALSE,
                terms_accepted BOOLEAN NOT NULL DEFAULT FALSE,
                is_blocked BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL,
                last_activity TIMESTAMPTZ NOT NULL,
                downloads_count BIGINT NOT NULL DEFAULT 0,
                failed_count BIGINT NOT NULL DEFAULT 0,
                total_bytes BIGINT NOT NULL DEFAULT 0
            )
            """
        )

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS downloads (
                id BIGSERIAL PRIMARY KEY,
                user_hash TEXT NOT NULL,
                title_encrypted TEXT,
                url_encrypted TEXT,
                quality TEXT NOT NULL,
                size_bytes BIGINT NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            )
            """
        )

        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_users_hash
            ON users(user_hash)
            """
        )

        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_downloads_user
            ON downloads(user_hash)
            """
        )

    logger.info(
        "PostgreSQL initialized"
    )


async def get_user(
    user_id: int,
):

    async with DB_POOL.acquire() as conn:

        return await conn.fetchrow(
            """
            SELECT *
            FROM users
            WHERE user_hash = $1
            """,
            user_hash(user_id),
        )


async def ensure_user(
    user_id: int,
    username: Optional[str],
    first_name: Optional[str],
):

    now = datetime.now(
        timezone.utc
    )

    async with DB_POOL.acquire() as conn:

        await conn.execute(
            """
            INSERT INTO users (
                user_hash,
                telegram_id_encrypted,
                username_encrypted,
                first_name_encrypted,
                created_at,
                last_activity
            )

            VALUES (
                $1,
                $2,
                $3,
                $4,
                $5,
                $5
            )

            ON CONFLICT (
                user_hash
            )

            DO UPDATE SET
                username_encrypted =
                    EXCLUDED.username_encrypted,

                first_name_encrypted =
                    EXCLUDED.first_name_encrypted,

                last_activity =
                    EXCLUDED.last_activity
            """,
            user_hash(user_id),
            encrypt(str(user_id)),
            encrypt(username),
            encrypt(first_name),
            now,
        )


async def set_language(
    user_id: int,
    language: str,
):

    async with DB_POOL.acquire() as conn:

        await conn.execute(
            """
            UPDATE users
            SET language = $1
            WHERE user_hash = $2
            """,
            language,
            user_hash(user_id),
        )


async def accept_terms(
    user_id: int,
):

    async with DB_POOL.acquire() as conn:

        await conn.execute(
            """
            UPDATE users
            SET
                setup_complete = TRUE,
                terms_accepted = TRUE
            WHERE user_hash = $1
            """,
            user_hash(user_id),
        )


async def delete_user_data(
    user_id: int,
):

    key = user_hash(
        user_id
    )

    async with DB_POOL.acquire() as conn:

        await conn.execute(
            """
            DELETE FROM downloads
            WHERE user_hash = $1
            """,
            key,
        )

        await conn.execute(
            """
            DELETE FROM users
            WHERE user_hash = $1
            """,
            key,
        )


async def record_download(
    user_id: int,
    title: str,
    url: str,
    quality: str,
    size_bytes: int,
    status: str,
):

    key = user_hash(
        user_id
    )

    async with DB_POOL.acquire() as conn:

        await conn.execute(
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

            VALUES (
                $1,
                $2,
                $3,
                $4,
                $5,
                $6,
                $7
            )
            """,
            key,
            encrypt(title),
            encrypt(url),
            quality,
            int(size_bytes),
            status,
            datetime.now(
                timezone.utc
            ),
        )

        if status == "success":

            await conn.execute(
                """
                UPDATE users
                SET
                    downloads_count =
                        downloads_count + 1,

                    total_bytes =
                        total_bytes + $1

                WHERE user_hash = $2
                """,
                int(size_bytes),
                key,
            )

        else:

            await conn.execute(
                """
                UPDATE users
                SET failed_count =
                    failed_count + 1
                WHERE user_hash = $1
                """,
                key,
            )


# ============================================================
# HELPERS
# ============================================================

def encrypt(
    value: Optional[str],
) -> Optional[str]:

    if value is None:
        return None

    return FERNET.encrypt(
        value.encode("utf-8")
    ).decode("utf-8")


def decrypt(
    value: Optional[str],
) -> Optional[str]:

    if not value:
        return None

    try:
        return FERNET.decrypt(
            value.encode("utf-8")
        ).decode("utf-8")

    except InvalidToken:
        return None


def user_hash(
    user_id: int,
) -> str:

    return hashlib.sha256(
        f"telegram:{user_id}".encode(
            "utf-8"
        )
    ).hexdigest()


def esc(
    value: Optional[str],
) -> str:

    return html.escape(
        value or ""
    )


def fmt_bytes(
    size: int,
) -> str:

    if size < 1024 * 1024:

        return (
            f"{size / 1024:.1f} KB"
        )

    if size < 1024**3:

        return (
            f"{size / 1024**2:.1f} MB"
        )

    return (
        f"{size / 1024**3:.2f} GB"
    )


def row_language(
    row,
) -> str:

    if (
        row
        and row["language"] in TEXT
    ):

        return row["language"]

    return "ru"


def is_admin(
    user_id: int,
) -> bool:

    return user_id == ADMIN_ID


def is_youtube(
    url: str,
) -> bool:

    return bool(
        YOUTUBE_RE.search(
            url
        )
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


def consent_keyboard(
    language: str,
):

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=(
                        "✅ Принять и продолжить"
                        if language == "ru"
                        else "✅ Accept & continue"
                    ),
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


def main_keyboard(
    language: str,
):

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=TEXT[language]["download"],
                    callback_data="menu:download",
                )
            ],
            [
                InlineKeyboardButton(
                    text=TEXT[language]["history_btn"],
                    callback_data="menu:history",
                ),
                InlineKeyboardButton(
                    text=TEXT[language]["settings_btn"],
                    callback_data="menu:settings",
                ),
            ],
        ]
    )


def back_keyboard(
    language: str,
):

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=TEXT[language]["back"],
                    callback_data="menu:main",
                )
            ]
        ]
    )


def settings_keyboard(
    language: str,
):

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
                    text=TEXT[language]["delete_data"],
                    callback_data="account:delete",
                )
            ],
            [
                InlineKeyboardButton(
                    text=TEXT[language]["back"],
                    callback_data="menu:main",
                )
            ],
        ]
    )


def quality_keyboard(
    language: str,
    items: list[dict],
):

    rows = []

    usable = [
        item
        for item in items
        if (
            item.get("size") is None
            or item["size"] <= SAFE_FILE_BYTES
        )
    ]

    auto_quality = None

    for quality in QUALITY_ORDER:

        if any(
            item["quality"] == str(quality)
            for item in usable
        ):

            auto_quality = str(
                quality
            )

            break

    if auto_quality:

        item = next(
            item
            for item in items
            if item["quality"]
            == auto_quality
        )

        label = (
            f"{TEXT[language]['auto']} "
            f"— {auto_quality}p"
        )

        if item.get("size"):

            label += (
                f" ~{fmt_bytes(item['size'])}"
            )

        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data="quality:auto",
                )
            ]
        )

    for item in items:

        quality = item["quality"]

        if int(quality) > 1080:
            continue

        label = f"{quality}p"

        if item.get("size"):

            label += (
                f" — "
                f"~{fmt_bytes(item['size'])}"
            )

            if item["size"] <= SAFE_FILE_BYTES:

                label += " ✅"

            else:

                label += " ❌"

        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=(
                        f"quality:{quality}"
                    ),
                )
            ]
        )

    if any(
        int(item["quality"]) > 1080
        for item in items
    ):

        rows.append(
            [
                InlineKeyboardButton(
                    text=TEXT[language]["best"],
                    callback_data="quality:best",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=TEXT[language]["back"],
                callback_data="menu:main",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard
