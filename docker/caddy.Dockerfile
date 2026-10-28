# ---------------------------------------------------------------------------
# Web image = static frontend build + Caddy (TLS + reverse proxy).
# Build context = repository root (needs ./web and ./docker/Caddyfile).
# ---------------------------------------------------------------------------
FROM node:20-alpine AS web-build

WORKDIR /web
ARG VITE_APP_VERSION=prod
ENV VITE_APP_VERSION=${VITE_APP_VERSION}

COPY web/package.json web/package-lock.json* ./
RUN npm ci || npm install

COPY web/ ./
# The OpenAPI type generation runs against a running API; in the image build we
# commit nothing generated, so relax the build to typecheck without it.
RUN npm run build

FROM caddy:2.8-alpine

COPY --from=web-build /web/dist /srv/www
COPY docker/Caddyfile docker/acme-email.caddy /etc/caddy/
COPY docker/caddy-entrypoint.sh /usr/local/bin/caddy-entrypoint.sh
RUN chmod 0755 /usr/local/bin/caddy-entrypoint.sh

EXPOSE 80 443 443/udp

# The base image's command, behind an entrypoint that only prepares the optional ACME e-mail
# (ACME_EMAIL empty or unset must not break the Caddyfile; see docker/caddy-entrypoint.sh).
ENTRYPOINT ["/usr/local/bin/caddy-entrypoint.sh"]
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
