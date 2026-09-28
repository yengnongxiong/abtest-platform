# Static demo page served by nginx. Files are baked into the image (and synced by
# Compose Watch in development) instead of bind-mounted, so the stack works the same
# on any Docker runtime.
FROM nginx:1.30.5-alpine
COPY index.html /usr/share/nginx/html/
