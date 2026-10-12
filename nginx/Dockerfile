# Civilytics production ingress.
#
# NGINX_SERVER_NAME is substituted at container start by entrypoint.sh,
# which also runs `nginx -t` there -- not here at build time. The config's
# upstream blocks (nginx.conf: server mediamtx:8554, api:8080, ...) need
# those hostnames to resolve, which only happens once the compose network
# is actually up; an isolated build has no network and can never pass
# this check, so it was failing every build regardless of config
# correctness. entrypoint.sh's runtime check is the real validation.
FROM nginx:1.27-alpine

RUN apk add --no-cache openssl

COPY nginx.conf /etc/nginx/nginx.conf.template
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 443 554

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD nginx -t >/dev/null 2>&1 || exit 1

ENTRYPOINT ["/entrypoint.sh"]
