# 📥 Telegram Video Downloader Bot (VLESS + REALITY)

A Telegram bot that downloads videos from **YouTube**, **TikTok** and
**Instagram Reels** in different qualities as **MP4** (video) or **MP3** (audio).

All download traffic is routed through a **VLESS + REALITY** proxy (an
[xray-core](https://github.com/XTLS/Xray-core) sidecar) so social networks stay
reachable from restricted networks. The proxy can be **disabled via the `.env`
file** for a direct connection.

Built with [pyTelegramBotAPI (Telebot)](https://github.com/eternnoir/pyTelegramBotAPI)
and [yt-dlp](https://github.com/yt-dlp/yt-dlp). Ships as a `docker compose` stack.

---

## Architecture

```
Telegram ──▶ [ bot ] ──(SOCKS5 socks5h://xray:10808)──▶ [ xray ] ──VLESS+REALITY──▶ Internet
                │                                                                    (YouTube,
             yt-dlp + ffmpeg                                                          TikTok, IG)
```

- **xray** container: reads `VLESS_*` vars from `.env`, renders its config from a
  template, dials the server via VLESS+REALITY, and exposes a SOCKS5 proxy on
  `10808` (and an HTTP proxy on `10809`) on the internal docker network only.
- **bot** container: `yt-dlp` points at `socks5h://xray:10808`. `socks5h` makes
  DNS resolve on the proxy side too.

---

## Setup

1. **Create the bot** with [@BotFather](https://t.me/BotFather) and copy the token.

2. **Configure env:**
   ```bash
   cp .env.example .env
   ```
   Edit `.env`:
   - `BOT_TOKEN` — your token from BotFather.
   - `VLESS_*` — your VLESS+REALITY server credentials (address, port, UUID,
     SNI, public key / `pbk`, short id, flow, fingerprint).
   - `ALLOWED_USER_IDS` — **required for access**. The bot is deny-by-default:
     only the numeric ids you list here can use it (see "Access control" below).

3. **Run:**
   ```bash
   docker compose up -d --build
   ```

4. **Logs:**
   ```bash
   docker compose logs -f bot
   docker compose logs -f xray
   ```

---

## Disabling the proxy

Set in `.env`:

```env
PROXY_ENABLED=false
```

The bot then connects directly (no proxy for yt-dlp). You can also stop the
xray container: `docker compose stop xray` — the bot no longer depends on it
at runtime once the proxy is off.

Extra toggle: `TELEGRAM_USE_PROXY=true` also routes the Telegram Bot API traffic
through the proxy (only needed if Telegram itself is blocked for you).

---

## Access control (only your users)

The bot is **deny-by-default**:

```env
ALLOW_ALL_USERS=false
ALLOWED_USER_IDS=123456789,987654321
```

- Only the numeric ids listed can use the bot; everyone else is refused and the
  attempt is logged (`Rejected user id=… username=…`).
- An **empty** `ALLOWED_USER_IDS` with `ALLOW_ALL_USERS=false` means **nobody**
  can use it (fail-safe).
- To find an id: send the bot any message — the refusal reply includes the
  sender's id — or use [@userinfobot](https://t.me/userinfobot).
- Set `ALLOW_ALL_USERS=true` only if you deliberately want it public.

The gate is enforced on **every** entry point (`/start`, links, and the inline
quality buttons), so a stranger can't trigger a download via a stale button.

---

## Usage

1. Send the bot a link (YouTube / TikTok / Instagram Reel).
2. Pick a format from the inline buttons:
   - 🎵 **MP3** — audio only (192 kbps)
   - 📹 **144p / 360p / 480p / 720p / 1080p / Best** — MP4 video
3. The bot downloads (through the proxy) and sends the file back.

---

## Notes & limits

- **File size:** the plain Telegram Bot API caps uploads at **50 MB**
  (`MAX_FILE_SIZE_MB`). For larger files, run a
  [local Bot API server](https://github.com/tdlib/telegram-bot-api) (up to 2 GB)
  and raise the limit. The bot warns and refuses to send oversized files.
- Only single videos are downloaded (`noplaylist`).
- Instagram may require login for some content; public Reels generally work.
  To use cookies, mount a `cookies.txt` into the bot container and add
  `"cookiefile"` to the yt-dlp options in `bot/downloader.py`.
- Respect each platform's Terms of Service and copyright. Download only content
  you have the right to.

---

## Project layout

```
.
├── docker-compose.yml
├── .env.example
├── README.md
├── bot/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── config.py        # env parsing + proxy URL
│   ├── downloader.py    # yt-dlp wrapper (quality presets, proxy, mp3/mp4)
│   └── bot.py           # Telebot handlers
└── xray/
    ├── Dockerfile
    ├── config.json.template
    └── entrypoint.sh    # renders config from env, starts xray
```
