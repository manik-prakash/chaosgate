import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: `npm run dev` proxies the API to a port-forwarded orchestrator
// (kubectl port-forward svc/chaos-gate-dashboard 18080:8080).
const target = process.env.API_TARGET ?? "http://localhost:18080";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": target, "/reports": target } },
  build: { outDir: "dist", sourcemap: false },
});
