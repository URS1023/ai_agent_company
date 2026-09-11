import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { createRequire } from 'node:module'
import { extname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { DASHBOARD_TEMPLATES } from '../lynx/src/reportDashboardTemplates.js'
import { preparePreview } from './preview.mjs'

const require = createRequire(new URL('../../../e2e/package.json', import.meta.url))
const { chromium } = require('@playwright/test')
const root = new URL('../../artifacts/dashboard-viewer/', import.meta.url)
const output = new URL('../../artifacts/dashboard-viewer-browser/', import.meta.url)
const mime = {
  '.html': 'text/html',
  '.js': 'application/javascript',
  '.css': 'text/css',
  '.json': 'application/json',
}
const server = createServer(async (request, response) => {
  const url = new URL(request.url, 'http://127.0.0.1')
  if (url.pathname === '/favicon.ico') {
    response.writeHead(204).end()
    return
  }
  const file = new URL(`.${url.pathname === '/' ? '/index.html' : url.pathname}`, root)
  if (!file.href.startsWith(root.href)) {
    response.writeHead(404).end()
    return
  }
  try {
    const bytes = await readFile(file)
    response.writeHead(200, {
      'Content-Type': mime[extname(file.pathname)] || 'application/octet-stream',
      'X-Content-Type-Options': 'nosniff',
    })
    response.end(bytes)
  } catch {
    response.writeHead(404).end()
  }
})
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
const origin = `http://127.0.0.1:${server.address().port}`
let browser
const report = {
  checkedAt: new Date().toISOString(),
  scope: 'Static template visual smoke, not authenticated Dify or live business data',
  templates: [],
}
try {
  browser = await chromium.launch({ headless: true })
  await mkdir(output, { recursive: true })
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  const errors = []
  const denied = []
  const failedRequests = []
  const httpErrors = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('requestfailed', (request) => failedRequests.push(request.url()))
  page.on('response', (response) => {
    if (response.status() >= 400)
      httpErrors.push({ url: response.url(), status: response.status() })
  })
  await page.route('**/*', (route) => {
    const url = new URL(route.request().url())
    if (url.origin !== origin) {
      denied.push(url.origin)
      return route.abort()
    }
    return route.continue()
  })
  for (const { id } of DASHBOARD_TEMPLATES) {
    const expected = preparePreview(id)
    await page.goto(`${origin}/?template=${id}`)
    await page.locator('.screen-widget').last().waitFor()
    await page.waitForFunction(
      (count) => document.querySelectorAll('.screen-widget').length === count,
      expected.widgets.length,
    )
    assert.equal(await page.locator('.screen-widget').count(), expected.widgets.length)
    assert.equal(await page.locator('iframe,video').count(), 0)
    const dimensions = await page
      .locator('.screen-stage')
      .evaluate((node) => ({ width: node.style.width, height: node.style.height }))
    assert.deepEqual(dimensions, {
      width: `${expected.page.design.width}px`,
      height: `${expected.page.design.height}px`,
    })
    const geometry = await page.locator('.screen-widget').evaluateAll((nodes) =>
      nodes.map((node) => ({
        width: node.style.width,
        height: node.style.height,
        transform: node.style.transform,
      })),
    )
    assert.deepEqual(
      geometry,
      [...expected.widgets]
        .sort((left, right) => (left.orderNum || 0) - (right.orderNum || 0))
        .map((widget) => ({
          width: `${widget.w}px`,
          height: `${widget.h}px`,
          transform: `translate(${widget.x}px, ${widget.y}px) rotate(${widget.rotation || 0}deg)`,
        })),
      id,
    )
    const screenshot = fileURLToPath(new URL(`${id}.png`, output))
    await page.screenshot({ path: screenshot, animations: 'disabled' })
    report.templates.push({ id, widgetCount: expected.widgets.length, dimensions, screenshot })
  }
  const manifest = JSON.parse(await readFile(new URL('.vite/manifest.json', root), 'utf8'))
  assert.ok(manifest['runtime.mjs']?.file, 'Missing reusable dashboard runtime entry')
  const runtimeUrl = `${origin}/${manifest['runtime.mjs'].file}`
  const templateId = 'equipment-digital-ops'
  const metric = preparePreview(templateId).widgets.find(
    (widget) => widget.component === 'JStatsSummary',
  )
  await page.evaluate(
    async ({ runtimeUrl, templateId, slotId }) => {
      const { mountDashboard } = await import(runtimeUrl)
      document.querySelector('#app').style.display = 'none'
      const host = document.createElement('div')
      host.id = 'data-fixture'
      host.style.cssText = 'width:1440px;height:900px'
      document.body.append(host)
      window.dashboardFixture = mountDashboard(host, {
        templateId,
        batch: [{ slotId, rows: [{ name: '验收指标', value: 98.25 }] }],
      })
    },
    { runtimeUrl, templateId, slotId: metric.id },
  )
  const fixture = page.locator('#data-fixture')
  await fixture.getByText('验收指标', { exact: true }).waitFor({ state: 'attached' })
  await page.screenshot({
    path: fileURLToPath(new URL('populated-diagnostic.png', output)),
    animations: 'disabled',
  })
  const layoutTrace = await fixture
    .locator('.stat-label')
    .first()
    .evaluate((node) => {
      const trace = []
      for (let current = node; current; current = current.parentElement) {
        const css = getComputedStyle(current)
        const rect = current.getBoundingClientRect()
        trace.push({
          tag: current.tagName,
          class: current.className,
          display: css.display,
          visibility: css.visibility,
          width: rect.width,
          height: rect.height,
        })
      }
      return trace
    })
  await writeFile(
    new URL('populated-layout-trace.json', output),
    JSON.stringify(layoutTrace, null, 2),
  )
  await fixture.getByText('验收指标', { exact: true }).waitFor({ timeout: 3000 })
  assert.match(await fixture.locator('.stat-value').first().innerText(), /98\.25/)
  const metricBounds = await fixture
    .locator('.stat-value')
    .first()
    .evaluate((node) => {
      const value = node.getBoundingClientRect()
      const widget = node.closest('.screen-widget').getBoundingClientRect()
      return {
        visible: value.width > 0 && value.height > 0,
        contained:
          value.left >= widget.left &&
          value.right <= widget.right &&
          value.top >= widget.top &&
          value.bottom <= widget.bottom,
      }
    })
  assert.deepEqual(metricBounds, { visible: true, contained: true })
  const beforeUpdate = await fixture
    .locator('.screen-widget')
    .evaluateAll((nodes) => nodes.map((node) => node.getAttribute('style')))
  await page.evaluate(
    ({ templateId, slotId }) =>
      window.dashboardFixture.update({
        templateId,
        batch: [{ slotId, rows: [{ name: '更新指标', value: 0 }] }],
      }),
    { templateId, slotId: metric.id },
  )
  await fixture.getByText('更新指标', { exact: true }).waitFor()
  assert.equal((await fixture.locator('.stat-value').first().innerText()).trim(), '0')
  assert.deepEqual(
    await fixture
      .locator('.screen-widget')
      .evaluateAll((nodes) => nodes.map((node) => node.getAttribute('style'))),
    beforeUpdate,
  )
  const invalidUpdate = await page.evaluate(
    ({ templateId, slotId }) => {
      try {
        window.dashboardFixture.update({
          templateId,
          batch: [{ slotId, rows: [{ name: 'invalid', value: 1, style: 'red' }] }],
        })
        return 'accepted'
      } catch (error) {
        return error.message
      }
    },
    { templateId, slotId: metric.id },
  )
  assert.equal(invalidUpdate, 'invalid_series_batch')
  assert.equal(await fixture.getByText('更新指标', { exact: true }).count(), 1)
  await page.screenshot({
    path: fileURLToPath(new URL('populated-equipment.png', output)),
    animations: 'disabled',
  })
  await page.evaluate(() => window.dashboardFixture.dispose())
  assert.equal(await fixture.locator('.screen-widget').count(), 0)
  assert.equal(
    await page.evaluate(() => {
      window.dashboardFixture.dispose()
      try {
        window.dashboardFixture.update({ templateId: 'equipment-digital-ops', batch: [] })
      } catch (error) {
        return error.message
      }
    }),
    'dashboard_disposed',
  )
  report.populatedMetric = { initial: 98.25, updated: 0, stylesUnchanged: true, disposed: true }
  await page.evaluate(
    async ({ runtimeUrl, templateId, slotId }) => {
      const { mountDashboardView } = await import(runtimeUrl)
      const expected = {
        id: 'dashboard-1',
        template_id: templateId,
        design_identity: 'design-1',
        renderer_build_id: 'renderer-1',
      }
      window.apiView = {
        ...expected,
        revision: 1,
        status: 'ready',
        failures: [],
        last_attempt_id: 'batch-1',
        current: {
          batch_id: 'batch-1',
          slots: [
            {
              slot_id: slotId,
              rows: [
                {
                  name: { kind: 'string', value: '精确指标' },
                  value: { kind: 'decimal', value: '98.2500' },
                },
              ],
            },
          ],
        },
      }
      window.apiFixture = mountDashboardView(
        document.querySelector('#data-fixture'),
        window.apiView,
        expected,
      )
      expected.design_identity = 'mutated-by-caller'
    },
    { runtimeUrl, templateId, slotId: metric.id },
  )
  await fixture.getByText('精确指标', { exact: true }).waitFor()
  assert.equal((await fixture.locator('.stat-value').first().innerText()).trim(), '98.2500')
  await page.evaluate(() =>
    window.apiFixture.update({
      ...window.apiView,
      revision: 2,
      status: 'failed',
      last_attempt_id: 'batch-2',
      failures: [{ slot_id: 'metric', code: 'source_query_failed' }],
    }),
  )
  await fixture.getByRole('alert').waitFor()
  assert.equal((await fixture.locator('.stat-value').first().innerText()).trim(), '98.2500')
  assert.deepEqual(
    await fixture
      .locator('.screen-widget')
      .evaluateAll((nodes) => nodes.map((node) => node.getAttribute('style'))),
    beforeUpdate,
  )
  assert.equal(
    await page.evaluate(() => {
      try {
        window.apiFixture.update(window.apiView)
        return 'accepted'
      } catch (error) {
        return error.message
      }
    }),
    'dashboard_revision_stale',
  )
  await page.screenshot({
    path: fileURLToPath(new URL('api-failed-retained.png', output)),
    animations: 'disabled',
  })
  await page.evaluate(() =>
    window.apiFixture.update({
      ...window.apiView,
      revision: 3,
      status: 'empty',
      current: null,
      last_attempt_id: null,
    }),
  )
  await fixture.getByRole('status').waitFor()
  assert.equal(await fixture.getByRole('alert').count(), 0)
  assert.equal(await fixture.getByText('精确指标', { exact: true }).count(), 0)
  await page.evaluate(() => window.apiFixture.dispose())
  assert.equal(await fixture.locator('.screen-widget').count(), 0)
  report.apiData = {
    exactDecimal: '98.2500',
    failedRetainsData: true,
    staleRejected: true,
    emptyStatus: true,
  }
  // Match the local-only view sent by SqlTrialPreview, not a persisted dashboard batch.
  await page.evaluate(
    async ({ runtimeUrl }) => {
      const { mountDashboardView } = await import(runtimeUrl)
      const view = structuredClone(window.apiView)
      view.current.batch_id = 'preview-trial-1'
      view.last_attempt_id = 'preview-trial-1'
      view.current.slots[0].rows = [
        {
          name: { kind: 'string', value: '试跑精确计数' },
          value: { kind: 'integer', value: '18446744073709551616' },
        },
      ]
      window.trialPreviewFixture = mountDashboardView(
        document.querySelector('#data-fixture'),
        view,
        view,
      )
    },
    { runtimeUrl },
  )
  await fixture.getByText('试跑精确计数', { exact: true }).waitFor()
  assert.equal(
    (await fixture.locator('.stat-value').first().innerText()).trim(),
    '18446744073709551616',
  )
  const trialMetricLayout = await fixture
    .locator('.stat-value')
    .first()
    .evaluate((node) => ({
      clientWidth: node.clientWidth,
      scrollWidth: node.scrollWidth,
      clipped: node.scrollWidth > node.clientWidth,
    }))
  assert.deepEqual(
    await fixture
      .locator('.screen-widget')
      .evaluateAll((nodes) => nodes.map((node) => node.getAttribute('style'))),
    beforeUpdate,
  )
  await page.screenshot({
    path: fileURLToPath(new URL('trial-preview-exact-integer.png', output)),
    animations: 'disabled',
  })
  await page.evaluate(() => window.trialPreviewFixture.dispose())
  assert.equal(await fixture.locator('.screen-widget').count(), 0)
  report.trialPreview = {
    scope:
      'Renderer receives the same single-slot local view shape as the React preview; no authenticated API calls',
    exactInteger: '18446744073709551616',
    stylesUnchanged: true,
    disposed: true,
    metricLayout: trialMetricLayout,
  }
  const selectedMetrics = preparePreview(templateId)
    .widgets.filter((widget) => widget.component === 'JStatsSummary')
    .slice(0, 2)
  assert.equal(selectedMetrics.length, 2)
  await page.evaluate(
    async ({ runtimeUrl, slots }) => {
      const { mountDashboardView } = await import(runtimeUrl)
      const view = structuredClone(window.apiView)
      view.current = { batch_id: 'preview-combined', slots }
      view.last_attempt_id = 'preview-combined'
      window.combinedView = view
      window.combinedFixture = mountDashboardView(
        document.querySelector('#data-fixture'),
        view,
        view,
      )
    },
    {
      runtimeUrl,
      slots: selectedMetrics.map((widget, index) => ({
        slot_id: widget.id,
        rows: [
          {
            name: { kind: 'string', value: index === 0 ? '试跑设备数' : '试跑合格率' },
            value: {
              kind: index === 0 ? 'integer' : 'decimal',
              value: index === 0 ? '73' : '98.25',
            },
          },
        ],
      })),
    },
  )
  await fixture.getByText('试跑设备数', { exact: true }).waitFor()
  await fixture.getByText('试跑合格率', { exact: true }).waitFor()
  assert.deepEqual(
    await fixture
      .locator('.stat-value')
      .evaluateAll((nodes) => nodes.map((node) => node.textContent.trim())),
    ['73', '98.25'],
  )
  assert.deepEqual(
    await fixture
      .locator('.screen-widget')
      .evaluateAll((nodes) => nodes.map((node) => node.getAttribute('style'))),
    beforeUpdate,
  )
  assert.equal(
    await page.evaluate(() => {
      const invalid = structuredClone(window.combinedView)
      invalid.revision += 1
      invalid.current.slots[1].slot_id = invalid.current.slots[0].slot_id
      try {
        window.combinedFixture.update(invalid)
        return 'accepted'
      } catch (error) {
        return error.message
      }
    }),
    'invalid_series_batch',
  )
  assert.equal(await fixture.getByText('试跑合格率', { exact: true }).count(), 1)
  await page.screenshot({
    path: fileURLToPath(new URL('combined-trial-preview.png', output)),
    animations: 'disabled',
  })
  await page.evaluate(() => window.combinedFixture.dispose())
  assert.equal(await fixture.locator('.screen-widget').count(), 0)
  report.combinedPreview = {
    slotCount: 2,
    values: ['73', '98.25'],
    stylesUnchanged: true,
    duplicateRejectedAtomically: true,
    disposed: true,
  }
  await page.goto(`${origin}/?template=unregistered`)
  await page.getByRole('alert').waitFor()
  assert.equal(await page.locator('.screen-widget').count(), 0)
  assert.deepEqual(errors, [])
  assert.deepEqual(denied, [])
  assert.deepEqual(failedRequests, [])
  assert.deepEqual(httpErrors, [])
  report.checkedTemplateCount = report.templates.length
  report.geometryChecked = true
  report.browser = browser.version()
  await writeFile(new URL('verification.json', output), `${JSON.stringify(report, null, 2)}\n`)
  console.log(JSON.stringify(report))
} finally {
  await browser?.close()
  await new Promise((resolve) => server.close(resolve))
}
