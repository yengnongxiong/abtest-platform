# The demo page, served by nginx with a fresh build of the SDK next to it.
# docker-compose.yml passes the SDK's source in as the "sdk-src" build context.

FROM node:24.21.0-slim AS sdk
WORKDIR /sdk
COPY --from=sdk-src package.json package-lock.json ./
RUN npm ci
COPY --from=sdk-src . .
RUN npm run build

FROM nginx:1.30.5-alpine
COPY --from=sdk /sdk/dist/index.js /usr/share/nginx/html/sdk/index.js
COPY index.html /usr/share/nginx/html/
# nginx's entrypoint runs every script in /docker-entrypoint.d/ before starting.
COPY --chmod=755 write-config.sh /docker-entrypoint.d/40-write-config.sh
