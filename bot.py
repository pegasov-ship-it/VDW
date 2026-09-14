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
    ""
).strip()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    ""
).strip()

DATA_ENCRYPTION_KEY = os.getenv(
    "DATA_ENCRYPTION_KEY",
    ""
).strip()

COBALT_API_URL = os.getenv(
    "COBALT_API_URL",
    ""
).strip().rstrip("/")

BGUTIL_POT_SERVER = os.getenv(
    "BGUTIL_POT_SERVER",
    ""
).strip().rstrip("/")

ADMIN_ID = int(
    os.getenv(
        "ADMIN_ID",
        "297496514"
    )
)

MAX_FILE_MB = int(
    os.getenv(
        "MAX_FILE_MB",
        "50"
    )
)

TELEGRAM_MAX_FILE_MB = int(
    os.getenv(
        "TELEGRAM_MAX_FILE_MB",
        "50"
    )
)

MAX_DURATION_SECONDS = int(
    os.getenv(
        "MAX_DURATION_SECONDS",
        "21600"
    )
)

DOWNLOAD_TIMEOUT = int(
    os.getenv(
        "DOWNLOAD_TIMEOUT",
        "600"
    )
)

SAFE_FILE_BYTES = int(
    min(
        MAX_FILE_MB,
        TELEGRAM_MAX_FILE_MB
    )
    * 1024
    * 1024
    * 0.98
)


# ============================================================
# VALIDATION
# ============================================================

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN is not configured"
    )

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not configured"
    )

if not DATA_ENCRYPTION_KEY:
    raise RuntimeError(
        "DATA_ENCRYPTION_KEY is not configured"
    )

if not COBALT_API_URL:
    raise RuntimeError(
        "COBALT_API_URL is not configured"
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
#
# IMPORTANT:
# bot and dp are created BEFORE ALL @dp handlers.
# ============================================================

bot = Bot(
    BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher()


# ============================================================
# GLOBAL STATE
# ============================================================

DB_POOL: Optional[asyncpg.Pool] = None

PENDING: dict[int, dict] = {}

ACTIVE: set[int] = set()

YOUTUBE_RE = re.compile(
    r"(youtube\.com|youtu\.be|youtube-nocookie\.com)",
    re.IGNORECASE
)

URL_RE = re.compile(
    r"^https?://\S+$",
    re.IGNORECASE
)

QUALITIES = (
    2160,
    1440,
    1080,
    720,
    480,
    360,
)


# ============================================================
# TRANSLATIONS
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
            "Сервис не предназначен для нарушения "
            "авторских или иных прав третьих лиц.\n\n"
            "Временные файлы удаляются после обработки.",

        "privacy":
            "🔐 <b>Конфиденциальность</b>\n\n"
            "Мы храним минимально необходимую информацию: "
            "Telegram ID, имя/username, язык и техническую "
            "историю загрузок.\n\n"
            "Видео не хранятся постоянно на сервере. "
            "Временные копии удаляются после обработки.\n\n"
            "Удалить свои данные можно в Настройках.",

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
            "Временный файл на сервере удалён.",

        "too_large":
            "❌ <b>Видео невозможно скачать</b>\n\n"
            "Даже самое низкое доступное качество "
            "превышает текущий лимит 50 МБ.\n\n"
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
            "The service is not intended for copyright "
            "infringement or violation of third-party rights.\n\n"
            "Temporary files are deleted after processing.",

        "privacy":
            "🔐 <b>Privacy</b>\n\n"
            "We store only the minimum information required: "
            "Telegram ID, name/username, language and technical "
            "download history.\n\n"
            "Videos are not permanently stored on the server. "
            "Temporary copies are deleted after processing.\n\n"
            "You can delete your data from Settings.",

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
            "The temporary server file has been deleted.",

        "too_large":
            "❌ <b>This video cannot be downloaded</b>\n\n"
            "Even the lowest available quality exceeds "
            "the current 50 MB limit.\n\n"
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

        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_downloads_created
            ON downloads(created_at DESC)
            """
        )

    logger.info(
        "PostgreSQL initialized"
    )


async def get_user(
    user_id: int
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

    stamp = datetime.now(
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
            stamp,
        )


async def set_language(
    user_id: int,
    language: str
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
    user_id: int
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
    user_id: int
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
    value: bool
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

                SET
                    failed_count =
                        failed_count + 1

                WHERE user_hash = $1
                """,
                key,
            )


# ============================================================
# CRYPTO / HELPERS
# ============================================================

def encrypt(
    value: Optional[str]
) -> Optional[str]:

    if value is None:

        return None

    return FERNET.encrypt(
        value.encode(
            "utf-8"
        )
    ).decode(
        "utf-8"
    )


def decrypt(
    value: Optional[str]
) -> Optional[str]:

    if not value:

        return None

    try:

        return FERNET.decrypt(
            value.encode(
                "utf-8"
            )
        ).decode(
            "utf-8"
        )

    except InvalidToken:

        return None


def user_hash(
    user_id: int
) -> str:

    return hashlib.sha256(
        f"telegram:{user_id}".encode(
            "utf-8"
        )
    ).hexdigest()


def esc(
    value: Optional[str]
) -> str:

    return html.escape(
        value or ""
    )


def fmt_bytes(
    size: int
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


def is_admin(
    user_id: int
) -> bool:

    return (
        user_id == ADMIN_ID
    )


def is_youtube(
    url: str
) -> bool:

    return bool(
        YOUTUBE_RE.search(
            url
        )
    )


def row_language(
    row
) -> str:

    if (
        row
        and row["language"] in TEXT
    ):

        return row["language"]

    return "ru"


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
    language: str
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


def main_keyboard(
    language: str
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
    language: str
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
    language: str
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
    items: list[dict]
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

    auto_quality = None

    for quality in QUALITIES:

        if any(
            item["quality"]
            == str(quality)

            for item in usable
        ):

            auto_quality = str(
                quality
            )

            break

    if auto_quality:

        auto_item = next(
            item
            for item in items
            if item["quality"]
            == auto_quality
        )

        label = (
            f"{TEXT[language]['auto']}"
            f" — {auto_quality}p"
        )

        if auto_item.get("size"):

            label += (
                f" ~"
                f"{fmt_bytes(auto_item['size'])}"
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
                f" — "
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
        inline_keyboard=rows
    )


# ============================================================
# YOUTUBE / YT-DLP
# ============================================================

def ytdlp_base_options() -> dict:

    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout":
            DOWNLOAD_TIMEOUT,
        "retries": 2,
    }

    if BGUTIL_POT_SERVER:

        options["extractor_args"] = {
            "youtubepot-bgutilhttp": {
                "base_url": [
                    BGUTIL_POT_SERVER
                ]
            }
        }

    return options


def ytdlp_info_sync(
    url: str
) -> dict:

    with yt_dlp.YoutubeDL(
        ytdlp_base_options()
    ) as ydl:

        return ydl.extract_info(
            url,
            download=False,
        )


def format_size(
    fmt: dict
) -> Optional[int]:

    value = (
        fmt.get("filesize")
        or fmt.get("filesize_approx")
    )

    if value is None:

        return None

    try:

        return int(
            value
        )

    except (
        TypeError,
        ValueError,
    ):

        return None


def video_formats(
    info: dict
) -> list[dict]:

    result = []

    for fmt in (
        info.get("formats")
        or []
    ):

        height = fmt.get(
            "height"
        )

        vcodec = fmt.get(
            "vcodec"
        )

        if (
            not height
            or not vcodec
            or vcodec == "none"
        ):

            continue

        try:

            height = int(
                height
            )

        except (
            TypeError,
            ValueError,
        ):

            continue

        result.append(
            {
                "format_id":
                    fmt.get("format_id"),

                "height":
                    height,

                "size":
                    format_size(fmt),

                "ext":
                    fmt.get("ext"),

                "vcodec":
                    vcodec,

                "acodec":
                    fmt.get("acodec"),
            }
        )

    return result


def best_video_format(
    info: dict,
    max_height: int
) -> Optional[dict]:

    candidates = []

    for fmt in video_formats(
        info
    ):

        if fmt["height"] > max_height:

            continue

        if fmt["size"] is None:

            continue

        score = (
            fmt["height"],
            1 if fmt["ext"] == "mp4" else 0,
            fmt["size"],
        )

        candidates.append(
            (
                score,
                fmt
            )
        )

    if not candidates:

        return None

    candidates.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    return candidates[0][1]


def best_audio_format(
    info: dict
) -> Optional[dict]:

    candidates = []

    for fmt in (
        info.get("formats")
        or []
    ):

        acodec = fmt.get(
            "acodec"
        )

        vcodec = fmt.get(
            "vcodec"
        )

        if (
            not acodec
            or acodec == "none"
        ):

            continue

        if (
            vcodec
            and vcodec != "none"
        ):

            continue

        size = format_size(
            fmt
        )

        if size is None:

            continue

        abr = float(
            fmt.get("abr")
            or 0
        )

        candidates.append(
            (
                abr,
                size,
                fmt,
            )
        )

    if not candidates:

        return None

    candidates.sort(
        key=lambda item: (
            item[0],
            item[1],
        ),
        reverse=True,
    )

    return candidates[0][2]


def estimate_youtube_size(
    info: dict,
    height: int
) -> Optional[int]:

    video = best_video_format(
        info,
        height
    )

    if not video:

        return None

    total = (
        video["size"]
        or 0
    )

    audio = best_audio_format(
        info
    )

    if audio:

        total += (
            format_size(audio)
            or 0
        )

    return total


def youtube_quality_items(
    info: dict
) -> list[dict]:

    formats = video_formats(
        info
    )

    if not formats:

        return []

    max_height = max(
        fmt["height"]
        for fmt in formats
    )

    items = []

    for quality in QUALITIES:

        if max_height < quality:

            continue

        if not best_video_format(
            info,
            quality,
        ):

            continue

        items.append(
            {
                "quality":
                    str(quality),

                "size":
                    estimate_youtube_size(
                        info,
                        quality,
                    ),
            }
        )

    return items


def choose_youtube_auto(
    info: dict
) -> Optional[int]:

    for quality in QUALITIES:

        if not best_video_format(
            info,
            quality,
        ):

            continue

        size = estimate_youtube_size(
            info,
            quality,
        )

        if (
            size is None
            or size <= SAFE_FILE_BYTES
        ):

            return quality

    return None


def youtube_format_selector(
    quality: int
) -> str:

    return (
        f"bv*[height<={quality}]"
        "[ext=mp4]"
        "+ba[ext=m4a]/"
        f"bv*[height<={quality}]"
        "+ba/"
        f"b[height<={quality}]"
    )


def download_youtube_sync(
    url: str,
    quality: int,
    directory: str,
):

    output = str(
        Path(directory)
        / "%(title).80s_%(id)s.%(ext)s"
    )

    options = (
        ytdlp_base_options()
    )

    options.update(
        {
            "format":
                youtube_format_selector(
                    quality
                ),

            "outtmpl":
                output,

            "merge_output_format":
                "mp4",
        }
    )

    with yt_dlp.YoutubeDL(
        options
    ) as ydl:

        info = ydl.extract_info(
            url,
            download=True,
        )

    files = [
        path
        for path
        in Path(directory).iterdir()
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

    return (
        media,
        info,
    )


# ============================================================
# COBALT
# ============================================================

def cobalt_request_sync(
    url: str,
    quality: str
) -> dict:

    payload = {
        "url":
            url,

        "videoQuality":
            quality,

        "downloadMode":
            "auto",

        "filenameStyle":
            "pretty",

        "videoCodecs":
            [
                "h264"
            ],
    }

    import json

    request = Request(
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
            request,
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
            f"{body[:400]}"
        ) from exc

    except URLError as exc:

        raise RuntimeError(
            f"Cobalt connection error: "
            f"{exc}"
        ) from exc


def cobalt_media(
    response: dict
) -> dict:

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

    if status == "error":

        error = (
            response.get(
                "error"
            )
            or {}
        )

        raise RuntimeError(
            "Cobalt error: "
            + str(
                error.get(
                    "code"
                )
                or "unknown"
            )
        )

    raise RuntimeError(
        "Unsupported Cobalt status: "
        + str(status)
    )


async def cobalt_items(
    url: str
) -> list[dict]:

    results = []

    for quality in (
        "1080",
        "720",
        "480",
        "360",
    ):

        try:

            response = (
                await asyncio.to_thread(
                    cobalt_request_sync,
                    url,
                    quality,
                )
            )

            media = cobalt_media(
                response
            )

            results.append(
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

            logger.info(
                "Cobalt %sp failed: %s",
                quality,
                exc,
            )

    return results


# ============================================================
# DIRECT MEDIA DOWNLOAD
# ============================================================

def stream_download_sync(
    url: str,
    directory: str,
    max_bytes: Optional[int],
    filename: str,
):

    safe_name = re.sub(
        r"[^\w. -]+",
        "_",
        filename,
    ).strip()

    if not safe_name:

        safe_name = "video.mp4"

    safe_name = Path(
        safe_name
    ).name

    path = (
        Path(directory)
        / safe_name
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
        method="GET",
    )

    total = 0

    try:

        with urlopen(
            request,
            timeout=DOWNLOAD_TIMEOUT,
        ) as response:

            with open(
                path,
                "wb"
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

                        return (
                            None,
                            total,
                        )

                    output.write(
                        chunk
                    )

        return (
            path,
            total,
        )

    except Exception as exc:

        path.unlink(
            missing_ok=True
        )

        raise RuntimeError(
            f"Media download failed: "
            f"{exc}"
        ) from exc


# ============================================================
# START
# ============================================================

@dp.message(
    CommandStart()
)
async def start(
    message: Message
):

    logger.info(
        "START received from user %s",
        message.from_user.id,
    )

    user = message.from_user

    await ensure_user(
        user.id,
        user.username,
        user.first_name,
    )

    row = await get_user(
        user.id
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
        reply_markup=main_keyboard(
            language
        ),
    )


# ============================================================
# LANGUAGE
# ============================================================

@dp.callback_query(
    F.data.startswith("lang:")
)
async def language_select(
    callback: CallbackQuery
):

    language = (
        callback.data
        .split(
            ":",
            1
        )[1]
    )

    if language not in (
        "ru",
        "en",
    ):

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
    callback: CallbackQuery
):

    await accept_terms(
        callback.from_user.id
    )

    row = await get_user(
        callback.from_user.id
    )

    language = row_language(
        row
    )

    await callback.message.edit_text(
        TEXT[language]["ready"],
        reply_markup=main_keyboard(
            language
        ),
    )

    await callback.answer()


# ============================================================
# TERMS / PRIVACY
# ============================================================

@dp.callback_query(
    F.data == "info:terms"
)
async def info_terms(
    callback: CallbackQuery
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
    callback: CallbackQuery
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
    message: Message
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
    message: Message
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
# MAIN MENU
# ============================================================

@dp.callback_query(
    F.data == "menu:main"
)
async def menu_main(
    callback: CallbackQuery
):

    row = await get_user(
        callback.from_user.id
    )

    language = row_language(
        row
    )

    await callback.message.edit_text(
        TEXT[language]["menu"],
        reply_markup=main_keyboard(
            language
        ),
    )

    await callback.answer()


@dp.callback_query(
    F.data == "menu:download"
)
async def menu_download(
    callback: CallbackQuery
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


# ============================================================
# SETTINGS
# ============================================================

@dp.callback_query(
    F.data == "menu:settings"
)
async def menu_settings(
    callback: CallbackQuery
):

    row = await get_user(
        callback.from_user.id
    )

    language = row_language(
        row
    )

    await callback.message.edit_text(
        TEXT[language]["settings"],
        reply_markup=settings_keyboard(
            language
        ),
    )

    await callback.answer()


@dp.message(
    Command("settings")
)
async def settings_command(
    message: Message
):

    row = await get_user(
        message.from_user.id
    )

    language = row_language(
        row
    )

    await message.answer(
        TEXT[language]["settings"],
        reply_markup=settings_keyboard(
            language
        ),
    )


@dp.callback_query(
    F.data.startswith(
        "settings_lang:"
    )
)
async def settings_language(
    callback: CallbackQuery
):

    language = (
        callback.data
        .split(
            ":",
            1
        )[1]
    )

    if language not in (
        "ru",
        "en",
    ):

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
        TEXT[language]["settings"],
        reply_markup=settings_keyboard(
            language
        ),
    )

    await callback.answer(
        TEXT[language]["language_changed"]
    )


@dp.callback_query(
    F.data == "account:delete"
)
async def account_delete(
    callback: CallbackQuery
):

    user_id = (
        callback.from_user.id
    )

    row = await get_user(
        user_id
    )

    language = row_language(
        row
    )

    await delete_user_data(
        user_id
    )

    PENDING.pop(
        user_id,
        None
    )

    ACTIVE.discard(
        user_id
    )

    await callback.message.edit_text(
        TEXT[language]["deleted_account"]
    )

    await callback.answer()


@dp.message(
    Command("delete_me")
)
async def delete_me(
    message: Message
):

    user_id = (
        message.from_user.id
    )

    row = await get_user(
        user_id
    )

    language = row_language(
        row
    )

    await delete_user_data(
        user_id
    )

    PENDING.pop(
        user_id,
        None
    )

    ACTIVE.discard(
        user_id
    )

    await message.answer(
        TEXT[language]["deleted_account"]
    )


# ============================================================
# TEXT / URL ROUTER
#
# IMPORTANT:
# This intentionally uses F.text instead of a bare @dp.message.
# ============================================================

@dp.message(
    F.text
)
async def text_router(
    message: Message
):

    logger.info(
        "TEXT received from user %s: %s",
        message.from_user.id,
        (
            message.text
            or ""
        )[:300],
    )

    user = message.from_user

    await ensure_user(
        user.id,
        user.username,
        user.first_name,
    )

    row = await get_user(
        user.id
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
        user.id in ACTIVE
        and not is_admin(
            user.id
        )
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
        # YOUTUBE -> YT-DLP
        # ----------------------------------------------------

        if is_youtube(
            url
        ):

            logger.info(
                "Using yt-dlp for YouTube URL"
            )

            info = await asyncio.to_thread(
                ytdlp_info_sync,
                url,
            )

            items = youtube_quality_items(
                info
            )

            if not items:

                raise RuntimeError(
                    "No usable YouTube formats"
                )

            PENDING[
                user.id
            ] = {
                "source":
                    "youtube",

                "url":
                    url,

                "info":
                    info,

                "items":
                    items,
            }

        # ----------------------------------------------------
        # OTHER SOURCES -> COBALT
        # ----------------------------------------------------

        else:

            logger.info(
                "Using Cobalt for non-YouTube URL"
            )

            items = await cobalt_items(
                url
            )

            if not items:

                raise RuntimeError(
                    "No usable Cobalt formats"
                )

            PENDING[
                user.id
            ] = {
                "source":
                    "cobalt",

                "url":
                    url,

                "items":
                    items,
            }

        await status.edit_text(
            TEXT[language]["quality"],
            reply_markup=quality_keyboard(
                language,
                PENDING[
                    user.id
                ]["items"],
            ),
        )

    except Exception:

        logger.exception(
            "Analysis failed for user %s",
            user.id,
        )

        await status.edit_text(
            TEXT[language]["analysis_failed"],
            reply_markup=back_keyboard(
                language
            ),
        )


# ============================================================
# QUALITY
# ============================================================

@dp.callback_query(
    F.data.startswith(
        "quality:"
    )
)
async def quality_selected(
    callback: CallbackQuery
):

    user_id = (
        callback.from_user.id
    )

    row = await get_user(
        user_id
    )

    language = row_language(
        row
    )

    pending = PENDING.get(
        user_id
    )

    if not pending:

        await callback.answer(
            TEXT[language]["no_url"],
            show_alert=True,
        )

        return

    if (
        user_id in ACTIVE
        and not is_admin(
            user_id
        )
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

    selected = None

    # --------------------------------------------------------
    # AUTO - YOUTUBE
    # --------------------------------------------------------

    if (
        pending["source"]
        == "youtube"
        and selection == "auto"
    ):

        quality = (
            choose_youtube_auto(
                pending["info"]
            )
        )

        if quality:

            selected = {
                "quality":
                    str(quality),

                "size":
                    estimate_youtube_size(
                        pending["info"],
                        quality,
                    ),
            }

    # --------------------------------------------------------
    # AUTO - COBALT
    # --------------------------------------------------------

    elif selection == "auto":

        for quality in (
            "2160",
            "1440",
            "1080",
            "720",
            "480",
            "360",
        ):

            candidate = next(
                (
                    item
                    for item in items
                    if (
                        item["quality"]
                        == quality

                        and (
                            item.get("size")
                            is None

                            or item["size"]
                            <= SAFE_FILE_BYTES
                        )
                    )
                ),
                None,
            )

            if candidate:

                selected = candidate

                break

    # --------------------------------------------------------
    # BEST
    # --------------------------------------------------------

    elif selection == "best":

        selected = max(
            items,
            key=lambda item:
                int(
                    item["quality"]
                )
        )

    # --------------------------------------------------------
    # EXPLICIT QUALITY
    # --------------------------------------------------------

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

        await callback.answer(
            TEXT[language]["failed"],
            show_alert=True,
        )

        return

    known_size = (
        selected.get(
            "size"
        )
    )

    if (
        not is_admin(user_id)
        and known_size is not None
        and known_size > SAFE_FILE_BYTES
    ):

        if selected["quality"] == "360":

            await callback.message.edit_text(
                TEXT[language]["too_large"],
                reply_markup=back_keyboard(
                    language
                ),
            )

        else:

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

    if not is_admin(
        user_id
    ):

        ACTIVE.add(
            user_id
        )

    await callback.answer()

    status = await callback.message.edit_text(
        TEXT[language]["downloading"]
    )

    temp_dir = tempfile.mkdtemp(
        prefix="vdw_"
    )

    try:

        # ----------------------------------------------------
        # YOUTUBE DOWNLOAD
        # ----------------------------------------------------

        if (
            pending["source"]
            == "youtube"
        ):

            quality = int(
                selected["quality"]
            )

            media, info = (
                await asyncio.to_thread(
                    download_youtube_sync,
                    pending["url"],
                    quality,
                    temp_dir,
                )
            )

            title = (
                info.get(
                    "title"
                )
                or "Video"
            )

            duration = (
                info.get(
                    "duration"
                )
            )

        # ----------------------------------------------------
        # COBALT DOWNLOAD
        # ----------------------------------------------------

        else:

            filename = (
                selected.get(
                    "filename"
                )
                or "video.mp4"
            )

            media, downloaded_size = (
                await asyncio.to_thread(
                    stream_download_sync,
                    selected["url"],
                    temp_dir,
                    (
                        None
                        if is_admin(user_id)
                        else SAFE_FILE_BYTES
                    ),
                    filename,
                )
            )

            title = filename
            duration = None

        # ----------------------------------------------------
        # TOO LARGE DURING DOWNLOAD
        # ----------------------------------------------------

        if media is None:

            await record_download(
                user_id,
                title,
                pending["url"],
                selected["quality"],
                0,
                "failed",
            )

            if selected["quality"] == "360":

                await status.edit_text(
                    TEXT[language]["too_large"],
                    reply_markup=back_keyboard(
                        language
                    ),
                )

            else:

                await status.edit_text(
                    TEXT[language][
                        "quality_too_large"
                    ],
                    reply_markup=quality_keyboard(
                        language,
                        items,
                    ),
                )

            return

        size_bytes = media.stat().st_size

        # ----------------------------------------------------
        # DURATION LIMIT
        # ----------------------------------------------------

        if (
            not is_admin(user_id)
            and duration
            and int(duration)
                > MAX_DURATION_SECONDS
        ):

            await record_download(
                user_id,
                title,
                pending["url"],
                selected["quality"],
                size_bytes,
                "failed",
            )

            message_text = (
                "❌ Видео слишком длинное."
                if language == "ru"
                else
                "❌ The video is too long."
            )

            await status.edit_text(
                message_text,
                reply_markup=back_keyboard(
                    language
                ),
            )

            return

        # ----------------------------------------------------
        # FINAL SIZE LIMIT
        # ----------------------------------------------------

        if (
            not is_admin(user_id)
            and size_bytes > SAFE_FILE_BYTES
        ):

            await record_download(
                user_id,
                title,
                pending["url"],
                selected["quality"],
                size_bytes,
                "failed",
            )

            if selected["quality"] == "360":

                await status.edit_text(
                    TEXT[language]["too_large"],
                    reply_markup=back_keyboard(
                        language
                    ),
                )

            else:

                await status.edit_text(
                    TEXT[language][
                        "quality_too_large"
                    ],
                    reply_markup=quality_keyboard(
                        language,
                        items,
                    ),
                )

            return

        # ----------------------------------------------------
        # SEND
        # ----------------------------------------------------

        await status.edit_text(
            TEXT[language]["sending"]
        )

        filename = Path(
            media.name
        ).name

        await bot.send_document(
            chat_id=user_id,
            document=FSInputFile(
                str(media),
                filename=filename,
            ),
            caption=(
                f"🎬 <b>{esc(title)}</b>\n"
                f"Quality: "
                f"{selected['quality']}p\n"
                f"Size: "
                f"{fmt_bytes(size_bytes)}"
            ),
        )

        # ----------------------------------------------------
        # HISTORY
        # ----------------------------------------------------

        await record_download(
            user_id,
            title,
            pending["url"],
            selected["quality"],
            size_bytes,
            "success",
        )

        await status.edit_text(
            TEXT[language]["done"],
            reply_markup=main_keyboard(
                language
            ),
        )

    except Exception:

        logger.exception(
            "Download failed for user %s",
            user_id,
        )

        try:

            await record_download(
                user_id,
                pending.get(
                    "url"
                )
                or "Video",
                pending.get(
                    "url"
                )
                or "",
                selected["quality"],
                0,
                "failed",
            )

        except Exception:

            logger.exception(
                "Failed to record download error"
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
            user_id,
            None,
        )

        ACTIVE.discard(
            user_id
        )


# ============================================================
# HISTORY
# ============================================================

@dp.callback_query(
    F.data == "menu:history"
)
async def show_history(
    callback: CallbackQuery
):

    user_id = (
        callback.from_user.id
    )

    row = await get_user(
        user_id
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
            user_hash(
                user_id
            ),
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
                item["title_encrypted"]
            )
            or "Video"
        )

        lines.append(
            f"🎬 <b>{esc(title[:70])}</b>\n"
            f"{esc(str(item['quality']))}p"
            f" • "
            f"{fmt_bytes(int(item['size_bytes']))}\n"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"🗑 {title[:30]}",
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
async def delete_history_item(
    callback: CallbackQuery
):

    user_id = (
        callback.from_user.id
    )

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

    async with DB_POOL.acquire() as conn:

        await conn.execute(
            """
            DELETE FROM downloads

            WHERE
                id = $1
                AND user_hash = $2
            """,
            item_id,
            user_hash(
                user_id
            ),
        )

    row = await get_user(
        user_id
    )

    language = row_language(
        row
    )

    await callback.answer(
        TEXT[language]["deleted"]
    )

    await show_history(
        callback
    )


@dp.callback_query(
    F.data == "history:clear"
)
async def clear_history(
    callback: CallbackQuery
):

    user_id = (
        callback.from_user.id
    )

    async with DB_POOL.acquire() as conn:

        await conn.execute(
            """
            DELETE FROM downloads
            WHERE user_hash = $1
            """,
            user_hash(
                user_id
            ),
        )

    row = await get_user(
        user_id
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

@dp.message(
    Command("admin")
)
async def admin_panel(
    message: Message
):

    if not is_admin(
        message.from_user.id
    ):

        return

    await message.answer(
        "🛠 <b>ADMIN PANEL</b>\n\n"
        "/stats — statistics\n"
        "/users — users\n"
        "/user TELEGRAM_ID — user card\n"
        "/recent — recent downloads\n"
        "/block TELEGRAM_ID — block user\n"
        "/unblock TELEGRAM_ID — unblock user\n"
        "/broadcast TEXT — broadcast\n\n"
        "Application-level download restrictions "
        "are disabled for the admin.\n\n"
        "Telegram and infrastructure limits "
        "still apply."
    )


@dp.message(
    Command("stats")
)
async def admin_stats(
    message: Message
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
    message: Message
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

        telegram_id = (
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

        first_name = (
            decrypt(
                row[
                    "first_name_encrypted"
                ]
            )
            or "-"
        )

        block = (
            f"👤 <b>{esc(first_name)}</b>\n"
            f"ID: "
            f"<code>{esc(telegram_id)}</code>\n"
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
    Command("user")
)
async def admin_user(
    message: Message
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
            "Usage: /user TELEGRAM_ID"
        )

        return

    try:

        target_id = int(
            parts[1]
        )

    except ValueError:

        await message.answer(
            "Invalid ID."
        )

        return

    row = await get_user(
        target_id
    )

    if not row:

        await message.answer(
            "User not found."
        )

        return

    username = (
        decrypt(
            row[
                "username_encrypted"
            ]
        )
        or "-"
    )

    first_name = (
        decrypt(
            row[
                "first_name_encrypted"
            ]
        )
        or "-"
    )

    await message.answer(
        "👤 <b>User</b>\n\n"
        f"ID: "
        f"<code>{target_id}</code>\n"
        f"Name: "
        f"{esc(first_name)}\n"
        f"Username: "
        f"@{esc(username)}\n"
        f"Language: "
        f"{row['language']}\n"
        f"Downloads: "
        f"{row['downloads_count']}\n"
        f"Errors: "
        f"{row['failed_count']}\n"
        f"Transferred: "
        f"{fmt_bytes(int(row['total_bytes']))}\n"
        f"Blocked: "
        f"{row['is_blocked']}\n"
        f"Created: "
        f"{row['created_at']}\n"
        f"Last activity: "
        f"{row['last_activity']}"
    )


@dp.message(
    Command("recent")
)
async def admin_recent(
    message: Message
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
            f"Quality: "
            f"{esc(str(row['quality']))}p\n"
            f"Size: "
            f"{fmt_bytes(int(row['size_bytes']))}\n"
            f"Status: "
            f"{esc(row['status'])}\n"
            f"Date: "
            f"{row['created_at']}\n"
        )

    await message.answer(
        "\n".join(lines)
    )


@dp.message(
    Command("block")
)
async def admin_block(
    message: Message
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

        target_id = int(
            parts[1]
        )

    except ValueError:

        await message.answer(
            "Invalid ID."
        )

        return

    if target_id == ADMIN_ID:

        await message.answer(
            "Admin cannot be blocked."
        )

        return

    await set_blocked(
        target_id,
        True,
    )

    await message.answer(
        "⛔ User blocked."
    )


@dp.message(
    Command("unblock")
)
async def admin_unblock(
    message: Message
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

        target_id = int(
            parts[1]
        )

    except ValueError:

        await message.answer(
            "Invalid ID."
        )

        return

    await set_blocked(
        target_id,
        False,
    )

    await message.answer(
        "✅ User unblocked."
    )


@dp.message(
    Command("broadcast")
)
async def admin_broadcast(
    message: Message
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
        "Video Downloader started"
    )

    logger.info(
        "YouTube provider: %s",
        BGUTIL_POT_SERVER
        or "not configured"
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
