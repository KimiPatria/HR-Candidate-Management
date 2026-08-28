import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Everything under /api is proxied to FastAPI, including the transcript WebSocket, so
// the browser only ever talks to one origin and the HR session cookie just works.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
