// defineConfig comes from vitest/config rather than vite: it is the same Vite
// config type extended with the `test` block, which plain vite/config rejects.
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    // The dev server runs in a container over a bind mount, where inotify events
    // from the host are not delivered reliably.
    watch: { usePolling: true },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
