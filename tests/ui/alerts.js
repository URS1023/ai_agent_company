/*
 * 中文说明（设备预警全流程真实浏览器验收）：
 * 验证行为：普通设备表单 -> 完整的预警向导 -> 报告 -> 问题处置；逐步断言落库结果与页面展示一致。
 * 运行方式：需隔离验收服务（TEST5_E2E_BASE_URL，默认 http://127.0.0.1:8011）与本机 Edge；
 *   结果写入 .implementation/ui-results/alerts-<run>/。会创建预警设备/流程/调度，故需可写的隔离库。
 * 边界：依赖向导真实发布流程与调度记录，禁止对生产环境运行；失败与违规分别记录便于定位。
 */
/* Real Edge: ordinary equipment forms -> complete alert wizard -> report -> handling. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const puppeteer = require('puppeteer-core');
const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011';
const RUN = Date.now().toString(36), OUTPUT = path.resolve('.implementation/ui-results/alerts-' + RUN);
const checks = [], failures = [], violations = [], calls = [], created = [];
let browser, page, frame, fixture, sourceId, workflowId, scheduleId, scheduleVersion, inboxMessage;
const pass = text => { checks.push(text); console.log('PASS ' + text); };
async function button(label, selector = 'button') {
  await frame.waitForFunction((label, selector) => [...document.querySelectorAll(selector)].some(el => el.getClientRects().length && el.textContent.trim() === label && !el.disabled), {}, label, selector);
  for (const handle of await frame.$$(selector)) if (await handle.evaluate((el, text) => el.getClientRects().length && el.textContent.trim() === text, label)) {
    await handle.scrollIntoView(); await frame.waitForFunction(el => { const rect = el.getBoundingClientRect(); return el.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2)); }, {}, handle); await handle.click(); return;
  }
  throw new Error('Missing button ' + label);
}
async function field(label, value, root = '.workflow-ui') {
  await frame.waitForFunction((label, root) => [...document.querySelectorAll(root + ' label')].some(el => el.getClientRects().length && el.firstChild?.textContent?.trim() === label && el.querySelector('input,textarea,select')), {}, label, root);
  const handle = await frame.evaluateHandle((label, root) => [...document.querySelectorAll(root + ' label')].find(el => el.getClientRects().length && el.firstChild?.textContent?.trim() === label)?.querySelector('input,textarea,select'), label, root);
  const element = handle.asElement(); assert(element, 'Missing field ' + label); await element.scrollIntoView();
  if (await element.evaluate(el => el.tagName === 'SELECT')) return element.select(value);
  await element.click({ clickCount: 3 }); await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control'); await element.type(value); await element.press('Tab');
}
async function api(url, method = 'GET', body) { return page.evaluate(async (url, method, body) => { const response = await fetch(url, { method, headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + localStorage.getItem('agent_token'), 'X-Workspace-Id': localStorage.getItem('agent_workspace_id') }, body: body === undefined ? undefined : JSON.stringify(body) }); return { status: response.status, body: await response.json() }; }, url, method, body); }
async function visit(route) { assert.equal((await page.goto(BASE + route, { waitUntil: 'networkidle0' })).status(), 200); frame = await (await page.waitForSelector('#business-module-frame')).contentFrame(); await frame.waitForSelector('.alerts-ui', { visible: true }); }
async function audit(label) { await frame.waitForFunction(() => !document.querySelector('[class*="enter-active"],[class*="leave-active"]')); await frame.addScriptTag({ path: require.resolve('axe-core/axe.min.js') }); const found = await frame.evaluate(async () => (await axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] } })).violations.map(item => ({ id: item.id, impact: item.impact, nodes: item.nodes.map(node => ({ target: node.target, summary: node.failureSummary })) }))); violations.push(...found.map(item => ({ label, ...item }))); }
async function openRunDialog() {
  await button('运行');
  try { await frame.waitForSelector('.el-dialog .workflow-ui select', { visible: true, timeout: 5000 }); return; }
  catch { /* Chromium can occasionally drop the first post-navigation pointer event. */ }
  assert(await frame.evaluate(() => { const control = [...document.querySelectorAll('.wf-toolbar button')].find(item => item.textContent.trim() === '运行' && !item.disabled); if (!control) return false; control.click(); return true; }));
  await frame.waitForSelector('.el-dialog .workflow-ui select', { visible: true });
}
async function save(kind) { const saving = page.waitForResponse(response => response.url().endsWith('/api/v1/alerts/' + kind) && response.request().method() === 'POST'); await button('保存配置'); const response = await saving; assert.equal(response.status(), 201, await response.text()); const row = (await response.json()).data; created.push([kind, row.id]); await frame.waitForSelector('#equipment-edit-form', { hidden: true }); return row; }
async function filter(name) { await field('名称', name, '.alert-filter'); await button('查询'); await frame.waitForFunction(name => document.querySelector('.alert-table')?.textContent.includes(name), {}, name); }

(async () => {
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, 'isolated'); assert(process.env.TEST5_E2E_PASSWORD);
  assert(['127.0.0.1', 'localhost'].includes(new URL(BASE).hostname)); assert(['test', 'ui-validation'].includes((await (await fetch(BASE + '/api/health')).json()).app));
  fs.mkdirSync(OUTPUT, { recursive: true });
  fixture = http.createServer((request, response) => {
    let body = ''; request.on('data', chunk => { body += chunk; }); request.on('end', () => {
      const payload = body ? JSON.parse(body) : {}; calls.push({ url: request.url, body: payload });
      response.writeHead(200, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({ items: [{ device: payload.device || 'COMP-A', temperature: '82.50' }] }));
    });
  });
  await new Promise(resolve => fixture.listen(0, '127.0.0.1', resolve));
  browser = await puppeteer.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true }); page = await browser.newPage(); await page.setViewport({ width: 1600, height: 1100 });
  page.on('pageerror', cause => failures.push(cause.message)); page.on('response', response => { if (response.status() >= 500) failures.push(response.status() + ' ' + response.url()); });
  await page.goto(BASE, { waitUntil: 'networkidle0' }); await page.type('#login-username', 'workspace-admin'); await page.type('#login-password', process.env.TEST5_E2E_PASSWORD); await page.click('#login-btn'); await page.waitForSelector('.overview-intro');
  const source = await api('/api/v1/collection/sources', 'POST', {
    name: 'alert-ui-source-' + RUN, url: 'http://127.0.0.1:' + fixture.address().port,
    method: 'POST', bodies: [{ device: 'COMP-A' }, { device: 'COMP-B' }], recordPath: '$.items',
    fields: [
      { name: 'device', path: '$.device', type: 'string', required: true },
      { name: 'temperature', path: '$.temperature', type: 'decimal', required: true },
    ], allowPrivate: true, allowAutomatic: true,
  }); assert.equal(source.status, 201, JSON.stringify(source.body)); sourceId = source.body.data.id;
  await visit('/alerts/devices'); await button('使用说明'); await frame.waitForSelector('.business-process-guide', { visible: true });
  assert.equal(await frame.$$eval('.business-guide-flow > li', items => items.length), 6);
  assert((await frame.$eval('.business-process-guide', item => item.innerText)).includes('从真实数据到问题闭环'));
  await page.screenshot({ path: path.join(OUTPUT, 'alert-usage-guide.png'), fullPage: true }); await page.keyboard.press('Escape'); await frame.waitForSelector('.business-process-guide', { hidden: true });
  pass('Equipment alert guidance explains the complete real-data lifecycle on demand without occupying the workbench');
  await button('新增设备'); await frame.waitForSelector('#equipment-edit-form');
  await field('设备编码', 'COMP-' + RUN); await field('设备名称', '真实压缩机 ' + RUN); await field('设备类型', '压缩机'); await field('安装位置', '一号车间'); await field('预警 / 通用主采集源', sourceId); await audit('Equipment form'); await page.screenshot({ path: path.join(OUTPUT, 'device-form.png'), fullPage: true }); const device = await save('devices'); assert.equal(calls.length, 0);
  const actionLayout = await frame.evaluate(name => { const row = [...document.querySelectorAll('.alert-table tbody tr')].find(item => item.textContent.includes(name)); const cell = row?.querySelector('.alert-action-cell'); const buttons = [...(cell?.querySelectorAll('button') || [])].map(item => Math.round(item.getBoundingClientRect().top)); return { wrap: cell ? getComputedStyle(cell.querySelector('.wf-actions')).flexWrap : '', tops: buttons, width: cell?.getBoundingClientRect().width || 0 }; }, device.name);
  assert.equal(actionLayout.wrap, 'nowrap'); assert(actionLayout.width >= 169); assert.equal(new Set(actionLayout.tops).size, 1, JSON.stringify(actionLayout));
  pass('Shared alert/quality device is saved without acquisition and its fixed action column keeps edit/remove controls aligned');
  await visit('/alerts/points'); await button('新增测点'); await field('所属设备', device.id); await field('测点号', 'T1'); await field('测点名称', '冷却温度 ' + RUN); await field('采集字段名', 'temperature'); await field('单位', '℃'); await field('期望同步间隔（分钟）', '5'); const point = await save('points'); assert.equal(point.frequencyMinutes, 5); pass('Measurement field, unit and desired interval are maintained through normal forms');
  await button('新增测点'); await field('所属设备', device.id); await field('测点号', 'D1'); await field('测点名称', '设备编号 ' + RUN); await field('采集字段名', 'device'); await field('单位', ''); await field('测点类型', 'string'); await field('期望同步间隔（分钟）', '5'); const devicePoint = await save('points'); assert.equal(devicePoint.fieldName, 'device');
  await visit('/alerts/advice'); await button('新增经验'); await field('经验名称', '冷却预案 ' + RUN); await field('适用工况', '持续高负载'); await field('处置预案', '检查冷却系统及循环水'); await field('预案简称', '冷却检查'); const advice = await save('advice');
  await visit('/alerts/rules'); await button('新增策略'); await field('策略名称', '温度超限 ' + RUN); await field('设备', device.id); await field('问题定位字段', 'device'); await field('专家经验', advice.id); await field('规则项名称', '温度阈值');
  await button('temperature', '.wf-field-picks button'); assert.equal(await frame.$eval('#equipment-edit-form', el => el.getClientRects().length > 0), true); assert.equal(calls.length, 0);
  await field('比较值', '"80.000000000000000001"'); await field('问题说明', '检查冷却系统'); await audit('Alert policy and shared expression editor'); await page.screenshot({ path: path.join(OUTPUT, 'alert-policy-form.png'), fullPage: true }); const rule = await save('rules'); assert.equal(rule.rules[0].expression.args[1].value, '80.000000000000000001'); pass('Policy selects device, measured field and expert advice while retaining an exact threshold; field selection does not submit the form');
  await filter(rule.name); await button('配置预警流程'); await frame.waitForSelector('.alert-workflow-wizard', { visible: true });
  await frame.waitForFunction(() => document.querySelectorAll('.alert-point-grid input[type="checkbox"]').length === 2);
  assert.equal(await frame.$$eval('.alert-point-grid input[type="checkbox"]', items => items.filter(item => item.checked).length), 0);
  assert.equal(await frame.$eval('#alert-workflow-wizard button[type="submit"]', item => item.disabled), true);
  assert((await frame.$eval('.alert-workflow-wizard', item => item.textContent)).includes('2 条批量请求体'));
  await audit('Alert workflow wizard desktop'); await page.screenshot({ path: path.join(OUTPUT, 'alert-workflow-wizard.png'), fullPage: true });
  pass('Alert workflow wizard fixes the policy revision and shows the actual batch HTTP/JSONPath source without selecting default fields or calling equipment');
  await page.setViewport({ width: 390, height: 844 }); await frame.waitForSelector('.alert-workflow-wizard', { visible: true });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)); assert(await frame.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await audit('390px alert workflow wizard'); await page.screenshot({ path: path.join(OUTPUT, 'alert-workflow-wizard-mobile.png'), fullPage: true });
  pass('Complete alert workflow form remains usable at 390px without whole-page horizontal overflow');
  await page.setViewport({ width: 1600, height: 1100 }); await button('勾选必需测点');
  assert.equal(await frame.$$eval('.alert-point-grid input[type="checkbox"]', items => items.filter(item => item.checked).length), 2);
  await button('添加计算'); await field('输出字段', 'temperatureF', '.alert-workflow-wizard'); await field('公式', 'temperature * 1.8 + 32', '.alert-workflow-wizard');
  for (const label of ['站内通知', '定时运行']) {
    assert(await frame.evaluate(label => { const toggle = [...document.querySelectorAll('.alert-option-toggle')].find(item => item.textContent.includes(label)); const input = toggle?.querySelector('input'); if (!input) return false; input.click(); return input.checked; }, label), 'Missing option ' + label);
  }
  await frame.waitForFunction(() => !document.querySelector('#alert-workflow-wizard button[type="submit"]').disabled);
  assert.equal(calls.length, 0);
  const createdFlow = page.waitForResponse(response => response.url().endsWith('/api/v1/alerts/workflows') && response.request().method() === 'POST'); await button('创建并发布');
  const flowResponse = await createdFlow; assert.equal(flowResponse.status(), 201, await flowResponse.text()); const creation = (await flowResponse.json()).data; const flow = creation.workflow; workflowId = flow.id; scheduleId = creation.schedule?.id; scheduleVersion = creation.schedule?.version;
  await frame.waitForSelector('.wf-editor');
  assert.equal(flow.status, 'published'); assert.equal(flow.publishedRevision, 1); assert.equal(creation.schedule?.status, 'paused');
  assert.deepEqual(flow.graph.nodes.map(node => node.kind), ['start', 'http-request', 'jsonpath-extract', 'arithmetic', 'alert-evaluate', 'alert-notify', 'end']);
  assert.equal(flow.graph.nodes.find(node => node.kind === 'alert-evaluate').config.ruleRevision, rule.version);
  assert.deepEqual(creation.selectedFields.sort(), ['device', 'temperature']); assert.equal(calls.length, 0);
  await audit('Published alert workflow editor'); pass('One customer form atomically creates a published batch collection, extraction, calculation, policy, notification and paused-schedule workflow');
  await openRunDialog(); await field('执行版本', 'published', '.el-dialog .workflow-ui'); const executionResponse = page.waitForResponse(response => response.url().endsWith('/runs') && response.request().method() === 'POST'); await button('确认执行'); const execution = await executionResponse; assert.equal(execution.status(), 200); const result = (await execution.json()).data;
  const reportOutput = Object.values(result.result.outputs).find(value => value && typeof value === 'object' && value.alertReportId); assert(reportOutput, JSON.stringify(result)); const reportId = reportOutput.alertReportId;
  await frame.waitForSelector('.wf-result', { visible: true }); await button('打开预警业务报告', 'a'); await frame.waitForSelector('.alert-report-detail', { visible: true });
  assert.equal(calls.length, 2); const reportText = await frame.$eval('.alert-report-detail', el => el.textContent); assert(reportText.includes('82.50')); assert(reportText.includes('COMP-A')); assert(reportText.includes('COMP-B')); assert(reportText.includes('检查冷却系统'));
  const inbox = await api('/api/v1/system-tools/inbox?limit=100'); assert.equal(inbox.status, 200); inboxMessage = inbox.body.items.find(item => item.messageType === 'alert' && item.body.includes(reportId)); assert(inboxMessage);
  await audit('Actual alert report and issue evidence'); await page.screenshot({ path: path.join(OUTPUT, 'actual-alert-report.png'), fullPage: true }); pass('Published workflow executes both saved request bodies and persists two issues, original evidence and one transactional inbox notification');
  await button('处理问题', '.alert-report-detail button'); await frame.waitForSelector('#problem-action-form'); await field('处置状态', 'resolved', '#problem-action-form'); await field('处置理由', '已检查并处理冷却回路', '#problem-action-form'); await button('保存处置'); await frame.waitForSelector('#problem-action-form', { hidden: true }); await frame.waitForFunction(() => document.querySelector('.alert-report-detail .alert-table')?.textContent.includes('已解决'));
  await button('处理问题', '.alert-report-detail button'); await frame.waitForFunction(() => document.querySelector('.alert-events')?.textContent.includes('已检查并处理冷却回路')); await audit('Problem handling and immutable history'); await page.screenshot({ path: path.join(OUTPUT, 'problem-handling.png'), fullPage: true }); await page.keyboard.press('Escape'); await frame.waitForSelector('#problem-action-form', { hidden: true });
  const savedReport = await api('/api/v1/alerts/reports/' + reportId); assert.equal(savedReport.body.data.summary.issues, 2); assert.equal(calls.length, 2); pass('Issue handling appends its reason and history without changing the original report or refetching equipment');
  const downloading = page.waitForResponse(response => response.url().endsWith('/reports/' + reportId + '/export')); await button('下载报告'); const download = await downloading; assert.equal(download.status(), 200); assert.equal(calls.length, 2); pass('Report download uses persisted evidence with no repeated device request');
  await page.setViewport({ width: 390, height: 844 }); await visit('/alerts/reports?report=' + reportId); await frame.waitForSelector('.alert-report-detail', { visible: true }); await audit('390px alert report'); assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)); assert(await frame.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)); await page.screenshot({ path: path.join(OUTPUT, 'alert-mobile.png'), fullPage: true }); pass('Report deep link reopens correctly at 390px without whole-page overflow');
  await frame.focus('[data-testid="alert-actual-data"]');
  assert(await frame.$eval('[data-testid="alert-actual-data"]', el => document.activeElement === el));
  await page.keyboard.press('ArrowRight');
  await frame.waitForFunction(() => document.querySelector('[data-testid="alert-actual-data"]').scrollLeft > 0);
  pass('The mobile report data region is keyboard focusable and ArrowRight scrolls real table columns');
  await page.setViewport({ width: 1600, height: 1100 }); await visit('/alert/report'); assert(new URL(page.url()).pathname === '/alerts/reports'); await audit('Legacy report route resolves to new ordinary page'); pass('Old /alert/report route resolves to the real report page, not 404');
  assert.deepEqual(failures, []); assert.deepEqual(violations, []);
})().catch(cause => { failures.push(cause.stack || String(cause)); console.error(cause); process.exitCode = 1; }).finally(async () => {
  if (page) try {
    if (inboxMessage) await api('/api/v1/system-tools/inbox/' + inboxMessage.id + '/archive', 'POST', { version: inboxMessage.version });
    if (scheduleId) await api('/api/v1/system-tools/jobs/' + scheduleId + '/status/archive', 'POST', { version: scheduleVersion });
    if (workflowId) { const row = await api('/api/v1/workflows/' + workflowId); if (row.status === 200) assert.equal((await api('/api/v1/workflows/' + workflowId + '?version=' + row.body.data.version, 'DELETE')).status, 200); }
    for (const [kind, id] of [...created].reverse()) { const row = await api('/api/v1/alerts/' + kind + '/' + id); if (row.status === 200) assert.equal((await api('/api/v1/alerts/' + kind + '/' + id + '?version=' + row.body.data.version, 'DELETE')).status, 200); }
    if (sourceId) { const row = await api('/api/v1/collection/sources/' + sourceId); if (row.status === 200) assert.equal((await api('/api/v1/collection/sources/' + sourceId + '?version=' + row.body.data.version, 'DELETE')).status, 200); }
    if (failures.length) await page.screenshot({ path: path.join(OUTPUT, 'failure.png'), fullPage: true });
  } catch (cause) { failures.push('Cleanup: ' + cause.message); process.exitCode = 1; }
  fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({ checks, failures, violations, actualHttpRequests: calls.length }, null, 2)); if (browser) await browser.close(); if (fixture) await new Promise(resolve => fixture.close(resolve)); console.log('Evidence ' + OUTPUT);
});
