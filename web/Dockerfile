# Development image for the dashboard (see docker-compose.yml).
FROM node:24.21.0-slim

WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1

# Dependencies first, so this layer stays cached until the lock file changes.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev", "--", "--hostname", "0.0.0.0"]
