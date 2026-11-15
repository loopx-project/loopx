import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { readOnlyForward } from './readonly-forward.ts';

// No `server.proxy` / `preview.proxy`: every forward to the local LoopX services (status 8766, chat
// 8767) happens inside readonly-forward.ts, which enumerates the GET/HEAD routes the workbench reads
// and answers everything else locally, in dev and in preview alike.
export default defineConfig({
  plugins: [readOnlyForward(), react()],
  server: { host: '127.0.0.1', port: 5174 },
  preview: { host: '127.0.0.1', port: 4174 },
  optimizeDeps: { esbuildOptions: { target: 'es2022' } },
  build: {
    target: ['chrome89', 'edge89', 'firefox89', 'safari15'],
    outDir: 'dist',
    // Keep the published UI available if a build fails or is interrupted.
    emptyOutDir: false,
    sourcemap: false,
  },
});
