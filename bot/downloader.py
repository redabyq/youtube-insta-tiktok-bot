"""Video/audio downloading via yt-dlp, routed through the VLESS proxy."""
import os
import glob
import logging
import tempfile

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

# YouTube regularly 403s the media URLs served to the default "web" client
# (expired nsig / throttling / PO-token walls), which surfaces as
# "unable to download video data: HTTP Error 403: Forbidden" during the
# fragment/data download step. Pulling formats from additional player clients —
# notably the TV and Safari clients, which hand out HLS manifests that dodge the
# 403 wall — gives the format selector a working stream to fall back to.
_YT_PLAYER_CLIENTS = ["default", "tv", "web_safari", "mweb"]
# Clients to try if the first attempt still 403s: HLS-serving clients only.
_YT_PLAYER_CLIENTS_FALLBACK = ["tv", "web_safari", "ios"]


def _extractor_args(clients=None) -> dict:
    return {"youtube": {"player_client": clients or _YT_PLAYER_CLIENTS}}


def _is_youtube(url: str) -> bool:
    u = url.lower()
    return "youtube.com" in u or "youtu.be" in u


def _looks_forbidden(msg: str) -> bool:
    m = msg.lower()
    return "403" in m or "forbidden" in m


class DownloadError(Exception):
    pass


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


def _base_opts(tmpdir: str) -> dict:
    opts = {
        "outtmpl": os.path.join(tmpdir, "%(title).80s.%(ext)s"),
        "restrictfilenames": True,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": True,
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
    if config.PROXY_URL:
        opts["proxy"] = config.PROXY_URL
    if _IMPERSONATE is not None:
        # Impersonate a real browser's TLS fingerprint to get past 403 walls.
        opts["impersonate"] = _IMPERSONATE
    # Harmless for non-YouTube URLs (yt-dlp ignores extractor args it doesn't
    # apply); essential for getting past YouTube's 403 media walls.
    opts["extractor_args"] = _extractor_args()
    # Optional cookies file (e.g. exported YouTube/Instagram cookies) lets the
    # bot reach age-gated / login-walled videos. Mount it and set COOKIES_FILE.
    if getattr(config, "COOKIES_FILE", ""):
        opts["cookiefile"] = config.COOKIES_FILE
    return opts


def probe(url: str) -> dict:
    """Fetch metadata without downloading. Returns the info dict."""
    with tempfile.TemporaryDirectory() as tmp:
        opts = _base_opts(tmp)
        opts["skip_download"] = True
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        except Exception as e:  # noqa: BLE001
            raise DownloadError(str(e)) from e


def _run_download(opts: dict, url: str) -> None:
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)


def download(url: str, quality_key: str) -> str:
    if quality_key not in QUALITY_PRESETS:
        raise DownloadError(f"Unknown quality: {quality_key}")

    _label, height, is_audio = QUALITY_PRESETS[quality_key]

    tmpdir = tempfile.mkdtemp(prefix="dl_")
    opts = _base_opts(tmpdir)

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
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
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
            # Clear any partial fragments from the first attempt.
            for f in glob.glob(os.path.join(tmpdir, "*")):
                try:
                    os.remove(f)
                except OSError:
                    pass
            try:
                _run_download(opts, url)
            except Exception as e2:  # noqa: BLE001
                raise DownloadError(str(e2)) from e2
        else:
            raise DownloadError(str(e)) from e

    files = [f for f in glob.glob(os.path.join(tmpdir, "*")) if os.path.isfile(f)]
    if not files:
        raise DownloadError("Download produced no file.")

    # Pick the largest resulting file (the merged/converted output).
    result = max(files, key=os.path.getsize)
    return result
