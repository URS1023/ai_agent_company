/** Capture real Test5 report viewer screenshots for direct Lynx imports. */
import assert from 'node:assert/strict'
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import puppeteer from 'puppeteer-core'

const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011'
const PASSWORD = process.env.TEST5_E2E_PASSWORD
const EDGE = process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const OFFSET = Math.max(0, Number(process.env.TEST5_CAPTURE_OFFSET || 0))
const LIMIT = Math.max(1, Number(process.env.TEST5_CAPTURE_LIMIT || 1000))
const OUTPUT = path.resolve('apps/analytics/public/jimu-screen/report-thumbnails')

async function api(pathname, options = {}) {
  const response = await fetch(BASE + pathname, options)
  const body = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(body.message || body.detail || `HTTP ${response.status}`)
  return body.data ?? body
}

async function main() {
  assert(PASSWORD, 'TEST5_E2E_PASSWORD is required')
  const session = await api('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: 'workspace-admin', password: PASSWORD }),
  })
  const workspaceId = session.workspaces?.[0]?.id
  assert(session.token && workspaceId, 'login response is missing token or workspace')
  const headers = {
    Authorization: `Bearer ${session.token}`,
    'X-Workspace-Id': workspaceId,
  }
  const reports = (await api('/api/v1/reports/list', { headers }))
    .filter(report => report.lineage?.entry === 'lynx_interface_import')
    .sort((left, right) => Number(right.lineage.sourceReportId) - Number(left.lineage.sourceReportId))
    .slice(OFFSET, OFFSET + LIMIT)
  assert(reports.length, 'no direct Lynx reports are available to capture')
  fs.mkdirSync(OUTPUT, { recursive: true })

  const browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: true,
    args: ['--no-sandbox', '--disable-gpu'],
    defaultViewport: { width: 1600, height: 900, deviceScaleFactor: 1 },
  })
  const page = await browser.newPage()
  await page.evaluateOnNewDocument((token, workspace) => {
    localStorage.setItem('agent_token', token)
    localStorage.setItem('agent_workspace_id', workspace)
  }, session.token, workspaceId)

  const results = []
  try {
    for (const report of reports) {
      const sourceId = String(report.lineage.sourceReportId)
      const failures = []
      const onPageError = error => failures.push(`pageerror: ${error.message}`)
      const onResponse = response => {
        const pathname = new URL(response.url()).pathname
        if (response.status() >= 400 && pathname !== '/favicon.ico') {
          failures.push(`${response.status()} ${pathname}`)
        }
      }
      page.on('pageerror', onPageError)
      page.on('response', onResponse)
      const reportPath = `/report/view/${report.id}?workspace_id=${encodeURIComponent(workspaceId)}`
      await page.goto(`${BASE}/analytics-app?path=${encodeURIComponent(reportPath)}`, {
        waitUntil: 'domcontentloaded', timeout: 30000,
      })
      await page.waitForSelector('.dashboard-stage .screen-stage', { timeout: 30000 })
      await page.waitForFunction(
        () => document.querySelectorAll('.screen-stage .screen-widget').length > 0,
        { timeout: 30000 },
      )
      // Dynamic source widgets query concurrently after the canvas mounts. A
      // screenshot taken at the first paint would capture loading placeholders
      // instead of the real report data, so wait for every visible query state
      // to settle.  Static Lynx reports satisfy this condition immediately.
      await page.waitForFunction(
        () => document.querySelectorAll('.screen-stage .data-empty-state[role="status"]').length === 0,
        { timeout: 60000 },
      )
      await page.addStyleTag({ content: `
        .viewer-tools { display: none !important; }
        *, *::before, *::after { animation: none !important; transition: none !important; }
      ` })
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
      const metrics = await page.evaluate(() => ({
        widgetCount: document.querySelectorAll('.screen-stage .screen-widget').length,
        dataErrors: document.querySelectorAll('.data-load-error').length,
        loading: document.querySelectorAll('.screen-stage .data-empty-state[role="status"], .stage-loading').length,
        stage: (() => {
          const rect = document.querySelector('.screen-stage')?.getBoundingClientRect()
          return rect ? { width: Math.round(rect.width), height: Math.round(rect.height) } : null
        })(),
      }))
      const filename = `lynx-${sourceId}.png`
      const target = path.join(OUTPUT, filename)
      await page.screenshot({ path: target, type: 'png', fullPage: false })
      const bytes = fs.readFileSync(target)
      results.push({
        sourceId,
        filename,
        bytes: bytes.length,
        sha256: crypto.createHash('sha256').update(bytes).digest('hex'),
        ...metrics,
        failures,
      })
      console.log(`captured source=${sourceId} widgets=${metrics.widgetCount} dataErrors=${metrics.dataErrors}`)
      page.off('pageerror', onPageError)
      page.off('response', onResponse)
    }
  } finally {
    await browser.close()
  }
  const manifest = {
    generatedAt: new Date().toISOString(),
    base: BASE,
    workspaceId,
    offset: OFFSET,
    count: results.length,
    results,
  }
  fs.writeFileSync(path.join(OUTPUT, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n')
  if (results.some(result => result.failures.length || result.widgetCount === 0 || result.dataErrors || result.loading)) process.exitCode = 2
}

main().catch(error => {
  console.error(error.stack || error)
  process.exitCode = 1
})
