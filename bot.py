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

# Установленный нами лимит для обычных пользователей.
APP_MAX_FILE_MB = int(
    os.getenv("MAX_FILE_MB", "50")
)

APP_MAX_DURATION_SECONDS = int(
    os.getenv("MAX_DURATION_SECONDS", "21600")
)

DOWNLOAD_TIMEOUT = int(
    os.getenv("DOWNLOAD_TIMEOUT", "600")
)

# Реальное ограничение обычного Telegram Bot API.
# После подключения Local Bot API Server это значение можно будет
# заменить на 2000.
TELEGRAM_MAX_FILE_MB = int(
    os.getenv("TELEGRAM_MAX_FILE_MB", "50")
)

# Используем небольшой запас.
SAFE_FILE_MB = min(
    APP_MAX_FILE_MB,
    TELEGRAM_MAX_FILE_MB
)

SAFE_FILE_BYTES = int(
    SAFE_FILE_MB * 1024 * 1024 * 0.98
)


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
# DATABASE
# ============================================================

db_pool: Optional[asyncpg.Pool] = None


async def init_db():
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


async def close_db():
    global db_pool

    if db_pool:
        await db_pool.close()
        db_pool = None


def utc_now():
    return datetime.now(
        timezone.utc
    )


def hash_user(
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
            hash_user(
                telegram_id
            ),
        )


async def ensure_user(
    telegram_id: int,
    username: Optional[str],
    first_name: Optional[str],
):

    h = hash_user(
        telegram_id
    )

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
                $1, $2, $3, $4,
                'ru',
                FALSE,
                FALSE,
                FALSE,
                $5,
                $5
            )
            ON CONFLICT (user_hash)
            DO UPDATE SET
                username_encrypted = EXCLUDED.username_encrypted,
                first_name_encrypted = EXCLUDED.first_name_encrypted,
                last_activity = EXCLUDED.last_activity
            """,
            h,
            encrypt(
                str(telegram_id)
            ),
            encrypt(username),
            encrypt(first_name),
            utc_now(),
        )


async def set_language(
    telegram_id: int,
    language: str,
):
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE users
            SET language = $1
            WHERE user_hash = $2
            """,
            language,
            hash_user(telegram_id),
        )


async def complete_setup(
    telegram_id: int
):
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE users
            SET
                setup_complete = TRUE,
                terms_accepted = TRUE
            WHERE user_hash = $1
            """,
            hash_user(telegram_id),
        )


async def delete_all_user_data(
    telegram_id: int
):
    h = hash_user(
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
    blocked: bool,
):
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE users
            SET is_blocked = $1
            WHERE user_hash = $2
            """,
            blocked,
            hash_user(telegram_id),
        )


async def add_download(
    telegram_id: int,
    title: str,
    url: str,
    quality: str,
    size_bytes: int,
    status: str,
):

    h = hash_user(
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
                $1, $2, $3, $4, $5, $6, $7
            )
            """,
            h,
            encrypt(title),
            encrypt(url),
            quality,
            int(size_bytes),
            status,
            utc_now(),
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
# LANGUAGE TEXT
# ============================================================

TEXT = {

    "ru": {

        "welcome":
            "👋 <b>Добро пожаловать в Video Downloader</b>\n\n"
            "Выберите язык:",

        "terms":
            "📄 <b>Условия использования</b>\n\n"
            "Вы подтверждаете, что имеете необходимые права "
            "или разрешение на загрузку и использование "
            "отправленного контента.\n\n"
            "Сервис не предназначен для нарушения авторских "
            "прав или иных прав третьих лиц.\n\n"
            "Видео хранится на сервере только временно, "
            "для обработки и отправки пользователю.",

        "privacy":
            "🔐 <b>Конфиденциальность</b>\n\n"
            "Мы храним минимально необходимую информацию: "
            "Telegram ID, имя/username, язык и техническую "
            "историю загрузок.\n\n"
            "Сами видео после обработки удаляются с сервера.\n\n"
            "Ваши данные можно удалить через Настройки.",

        "ready":
            "✅ Готово!\n\n"
            "Отправьте публичную ссылку на видео.",

        "menu":
            "Главное меню:",

        "send_url":
            "🔗 Отправьте публичную ссылку на видео.",

        "analyzing":
            "🔎 Анализирую доступные качества и размер видео…",

        "quality":
            "🎬 <b>Выберите качество</b>\n\n"
            "Размер указан приблизительно, когда он доступен.",

        "downloading":
            "⏳ Скачиваю видео…",

        "sending":
            "📤 Отправляю видео в Telegram…",

        "done":
            "✅ <b>Готово!</b>\n\n"
            "Временный файл на сервере удалён.",

        "too_large":
            "❌ <b>Видео невозможно отправить</b>\n\n"
            "Даже самое лёгкое доступное качество "
            "превышает текущий лимит Telegram в 50 МБ.\n\n"
            "Попробуйте более короткое видео.",

        "download_failed":
            "❌ Не удалось скачать это видео.\n\n"
            "Попробуйте другое качество или другую ссылку.",

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
            "❌ Отправьте корректную ссылку, начинающуюся с http:// или https://.",

        "deleted_account":
            "✅ Ваши данные удалены.",

        "no_url":
            "⚠️ Сначала отправьте ссылку на видео.",

        "auto":
            "🎯 Авто",

    },

    "en": {

        "welcome":
            "👋 <b>Welcome to Video Downloader</b>\n\n"
            "Choose your language:",

        "terms":
            "📄 <b>Terms of Use</b>\n\n"
            "You confirm that you have the necessary rights "
            "or permission to download and use the submitted "
            "content.\n\n"
            "The service is not intended for copyright "
            "infringement or violation of third-party rights.\n\n"
            "Videos are stored on the server only temporarily "
            "for processing and delivery.",

        "privacy":
            "🔐 <b>Privacy</b>\n\n"
            "We store only the minimum information required: "
            "Telegram ID, name/username, language and technical "
            "download history.\n\n"
            "Video files are deleted from the server after processing.\n\n"
            "You can delete your data from Settings.",

        "ready":
            "✅ Ready!\n\n"
            "Send a public video URL.",

        "menu":
            "Main menu:",

        "send_url":
            "🔗 Send a public video URL.",

        "analyzing":
            "🔎 Analyzing available qualities and video size…",

        "quality":
            "🎬 <b>Choose quality</b>\n\n"
            "The size is approximate when source information is available.",

        "downloading":
            "⏳ Downloading video…",

        "sending":
            "📤 Sending video to Telegram…",

        "done":
            "✅ <b>Done!</b>\n\n"
            "The temporary server file has been deleted.",

        "too_large":
            "❌ <b>This video cannot be sent</b>\n\n"
            "Even the lowest available quality exceeds "
            "Telegram's current 50 MB bot upload limit.\n\n"
            "Please try a shorter video.",

        "download_failed":
            "❌ Could not download this video.\n\n"
            "Try another quality or another URL.",

        "busy":
            "⏳ You already have an active download.",

        "history":
            "📁 <b>Downloaded videos</b>",

        "history_empty":
            "📁 Download history is empty.",

        "deleted":
            "✅ History entry deleted.",

        "all_deleted":
            "✅ History deleted.",

        "settings":
            "⚙️ <b>Settings</b>",

        "language_changed":
            "✅ Language changed.",

        "blocked":
            "⛔ Access to the service is restricted.",

        "invalid_url":
            "❌ Send a valid URL beginning with http:// or https://.",

        "deleted_account":
            "✅ Your data has been deleted.",

        "no_url":
            "⚠️ Send a video URL first.",

        "auto":
            "🎯 Auto",

    },

}


def get_lang(
    row
) -> str:

    if not row:
        return "ru"

    return row["language"] or "ru"


def tr(
    row,
    key: str,
) -> str:

    language = get_lang(row)

    return TEXT[
        language
    ][key]


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

    accept = (
        "✅ Принять и продолжить"
        if language == "ru"
        else
        "✅ Accept & continue"
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


def main_keyboard(
    language: str
):

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text=(
                        "➕ Скачать видео"
                        if language == "ru"
                        else
                        "➕ Download video"
                    ),
                    callback_data="menu:download",
                )
            ],

            [
                InlineKeyboardButton(
                    text=(
                        "📁 Загруженные видео"
                        if language == "ru"
                        else
                        "📁 Downloaded videos"
                    ),
                    callback_data="menu:history",
                ),

                InlineKeyboardButton(
                    text=(
                        "⚙️ Настройки"
                        if language == "ru"
                        else
                        "⚙️ Settings"
                    ),
                    callback_data="menu:settings",
                ),
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
                    text=(
                        "🗑 Удалить мои данные"
                        if language == "ru"
                        else
                        "🗑 Delete my data"
                    ),
                    callback_data="account:delete",
                )
            ],

        ]
    )


# ============================================================
# RUNTIME STATE
# ============================================================

pending_urls: dict[int, str] = {}

pending_quality_data: dict[
    int,
    dict
] = {}

active_downloads: set[int] = set()

URL_RE = re.compile(
    r"^https?://\S+$",
    re.IGNORECASE,
)


# ============================================================
# HELPERS
# ============================================================

def is_admin(
    telegram_id: int
) -> bool:
    return telegram_id == ADMIN_ID


async def is_user_blocked(
    telegram_id: int
) -> bool:

    row = await get_user(
        telegram_id
    )

    return bool(
        row and row["is_blocked"]
    )


async def is_ready(
    telegram_id: int
) -> bool:

    row = await get_user(
        telegram_id
    )

    if not row:
        return False

    return bool(
        row["setup_complete"]
        and row["terms_accepted"]
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


def safe_title(
    title: str
) -> str:

    return html.escape(
        (title or "Video")[:200]
    )


# ============================================================
# YT-DLP ANALYSIS
# ============================================================

def extract_info(
    url: str
):
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


def format_size_from_entry(
    entry: dict
) -> Optional[int]:

    value = (
        entry.get("filesize")
        or entry.get("filesize_approx")
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


def select_best_video_format(
    formats: list,
    max_height: int
):

    candidates = []

    for fmt in formats:

        height = fmt.get(
            "height"
        )

        vcodec = fmt.get(
            "vcodec"
        )

        if not height:
            continue

        if height > max_height:
            continue

        if not vcodec or vcodec == "none":
            continue

        filesize = (
            format_size_from_entry(
                fmt
            )
        )

        if not filesize:
            continue

        ext = fmt.get(
            "ext",
            ""
        )

        score = (
            height,
            1 if ext == "mp4" else 0,
            filesize,
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
        key=lambda x: x[0],
        reverse=True,
    )

    return candidates[0][1]


def select_best_audio_format(
    formats: list
):

    candidates = []

    for fmt in formats:

        vcodec = fmt.get(
            "vcodec"
        )

        acodec = fmt.get(
            "acodec"
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

        filesize = (
            format_size_from_entry(
                fmt
            )
        )

        if not filesize:
            continue

        abr = (
            fmt.get("abr")
            or 0
        )

        ext = fmt.get(
            "ext",
            ""
        )

        score = (
            abr,
            1 if ext == "m4a" else 0,
            filesize,
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
        key=lambda x: x[0],
        reverse=True,
    )

    return candidates[0][1]


def estimate_quality_size(
    info: dict,
    quality: int,
) -> Optional[int]:

    formats = (
        info.get("formats")
        or []
    )

    video = select_best_video_format(
        formats,
        quality,
    )

    audio = select_best_audio_format(
        formats
    )

    if not video:
        return None

    video_size = (
        format_size_from_entry(
            video
        )
    )

    if video_size is None:
        return None

    if audio:

        audio_size = (
            format_size_from_entry(
                audio
            )
        )

        if audio_size:
            return (
                video_size
                + audio_size
            )

    # Progressive fallback.
    return video_size


def available_max_height(
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

        vcodec = fmt.get(
            "vcodec"
        )

        if (
            height
            and vcodec
            and vcodec != "none"
        ):
            heights.append(
                int(height)
            )

    return max(
        heights,
        default=0,
    )


def auto_quality(
    info: dict
):

    for quality in (
        1080,
        720,
        480,
        360,
    ):

        estimated = estimate_quality_size(
            info,
            quality,
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


async def analyze_video(
    url: str
):

    return await asyncio.to_thread(
        extract_info,
        url,
    )


# ============================================================
# QUALITY KEYBOARD
# ============================================================

def build_quality_keyboard(
    info: dict,
    language: str,
):

    buttons = []

    max_height = available_max_height(
        info
    )

    auto_q, auto_size = auto_quality(
        info
    )

    if auto_q:

        auto_text = (
            f"🎯 Авто — {auto_q}p "
            f"~{format_bytes(auto_size)}"
            if language == "ru"
            else
            f"🎯 Auto — {auto_q}p "
            f"~{format_bytes(auto_size)}"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=auto_text,
                    callback_data=f"quality:auto",
                )
            ]
        )

    elif max_height:

        buttons.append(
            [
                InlineKeyboardButton(
                    text=(
                        "🎯 Авто"
                        if language == "ru"
                        else
                        "🎯 Auto"
                    ),
                    callback_data="quality:auto",
                )
            ]
        )

    for quality in (
        1080,
        720,
        480,
        360,
    ):

        if max_height < quality:
            continue

        estimated = estimate_quality_size(
            info,
            quality,
        )

        if estimated:

            if estimated > SAFE_FILE_BYTES:

                text = (
                    f"{quality}p — "
                    f"~{format_bytes(estimated)} ❌"
                )

            else:

                text = (
                    f"{quality}p — "
                    f"~{format_bytes(estimated)} ✅"
                )

        else:

            text = (
                f"{quality}p"
            )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=text,
                    callback_data=f"quality:{quality}",
                )
            ]
        )

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


# ============================================================
# DOWNLOAD
# ============================================================

def quality_selector(
    quality: str
):

    if quality == "auto":

        return (
            "bv*[height<=1080]"
            "[ext=mp4]"
            "+ba[ext=m4a]/"
            "bv*[height<=1080]"
            "+ba/"
            "b[height<=1080]"
        )

    q = int(quality)

    return (
        f"bv*[height<={q}]"
        "[ext=mp4]"
        f"+ba[ext=m4a]/"
        f"bv*[height<={q}]"
        f"+ba/"
        f"b[height<={q}]"
    )


def perform_download(
    url: str,
    quality: str,
    temp_dir: str,
):

    output = str(
        Path(temp_dir)
        / "%(title)s_%(id)s.%(ext)s"
    )

    options = {
        "format": quality_selector(
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

    title = (
        info.get("title")
        or "Video"
    )

    duration = info.get(
        "duration"
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
            "Downloaded media file not found"
        )

    media_file = max(
        files,
        key=lambda p: p.stat().st_size
    )

    size = media_file.stat().st_size

    return (
        media_file,
        title,
        duration,
        size,
    )


async def download(
    url: str,
    quality: str,
):

    temp_dir = tempfile.mkdtemp(
        prefix="video_downloader_"
    )

    try:

        return await asyncio.to_thread(
            perform_download,
            url,
            quality,
            temp_dir,
        )

    except Exception:

        shutil.rmtree(
            temp_dir,
            ignore_errors=True,
        )

        raise


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

    if await is_user_blocked(
        user.id
    ):

        await message.answer(
            TEXT["ru"]["blocked"]
        )

        return

    row = await get_user(
        user.id
    )

    if not row or not row["setup_complete"]:

        await message.answer(
            TEXT["ru"]["welcome"],
            reply_markup=language_keyboard(),
        )

        return

    await message.answer(
        tr(row, "menu"),
        reply_markup=main_keyboard(
            get_lang(row)
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
        callback.data.split(":")[1]
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


@dp.callback_query(
    F.data == "consent:accept"
)
async def accept_terms(
    callback: CallbackQuery
):

    await complete_setup(
        callback.from_user.id
    )

    row = await get_user(
        callback.from_user.id
    )

    language = get_lang(row)

    await callback.message.edit_text(
        TEXT[language]["ready"],
        reply_markup=main_keyboard(
            language
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
    callback: CallbackQuery
):

    await callback.message.answer(
        tr(
            await get_user(
                callback.from_user.id
            ),
            "terms",
        )
    )

    await callback.answer()


@dp.callback_query(
    F.data == "info:privacy"
)
async def info_privacy(
    callback: CallbackQuery
):

    await callback.message.answer(
        tr(
            await get_user(
                callback.from_user.id
            ),
            "privacy",
        )
    )

    await callback.answer()


@dp.message(
    Command("terms")
)
async def command_terms(
    message: Message
):

    row = await get_user(
        message.from_user.id
    )

    await message.answer(
        tr(row, "terms")
    )


@dp.message(
    Command("privacy")
)
async def command_privacy(
    message: Message
):

    row = await get_user(
        message.from_user.id
    )

    await message.answer(
        tr(row, "privacy")
    )


# ============================================================
# SETTINGS
# ============================================================

@dp.callback_query(
    F.data == "menu:settings"
)
async def settings(
    callback: CallbackQuery
):

    row = await get_user(
        callback.from_user.id
    )

    language = get_lang(row)

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
async def command_settings(
    message: Message
):

    row = await get_user(
        message.from_user.id
    )

    language = get_lang(row)

    await message.answer(
        TEXT[language]["settings"],
        reply_markup=settings_keyboard(
            language
        ),
    )


@dp.callback_query(
    F.data.startswith("settings_lang:")
)
async def settings_language(
    callback: CallbackQuery
):

    language = (
        callback.data.split(":")[1]
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
# DELETE DATA
# ============================================================

@dp.callback_query(
    F.data == "account:delete"
)
async def delete_account(
    callback: CallbackQuery
):

    telegram_id = (
        callback.from_user.id
    )

    row = await get_user(
        telegram_id
    )

    language = get_lang(row)

    await delete_all_user_data(
        telegram_id
    )

    pending_urls.pop(
        telegram_id,
        None,
    )

    pending_quality_data.pop(
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

    language = get_lang(row)

    await delete_all_user_data(
        telegram_id
    )

    pending_urls.pop(
        telegram_id,
        None,
    )

    pending_quality_data.pop(
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

    await callback.message.answer(
        tr(row, "send_url")
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

    if await is_user_blocked(
        telegram_id
    ):

        await message.answer(
            TEXT["ru"]["blocked"]
        )

        return

    if not await is_ready(
        telegram_id
    ):

        await message.answer(
            TEXT["ru"]["welcome"],
            reply_markup=language_keyboard(),
        )

        return

    url = (
        message.text or ""
    ).strip()

    if not URL_RE.match(
        url
    ):

        row = await get_user(
            telegram_id
        )

        await message.answer(
            tr(row, "invalid_url")
        )

        return

    # Приложение не позволяет несколько скачиваний
    # одновременно обычному пользователю.
    if (
        telegram_id in active_downloads
        and not is_admin(telegram_id)
    ):

        row = await get_user(
            telegram_id
        )

        await message.answer(
            tr(row, "busy")
        )

        return

    row = await get_user(
        telegram_id
    )

    language = get_lang(row)

    status = await message.answer(
        TEXT[language]["analyzing"]
    )

    try:

        info = await analyze_video(
            url
        )

        pending_urls[
            telegram_id
        ] = url

        pending_quality_data[
            telegram_id
        ] = info

        keyboard = build_quality_keyboard(
            info,
            language,
        )

        await status.edit_text(
            TEXT[language]["quality"],
            reply_markup=keyboard,
        )

    except Exception:

        logger.exception(
            "Analysis failed"
        )

        await status.edit_text(
            tr(row, "download_failed")
        )


# ============================================================
# QUALITY SELECTED
# ============================================================

@dp.callback_query(
    F.data.startswith("quality:")
)
async def quality_selected(
    callback: CallbackQuery
):

    telegram_id = (
        callback.from_user.id
    )

    quality = (
        callback.data.split(":")[1]
    )

    url = pending_urls.get(
        telegram_id
    )

    info = pending_quality_data.get(
        telegram_id
    )

    row = await get_user(
        telegram_id
    )

    language = get_lang(row)

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

    if quality == "auto":

        if info:

            auto_q, estimated = (
                auto_quality(
                    info
                )
            )

            if auto_q:

                quality = str(
                    auto_q
                )

            else:

                await callback.message.edit_text(
                    TEXT[language]["too_large"]
                )

                pending_urls.pop(
                    telegram_id,
                    None,
                )

                pending_quality_data.pop(
                    telegram_id,
                    None,
                )

                await callback.answer()
                return

    # Перед фактической загрузкой проверяем известную оценку.
    if (
       
