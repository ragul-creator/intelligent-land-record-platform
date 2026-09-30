import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    allowedHosts: [".railway.app", ".railway.internal"],
  },
  preview: {
    host: "0.0.0.0",
    port: 5173,
    allowedHosts: [".railway.app", ".railway.internal"],
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/tests/setup.ts",
  },
});
