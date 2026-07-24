#!/bin/sh
set -e

# Render the xray config from the template using the VLESS_* env vars.
echo "[xray] Rendering config from template..."
envsubst < /etc/xray/config.json.template > /etc/xray/config.json

echo "[xray] Starting xray with VLESS+REALITY outbound -> SOCKS5 :10808 / HTTP :10809"
exec xray run -config /etc/xray/config.json
