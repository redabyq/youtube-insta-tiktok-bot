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

# --- Proxy (VLESS + REALITY via xray sidecar) ---------------------------------
PROXY_ENABLED = _get_bool("PROXY_ENABLED", True)
TELEGRAM_USE_PROXY = _get_bool("TELEGRAM_USE_PROXY", False)
PROXY_HOST = os.getenv("PROXY_HOST", "xray").strip()
PROXY_PORT = _get_int("PROXY_PORT", 10808)

# SOCKS5 URL for the local xray proxy, or None when disabled.
PROXY_URL = f"socks5h://{PROXY_HOST}:{PROXY_PORT}" if PROXY_ENABLED else None


def summary() -> str:
    lines = [
        f"proxy_enabled={PROXY_ENABLED}",
        f"proxy_url={PROXY_URL}",
        f"telegram_use_proxy={TELEGRAM_USE_PROXY}",
        f"max_file_size_mb={MAX_FILE_SIZE_MB}",
        f"access={'ALL (open!)' if ALLOW_ALL_USERS else sorted(ALLOWED_USER_IDS) or 'NOBODY'}",
    ]
    return " | ".join(lines)
