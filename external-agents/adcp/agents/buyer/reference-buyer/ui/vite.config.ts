// `defineConfig` from vitest/config, not vite: it is the same function widened to accept the
// `test` block below. Importing vite's own rejects `test` as an unknown property.
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Relative asset URLs, deliberately. The same build is served two ways, exactly as the
  // vanilla UI is: from S3/CloudFront by deploy_ui.py, and straight off disk by app.py's
  // FileResponse. An absolute "/assets/..." base resolves under only one of them.
  base: './',
  build: {
    outDir: 'dist',
    // Named so deploy_ui.py can upload the tree with one Cache-Control rule: everything under
    // assets/ is content-hashed and therefore safe to cache immutably, while index.html stays
    // no-cache. See deploy_ui.py's VENDOR_ASSETS note for why that split matters.
    assetsDir: 'assets',
    sourcemap: true,
  },
  server: {
    // app.py serves /config locally; proxying it means `npm run dev` talks to the same
    // config route the deployed build reads, rather than needing a separate dev-only path.
    proxy: {
      '/config': 'http://localhost:8080',
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    // Only our own tests; node_modules and the build output have nothing for us.
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
  },
});
