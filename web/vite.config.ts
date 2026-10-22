import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Served from https://sayamrina.github.io/range-atlas/ in production
export default defineConfig({
  base: process.env.VITE_BASE ?? "/",
  plugins: [react()],
  optimizeDeps: { exclude: ["@duckdb/duckdb-wasm"] },
  // MapLibre alone is ~800 kB; it is already split into the lazily loaded map page
  build: { chunkSizeWarningLimit: 1000 },
});
