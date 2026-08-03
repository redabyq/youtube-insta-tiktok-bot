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
