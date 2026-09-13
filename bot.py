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

import asyncpg
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

ADMIN_ID = int(
    os.getenv("ADMIN_ID", "297496514")
)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    ""
).strip()

DATA_ENCRYPTION_KEY = os.getenv(
    "DATA_ENCRYPTION_KEY",
    ""
).strip()


# ------------------------------------------------------------
# Application limits for ordinary users.
# Admin bypasses these application-level limits.
# ------------------------------------------------------------

APP_MAX_FILE_MB = int(
    os.getenv("MAX_FILE_MB", "50")
)

APP_MAX_DURATION_SECONDS = int(
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


# ------------------------------------------------------------
# Telegram standard Bot API limit.
#
# Later, when Local Bot API Server is connected,
# this can be changed to 2000.
# ------------------------------------------------------------

TELEGRAM_MAX_FILE_MB = int(
    os.getenv(
        "TELEGRAM_MAX_FILE_MB",
        "50"
    )
)


# Leave a tiny safety margin.
SAFE_FILE_BYTES = int(
    min(
        APP_MAX_FILE_MB,
        TELEGRAM_MAX_FILE_MB
    )
    * 1024
    * 1024
    * 0.98
)


# ============================================================
# ENVIRONMENT VALIDATION
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


try:
    FERNET = Fernet(
        DATA_ENCRYPTION_KEY.encode()
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
    "video_downloader"
)


# ============================================================
# GLOBAL DATABASE POOL
# ============================================================

db_pool: Optional[asyncpg.Pool] = None


# ============================================================
# RUNTIME STATE
# ============================================================

# These dictionaries contain only temporary process state.
# They do not persist user files or URLs permanently.

pending_urls: dict[int, str] = {}

pending_info: dict[int, dict] = {}

active_downloads: set[int] = set()


URL_RE = re.compile(
    r"^https?://\S+$",
    re.IGNORECASE
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
            "Используя бота, вы подтверждаете, что имеете "
            "необходимые права или разрешение на загрузку "
            "и использование отправленного контента.\n\n"
            "Сервис не предназначен для нарушения авторских "
            "прав или иных прав третьих лиц.\n\n"
            "Временный файл видео удаляется с сервера "
            "после завершения обработки.",

        "privacy":
            "🔐 <b>Конфиденциальность</b>\n\n"
            "Мы храним минимально необходимые данные: "
            "Telegram ID, имя/username, язык и техническую "
            "историю загрузок.\n\n"
            "Сами видео не хранятся постоянно на сервере. "
            "Временный файл удаляется после обработки.\n\n"
            "Удалить сохранённые данные можно в Настройках.",

        "ready":
            "✅ <b>Готово!</b>\n\n"
            "Отправьте публичную ссылку на видео.",

        "menu":
            "Главное меню:",

        "send_url":
            "🔗 Отправьте публичную ссылку на видео.",

        "analyzing":
            "🔎 Анализирую доступные качества и размер видео…",

        "quality":
            "🎬 <b>Выберите качество</b>\n\n"
            "Размер указан приблизительно, если его "
            "можно определить заранее.",

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

        "download_failed":
            "❌ Не удалось обработать это видео.\n\n"
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

        "analyse_failed":
            "❌ Не удалось получить информацию о видео.",

        "auto":
            "🎯 Авто",

        "best":
            "⭐ Максимум",

        "download_button":
            "➕ Скачать видео",

        "history_button":
            "📁 Загруженные видео",

        "settings_button":
            "⚙️ Настройки",

        "delete_data":
            "🗑 Удалить мои данные",

        "delete_history":
            "🗑 Удалить всю историю",
    },


    "en": {

        "welcome":
            "👋 <b>Welcome to Video Downloader</b>\n\n"
            "Choose your language:",

        "terms":
            "📄 <b>Terms of Use</b>\n\n"
            "By using the bot, you confirm that you have "
            "the necessary rights or permission to download "
            "and use the submitted content.\n\n"
            "The service is not intended for copyright "
            "infringement or violation of third-party rights.\n\n"
            "Temporary video files are deleted from the server "
            "after processing.",

        "privacy":
            "🔐 <b>Privacy</b>\n\n"
            "We store only minimal information required to "
            "operate: Telegram ID, name/username, language "
            "and technical download history.\n\n"
            "Video files are not permanently stored on the "
            "server. Temporary files are deleted after processing.\n\n"
            "You can delete stored data from Settings.",

        "ready":
            "✅ <b>Ready!</b>\n\n"
            "Send a public video URL.",

        "menu":
            "Main menu:",

        "send_url":
            "🔗 Send a public video URL.",

        "analyzing":
            "🔎 Analyzing available qualities and video size…",

        "quality":
            "🎬 <b>Choose quality</b>\n\n"
            "The size is approximate when it can be "
            "determined in advance.",

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

        "download_failed":
            "❌ Could not process this video.\n\n"
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

        "analyse_failed":
            "❌ Could not get information about this video.",

        "auto":
            "🎯 Auto",

        "best":
            "⭐ Best",

        "download_button":
            "➕ Download video",

        "history_button":
            "📁 Downloaded videos",

        "settings_button":
            "⚙️ Settings",

        "delete_data":
            "🗑 Delete my data",

        "delete_history":
            "🗑 Delete all history",
    },
}


# ============================================================
# GENERAL HELPERS
# ============================================================

def tr(
    language: str,
    key: str
) -> str:

    dictionary = TEXT.get(
        language,
        TEXT["ru"]
    )

    return dictionary.get(
        key,
        TEXT["ru"][key]
    )


def esc(
    value: Optional[str]
) -> str:

    return html.escape(
        value or ""
    )


def format_bytes(
    size: int
) -> str:

    if size < 1024 * 1024:

        return (
            f"{size / 1024:.1f} KB"
        )

    if size < 1024 * 1024 * 1024:

        return (
            f"{size / 1024**2:.1f} MB"
        )

    return (
        f"{size / 1024**3:.2f} GB"
    )


def now() -> datetime:

    return datetime.now(
        timezone.utc
    )


def user_hash(
    telegram_id: int
) -> str:

    return hashlib.sha256(
        f"telegram:{telegram_id}".encode(
            "utf-8"
        )
    ).hexdigest()


def encrypt(
    value: Optional[str]
) -> Optional[str]:

    if value is None:
        return None

    return FERNET.encrypt(
        value.encode("utf-8")
    ).decode("utf-8")


def decrypt(
    value: Optional[str]
) -> Optional[str]:

    if not value:
        return None

    try:

        return FERNET.decrypt(
            value.encode("utf-8")
        ).decode("utf-8")

    except InvalidToken:

        return None


def is_admin(
    telegram_id: int
) -> bool:

    return telegram_id == ADMIN_ID


# ============================================================
# DATABASE
# ============================================================

async def init_db() -> None:

    global db_pool

    db_pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=5,
        command_timeout=60,
    )

    async with db_pool.acquire() as conn:

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
            CREATE INDEX IF NOT EXISTS idx_downloads_user_hash
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


async def close_db() -> None:

    global db_pool

    if db_pool:

        await db_pool.close()

        db_pool = None


async def get_user(
    telegram_id: int
):

    async with db_pool.acquire() as conn:

        return await conn.fetchrow(
            """
            SELECT *
            FROM users
            WHERE user_hash = $1
            """,
            user_hash(
                telegram_id
            ),
        )


async def ensure_user(
    telegram_id: int,
    username: Optional[str],
    first_name: Optional[str],
) -> None:

    h = user_hash(
        telegram_id
    )

    stamp = now()

    async with db_pool.acquire() as conn:

        await conn.execute(
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
            VALUES (
                $1,
                $2,
                $3,
                $4,
                'ru',
                FALSE,
                FALSE,
                FALSE,
                $5,
                $5
            )

            ON CONFLICT (user_hash)
            DO UPDATE SET
                username_encrypted =
                    EXCLUDED.username_encrypted,

                first_name_encrypted =
                    EXCLUDED.first_name_encrypted,

                last_activity =
                    EXCLUDED.last_activity
            """,
            h,
            encrypt(
                str(telegram_id)
            ),
            encrypt(username),
            encrypt(first_name),
            stamp,
        )


async def set_language(
    telegram_id: int,
    language: str
) -> None:

    async with db_pool.acquire() as conn:

        await conn.execute(
            """
            UPDATE users
            SET language = $1
            WHERE user_hash = $2
            """,
            language,
            user_hash(
                telegram_id
            ),
        )


async def accept_terms(
    telegram_id: int
) -> None:

    async with db_pool.acquire() as conn:

        await conn.execute(
            """
            UPDATE users

            SET
                setup_complete = TRUE,
                terms_accepted = TRUE

            WHERE user_hash = $1
            """,
            user_hash(
                telegram_id
            ),
        )


async def delete_user_data(
    telegram_id: int
) -> None:

    h = user_hash(
        telegram_id
    )

    async with db_pool.acquire() as conn:

        await conn.execute(
            """
            DELETE FROM downloads
            WHERE user_hash = $1
            """,
            h,
        )

        await conn.execute(
            """
            DELETE FROM users
            WHERE user_hash = $1
            """,
            h,
        )


async def set_blocked(
    telegram_id: int,
    blocked: bool
) -> None:

    async with db_pool.acquire() as conn:

        await conn.execute(
            """
            UPDATE users
            SET is_blocked = $1
            WHERE user_hash = $2
            """,
            blocked,
            user_hash(
                telegram_id
            ),
        )


async def add_download(
    telegram_id: int,
    title: str,
    url: str,
    quality: str,
    size_bytes: int,
    status: str,
) -> None:

    h = user_hash(
        telegram_id
    )

    async with db_pool.acquire() as conn:

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
            h,
            encrypt(title),
            encrypt(url),
            quality,
            int(size_bytes),
            status,
            now(),
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
                h,
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
                h,
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
            ],

        ]
    )


def consent_keyboard(
    language: str
):

    accept_text = (
        "✅ Принять и продолжить"
        if language == "ru"
        else
        "✅ Accept & continue"
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text=accept_text,
                    callback_data="consent:accept",
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

        ]
    )


def main_keyboard(
    language: str
):

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text=tr(
                        language,
                        "download_button"
                    ),
                    callback_data="menu:download",
                ),
            ],

            [
                InlineKeyboardButton(
                    text=tr(
                        language,
                        "history_button"
                    ),
                    callback_data="menu:history",
                ),

                InlineKeyboardButton(
                    text=tr(
                        language,
                        "settings_button"
                    ),
                    callback_data="menu:settings",
                ),
            ],

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
                    text=tr(
                        language,
                        "delete_data"
                    ),
                    callback_data="account:delete",
                ),
            ],

        ]
    )


def quality_keyboard(
    info: dict,
    language: str
):

    buttons = []

    auto_quality_value, auto_size = (
        choose_auto_quality(
            info
        )
    )

    if auto_quality_value is not None:

        auto_text = (
            f"🎯 Авто — "
            f"{auto_quality_value}p "
            f"~{format_bytes(auto_size)}"
            if language == "ru"
            else
            f"🎯 Auto — "
            f"{auto_quality_value}p "
            f"~{format_bytes(auto_size)}"
        )

    else:

        auto_text = tr(
            language,
            "auto"
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text=auto_text,
                callback_data="quality:auto",
            )
        ]
    )

    max_height = max_available_height(
        info
    )

    for quality in (
        1080,
        720,
        480,
        360,
    ):

        if max_height < quality:
            continue

        estimated = estimate_total_size(
            info,
            quality
        )

        if estimated is None:

            button_text = (
                f"{quality}p"
            )

        elif estimated <= SAFE_FILE_BYTES:

            button_text = (
                f"{quality}p — "
                f"~{format_bytes(estimated)} ✅"
            )

        else:

            button_text = (
                f"{quality}p — "
                f"~{format_bytes(estimated)} ❌"
            )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=(
                        f"quality:{quality}"
                    ),
                )
            ]
        )

    if max_height > 1080:

        buttons.append(
            [
                InlineKeyboardButton(
                    text=tr(
                        language,
                        "best"
                    ),
                    callback_data="quality:best",
                )
            ]
        )

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


# ============================================================
# YT-DLP ANALYSIS
# ============================================================

def extract_info_sync(
    url: str
) -> dict:

    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": DOWNLOAD_TIMEOUT,
    }

    with yt_dlp.YoutubeDL(
        options
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

    if not value:
        return None

    try:

        return int(value)

    except (
        TypeError,
        ValueError,
    ):

        return None


def max_available_height(
    info: dict
) -> int:

    heights = []

    for fmt in (
        info.get("formats")
        or []
    ):

        height = fmt.get(
            "height"
        )

        video_codec = fmt.get(
            "vcodec"
        )

        if (
            height
            and video_codec
            and video_codec != "none"
        ):

            heights.append(
                int(height)
            )

    return max(
        heights,
        default=0
    )


def best_video_format(
    info: dict,
    max_height: int
) -> Optional[dict]:

    candidates = []

    for fmt in (
        info.get("formats")
        or []
    ):

        height = fmt.get(
            "height"
        )

        video_codec = fmt.get(
            "vcodec"
        )

        size = format_size(
            fmt
        )

        if not height:
            continue

        if not video_codec:
            continue

        if video_codec == "none":
            continue

        if height > max_height:
            continue

        if size is None:
            continue

        score = (
            int(height),
            1 if fmt.get("ext") == "mp4" else 0,
            size,
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

        audio_codec = fmt.get(
            "acodec"
        )

        video_codec = fmt.get(
            "vcodec"
        )

        size = format_size(
            fmt
        )

        if not audio_codec:
            continue

        if audio_codec == "none":
            continue

        if (
            video_codec
            and video_codec != "none"
        ):
            continue

        if size is None:
            continue

        bitrate = float(
            fmt.get("abr")
            or 0
        )

        score = (
            bitrate,
            1 if fmt.get("ext") == "m4a" else 0,
            size,
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


def estimate_total_size(
    info: dict,
    height: int
) -> Optional[int]:

    video = best_video_format(
        info,
        height
    )

    if not video:
        return None

    total = format_size(
        video
    )

    if total is None:
        return None

    audio = best_audio_format(
        info
    )

    if audio:

        audio_size = format_size(
            audio
        )

        if audio_size:

            total += audio_size

    return total


def choose_auto_quality(
    info: dict
):

    for quality in (
        1080,
        720,
        480,
        360,
    ):

        estimated = estimate_total_size(
            info,
            quality
        )

        if (
            estimated is not None
            and estimated <= SAFE_FILE_BYTES
        ):

            return (
                quality,
                estimated,
            )

    return (
        None,
        None,
    )


# ============================================================
# YT-DLP DOWNLOAD
# ============================================================

def format_selector(
    quality: str
) -> str:

    if quality == "best":

        return (
            "bv*+ba/b"
        )

    quality_int = int(
        quality
    )

    return (
        f"bv*[height<={quality_int}]"
        "[ext=mp4]"
        "+ba[ext=m4a]/"
        f"bv*[height<={quality_int}]"
        "+ba/"
        f"b[height<={quality_int}]"
    )


def download_sync(
    url: str,
    quality: str,
    temp_dir: str
):

    output = str(
        Path(temp_dir)
        / "%(title)s_%(id)s.%(ext)s"
    )

    options = {
        "format": format_selector(
            quality
        ),

        "outtmpl": output,

        "merge_output_format": "mp4",

        "noplaylist": True,

        "quiet": True,

        "no_warnings": True,

        "retries": 2,

        "socket_timeout": DOWNLOAD_TIMEOUT,
    }

    with yt_dlp.YoutubeDL(
        options
    ) as ydl:

        info = ydl.extract_info(
            url,
            download=True,
        )

    files = [
        item
        for item in Path(
            temp_dir
        ).iterdir()
        if item.is_file()
    ]

    if not files:

        raise RuntimeError(
            "Downloaded file not found"
        )

    media_file = max(
        files,
        key=lambda path: path.stat().st_size
    )

    title = (
        info.get("title")
        or "Video"
    )

    duration = info.get(
        "duration"
    )

    size_bytes = media_file.stat().st_size

    return (
        media_file,
        title,
        duration,
        size_bytes,
    )


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

@dp.message(
    Command("start")
)
async def start(
    message: Message
):

    user = message.from_user

    await ensure_user(
        user.id,
        user.username,
        user.first_name,
    )

    if await is_blocked_user(
        user.id
    ):

        await message.answer(
            TEXT["ru"]["blocked"]
        )

        return

    row = await get_user(
        user.id
    )

    if (
        not row
        or not row["setup_complete"]
    ):

        await message.answer(
            TEXT["ru"]["welcome"],
            reply_markup=language_keyboard(),
        )

        return

    language = (
        row["language"]
        or "ru"
    )

    await message.answer(
        TEXT[language]["menu"],
        reply_markup=main_keyboard(
            language
        ),
    )


async def is_blocked_user(
    telegram_id: int
) -> bool:

    row = await get_user(
        telegram_id
    )

    return bool(
        row
        and row["is_blocked"]
    )


# ============================================================
# LANGUAGE SELECTION
# ============================================================

@dp.callback_query(
    F.data.startswith("lang:")
)
async def language_select(
    callback: CallbackQuery
):

    language = (
        callback.data.split(
            ":",
            1
        )[1]
    )

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


# ============================================================
# CONSENT
# ============================================================

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

    language = (
        row["language"]
        if row
        else "ru"
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

    language = (
        row["language"]
        if row
        else "ru"
    )

    await callback.message.answer(
        TEXT[language]["terms"]
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

    language = (
        row["language"]
        if row
        else "ru"
    )

    await callback.message.answer(
        TEXT[language]["privacy"]
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

    language = (
        row["language"]
        if row
        else "ru"
    )

    await message.answer(
        TEXT[language]["terms"]
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

    language = (
        row["language"]
        if row
        else "ru"
    )

    await message.answer(
        TEXT[language]["privacy"]
    )


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

    language = (
        row["language"]
        if row
        else "ru"
    )

    await callback.message.answer(
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

    language = (
        row["language"]
        if row
        else "ru"
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
        callback.data.split(
            ":",
            1
        )[1]
    )

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


# ============================================================
# DELETE ACCOUNT DATA
# ============================================================

@dp.callback_query(
    F.data == "account:delete"
)
async def account_delete(
    callback: CallbackQuery
):

    telegram_id = (
        callback.from_user.id
    )

    row = await get_user(
        telegram_id
    )

    language = (
        row["language"]
        if row
        else "ru"
    )

    await delete_user_data(
        telegram_id
    )

    pending_urls.pop(
        telegram_id,
        None,
    )

    pending_info.pop(
        telegram_id,
        None,
    )

    active_downloads.discard(
        telegram_id
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

    telegram_id = (
        message.from_user.id
    )

    row = await get_user(
        telegram_id
    )

    language = (
        row["language"]
        if row
        else "ru"
    )

    await delete_user_data(
        telegram_id
    )

    pending_urls.pop(
        telegram_id,
        None,
    )

    pending_info.pop(
        telegram_id,
        None,
    )

    active_downloads.discard(
        telegram_id
    )

    await message.answer(
        TEXT[language]["deleted_account"]
    )


# ============================================================
# DOWNLOAD MENU
# ============================================================

@dp.callback_query(
    F.data == "menu:download"
)
async def menu_download(
    callback: CallbackQuery
):

    row = await get_user(
        callback.from_user.id
    )

    language = (
        row["language"]
        if row
        else "ru"
    )

    await callback.message.answer(
        TEXT[language]["send_url"]
    )

    await callback.answer()


# ============================================================
# RECEIVE URL
# ============================================================

@dp.message()
async def receive_url(
    message: Message
):

    telegram_id = (
        message.from_user.id
    )

    await ensure_user(
        telegram_id,
        message.from_user.username,
        message.from_user.first_name,
    )

    if await is_blocked_user(
        telegram_id
    ):

        await message.answer(
            TEXT["ru"]["blocked"]
        )

        return

    row = await get_user(
        telegram_id
    )

    if (
        not row
        or not row["setup_complete"]
    ):

        await message.answer(
            TEXT["ru"]["welcome"],
            reply_markup=language_keyboard(),
        )

        return

    language = (
        row["language"]
        or "ru"
    )

    url = (
        message.text
        or ""
    ).strip()

    if not URL_RE.match(
        url
    ):

        await message.answer(
            TEXT[language]["invalid_url"]
        )

        return

    if (
        telegram_id in active_downloads
        and not is_admin(telegram_id)
    ):

        await message.answer(
            TEXT[language]["busy"]
        )

        return

    status_message = await message.answer(
        TEXT[language]["analyzing"]
    )

    try:

        info = await asyncio.to_thread(
            extract_info_sync,
            url,
        )

        pending_urls[
            telegram_id
        ] = url

        pending_info[
            telegram_id
        ] = info

        keyboard = quality_keyboard(
            info,
            language,
        )

        await status_message.edit_text(
            TEXT[language]["quality"],
            reply_markup=keyboard,
        )

    except Exception:

        logger.exception(
            "Analysis failed for user %s",
            telegram_id,
        )

        await status_message.edit_text(
            TEXT[language]["analyse_failed"]
        )


# ============================================================
# QUALITY SELECTION
# ============================================================

@dp.callback_query(
    F.data.startswith(
        "quality:"
    )
)
async def quality_selected(
    callback: CallbackQuery
):

    telegram_id = (
        callback.from_user.id
    )

    quality = (
        callback.data.split(
            ":",
            1
        )[1]
    )

    url = pending_urls.get(
        telegram_id
    )

    info = pending_info.get(
        telegram_id
    )

    row = await get_user(
        telegram_id
    )

    language = (
        row["language"]
        if row
        else "ru"
    )

    if not url:

        await callback.answer(
            TEXT[language]["no_url"],
            show_alert=True,
        )

        return

    if (
        telegram_id in active_downloads
        and not is_admin(telegram_id)
    ):

        await callback.answer(
            TEXT[language]["busy"],
            show_alert=True,
        )

        return

    # --------------------------------------------------------
    # AUTO QUALITY
    # --------------------------------------------------------

    if quality == "auto":

        if info:

            auto_quality_value, auto_size = (
                choose_auto_quality(
                    info
                )
            )

            if auto_quality_value is None:

                pending_urls.pop(
                    telegram_id,
                    None,
                )

                pending_info.pop(
                    telegram_id,
                    None,
                )

                await callback.message.edit_text(
                    TEXT[language]["too_large"]
                )

                await callback.answer()

                return

            quality = str(
                auto_quality_value
            )

    # --------------------------------------------------------
    # PRE-DOWNLOAD ESTIMATE
    # --------------------------------------------------------

    if (
        info
        and quality.isdigit()
        and not is_admin(telegram_id)
    ):

        estimated = estimate_total_size(
            info,
            int(quality)
        )

        if (
            estimated is not None
            and estimated > SAFE_FILE_BYTES
        ):

            if int(quality) == 360:

                response_text = (
                    TEXT[language]["too_large"]
                )

            else:

                response_text = (
                    TEXT[language][
                        "quality_too_large"
                    ]
                )

            await callback.message.edit_text(
                response_text,
                reply_markup=quality_keyboard(
                    info,
                    language,
                ),
            )

            await callback.answer()

            return

    # --------------------------------------------------------
    # ADMIN BYPASSES APPLICATION CONCURRENCY LIMIT
    # --------------------------------------------------------

    if not is_admin(
        telegram_id
    ):

        active_downloads.add(
            telegram_id
        )

    await callback.answer()

    status_message = (
        await callback.message.edit_text(
            TEXT[language]["downloading"]
        )
    )

    temp_dir = None

    try:

        # ----------------------------------------------------
        # TEMPORARY SERVER DIRECTORY
        # ----------------------------------------------------

        temp_dir = tempfile.mkdtemp(
            prefix="video_downloader_"
        )

        (
            media_file,
            title,
            duration,
            size_bytes,
        ) = await asyncio.to_thread(
            download_sync,
            url,
            quality,
            temp_dir,
        )

        # ----------------------------------------------------
        # APPLICATION DURATION LIMIT
        # ----------------------------------------------------

        if (
            not is_admin(telegram_id)
            and duration
            and int(duration)
                > APP_MAX_DURATION_SECONDS
        ):

            await add_download(
                telegram_id,
                title,
                url,
                quality,
                size_bytes,
                "failed",
            )

            await status_message.edit_text(
                "❌ "
                + (
                    "Видео слишком длинное."
                    if language == "ru"
                    else
                    "The video is too long."
                )
            )

            return

        # ----------------------------------------------------
        # ACTUAL FILE SIZE CHECK
        # ----------------------------------------------------

        if (
            not is_admin(telegram_id)
            and size_bytes > SAFE_FILE_BYTES
        ):

            await add_download(
                telegram_id,
                title,
                url,
                quality,
                size_bytes,
                "failed",
            )

            if quality == "360":

                await status_message.edit_text(
                    TEXT[language]["too_large"]
                )

            else:

                await status_message.edit_text(
                    TEXT[language][
                        "quality_too_large"
                    ],
                    reply_markup=quality_keyboard(
                        info or {},
                        language,
                    ),
                )

            return

        # ----------------------------------------------------
        # SEND
        # ----------------------------------------------------

        await status_message.edit_text(
            TEXT[language]["sending"]
        )

        document = FSInputFile(
            str(media_file),
            filename=(
                f"{title[:100]}.mp4"
            ),
        )

        await bot.send_document(
            chat_id=telegram_id,
            document=document,
            caption=(
                f"🎬 <b>{esc(title)}</b>\n"
                f"Quality: {esc(str(quality))}p\n"
                f"Size: {format_bytes(size_bytes)}"
            ),
        )

        # ----------------------------------------------------
        # DB HISTORY
        # ----------------------------------------------------

        await add_download(
            telegram_id,
            title,
            url,
            quality,
            size_bytes,
            "success",
        )

        await status_message.edit_text(
            TEXT[language]["done"]
        )

    except Exception:

        logger.exception(
            "Download failed for user %s",
            telegram_id,
        )

        try:

            await add_download(
                telegram_id,
                "",
                url,
                quality,
                0,
                "failed",
            )

        except Exception:

            logger.exception(
                "Could not write failed download"
            )

        await status_message.edit_text(
            TEXT[language]["download_failed"]
        )

    finally:

        # ----------------------------------------------------
        # ABSOLUTELY DELETE TEMPORARY VIDEO DIRECTORY
        # ----------------------------------------------------

        if temp_dir:

            shutil.rmtree(
                temp_dir,
                ignore_errors=True,
            )

        pending_urls.pop(
            telegram_id,
            None,
        )

        pending_info.pop(
            telegram_id,
            None,
        )

        if not is_admin(
            telegram_id
        ):

            active_downloads.discard(
                telegram_id
            )


# ============================================================
# HISTORY
# ============================================================

@dp.callback_query(
    F.data == "menu:history"
)
async def history(
    callback: CallbackQuery
):

    telegram_id = (
        callback.from_user.id
    )

    row = await get_user(
        telegram_id
    )

    language = (
        row["language"]
        if row
        else "ru"
    )

    async with db_pool.acquire() as conn:

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
                telegram_id
            ),
        )

    if not rows:

        await callback.message.answer(
            TEXT[language][
                "history_empty"
            ]
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

        size = format_bytes(
            int(
                item["size_bytes"]
            )
        )

        lines.append(
            f"🎬 <b>{esc(title[:70])}</b>\n"
            f"{esc(str(item['quality']))}p"
            f" • {size}\n"
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
                callback_data=(
                    "history:clear"
                ),
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


@dp.callback_query(
    F.data.startswith(
        "history_delete:"
    )
)
async def delete_history_item(
    callback: CallbackQuery
):

    telegram_id = (
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

    async with db_pool.acquire() as conn:

        await conn.execute(
            """
            DELETE FROM downloads

            WHERE
                id = $1
                AND user_hash = $2
            """,
            item_id,
            user_hash(
                telegram_id
            ),
        )

    row = await get_user(
        telegram_id
    )

    language = (
        row["language"]
        if row
        else "ru"
    )

    await callback.message.delete()

    await callback.answer(
        TEXT[language]["deleted"]
    )


@dp.callback_query(
    F.data == "history:clear"
)
async def clear_history(
    callback: CallbackQuery
):

    telegram_id = (
        callback.from_user.id
    )

    async with db_pool.acquire() as conn:

        await conn.execute(
            """
            DELETE FROM downloads
            WHERE user_hash = $1
            """,
            user_hash(
                telegram_id
            ),
        )

    row = await get_user(
        telegram_id
    )

    language = (
        row["language"]
        if row
        else "ru"
    )

    await callback.message.edit_text(
        TEXT[language]["all_deleted"]
    )

    await callback.answer()


# ============================================================
# ADMIN PANEL
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
        "are disabled for the admin ID.\n\n"

        "External Telegram/API and infrastructure "
        "limits still apply."
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

    async with db_pool.acquire() as conn:

        users = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM users
            """
        )

        success = await conn.fetchval(
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
        f"Successful downloads: {success}\n"
        f"Failed: {failed}\n"
        f"Transferred: "
        f"{format_bytes(int(total or 0))}"
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

    async with db_pool.acquire() as conn:

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

        text += (
            f"👤 <b>"
            f"{esc(first_name)}"
            f"</b>\n"

            f"ID: "
            f"<code>"
            f"{esc(telegram_id)}"
            f"</code>\n"

            f"@{esc(username)}\n"

            f"Downloads: "
            f"{row['downloads_count']}\n"

            f"Errors: "
            f"{row['failed_count']}\n"

            f"Blocked: "
            f"{row['is_blocked']}\n\n"
        )

    # Telegram message safety.
    while text:

        chunk = text[:3500]

        if len(text) > 3500:

            cut = chunk.rfind(
                "\n\n"
            )

            if cut > 1000:

                chunk = chunk[:cut]

        await message.answer(
            chunk
        )

        text = text[
            len(chunk):
        ]


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
        message.text or ""
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
            "Invalid Telegram ID."
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
        f"{format_bytes(int(row['total_bytes']))}\n"

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

    async with db_pool.acquire() as conn:

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
            f"🎬 "
            f"{esc(title[:60])}\n"

            f"Quality: "
            f"{esc(str(row['quality']))}p\n"

            f"Size: "
            f"{format_bytes("
                f"int(row['size_bytes'])"
                f")}\n"

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
        message.text or ""
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
            "Invalid Telegram ID."
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
        message.text or ""
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
            "Invalid Telegram ID."
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
        message.text or ""
    ).split(
        maxsplit=1
    )

    if len(parts) != 2:

        await message.answer(
            "Usage: /broadcast TEXT"
        )

        return

    broadcast_text = parts[1]

    async with db_pool.acquire() as conn:

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
                broadcast_text,
            )

            sent += 1

        except Exception:

            failed += 1

        # Keep broadcast traffic conservative.
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

    # Ensure we use long polling.
    await bot.delete_webhook(
        drop_pending_updates=True
    )

    try:

        await dp.start_polling(
            bot
        )

    finally:

        await close_db()

        await bot.session.close()


if __name__ == "__main__":

    asyncio.run(
        main()
    )
