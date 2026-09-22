import { defineConfig } from 'vite'

// base:'./' keeps the built bundle portable - the dist/ folder can be served
// from any path (or a USB stick via a static server) without rewriting asset URLs.
export default defineConfig({
  base: './',
  server: { open: true, port: 5173 },
  build: { outDir: 'dist', assetsDir: 'assets', chunkSizeWarningLimit: 1200 },
})
