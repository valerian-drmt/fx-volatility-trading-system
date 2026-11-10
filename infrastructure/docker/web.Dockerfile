# Multi-stage build for the React frontend image.
#
# Stage 1 compiles the bundle with Node 22 (matches the CI `frontend` job).
# Stage 2 copies the dist/ output into an nginx:alpine image configured with
# frontend.conf (SPA fallback + immutable cache for hashed assets).
#
#   docker build -f infrastructure/docker/web.Dockerfile -t fx-options-frontend .

# Pinned by tag and digest (#305); Node 22 LTS (Node 20 is end-of-life).
ARG NODE_IMAGE=node:22.23.4-alpine3.24@sha256:0e1754758c2df4cfa3e68d59e2e1b7374bb7fd9ea6328eaaacf15cc934af8202
ARG NGINX_IMAGE=nginx:1.31.8-alpine@sha256:713fc2ebfd92e8217db451477182ee91944b123d2b1ee9fd5a8c6e91ca6c3a11

FROM ${NODE_IMAGE} AS build

WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
# `npm ci` errors if the lockfile is out of sync — deterministic installs.
RUN npm ci

COPY frontend/ ./
# The desk is live-only : it calls the backend same-origin (/api, /ws) via the
# main nginx. No mock build flag — there is no mock mode.
RUN npm run build && find dist -name '*.map' -delete

FROM ${NGINX_IMAGE} AS runtime

# Nginx config (SPA fallback + immutable cache + security headers).
COPY infrastructure/nginx/frontend.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080

# nginx:alpine ships a default ENTRYPOINT + CMD. Override only the listen port
# via the conf file — no command override needed.
