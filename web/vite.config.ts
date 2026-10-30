import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// The backend is a FastAPI app on 8765; production serves web/dist itself,
// so every request goes to the same-origin '/api' path in both modes.
export default defineConfig({
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
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    restoreMocks: true,
  },
});
