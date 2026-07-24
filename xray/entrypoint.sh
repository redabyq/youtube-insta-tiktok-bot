#!/bin/sh
set -e

# Render the xray config directly from environment variables using a heredoc.
# (No envsubst / no extra packages needed — plain POSIX sh expands the vars.)
: "${VLESS_FLOW:=xtls-rprx-vision}"
: "${VLESS_FINGERPRINT:=chrome}"
: "${VLESS_PORT:=443}"

echo "[xray] Rendering config for ${VLESS_ADDRESS}:${VLESS_PORT} (SNI ${VLESS_SNI})"

cat > /etc/xray/config.json <<EOF
{
  "log": { "loglevel": "warning" },
  "inbounds": [
    {
      "tag": "socks-in",
      "listen": "0.0.0.0",
      "port": 10808,
      "protocol": "socks",
      "settings": { "udp": true, "auth": "noauth" },
      "sniffing": { "enabled": true, "destOverride": ["http", "tls"] }
    },
    {
      "tag": "http-in",
      "listen": "0.0.0.0",
      "port": 10809,
      "protocol": "http"
    }
  ],
  "outbounds": [
    {
      "tag": "vless-reality",
      "protocol": "vless",
      "settings": {
        "vnext": [
          {
            "address": "${VLESS_ADDRESS}",
            "port": ${VLESS_PORT},
            "users": [
              {
                "id": "${VLESS_UUID}",
                "encryption": "none",
                "flow": "${VLESS_FLOW}"
              }
            ]
          }
        ]
      },
      "streamSettings": {
        "network": "tcp",
        "security": "reality",
        "realitySettings": {
          "show": false,
          "fingerprint": "${VLESS_FINGERPRINT}",
          "serverName": "${VLESS_SNI}",
          "publicKey": "${VLESS_PUBLIC_KEY}",
          "shortId": "${VLESS_SHORT_ID}",
          "spiderX": ""
        }
      }
    },
    { "tag": "direct", "protocol": "freedom" }
  ]
}
EOF

echo "[xray] Starting xray (VLESS+REALITY -> SOCKS5 :10808 / HTTP :10809)"
exec xray run -config /etc/xray/config.json
