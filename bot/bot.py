"""Telegram bot: download YouTube / TikTok / Instagram Reels as MP4 or MP3.

All media traffic is routed through a VLESS+REALITY proxy (xray sidecar)
unless disabled via the PROXY_ENABLED env flag.
"""
import os
import re
import time
import socket
import shutil
import logging
import threading
from urllib.parse import urlparse

import requests
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

if config.USE_LOCAL_BOT_API:
    # Talk to the self-hosted telegram-bot-api server instead of api.telegram.org.
    # That server reaches Telegram through xray (via tun2socks), so the bot does
    # NOT need its own proxy for Telegram here.
    apihelper.API_URL = config.LOCAL_BOT_API_URL + "/bot{0}/{1}"
    apihelper.FILE_URL = config.LOCAL_BOT_API_URL
    log.info("Using local Bot API server at %s", config.LOCAL_BOT_API_URL)
elif config.PROXY_ENABLED and config.TELEGRAM_USE_PROXY and config.PROXY_URL:
    # Talking to api.telegram.org directly -> route it through the SOCKS proxy.
    apihelper.proxy = {"https": config.PROXY_URL}
    log.info("Telegram API traffic routed through proxy %s", config.PROXY_URL)

bot = telebot.TeleBot(config.BOT_TOKEN, parse_mode="HTML")

# Seconds to allow for uploading a media file to Telegram.
UPLOAD_TIMEOUT = 900

URL_RE = re.compile(r"https?://[^\s]+")

SUPPORTED_HINT = (
    "youtube.com", "youtu.be", "tiktok.com", "instagram.com",
    "instagr.am", "vt.tiktok.com",
    "pornhub.com", "pornhub.org",
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
            f"⛔ Вы не можете использовать этого бота.\n"
            f"Ваш Telegram ID: <code>{message.from_user.id}</code>.",
        )
        return
    bot.reply_to(
        message,
        "👋 <b>Бот для скачивания видео!</b>\n\n"
        "Отправьте мне ссылку на видео из <b>YouTube</b>, <b>TikTok</b> или на"
        "<b>Instagram Reels</b> и вам будут доступна функция скачивания в разном формате"
        "(разные качества MP4, или MP3 аудио).\n\n"
        f"⚠️ Max file size: <b>{config.MAX_FILE_SIZE_MB} MB</b> "
        "",
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
            f"⛔ Вы не можете использовать этого бота.\n"
            f"Ваш Telegram ID: <code>{message.from_user.id}</code> — "
            "",
        )
        return

    match = URL_RE.search(message.text or "")
    if not match:
        bot.reply_to(message, "Отправьте мне ссылку на видео. /help для помощи.")
        return

    url = match.group(0)
    if not any(h in url.lower() for h in SUPPORTED_HINT):
        bot.reply_to(
            message,
            "Работа с данным сервисом не поддерживается.\n"
            "Поддерживаемые сервисы: YouTube, TikTok, Instagram Reels.",
        )
        return

    token = _put_url(url)
    bot.reply_to(
        message,
        "Выберите формат / качество:",
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
        bot.answer_callback_query(call.id, "Запрос не обработан. Отправьте ссылку снова")
        return

    bot.answer_callback_query(call.id)
    label = downloader.QUALITY_PRESETS[quality_key][0]
    status = bot.edit_message_text(
        f"⏳ Скачиваю <b>{label}</b>…",
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
                f"❌ Размер файла {size / 1024 / 1024:.1f} MB, что превышает лимит в "
                f"{config.MAX_FILE_SIZE_MB} MB.\n"
                "Попробуйте качество поменьше или формат MP3.",
                chat_id=chat_id,
                message_id=status_id,
            )
            return

        bot.edit_message_text(
            "📤 Отправляю…", chat_id=chat_id, message_id=status_id
        )

        # Large files (up to 2 GB via the local server) need a generous timeout.
        with open(path, "rb") as fh:
            if is_audio:
                bot.send_audio(chat_id, fh, timeout=UPLOAD_TIMEOUT)
            else:
                bot.send_video(
                    chat_id, fh, supports_streaming=True, timeout=UPLOAD_TIMEOUT
                )

        bot.delete_message(chat_id, status_id)

    except downloader.DownloadError as e:
        log.warning("Download failed: %s", e)
        bot.edit_message_text(
            f"❌ Загрузка не завершена:\n<code>{_short(str(e))}</code>",
            chat_id=chat_id,
            message_id=status_id,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("Unexpected error")
        bot.edit_message_text(
            f"❌ Неожиданная ошибка:\n<code>{_short(str(e))}</code>",
            chat_id=chat_id,
            message_id=status_id,
        )
    finally:
        if path:
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)


def _short(text: str, limit: int = 300) -> str:
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    return text if len(text) <= limit else text[:limit] + "…"


def wait_for_local_api(max_wait: int = 180) -> None:
    """Block until the local Bot API server's TCP port is open.

    We only check that the server is *listening* (a plain TCP connect), NOT that
    getMe succeeds — getMe requires a round-trip to Telegram through the tunnel,
    so using it here would conflate "server down" with "tunnel down". Once the
    port is open, infinity_polling handles Telegram-side retries itself.
    """
    if not config.USE_LOCAL_BOT_API:
        return

    parsed = urlparse(config.LOCAL_BOT_API_URL)
    host, port = parsed.hostname, parsed.port or 80
    deadline = time.time() + max_wait
    attempt = 0
    while True:
        attempt += 1
        try:
            with socket.create_connection((host, port), timeout=5):
                log.info("Local Bot API server is listening on %s:%s", host, port)
                return
        except OSError as e:
            if time.time() > deadline:
                raise SystemExit(
                    f"Local Bot API server not listening on {host}:{port} after "
                    f"{max_wait}s ({e}).\n"
                    "Check it with:  docker compose logs telegram-bot-api\n"
                    "Common causes: TELEGRAM_API_ID / TELEGRAM_API_HASH missing/wrong."
                )
            if attempt == 1 or attempt % 5 == 0:
                log.info("Waiting for local Bot API server to listen…")
            time.sleep(3)


def self_test_proxy() -> None:
    """Best-effort check that the xray VLESS tunnel actually reaches the internet.

    Fetches our external IP through socks5h://xray:10808. If this succeeds, xray
    + VLESS work; if the bot then still can't reach Telegram, the fault is in
    tun2socks (which telegram-bot-api relies on), not xray.
    """
    if not config.PROXY_URL:
        log.info("Proxy disabled — skipping tunnel self-test.")
        return
    proxies = {"http": config.PROXY_URL, "https": config.PROXY_URL}
    try:
        r = requests.get("https://api.ipify.org", proxies=proxies, timeout=15)
        log.info("✅ Tunnel OK — external IP via xray/VLESS: %s", r.text.strip())
    except requests.RequestException as e:
        log.error(
            "❌ Tunnel self-test FAILED through %s (%s: %s). "
            "xray/VLESS is likely misconfigured or the server is unreachable — "
            "check `docker compose logs xray`.",
            config.PROXY_URL, type(e).__name__, e,
        )


def main():
    log.info("Config: %s", config.summary())
    if config.ALLOW_ALL_USERS:
        log.warning("ALLOW_ALL_USERS=true — the bot is OPEN to everyone!")
    elif not config.ALLOWED_USER_IDS:
        log.warning(
            "No ALLOWED_USER_IDS set and ALLOW_ALL_USERS=false — "
            "NOBODY can use the bot. Add your user id to ALLOWED_USER_IDS in .env."
        )
    self_test_proxy()
    wait_for_local_api()
    log.info("Bot starting (polling)…")
    bot.infinity_polling(timeout=30, long_polling_timeout=30)


if __name__ == "__main__":
    main()
