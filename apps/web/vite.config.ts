import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    // All state and secrets live behind FastAPI (§2); the SPA never holds them.
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
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
    port: 4173,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
