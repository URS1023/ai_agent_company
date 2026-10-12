/*
 * 中文说明（查询体验回归：真实 SQL/参数 + 确定性的延迟响应）：
 * 验证行为：查询交互在慢响应下的可见状态与最终结果；SQL 与参数是真实的，
 *   延迟由桩服务控制而不是真实模型。
 * 运行方式：默认连 http://127.0.0.1:8035 的隔离桩服务（可用 TEST5_E2E_BASE_URL 覆盖），需本机 Edge；
 *   结果写入 .implementation/ui-results/query-usability-<run>/。
 * 边界：api() 直接 fetch 桩服务以便预置数据；headers 由前置登录步骤填充。
 */
/* Query UX regression: real isolated SQL/parameters plus deterministic delayed-response coverage. */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const puppeteer = require('puppeteer-core')
const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8035'
const RUN = Date.now().toString(36)
const OUTPUT = path.resolve('.implementation/ui-results/query-usability-' + RUN)
let browser, page, frame, headers, stage = 'isolated API setup'
const sources = [], templates = [], checks = [], errors = []
const pass = label => { checks.push(label); console.log('PASS ' + label) }
async function api(url, method = 'GET', body) {
  const response = await fetch(BASE + url, { method, headers: { ...headers, ...(body instanceof FormData ? {} : { 'Content-Type': 'application/json' }) }, body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body) })
  return { status: response.status, body: await response.json() }
}
async function clickText(selector, text) {
  for (const element of await frame.$$(selector)) {
    if (await element.evaluate((el, value) => el.getClientRects().length && el.textContent.trim() === value, text)) { await element.click(); return }
  }
  throw new Error('Visible control missing: ' + text)
}
async function fill(selector, value) {
  await frame.$eval(selector, (el, value) => { el.value = value; el.dispatchEvent(new Event('input', { bubbles: true })) }, value)
}
async function choose(selector, text) {
  await frame.click(selector)
  await frame.waitForFunction(text => [...document.querySelectorAll('.el-select-dropdown__item')].some(el => el.getClientRects().length && el.textContent.includes(text)), {}, text)
  await frame.evaluate(text => [...document.querySelectorAll('.el-select-dropdown__item')].find(el => el.getClientRects().length && el.textContent.includes(text)).click(), text)
}
async function visit(route, selector) {
  await page.goto(BASE + route, { waitUntil: 'networkidle0' })
  await page.waitForSelector('#business-module-frame')
  frame = await (await page.$('#business-module-frame')).contentFrame()
  await frame.waitForSelector(selector, { visible: true })
}
async function tab(label, selector) {
  await clickText('.module-tabs a', label)
  await frame.waitForSelector(selector, { visible: true })
}
async function execute() {
  const response = page.waitForResponse(r => r.url().endsWith('/api/v1/analytics/query') && r.request().method() === 'POST')
  await clickText('button', '执行查询')
  const result = await response
  assert.equal(result.status(), 200, await result.text())
  await frame.waitForSelector('.query-result-panel')
  return (await result.json()).data
}
;(async () => {
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, 'isolated')
  assert(process.env.TEST5_E2E_PASSWORD)
  assert(['127.0.0.1', 'localhost'].includes(new URL(BASE).hostname))
  assert.equal((await (await fetch(BASE + '/api/health')).json()).app, 'test')
  fs.mkdirSync(OUTPUT, { recursive: true })
  const login = await api('/api/auth/login', 'POST', { username: 'workspace-admin', password: process.env.TEST5_E2E_PASSWORD })
  assert.equal(login.status, 200)
  headers = { Authorization: 'Bearer ' + login.body.token, 'X-Workspace-Id': login.body.workspaces[0].id }
  for (const suffix of ['A', 'B']) {
    const form = new FormData()
    form.append('name', '查询体验 ' + suffix + ' ' + RUN)
    form.append('table_name', suffix === 'A' ? 'query_sales' : 'act_evt_log')
    form.append('file', new Blob(['department,amount\n设备,100\n财务,200\n设备,300\n']), 'query.csv')
    const response = await api('/api/v1/analytics/datasources/import', 'POST', form)
    assert.equal(response.status, 201, JSON.stringify(response.body))
    sources.push(response.body.data)
  }
  const template = await api('/api/v1/analytics/query-templates', 'POST', { name: '参数复用 ' + RUN, category: '验收', mode: 'sql', content: 'SELECT * FROM query_sales WHERE department = :department', parameters: { department: '设备' } })
  assert.equal(template.status, 201)
  templates.push(template.body.data)
  stage = 'browser launch'; console.log('STAGE ' + stage)
  browser = await puppeteer.launch({ executablePath: process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true, timeout: 60000, args: ['--disable-gpu'] })
  page = await browser.newPage()
  page.on('pageerror', error => errors.push(error.message))
  await page.setViewport({ width: 1440, height: 960 })
  stage = 'initial page'; console.log('STAGE ' + stage)
  await page.goto(BASE)
  await page.evaluate((token, workspace) => { localStorage.setItem('agent_token', token); localStorage.setItem('agent_workspace_id', workspace) }, login.body.token, login.body.workspaces[0].id)
  stage = 'intelligent query template'; console.log('STAGE ' + stage)
  await visit('/analytics?datasourceId=' + sources[0].id, '.intelligent-query')
  await frame.waitForSelector('.quick-queries button')
  await clickText('button', '查询模板')
  await frame.waitForSelector('.template-item')
  await frame.evaluate(name => [...document.querySelectorAll('.template-item')].find(el => el.textContent.includes(name)).querySelector('button').click(), template.body.data.name)
  await frame.waitForFunction(() => document.querySelector('textarea[aria-label="SQL 命名参数 JSON"]')?.value.includes('设备'))
  await frame.waitForSelector('.el-dialog', { hidden: true })
  assert.equal((await execute()).totalCount, 2)
  await frame.waitForSelector('.query-history button')
  await frame.click('.query-history summary')
  await fill('textarea[aria-label="SQL 命名参数 JSON"]', '{}')
  await frame.click('.query-history button')
  assert((await frame.$eval('textarea[aria-label="SQL 命名参数 JSON"]', el => el.value)).includes('设备'))
  pass('智能问数使用 SQL 模板和最近查询均保留参数并返回真实两行结果')
  await tab('SQL 查询', '.sql-workspace')
  await frame.waitForFunction(() => document.querySelector('.metadata-panel .el-select')?.textContent.includes('查询体验 A'))
  await choose('.editor-toolbar .el-select', template.body.data.name)
  await frame.waitForFunction(() => document.querySelector('textarea[aria-label="SQL 语句"]')?.value.includes(':department'))
  const keyboardResponse = page.waitForResponse(r => r.url().endsWith('/api/v1/analytics/query') && r.request().method() === 'POST')
  await frame.click('textarea[aria-label="SQL 语句"]')
  await page.keyboard.down('Control'); await page.keyboard.press('Enter'); await page.keyboard.up('Control')
  assert.equal((await keyboardResponse).status(), 200)
  await frame.waitForSelector('.query-result-panel')
  pass('SQL 页记住跨标签数据源，支持参数模板与 Ctrl+Enter 执行')
  await tab('查询历史', 'section[aria-label="查询历史"]')
  assert.equal(await frame.$('.sql-workspace'), null)
  await frame.waitForSelector('.el-table__row')
  await frame.evaluate(() => [...document.querySelectorAll('.el-table__row')][0].querySelectorAll('button')[1].click())
  await frame.waitForSelector('.sql-workspace')
  await frame.waitForFunction(() => document.querySelector('textarea[aria-label="SQL 命名参数 JSON"]')?.value.includes('设备'))
  assert.equal((await execute()).totalCount, 2)
  assert(page.url().includes('historyId='))
  pass('历史页内容独立，复用通过历史标识恢复 SQL 与参数并可再次执行')
  await fill('textarea[aria-label="SQL 命名参数 JSON"]', '[]')
  await clickText('button', '执行查询')
  await frame.waitForFunction(() => document.querySelector('.el-alert')?.textContent.includes('JSON 对象'))
  pass('非法参数在执行前给出可操作提示')
  await fill('textarea[aria-label="SQL 命名参数 JSON"]', '{}')
  await fill('textarea[aria-label="SQL 语句"]', 'SELECT * FROM query_sales')
  await page.setRequestInterception(true)
  const delayedTemplate = async request => {
    if (request.url().endsWith('/query-templates/' + template.body.data.id + '/use')) {
      await new Promise(resolve => setTimeout(resolve, 900))
      try { await request.respond({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, data: template.body.data }) }) } catch { /* Route can be canceled. */ }
    } else { try { await request.continue() } catch { /* Already handled. */ } }
  }
  page.on('request', delayedTemplate)
  await choose('.editor-toolbar .el-select', template.body.data.name)
  await fill('textarea[aria-label="SQL 语句"]', 'SELECT 123 AS manual_edit')
  await new Promise(resolve => setTimeout(resolve, 1100))
  assert.equal(await frame.$eval('textarea[aria-label="SQL 语句"]', el => el.value), 'SELECT 123 AS manual_edit')
  page.off('request', delayedTemplate)
  await page.setRequestInterception(false)
  pass('迟到的查询模板不会覆盖用户已开始编辑的 SQL')
  await fill('textarea[aria-label="SQL 语句"]', 'SELECT * FROM query_sales')
  let delayed = false
  await page.setRequestInterception(true)
  const intercept = async request => {
    if (!delayed && request.url().endsWith('/api/v1/analytics/query') && request.method() === 'POST') {
      delayed = true
      await new Promise(resolve => setTimeout(resolve, 1300))
      try { await request.respond({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, data: { queryId: 'stale-query', datasourceId: sources[0].id, generatedSql: 'SELECT 1', totalCount: 777, columns: [], data: [], insights: [], statistics: {}, analysis: '', enhancedAnalysis: {} } }) }) } catch { /* Source switching can cancel the browser request. */ }
    } else { try { await request.continue() } catch { /* Already canceled. */ } }
  }
  page.on('request', intercept)
  await clickText('button', '执行查询')
  await frame.waitForSelector('.sql-progress')
  await choose('#sql-source', sources[1].name)
  await frame.waitForFunction(() => document.querySelector('.metadata-panel')?.textContent.includes('act_evt_log'))
  await new Promise(resolve => setTimeout(resolve, 1500))
  assert.equal(await frame.$('.query-result-panel'), null)
  assert.equal(await frame.$('.sql-progress'), null)
  page.off('request', intercept)
  await page.setRequestInterception(false)
  pass('执行中切换数据源会取消等待，旧响应不会覆盖新数据源页面')
  await tab('智能问数', '.intelligent-query')
  await frame.waitForFunction(() => document.querySelector('.quick-queries')?.textContent.includes('act_evt_log'))
  await frame.waitForFunction(() => ![...document.querySelectorAll('.el-message')].some(el => el.getClientRects().length))
  await page.screenshot({ path: path.join(OUTPUT, 'query-desktop.png') })
  await page.setViewport({ width: 390, height: 844 })
  await frame.waitForFunction(() => document.documentElement.clientWidth < 600)
  assert(await frame.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 2))
  await page.screenshot({ path: path.join(OUTPUT, 'query-mobile.png'), fullPage: true })
  await frame.$eval('.query-actions', element => element.scrollIntoView({ block: 'center' }))
  await page.screenshot({ path: path.join(OUTPUT, 'query-mobile-actions.png') })
  pass('390px 手机上查询操作换行，无页面横向溢出')
  assert.deepEqual(errors, [])
})().catch(async error => { console.error('FAILED STAGE ' + stage, error); process.exitCode = 1; if (page) { await page.screenshot({ path: path.join(OUTPUT, 'failure.png') }).catch(() => {}); console.error(await frame?.evaluate(() => document.body.innerText).catch(() => 'frame unavailable')) } }).finally(async () => {
  for (const template of templates) await api('/api/v1/analytics/query-templates/' + template.id, 'DELETE').catch(() => {})
  for (const source of sources) await api('/api/v1/analytics/datasources/' + source.id, 'DELETE').catch(() => {})
  fs.mkdirSync(OUTPUT, { recursive: true })
  fs.writeFileSync(path.join(OUTPUT, 'results.json'), JSON.stringify({ checks, errors, failed: !!process.exitCode }, null, 2))
  if (browser) await browser.close()
  console.log('OUTPUT ' + OUTPUT)
})
