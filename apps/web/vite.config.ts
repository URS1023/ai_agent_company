import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { fileURLToPath, URL } from "node:url";

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
      "@dify-workflow": fileURLToPath(
        new URL("../../packages/dify-workflow/src/index.tsx", import.meta.url),
      ),
      "@openwebui-chat": fileURLToPath(
        new URL("../../packages/openwebui-chat/dist/index.js", import.meta.url),
      ),
    },
    dedupe: ["react", "react-dom", "reactflow", "immer"],
  },
  esbuild: { jsx: "automatic", jsxImportSource: "react" },
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8080", changeOrigin: false } },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
