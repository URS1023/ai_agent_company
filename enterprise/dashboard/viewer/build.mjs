import assert from 'node:assert/strict'
import { mkdir, copyFile, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { dirname, isAbsolute, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { locateRendererAssets, preserveMetricPrecision } from './renderer-compat.mjs'

const dependencyRoot = process.argv[2]
assert.ok(
  dependencyRoot && isAbsolute(dependencyRoot),
  'Pass the existing frontend dependency directory',
)
const require = createRequire(join(dependencyRoot, 'package.json'))
const { build } = await import(
  pathToFileURL(join(dirname(require.resolve('vite/package.json')), 'dist/node/index.js')).href
)
const vue = require('@vitejs/plugin-vue')
const root = fileURLToPath(new URL('./', import.meta.url))
const output = fileURLToPath(new URL('../../artifacts/dashboard-viewer/', import.meta.url))
await build({
  configFile: false,
  root,
  base: './',
  publicDir: false,
  plugins: [
    {
      name: 'enterprise-metric-precision',
      enforce: 'pre',
      transform(source, id) {
        const chart = fileURLToPath(
          new URL('../lynx-renderer/src/components/JimuChart.vue', import.meta.url),
        )
        if (id.replaceAll('\\', '/') === chart.replaceAll('\\', '/'))
          return { code: locateRendererAssets(source), map: null }
        const target = fileURLToPath(
          new URL('../lynx-renderer/src/components/JimuWidgetRenderer.vue', import.meta.url),
        )
        if (id.replaceAll('\\', '/') !== target.replaceAll('\\', '/')) return null
        return { code: preserveMetricPrecision(source), map: null }
      },
    },
    (vue.default ?? vue)(),
  ],
  resolve: {
    alias: [
      { find: '@/api/request', replacement: join(root, 'no-business-network.mjs') },
      {
        find: '@/utils',
        replacement: fileURLToPath(new URL('../lynx-renderer/src/utils/', import.meta.url)),
      },
      { find: 'vue', replacement: require.resolve('vue/dist/vue.runtime.esm-bundler.js') },
      { find: 'echarts', replacement: require.resolve('echarts') },
    ],
  },
  build: {
    outDir: output,
    emptyOutDir: false,
    manifest: true,
    rollupOptions: {
      input: { index: join(root, 'index.html'), runtime: join(root, 'runtime.mjs') },
      preserveEntrySignatures: 'strict',
    },
  },
})
await mkdir(join(output, 'jimu-screen'), { recursive: true })
await copyFile(
  fileURLToPath(new URL('../lynx-renderer/public/china.json', import.meta.url)),
  join(output, 'jimu-screen/china.json'),
)
await writeFile(
  join(output, 'build-verification.json'),
  `${JSON.stringify(
    {
      checkedAt: new Date().toISOString(),
      scope: 'Template preview and data-only mount API; no source transport, SQL or publication',
      vite: require('vite/package.json').version,
      vue: require('vue/package.json').version,
      echarts: require('echarts/package.json').version,
      rendererCompatibility: [
        'metric-number-no-forced-rounding',
        'equipment-compact-metric-layout',
        'bundle-relative-map-resource',
      ],
    },
    null,
    2,
  )}\n`,
)
console.log(output)
