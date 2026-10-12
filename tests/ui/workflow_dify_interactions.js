/*
 * 中文说明（参考实现风格的工作流交互验收）：
 * 验证行为：对齐参考实现（Dify 风格）的编排交互在真实浏览器中的行为。
 * 运行方式：需隔离服务与本机 Edge；会产生工作流编辑记录（隔离环境）。
 * 边界：以交互结果断言为主，不覆盖后端业务语义。
 */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const puppeteer = require('puppeteer-core')

const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011'
const EDGE = process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const OUTPUT = path.resolve(process.env.TEST5_E2E_OUTPUT || 'ui_test_report/workflow_dify_interactions_20260907')
const pause = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds))

async function seedSavedSession(page) {
  const statePath = path.resolve('ui_test_report/dogfood_20260904/auth-state.json')
  if (!fs.existsSync(statePath)) return
  const state = JSON.parse(fs.readFileSync(statePath, 'utf8'))
  const entries = state.origins?.flatMap(origin => origin.localStorage || []) || []
  if (!entries.length) return
  await page.goto(BASE, { waitUntil: 'domcontentloaded' })
  await page.evaluate(values => values.forEach(item => localStorage.setItem(item.name, item.value)), entries)
}

async function loginIfNeeded(page) {
  await page.goto(BASE + '/alerts', { waitUntil: 'domcontentloaded' })
  if (!await page.$('#platform-login-form')) return
  const password = process.env.TEST5_E2E_PASSWORD
  assert(password, 'TEST5_E2E_PASSWORD is required when the saved session is unavailable or expired')
  await page.type('#login-username', process.env.TEST5_E2E_USERNAME || 'workspace-admin', { delay: 55 })
  await page.type('#login-password', password, { delay: 55 })
  await pause(250)
  await page.click('#login-btn')
  await page.waitForSelector('#business-module-frame', { timeout: 30000 })
}

async function frameFor(page, selector) {
  if (await page.$('#analytics-app')) {
    await page.waitForSelector(selector, { timeout: 30000 })
    return page
  }
  const element = await page.waitForSelector('#business-module-frame', { timeout: 30000 })
  const frame = await element.contentFrame()
  assert(frame, 'business module iframe is available')
  await frame.waitForSelector(selector, { timeout: 30000 })
  return frame
}

async function buttonByText(frame, selector, text) {
  const buttons = await frame.$$(selector)
  for (const button of buttons) {
    if ((await button.evaluate(element => element.textContent.trim())).includes(text)) return button
  }
  throw new Error(`Button not found: ${text}`)
}

async function waitForDialog(frame, title) {
  await frame.waitForFunction(expected => [...document.querySelectorAll('.el-dialog')].some(dialog => dialog.textContent.includes(expected)), { timeout: 30000 }, title)
}

async function closeDialog(frame, title) {
  const dialogs = await frame.$$('.el-dialog')
  for (const dialog of dialogs) {
    if (!(await dialog.evaluate((element, expected) => element.textContent.includes(expected), title))) continue
    const cancel = await dialog.$('.el-dialog__footer button')
    if (cancel) await cancel.click()
    else await dialog.$eval('.el-dialog__headerbtn', button => button.click())
    break
  }
  await frame.waitForFunction(expected => ![...document.querySelectorAll('.el-dialog')].some(dialog => {
    const rect = dialog.getBoundingClientRect()
    return dialog.textContent.includes(expected) && rect.width > 0 && rect.height > 0 && getComputedStyle(dialog).visibility !== 'hidden'
  }), { timeout: 10000 }, title)
}

async function version(frame) {
  return frame.$eval('.wf-save-state', element => Number(element.textContent.match(/v(\d+)/)?.[1] || 0))
}

async function waitForAutoSave(frame, previousVersion) {
  await frame.waitForFunction(previous => {
    const match = document.querySelector('.wf-save-state')?.textContent.match(/v(\d+)/)
    const state = document.querySelector('.wf-autosave-state')?.textContent || ''
    return Number(match?.[1] || 0) > previous && /^自动保存 \d{2}:\d{2}:\d{2}/.test(state)
  }, { timeout: 30000 }, previousVersion)
  return version(frame)
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
  page.on('response', response => { if (response.status() >= 500) failures.push(`${response.status()} ${response.url()}`) })

  try {
    await seedSavedSession(page)
    await loginIfNeeded(page)
    let frame = await frameFor(page, '.device-entry-page[data-domain="alert"]')
    await frame.waitForFunction(() => !document.querySelector('.device-card-skeleton'), { timeout: 30000 })
    await frame.click('.device-card[data-device-id] .device-open')
    await page.waitForFunction(() => /^\/alerts\/devices\/[A-Za-z0-9_-]+\/workflow$/.test(location.pathname), { timeout: 30000 })
    frame = await frameFor(page, '.wf-immersive .wf-canvas')
    await frame.waitForFunction(() => document.querySelectorAll('.wf-node[data-node-id]').length >= 3, { timeout: 30000 })

    const businessNodeHandle = await frame.evaluateHandle(() => [...document.querySelectorAll('.wf-node[data-node-id]')]
      .find(node => (node.textContent || '').includes('预警')) || null)
    const businessNode = businessNodeHandle.asElement()
    assert(businessNode, 'alert business node is present')
    await businessNode.click()
    await frame.waitForSelector('.wf-properties', { visible: true, timeout: 30000 })
    assert(await frame.$('.wf-panel-tabs button.active'), 'selected business node opens the settings panel')
    const lastRun = await buttonByText(frame, '.wf-panel-tabs button', '上次运行')
    await lastRun.click()
    await frame.waitForSelector('.wf-last-run', { visible: true })
    const settings = await buttonByText(frame, '.wf-panel-tabs button', '设置')
    await settings.click()
    assert(await frame.$('.wf-panel-content'), 'settings tab restores the node form')

    await frame.click('.wf-panel-run')
    await frame.waitForSelector('.wf-debug-panel[aria-label="调试和预览"]', { visible: true, timeout: 30000 })
    assert.equal(await frame.$('.el-dialog[aria-label*="运行工作流"]'), null, 'Dify preview does not open a centered run dialog')
    await page.screenshot({ path: path.join(OUTPUT, '01-debug-preview-panel.png'), fullPage: true })
    await frame.click('.wf-debug-panel button[aria-label="关闭调试和预览"]')
    await frame.waitForFunction(() => !document.querySelector('.wf-debug-panel'), { timeout: 30000 })

    await frame.click('.wf-canvas-frame')
    await page.keyboard.down('Alt')
    await page.keyboard.press('r')
    await page.keyboard.up('Alt')
    await frame.waitForSelector('.wf-debug-panel[aria-label="调试和预览"]', { visible: true, timeout: 30000 })
    await frame.click('.wf-debug-panel button[aria-label="关闭调试和预览"]')
    await frame.waitForFunction(() => !document.querySelector('.wf-debug-panel'), { timeout: 30000 })

    await frame.click('.wf-toolbar-check')
    await frame.waitForSelector('.wf-validation', { visible: true })
    const checklistText = await frame.$eval('.wf-validation', node => node.textContent)
    const checklistBadge = await frame.$('.wf-toolbar-badge')
    assert(checklistBadge || checklistText.includes('所有问题均已解决'), 'checklist reports either actionable issues or the resolved state')
    await page.screenshot({ path: path.join(OUTPUT, '02-configuration-checklist.png'), fullPage: true })
    await frame.click('.wf-validation button[aria-label="关闭配置检查"]')
    await frame.waitForFunction(() => !document.querySelector('.wf-toolbar-checklist[open]'))

    const variableInspect = await frame.$('.wf-variable-inspect>summary')
    assert(variableInspect, 'variable inspection trigger remains at the bottom of the canvas')
    assert.equal(await variableInspect.evaluate(node => node.textContent.trim()), '变量检查')

    await frame.click('.wf-toolbar button[aria-label="版本与运行"]')
    await waitForDialog(frame, '版本与运行记录')
    await closeDialog(frame, '版本与运行记录')

    await frame.click('button[aria-label="手型模式"]')
    assert(await frame.$('button[aria-label="手型模式"].active'), 'hand mode has an active state')
    await frame.click('button[aria-label="指针模式"]')
    assert(await frame.$('button[aria-label="指针模式"].active'), 'pointer mode has an active state')

    assert(await frame.$('.wf-minimap'), 'minimap starts visible like Dify')
    await frame.click('button[aria-label="显示或隐藏小地图"]')
    await frame.waitForFunction(() => !document.querySelector('.wf-minimap'))
    await frame.click('button[aria-label="显示或隐藏小地图"]')
    await frame.waitForSelector('.wf-minimap', { visible: true })

    const baselineNodeCount = await frame.$$eval('.wf-node[data-node-id]', nodes => nodes.length)
    const startNode = await frame.$('.wf-node[data-node-id]')
    assert(startNode, 'start node is present')
    const startId = await startNode.evaluate(element => element.dataset.nodeId)
    const initialVersion = await version(frame)
    await startNode.focus()
    await page.keyboard.press('ArrowDown')
    let savedVersion = await waitForAutoSave(frame, initialVersion)
    await frame.click('.wf-canvas-history button[aria-label="撤销"]')
    savedVersion = await waitForAutoSave(frame, savedVersion)

    const closePanel = await frame.$('.wf-properties-close')
    if (closePanel) await closePanel.click()
    await frame.click(`[data-node-id="${startId}"] .wf-node-handle-source.wf-node-handle-top`)
    await frame.waitForSelector('.wf-palette-dialog', { visible: true })
    const template = await buttonByText(frame, '.wf-palette-group>button', '模板渲染')
    await template.click()
    await frame.waitForFunction(count => document.querySelectorAll('.wf-node[data-node-id]').length === count + 1, { timeout: 10000 }, baselineNodeCount)
    savedVersion = await waitForAutoSave(frame, savedVersion)
    const addedPanelClose = await frame.$('.wf-properties-close')
    if (addedPanelClose) await addedPanelClose.click()
    await frame.click('.wf-canvas-history button[aria-label="撤销"]')
    await frame.waitForFunction(count => document.querySelectorAll('.wf-node[data-node-id]').length === count, { timeout: 10000 }, baselineNodeCount)
    await waitForAutoSave(frame, savedVersion)

    await frame.click('button[aria-label="快捷键帮助"]')
    await frame.waitForSelector('.wf-shortcuts', { visible: true })
    await page.screenshot({ path: path.join(OUTPUT, '03-dify-interactions-restored.png'), fullPage: true })
    await frame.click('.wf-shortcuts button[aria-label="关闭快捷键"]')

    assert.deepEqual(failures, [], 'no page errors or server failures occurred')
    console.log(JSON.stringify({ ok: true, screenshot: OUTPUT, autosaveVersion: await version(frame), restoredNodeCount: baselineNodeCount }))
  } finally {
    await browser.close()
  }
}

main().catch(error => {
  console.error(error.stack || error)
  process.exitCode = 1
})
