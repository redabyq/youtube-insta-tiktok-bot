FROM ghcr.io/xtls/xray-core:latest AS xray

FROM alpine:3.20

# envsubst (gettext) is used to render the config template at start-up
RUN apk add --no-cache gettext ca-certificates

# Bring in the xray binary and its geo data files
COPY --from=xray /usr/bin/xray /usr/bin/xray
COPY --from=xray /usr/share/xray/ /usr/share/xray/

COPY config.json.template /etc/xray/config.json.template
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 10808 10809

ENTRYPOINT ["/entrypoint.sh"]
