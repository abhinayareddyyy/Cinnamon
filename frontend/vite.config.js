import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// In development the console runs on :5173 and proxies /api to the FastAPI
// backend on :8000, so the browser only ever talks to one origin.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': process.env.API_URL ?? 'http://localhost:8000',
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/setupTests.js',
  },
});
