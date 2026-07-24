FROM python:3.12-slim

# ffmpeg is required by yt-dlp for merging video+audio and MP3 extraction.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as a non-root user
RUN useradd -m appuser
USER appuser

CMD ["python", "-u", "bot.py"]
