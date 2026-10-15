import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, type ProxyOptions } from "vite";

const chatTarget = "http://127.0.0.1:8767";
const chatProxy: ProxyOptions = {
  target: chatTarget,
  changeOrigin: true,
  configure(proxy) {
    proxy.on("proxyReq", (proxyRequest, request) => {
      // Only the dashboard's own browser requests may cross the dev proxy.
      const dashboardHost = `127.0.0.1:${request.socket.localPort}`;
      if (request.headers.host === dashboardHost && request.headers.origin === `http://${dashboardHost}`) {
        proxyRequest.setHeader("Origin", chatTarget);
      }
    });
  },
};

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/periodic-report-workspace": {
        target: "http://127.0.0.1:8766",
        changeOrigin: false,
      },
      "/status.json": {
        target: "http://127.0.0.1:8766",
        changeOrigin: false,
      },
      "/ssh-hosts": chatProxy,
      "/api/chat": chatProxy,
      "/api/actions": chatProxy,
      "/api/ssh-source": chatProxy,
    },
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
  },
});
