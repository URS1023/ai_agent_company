/*
 * 中文说明（知识库上传重试的人工操作验收）：
 * 验证行为：用原生指针/键盘与文件上传控件完成知识库上传，并验证失败后重试的交互。
 * 约束：只做 DOM 检查与被动的响应计数，不注入事件、不调用应用 API。
 * 运行方式：需隔离服务与本机浏览器，产生知识库文档（隔离环境）。
 *
 * Native Chrome pointer/keyboard/file upload. DOM inspection and passive response counts only.
 */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const puppeteer = require('puppeteer-core')
const RUN = Date.now().toString(36)
const OUTPUT = path.resolve('.implementation/ui-results/human-knowledge-upload-' + RUN)
const steps = [], responses = [], errors = []
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
const PASSWORD = process.env.TEST5_E2E_PASSWORD
if (!PASSWORD)
  throw new Error('TEST5_E2E_PASSWORD is required')
let browser, page, stage = 'launch'
async function click(selector, text = null) {
  stage = 'click ' + (text || selector)
  const until = Date.now() + 30000
  while (Date.now() < until) {
    for (const el of await page.$$(selector)) {
      if (!await el.evaluate((node, value) => node.getClientRects().length > 0 && !node.disabled && (value === null || node.textContent.trim() === value), text)) continue
      await el.hover()
      if (!await el.evaluate(node => { const r = node.getBoundingClientRect(); const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2); return !!hit && (hit === node || node.contains(hit)) })) continue
      if (await el.evaluate(node => node.matches('a[href]'))) await Promise.all([page.waitForNavigation({ waitUntil: 'networkidle0', timeout: 30000 }), el.click()])
      else await el.click()
      return
    }
    await sleep(100)
  }
  throw new Error('No visible native click target: ' + (text || selector))
}
async function input(selector, text) {
  const handle = await page.waitForSelector(selector, { visible: true })
  await handle.click(); await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control')
  await page.keyboard.press('Backspace'); await handle.type(text, { delay: 10 }); await page.keyboard.press('Tab')
}
async function record(label, detail) {
  await sleep(250)
  const screenshot = String(steps.length + 1).padStart(2, '0') + '.png'
  await page.screenshot({ path: path.join(OUTPUT, screenshot), fullPage: true })
  steps.push({ label, detail, screenshot, url: page.url() }); console.log('PASS ' + label)
}
async function errorAfterSubmit() {
  await click('#platform-dialog button[type=submit]')
  await page.waitForFunction(() => { const e = document.querySelector('#platform-dialog .form-error'); return e && !e.hidden && !document.querySelector('#platform-dialog [type=submit]').disabled }, { timeout: 90000 })
  return page.$$eval('#platform-dialog .form-error, #platform-dialog [data-upload-state=failed]', els => els.map(el => el.textContent).join('\n'))
}
;(async () => {
  fs.mkdirSync(OUTPUT, { recursive: true })
  const invalidPdf = path.join(OUTPUT, 'invalid-document-' + RUN + '.pdf')
  fs.writeFileSync(invalidPdf, 'This is ordinary plain text, deliberately not a PDF document.\n', 'utf8')
  browser = await puppeteer.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true, args: ['--disable-gpu'], timeout: 60000 })
  page = await browser.newPage()
  await page.setViewport({ width: 390, height: 844, isMobile: false })
  page.on('pageerror', error => errors.push(error.message))
  page.on('response', response => {
    const request = response.request(), pathname = new URL(response.url()).pathname
    if (request.method() === 'POST' && ['/api/files', '/api/knowledge/documents'].includes(pathname)) responses.push({ path: pathname, status: response.status() })
  })
  await page.goto(process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8037', { waitUntil: 'networkidle0' })
  await input('#login-username', 'workspace-admin')
  await input('#login-password', PASSWORD)
  await click('#login-btn')
  await page.waitForSelector('.overview-intro,.enterprise-area-grid', { visible: true })
  await click('a[href="/knowledge"]')
  await click('a', '泵站运维手册 · 真人验收')
  await click('[data-add-document]')
  await page.waitForSelector('#platform-dialog[open]', { visible: true })
  await click('input[name=document_source][value=upload]')
  const emptyError = await errorAfterSubmit()
  const fields = await page.$$eval('#platform-dialog .field-error', els => els.filter(e => e.getClientRects().length > 0).map(e => e.textContent))
  assert.equal(fields.length, 1)
  assert(emptyError.includes('1 处'), emptyError)
  assert.equal(responses.length, 0)
  await record('390px上传空选只报告1处填写问题且没有提交请求', { emptyError, fields })
  await (await page.$('#platform-dialog input[type=file]')).uploadFile(invalidPdf)
  let failure = await errorAfterSubmit()
  let format = 'invalid PDF'
  if (!failure.includes('文件已保存在文件库')) {
    await record('无效PDF在上传阶段被拒绝，保留具体反馈', failure)
    const emptyTxt = path.join(OUTPUT, 'empty-document-' + RUN + '.txt')
    fs.writeFileSync(emptyTxt, '', 'utf8')
    await (await page.$('#platform-dialog input[type=file]')).uploadFile(emptyTxt)
    failure = await errorAfterSubmit()
    format = 'empty TXT'
  }
  const beforeRetry = responses.filter(r => r.path === '/api/files').length
  await record('真实无效文件失败反馈', { format, failure, responses: [...responses] })
  assert(failure.includes('文件已保存在文件库'), failure)
  if (format === 'invalid PDF') {
    assert(failure.includes('扩展名与实际格式一致'), 'Invalid file must explain how to recover: ' + failure)
    assert.equal(responses.filter(r => r.path === '/api/knowledge/documents').at(-1)?.status, 422)
  }
  const retryFailure = await errorAfterSubmit()
  assert(retryFailure.includes('文件已保存在文件库'), retryFailure)
  assert.equal(responses.filter(r => r.path === '/api/files').length, beforeRetry, 'Retry must reuse the already uploaded file')
  const dimensions = await page.$eval('#platform-dialog', el => { const r = el.getBoundingClientRect(); return { x: r.x, right: r.right, width: r.width, clientWidth: el.clientWidth, scrollWidth: el.scrollWidth, viewport: innerWidth } })
  assert(dimensions.x >= 0 && dimensions.right <= dimensions.viewport + 1 && dimensions.scrollWidth <= dimensions.clientWidth + 1, JSON.stringify(dimensions))
  await record('解析失败重试没有重复上传且390px弹窗没有横向溢出', { retryFailure, uploads: beforeRetry, responses: [...responses], dimensions })
  await click('#platform-dialog footer button', '取消')
  if (await page.$eval('#platform-dialog .dialog-discard', el => !el.hidden)) await click('#platform-dialog [data-discard]')
  await page.waitForSelector('#platform-dialog[open]', { hidden: true })
  await record('取消并放弃修改可正常返回知识库')
})().catch(async error => {
  errors.push(stage + ': ' + error.stack)
  console.error(error.stack)
  if (page) { await page.screenshot({ path: path.join(OUTPUT, 'failure.png'), fullPage: true }).catch(() => {}); fs.writeFileSync(path.join(OUTPUT, 'failure-dom.txt'), await page.evaluate(() => document.body.innerText).catch(() => ''), 'utf8') }
  process.exitCode = 1
}).finally(async () => {
  fs.writeFileSync(path.join(OUTPUT, 'results.json'), JSON.stringify({ mode: 'Native real Chrome at 390x844; passive response counting only', steps, responses, errors }, null, 2), 'utf8')
  fs.writeFileSync(path.join(OUTPUT, 'report.md'), '# 知识库上传失败与重试真人浏览器复验\n\n390×844，isMobile=false；原生点击、键盘、文件上传，DOM只读，响应仅监听计数。\n\n' + steps.map(s => '## ' + s.label + '\n\n' + JSON.stringify(s.detail || '', null, 2) + '\n\n![' + s.label + '](' + s.screenshot + ')').join('\n\n') + '\n\n错误：' + JSON.stringify(errors), 'utf8')
  await browser?.close()
  console.log(OUTPUT)
})
