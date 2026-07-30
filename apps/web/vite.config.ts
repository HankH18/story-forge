import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// This file runs in Node, not in the browser bundle, and `@types/node` is not a
// dependency of an app that ships no Node code. One declaration is cheaper than
// pulling the whole Node typing surface into a browser project's tsconfig.
declare const process: { env: Record<string, string | undefined> }

/**
 * Which API this dev server proxies to.
 *
 * Overridable because the Phase 6 audit's finding 0 was that a browser session
 * had been driven against an API running §18's `DEMO_BYPASS_AUTH` without anyone
 * noticing — the SPA looked signed in because nothing was checking. Verifying a
 * fix now means standing up a second, enforced API on a spare port and pointing
 * a dev server at it, and hard-coding `:8000` made that a config edit every
 * time. The default is unchanged, so `make dev` behaves exactly as before.
 */
const API_TARGET = process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000'

/**
 * The §10 fit-check harness is a SECOND build entry, and it is opt-in.
 *
 * `scripts/fit-check.mjs` measures every servable segment against the real
 * `ReadingView` in the real production bundle (DEVIATIONS 13.2) — a hand-written
 * HTML replica would measure a layout the app does not have, and drift silently
 * the first time either side is edited. So the harness must be compiled by this
 * config. It must equally never reach a deploy, so it is emitted only when
 * `SF_FIT_HARNESS` is set, which only `bun run fit-check` sets. With the flag
 * unset this object is empty and `bun run build` emits exactly what it always
 * did.
 */
const FIT_HARNESS = Boolean(process.env.SF_FIT_HARNESS)

export default defineConfig({
  plugins: [react()],
  build: FIT_HARNESS
    ? { rollupOptions: { input: { index: 'index.html', 'fit-check': 'fit-check.html' } } }
    : {},
  server: {
    port: Number(process.env.VITE_DEV_PORT ?? 5173),
    strictPort: true,
    // All state and secrets live behind FastAPI (§2); the SPA never holds them.
    proxy: {
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
      },
    },
  },
  // The same proxy for `vite preview`, so the PRODUCTION bundle can be driven
  // against the real API before it is deployed. That matters here specifically:
  // Tailwind only purges on build, so a class that is emitted in dev and dropped
  // in production (STATE invariant 7 — the verdict colours) is invisible until
  // something exercises `dist/`.
  preview: {
    port: Number(process.env.VITE_PREVIEW_PORT ?? 4173),
    strictPort: true,
    proxy: {
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
      },
    },
  },
})
