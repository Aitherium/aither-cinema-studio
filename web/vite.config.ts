import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

/**
 * Dev and self-host: the backend serves web/dist itself, so requests go to the
 * same-origin '/api' path and vite proxies them to :8765.
 *
 * GitHub Pages: the page is served from /aither-cinema-studio/, so the build
 * carries that base. The base is production-only — dev stays at '/'.
 */
export default defineConfig(({ mode }) => ({
  base: mode === 'production' ? '/aither-cinema-studio/' : '/',
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    restoreMocks: true,
  },
}));
