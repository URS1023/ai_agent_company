import { defineConfig } from "vite";
import { svelte, vitePreprocess } from "@sveltejs/vite-plugin-svelte";
import tailwind from "@tailwindcss/postcss";
import { fileURLToPath, URL } from "node:url";
/** 原Svelte插件与原Vite5独立构建，宿主Vue/Vite7只消费编译产物。 */
export default defineConfig({
  plugins: [
    svelte({ emitCss: false, preprocess: vitePreprocess({ script: true }) }),
  ],
  css: { postcss: { plugins: [tailwind()] } },
  build: {
    lib: {
      entry: fileURLToPath(new URL("./src/index.ts", import.meta.url)),
      formats: ["es"],
      fileName: () => "index.js",
    },
    outDir: "dist",
    emptyOutDir: true,
    minify: "esbuild",
  },
});
