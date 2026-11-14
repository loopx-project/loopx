import { defineConfig, type ProxyOptions } from 'vite';
import react from '@vitejs/plugin-react';

// Local LoopX services, same proxy rules as the official dashboard
// (apps/presentation/dashboard/vite.config.ts). The workbench only issues GET
// requests through them.
const statusTarget = 'http://127.0.0.1:8766';
const statusProxy: ProxyOptions = {
  target: statusTarget,
  changeOrigin: false,
};
const chatTarget = 'http://127.0.0.1:8767';
const chatProxy: ProxyOptions = {
  target: chatTarget,
  changeOrigin: true,
  configure(proxy) {
    proxy.on('proxyReq', (proxyRequest, request) => {
      // Only the workbench's own browser requests may cross the dev proxy.
      const workbenchHost = `127.0.0.1:${request.socket.localPort}`;
      if (request.headers.host === workbenchHost && request.headers.origin === `http://${workbenchHost}`) {
        proxyRequest.setHeader('Origin', chatTarget);
      }
    });
  },
};

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5174,
    proxy: {
      '/status.json': statusProxy,
      '/ssh-hosts': chatProxy,
      '/api/chat': chatProxy,
    },
  },
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
