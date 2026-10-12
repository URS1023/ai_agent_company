/*
 * 中文说明（业务回写链路验收）：
 * 验证行为：目标表单 -> 字段映射 -> HTTP 预警 -> 数据库写入 -> 回执，完整闭环在隔离环境可复现。
 * 运行方式：需隔离服务 + 本机 Edge；本地 http 桩提供被调用的外部接口。
 * 边界：会写库（仅隔离环境）；回写结果与回执必须一致。
 */
/* Isolated real Edge: target form -> field mapping -> HTTP alert -> database -> receipt. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const puppeteer = require('puppeteer-core');
const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011';
const RUN = Date.now().toString(36), OUTPUT = path.resolve('.implementation/ui-results/writeback-' + RUN);
const checks = [], failures = [], violations = [], calls = [];
let browser, page, frame, fixture, sourceId, httpId, targetId, deviceId, ruleId, workflowId;
const pass = text => { checks.push(text); console.log('PASS ' + text); };
async function api(url, method = 'GET', body) {
  return page.evaluate(async (url, method, body) => {
    const response = await fetch(url, { method, headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + localStorage.getItem('agent_token'), 'X-Workspace-Id': localStorage.getItem('agent_workspace_id') }, body: body === undefined ? undefined : JSON.stringify(body) });
    return { status: response.status, body: await response.json() };
  }, url, method, body);
}
async function button(label, selector = 'button') {
  await frame.waitForFunction((label, selector) => [...document.querySelectorAll(selector)].some(el => el.getClientRects().length && el.textContent.trim() === label && !el.disabled), {}, label, selector);
  for (const handle of await frame.$$(selector)) if (await handle.evaluate((el, text) => el.getClientRects().length && el.textContent.trim() === text, label)) {
    await handle.scrollIntoView(); await frame.waitForFunction(el => { const r = el.getBoundingClientRect(); return el.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)); }, {}, handle); await handle.click(); return;
  }
  throw new Error('Missing button ' + label);
}
async function field(label, value, root = '.workflow-ui', index = 0) {
  await frame.waitForFunction((label, root, index) => [...document.querySelectorAll(root + ' label')].filter(el => el.getClientRects().length && el.firstChild?.textContent?.trim() === label)[index]?.querySelector('input,textarea,select'), {}, label, root, index);
  const handle = (await frame.evaluateHandle((label, root, index) => [...document.querySelectorAll(root + ' label')].filter(el => el.getClientRects().length && el.firstChild?.textContent?.trim() === label)[index].querySelector('input,textarea,select'), label, root, index)).asElement();
  await handle.scrollIntoView();
  if (await handle.evaluate(el => el.tagName === 'SELECT')) { await frame.waitForFunction((el, value) => !el.disabled && [...el.options].some(option => option.value === value), {}, handle, value); return handle.select(value); }
  await handle.click({ clickCount: 3 }); await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control'); await handle.type(value); await handle.press('Tab');
}
async function checkbox(label, root) {
  const handle = (await frame.evaluateHandle((label, root) => [...document.querySelectorAll(root + ' label')].find(el => el.textContent.trim().startsWith(label))?.querySelector('input[type=checkbox]'), label, root)).asElement();
  assert(handle, label); await handle.scrollIntoView(); await handle.click();
}
async function visit(route, selector) {
  assert.equal((await page.goto(BASE + route, { waitUntil: 'networkidle0' })).status(), 200);
  frame = await (await page.waitForSelector('#business-module-frame')).contentFrame(); await frame.waitForSelector(selector, { visible: true });
}
async function audit(label) {
  await frame.waitForFunction(() => !document.querySelector('[class*="enter-active"],[class*="leave-active"]'));
  await frame.addScriptTag({ path: require.resolve('axe-core/axe.min.js') });
  const found = await frame.evaluate(async () => (await axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] } })).violations.map(item => ({ id: item.id, impact: item.impact, nodes: item.nodes.map(node => ({ target: node.target, summary: node.failureSummary })) })));
  violations.push(...found.map(item => ({ label, ...item })));
}
(async () => {
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, 'isolated'); assert(process.env.TEST5_E2E_PASSWORD);
  assert(['127.0.0.1', 'localhost'].includes(new URL(BASE).hostname)); assert.equal((await (await fetch(BASE + '/api/health')).json()).app, 'test');
  fs.mkdirSync(OUTPUT, { recursive: true });
  fixture = http.createServer((request, response) => { calls.push(request.url); response.writeHead(200, { 'Content-Type': 'application/json' }); response.end(JSON.stringify({ items: [{ device: 'COMP-' + RUN, temperature: '82.50' }] })); });
  await new Promise(resolve => fixture.listen(0, '127.0.0.1', resolve));
  browser = await puppeteer.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true }); page = await browser.newPage(); await page.setViewport({ width: 1600, height: 1100 });
  page.on('pageerror', error => failures.push(error.message)); page.on('response', response => { if (response.status() >= 500) failures.push(response.status() + ' ' + response.url()); });
  await page.goto(BASE, { waitUntil: 'networkidle0' }); await page.type('#login-username', 'workspace-admin'); await page.type('#login-password', process.env.TEST5_E2E_PASSWORD); await page.click('#login-btn'); await page.waitForSelector('.overview-intro');
  const imported = await page.evaluate(async run => { const body = new FormData(); body.append('file', new Blob(['device,temperature,report_id\nseed,0,seed\n'], { type: 'text/csv' }), 'write-fixture.csv'); body.append('name', '写入验收 ' + run); body.append('table_name', 'inspection_results'); const response = await fetch('/api/v1/analytics/datasources/import', { method: 'POST', headers: { Authorization: 'Bearer ' + localStorage.getItem('agent_token'), 'X-Workspace-Id': localStorage.getItem('agent_workspace_id') }, body }); return { status: response.status, body: await response.json() }; }, RUN);
  assert.equal(imported.status, 201, JSON.stringify(imported.body)); sourceId = imported.body.data.id;
  await visit('/datasources?type=writeback', '.write-targets'); await button('新增写入目标'); await field('目标名称', '设备报告输出 ' + RUN, '#write-target-form'); await field('目标数据源', sourceId, '#write-target-form');
  await frame.waitForFunction(() => document.querySelector('#write-target-form')?.textContent.includes('inspection_results')); await field('目标业务表', 'inspection_results', '#write-target-form');
  for (const name of ['device', 'temperature', 'report_id']) await checkbox(name, '#write-target-form'); await checkbox('追加记录', '#write-target-form');
  await audit('Explicit table and field authorization'); await page.screenshot({ path: path.join(OUTPUT, 'write-target-form.png'), fullPage: true });
  const saving = page.waitForResponse(response => response.url().endsWith('/writeback/targets') && response.request().method() === 'POST'); await button('保存写入授权'); const saved = await saving; assert.equal(saved.status(), 201, await saved.text()); const target = (await saved.json()).data; targetId = target.id; await frame.waitForSelector('#write-target-form', { hidden: true }); pass('Ordinary data-source page creates an explicit table/column/insert allowlist without writing data');
  const httpSource = await api('/api/v1/collection/sources', 'POST', { name: 'write-http-' + RUN, url: 'http://127.0.0.1:' + fixture.address().port, recordPath: '$.items', allowPrivate: true, allowAutomatic: true }); assert.equal(httpSource.status, 201); httpId = httpSource.body.data.id;
  const device = await api('/api/v1/alerts/devices', 'POST', { name: '写入测试设备', code: 'WRITE-' + RUN, datasourceId: httpId }); assert.equal(device.status, 201); deviceId = device.body.data.id;
  const rule = await api('/api/v1/alerts/rules', 'POST', { name: '写入温度策略', deviceId, recordKeyField: 'device', rules: [{ id: 'heat', name: '温度超限', message: '检查冷却系统', expression: { op: 'gte', args: [{ field: 'temperature' }, { value: '80' }] } }] }); assert.equal(rule.status, 201); ruleId = rule.body.data.id;
  const nodes = [['start', 'start', {}], ['http', 'http-request', { datasourceId: httpId }], ['alert', 'alert-evaluate', { ruleId, ruleRevision: 1 }], ['write', 'output-table', {}], ['end', 'end', {}]].map(([id, kind, config], i) => ({ id, kind, title: id, config, position: { x: 40 + i * 180, y: 60 } }));
  const edges = nodes.slice(1).map((node, i) => ({ id: nodes[i].id + '-' + node.id, source: nodes[i].id, target: node.id }));
  const flowResponse = await api('/api/v1/workflows', 'POST', { name: '写入闭环 ' + RUN, domains: ['alert', 'quality'], graph: { nodes, edges } }); assert.equal(flowResponse.status, 201); workflowId = flowResponse.body.data.id;
  await visit('/workflows/' + workflowId, '.wf-editor'); await button('适应画布'); await frame.click('.wf-node[data-node-id="write"]'); await frame.waitForSelector('.write-node-form'); await field('授权写入目标', targetId, '.write-node-form');
  await field('写入方式', 'insert', '.write-node-form'); await field('写入记录 JSONPath', '$.data', '.write-node-form');
  for (const [index, name] of ['device', 'temperature', 'report_id'].entries()) { await button('添加写入字段'); await field('目标字段', name, '.write-node-form', index); if (name === 'report_id') await field('值的来源', 'input', '.write-node-form', index); await field('取值 JSONPath', name === 'report_id' ? '$.alertReportId' : '$.' + name, '.write-node-form', index); }
  await button('保存草稿'); await frame.waitForFunction(() => document.querySelector('.wf-toolbar')?.textContent.includes('草稿 v2')); await audit('Dedicated target write node form'); await page.screenshot({ path: path.join(OUTPUT, 'write-node-mapping.png'), fullPage: true }); pass('Dedicated workflow node selects a fixed target version and maps actual row fields plus report ID');
  await button('运行'); const refused = page.waitForResponse(response => response.url().endsWith('/runs') && response.request().method() === 'POST'); await button('确认执行'); assert.equal((await refused).status(), 422); assert.equal(calls.length, 0); pass('Unchecked external-write confirmation stops the run before the first HTTP request');
  await frame.click('.wf-write-consent input'); const running = page.waitForResponse(response => response.url().endsWith('/runs') && response.request().method() === 'POST'); await button('确认执行'); const response = await running; assert.equal(response.status(), 200, await response.text()); const run = (await response.json()).data;
  await frame.waitForFunction(() => document.querySelector('.write-receipts')?.textContent.includes('已确认提交')); assert.equal(calls.length, 1);
  const reportId = run.result.outputs.end.alertReportId;
  const query = await api('/api/v1/analytics/query', 'POST', { datasourceId: sourceId, query: 'SELECT * FROM inspection_results', mode: 'sql', needAnalysis: false, needChart: false }); assert.equal(query.status, 200); assert.equal(query.body.data.data.length, 2); assert.deepEqual(query.body.data.data.find(row => row.device === 'COMP-' + RUN), { device: 'COMP-' + RUN, temperature: '82.50', report_id: reportId }); pass('Real HTTP alert writes an actual SQLite row with exact text reading and the persistent report ID');
  await button('打开预警业务报告', 'a'); await frame.waitForSelector('.alert-report-detail', { visible: true }); await frame.waitForFunction(() => document.querySelector('.alert-report-detail .write-receipts')?.textContent.includes('已确认提交')); assert.equal(calls.length, 1); await audit('Business report with durable write receipt'); await page.screenshot({ path: path.join(OUTPUT, 'alert-write-report.png'), fullPage: true }); pass('Ordinary alert report displays original findings and confirmed database-write evidence without rerunning equipment');
  await page.setViewport({ width: 390, height: 844 }); await visit('/alerts/reports?report=' + reportId, '.alert-report-detail'); await audit('390px report with write receipts'); assert(await frame.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)); await page.screenshot({ path: path.join(OUTPUT, 'write-report-mobile.png'), fullPage: true }); pass('Write evidence remains readable in the 390px ordinary report page');
  await visit('/datasources?type=writeback', '.write-targets'); await button('编辑目标'); await frame.waitForSelector('#write-target-form', { visible: true });
  await frame.waitForFunction(() => document.querySelector('#write-target-form')?.textContent.includes('report_id'));
  await audit('390px write target edit form'); assert(await frame.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await page.screenshot({ path: path.join(OUTPUT, 'write-target-mobile.png'), fullPage: true }); pass('The saved target reopens in a bounded 390px edit form with its actual field selections');
  assert.deepEqual(failures, []); assert.deepEqual(violations, []);
})().catch(cause => { failures.push(cause.stack || String(cause)); console.error(cause); process.exitCode = 1; }).finally(async () => {
  if (page) try {
    if (failures.length) await page.screenshot({ path: path.join(OUTPUT, 'failure.png'), fullPage: true });
    for (const [base, id] of [['/api/v1/workflows', workflowId], ['/api/v1/writeback/targets', targetId], ['/api/v1/alerts/rules', ruleId], ['/api/v1/alerts/devices', deviceId], ['/api/v1/collection/sources', httpId]]) if (id) { const row = await api(base + '/' + id); if (row.status === 200) assert.equal((await api(base + '/' + id + '?version=' + row.body.data.version, 'DELETE')).status, 200); }
    if (sourceId) assert.equal((await api('/api/v1/analytics/datasources/' + sourceId, 'DELETE')).status, 200);
  } catch (cause) { failures.push('Cleanup: ' + cause.message); process.exitCode = 1; }
  fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({ checks, failures, violations, actualHttpRequests: calls.length }, null, 2)); if (browser) await browser.close(); if (fixture) await new Promise(resolve => fixture.close(resolve)); console.log('Evidence ' + OUTPUT);
});
