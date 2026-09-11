import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const root = new URL('../../web/node_modules/uuid/dist/', import.meta.url)
const require = createRequire(new URL('../../e2e/package.json', import.meta.url))
const { chromium } = require('@playwright/test')
let browser
try {
  browser = await chromium.launch({ headless: true })
  const page = await browser.newPage()
  await page.route('http://dashboard-fixture.test/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/')
      return route.fulfill({
        contentType: 'text/html',
        body: '<!doctype html><title>Dashboard request-key verification</title>',
      })
    if (!/^\/[a-z0-9-]+\.js$/.test(path)) return route.fulfill({ status: 404 })
    return route.fulfill({
      contentType: 'application/javascript',
      body: await readFile(new URL(path.slice(1), root)),
    })
  })
  await page.goto('http://dashboard-fixture.test/')
  const result = await page.evaluate(async () => {
    const { default: v4 } = await import('/v4.js')
    return {
      secureContext: isSecureContext,
      nativeUUID: typeof crypto.randomUUID,
      randomValues: typeof crypto.getRandomValues,
      keys: Array.from({ length: 100 }, () => v4()),
    }
  })
  assert.equal(result.secureContext, false)
  assert.equal(result.nativeUUID, 'undefined')
  assert.equal(result.randomValues, 'function')
  assert.equal(new Set(result.keys).size, 100)
  for (const key of result.keys)
    assert.match(key, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
  const output = new URL('../artifacts/dashboard-request-key/', import.meta.url)
  await mkdir(output, { recursive: true })
  const report = {
    checkedAt: new Date().toISOString(),
    browser: browser.version(),
    scope:
      'Installed UUID browser implementation on non-secure HTTP origin with local-file route fulfillment; not live LAN or complete dashboard UI',
    secureContext: result.secureContext,
    nativeUUID: result.nativeUUID,
    generatedDistinctKeys: result.keys.length,
  }
  await writeFile(new URL('verification.json', output), JSON.stringify(report, null, 2))
  console.log(JSON.stringify({ ...report, output: fileURLToPath(output) }))
} finally {
  await browser?.close()
}
