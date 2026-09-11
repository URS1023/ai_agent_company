// Real-browser download smoke check against a separately started local web build.
// It neither logs in nor creates, publishes, binds or runs a business workflow.
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const requireE2E = createRequire(new URL('../../e2e/package.json', import.meta.url))
const { chromium } = requireE2E('@playwright/test')
const base = new URL(process.argv[2] ?? 'http://127.0.0.1:3001')
assert.equal(base.protocol, 'http:')
assert.ok(['127.0.0.1', 'localhost'].includes(base.hostname), 'Use a local test frontend')
assert.equal(base.pathname, '/')
assert.equal(base.username + base.password + base.search + base.hash, '')
const output = new URL('../artifacts/browser-workflow-downloads/', import.meta.url)
await mkdir(output, { recursive: true })
const browser = await chromium.launch({ headless: true })
const evidence = {
  checkedAt: new Date().toISOString(),
  origin: base.origin,
  browser: browser.version(),
  downloads: [],
}
try {
  const context = await browser.newContext({ acceptDownloads: true })
  const page = await context.newPage()
  for (const scenario of ['alert', 'quality']) {
    // A plain link exercises Chromium's real attachment handling without bypassing
    // product authentication to manufacture a logged-in device configuration page.
    const href = new URL(`/enterprise/workflow-templates/${scenario}`, base).href
    await page.setContent(`<a href="${href}">Download ${scenario}</a>`)
    const pending = page.waitForEvent('download')
    await page.getByRole('link', { name: `Download ${scenario}`, exact: true }).click()
    const download = await pending
    assert.equal(await download.failure(), null)
    assert.equal(download.suggestedFilename(), `default-${scenario}.yml`)
    const path = fileURLToPath(new URL(`default-${scenario}.yml`, output))
    await download.saveAs(path)
    const actual = await readFile(path)
    const expected = await readFile(
      new URL(`../workflows/default-${scenario}.yml`, import.meta.url),
    )
    assert.deepEqual(actual, expected, 'The downloaded asset must match its reviewed source')
    evidence.downloads.push({
      scenario,
      path,
      bytes: actual.length,
      sha256: createHash('sha256').update(actual).digest('hex'),
    })
  }
  const missing = await context.request.get(
    new URL('/enterprise/workflow-templates/unknown', base).href,
  )
  assert.equal(missing.status(), 404)
  await context.close()
} finally {
  await browser.close()
}
await writeFile(new URL('verification.json', output), `${JSON.stringify(evidence, null, 2)}\n`)
process.stdout.write(`${JSON.stringify(evidence, null, 2)}\n`)
