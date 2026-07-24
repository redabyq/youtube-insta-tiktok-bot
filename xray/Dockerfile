FROM alpine:3.20

# envsubst (gettext) renders the config template at start-up.
# unzip/curl fetch the xray release; ca-certificates for TLS.
RUN apk add --no-cache gettext ca-certificates curl unzip

# Download the official Xray-core release binary + geo data.
# XRAY_VERSION="latest" always resolves; pin to e.g. "v1.8.24" for reproducible builds.
ARG XRAY_VERSION=latest
RUN set -eux; \
    case "$(apk --print-arch)" in \
        x86_64)  XRAY_ARCH="64" ;; \
        aarch64) XRAY_ARCH="arm64-v8a" ;; \
        armv7)   XRAY_ARCH="arm32-v7a" ;; \
        *) echo "unsupported arch: $(apk --print-arch)" >&2; exit 1 ;; \
    esac; \
    if [ "$XRAY_VERSION" = "latest" ]; then \
        URL="https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-${XRAY_ARCH}.zip"; \
    else \
        URL="https://github.com/XTLS/Xray-core/releases/download/${XRAY_VERSION}/Xray-linux-${XRAY_ARCH}.zip"; \
    fi; \
    curl -fsSL -o /tmp/xray.zip "$URL"; \
    unzip -o /tmp/xray.zip -d /usr/local/xray; \
    install -m 0755 /usr/local/xray/xray /usr/bin/xray; \
    mkdir -p /usr/share/xray; \
    cp /usr/local/xray/*.dat /usr/share/xray/ 2>/dev/null || true; \
    rm -rf /tmp/xray.zip; \
    xray version

ENV XRAY_LOCATION_ASSET=/usr/share/xray

COPY config.json.template /etc/xray/config.json.template
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 10808 10809

ENTRYPOINT ["/entrypoint.sh"]
