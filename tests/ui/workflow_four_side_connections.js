/*
 * 中文说明（四边端口连线验收）：
 * 验证行为：节点上下左右四个方向的端口都能建立连线，且方向与渲染正确。
 * 运行方式：需隔离服务与本机 Edge；全部交互经界面完成。
 * 边界：保存后的连线写入工作流图（隔离环境）。
 */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const puppeteer = require('puppeteer-core')

const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011'
const EDGE = process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const OUTPUT = path.resolve(process.env.TEST5_E2E_OUTPUT || 'ui_test_report/workflow_four_side_20260907')
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
  await page.goto(BASE + '/workflows/new', { waitUntil: 'domcontentloaded' })
  if (!await page.$('#platform-login-form')) return
  const password = process.env.TEST5_E2E_PASSWORD
  assert(password, 'TEST5_E2E_PASSWORD is required when the saved session is unavailable or expired')
  await page.type('#login-username', process.env.TEST5_E2E_USERNAME || 'workspace-admin', { delay: 55 })
  await page.type('#login-password', password, { delay: 55 })
  await pause(250)
  await page.click('#login-btn')
  await page.waitForSelector('#business-module-frame', { timeout: 30000 })
}

async function moduleFrame(page) {
  if (await page.$('#analytics-app')) {
    await page.waitForSelector('.wf-canvas', { timeout: 30000 })
    return page
  }
  const element = await page.waitForSelector('#business-module-frame', { timeout: 30000 })
  const frame = await element.contentFrame()
  assert(frame, 'business module iframe is available')
  await frame.waitForSelector('.wf-canvas', { timeout: 30000 })
  return frame
}

async function buttonByText(frame, selector, text) {
  const buttons = await frame.$$(selector)
  for (const button of buttons) {
    if ((await button.evaluate(element => element.textContent.trim())).includes(text)) return button
  }
  throw new Error(`Button not found: ${text}`)
}

async function dragHandle(page, frame, sourceSelector, targetSelector) {
  const source = await frame.$(sourceSelector)
  const target = await frame.$(targetSelector)
  assert(source, `source handle exists: ${sourceSelector}`)
  assert(target, `target handle exists: ${targetSelector}`)
  const from = await source.boundingBox()
  const to = await target.boundingBox()
  assert(from && to, 'connection handles are visible in the browser viewport')
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2)
  await page.mouse.down()
  await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, { steps: 18 })
  await pause(220)
  await page.mouse.up()
  await frame.waitForFunction(
    (sourceHandle, targetHandle) => document.querySelector(sourceHandle)?.classList.contains('connected') && document.querySelector(targetHandle)?.classList.contains('connected'),
    { timeout: 10000 },
    sourceSelector,
    targetSelector,
  )
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
  let cleanup = null
  try {
    await seedSavedSession(page)
    await loginIfNeeded(page)
    const frame = await moduleFrame(page)
    await frame.waitForFunction(() => document.querySelectorAll('.wf-node[data-node-id]').length === 2, { timeout: 30000 })

    await frame.click('.wf-canvas-add .wf-primary')
    await frame.waitForSelector('.wf-palette-dialog', { visible: true })
    const templateButton = await buttonByText(frame, '.wf-palette-group>button', '模板渲染')
    await templateButton.click()
    await frame.waitForFunction(() => document.querySelectorAll('.wf-node[data-node-id]').length === 3, { timeout: 30000 })

    const ids = await frame.$$eval('.wf-node[data-node-id]', nodes => Object.fromEntries(nodes.map(node => [node.querySelector('.wf-node-heading span')?.textContent.trim(), node.dataset.nodeId])))
    assert(ids['开始'] && ids['结束'] && ids['模板渲染'], 'start, template and end nodes are present')
    const close = await frame.$('.wf-properties-close')
    if (close) await close.click()
    await frame.click('button[aria-label="适应画布"]')
    await pause(500)

    await dragHandle(
      page,
      frame,
      `[data-node-id="${ids['开始']}"] .wf-node-handle-source.wf-node-handle-top`,
      `[data-node-id="${ids['模板渲染']}"] .wf-node-handle-target.wf-node-handle-bottom`,
    )
    await dragHandle(
      page,
      frame,
      `[data-node-id="${ids['模板渲染']}"] .wf-node-handle-source.wf-node-handle-left`,
      `[data-node-id="${ids['结束']}"] .wf-node-handle-target.wf-node-handle-right`,
    )

    await page.screenshot({ path: path.join(OUTPUT, '01-four-side-connections.png'), fullPage: true })
    const save = await buttonByText(frame, '.wf-toolbar button', '保存草稿')
    await save.click()
    await page.waitForFunction(() => /^\/workflows\/[A-Za-z0-9_-]+$/.test(location.pathname), { timeout: 30000 })
    const workflowId = new URL(page.url()).pathname.split('/').at(-1)
    const record = await frame.evaluate(async id => {
      const response = await fetch(`/api/v1/workflows/${encodeURIComponent(id)}`, {
        headers: {
          Authorization: `Bearer ${localStorage.getItem('agent_token')}`,
          'X-Workspace-Id': localStorage.getItem('agent_workspace_id'),
        },
      })
      if (!response.ok) throw new Error(`Workflow read failed: ${response.status}`)
      return (await response.json()).data
    }, workflowId)
    cleanup = { id: workflowId, version: record.version }
    const topToBottom = record.graph.edges.find(edge => edge.source === ids['开始'] && edge.target === ids['模板渲染'])
    const leftToRight = record.graph.edges.find(edge => edge.source === ids['模板渲染'] && edge.target === ids['结束'])
    assert.deepEqual(
      { sourceSide: topToBottom?.sourceSide, targetSide: topToBottom?.targetSide },
      { sourceSide: 'top', targetSide: 'bottom' },
      'top-to-bottom drag is persisted',
    )
    assert.deepEqual(
      { sourceSide: leftToRight?.sourceSide, targetSide: leftToRight?.targetSide },
      { sourceSide: 'left', targetSide: 'right' },
      'left-to-right drag is persisted',
    )
    assert.deepEqual(failures, [], 'no page errors or server failures occurred')
    console.log(JSON.stringify({ ok: true, screenshot: OUTPUT, edges: [topToBottom, leftToRight] }))
  } finally {
    if (cleanup) {
      try {
        const target = page.frames().find(item => item.url().includes('/analytics-app')) || page
        if (target) await target.evaluate(async item => {
          await fetch(`/api/v1/workflows/${encodeURIComponent(item.id)}?version=${item.version}`, {
            method: 'DELETE',
            headers: {
              Authorization: `Bearer ${localStorage.getItem('agent_token')}`,
              'X-Workspace-Id': localStorage.getItem('agent_workspace_id'),
            },
          })
        }, cleanup)
      } catch { /* cleanup must not hide the interaction result */ }
    }
    await browser.close()
  }
}

main().catch(error => {
  console.error(error.stack || error)
  process.exitCode = 1
})
