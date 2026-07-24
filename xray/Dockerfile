# Prebuilt, well-maintained xray image (Alpine-based, `xray` on PATH,
# geo assets in /usr/share/xray). No build-time downloads needed.
FROM teddysun/xray:latest

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 10808 10809

ENTRYPOINT ["/entrypoint.sh"]
