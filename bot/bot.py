"""Telegram bot: download YouTube / TikTok / Instagram Reels as MP4 or MP3.

All media traffic is routed through a VLESS+REALITY proxy (xray sidecar)
unless disabled via the PROXY_ENABLED env flag.
"""
import os
import re
import time
import shutil
import logging
import threading

import telebot
from telebot import apihelper
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

import config
import downloader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("bot")

if not config.BOT_TOKEN:
    raise SystemExit("BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")

# Route Telegram API traffic through the proxy if requested.
if config.PROXY_ENABLED and config.TELEGRAM_USE_PROXY and config.PROXY_URL:
    apihelper.proxy = {"https": config.PROXY_URL}
    log.info("Telegram API traffic routed through proxy %s", config.PROXY_URL)

bot = telebot.TeleBot(config.BOT_TOKEN, parse_mode="HTML")

URL_RE = re.compile(r"https?://[^\s]+")

SUPPORTED_HINT = (
    "youtube.com", "youtu.be", "tiktok.com", "instagram.com",
    "instagr.am", "vt.tiktok.com",
)

# Short-lived store: token -> url  (callback_data is limited to 64 bytes).
_pending: dict[str, tuple[str, float]] = {}
_pending_lock = threading.Lock()
_PENDING_TTL = 60 * 30  # 30 minutes


def _put_url(url: str) -> str:
    token = os.urandom(6).hex()
    with _pending_lock:
        _prune_pending()
        _pending[token] = (url, time.time())
    return token


def _get_url(token: str) -> str | None:
    with _pending_lock:
        item = _pending.get(token)
        return item[0] if item else None


def _prune_pending() -> None:
    now = time.time()
    stale = [k for k, (_, ts) in _pending.items() if now - ts > _PENDING_TTL]
    for k in stale:
        _pending.pop(k, None)


def _authorized(user_id: int) -> bool:
    if config.ALLOW_ALL_USERS:
        return True
    # Deny-by-default: only explicitly listed ids are allowed.
    return user_id in config.ALLOWED_USER_IDS


def _quality_keyboard(token: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=3)
    buttons = [
        InlineKeyboardButton(label, callback_data=f"dl|{key}|{token}")
        for key, (label, _fmt, _audio) in downloader.QUALITY_PRESETS.items()
    ]
    kb.add(*buttons)
    return kb


# --------------------------------------------------------------------------- #
#  Handlers
# --------------------------------------------------------------------------- #
@bot.message_handler(commands=["start", "help"])
def on_start(message):
    if not _authorized(message.from_user.id):
        bot.reply_to(
            message,
            f"⛔ You are not allowed to use this bot.\n"
            f"Your user id is <code>{message.from_user.id}</code>.",
        )
        return
    bot.reply_to(
        message,
        "👋 <b>Video downloader bot</b>\n\n"
        "Send me a link from <b>YouTube</b>, <b>TikTok</b> or "
        "<b>Instagram Reels</b> and I'll offer you download options "
        "(different qualities as MP4, or MP3 audio).\n\n"
        f"⚠️ Max file size: <b>{config.MAX_FILE_SIZE_MB} MB</b> "
        "(Telegram limit for bots).",
    )


@bot.message_handler(func=lambda m: True, content_types=["text"])
def on_text(message):
    if not _authorized(message.from_user.id):
        log.warning(
            "Rejected user id=%s username=@%s",
            message.from_user.id, message.from_user.username,
        )
        bot.reply_to(
            message,
            f"⛔ You are not allowed to use this bot.\n"
            f"Your user id is <code>{message.from_user.id}</code> — "
            "ask the owner to add it.",
        )
        return

    match = URL_RE.search(message.text or "")
    if not match:
        bot.reply_to(message, "Please send me a video link. /help for details.")
        return

    url = match.group(0)
    if not any(h in url.lower() for h in SUPPORTED_HINT):
        bot.reply_to(
            message,
            "That doesn't look like a supported link.\n"
            "Supported: YouTube, TikTok, Instagram Reels.",
        )
        return

    token = _put_url(url)
    bot.reply_to(
        message,
        "Choose a format / quality:",
        reply_markup=_quality_keyboard(token),
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("dl|"))
def on_choice(call):
    if not _authorized(call.from_user.id):
        bot.answer_callback_query(call.id, "Not allowed.")
        return

    try:
        _, quality_key, token = call.data.split("|", 2)
    except ValueError:
        bot.answer_callback_query(call.id, "Bad request.")
        return

    url = _get_url(token)
    if not url:
        bot.answer_callback_query(call.id, "This request expired. Send the link again.")
        return

    bot.answer_callback_query(call.id)
    label = downloader.QUALITY_PRESETS[quality_key][0]
    status = bot.edit_message_text(
        f"⏳ Downloading <b>{label}</b>…",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
    )

    # Run the (blocking) download in a worker thread so polling stays responsive.
    threading.Thread(
        target=_do_download,
        args=(call.message.chat.id, status.message_id, url, quality_key),
        daemon=True,
    ).start()


def _do_download(chat_id: int, status_id: int, url: str, quality_key: str):
    is_audio = downloader.QUALITY_PRESETS[quality_key][2]
    path = None
    try:
        path = downloader.download(url, quality_key)
        size = os.path.getsize(path)

        if size > config.MAX_FILE_SIZE_BYTES:
            bot.edit_message_text(
                f"❌ File is {size / 1024 / 1024:.1f} MB, which exceeds the "
                f"{config.MAX_FILE_SIZE_MB} MB limit.\n"
                "Try a lower quality or MP3.",
                chat_id=chat_id,
                message_id=status_id,
            )
            return

        bot.edit_message_text(
            "📤 Uploading…", chat_id=chat_id, message_id=status_id
        )

        with open(path, "rb") as fh:
            if is_audio:
                bot.send_audio(chat_id, fh)
            else:
                bot.send_video(chat_id, fh, supports_streaming=True)

        bot.delete_message(chat_id, status_id)

    except downloader.DownloadError as e:
        log.warning("Download failed: %s", e)
        bot.edit_message_text(
            f"❌ Download failed:\n<code>{_short(str(e))}</code>",
            chat_id=chat_id,
            message_id=status_id,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("Unexpected error")
        bot.edit_message_text(
            f"❌ Unexpected error:\n<code>{_short(str(e))}</code>",
            chat_id=chat_id,
            message_id=status_id,
        )
    finally:
        if path:
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)


def _short(text: str, limit: int = 300) -> str:
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    return text if len(text) <= limit else text[:limit] + "…"


def main():
    log.info("Config: %s", config.summary())
    if config.ALLOW_ALL_USERS:
        log.warning("ALLOW_ALL_USERS=true — the bot is OPEN to everyone!")
    elif not config.ALLOWED_USER_IDS:
        log.warning(
            "No ALLOWED_USER_IDS set and ALLOW_ALL_USERS=false — "
            "NOBODY can use the bot. Add your user id to ALLOWED_USER_IDS in .env."
        )
    log.info("Bot starting (polling)…")
    bot.infinity_polling(timeout=30, long_polling_timeout=30)


if __name__ == "__main__":
    main()
