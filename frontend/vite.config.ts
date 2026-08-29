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
    // Vitest globs `**/*.test.*` and `**/*.spec.*` by default, which would collect
    // the Playwright specs and fail on an import it cannot resolve. The two runners
    // own separate directories.
    exclude: ["node_modules/**", "dist/**", "e2e/**"],
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    coverage: {
      provider: "v8",
      // `src` only. Without this the report includes config files and the test
      // helpers themselves, which inflates the number with code that is not the
      // application.
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/test/**", "src/**/__tests__/**", "src/main.tsx", "src/vite-env.d.ts"],
      reporter: ["text-summary"],
      // A floor, not a target — the measured value rounded down. It exists to stop
      // coverage falling, not to be chased upward: tests written to cover a line
      // rather than a behaviour make the suite slower and the number prettier.
      thresholds: { statements: 75, branches: 80, functions: 75, lines: 75 },
    },
  },
});
