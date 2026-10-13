import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: { watch: { ignored: ["**/src-tauri/**"] } },
  build: { outDir: "frontend-dist", target: ["es2022", "safari15"] },
});
