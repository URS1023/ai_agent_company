import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  base: '/assets/analytics/',
  plugins: [vue()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  // Open LAN clients may still request chunks from the previous build.
  build: { outDir: '../web/assets/analytics', emptyOutDir: false, chunkSizeWarningLimit: 1500 },
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
})
