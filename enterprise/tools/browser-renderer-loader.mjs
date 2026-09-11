import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { createRequire } from 'node:module'
import { dirname, extname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = new URL('../../', import.meta.url)
const artifact = new URL('../artifacts/renderer-loader-browser/', import.meta.url)
const legacy = process.argv[2]
assert.ok(legacy, 'Pass existing legacy frontend dependencies')
const requireLegacy = createRequire(join(legacy, 'package.json'))
const { build } = await import(
  pathToFileURL(join(dirname(requireLegacy.resolve('vite/package.json')), 'dist/node/index.js'))
    .href
)
await mkdir(artifact, { recursive: true })
await build({
  configFile: false,
  build: {
    outDir: fileURLToPath(artifact),
    emptyOutDir: false,
    lib: {
      entry: fileURLToPath(
        new URL('web/app/components/enterprise/dashboards/renderer-loader.ts', root),
      ),
      formats: ['es'],
      fileName: () => 'loader.js',
    },
  },
})
const metadata = JSON.parse(
  await readFile(
    new URL('web/app/components/enterprise/dashboards/renderer-build.json', root),
    'utf8',
  ),
)
const require = createRequire(new URL('e2e/package.json', root))
const { chromium } = require('@playwright/test')
const mime = { '.js': 'application/javascript', '.css': 'text/css', '.json': 'application/json' }
const failures = []
const requests = []
const server = createServer(async (request, response) => {
  const url = new URL(request.url, 'http://127.0.0.1')
  requests.push(url.pathname)
  if (url.pathname === '/portal/enterprise/dashboards/screen') {
    response
      .writeHead(200, { 'Content-Type': 'text/html' })
      .end(
        '<!doctype html><html><head><meta charset="utf-8"></head><body><div id="screen" style="width:1280px;height:720px"></div></body></html>',
      )
    return
  }
  if (url.pathname === '/favicon.ico') {
    response.writeHead(204).end()
    return
  }
  const publicRoot = new URL('web/public/', root)
  const file =
    url.pathname === '/loader.js'
      ? new URL('loader.js', artifact)
      : new URL(`.${url.pathname.replace(/^\/portal/, '')}`, publicRoot)
  if (file.href !== new URL('loader.js', artifact).href && !file.href.startsWith(publicRoot.href)) {
    response.writeHead(404).end()
    return
  }
  try {
    const bytes = await readFile(file)
    response
      .writeHead(200, {
        'Content-Type': mime[extname(file.pathname)] ?? 'application/octet-stream',
        'X-Content-Type-Options': 'nosniff',
      })
      .end(bytes)
  } catch {
    failures.push(url.pathname)
    response.writeHead(404).end()
  }
})
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
let browser
try {
  browser = await chromium.launch({ headless: true })
  const page = await browser.newPage({ viewport: { width: 1360, height: 800 } })
  page.on('pageerror', (error) => failures.push(error.message))
  await page.goto(`http://127.0.0.1:${server.address().port}/portal/enterprise/dashboards/screen`)
  const result = await page.evaluate(
    async ({ buildId }) => {
      const { loadDashboardRenderer } = await import('/loader.js')
      const element = document.querySelector('#screen')
      const view = {
        id: 'screen',
        name: 'Screen',
        revision: 1,
        template_id: 'equipment-digital-ops',
        design_identity: 'a'.repeat(64),
        renderer_build_id: buildId,
        status: 'empty',
        current: null,
        last_attempt_id: null,
        failures: [],
      }
      const runtime = await loadDashboardRenderer(element, view, '/portal')
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))
      const mounted = element.childElementCount > 0
      const styles = [...document.querySelectorAll('link[data-dashboard-renderer]')].map(
        (link) => ({ href: link.getAttribute('href'), loaded: Boolean(link.sheet) }),
      )
      runtime.update({ ...view, revision: 2 })
      await new Promise((resolve) => requestAnimationFrame(resolve))
      runtime.dispose()
      return {
        mounted,
        styles,
        disposed: element.childElementCount === 0,
        stylesRemoved: document.querySelectorAll('link[data-dashboard-renderer]').length === 0,
      }
    },
    { buildId: metadata.rendererBuildId },
  )
  assert.equal(result.mounted, true)
  assert.ok(
    result.styles.length > 0 &&
      result.styles.every(
        (item) => item.loaded && item.href.startsWith('/portal/enterprise/renderers/'),
      ),
  )
  assert.equal(result.disposed, true)
  assert.equal(result.stylesRemoved, true)
  assert.deepEqual(failures, [])
  await page.route('**/*.css', (route) => route.fulfill({ status: 404, body: '' }))
  await page.reload()
  const styleFailure = await page.evaluate(
    async ({ buildId }) => {
      const { loadDashboardRenderer } = await import('/loader.js')
      const element = document.querySelector('#screen')
      const view = {
        id: 'screen',
        name: 'Screen',
        revision: 1,
        template_id: 'equipment-digital-ops',
        design_identity: 'a'.repeat(64),
        renderer_build_id: buildId,
        status: 'empty',
        current: null,
        last_attempt_id: null,
        failures: [],
      }
      let errorCode = null
      try {
        await loadDashboardRenderer(element, view, '/portal')
      } catch (error) {
        errorCode = error.message
      }
      return {
        errorCode,
        mounted: element.childElementCount > 0,
        styleCount: document.querySelectorAll('link[data-dashboard-renderer]').length,
      }
    },
    { buildId: metadata.rendererBuildId },
  )
  assert.deepEqual(styleFailure, {
    errorCode: 'dashboard_style_failed',
    mounted: false,
    styleCount: 0,
  })
  assert.deepEqual(failures, [])
  const report = {
    checkedAt: new Date().toISOString(),
    scope:
      'Actual compiled loader, CSS and module transport on nested prefixed fixture page; not authenticated Dify or business source integration',
    browser: browser.version(),
    rendererBuildId: metadata.rendererBuildId,
    ...result,
    styleFailure,
    requests,
  }
  await writeFile(new URL('verification.json', artifact), JSON.stringify(report, null, 2) + '\n')
  console.log(JSON.stringify(report))
} finally {
  await browser?.close()
  await new Promise((resolve) => server.close(resolve))
}
