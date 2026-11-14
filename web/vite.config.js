import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Local only: the dev server listens on 127.0.0.1 and forwards /api to the local FastAPI server.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: { "/api": `http://127.0.0.1:${process.env.DASH_API_PORT || 8765}` },
  },
  preview: { proxy: { "/api": `http://127.0.0.1:${process.env.DASH_API_PORT || 8765}` } },
});
