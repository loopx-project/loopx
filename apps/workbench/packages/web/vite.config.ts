import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// In development the UI runs on Vite and forwards /api to the daemon.
// Override the target with CALLI_DAEMON_URL when the daemon is not on localhost:8787.
const daemon = process.env.CALLI_DAEMON_URL ?? 'http://127.0.0.1:8787';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      // ws: the desktop's VNC stream is the one WebSocket.
      '/api': { target: daemon, changeOrigin: false, ws: true },
    },
  },
  // noVNC uses top-level await: the oldest browsers that have it (Safari 15, 2021).
  optimizeDeps: { esbuildOptions: { target: 'es2022' } },
  build: {
    target: ['chrome89', 'edge89', 'firefox89', 'safari15'],
    outDir: 'dist',
    // Keep the published UI available if a build fails or is interrupted.
    emptyOutDir: false,
    sourcemap: false,
  },
});
