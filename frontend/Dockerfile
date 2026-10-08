# MateMail frontend image.
#
# Built in CI and published to GHCR as
#   ghcr.io/rizwansammo/matemail-frontend:<commit-sha>
# Never built on the production server.
#
# NEXT_PUBLIC_* values are inlined into the client bundle at build time, so they
# are build arguments rather than runtime environment. Changing a public URL
# therefore requires a rebuild, not a restart.

FROM node:24-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --frozen-lockfile

FROM node:24-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
# MateMail Workspace — the customer surface. `app.matemail.online` was
# this hostname until P11 and is now a legacy redirect only; nothing
# built into the image may point at it.
ARG NEXT_PUBLIC_API_URL=https://portal.matemail.pro
ARG NEXT_PUBLIC_APP_URL=https://portal.matemail.pro
# The three hostnames middleware.ts compares the incoming Host against,
# inlined at build time like the others rather than read from the
# container's env — middleware runs in the Edge runtime, which does not
# see the container's environment.
ARG NEXT_PUBLIC_WORKSPACE_HOST=portal.matemail.pro
ARG NEXT_PUBLIC_PLATFORM_HOST=platform.matemail.pro
ARG NEXT_PUBLIC_POSTBOX_HOST=postbox.matemail.pro
# Retired hostnames, redirected to the Workspace host and never served.
ARG NEXT_PUBLIC_LEGACY_WORKSPACE_HOSTS=app.matemail.online,portal.matemail.online
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_APP_URL=$NEXT_PUBLIC_APP_URL
ENV NEXT_PUBLIC_WORKSPACE_HOST=$NEXT_PUBLIC_WORKSPACE_HOST
ENV NEXT_PUBLIC_PLATFORM_HOST=$NEXT_PUBLIC_PLATFORM_HOST
ENV NEXT_PUBLIC_POSTBOX_HOST=$NEXT_PUBLIC_POSTBOX_HOST
ENV NEXT_PUBLIC_LEGACY_WORKSPACE_HOSTS=$NEXT_PUBLIC_LEGACY_WORKSPACE_HOSTS
RUN npm run build

FROM node:24-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

# node's own fetch rather than curl/wget: the alpine runtime has no curl, and
# adding one to a production image for a healthcheck is not worth the surface.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=5 \
    CMD node -e "fetch('http://127.0.0.1:3000/login').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
