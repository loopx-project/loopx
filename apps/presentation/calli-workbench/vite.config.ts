import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// No proxy yet: nothing in this layer calls a LoopX service. The read-only
// proxy arrives with the projections PR, together with its first caller.
export default defineConfig({
  plugins: [react()],
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
