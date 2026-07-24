"""Runtime configuration read from environment variables."""
import os


def _get_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

MAX_FILE_SIZE_MB = _get_int("MAX_FILE_SIZE_MB", 50)
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

# Allow-list of Telegram user ids.
#   ALLOW_ALL_USERS=false (default) => ONLY ids in ALLOWED_USER_IDS may use the bot.
#                                      An empty list therefore means "nobody".
#   ALLOW_ALL_USERS=true            => open to everyone (use with care).
ALLOW_ALL_USERS = _get_bool("ALLOW_ALL_USERS", False)
_raw_ids = os.getenv("ALLOWED_USER_IDS", "").strip()
ALLOWED_USER_IDS = {
    int(x) for x in _raw_ids.replace(" ", "").split(",") if x
} if _raw_ids else set()

# --- Local Telegram Bot API server --------------------------------------------
# When true, the bot talks to a self-hosted telegram-bot-api server (reachable
# on the docker network) instead of api.telegram.org. That server's traffic is
# tunnelled to Telegram through xray via tun2socks, and it raises the upload
# limit from 50 MB to ~2000 MB.
USE_LOCAL_BOT_API = _get_bool("USE_LOCAL_BOT_API", True)
LOCAL_BOT_API_URL = os.getenv("LOCAL_BOT_API_URL", "http://tun2socks:8081").strip().rstrip("/")

# --- Proxy (VLESS + REALITY via xray sidecar) ---------------------------------
PROXY_ENABLED = _get_bool("PROXY_ENABLED", True)
# Only relevant when NOT using the local bot-api server (i.e. talking to
# api.telegram.org directly through the SOCKS proxy).
TELEGRAM_USE_PROXY = _get_bool("TELEGRAM_USE_PROXY", False)
PROXY_HOST = os.getenv("PROXY_HOST", "xray").strip()
PROXY_PORT = _get_int("PROXY_PORT", 10808)

# SOCKS5 URL for the local xray proxy, or None when disabled.
PROXY_URL = f"socks5h://{PROXY_HOST}:{PROXY_PORT}" if PROXY_ENABLED else None


def summary() -> str:
    lines = [
        f"local_bot_api={USE_LOCAL_BOT_API} ({LOCAL_BOT_API_URL if USE_LOCAL_BOT_API else 'api.telegram.org'})",
        f"proxy_enabled={PROXY_ENABLED}",
        f"proxy_url={PROXY_URL}",
        f"telegram_use_proxy={TELEGRAM_USE_PROXY}",
        f"max_file_size_mb={MAX_FILE_SIZE_MB}",
        f"access={'ALL (open!)' if ALLOW_ALL_USERS else sorted(ALLOWED_USER_IDS) or 'NOBODY'}",
    ]
    return " | ".join(lines)
