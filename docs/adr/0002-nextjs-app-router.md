# ADR-0002: Next.js App Router for the web app

- Status: Accepted (Phase 0, chosen by the product owner)
- Date: 2026-11-07

## Context
`apps/web` serves the seller dashboard, the public storefront (Phase 3) and the marketing site (Phase 4). The storefront and marketing pages need server rendering for SEO. The PRD allows Vite or Next.js.

## Decision
- Next.js 16 (App Router, Turbopack), React 19, TypeScript strict, Tailwind CSS 4, shadcn-style components on Radix, TanStack Query, React Hook Form + Zod, next-intl (en-GB, keys ready for more locales), next-themes.
- **FastAPI stays the only API.** Next.js has no business logic or database access. The browser calls `/api/*` on the web origin, and Next.js rewrites it to FastAPI (`API_INTERNAL_URL`). The app is same-origin, so it needs no CORS, and cookies stay first-party.
- Dashboard pages are client components that use the in-memory access token. Public pages (landing, later storefront and marketing) are server components.
- Output is `standalone` for a small Docker image.

## Consequences
- The rewrite target is fixed at build time, so the compose build passes `API_INTERNAL_URL` as a build arg.
- The mobile app (L6) uses the same FastAPI endpoints directly.
