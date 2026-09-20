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

Proxy mode uses four containers; direct mode uses only the bot and local API:

```
                 ┌──────────────────────────── downloads (yt-dlp) ───────────────────────────┐
                 │                                                                            ▼
  user ◀─▶ [ bot ] ──── Bot API calls ───▶ [ telegram-bot-api ]                          [ xray ]
                                            (shares netns of)  ╲                     VLESS+REALITY
                                                                ╲                            │
                                                             [ tun2socks ] ─ SOCKS5 ─────────┘
                                                                                             │
                                                                                             ▼
                                                                             Telegram DCs / YouTube / TikTok / IG
```

- **xray** — dials the server via VLESS+REALITY, exposes a SOCKS5 proxy on
  `10808` (HTTP on `10809`) on the internal network only.
- **tun2socks** — creates a TUN device and forwards everything in its network
  namespace to `socks5://xray:10808`.
- **telegram-bot-api** — a self-hosted [Bot API server](https://github.com/tdlib/telegram-bot-api)
  in *local* mode (uploads up to ~2000 MB). In proxy mode it runs with
  `network_mode: service:tun2socks`, so **all its traffic to Telegram is
  tunnelled through VLESS+REALITY**. This is required because the Bot API server
  has no proxy option for its own connection to Telegram — only network-level
  tunnelling works.
- **bot** — pyTelegramBotAPI + yt-dlp + ffmpeg. Talks to the local Bot API
  server at `http://tun2socks:8081`, and downloads media via `socks5h://xray:10808`.

> Why the local server? Your host reported `api.telegram.org … Network is
> unreachable`. Routing Telegram through the tunnel (and getting 2 GB uploads)
> is exactly what the local server + tun2socks solves.

---

## Setup

1. **Create the bot** with [@BotFather](https://t.me/BotFather) and copy the token.

2. **Configure env:**
   ```bash
   cp .env.example .env
   ```
   Edit `.env`:
   - `BOT_TOKEN` — your token from BotFather.
   - `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` — **required by the local Bot API
     server**. Get them at [my.telegram.org](https://my.telegram.org) → *API
     development tools*. (These are not the bot token.)
   - `VLESS_*` — your VLESS+REALITY server credentials (address, port, UUID,
     SNI, public key / `pbk`, short id, flow, fingerprint).
   - `ALLOWED_USER_IDS` — **required for access**. The bot is deny-by-default:
     only the numeric ids you list here can use it (see "Access control" below).

3. **If the bot was ever used against the cloud API**, log it out once so it can
   move to the local server (skip for a brand-new bot):
   ```bash
   curl "https://api.telegram.org/bot<YOUR_TOKEN>/logOut"
   ```
   Run this from any machine that *can* reach Telegram. After a successful
   `logOut`, the bot may only be used on your local server for ~10 minutes'
   cooldown, then start the stack.

5. **Run:**
   ```bash
   python compose.py up -d --build
   ```

6. **Logs:**
   ```bash
   python compose.py logs -f bot
   python compose.py logs -f telegram-bot-api
   python compose.py logs -f tun2socks
   python compose.py logs -f xray
   ```

---

## Local vs. cloud Bot API

Controlled by `USE_LOCAL_BOT_API` in `.env`:

- **`true` (default)** — bot uses the self-hosted `telegram-bot-api` server via
  tun2socks. Needed when `api.telegram.org` is unreachable from your host, and
  raises the upload limit to **2000 MB**. Requires `TELEGRAM_API_ID` /
  `TELEGRAM_API_HASH`.
- **`false`** — bot talks to `api.telegram.org` directly. Set
  `TELEGRAM_USE_PROXY=true` to route those calls through the SOCKS proxy if
  Telegram is blocked. Upload limit is **50 MB**. (The `tun2socks` and
  `telegram-bot-api` containers can then be removed/stopped.)

## Disabling the proxy

Set `PROXY_ENABLED=0` (or `false`) in `.env`, then run:

```bash
python compose.py up -d --build
```

The launcher reads `.env` through Docker Compose and selects the matching
configuration. Direct mode starts no xray or tun2socks containers and has no
proxy health dependencies. The local Bot API connects directly to Telegram.
`USE_LOCAL_BOT_API=false` also omits the local API server. Switching modes
removes obsolete containers automatically, retaining the API data volume.
Python 3.10+ and Docker Compose are required on the host.

Use `python compose.py` in place of `docker compose` for subsequent commands.
Plain `docker compose up` always uses the direct configuration; it does not
select proxy mode. For hosts without Python, choose `docker-compose.yml` for
direct mode or `docker-compose.proxy.yml` for proxy mode explicitly with `-f`.

### Authentication and unavailable videos

Place Netscape-format cookie exports in the host `cookies/` directory, which
is mounted read-only. For example:

```env
YOUTUBE_COOKIES_FILE=/cookies/youtube.txt
INSTAGRAM_COOKIES_FILE=/cookies/instagram.txt
```

`COOKIES_FILE` remains a shared fallback. Restart the bot after changing env
settings. Each request copies its cookie file privately so concurrent requests
do not modify your exports. Cookies are credentials; they are ignored by Git.

The image includes Deno for YouTube JavaScript challenges and uses yt-dlp's
maintained default clients. Login checks can still require fresh cookies or
a different server IP. Deleted, private, or region-restricted videos cannot
be made universally downloadable. The bot now returns short actionable
messages instead of raw extractor instructions.

MP3 files include title metadata and embedded cover art when a thumbnail is
available, plus a Telegram audio preview. A failed cover conversion does not
prevent delivery of the audio.

---

## Speed / performance

Everything goes through the VLESS tunnel, so throughput is capped by your
server. Levers, in order of impact:

1. **`CONCURRENT_FRAGMENTS`** (`.env`) — parallel fragment downloads in yt-dlp.
   The biggest win for YouTube. Default 8; try 12–16 on a fast link.
2. **VLESS server location & bandwidth** — the dominant factor. A closer,
   less-loaded server is faster. All creds live in `.env`
   (`VLESS_ADDRESS`, `VLESS_PORT`, `VLESS_UUID`, `VLESS_SNI`,
   `VLESS_PUBLIC_KEY`, `VLESS_SHORT_ID`) — swap them and `python compose.py up -d`
   to point at a different/faster server. No rebuild needed for xray.
3. **`VLESS_FLOW=xtls-rprx-vision`** (default) — keep this. XTLS-Vision avoids
   double TLS encryption and is markedly faster than an empty flow.
4. **tun2socks `TCP_SNDBUF` / `TCP_RCVBUF`** (compose, default `4m`) — larger
   buffers fill a high-latency link's bandwidth-delay product; raise to `8m`
   if uploads to Telegram are the bottleneck.
5. **Pick a lower quality** — 720p downloads/sends far faster than 1080p/Best.

The download (yt-dlp → xray) and the Telegram upload (tun2socks → xray) are
independent paths — if only one feels slow, tune the matching lever above.

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

- **File size:** with the bundled local Bot API server, uploads go up to
  **~2000 MB** (`MAX_FILE_SIZE_MB`). On the cloud API the hard limit is 50 MB.
  The bot warns and refuses to send oversized files rather than crashing.
- **tun2socks** needs the `NET_ADMIN` capability and `/dev/net/tun`. Most hosts
  (incl. Coolify) allow this; if `/dev/net/tun` is missing, load the module on
  the host with `sudo modprobe tun`.
- Only single videos are downloaded (`noplaylist`).
- **Keep yt-dlp fresh.** YouTube/TikTok change their internals constantly, and a
  stale yt-dlp is the top cause of `HTTP Error 403: Forbidden` (YouTube) and
  `Unexpected response from webpage request` (TikTok). yt-dlp is left unpinned
  and the Dockerfile force-upgrades it to the newest release on each build, so if
  downloads start failing, just rebuild to pull the latest fix:
  ```bash
  python compose.py build --no-cache bot && python compose.py up -d bot
  ```
  (or bump `YTDLP_REFRESH` in `bot/Dockerfile` to bust the cache).
- YouTube uses yt-dlp's default player clients and retries media 403s once
  with Safari. The Docker image includes the JavaScript runtime required by
  current yt-dlp. See [yt-dlp EJS setup](https://github.com/yt-dlp/yt-dlp/wiki/EJS).
- For Instagram login walls and YouTube account checks, configure cookie
  exports as described above. See [yt-dlp cookie guidance](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp).
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
│   ├── config.py        # env parsing + proxy / local-API config
│   ├── downloader.py    # yt-dlp wrapper (quality presets, proxy, mp3/mp4)
│   └── bot.py           # Telebot handlers
└── xray/
    ├── Dockerfile       # FROM teddysun/xray (no build-time download)
    └── entrypoint.sh    # renders config.json from env, starts xray
```
