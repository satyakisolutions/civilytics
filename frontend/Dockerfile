# Civilytics dashboard — build the Vite bundle, serve it with nginx.
#
# Used by docker-compose.test.yml and docker-compose.prod.yml. The dev
# stack does NOT use this file; it runs `npm run dev` in a node image so
# HMR works over a bind mount.
#
# Vite inlines VITE_* vars at BUILD time, so the API/WS/MediaMTX URLs are
# baked into the bundle here and cannot be changed by setting env vars on
# the running container. Pass them as build args per environment.

# ── Stage 1: build ────────────────────────────────────────────────────
FROM node:20-alpine AS build

WORKDIR /app

# Copy manifests first so `npm ci` is cached independently of source changes.
COPY package.json package-lock.json* ./

# --legacy-peer-deps matches what the dev container uses; this tree has
# peer-dep conflicts that npm 10 refuses to resolve without it.
RUN npm ci --legacy-peer-deps --no-audit --no-fund \
    || npm install --legacy-peer-deps --no-audit --no-fund

COPY . .

# api-base.ts falls back to window.location.hostname with well-known
# ports when these are unset, which works for a same-host deployment.
# Set them explicitly when the API is on a different origin.
ARG VITE_API_URL=""
ARG VITE_WS_URL=""
ARG VITE_MEDIAMTX_URL=""
ENV VITE_API_URL=$VITE_API_URL \
    VITE_WS_URL=$VITE_WS_URL \
    VITE_MEDIAMTX_URL=$VITE_MEDIAMTX_URL

RUN npm run build

# ── Stage 2: serve ────────────────────────────────────────────────────
FROM nginx:1.27-alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://localhost/ >/dev/null 2>&1 || exit 1

CMD ["nginx", "-g", "daemon off;"]
