import asyncio
import hashlib
import html
import json
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

INSTAGRAM_RE = re.compile(
    r"instagram\.com",
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
            "Временная копия удаляется после отправки.",

        "ready":
            "✅ <b>Готово!</b>\n\n"
            "Отправьте публичную ссылку на видео.",

        "menu":
            "Главное меню:",

        "send_url":
            "🔗 Отправьте публичную ссылку на видео.",

        "analyzing":
            "🔎 Анализирую доступные способы загрузки…",

        "quality":
            "🎬 <b>Выберите качество</b>\n\n"
            "Если один источник временно недоступен, "
            "бот попробует резервный.",

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
            "превышает лимит 50 МБ.",

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

        "admin_btn":
            "🔐 Админ",

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
            "The temporary copy is deleted after sending.",

        "ready":
            "✅ <b>Ready!</b>\n\n"
            "Send a public video URL.",

        "menu":
            "Main menu:",

        "send_url":
            "🔗 Send a public video URL.",

        "analyzing":
            "🔎 Checking available download methods…",

        "quality":
            "🎬 <b>Choose quality</b>\n\n"
            "If one source is temporarily unavailable, "
            "the bot will try a fallback.",

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
            "50 MB.",

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

        "admin_btn":
            "🔐 Admin",

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
        return f"{size / 1024:.1f} KB"

    if size < 1024**3:
        return f"{size / 1024**2:.1f} MB"

    return f"{size / 1024**3:.2f} GB"


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


def is_instagram(
    url: str,
) -> bool:

    return bool(
        INSTAGRAM_RE.search(
            url
        )
    )


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
                telegram_chat_id BIGINT,
                telegram_message_id BIGINT,
                telegram_file_id TEXT,
                created_at TIMESTAMPTZ NOT NULL
            )
            """
        )

        await conn.execute(
            """
            ALTER TABLE downloads
            ADD COLUMN IF NOT EXISTS telegram_chat_id BIGINT
            """
        )

        await conn.execute(
            """
            ALTER TABLE downloads
            ADD COLUMN IF NOT EXISTS telegram_message_id BIGINT
            """
        )

        await conn.execute(
            """
            ALTER TABLE downloads
            ADD COLUMN IF NOT EXISTS telegram_file_id TEXT
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
            encrypt(
                str(user_id)
            ),
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


async def set_blocked(
    user_id: int,
    value: bool,
):

    async with DB_POOL.acquire() as conn:

        await conn.execute(
            """
            UPDATE users
            SET is_blocked = $1
            WHERE user_hash = $2
            """,
            value,
            user_hash(user_id),
        )


async def record_download(
    user_id: int,
    title: str,
    url: str,
    quality: str,
    size_bytes: int,
    status: str,
    telegram_chat_id: Optional[int] = None,
    telegram_message_id: Optional[int] = None,
    telegram_file_id: Optional[str] = None,
):

    key = user_hash(
        user_id
    )

    async with DB_POOL.acquire() as conn:

        row = await conn.fetchrow(
            """
            INSERT INTO downloads (
                user_hash,
                title_encrypted,
                url_encrypted,
                quality,
                size_bytes,
                status,
                telegram_chat_id,
                telegram_message_id,
                telegram_file_id,
                created_at
            )

            VALUES (
                $1,
                $2,
                $3,
                $4,
                $5,
                $6,
                $7,
                $8,
                $9,
                $10
            )

            RETURNING id
            """,
            key,
            encrypt(title),
            encrypt(url),
            quality,
            int(size_bytes),
            status,
            telegram_chat_id,
            telegram_message_id,
            telegram_file_id,
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
                SET
                    failed_count =
                        failed_count + 1

                WHERE user_hash = $1
                """,
                key,
            )

        return row["id"]


async def delete_download(
    user_id: int,
    item_id: int,
):

    async with DB_POOL.acquire() as conn:

        row = await conn.fetchrow(
            """
            DELETE FROM downloads

            WHERE
                id = $1
                AND user_hash = $2

            RETURNING
                telegram_chat_id,
                telegram_message_id
            """,
            item_id,
            user_hash(user_id),
        )

    if (
        row
        and row["telegram_chat_id"]
        and row["telegram_message_id"]
    ):

        try:

            await bot.delete_message(
                row["telegram_chat_id"],
                row["telegram_message_id"],
            )

        except Exception as exc:

            logger.info(
                "Telegram message deletion failed: %s",
                exc,
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
                        else
                        "✅ Accept & continue"
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


def main_keyboard_for_user(
    language: str,
    user_id: int,
):

    rows = [
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

    if is_admin(user_id):

        rows.append(
            [
                InlineKeyboardButton(
                    text=TEXT[language]["admin_btn"],
                    callback_data="menu:admin",
                )
            ]
        )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
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
    user_id: int,
):

    rows = [
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

    if is_admin(user_id):

        rows.insert(
            -1,
            [
                InlineKeyboardButton(
                    text=TEXT[language]["admin_btn"],
                    callback_data="menu:admin",
                )
            ],
        )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
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
            or item["size"]
            <= SAFE_FILE_BYTES
        )
    ]

    auto_quality = next(
        (
            str(quality)
            for quality in QUALITY_ORDER
            if any(
                item["quality"]
                == str(quality)

                for item in usable
            )
        ),
        None,
    )

    if auto_quality:

        item = next(
            item
            for item in items
            if item["quality"]
            == auto_quality
        )

        label = (
            f"{TEXT[language]['auto']}"
            f" — {auto_quality}p"
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

        label = (
            f"{quality}p"
        )

        if item.get("size"):

            label += (
                f" — ~"
                f"{fmt_bytes(item['size'])}"
            )

            if (
                item["size"]
                <= SAFE_FILE_BYTES
            ):

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

    rows.append(
        [
            InlineKeyboardButton(
                text=TEXT[language]["back"],
                callback_data="menu:main",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


# ============================================================
# YT-DLP
# ============================================================

def ytdlp_options(
    client: str,
):

    args = {
        "youtube": {
            "player_client": [
                client
            ]
        }
    }

    if BGUTIL_POT_SERVER:

        args[
            "youtubepot-bgutilhttp"
        ] = {
            "base_url": [
                BGUTIL_POT_SERVER
            ]
        }

    return {
        "quiet": True,
        "no_warnings": False,
        "noplaylist": True,
        "socket_timeout":
            DOWNLOAD_TIMEOUT,
        "retries": 2,
        "extractor_args":
            args,
    }


class YTDLPLogger:

    def debug(
        self,
        message,
    ):

        text = str(
            message
        )

        important = (
            "PO Token",
            "bgutil",
            "[youtube]",
            "sabr",
            "formats",
        )

        if any(
            key.lower()
            in text.lower()
            for key in important
        ):

            logger.info(
                "yt-dlp: %s",
                text,
            )

    def warning(
        self,
        message,
    ):

        logger.warning(
            "yt-dlp: %s",
            message,
        )

    def error(
        self,
        message,
    ):

        logger.error(
            "yt-dlp: %s",
            message,
        )


def ytdlp_extract_sync(
    url: str,
    client: str,
):

    opts = ytdlp_options(
        client
    )

    opts[
        "logger"
    ] = YTDLPLogger()

    with yt_dlp.YoutubeDL(
        opts
    ) as ydl:

        return ydl.extract_info(
            url,
            download=False,
        )


def fmt_size(
    fmt: dict,
) -> Optional[int]:

    value = (
        fmt.get("filesize")
        or fmt.get("filesize_approx")
    )

    if value is None:

        return None

    try:

        return int(value)

    except (
        TypeError,
        ValueError,
    ):

        return None


def video_formats(
    info: dict,
) -> list[dict]:

    result = []

    for fmt in (
        info.get("formats")
        or []
    ):

        if (
            not fmt.get("height")
            or fmt.get("vcodec")
            in (
                None,
                "none",
            )
        ):

            continue

        try:

            height = int(
                fmt["height"]
            )

        except (
            TypeError,
            ValueError,
        ):

            continue

        try:

            width = (
                int(
                    fmt["width"]
                )
                if fmt.get(
                    "width"
                )
                is not None
                else None
            )

        except (
            TypeError,
            ValueError,
        ):

            width = None

        result.append(
            {
                "format_id":
                    fmt.get("format_id"),

                "width":
                    width,

                "height":
                    height,

                "size":
                    fmt_size(fmt),

                "ext":
                    fmt.get("ext"),

                "vcodec":
                    fmt.get("vcodec"),

                "acodec":
                    fmt.get("acodec"),

                "fps":
                    fmt.get("fps"),

                "aspect_ratio":
                    fmt.get(
                        "aspect_ratio"
                    ),

                "stretched_ratio":
                    fmt.get(
                        "stretched_ratio"
                    ),
            }
        )

    return result


def audio_formats(
    info: dict,
) -> list[dict]:

    result = []

    for fmt in (
        info.get("formats")
        or []
    ):

        if (
            not fmt.get("acodec")
            or fmt.get("acodec")
            == "none"
        ):

            continue

        if fmt.get(
            "vcodec"
        ) not in (
            None,
            "none",
        ):

            continue

        size = fmt_size(
            fmt
        )

        if size is None:

            continue

        result.append(
            {
                "format_id":
                    fmt.get(
                        "format_id"
                    ),

                "size":
                    size,

                "ext":
                    fmt.get("ext"),

                "abr":
                    float(
                        fmt.get("abr")
                        or 0
                    ),

                "acodec":
                    fmt.get("acodec"),
            }
        )

    return result


def best_video(
    info: dict,
    max_height: int,
) -> Optional[dict]:

    candidates = []

    for fmt in video_formats(
        info
    ):

        if (
            fmt["height"]
            > max_height
        ):

            continue

        if fmt["size"] is None:

            continue

        codec = str(
            fmt.get(
                "vcodec"
            )
            or ""
        )

        h264 = int(
            codec.startswith(
                "avc1"
            )
            or codec.startswith(
                "h264"
            )
        )

        mp4 = int(
            fmt.get("ext")
            == "mp4"
        )

        candidates.append(
            (
                (
                    fmt["height"],
                    h264,
                    mp4,
                    float(
                        fmt.get(
                            "fps"
                        )
                        or 0
                    ),
                    fmt["size"],
                ),
                fmt,
            )
        )

    if not candidates:

        return None

    return max(
        candidates,
        key=lambda item:
            item[0]
    )[1]


def best_audio(
    info: dict,
) -> Optional[dict]:

    candidates = []

    for fmt in audio_formats(
        info
    ):

        m4a = int(
            fmt.get("ext")
            == "m4a"
        )

        candidates.append(
            (
                (
                    m4a,
                    fmt["abr"],
                    fmt["size"],
                ),
                fmt,
            )
        )

    if not candidates:

        return None

    return max(
        candidates,
        key=lambda item:
            item[0]
    )[1]


def make_youtube_items(
    info: dict,
    client: str,
) -> list[dict]:

    audio = best_audio(
        info
    )

    if not audio:

        return []

    result = []

    for quality in QUALITY_ORDER:

        video = best_video(
            info,
            quality,
        )

        if not video:

            continue

        if video["size"] is None:

            continue

        estimated = (
            video["size"]
            + audio["size"]
        )

        result.append(
            {
                "quality":
                    str(quality),

                "size":
                    estimated,

                "video_format_id":
                    video["format_id"],

                "audio_format_id":
                    audio["format_id"],

                "width":
                    video.get("width"),

                "height":
                    video.get("height"),

                "aspect_ratio":
                    video.get(
                        "aspect_ratio"
                    ),

                "stretched_ratio":
                    video.get(
                        "stretched_ratio"
                    ),

                "client":
                    client,
            }
        )

    return result


def choose_youtube_auto(
    items: list[dict],
) -> Optional[dict]:

    for quality in QUALITY_ORDER:

        for item in items:

            if (
                item["quality"]
                == str(quality)

                and (
                    item.get(
                        "size"
                    )
                    is None

                    or item["size"]
                    <= SAFE_FILE_BYTES
                )
            ):

                return item

    return None


def download_youtube_sync(
    url: str,
    item: dict,
    directory: str,
):

    output = str(
        Path(directory)
        / "%(title).80s_%(id)s.%(ext)s"
    )

    opts = ytdlp_options(
        item["client"]
    )

    opts[
        "logger"
    ] = YTDLPLogger()

    opts.update(
        {
            "format":
                (
                    f"{item['video_format_id']}"
                    "+"
                    f"{item['audio_format_id']}"
                ),

            "outtmpl":
                output,

            "merge_output_format":
                "mp4",
        }
    )

    with yt_dlp.YoutubeDL(
        opts
    ) as ydl:

        info = ydl.extract_info(
            url,
            download=True,
        )

    files = [
        path
        for path in Path(
            directory
        ).iterdir()
        if path.is_file()
    ]

    if not files:

        raise RuntimeError(
            "yt-dlp produced no file"
        )

    media = max(
        files,
        key=lambda path:
            path.stat().st_size
    )

    return media, info


# ============================================================
# COBALT
# ============================================================

def cobalt_request_sync(
    url: str,
    quality: str,
) -> dict:

    payload = {
        "url":
            url,

        "videoQuality":
            quality,

        "audioFormat":
            "best",

        "downloadMode":
            "auto",

        "filenameStyle":
            "pretty",

        "youtubeVideoCodec":
            "h264",
    }

    req = Request(
        f"{COBALT_API_URL}/",
        data=json.dumps(
            payload
        ).encode(
            "utf-8"
        ),
        headers={
            "Accept":
                "application/json",

            "Content-Type":
                "application/json",

            "User-Agent":
                "VideoDownloader/1.0",
        },
        method="POST",
    )

    try:

        with urlopen(
            req,
            timeout=DOWNLOAD_TIMEOUT,
        ) as response:

            return json.loads(
                response.read().decode(
                    "utf-8"
                )
            )

    except HTTPError as exc:

        body = exc.read().decode(
            "utf-8",
            errors="replace",
        )

        raise RuntimeError(
            f"Cobalt HTTP {exc.code}: "
            f"{body[:500]}"
        ) from exc

    except URLError as exc:

        raise RuntimeError(
            f"Cobalt connection error: "
            f"{exc}"
        ) from exc

    except ValueError as exc:

        raise RuntimeError(
            "Cobalt returned invalid JSON"
        ) from exc


def cobalt_media(
    response: dict,
) -> Optional[dict]:

    status = response.get(
        "status"
    )

    if (
        status in (
            "tunnel",
            "redirect",
            "stream",
            "success",
        )

        and response.get("url")
    ):

        return {
            "url":
                response["url"],

            "filename":
                response.get(
                    "filename"
                )
                or "video.mp4",
        }

    if status == "picker":

        for item in (
            response.get(
                "picker"
            )
            or []
        ):

            if (
                item.get("type")
                == "video"

                and item.get("url")
            ):

                return {
                    "url":
                        item["url"],

                    "filename":
                        item.get(
                            "filename"
                        )
                        or "video.mp4",
                }

    if response.get("url"):

        return {
            "url":
                response["url"],

            "filename":
                response.get(
                    "filename"
                )
                or "video.mp4",
        }

    error = response.get(
        "error"
    )

    if error:

        raise RuntimeError(
            f"Cobalt error: {error}"
        )

    return None


async def cobalt_items(
    url: str,
) -> list[dict]:

    result = []

    for quality in (
        "1080",
        "720",
        "480",
        "360",
    ):

        try:

            media = (
                await asyncio.to_thread(
                    cobalt_request_sync,
                    url,
                    quality,
                )
            )

            media = cobalt_media(
                media
            )

            if media:

                result.append(
                    {
                        "quality":
                            quality,

                        "url":
                            media["url"],

                        "filename":
                            media["filename"],

                        "size":
                            None,
                    }
                )

        except Exception as exc:

            logger.warning(
                "Cobalt %sp failed: %s",
                quality,
                exc,
            )

    return result


# ============================================================
# DIRECT DOWNLOAD
# ============================================================

def download_direct_sync(
    url: str,
    directory: str,
    max_bytes: Optional[int],
    filename: str,
):

    name = re.sub(
        r"[^\w. -]+",
        "_",
        filename,
    ).strip()

    if not name:

        name = "video.mp4"

    name = Path(
        name
    ).name

    path = (
        Path(directory)
        / name
    )

    if not path.suffix:

        path = path.with_suffix(
            ".mp4"
        )

    request = Request(
        url,
        headers={
            "User-Agent":
                "Mozilla/5.0"
        },
    )

    total = 0

    try:

        with urlopen(
            request,
            timeout=DOWNLOAD_TIMEOUT,
        ) as response:

            with open(
                path,
                "wb",
            ) as output:

                while True:

                    chunk = response.read(
                        1024 * 1024
                    )

                    if not chunk:

                        break

                    total += len(
                        chunk
                    )

                    if (
                        max_bytes is not None
                        and total > max_bytes
                    ):

                        output.close()

                        path.unlink(
                            missing_ok=True
                        )

                        return None

                    output.write(
                        chunk
                    )

        return path

    except Exception:

        path.unlink(
            missing_ok=True
        )

        raise


# ============================================================
# START
# ============================================================

@dp.message(
    CommandStart()
)
async def start(
    message: Message,
):

    uid = message.from_user.id

    logger.info(
        "START received from user %s",
        uid,
    )

    await ensure_user(
        uid,
        message.from_user.username,
        message.from_user.first_name,
    )

    row = await get_user(
        uid
    )

    if (
        row
        and row["is_blocked"]
    ):

        await message.answer(
            TEXT["ru"]["blocked"]
        )

        return

    if (
        not row
        or not row["setup_complete"]
    ):

        await message.answer(
            TEXT["ru"]["welcome"],
            reply_markup=language_keyboard(),
        )

        return

    language = row_language(
        row
    )

    await message.answer(
        TEXT[language]["menu"],
        reply_markup=main_keyboard_for_user(
            language,
            uid,
        ),
    )


# ============================================================
# LANGUAGE
# ============================================================

@dp.callback_query(
    F.data.startswith(
        "lang:"
    )
)
async def language_select(
    callback: CallbackQuery,
):

    language = (
        callback.data
        .split(
            ":",
            1
        )[1]
    )

    if language not in TEXT:

        await callback.answer(
            "Invalid language",
            show_alert=True,
        )

        return

    await set_language(
        callback.from_user.id,
        language,
    )

    await callback.message.edit_text(
        TEXT[language]["terms"],
        reply_markup=consent_keyboard(
            language
        ),
    )

    await callback.answer()


@dp.callback_query(
    F.data == "consent:accept"
)
async def consent_accept(
    callback: CallbackQuery,
):

    uid = callback.from_user.id

    await accept_terms(
        uid
    )

    row = await get_user(
        uid
    )

    language = row_language(
        row
    )

    await callback.message.edit_text(
        TEXT[language]["ready"],
        reply_markup=main_keyboard_for_user(
            language,
            uid,
        ),
    )

    await callback.answer()


# ============================================================
# INFO
# ============================================================

@dp.callback_query(
    F.data == "info:terms"
)
async def info_terms(
    callback: CallbackQuery,
):

    row = await get_user(
        callback.from_user.id
    )

    language = row_language(
        row
    )

    await callback.message.answer(
        TEXT[language]["terms"],
        reply_markup=back_keyboard(
            language
        ),
    )

    await callback.answer()


@dp.callback_query(
    F.data == "info:privacy"
)
async def info_privacy(
    callback: CallbackQuery,
):

    row = await get_user(
        callback.from_user.id
    )

    language = row_language(
        row
    )

    await callback.message.answer(
        TEXT[language]["privacy"],
        reply_markup=back_keyboard(
            language
        ),
    )

    await callback.answer()


@dp.message(
    Command("terms")
)
async def terms_command(
    message: Message,
):

    row = await get_user(
        message.from_user.id
    )

    language = row_language(
        row
    )

    await message.answer(
        TEXT[language]["terms"],
        reply_markup=back_keyboard(
            language
        ),
    )


@dp.message(
    Command("privacy")
)
async def privacy_command(
    message: Message,
):

    row = await get_user(
        message.from_user.id
    )

    language = row_language(
        row
    )

    await message.answer(
        TEXT[language]["privacy"],
        reply_markup=back_keyboard(
            language
        ),
    )


# ============================================================
# MENU
# ============================================================

@dp.callback_query(
    F.data == "menu:main"
)
async def menu_main(
    callback: CallbackQuery,
):

    uid = callback.from_user.id

    row = await get_user(
        uid
    )

    language = row_language(
        row
    )

    await callback.message.edit_text(
        TEXT[language]["menu"],
        reply_markup=main_keyboard_for_user(
            language,
            uid,
        ),
    )

    await callback.answer()


@dp.callback_query(
    F.data == "menu:download"
)
async def menu_download(
    callback: CallbackQuery,
):

    row = await get_user(
        callback.from_user.id
    )

    language = row_language(
        row
    )

    await callback.message.edit_text(
        TEXT[language]["send_url"],
        reply_markup=back_keyboard(
            language
        ),
    )

    await callback.answer()


@dp.callback_query(
    F.data == "menu:settings"
)
async def menu_settings(
    callback: CallbackQuery,
):

    uid = callback.from_user.id

    row = await get_user(
        uid
    )

    language = row_language(
        row
    )

    await callback.message.edit_text(
        TEXT[language]["settings"],
        reply_markup=settings_keyboard(
            language,
            uid,
        ),
    )

    await callback.answer()


# ============================================================
# SETTINGS
# ============================================================

@dp.message(
    Command("settings")
)
async def settings_command(
    message: Message,
):

    uid = message.from_user.id

    row = await get_user(
        uid
    )

    language = row_language(
        row
    )

    await message.answer(
        TEXT[language]["settings"],
        reply_markup=settings_keyboard(
            language,
            uid,
        ),
    )


@dp.callback_query(
    F.data.startswith(
        "settings_lang:"
    )
)
async def settings_language(
    callback: CallbackQuery,
):

    uid = callback.from_user.id

    language = (
        callback.data
        .split(
            ":",
            1
        )[1]
    )

    if language not in TEXT:

        await callback.answer(
            "Invalid language",
            show_alert=True,
        )

        return

    await set_language(
        uid,
        language,
    )

    await callback.message.edit_text(
        TEXT[language]["settings"],
        reply_markup=settings_keyboard(
            language,
            uid,
        ),
    )

    await callback.answer(
        TEXT[language]["language_changed"]
    )


@dp.callback_query(
    F.data == "account:delete"
)
async def account_delete(
    callback: CallbackQuery,
):

    uid = callback.from_user.id

    row = await get_user(
        uid
    )

    language = row_language(
        row
    )

    await delete_user_data(
        uid
    )

    PENDING.pop(
        uid,
        None,
    )

    ACTIVE.discard(
        uid
    )

    await callback.message.edit_text(
        TEXT[language]["deleted_account"]
    )

    await callback.answer()


@dp.message(
    Command("delete_me")
)
async def delete_me(
    message: Message,
):

    uid = message.from_user.id

    row = await get_user(
        uid
    )

    language = row_language(
        row
    )

    await delete_user_data(
        uid
    )

    PENDING.pop(
        uid,
        None,
    )

    ACTIVE.discard(
        uid
    )

    await message.answer(
        TEXT[language]["deleted_account"]
    )


# ============================================================
# URL ROUTER
# ============================================================

@dp.message(
    F.text
)
async def text_router(
    message: Message,
):

    uid = message.from_user.id

    logger.info(
        "TEXT received from user %s: %s",
        uid,
        (
            message.text
            or ""
        )[:300],
    )

    await ensure_user(
        uid,
        message.from_user.username,
        message.from_user.first_name,
    )

    row = await get_user(
        uid
    )

    if (
        row
        and row["is_blocked"]
    ):

        await message.answer(
            TEXT["ru"]["blocked"]
        )

        return

    if (
        not row
        or not row["setup_complete"]
    ):

        await message.answer(
            TEXT["ru"]["welcome"],
            reply_markup=language_keyboard(),
        )

        return

    language = row_language(
        row
    )

    url = (
        message.text
        or ""
    ).strip()

    if not URL_RE.match(
        url
    ):

        await message.answer(
            TEXT[language]["invalid_url"],
            reply_markup=back_keyboard(
                language
            ),
        )

        return

    if (
        uid in ACTIVE
        and not is_admin(uid)
    ):

        await message.answer(
            TEXT[language]["busy"]
        )

        return

    status = await message.answer(
        TEXT[language]["analyzing"],
        reply_markup=back_keyboard(
            language
        ),
    )

    try:

        # ----------------------------------------------------
        # YOUTUBE
        # ----------------------------------------------------

        if is_youtube(url):

            info = None
            used_client = None
            items = []

            clients = [
                "web_embedded",
                "web_safari",
                "mweb",
                "android_vr",
            ]

            for client_name in clients:

                try:

                    logger.info(
                        "[YouTube] analysis attempt: %s",
                        client_name,
                    )

                    candidate = (
                        await asyncio.to_thread(
                            ytdlp_extract_sync,
                            url,
                            client_name,
                        )
                    )

                    candidate_items = (
                        make_youtube_items(
                            candidate,
                            client_name,
                        )
                    )

                    if candidate_items:

                        info = candidate
                        used_client = client_name
                        items = candidate_items

                        logger.info(
                            "[YouTube] analysis success: %s",
                            client_name,
                        )

                        break

                except Exception as exc:

                    logger.warning(
                        "[YouTube] %s failed: %s",
                        client_name,
                        exc,
                    )

            if not info:

                logger.info(
                    "[YouTube] yt-dlp exhausted, "
                    "trying Cobalt fallback"
                )

                cobalt = await cobalt_items(
                    url
                )

                if cobalt:

                    PENDING[uid] = {
                        "source":
                            "cobalt",

                        "url":
                            url,

                        "items":
                            cobalt,
                    }

                else:

                    raise RuntimeError(
                        "All YouTube extractors and "
                        "Cobalt failed"
                    )

            else:

                PENDING[uid] = {
                    "source":
                        "youtube",

                    "url":
                        url,

                    "info":
                        info,

                    "items":
                        items,

                    "client":
                        used_client,
                }

        # ----------------------------------------------------
        # INSTAGRAM
        # ----------------------------------------------------

        elif is_instagram(url):

            info = None

            instagram_options = {
                "quiet":
                    True,

                "no_warnings":
                    False,

                "noplaylist":
                    True,

                "socket_timeout":
                    DOWNLOAD_TIMEOUT,

                "retries":
                    2,

                "logger":
                    YTDLPLogger(),
            }

            try:

                def extract_instagram():

                    with yt_dlp.YoutubeDL(
                        instagram_options
                    ) as ydl:

                        return ydl.extract_info(
                            url,
                            download=False,
                        )

                info = (
                    await asyncio.to_thread(
                        extract_instagram
                    )
                )

            except Exception as exc:

                logger.warning(
                    "[Instagram] yt-dlp failed: %s",
                    exc,
                )

            if (
                info
                and info.get("formats")
            ):

                items = []

                audio = best_audio(
                    info
                )

                for quality in QUALITY_ORDER:

                    video = best_video(
                        info,
                        quality,
                    )

                    if not video:

                        continue

                    estimated = None

                    if (
                        audio
                        and video.get(
                            "size"
                        ) is not None
                    ):

                        estimated = (
                            video["size"]
                            + audio["size"]
                        )

                    elif video.get(
                        "size"
                    ) is not None:

                        estimated = (
                            video["size"]
                        )

                    items.append(
                        {
                            "quality":
                                str(quality),

                            "size":
                                estimated,

                            "video_format_id":
                                video["format_id"],

                            "audio_format_id":
                                (
                                    audio["format_id"]
                                    if audio
                                    else None
                                ),

                            "width":
                                video.get(
                                    "width"
                                ),

                            "height":
                                video.get(
                                    "height"
                                ),

                            "client":
                                "web",
                        }
                    )

                if items:

                    PENDING[uid] = {
                        "source":
                            "instagram_ytdlp",

                        "url":
                            url,

                        "info":
                            info,

                        "items":
                            items,
                    }

                else:

                    raise RuntimeError(
                        "Instagram yt-dlp returned "
                        "no usable formats"
                    )

            else:

                logger.info(
                    "[Instagram] yt-dlp failed, "
                    "trying Cobalt fallback"
                )

                cobalt = await cobalt_items(
                    url
                )

                if not cobalt:

                    raise RuntimeError(
                        "Instagram yt-dlp and "
                        "Cobalt failed"
                    )

                PENDING[uid] = {
                    "source":
                        "cobalt",

                    "url":
                        url,

                    "items":
                        cobalt,
                }

        # ----------------------------------------------------
        # OTHER SOURCES
        # ----------------------------------------------------

        else:

            cobalt = await cobalt_items(
                url
            )

            if not cobalt:

                raise RuntimeError(
                    "Cobalt failed"
                )

            PENDING[uid] = {
                "source":
                    "cobalt",

                "url":
                    url,

                "items":
                    cobalt,
            }

        await status.edit_text(
            TEXT[language]["quality"],
            reply_markup=quality_keyboard(
                language,
                PENDING[uid]["items"],
            ),
        )

    except Exception:

        logger.exception(
            "Analysis failed for user %s",
            uid,
        )

        await status.edit_text(
            TEXT[language]["analysis_failed"],
            reply_markup=back_keyboard(
                language
            ),
        )


# ============================================================
# QUALITY / DOWNLOAD
# ============================================================

@dp.callback_query(
    F.data.startswith(
        "quality:"
    )
)
async def quality_selected(
    callback: CallbackQuery,
):

    uid = callback.from_user.id

    row = await get_user(
        uid
    )

    language = row_language(
        row
    )

    pending = PENDING.get(
        uid
    )

    if not pending:

        await callback.answer(
            TEXT[language]["no_url"],
            show_alert=True,
        )

        return

    if (
        uid in ACTIVE
        and not is_admin(uid)
    ):

        await callback.answer(
            TEXT[language]["busy"],
            show_alert=True,
        )

        return

    selection = (
        callback.data
        .split(
            ":",
            1
        )[1]
    )

    items = pending[
        "items"
    ]

    if (
        pending["source"]
        in (
            "youtube",
            "instagram_ytdlp",
        )
        and selection == "auto"
    ):

        selected = (
            choose_youtube_auto(
                items
            )
        )

    elif selection == "auto":

        selected = next(
            (
                item

                for quality in (
                    "2160",
                    "1440",
                    "1080",
                    "720",
                    "480",
                    "360",
                )

                for item in items

                if (
                    item["quality"]
                    == quality

                    and (
                        item.get(
                            "size"
                        ) is None

                        or item["size"]
                        <= SAFE_FILE_BYTES
                    )
                )
            ),
            None,
        )

    elif selection == "best":

        selected = max(
            items,
            key=lambda item:
                int(
                    item["quality"]
                ),
        )

    else:

        selected = next(
            (
                item
                for item in items
                if item["quality"]
                == selection
            ),
            None,
        )

    if not selected:

        await callback.message.edit_text(
            TEXT[language]["too_large"],
            reply_markup=back_keyboard(
                language
            ),
        )

        await callback.answer()

        return

    if (
        not is_admin(uid)

        and selected.get(
            "size"
        ) is not None

        and selected["size"]
        > SAFE_FILE_BYTES
    ):

        await callback.message.edit_text(
            TEXT[language][
                "quality_too_large"
            ],
            reply_markup=quality_keyboard(
                language,
                items,
            ),
        )

        await callback.answer()

        return

    ACTIVE.add(
        uid
    )

    await callback.answer()

    status = await callback.message.edit_text(
        TEXT[language]["downloading"]
    )

    temp_dir = tempfile.mkdtemp(
        prefix="vdw_"
    )

    try:

        source = pending[
            "source"
        ]

        # ----------------------------------------------------
        # YOUTUBE / INSTAGRAM via yt-dlp
        # ----------------------------------------------------

        if source in (
            "youtube",
            "instagram_ytdlp",
        ):

            media, info = (
                await asyncio.to_thread(
                    download_youtube_sync,
                    pending["url"],
                    selected,
                    temp_dir,
                )
            )

            title = (
                info.get(
                    "title"
                )
                or "Video"
            )

            duration = info.get(
                "duration"
            )

            width = selected.get(
                "width"
            )

            height = selected.get(
                "height"
            )

        # ----------------------------------------------------
        # COBALT
        # ----------------------------------------------------

        else:

            media = await asyncio.to_thread(
                download_direct_sync,
                selected["url"],
                temp_dir,
                (
                    None
                    if is_admin(uid)
                    else SAFE_FILE_BYTES
                ),
                selected.get(
                    "filename"
                )
                or "video.mp4",
            )

            if media is None:

                await status.edit_text(
                    TEXT[language]["too_large"],
                    reply_markup=back_keyboard(
                        language
                    ),
                )

                return

            title = (
                selected.get(
                    "filename"
                )
                or "Video"
            )

            duration = None
            width = None
            height = None

        if (
            not media
            or not media.exists()
        ):

            raise RuntimeError(
                "Downloaded media missing"
            )

        size_bytes = media.stat().st_size

        if (
            not is_admin(uid)
            and size_bytes
            > SAFE_FILE_BYTES
        ):

            await record_download(
                uid,
                title,
                pending["url"],
                selected["quality"],
                size_bytes,
                "failed",
            )

            await status.edit_text(
                TEXT[language]["too_large"],
                reply_markup=back_keyboard(
                    language
                ),
            )

            return

        # ----------------------------------------------------
        # TELEGRAM
        # ----------------------------------------------------

        await status.edit_text(
            TEXT[language]["sending"]
        )

        caption = (
            f"🎬 <b>{esc(title)}</b>\n"
            f"Quality: "
            f"{selected['quality']}p\n"
            f"Size: "
            f"{fmt_bytes(size_bytes)}"
        )

        logger.info(
            "Sending video: %sx%s, %s bytes",
            width,
            height,
            size_bytes,
        )

        sent = await bot.send_video(
            chat_id=uid,

            video=FSInputFile(
                str(media)
            ),

            duration=(
                int(duration)
                if duration
                else None
            ),

            width=width,

            height=height,

            caption=caption,

            supports_streaming=True,
        )

        file_id = (
            sent.video.file_id
            if sent.video
            else None
        )

        await record_download(
            uid,
            title,
            pending["url"],
            selected["quality"],
            size_bytes,
            "success",
            sent.chat.id,
            sent.message_id,
            file_id,
        )

        await status.edit_text(
            TEXT[language]["done"],
            reply_markup=main_keyboard_for_user(
                language,
                uid,
            ),
        )

    except Exception:

        logger.exception(
            "Download failed for user %s",
            uid,
        )

        try:

            await record_download(
                uid,
                "Video",
                pending.get(
                    "url",
                    "",
                ),
                selected["quality"],
                0,
                "failed",
            )

        except Exception:

            logger.exception(
                "Failed to record download failure"
            )

        await status.edit_text(
            TEXT[language]["failed"],
            reply_markup=back_keyboard(
                language
            ),
        )

    finally:

        shutil.rmtree(
            temp_dir,
            ignore_errors=True,
        )

        PENDING.pop(
            uid,
            None,
        )

        ACTIVE.discard(
            uid
        )


# ============================================================
# HISTORY
# ============================================================

@dp.callback_query(
    F.data == "menu:history"
)
async def history_menu(
    callback: CallbackQuery,
):

    uid = callback.from_user.id

    row = await get_user(
        uid
    )

    language = row_language(
        row
    )

    async with DB_POOL.acquire() as conn:

        rows = await conn.fetch(
            """
            SELECT *
            FROM downloads

            WHERE
                user_hash = $1
                AND status = 'success'

            ORDER BY id DESC

            LIMIT 20
            """,
            user_hash(uid),
        )

    if not rows:

        await callback.message.edit_text(
            TEXT[language]["history_empty"],
            reply_markup=back_keyboard(
                language
            ),
        )

        await callback.answer()

        return

    lines = [
        TEXT[language]["history"],
        "",
    ]

    buttons = []

    for item in rows:

        title = (
            decrypt(
                item[
                    "title_encrypted"
                ]
            )
            or "Video"
        )

        lines.append(
            f"🎬 <b>{esc(title[:70])}</b> — "
            f"{item['quality']}p • "
            f"{fmt_bytes(int(item['size_bytes']))}"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"🗑 {title[:30]}"
                    ),

                    callback_data=(
                        f"history_delete:"
                        f"{item['id']}"
                    ),
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text=TEXT[language][
                    "delete_history"
                ],
                callback_data="history:clear",
            )
        ]
    )

    buttons.append(
        [
            InlineKeyboardButton(
                text=TEXT[language]["back"],
                callback_data="menu:main",
            )
        ]
    )

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
    )

    await callback.answer()


@dp.callback_query(
    F.data.startswith(
        "history_delete:"
    )
)
async def history_delete(
    callback: CallbackQuery,
):

    uid = callback.from_user.id

    try:

        item_id = int(
            callback.data.split(
                ":",
                1
            )[1]
        )

    except (
        ValueError,
        IndexError,
    ):

        await callback.answer(
            "Invalid ID",
            show_alert=True,
        )

        return

    await delete_download(
        uid,
        item_id,
    )

    row = await get_user(
        uid
    )

    language = row_language(
        row
    )

    await callback.answer(
        TEXT[language]["deleted"]
    )

    await history_menu(
        callback
    )


@dp.callback_query(
    F.data == "history:clear"
)
async def history_clear(
    callback: CallbackQuery,
):

    uid = callback.from_user.id

    async with DB_POOL.acquire() as conn:

        rows = await conn.fetch(
            """
            SELECT
                telegram_chat_id,
                telegram_message_id
            FROM downloads

            WHERE
                user_hash = $1
                AND status = 'success'
            """,
            user_hash(uid),
        )

        await conn.execute(
            """
            DELETE FROM downloads
            WHERE user_hash = $1
            """,
            user_hash(uid),
        )

    for row in rows:

        if (
            row["telegram_chat_id"]
            and row["telegram_message_id"]
        ):

            try:

                await bot.delete_message(
                    row["telegram_chat_id"],
                    row["telegram_message_id"],
                )

            except Exception:

                pass

    row = await get_user(
        uid
    )

    language = row_language(
        row
    )

    await callback.message.edit_text(
        TEXT[language]["all_deleted"],
        reply_markup=back_keyboard(
            language
        ),
    )

    await callback.answer()


# ============================================================
# ADMIN
# ============================================================

@dp.callback_query(
    F.data == "menu:admin"
)
async def menu_admin(
    callback: CallbackQuery,
):

    uid = callback.from_user.id

    if not is_admin(uid):

        await callback.answer(
            "Forbidden",
            show_alert=True,
        )

        return

    await callback.message.edit_text(
        "🔐 <b>Админ-панель</b>\n\n"
        "/stats — статистика\n"
        "/users — пользователи\n"
        "/recent — загрузки\n"
        "/block ID — блокировка\n"
        "/unblock ID — разблокировка\n"
        "/broadcast TEXT — рассылка",
        reply_markup=back_keyboard(
            "ru"
        ),
    )

    await callback.answer()


@dp.message(
    Command("admin")
)
async def admin_panel(
    message: Message,
):

    if not is_admin(
        message.from_user.id
    ):

        return

    await message.answer(
        "🔐 <b>Админ-панель</b>\n\n"
        "/stats\n"
        "/users\n"
        "/recent\n"
        "/block ID\n"
        "/unblock ID\n"
        "/broadcast TEXT"
    )


@dp.message(
    Command("stats")
)
async def admin_stats(
    message: Message,
):

    if not is_admin(
        message.from_user.id
    ):

        return

    async with DB_POOL.acquire() as conn:

        users = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM users
            """
        )

        successful = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM downloads
            WHERE status = 'success'
            """
        )

        failed = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM downloads
            WHERE status = 'failed'
            """
        )

        total = await conn.fetchval(
            """
            SELECT COALESCE(
                SUM(size_bytes),
                0
            )
            FROM downloads
            WHERE status = 'success'
            """
        )

    await message.answer(
        "📊 <b>Statistics</b>\n\n"
        f"Users: {users}\n"
        f"Successful: {successful}\n"
        f"Failed: {failed}\n"
        f"Transferred: "
        f"{fmt_bytes(int(total or 0))}"
    )


@dp.message(
    Command("users")
)
async def admin_users(
    message: Message,
):

    if not is_admin(
        message.from_user.id
    ):

        return

    async with DB_POOL.acquire() as conn:

        rows = await conn.fetch(
            """
            SELECT *
            FROM users

            ORDER BY id DESC

            LIMIT 100
            """
        )

    if not rows:

        await message.answer(
            "No users."
        )

        return

    text = (
        "👥 <b>Users</b>\n\n"
    )

    for row in rows:

        uid = (
            decrypt(
                row[
                    "telegram_id_encrypted"
                ]
            )
            or "?"
        )

        username = (
            decrypt(
                row[
                    "username_encrypted"
                ]
            )
            or "-"
        )

        name = (
            decrypt(
                row[
                    "first_name_encrypted"
                ]
            )
            or "-"
        )

        block = (
            f"👤 <b>{esc(name)}</b>\n"
            f"ID: "
            f"<code>{esc(uid)}</code>\n"
            f"@{esc(username)}\n"
            f"Downloads: "
            f"{row['downloads_count']}\n"
            f"Errors: "
            f"{row['failed_count']}\n"
            f"Blocked: "
            f"{row['is_blocked']}\n\n"
        )

        if (
            len(text)
            + len(block)
            > 3500
        ):

            await message.answer(
                text
            )

            text = ""

        text += block

    if text.strip():

        await message.answer(
            text
        )


@dp.message(
    Command("recent")
)
async def admin_recent(
    message: Message,
):

    if not is_admin(
        message.from_user.id
    ):

        return

    async with DB_POOL.acquire() as conn:

        rows = await conn.fetch(
            """
            SELECT *
            FROM downloads

            ORDER BY id DESC

            LIMIT 30
            """
        )

    if not rows:

        await message.answer(
            "No downloads."
        )

        return

    lines = [
        "🧾 <b>Recent downloads</b>",
        "",
    ]

    for row in rows:

        title = (
            decrypt(
                row[
                    "title_encrypted"
                ]
            )
            or "Video"
        )

        lines.append(
            f"🎬 {esc(title[:60])}\n"
            f"{row['quality']}p • "
            f"{fmt_bytes(int(row['size_bytes']))}\n"
            f"{row['status']}\n"
            f"{row['created_at']}\n"
        )

    await message.answer(
        "\n".join(lines)
    )


@dp.message(
    Command("block")
)
async def admin_block(
    message: Message,
):

    if not is_admin(
        message.from_user.id
    ):

        return

    parts = (
        message.text
        or ""
    ).split(
        maxsplit=1
    )

    if len(parts) != 2:

        await message.answer(
            "Usage: /block TELEGRAM_ID"
        )

        return

    try:

        target = int(
            parts[1]
        )

    except ValueError:

        await message.answer(
            "Invalid ID."
        )

        return

    if target == ADMIN_ID:

        await message.answer(
            "Admin cannot be blocked."
        )

        return

    await set_blocked(
        target,
        True,
    )

    await message.answer(
        "⛔ User blocked."
    )


@dp.message(
    Command("unblock")
)
async def admin_unblock(
    message: Message,
):

    if not is_admin(
        message.from_user.id
    ):

        return

    parts = (
        message.text
        or ""
    ).split(
        maxsplit=1
    )

    if len(parts) != 2:

        await message.answer(
            "Usage: /unblock TELEGRAM_ID"
        )

        return

    try:

        target = int(
            parts[1]
        )

    except ValueError:

        await message.answer(
            "Invalid ID."
        )

        return

    await set_blocked(
        target,
        False,
    )

    await message.answer(
        "✅ User unblocked."
    )


@dp.message(
    Command("broadcast")
)
async def admin_broadcast(
    message: Message,
):

    if not is_admin(
        message.from_user.id
    ):

        return

    parts = (
        message.text
        or ""
    ).split(
        maxsplit=1
    )

    if len(parts) != 2:

        await message.answer(
            "Usage: /broadcast TEXT"
        )

        return

    async with DB_POOL.acquire() as conn:

        rows = await conn.fetch(
            """
            SELECT
                telegram_id_encrypted
            FROM users
            WHERE is_blocked = FALSE
            """
        )

    sent = 0
    failed = 0

    for row in rows:

        raw_id = decrypt(
            row[
                "telegram_id_encrypted"
            ]
        )

        if not raw_id:

            continue

        try:

            await bot.send_message(
                int(raw_id),
                parts[1],
            )

            sent += 1

        except Exception:

            failed += 1

        await asyncio.sleep(
            0.05
        )

    await message.answer(
        "📣 <b>Broadcast finished</b>\n\n"
        f"Sent: {sent}\n"
        f"Failed: {failed}"
    )


# ============================================================
# MAIN
# ============================================================

async def main():

    await init_db()

    logger.info(
        "yt-dlp version: %s",
        yt_dlp.version.__version__,
    )

    logger.info(
        "BGUTIL_POT_SERVER: %s",
        BGUTIL_POT_SERVER
        or "NOT SET",
    )

    logger.info(
        "Video Downloader started"
    )

    await bot.delete_webhook(
        drop_pending_updates=True
    )

    try:

        await dp.start_polling(
            bot
        )

    finally:

        if DB_POOL:

            await DB_POOL.close()

        await bot.session.close()


if __name__ == "__main__":

    asyncio.run(
        main()
    )
