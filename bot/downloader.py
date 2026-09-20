"""Video/audio downloading via yt-dlp, routed through the VLESS proxy."""
import os
import glob
import logging
import tempfile
import shutil
import subprocess
from urllib.parse import urlparse

import yt_dlp

import config

log = logging.getLogger("downloader")

# Browser TLS-fingerprint impersonation (via curl_cffi) lets yt-dlp bypass
# 403/bot-detection walls. Resolve a target once; None if unavailable.
try:
    from yt_dlp.networking.impersonate import ImpersonateTarget
    _IMPERSONATE = ImpersonateTarget("chrome")
except Exception:  # noqa: BLE001
    _IMPERSONATE = None

# Let yt-dlp choose maintained clients, including cookie-aware defaults.
_YT_PLAYER_CLIENTS = ["default"]
# Best-effort alternative for media 403s; cannot bypass access restrictions.
_YT_PLAYER_CLIENTS_FALLBACK = ["web_safari"]

# TikTok gates some posts ("This post may not be comfortable for some
# audiences. Log in for access.") behind a login wall on its *web* API. The
# mobile-app API serves the same posts without that interstitial, so when we hit
# the wall we retry pointing yt-dlp's TikTok extractor at the app API. Values
# mirror yt-dlp's own documented app-API preset; they drift over time, so this
# is best-effort; authenticated cookies may still be necessary.
_TIKTOK_APP_ARGS = {
    "tiktok": {
        "api_hostname": ["api22-normal-c-useast2a.tiktokv.com"],
        "app_name": ["trill"],
        "app_version": ["34.1.2"],
        "manifest_app_version": ["2023401020"],
        "aid": ["1180"],
    }
}


def _extractor_args(clients=None) -> dict:
    return {"youtube": {"player_client": clients or _YT_PLAYER_CLIENTS}}


def _is_youtube(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in ("youtube.com", "youtu.be") or host.endswith(".youtube.com")


def _is_tiktok(url: str) -> bool:
    return "tiktok" in url.lower()


def _looks_forbidden(msg: str) -> bool:
    m = msg.lower()
    return "403" in m or "forbidden" in m


def _looks_login_required(msg: str) -> bool:
    m = msg.lower()
    return (
        "log in for access" in m
        or "may not be comfortable" in m
        or "use --cookies" in m
    )


class DownloadError(Exception):
    pass


def friendly_error(error: Exception) -> str:
    message = str(error).lower()
    if "empty media response" in message:
        return ("Instagram не предоставил видео. Проверьте доступность публикации "
                "и обновите cookies Instagram на сервере бота.")
    if any(s in message for s in ("sign in", "log in", "login", "use --cookies", "bot", "age-restricted")):
        return ("Сервис требует входа или проверки аккаунта. Администратору нужно "
                "добавить или обновить cookies для этого сервиса. Доступ также может зависеть от IP сервера.")
    if any(s in message for s in ("not available", "unavailable", "private video", "removed", "not found")):
        return ("Видео недоступно: возможно, оно удалено, приватное или ограничено "
                "для региона/аккаунта. Проверьте ссылку и доступ к видео в браузере.")
    if _looks_forbidden(message):
        return "Сервис отклонил скачивание (403). Обновите yt-dlp и cookies; проверьте доступ с IP сервера."
    return "Не удалось скачать видео. Попробуйте позже; подробности доступны администратору в журнале."


class _YDLLogger:
    def debug(self, message):
        log.debug(message)

    def warning(self, message):
        log.debug(message)

    def error(self, message):
        # The caller reports the final failure after any recovery attempt.
        log.debug(message)


# Quality presets. Key -> (label, max_height | None, is_audio)
# The actual yt-dlp format string is built per-URL by _build_format() so we can
# use a platform-appropriate selector (see the TikTok note there).
QUALITY_PRESETS = {
    "mp3": ("🎵 MP3 (audio)", None, True),
    "144": ("📹 144p", 144, False),
    "360": ("📹 360p", 360, False),
    "480": ("📹 480p", 480, False),
    "720": ("📹 720p", 720, False),
    "1080": ("📹 1080p", 1080, False),
    "best": ("📹 Best available", None, False),
}


def _build_format(height, url: str) -> str:
    """Build a yt-dlp format selector, guaranteeing the result has audio.

    TikTok quirk (verified with ffprobe): TikTok's HEVC/``bytevc1`` (vcodec
    ``h265``) renditions are effectively *video-only* — they carry no audio
    stream even though yt-dlp's format listing falsely reports ``acodec=aac``.
    Since "best" would pick the higher-bitrate HEVC stream, the file comes out
    silent. Only the ``h264`` renditions actually contain audio.

    So for TikTok we explicitly prefer ``vcodec=h264`` and DO NOT apply a height
    cap: TikTok clips are portrait (e.g. 576x1024), so a ``height<=720`` filter
    would wrongly exclude the good H.264 stream. H.264 is also the most
    Telegram-compatible codec. We can't rely on ``[acodec!=none]`` here because
    yt-dlp mis-reports audio on the HEVC formats.

    For YouTube/others, merging separate best video+audio gives the best
    quality; the fallbacks still require audio so we never emit a silent file.
    """
    if "tiktok" in url.lower():
        return (
            "best[vcodec=h264]/"
            "best[vcodec^=avc]/"
            "best[vcodec!=h265][vcodec!=hevc]/"
            "best"
        )
    hcap = f"[height<={height}]" if height else ""
    return (
        f"bestvideo{hcap}+bestaudio/"
        f"best{hcap}[acodec!=none]/"
        f"best{hcap}/best"
    )


def _base_opts(tmpdir: str, url: str = "") -> dict:
    opts = {
        # Keep the real title in the filename. We deliberately DON'T set
        # restrictfilenames: it strips everything to ASCII, so a fully Cyrillic
        # (or other non-Latin) title collapses to a single "_", giving "_.mp3"/
        # "_.mp4". The "%(title,id)s" alternate falls back to the video id only
        # if the title is genuinely empty. Output lives in a private tmpdir on
        # Linux, so Unicode filenames are fine.
        "outtmpl": os.path.join(tmpdir, "%(title,id).80s.%(ext)s"),
        "restrictfilenames": False,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "logger": _YDLLogger(),
        "retries": 5,
        "fragment_retries": 10,
        "socket_timeout": 30,
        # Speed: download DASH/HLS fragments in parallel instead of one-by-one.
        # This is the biggest win over a high-latency proxy (esp. YouTube).
        "concurrent_fragment_downloads": config.CONCURRENT_FRAGMENTS,
        # Larger read buffer; helps sustain throughput through the tunnel.
        "buffersize": 1024 * 1024,
        # Cap per-request chunk so a single throttled connection can't stall.
        "http_chunk_size": 10 * 1024 * 1024,
    }
    opts["proxy"] = config.PROXY_URL or ""
    if _IMPERSONATE is not None:
        # Impersonate a real browser's TLS fingerprint to get past 403 walls.
        opts["impersonate"] = _IMPERSONATE
    # Harmless for non-YouTube URLs (yt-dlp ignores extractor args it doesn't
    # apply); essential for getting past YouTube's 403 media walls.
    opts["extractor_args"] = _extractor_args()
    # Optional cookies file (e.g. exported YouTube/Instagram cookies) lets the
    # bot reach age-gated / login-walled videos. Mount it and set COOKIES_FILE.
    cookie_file = config.COOKIES_FILE
    host = (urlparse(url).hostname or "").lower()
    if _is_youtube(url):
        cookie_file = os.getenv("YOUTUBE_COOKIES_FILE", "").strip() or cookie_file
    elif host == "instagram.com" or host.endswith(".instagram.com") or host == "instagr.am":
        cookie_file = os.getenv("INSTAGRAM_COOKIES_FILE", "").strip() or cookie_file
    if cookie_file:
        try:
            # yt-dlp writes its cookie jar. Keep the source mount read-only and
            # isolate concurrent downloads from one another.
            target = os.path.join(tmpdir, "cookies.txt")
            shutil.copyfile(cookie_file, target)
            opts["cookiefile"] = target
        except OSError as exc:
            raise DownloadError("Не удалось прочитать cookies. Проверьте путь и права доступа.") from exc
    return opts


def probe(url: str) -> dict:
    """Fetch metadata without downloading. Returns the info dict."""
    with tempfile.TemporaryDirectory() as tmp:
        opts = _base_opts(tmp, url)
        opts["skip_download"] = True
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        except Exception as e:  # noqa: BLE001
            if _is_tiktok(url) and _looks_login_required(str(e)):
                opts["extractor_args"] = {
                    **opts.get("extractor_args", {}), **_TIKTOK_APP_ARGS
                }
                try:
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        return ydl.extract_info(url, download=False)
                except Exception as e2:  # noqa: BLE001
                    raise DownloadError(friendly_error(e2)) from e2
            raise DownloadError(friendly_error(e)) from e


def _run_download(opts: dict, url: str) -> None:
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)


def download(url: str, quality_key: str) -> str:
    tmpdir = tempfile.mkdtemp(prefix="dl_")
    try:
        return _download(url, quality_key, tmpdir)
    except Exception:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise


def _download(url: str, quality_key: str, tmpdir: str) -> str:
    if quality_key not in QUALITY_PRESETS:
        raise DownloadError(f"Unknown quality: {quality_key}")

    _label, height, is_audio = QUALITY_PRESETS[quality_key]

    opts = _base_opts(tmpdir, url)

    if is_audio:
        # On TikTok, bestaudio may resolve to a (mislabeled) silent HEVC stream,
        # so pull audio from the h264 rendition that actually contains it.
        if "tiktok" in url.lower():
            opts["format"] = "best[vcodec=h264]/best[vcodec^=avc]/bestaudio/best"
        else:
            opts["format"] = "bestaudio/best"
    else:
        opts["format"] = _build_format(height, url)

    if is_audio:
        opts["writethumbnail"] = True
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            },
            {"key": "FFmpegMetadata"},
        ]
    else:
        # Prefer mp4 output for Telegram compatibility. Remux (copy streams into
        # an mp4 container) instead of a full re-encode: much faster, and it
        # keeps the audio track intact. Remuxer re-encodes only if a codec is
        # incompatible with mp4.
        opts["merge_output_format"] = "mp4"
        opts["postprocessors"] = [
            {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}
        ]

    try:
        _run_download(opts, url)
    except Exception as e:  # noqa: BLE001
        # YouTube's default client sometimes 403s the media URLs. Retry once
        # forcing the HLS-serving clients, which usually hand out a stream that
        # downloads cleanly.
        if _is_youtube(url) and _looks_forbidden(str(e)):
            log.warning("YouTube 403 — retrying with HLS-only player clients")
            opts["extractor_args"] = _extractor_args(_YT_PLAYER_CLIENTS_FALLBACK)
        # TikTok's web API hides sensitive/age-gated posts behind a login wall.
        # Retry once via the mobile-app API, which usually serves them.
        elif _is_tiktok(url) and _looks_login_required(str(e)):
            log.warning("TikTok login wall — retrying via mobile-app API")
            opts["extractor_args"] = {**opts.get("extractor_args", {}), **_TIKTOK_APP_ARGS}
        else:
            log.warning("Download extractor failed: %s", e)
            raise DownloadError(friendly_error(e)) from e

        # Clear any partial fragments from the first attempt.
        for f in glob.glob(os.path.join(tmpdir, "*")):
            if f == opts.get("cookiefile"):
                continue
            try:
                os.remove(f)
            except OSError:
                pass
        try:
            _run_download(opts, url)
        except Exception as e2:  # noqa: BLE001
            log.warning("Download retry failed: %s", e2)
            raise DownloadError(friendly_error(e2)) from e2

    extension = ".mp3" if is_audio else ".mp4"
    files = [f for f in glob.glob(os.path.join(tmpdir, "*"))
             if os.path.isfile(f) and f.lower().endswith(extension)]
    if not files:
        raise DownloadError("Download produced no file.")

    # Pick the largest resulting file (the merged/converted output).
    result = max(files, key=os.path.getsize)
    if is_audio:
        _add_cover(result, tmpdir)
    return result


def _add_cover(audio_path: str, tmpdir: str) -> None:
    """Best-effort ID3 cover plus a small JPEG for Telegram's audio preview."""
    images = [p for p in glob.glob(os.path.join(tmpdir, "*"))
              if os.path.splitext(p)[1].lower() in (".jpg", ".jpeg", ".png", ".webp", ".avif")]
    if not images:
        return
    cover = os.path.join(tmpdir, "cover.jpg")
    try:
        subprocess.run(['ffmpeg', '-y', '-i', images[-1], '-vf',
                        'scale=320:320:force_original_aspect_ratio=decrease',
                        '-frames:v', '1', '-q:v', '5', cover],
                       check=True, capture_output=True, timeout=30)
        from mutagen.id3 import ID3, APIC, ID3NoHeaderError
        try:
            tags = ID3(audio_path)
        except ID3NoHeaderError:
            tags = ID3()
        with open(cover, 'rb') as fh:
            tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=fh.read()))
        tags.save(audio_path, v2_version=3)
    except Exception as exc:
        log.warning("Cover art unavailable; sending audio without cover: %s", exc)
        if os.path.exists(cover):
            os.remove(cover)
