FROM node:26-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:26-alpine

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    # server.js reads HOSTNAME, and Docker sets that to the container id, which resolves to nothing
    # it can bind. Without this the server listens on an address no other container can reach.
    HOSTNAME=0.0.0.0

WORKDIR /app
# `output: 'standalone'` traces the modules the pages actually load into .next/standalone, beside a
# server.js that needs no node_modules. Static assets are not traced, so they are copied separately.
# Copied to the image's own unprivileged user rather than to root, because a source file the host
# keeps at mode 600 stays unreadable here otherwise.
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static

USER node
EXPOSE 3000
CMD ["node", "server.js"]
