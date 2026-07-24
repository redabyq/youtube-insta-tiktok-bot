"""Video/audio downloading via yt-dlp, routed through the VLESS proxy."""
import os
import glob
import logging
import tempfile

import yt_dlp

import config

log = logging.getLogger("downloader")


class DownloadError(Exception):
    pass


# Quality presets. Key -> (label, yt-dlp format string, is_audio)
QUALITY_PRESETS = {
    "mp3": ("🎵 MP3 (audio)", "bestaudio/best", True),
    "144": ("📹 144p", "bestvideo[height<=144]+bestaudio/best[height<=144]/best", False),
    "360": ("📹 360p", "bestvideo[height<=360]+bestaudio/best[height<=360]/best", False),
    "480": ("📹 480p", "bestvideo[height<=480]+bestaudio/best[height<=480]/best", False),
    "720": ("📹 720p", "bestvideo[height<=720]+bestaudio/best[height<=720]/best", False),
    "1080": ("📹 1080p", "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best", False),
    "best": ("📹 Best available", "bestvideo+bestaudio/best", False),
}


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


def download(url: str, quality_key: str) -> str:
    """Download the media and return the path to the resulting file."""
    if quality_key not in QUALITY_PRESETS:
        raise DownloadError(f"Unknown quality: {quality_key}")

    _label, fmt, is_audio = QUALITY_PRESETS[quality_key]

    # Persistent dir so the caller can send the file after this returns.
    tmpdir = tempfile.mkdtemp(prefix="dl_")
    opts = _base_opts(tmpdir)
    opts["format"] = fmt

    if is_audio:
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]
    else:
        # Prefer mp4 output for maximum Telegram compatibility.
        opts["merge_output_format"] = "mp4"
        opts["postprocessors"] = [
            {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}
        ]

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as e:  # noqa: BLE001
        raise DownloadError(str(e)) from e

    files = [f for f in glob.glob(os.path.join(tmpdir, "*")) if os.path.isfile(f)]
    if not files:
        raise DownloadError("Download produced no file.")

    # Pick the largest resulting file (the merged/converted output).
    result = max(files, key=os.path.getsize)
    return result
