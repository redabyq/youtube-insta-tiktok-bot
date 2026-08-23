FROM python:3.12-slim

# ffmpeg is required by yt-dlp for merging video+audio and MP3 extraction.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Force the newest yt-dlp on every build. Sites (YouTube/TikTok) break stale
# versions constantly, causing 403s and "Unexpected response" errors. Bump
# YTDLP_REFRESH (or build with --no-cache) to bust the layer cache and refetch.
ARG YTDLP_REFRESH=2026-08-23
RUN pip install --no-cache-dir --upgrade "yt-dlp[default,curl-cffi]"

COPY . .

# Run as a non-root user
RUN useradd -m appuser
USER appuser

CMD ["python", "-u", "bot.py"]
