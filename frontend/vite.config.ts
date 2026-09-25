import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Everything under /api is proxied to FastAPI, including the transcript WebSocket, so
// the browser only ever talks to one origin and the HR session cookie just works.
export default defineConfig({
  plugins: [react()],
  build: {
    // AudioWorklet modules must be fetched as real files. Vite inlines any asset under
    // 4 KB as a `data:` URI, and `audioWorklet.addModule()` on a data URI is refused
    // under a `script-src` CSP that does not allow `data:`, and has historically been
    // unreliable in Safari. The mic worklet is ~3 KB, so it lands right in that trap -
    // and the failure is a candidate whose microphone silently never captures. Keeping
    // it a separate emitted file also keeps its content hash, so a deploy cannot serve
    // a stale worklet from cache.
    assetsInlineLimit: (filePath: string) =>
      filePath.includes("micWorklet") ? false : undefined,
  },
  server: {
    port: 5173,
    // Vite refuses any request whose Host header it does not recognise (a DNS-rebinding
    // guard), and a tunnel hostname is exactly that: every page load through ngrok or
    // cloudflared comes back "Blocked request. This host is not allowed." rather than
    // failing at the network layer, so it reads as an app bug instead of a config one.
    // Suffix entries match any subdomain, which is what the rotating free-tier hostnames
    // need - a quick tunnel mints a new random name on every start.
    allowedHosts: [
      ".ngrok-free.dev", // current free-tier suffix - what this deployment uses
      ".ngrok-free.app", // older free-tier suffix, still issued to some accounts
      ".ngrok.app",
      ".ngrok.io",
      ".trycloudflare.com",
      ".devtunnels.ms", // VS Code port forwarding
    ],
    // The HMR client derives its socket from the page URL but keeps the dev-server port,
    // so through a tunnel it dials wss://<tunnel-host>:5173 - a port the tunnel does not
    // publish. The socket then fails on a loop in the console. Pinning the client port to
    // 443 sends it back over the tunnel itself. Harmless locally: the browser only reads
    // this when the page is already on https, which plain localhost never is.
    hmr: { clientPort: 443 },
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
