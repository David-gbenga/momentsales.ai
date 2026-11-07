# ADR-0006: Node 22 LTS, React 19, TypeScript 5.9

- Status: Accepted (Phase 0, chosen by the product owner)
- Date: 2026-11-07

## Context
The PRD said Node 20 LTS and React 18. Node 20 reached end-of-life in April 2026, and current Next.js and shadcn target React 19.

## Decision
- **Node 22 LTS** (`engines: >=22.11 <23`; Docker and CI use the latest 22.x) and **React 19**. The PRD constraint line is updated to match.
- **TypeScript 5.9.3**, not 7.x: typescript-eslint and openapi-typescript support only TS < 6.1.
- **ESLint 9.39**, not 10: eslint-plugin-react (inside eslint-config-next) supports only ESLint ≤ 9.

## Known constraint (local machine)
The development machine runs Node **22.11.0**. Some current packages need 22.12+ (`require(esm)`, Vite 7/8, jsdom 31, Tailwind ≥ 4.3.4). To keep local dev working, Phase 0 pins:
- Vitest 4.1 with Vite 6.4 and happy-dom (not jsdom 31 or @vitejs/plugin-react 6),
- Tailwind CSS 4.3.3.

**Recommended follow-up:** update local Node to the latest 22.x, then lift these pins (Tailwind 4.3.5+, Vite 7+). Docker and CI already use the latest 22.x.
