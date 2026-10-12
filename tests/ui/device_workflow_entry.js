/*
 * 中文说明（设备工作流入口的浏览器验收）：
 * 验证行为：从设备入口打开工作流的交互路径（入口可见性、跳转后落地页与业务流程一致）。
 * 运行方式：需隔离服务（TEST5_E2E_BASE_URL，默认 http://127.0.0.1:8011）+ 本机 Edge；
 *   截图/结果输出目录可用 TEST5_E2E_OUTPUT 覆盖（默认 ui_test_report/device_workflow_redesign_20260907）。
 * 边界：会产生真实的运行/截图产物文件；业务断言以页面可见状态为准。
 */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const puppeteer = require('puppeteer-core')

const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011'
const EDGE = process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const OUTPUT = path.resolve(process.env.TEST5_E2E_OUTPUT || 'ui_test_report/device_workflow_redesign_20260907')

const pause = (milliseconds = 250) => new Promise(resolve => setTimeout(resolve, milliseconds))

async function seedSavedSession(page) {
  const statePath = path.resolve('ui_test_report/dogfood_20260904/auth-state.json')
  if (!fs.existsSync(statePath)) return
  const state = JSON.parse(fs.readFileSync(statePath, 'utf8'))
  const entries = state.origins?.flatMap(origin => origin.localStorage || []) || []
  if (!entries.length) return
  await page.goto(BASE, { waitUntil: 'domcontentloaded' })
  await page.evaluate(values => {
    for (const item of values) localStorage.setItem(item.name, item.value)
  }, entries)
}

async function loginIfNeeded(page) {
  await page.goto(BASE + '/alerts', { waitUntil: 'domcontentloaded' })
  const login = await page.$('#platform-login-form')
  if (!login) return
  const password = process.env.TEST5_E2E_PASSWORD
  assert(password, 'TEST5_E2E_PASSWORD is required when the saved session is unavailable or expired')
  await page.click('#login-username')
  await page.type('#login-username', process.env.TEST5_E2E_USERNAME || 'workspace-admin', { delay: 55 })
  await page.click('#login-password')
  await page.type('#login-password', password, { delay: 55 })
  await pause(300)
  await page.click('#login-btn')
  await page.waitForSelector('#business-module-frame', { timeout: 30000 })
}

async function moduleFrame(page, selector) {
  if (await page.$('#analytics-app')) {
    await page.waitForSelector(selector, { timeout: 30000 })
    return page
  }
  const frameElement = await page.waitForSelector('#business-module-frame', { timeout: 30000 })
  const frame = await frameElement.contentFrame()
  assert(frame, 'business module iframe is available')
  await frame.waitForSelector(selector, { timeout: 30000 })
  return frame
}

async function searchAndOpenFirstDevice(page, domain) {
  const rootPath = domain === 'alert' ? '/alerts' : '/quality'
  if (new URL(page.url()).pathname !== rootPath) {
    await page.goto(BASE + rootPath, { waitUntil: 'domcontentloaded' })
  }
  let frame = await moduleFrame(page, `.device-entry-page[data-domain="${domain}"]`)
  assert.equal(await frame.$('.alert-tabs, .qc-tabs'), null, 'small-module tabs are absent')
  await frame.waitForFunction(() => !document.querySelector('.device-card-skeleton'), { timeout: 30000 })
  const cards = await frame.$$('.device-card[data-device-id]')
  assert(cards.length > 0, `${domain} has at least one configured device for the real-browser journey`)

  const first = cards[0]
  const deviceName = await first.$eval('h2', element => element.textContent.trim())
  const deviceCode = await first.$eval('.device-card-copy p', element => element.textContent.split('·')[0].trim())
  const search = await frame.$('.device-search input')
  await search.click({ clickCount: 3 })
  await search.type(deviceCode || deviceName.slice(0, 2), { delay: 70 })
  await pause(250)
  await frame.click('.device-search button[type="submit"]')
  await frame.waitForFunction(() => !document.querySelector('.device-card-skeleton'), { timeout: 30000 })
  await frame.waitForSelector('.device-card[data-device-id] .device-open', { timeout: 30000 })
  await pause(350)
  await frame.click('.device-card[data-device-id] .device-open')

  await page.waitForFunction(expectedDomain => {
    const expectedRoot = expectedDomain === 'alert' ? 'alerts' : 'quality'
    return new RegExp(`^/${expectedRoot}/devices/[A-Za-z0-9_-]+/workflow$`).test(location.pathname)
  }, { timeout: 30000 }, domain)
  frame = await moduleFrame(page, '.wf-design-page')
  await frame.waitForFunction(() => document.querySelectorAll('.wf-node').length >= 3, { timeout: 30000 })
  const heading = await frame.$eval('.wf-workbench-identity h1', element => element.textContent.trim())
  assert(heading.includes(deviceName), 'workflow heading keeps the selected device context')
  assert((await frame.$$eval('.wf-node', nodes => nodes.length)) >= 3, 'device workflow opens with an editable graph')
  assert(await page.$('html.workflow-standalone body.workflow-standalone #analytics-app'), 'device workflow is mounted as a standalone page')
  assert.equal(await page.$('#business-module-frame, .enterprise-header, .enterprise-sidebar, .enterprise-contextbar'), null, 'no platform shell or iframe surrounds the full-screen designer')
  const viewport = await frame.$eval('.wf-design-page', element => ({
    height: element.getBoundingClientRect().height,
    viewportHeight: window.innerHeight,
  }))
  assert(Math.abs(viewport.height - viewport.viewportHeight) <= 2, 'designer fills the complete browser viewport')
  await frame.click('.wf-canvas-add .wf-primary')
  await frame.waitForSelector('.wf-palette-dialog', { visible: true })
  const scope = await frame.$eval('.wf-palette-header>span', element => element.textContent.trim())
  assert(scope.includes(domain === 'alert' ? '设备预警' : '质量检查'), 'node library is scoped to the active business')
  const tabs = await frame.$$eval('.wf-palette-tabs button', buttons => buttons.map(button => button.textContent.trim()))
  assert.deepEqual(tabs, ['节点', '工具', '开始', 'Snippets'], 'node selector keeps the Dify tab structure')
  if (domain === 'alert') await page.screenshot({ path: path.join(OUTPUT, '05-dify-node-selector.png'), fullPage: true })
  await frame.$$eval('.wf-palette-tabs button', buttons => buttons.find(button => button.textContent.trim() === 'Snippets')?.click())
  await frame.waitForSelector('.wf-snippet-list>button', { visible: true })
  await frame.click('.wf-palette-close')
  return { frame, deviceName, deviceCode }
}

async function clickReturnToDevices(frame) {
  const target = await frame.$('.wf-workbench-back')
  assert(target, 'device workflow has a clear top-left return action')
  await target.click()
}

async function main() {
  fs.mkdirSync(OUTPUT, { recursive: true })
  const browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: process.env.TEST5_HEADFUL !== '1',
    slowMo: process.env.TEST5_HEADFUL === '1' ? 45 : 0,
    args: ['--disable-gpu', '--no-first-run', '--window-size=1500,1000'],
    defaultViewport: { width: 1500, height: 920 },
    timeout: 60000,
  })
  const page = await browser.newPage()
  const failures = []
  page.on('pageerror', error => failures.push('pageerror: ' + error.message))
  page.on('response', response => {
    if (response.status() >= 500) failures.push(`${response.status()} ${response.url()}`)
  })
  try {
    await seedSavedSession(page)
    await loginIfNeeded(page)

    const alert = await searchAndOpenFirstDevice(page, 'alert')
    await page.screenshot({ path: path.join(OUTPUT, '01-alert-workflow.png'), fullPage: true })
    await clickReturnToDevices(alert.frame)
    await page.waitForFunction(() => location.pathname === '/alerts', { timeout: 30000 })
    let frame = await moduleFrame(page, '.device-entry-page[data-domain="alert"]')
    await frame.waitForFunction(expected => document.querySelector('.device-search input')?.value === expected, { timeout: 30000 }, alert.deviceCode)

    await page.goto(BASE + '/alerts/points', { waitUntil: 'domcontentloaded' })
    await page.waitForFunction(() => location.pathname === '/alerts', { timeout: 30000 })
    frame = await moduleFrame(page, '.device-entry-page[data-domain="alert"]')
    assert.equal(await frame.$('.alert-tabs'), null, 'legacy alert submodule redirects to the unified entry')
    await page.screenshot({ path: path.join(OUTPUT, '02-alert-device-entry.png'), fullPage: true })

    const quality = await searchAndOpenFirstDevice(page, 'quality')
    await page.screenshot({ path: path.join(OUTPUT, '03-quality-workflow.png'), fullPage: true })
    await clickReturnToDevices(quality.frame)
    await page.waitForFunction(() => location.pathname === '/quality', { timeout: 30000 })

    await page.goto(BASE + '/quality/templates', { waitUntil: 'domcontentloaded' })
    await page.waitForFunction(() => location.pathname === '/quality', { timeout: 30000 })
    frame = await moduleFrame(page, '.device-entry-page[data-domain="quality"]')
    assert.equal(await frame.$('.qc-tabs'), null, 'legacy quality submodule redirects to the unified entry')
    await page.screenshot({ path: path.join(OUTPUT, '04-quality-device-entry.png'), fullPage: true })

    assert.deepEqual(failures, [], 'no page errors or server failures occurred')
    console.log(JSON.stringify({ ok: true, screenshots: OUTPUT, alertDevice: alert.deviceName, qualityDevice: quality.deviceName }))
  } finally {
    await browser.close()
  }
}

main().catch(error => {
  console.error(error.stack || error)
  process.exitCode = 1
})
