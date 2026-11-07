FROM node:22-slim AS build
RUN npm install -g pnpm@10.34.9
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/package.json
COPY packages/shared/package.json packages/shared/package.json
RUN pnpm install --frozen-lockfile --filter "@momentsales/web..."
COPY packages/shared packages/shared
COPY apps/web apps/web
ARG API_INTERNAL_URL=http://api:8000
ARG NEXT_PUBLIC_PROVIDER_MODE=mock
ENV API_INTERNAL_URL=$API_INTERNAL_URL NEXT_PUBLIC_PROVIDER_MODE=$NEXT_PUBLIC_PROVIDER_MODE NEXT_TELEMETRY_DISABLED=1
RUN pnpm --filter @momentsales/web build

FROM node:22-slim AS run
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
WORKDIR /app
COPY --from=build --chown=node:node /app/apps/web/.next/standalone ./
COPY --from=build --chown=node:node /app/apps/web/.next/static ./apps/web/.next/static
COPY --from=build --chown=node:node /app/apps/web/public ./apps/web/public
USER node
EXPOSE 3000
CMD ["node", "apps/web/server.js"]
