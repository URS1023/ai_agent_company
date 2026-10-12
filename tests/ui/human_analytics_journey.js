/*
 * 中文说明（人工操作式数据分析旅程）：
 * 验证行为：完全用原生鼠标/键盘/文件选择器完成分析流程，检验普通用户真实操作路径是否可用。
 * 约束：不调用应用 API、不直接写存储、不注入 DOM 事件、不使用模拟响应——只有真人能做到的操作。
 * 运行方式：需隔离服务与本机浏览器；断言以页面可见结果为准。
 *
 * Human-operated analytics journey: native mouse/keyboard/file chooser only.
 * No application API calls, storage writes, injected DOM events or mocked responses.
 */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const puppeteer = require('puppeteer-core')
const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8037'
const PASSWORD = process.env.TEST5_E2E_PASSWORD
if (!PASSWORD)
  throw new Error('TEST5_E2E_PASSWORD is required')
const RUN = Date.now().toString(36)
const OUTPUT = path.resolve('.implementation/ui-results/human-analytics-' + RUN)
const SOURCE = '门店销售体验 ' + RUN
const TEMPLATE = '门店销售查询 ' + RUN
const steps = [], findings = [], errors = []
let browser, page, frame, stage = 'launch', number = 0
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
async function visible(target, selector, text, exact = true) {
  for (const handle of await target.$$(selector)) {
    if (await handle.evaluate((el, text, exact) => el.getClientRects().length > 0 && !el.disabled && (exact ? el.textContent.trim() === text : el.textContent.includes(text)), text, exact)) return handle
  }
  return null
}
async function click(target, selector, text, exact = true) {
  stage = 'click: ' + text
  const until = Date.now() + 30000
  let element
  while (!element && Date.now() < until) { element = await visible(target, selector, text, exact); if (!element) await sleep(80) }
  assert(element, 'Visible control unavailable: ' + text)
  await element.hover()
  while (Date.now() < until) {
    const exposed = await element.evaluate(el => {
      const rect = el.getBoundingClientRect()
      const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2)
      return !!hit && (hit === el || el.contains(hit))
    })
    if (exposed) break
    await sleep(100)
  }
  await element.click()
}
async function input(handle, text) {
  assert(handle, 'Input is visible')
  await handle.click()
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control')
  await page.keyboard.press('Backspace')
  if (text) await handle.type(text, { delay: 14 })
  await page.keyboard.press('Tab')
}
async function formInput(label, text, root = '.el-dialog') {
  for (const item of await frame.$$(root + ' .el-form-item')) {
    if (await item.evaluate((el, label) => el.getClientRects().length && el.querySelector('.el-form-item__label')?.textContent.replace('*', '').trim() === label, label)) {
      await input(await item.$('input:not([type=file]),textarea'), text); return
    }
  }
  throw new Error('Form field missing: ' + label)
}
async function getFrame(selector) {
  const element = await page.waitForSelector('#business-module-frame', { visible: true })
  frame = await element.contentFrame()
  await frame.waitForSelector(selector, { visible: true })
}
async function navigation(href, selector) {
  stage = 'navigation: ' + href
  const links = await page.$$('a[href="' + href + '"]')
  let link
  for (const item of links) { if (await item.evaluate(el => el.getClientRects().length > 0)) { link = item; break } }
  assert(link, 'Visible navigation link missing: ' + href)
  await link.click()
  await getFrame(selector)
}
async function record(label, detail = '') {
  await frame?.waitForFunction(() => ![...document.querySelectorAll('.el-message')].some(el => el.getClientRects().length), { timeout: 6000 }).catch(() => {})
  const filename = String(++number).padStart(2, '0') + '.png'
  await page.screenshot({ path: path.join(OUTPUT, filename), fullPage: true })
  steps.push({ label, detail, screenshot: filename, url: page.url() })
  console.log('PASS ' + label)
}
async function queryComplete() {
  await click(frame, 'button', '执行查询')
  await frame.waitForFunction(() => document.querySelector('.query-result-panel') || document.querySelector('.query-error,.el-alert--error'), { timeout: 90000 })
  const result = await frame.$('.query-result-panel')
  return !!result
}
async function choose(selector, label) {
  await frame.click(selector)
  await click(frame, '.el-select-dropdown__item', label, false)
}
;(async () => {
  fs.mkdirSync(path.join(OUTPUT, 'downloads'), { recursive: true })
  const csv = path.join(OUTPUT, '门店销售.csv')
  fs.writeFileSync(csv, '\uFEFFdepartment,amount,status\n一店,120,已支付\n二店,80,已支付\n一店,200,已支付\n二店,50,退款\n', 'utf8')
  browser = await puppeteer.launch({ executablePath: process.env.TEST5_EDGE_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: process.env.TEST5_HEADFUL !== '1', args: ['--disable-gpu'], timeout: 60000 })
  page = await browser.newPage()
  page.on('pageerror', error => errors.push(error.message))
  await page.setViewport({ width: 1440, height: 1000 })
  const client = await page.createCDPSession()
  await client.send('Page.setDownloadBehavior', { behavior: 'allow', downloadPath: path.join(OUTPUT, 'downloads') })
  await page.goto(BASE, { waitUntil: 'networkidle0' })
  await input(await page.waitForSelector('#login-username', { visible: true }), 'workspace-admin')
  await input(await page.$('#login-password'), PASSWORD)
  await page.click('#login-btn')
  await page.waitForSelector('.overview-intro,.enterprise-area-grid', { visible: true })
  await record('通过登录表单进入工作台')
  await navigation('/analytics', '.intelligent-query')
  await record('从可见导航进入数据分析，查看首次使用引导')
  await navigation('/datasources', '.datasource-workspace')
  await click(frame, 'button', '从 CSV / Excel 导入')
  await frame.waitForSelector('.el-dialog', { visible: true })
  await formInput('数据源名称', '取消后不应创建 ' + RUN)
  await click(frame, '.el-dialog button', '取消')
  await frame.waitForSelector('.el-dialog', { hidden: true })
  assert(!(await frame.evaluate(() => document.body.innerText)).includes('取消后不应创建 ' + RUN))
  await record('取消导入不会创建数据源')
  await click(frame, 'button', '从 CSV / Excel 导入')
  await frame.waitForSelector('.el-dialog input[type=file]', { visible: true })
  await (await frame.$('.el-dialog input[type=file]')).uploadFile(csv)
  await formInput('数据源名称', SOURCE)
  await formInput('数据表名称', 'store_sales')
  await record('通过文件控件上传真实CSV并填写用途名称', '4行销售记录，金额120、80、200、50。')
  await click(frame, '.el-dialog button', '导入')
  await frame.waitForSelector('.el-dialog', { hidden: true, timeout: 90000 })
  await frame.waitForFunction(name => [...document.querySelectorAll('.datasource-card')].some(el => el.textContent.includes(name)), { timeout: 60000 }, SOURCE)
  await record('导入成功，界面显示4行文件数据来源')
  await click(frame, 'button', '从 CSV / Excel 导入')
  await frame.waitForSelector('.el-dialog', { visible: true })
  const oldFile = await frame.$eval('.el-dialog input[type=file]', el => el.files.length)
  const oldName = await frame.$$eval('.el-dialog .el-form-item', items => items.find(el => el.querySelector('.el-form-item__label')?.textContent === '数据源名称')?.querySelector('input')?.value)
  if (oldFile || oldName === SOURCE) { findings.push({ issue: '成功导入后再次打开导入窗口仍带着上次文件与名称，容易重复导入', oldFile, oldName }); await record('发现：再次导入窗口保留已导入文件与名称') }
  else await record('再次打开导入窗口已清空文件与名称，避免误重复导入')
  await click(frame, '.el-dialog button', '取消')
  await frame.waitForSelector('.el-dialog', { hidden: true })
  const card = await visible(frame, '.datasource-card', SOURCE, false)
  await click(card, 'button', '查看结构')
  await frame.waitForSelector('.schema-search', { visible: true })
  await input(await frame.$('.schema-search input'), 'store_sales')
  await click(frame, '.el-dialog button', '查看字段')
  await record('使用结构搜索与字段查看了解数据，不依赖猜测字段名')
  await page.keyboard.press('Escape')
  await frame.waitForFunction(() => [...document.querySelectorAll('.el-dialog')].filter(el => el.getClientRects().length).length === 1)
  await sleep(400)
  await click(frame, '.el-dialog button', '查询')
  await frame.waitForSelector('.intelligent-query,.sql-workspace', { visible: true })
  await record('从数据表的查询按钮进入查询页')
  const defaultQueryWorked = await queryComplete()
  if (!defaultQueryWorked) {
    findings.push({ issue: '从已经选定的数据表点击查询仍要求调用AI模型，基础预览被模型配置阻断', displayed: await frame.evaluate(() => document.querySelector('.el-alert--error')?.textContent) })
    await record('发现：数据表预览受到模型配置阻断')
    await click(frame, '.module-tabs a', 'SQL 查询')
    await frame.waitForSelector('.sql-workspace', { visible: true })
    await click(frame, '.el-collapse-item__header', 'store_sales')
    await click(frame, 'button', '查询此表')
    assert(await queryComplete())
  }
  await frame.waitForFunction(() => document.querySelector('.query-result-panel .result-summary')?.textContent.includes('4'))
  await record('执行只读表预览，显示4行真实销售记录')
  if (await frame.$('.intelligent-query')) { await click(frame, '.module-tabs a', 'SQL 查询'); await frame.waitForSelector('.sql-workspace', { visible: true }); await click(frame, '.el-collapse-item__header', 'store_sales'); await click(frame, 'button', '查询此表') }
  const sqlInput = await frame.$('textarea[aria-label="SQL 语句"]')
  await input(sqlInput, 'SELECT department, SUM(CAST(amount AS REAL)) AS total FROM store_sales GROUP BY department ORDER BY department')
  assert(await queryComplete())
  const rows = await frame.$$eval('.query-result-panel .el-table__body tr', rows => rows.map(row => row.innerText))
  assert(rows.some(value => value.includes('一店') && value.includes('320')), JSON.stringify(rows))
  assert(rows.some(value => value.includes('二店') && value.includes('130')), JSON.stringify(rows))
  await record('按门店汇总销售额，页面结果一店320、二店130')
  await click(frame, 'button', '保存为查询模板')
  await frame.waitForSelector('.el-message-box input', { visible: true })
  await input(await frame.$('.el-message-box input'), TEMPLATE)
  await click(frame, '.el-message-box button', '保存')
  await frame.waitForSelector('.el-message-box', { hidden: true })
  await record('通过保存对话框建立可复用查询模板')
  await click(frame, '.module-tabs a', '查询历史')
  await frame.waitForSelector('section[aria-label="查询历史"] .el-table__row')
  await click(frame, '.el-table__row button', '结果')
  await frame.waitForSelector('.el-dialog .query-result-panel', { visible: true })
  await record('从查询历史打开已保存的分析结果')
  await click(frame, '.el-dialog .el-dropdown button', '导出结果')
  await click(frame, '.el-dropdown-menu__item', 'HTML 分析报告')
  const downloadDeadline = Date.now() + 30000
  let report
  while (!report && Date.now() < downloadDeadline) { report = fs.readdirSync(path.join(OUTPUT, 'downloads')).find(name => name.endsWith('.html')); if (!report) await sleep(150) }
  assert(report, 'HTML report was downloaded through the visible export menu')
  const reportText = fs.readFileSync(path.join(OUTPUT, 'downloads', report), 'utf8')
  assert(reportText.includes('320') && reportText.includes('130'))
  await record('从界面导出并核对实际HTML分析报告', '下载文件包含两家门店的320/130汇总结果。')
  await frame.click('.el-dialog__headerbtn')
  await frame.waitForSelector('.el-dialog', { hidden: true })
  await click(frame, '.el-table__row button', '复用 SQL')
  await frame.waitForSelector('.sql-workspace textarea[aria-label="SQL 语句"]', { visible: true })
  await frame.waitForFunction(() => document.querySelector('textarea[aria-label="SQL 语句"]')?.value.includes('SUM'))
  assert(await queryComplete())
  await record('通过历史复用SQL再次执行，业务结果保持一致')
  await input(await frame.$('textarea[aria-label="SQL 语句"]'), 'SELECT department FROM store_sales WHERE status = \'已支付\'')
  await click(frame, '.module-tabs a', '查询历史')
  await click(frame, '.module-tabs a', 'SQL 查询')
  await frame.waitForFunction(() => document.querySelector('.metadata-panel .el-select')?.textContent.includes('门店销售体验') && !document.querySelector('.metadata-panel .el-loading-mask'))
  const draft = await frame.$eval('textarea[aria-label="SQL 语句"]', el => el.value)
  if (!draft.includes('已支付')) { findings.push({ issue: 'SQL编辑未执行时切到历史再回来，编辑内容丢失且没有提醒', draft }); await record('发现：切换标签导致未执行SQL草稿丢失') }
  else { assert(await queryComplete()); await record('未执行SQL切到历史再返回仍保留草稿，继续执行得到已支付明细') }
  await choose('.editor-toolbar .el-select', TEMPLATE)
  await frame.waitForFunction(() => document.querySelector('textarea[aria-label="SQL 语句"]')?.value.includes('SUM'))
  assert(await queryComplete())
  await record('从模板下拉恢复门店汇总并成功执行')
  await navigation('/dashboards', '.datasource-manage')
  await click(frame, 'button', '新增报表')
  await frame.waitForSelector('.report-create-dialog', { visible: true })
  await formInput('报表名称', '取消的报表 ' + RUN, '.report-create-dialog')
  await record('检查报表创建方式与取消入口')
  await click(frame, '.report-create-dialog button', '取消')
  await frame.waitForSelector('.report-create-dialog', { hidden: true })
  assert(!(await frame.evaluate(() => document.body.innerText)).includes('取消的报表 ' + RUN))
  await record('取消新建报表不会产生空白报表')
  if (process.env.TEST5_VERIFY_FIXES === '1') assert.deepEqual(findings, [])
})().catch(async error => {
  errors.push(stage + ': ' + error.message); console.error(stage, error); process.exitCode = 1
  if (page) await record('旅程中断', error.message).catch(() => {})
}).finally(async () => {
  fs.mkdirSync(OUTPUT, { recursive: true })
  fs.writeFileSync(path.join(OUTPUT, 'results.json'), JSON.stringify({ mode: 'Real Chrome native pointer/keyboard/file upload; DOM read-only; no app API writes or state injection', source: SOURCE, template: TEMPLATE, steps, findings, errors }, null, 2))
  fs.writeFileSync(path.join(OUTPUT, 'report.md'), '# 真实浏览器数据分析旅程\n\n' + steps.map((step, index) => `${index + 1}. ${step.label}：${step.detail}\n\n![步骤截图](${step.screenshot})`).join('\n\n') + '\n\n## 发现\n\n' + findings.map(item => '- ' + item.issue).join('\n') + '\n\n## 异常\n\n' + errors.join('\n'))
  if (browser) await browser.close()
  console.log('OUTPUT ' + OUTPUT)
})
