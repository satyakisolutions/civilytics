# Civilytics production ingress.
#
# `nginx -t` at build time catches syntax errors here rather than at
# 3am on a deploy. NGINX_SERVER_NAME is substituted at container start by
# entrypoint.sh, so the template is validated with a placeholder.
FROM nginx:1.27-alpine

RUN apk add --no-cache openssl

COPY nginx.conf /etc/nginx/nginx.conf.template
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Validate the template with the placeholder filled in.
RUN sed 's/\${NGINX_SERVER_NAME}/_/' /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf \
    && mkdir -p /etc/nginx/certs \
    && openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
        -keyout /etc/nginx/certs/privkey.pem \
        -out /etc/nginx/certs/fullchain.pem \
        -subj "/CN=build-check" >/dev/null 2>&1 \
    && nginx -t \
    && rm -f /etc/nginx/certs/privkey.pem /etc/nginx/certs/fullchain.pem

EXPOSE 443 554

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD nginx -t >/dev/null 2>&1 || exit 1

ENTRYPOINT ["/entrypoint.sh"]
