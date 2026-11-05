import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
  build: {
    // Warn if any single chunk exceeds 500 kB (gzipped threshold ≈ 150 kB).
    chunkSizeWarningLimit: 500,
    rollupOptions: {
      output: {
        // Split vendor code into named chunks so browsers can cache them
        // independently of application code.
        manualChunks(id: string) {
          // React core — changes rarely; maximise cache lifetime.
          if (id.includes("node_modules/react") || id.includes("node_modules/react-dom")) {
            return "react-vendor";
          }
          // React Router — navigation primitives, separate from React core.
          if (id.includes("node_modules/react-router")) {
            return "router";
          }
          // TanStack Query — data-fetching runtime.
          if (id.includes("node_modules/@tanstack")) {
            return "query";
          }
          // Supabase client — auth and realtime, pulled in lazily via auth flow.
          if (id.includes("node_modules/@supabase")) {
            return "supabase";
          }
          // Zod — schema validation, shared by API layer and pages.
          if (id.includes("node_modules/zod")) {
            return "zod";
          }
          // Everything else in node_modules goes into a catch-all vendor chunk.
          if (id.includes("node_modules")) {
            return "vendor";
          }
          // Application source is left to Rollup's default code-splitting,
          // which creates per-route chunks thanks to the React.lazy() calls
          // in App.tsx.
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: true,
    clearMocks: true,
  },
});
