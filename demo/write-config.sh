#!/bin/sh
# Writes the page's settings from the container's environment when it starts. The client
# key is public by design (PRD §19), so it is fine to put it in a page.
set -eu
cat > /usr/share/nginx/html/config.js <<CONFIG
window.DEMO_CONFIG = {
  apiBaseUrl: "${DEMO_API_URL:-http://localhost:8000}",
  clientKey: "${ABTEST_CLIENT_KEY}"
};
CONFIG
