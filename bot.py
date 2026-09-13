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

COBALT_API_URL = os.getenv(
    "COBALT_API_URL",
    ""
).strip().rstrip("/")

DATA_ENCRYPTION_KEY = os.getenv(
    "DATA_ENCRYPTION_KEY",
    ""
).strip()

APP_MAX_FILE_MB = int(
    os.getenv("MAX_FILE_MB", "50")
)

APP_MAX_DURATION_SECONDS = int(
    os.getenv(
        "MAX_DURATION_SECONDS",
        "21600"
    )
)

TELEGRAM_MAX_FILE_MB = int(
    os.getenv(
        "TELEGRAM_MAX_FILE_MB",
        "50"
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
        APP_MAX_FILE_MB,
        TELEGRAM_MAX_FILE_MB,
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

if not COBALT_API_URL:
    raise RuntimeError(
        "COBALT_API_URL is not configured"
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
# BOT OBJECTS
#
# IMPORTANT:
# bot and dp are created BEFORE any @dp handlers.
# ============================================================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    ),
)

dp = Dispatcher()


# ============================================================
# GLOBAL STATE
# ============================================================

DB_POOL: Optional[asyncpg.Pool] = None

PENDING: dict[int, dict] = {}

ACTIVE: set[int] = set()

URL_RE = re.compile(
    r"^https?://\S+$",
    re.IGNORECASE
)

QUALITY_ORDER = (
    "1080",
    "720",
    "480",
    "360",
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
            "Временные видеофайлы удаляются после обработки.",

        "privacy":
            "🔐 <b>Конфиденциальность</b>\n\n"
            "Бот хранит минимально необходимые данные: "
            "Telegram ID, имя/username, язык и техническую "
            "историю загрузок.\n\n"
            "Сами видео постоянно на сервере не хранятся. "
            "Временная копия удаляется после обработки.\n\n"
            "Удалить данные можно в Настройках.",

        "ready":
            "✅ <b>Готово!</b>\n\n"
            "Отправьте публичную ссылку на видео.",

        "menu":
            "Главное меню:",

        "send_url":
            "🔗 Отправьте публичную ссылку на видео.",

        "analyzing":
            "🔎 Анализирую доступные качества и размер…",

        "quality":
            "🎬 <b>Выберите качество</b>\n\n"
            "Бот старается предложить максимальное качество, "
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

        "auto":
            "🎯 Авто",

        "best":
            "⭐ Максимум",

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
            "Temporary video files are deleted after processing.",

        "privacy":
            "🔐 <b>Privacy</b>\n\n"
            "The bot stores only the minimum data required: "
            "Telegram ID, name/username, language and technical "
            "download history.\n\n"
            "Videos are not permanently stored on the server. "
            "The temporary copy is deleted after processing.\n\n"
            "You can delete your data in Settings.",

        "ready":
            "✅ <b>Ready!</b>\n\n"
            "Send a public video URL.",

        "menu":
            "Main menu:",

        "send_url":
            "🔗 Send a public video URL.",

        "analyzing":
            "🔎 Checking available qualities and size…",

        "quality":
            "🎬 <b>Choose quality</b>\n\n"
            "The bot tries to recommend the highest quality "
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
            "❌ Could not get information about this video.",

        "auto":
            "🎯 Auto",

        "best":
            "⭐ Best",

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
    },
}


# ============================================================
# GENERAL HELPERS
# ============================================================

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


def is_admin(
    telegram_id: int
) -> bool:

    return telegram_id == ADMIN_ID


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


async def close_db():

    global DB_POOL

    if DB_POOL:

        await DB_POOL.close()

        DB_POOL = None


async def get_user(
    telegram_id: int
):

    async with DB_POOL.acquire() as conn:

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

    stamp = now()

    async with DB_POOL.acquire() as conn:

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
            hash_user(telegram_id),
            encrypt(str(telegram_id)),
            encrypt(username),
            encrypt(first_name),
            stamp,
        )


async def set_language(
    telegram_id: int,
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
            hash_user(
                telegram_id
            ),
        )


async def accept_terms(
    telegram_id: int
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
            hash_user(
                telegram_id
            ),
        )


async def delete_user_data(
    telegram_id: int
):

    async with DB_POOL.acquire() as conn:

        user_key = hash_user(
            telegram_id
        )

        await conn.execute(
            """
            DELETE FROM downloads
            WHERE user_hash = $1
            """,
            user_key,
        )

        await conn.execute(
            """
            DELETE FROM users
            WHERE user_hash = $1
            """,
            user_key,
        )


async def set_blocked(
    telegram_id: int,
    blocked: bool
):

    async with DB_POOL.acquire() as conn:

        await conn.execute(
            """
            UPDATE users
            SET is_blocked = $1
            WHERE user_hash = $2
            """,
            blocked,
            hash_user(
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
):

    user_key = hash_user(
        telegram_id
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
            user_key,
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
                user_key,
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
                user_key,
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
    entries: list[dict]
):

    buttons = []

    auto_quality = choose_auto(
        entries
    )

    auto_text = TEXT[language]["auto"]

    if auto_quality:

        auto_item = next(
            (
                item
                for item in entries
                if item["quality"]
                == auto_quality
            ),
            None,
        )

        if auto_item:

            auto_text += (
                f" — {auto_quality}p"
            )

            if auto_item.get("size"):

                auto_text += (
                    f" ~"
                    f"{fmt_bytes(auto_item['size'])}"
                )

    buttons.append(
        [
            InlineKeyboardButton(
                text=auto_text,
                callback_data="quality:auto",
            )
        ]
    )

    for item in entries:

        q = item["quality"]

        label = f"{q}p"

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

        buttons.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"quality:{q}",
                )
            ]
        )

    if any(
        int(item["quality"]) > 1080
        for item in entries
    ):

        buttons.append(
            [
                InlineKeyboardButton(
                    text=TEXT[language]["best"],
                    callback_data="quality:best",
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

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


# ============================================================
# COBALT
# ============================================================

def cobalt_request_sync(
    url: str,
    quality: str
) -> dict:

    body = {
        "url": url,
        "videoQuality": quality,
        "downloadMode": "auto",
        "filenameStyle": "pretty",
        "youtubeVideoCodec": "h264",
        "youtubeVideoContainer": "mp4",
        "youtubeBetterAudio": True,
    }

    request = Request(
        f"{COBALT_API_URL}/",
        data=json.dumps(
            body
        ).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:

        with urlopen(
            request,
            timeout=DOWNLOAD_TIMEOUT,
        ) as response:

            data = response.read().decode(
                "utf-8"
            )

        return json.loads(
            data
        )

    except HTTPError as exc:

        error_body = exc.read().decode(
            "utf-8",
            errors="replace",
        )

        raise RuntimeError(
            f"Cobalt HTTP {exc.code}: "
            f"{error_body[:300]}"
        ) from exc

    except URLError as exc:

        raise RuntimeError(
            f"Cobalt connection error: {exc}"
        ) from exc


def cobalt_media(
    response: dict
) -> dict:

    status = response.get(
        "status"
    )

    if status in (
        "tunnel",
        "redirect",
    ):

        media_url = response.get(
            "url"
        )

        if not media_url:

            raise RuntimeError(
                "Cobalt response has no URL"
            )

        return {
            "url": media_url,
            "filename": (
                response.get("filename")
                or "video.mp4"
            ),
        }

    if status == "picker":

        for item in (
            response.get("picker")
            or []
        ):

            if (
                item.get("type")
                == "video"
                and item.get("url")
            ):

                return {
                    "url": item["url"],
                    "filename": (
                        item.get(
                            "filename"
                        )
                        or "video.mp4"
                    ),
                }

        raise RuntimeError(
            "Cobalt picker has no video"
        )

    if status == "error":

        error = (
            response.get("error")
            or {}
        )

        raise RuntimeError(
            str(
                error.get(
                    "code"
                )
                or "Cobalt error"
            )
        )

    raise RuntimeError(
        f"Unsupported Cobalt status: "
        f"{status}"
    )


def head_size_sync(
    url: str
) -> Optional[int]:

    request = Request(
        url,
        headers={
            "User-Agent":
                "Mozilla/5.0"
        },
        method="HEAD",
    )

    try:

        with urlopen(
            request,
            timeout=30,
        ) as response:

            value = response.headers.get(
                "Content-Length"
            )

            if value:

                return int(
                    value
                )

    except Exception:

        pass

    return None


async def analyze_cobalt(
    url: str
) -> list[dict]:

    results = []

    for quality in QUALITY_ORDER:

        try:

            response = await asyncio.to_thread(
                cobalt_request_sync,
                url,
                quality,
            )

            media = await asyncio.to_thread(
                cobalt_media,
                response,
            )

            media_size = await asyncio.to_thread(
                head_size_sync,
                media["url"],
            )

            results.append(
                {
                    "quality": quality,
                    "url": media["url"],
                    "filename": media["filename"],
                    "size": media_size,
                }
            )

        except Exception as exc:

            logger.info(
                "Cobalt %sp unavailable: %s",
                quality,
                exc,
            )

    return results


def choose_auto(
    entries: list[dict]
) -> Optional[str]:

    for quality in QUALITY_ORDER:

        item = next(
            (
                entry
                for entry in entries
                if entry["quality"]
                == quality
            ),
            None,
        )

        if not item:
            continue

        size = item.get(
            "size"
        )

        if (
            size is None
            or size <= SAFE_FILE_BYTES
        ):

            return quality

    return None


def stream_download_sync(
    url: str,
    temp_dir: str,
    max_bytes: Optional[int]
):

    path = (
        Path(temp_dir)
        / "media.bin"
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
            f"Media download failed: {exc}"
        ) from exc


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
        callback.data.split(
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
async def terms_callback(
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
        TEXT[language]["terms"],
        reply_markup=back_keyboard(
            language
        ),
    )

    await callback.answer()


@dp.callback_query(
    F.data == "info:privacy"
)
async def privacy_callback(
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

    language = (
        row["language"]
        if row
        else "ru"
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

    language = (
        row["language"]
        if row
        else "ru"
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

    language = (
        row["language"]
        if row
        else "ru"
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

    language = (
        row["language"]
        if row
        else "ru"
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

    language = (
        row["language"]
        if row
        else "ru"
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

    user_id = (
        callback.from_user.id
    )

    row = await get_user(
        user_id
    )

    language = (
        row["language"]
        if row
        else "ru"
    )

    await delete_user_data(
        user_id
    )

    PENDING.pop(
        user_id,
        None,
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

    language = (
        row["language"]
        if row
        else "ru"
    )

    await delete_user_data(
        user_id
    )

    PENDING.pop(
        user_id,
        None,
    )

    ACTIVE.discard(
        user_id
    )

    await message.answer(
        TEXT[language]["deleted_account"]
    )


# ============================================================
# RECEIVE URL
# ============================================================

@dp.message()
async def receive_url(
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

        results = await analyze_cobalt(
            url
        )

        if not results:

            raise RuntimeError(
                "No usable Cobalt results"
            )

        PENDING[
            user.id
        ] = {
            "url": url,
            "results": results,
        }

        await status.edit_text(
            TEXT[language]["quality"],
            reply_markup=quality_keyboard(
                language,
                results,
            ),
        )

    except Exception:

        logger.exception(
            "Cobalt analysis failed for user %s",
            user.id,
        )

        await status.edit_text(
            TEXT[language]["analysis_failed"],
            reply_markup=back_keyboard(
                language
            ),
        )


# ============================================================
# QUALITY SELECTED
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

    pending = PENDING.get(
        user_id
    )

    row = await get_user(
        user_id
    )

    language = (
        row["language"]
        if row
        else "ru"
    )

    if not pending:

        await callback.answer(
            TEXT[language]["no_url"],
            show_alert=True,
        )

        return

    results = pending[
        "results"
    ]

    selection = (
        callback.data.split(
            ":",
            1
        )[1]
    )

    selected = None

    # --------------------------------------------------------
    # AUTO
    # --------------------------------------------------------

    if selection == "auto":

        auto_quality = choose_auto(
            results
        )

        if auto_quality:

            selected = next(
                (
                    item
                    for item in results
                    if item["quality"]
                    == auto_quality
                ),
                None,
            )

    # --------------------------------------------------------
    # BEST
    # --------------------------------------------------------

    elif selection == "best":

        if results:

            selected = max(
                results,
                key=lambda item: int(
                    item["quality"]
                ),
            )

    # --------------------------------------------------------
    # EXPLICIT QUALITY
    # --------------------------------------------------------

    else:

        selected = next(
            (
                item
                for item in results
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

    # --------------------------------------------------------
    # PRE-DOWNLOAD SIZE CHECK
    # --------------------------------------------------------

    known_size = selected.get(
        "size"
    )

    if (
        not is_admin(user_id)
        and known_size is not None
        and known_size > SAFE_FILE_BYTES
    ):

        if selection == "auto":

            # Auto should normally never choose this,
            # but keep a safe guard.
            await callback.message.edit_text(
                TEXT[language]["too_large"],
                reply_markup=back_keyboard(
                    language
                ),
            )

        elif (
            selected["quality"]
            == "360"
        ):

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
                    results,
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
        prefix="video_downloader_"
    )

    try:

        # No max_bytes for admin.
        max_bytes = (
            None
            if is_admin(user_id)
            else SAFE_FILE_BYTES
        )

        media_path, size_bytes = (
            await asyncio.to_thread(
                stream_download_sync,
                selected["url"],
                temp_dir,
                max_bytes,
            )
        )

        # ----------------------------------------------------
        # FILE EXCEEDED LIMIT DURING DOWNLOAD
        # ----------------------------------------------------

        if media_path is None:

            await add_download(
                user_id,
                selected.get(
                    "filename"
                ) or "Video",
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
                        results,
                    ),
                )

            return

        # ----------------------------------------------------
        # SEND
        # ----------------------------------------------------

        await status.edit_text(
            TEXT[language]["sending"]
        )

        filename = (
            selected.get(
                "filename"
            )
            or "video.mp4"
        )

        document = FSInputFile(
            str(media_path),
            filename=filename,
        )

        await bot.send_document(
            chat_id=user_id,
            document=document,
            caption=(
                f"🎬 <b>{esc(filename)}</b>\n"
                f"Quality: "
                f"{esc(selected['quality'])}p\n"
                f"Size: "
                f"{fmt_bytes(size_bytes)}"
            ),
        )

        await add_download(
            user_id,
            filename,
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
            "Download/send failed for user %s",
            user_id,
        )

        try:

            await add_download(
                user_id,
                selected.get(
                    "filename"
                ) or "Video",
                pending["url"],
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

        # The video never remains in permanent storage.
        shutil.rmtree(
            temp_dir,
            ignore_errors=True,
        )

        PENDING.pop(
            user_id,
            None,
        )

        if not is_admin(
            user_id
        ):

            ACTIVE.discard(
                user_id
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

    user_id = (
        callback.from_user.id
    )

    row = await get_user(
        user_id
    )

    language = (
        row["language"]
        if row
        else "ru"
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
            hash_user(
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

    for row in rows:

        title = (
            decrypt(
                row["title_encrypted"]
            )
            or "Video"
        )

        size = fmt_bytes(
            int(
                row["size_bytes"]
            )
        )

        lines.append(
            f"🎬 <b>{esc(title[:70])}</b>\n"
            f"{esc(str(row['quality']))}p"
            f" • {size}\n"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"🗑 {title[:30]}",
                    callback_data=(
                        f"history_delete:"
                        f"{row['id']}"
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
            hash_user(
                user_id
            ),
        )

    row = await get_user(
        user_id
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
async def history_clear(
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
            hash_user(
                user_id
            ),
        )

    row = await get_user(
        user_id
    )

    language = (
        row["language"]
        if row
        else "ru"
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
        "Telegram/API and infrastructure limits "
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
        f"Failed downloads: {failed}\n"
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

        if len(text) + len(block) > 3500:

            await message.answer(
                text
            )

            text = ""

        text += block

    if text:

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
        f"ID: <code>{target_id}</code>\n"
        f"Name: {esc(first_name)}\n"
        f"Username: @{esc(username)}\n"
        f"Language: {row['language']}\n"
        f"Downloads: {row['downloads_count']}\n"
        f"Errors: {row['failed_count']}\n"
        f"Transferred: "
        f"{fmt_bytes(int(row['total_bytes']))}\n"
        f"Blocked: {row['is_blocked']}\n"
        f"Created: {row['created_at']}\n"
        f"Last activity: {row['last_activity']}"
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
                row["title_encrypted"]
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

    async with DB_POOL.acquire() as conn:

        rows = await conn.fetch(
            """
            SELECT telegram_id_encrypted
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
