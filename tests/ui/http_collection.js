/*
 * 中文说明（HTTP 数据采集端到端验收）：
 * 验证行为：用真实的本地 HTTP 夹具作为被采集接口，通过普通数据源表单完成采集并保存报表绑定。
 * 运行方式：需隔离服务（TEST5_E2E_BASE_URL，默认 8011）+ 本机 Edge；夹具服务只提供采集目标。
 * 边界：会创建数据源与采集记录（隔离环境）；断言覆盖保存后的绑定关系。
 */
/* A real local HTTP fixture, normal source forms and saved report binding. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const puppeteer = require('puppeteer-core');
const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011';
const RUN = Date.now().toString(36);
const OUTPUT = path.resolve('.implementation/ui-results/http-collection-' + RUN);
const checks = [], failures = [], violations = [], calls = [];
let browser, page, frame, fixture;
let createdSourceId, createdReportId;
function pass(label) { checks.push(label); console.log('PASS ' + label); }
async function button(label, selector = 'button') {
  await frame.waitForFunction((label, selector) => [...document.querySelectorAll(selector)].some(el => el.getClientRects().length && el.textContent.trim() === label && !el.disabled), {}, label, selector);
  for (const element of await frame.$$(selector)) {
    if (await element.evaluate((el, label) => el.getClientRects().length && el.textContent.trim() === label, label)) {
      await element.scrollIntoView();
      await frame.waitForFunction(el => { const rect = el.getBoundingClientRect(); return !el.disabled && el.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2)); }, {}, element);
      await element.click(); return;
    }
  }
  throw new Error('Visible control not found: ' + label);
}
async function field(label, value, root = '.http-form') {
  const handle = await frame.evaluateHandle((label, root) => {
    const element = [...document.querySelectorAll(root + ' label')].find(item => item.firstChild?.textContent?.trim() === label);
    return element?.querySelector('input,textarea,select');
  }, label, root);
  const element = handle.asElement(); assert(element, 'Field not found: ' + label);
  if (await element.evaluate(el => el.tagName === 'SELECT')) { await element.select(value); return; }
  await element.click({ clickCount: 3 });
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  await element.type(value); await element.press('Tab');
}
async function check(label, root = '.http-form') {
  const handle = await frame.evaluateHandle((label, root) => {
    const element = [...document.querySelectorAll(root + ' label')].find(item => item.textContent.trim().includes(label));
    return element?.querySelector('input[type=checkbox],input[type=radio]');
  }, label, root);
  const element = handle.asElement(); assert(element, 'Checkbox not found: ' + label); await element.click();
}
async function visit(route, selector) {
  assert.equal((await page.goto(BASE + route, { waitUntil: 'networkidle0' })).status(), 200);
  frame = await (await page.waitForSelector('#business-module-frame')).contentFrame();
  await frame.waitForSelector(selector, { visible: true, timeout: 25000 });
}
async function api(url, method = 'GET', body) {
  return page.evaluate(async (url, method, body) => {
    const response = await fetch(url, { method, headers: { 'Content-Type': 'application/json',
      Authorization: 'Bearer ' + localStorage.getItem('agent_token'), 'X-Workspace-Id': localStorage.getItem('agent_workspace_id') },
      body: body === undefined ? undefined : JSON.stringify(body) });
    return { status: response.status, body: await response.json() };
  }, url, method, body);
}
async function audit(label) {
  await frame.waitForFunction(() => !document.querySelector('[class*="enter-active"],[class*="leave-active"]') && document.getAnimations().every(animation => animation.playState !== 'running' || animation.effect?.getComputedTiming().iterations === Infinity));
  await frame.addScriptTag({ path: require.resolve('axe-core/axe.min.js') });
  const found = await frame.evaluate(async () => (await axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] } })).violations.map(item => ({ id: item.id, impact: item.impact, nodes: item.nodes.map(node => ({ target: node.target, summary: node.failureSummary })) })));
  violations.push(...found.map(item => ({ label, ...item })));
}
async function closeDialog(selector) { await page.keyboard.press('Escape'); await frame.waitForSelector(selector, { hidden: true }); }

(async () => {
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, 'isolated');
  assert(process.env.TEST5_E2E_PASSWORD);
  assert(['127.0.0.1', 'localhost'].includes(new URL(BASE).hostname));
  assert.equal((await (await fetch(BASE + '/api/health')).json()).app, 'test');
  fs.mkdirSync(OUTPUT, { recursive: true });
  fixture = http.createServer(async (request, response) => {
    let text = ''; for await (const chunk of request) text += chunk;
    const body = JSON.parse(text || 'null'); calls.push({ method: request.method, body });
    if (body?.fail) { response.writeHead(503, { 'Content-Type': 'application/json' }); response.end('{"message":"fixture unavailable"}'); return; }
    const data = JSON.stringify({ data: { items: [{ device: body?.device || '压缩机', temperature: '82.50', enabled: false }] } });
    response.writeHead(200, { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) }); response.end(data);
  });
  await new Promise(resolve => fixture.listen(0, '127.0.0.1', resolve));
  const upstream = 'http://127.0.0.1:' + fixture.address().port + '/metrics';
  browser = await puppeteer.launch({ executablePath: process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  page = await browser.newPage(); await page.setViewport({ width: 1440, height: 1000 });
  page.on('pageerror', error => failures.push(error.message));
  page.on('response', response => { if (response.status() >= 500 && !response.url().endsWith('/collect')) failures.push(response.status() + ' ' + response.url()); });
  await page.goto(BASE, { waitUntil: 'networkidle0' });
  await page.type('#login-username', 'workspace-admin'); await page.type('#login-password', process.env.TEST5_E2E_PASSWORD);
  await page.click('#login-btn'); await page.waitForSelector('.overview-intro');
  assert.equal(await page.$('#composer-form'), null);
  await page.screenshot({ path: path.join(OUTPUT, 'enterprise-home.png') });
  pass('Home remains the Qknow-style enterprise overview, not Test5 chat');
  await visit('/datasources?type=http', '.http-manager');
  assert.equal(await page.$eval('.enterprise-area-link.active', el => el.dataset.businessArea), 'data');
  await button('新建 HTTP 数据源'); await frame.waitForSelector('.http-form', { visible: true });
  await field('数据源名称', '设备 HTTP 浏览器验收 ' + RUN);
  await field('接口地址', upstream);
  await field('请求方式', 'POST');
  await check('批量请求体');
  await field('批量请求体 · JSON 数组', JSON.stringify([{ device: '压缩机' }, { device: '空调' }]));
  await field('记录 JSONPath', '$.data.items[*]');
  await check('允许访问此本机'); await check('确认仅查询数据');
  await check('选择输出字段');
  await button('用响应样本识别字段、预览提取', 'summary');
  await field('JSON 响应样本', JSON.stringify({ data: { items: [{ device: '样本设备', temperature: '1.25', enabled: false }] } }));
  await button('从样本识别字段'); await frame.waitForSelector('.http-candidates label');
  assert.equal(calls.length, 0, 'Offline preview must never execute the device API');
  await check('device', '.http-candidates'); await check('temperature', '.http-candidates');
  await field('数据类型', 'decimal', '.http-field-row:nth-child(2)');
  await button('预览所选字段');
  await frame.waitForFunction(() => document.querySelectorAll('.http-preview-section .http-table th').length === 2);
  await audit('HTTP create form, selection and preview');
  await page.screenshot({ path: path.join(OUTPUT, 'http-source-form.png'), fullPage: true });
  const creation = page.waitForResponse(response => response.url().endsWith('/api/v1/collection/sources') && response.request().method() === 'POST');
  await button('创建数据源'); const createdSource = await creation; assert.equal(createdSource.status(), 201);
  createdSourceId = (await createdSource.json()).data.id;
  await frame.waitForSelector('.http-form', { hidden: true });
  const source = (await api('/api/v1/collection/sources?limit=500')).body.data.find(item => item.name === '设备 HTTP 浏览器验收 ' + RUN);
  assert(source && source.fields.length === 2 && source.bodies.length === 2 && source.allowAutomatic);
  assert.equal(calls.length, 0);
  pass('Normal form saved batch bodies, internal-target approval and selected JSONPath fields without sending requests');
  await field('本页筛选', source.name, '.http-toolbar');
  await button('采集'); await button('执行已保存配置');
  await frame.waitForSelector('.http-result-summary.success', { visible: true });
  assert.equal(calls.length, 2);
  const resultText = await frame.$eval('.http-result', el => el.textContent);
  assert(resultText.includes('压缩机') && resultText.includes('空调') && resultText.includes('82.50'));
  assert(!resultText.includes('样本设备'));
  await audit('Actual batch collection results');
  await page.screenshot({ path: path.join(OUTPUT, 'http-actual-result.png') });
  await closeDialog('.http-result');
  pass('Confirmed collection returned both actual devices with exact decimals, not sample preview data');
  await button('历史'); await frame.waitForSelector('.el-dialog .http-table button'); await button('查看结果');
  await frame.waitForSelector('.http-result', { visible: true });
  assert.equal(calls.length, 2, 'History is persisted, not a repeat POST');
  await closeDialog('.http-result'); await page.keyboard.press('Escape');
  pass('Persisted collection history reopens without re-sending POST');
  await button('编辑'); await frame.waitForSelector('.http-form', { visible: true });
  assert.equal(await frame.$eval('.http-form input', el => el.value), source.name);
  await field('批量请求体 · JSON 数组', JSON.stringify([{ device: '压缩机' }, { fail: true }, { device: '空调' }]));
  await check('单条失败后继续');
  const saved = page.waitForResponse(response => response.url().endsWith('/api/v1/collection/sources/' + source.id) && response.request().method() === 'PUT');
  await button('保存修改'); assert.equal((await saved).status(), 200);
  await frame.waitForSelector('.http-form', { hidden: true });
  await button('采集'); await button('执行已保存配置');
  await frame.waitForSelector('.http-result-summary.partial', { visible: true });
  assert((await frame.$eval('.http-result', el => el.textContent)).includes('503'));
  await page.screenshot({ path: path.join(OUTPUT, 'http-partial-result.png') });
  await closeDialog('.http-result');
  pass('Editing preserved fields and displayed partial success plus the real 503 request status');
  const current = (await api('/api/v1/collection/sources/' + source.id)).body.data;
  const input = { name: current.name, url: current.url, method: current.method, headers: current.headers, bodies: [{ device: '压缩机' }, { device: '空调' }], recordPath: current.recordPath, fields: current.fields,
    allowPrivate: true, allowAutomatic: true, version: current.version };
  assert.equal((await api('/api/v1/collection/sources/' + source.id, 'PUT', input)).status, 200);
  await page.setViewport({ width: 390, height: 844 });
  await visit('/datasources?type=http', '.http-manager'); await button('新建 HTTP 数据源');
  await frame.waitForSelector('.http-form', { visible: true });
  for (const target of [page, frame]) assert(await target.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  assert.equal(await frame.$eval('.http-form-footer', element => getComputedStyle(element).position), 'static', 'Form actions must scroll normally and never cover configuration fields');
  await page.screenshot({ path: path.join(OUTPUT, 'http-mobile-form.png'), fullPage: true });
  await audit('Mobile source form'); await button('取消');
  pass('390px source form has stacked fields and no page-level horizontal overflow');
  await page.setViewport({ width: 1440, height: 1000 });
  const created = await api('/api/v1/reports', 'POST', { reportName: 'HTTP 组件验收 ' + RUN, templateMode: 'none', refreshSeconds: 0,
    content: { canvas: { width: 1000, height: 700, backgroundColor: '#102238' }, widgets: [
      { id: 'http-table', component: 'JScrollBoard', componentName: '设备温度', x: 80, y: 100, w: 800, h: 400, config: { dataType: 1, chartData: '[]', option: { rowNum: 8, waitTime: 100000 } } },
    ] } });
  assert.equal(created.status, 201); const report = created.body.data; createdReportId = report.id;
  await visit('/report/designer/' + report.id, '.jimu-toolbar');
  await frame.click('.screen-widget'); await frame.click('.config-tabs button[title="数据"]'); await button('API');
  await frame.waitForSelector('.http-widget-binding select option[value="' + source.id + '"]');
  await field('HTTP 数据源', source.id, '.http-widget-binding'); await button('试运行并更新组件数据');
  await frame.waitForFunction(() => document.querySelector('.http-widget-binding')?.innerText.includes('已返回 2 行'));
  const reportSaved = page.waitForResponse(response => response.url().endsWith('/api/v1/reports/' + report.id) && response.request().method() === 'PUT');
  await frame.click('button[title="保存"]'); assert.equal((await reportSaved).status(), 200);
  await visit('/report/view/' + report.id, '.viewer-tools');
  await frame.waitForSelector('.scroll-row', { visible: true });
  assert((await frame.evaluate(() => document.body.innerText)).includes('82.50'));
  await page.screenshot({ path: path.join(OUTPUT, 'http-saved-report.png') });
  pass('Designer selected an approved HTTP source; saved report loaded actual device data');
  await visit('/datasources?type=http', '.http-manager'); await audit('HTTP source list');
  await page.screenshot({ path: path.join(OUTPUT, 'http-source-list.png') });
  assert.equal(failures.length, 0, JSON.stringify(failures));
  assert.equal(violations.length, 0, JSON.stringify(violations));
  console.log('OUTPUT ' + OUTPUT);
})().catch(async error => {
  failures.push(error.stack || String(error)); console.error(error);
  if (page) await page.screenshot({ path: path.join(OUTPUT, 'failure.png'), fullPage: true }).catch(() => {});
  process.exitCode = 1;
}).finally(async () => {
  if (createdReportId) {
    const removed = await api('/api/v1/reports/' + createdReportId, 'DELETE').catch(() => null);
    if (removed?.status !== 200) failures.push('Could not soft-remove isolated report ' + createdReportId);
  }
  if (createdSourceId) {
    const source = await api('/api/v1/collection/sources/' + createdSourceId).catch(() => null);
    const removed = source?.body.data && await api('/api/v1/collection/sources/' + createdSourceId + '?version=' + source.body.data.version, 'DELETE').catch(() => null);
    if (removed?.status !== 200) failures.push('Could not soft-remove isolated source ' + createdSourceId);
  }
  if (failures.length) process.exitCode = 1;
  fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({ checks, failures, violations, requests: calls.length }, null, 2));
  if (browser) await browser.close();
  if (fixture) { fixture.closeAllConnections(); await new Promise(resolve => fixture.close(resolve)); }
});
