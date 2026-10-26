import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev proxy keeps API calls same-origin so Django session auth works
// without CORS. Django runs on :8000, this dev server on :5173.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
