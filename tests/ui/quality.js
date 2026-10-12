/*
 * 中文说明（质检流程端到端浏览器验收）：
 * 验证行为：普通设备表单 -> 专用质检流程 -> 报告 -> 问题处置的完整链路。
 * 运行方式：需隔离服务（TEST5_E2E_BASE_URL，默认 8011）+ 本机 Edge + TEST5_E2E_PASSWORD；
 *   文件内本地 http 桩提供被检测数据。
 * 边界：会创建质检流程与报告（隔离环境）。
 */
/* Real Edge: ordinary equipment forms -> dedicated workflow -> report -> handling. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const puppeteer = require('puppeteer-core');
const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011';
const RUN = Date.now().toString(36), OUTPUT = path.resolve('.implementation/ui-results/quality-' + RUN);
const checks = [], failures = [], violations = [], calls = [], created = [];
let browser, page, frame, fixture, sourceId, workflowId, deviceId;
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
  if (await element.evaluate(el => el.tagName === 'SELECT')) { await frame.waitForFunction((el, value) => !el.disabled && [...el.options].some(option => option.value === value), {}, element, value); return element.select(value); }
  await element.click({ clickCount: 3 }); await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control'); await element.type(value); await element.press('Tab');
}
async function api(url, method = 'GET', body) { return page.evaluate(async (url, method, body) => { const response = await fetch(url, { method, headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + localStorage.getItem('agent_token'), 'X-Workspace-Id': localStorage.getItem('agent_workspace_id') }, body: body === undefined ? undefined : JSON.stringify(body) }); return { status: response.status, body: await response.json() }; }, url, method, body); }
async function visit(route) { assert.equal((await page.goto(BASE + route, { waitUntil: 'networkidle0' })).status(), 200); frame = await (await page.waitForSelector('#business-module-frame')).contentFrame(); await frame.waitForSelector('.quality-ui', { visible: true }); }
async function audit(label) { await frame.waitForFunction(() => !document.querySelector('[class*="enter-active"],[class*="leave-active"]')); await frame.addScriptTag({ path: require.resolve('axe-core/axe.min.js') }); const found = await frame.evaluate(async () => (await axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] } })).violations.map(item => ({ id: item.id, impact: item.impact, nodes: item.nodes.map(node => ({ target: node.target, summary: node.failureSummary })) }))); violations.push(...found.map(item => ({ label, ...item }))); }

async function save(kind) {
  const waiting = page.waitForResponse(response => response.url().endsWith('/api/v1/quality/' + kind) && response.request().method() === 'POST');
  await button(kind === 'inspections' ? '保存质检记录' : '保存配置');
  const response = await waiting; assert.equal(response.status(), 201, await response.text());
  const row = (await response.json()).data; created.push([kind, row.id]);
  await frame.waitForSelector(kind === 'inspections' ? '#qc-inspection-form' : '#qc-config-form', { hidden: true }); return row;
}
async function recorded(name) {
  const checkbox = await frame.evaluateHandle(name => [...document.querySelectorAll('.qc-observation')].find(el => el.querySelector('legend')?.textContent.includes(name))?.querySelector('input[type="checkbox"]'), name);
  assert(checkbox.asElement()); await checkbox.asElement().click();
}
(async () => {
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, 'isolated'); assert(process.env.TEST5_E2E_PASSWORD);
  assert(['127.0.0.1', 'localhost'].includes(new URL(BASE).hostname)); assert.equal((await (await fetch(BASE + '/api/health')).json()).app, 'test');
  fs.mkdirSync(OUTPUT, { recursive: true });
  fixture = http.createServer((request, response) => { calls.push(request.url); response.writeHead(200, { 'Content-Type': 'application/json' }); response.end(JSON.stringify({ items: [{ sampleId: 'LOT-' + RUN, imageRef: 'file:original-' + RUN, defects: [], components: [{ componentName: 'C', measuredValue: '0.300000000000000000001', unit: '%' }, { componentName: 'Fe', measuredValue: '99.7', unit: '%' }] }] })); });
  await new Promise(resolve => fixture.listen(0, '127.0.0.1', resolve));
  browser = await puppeteer.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true }); page = await browser.newPage(); await page.setViewport({ width: 1600, height: 1100 });
  page.on('pageerror', cause => failures.push(cause.message)); page.on('response', response => { if (response.status() >= 500) failures.push(response.status() + ' ' + response.url()); }); page.on('dialog', dialog => dialog.accept());
  await page.goto(BASE, { waitUntil: 'networkidle0' }); await page.type('#login-username', 'workspace-admin'); await page.type('#login-password', process.env.TEST5_E2E_PASSWORD); await page.click('#login-btn'); await page.waitForSelector('.overview-intro');
  const source = await api('/api/v1/collection/sources', 'POST', { name: 'qc-ui-source-' + RUN, url: 'http://127.0.0.1:' + fixture.address().port, recordPath: '$.items', allowPrivate: true, allowAutomatic: true }); assert.equal(source.status, 201); sourceId = source.body.data.id;
  const equipment = await api('/api/v1/alerts/devices', 'POST', { code: 'QC-' + RUN, name: '材料检验设备 ' + RUN, datasourceId: sourceId, category: 'spectrometer', domains: ['quality'] }); assert.equal(equipment.status, 201); deviceId = equipment.body.data.id;
  await visit('/quality/metrics'); await button('新增指标'); await field('指标名称', '碳含量 ' + RUN); await field('检测类别', 'component'); await field('成分名称', 'C'); await field('单位', '%'); await field('标准下限（含端点）', '0.10'); await field('标准上限（含端点）', '0.20');
  await audit('Normal metric form'); await page.screenshot({ path: path.join(OUTPUT, 'metric-form.png'), fullPage: true }); const carbon = await save('metrics'); assert.equal(carbon.standardMax, '0.20'); assert.equal(calls.length, 0);
  await button('新增指标'); await field('指标名称', '材料外观 ' + RUN); await field('检测类别', 'appearance'); const appearance = await save('metrics'); pass('Normal metric forms preserve exact component bounds and appearance criteria without calling devices');
  await visit('/quality/templates'); await button('新增模板'); await field('模板名称', '材料检验标准 ' + RUN); await field('适用设备类别', 'spectrometer'); await field('添加检验指标', carbon.id); await frame.waitForFunction(() => document.querySelector('.qc-selected')?.textContent.includes('碳含量')); await field('添加检验指标', appearance.id); await frame.waitForFunction(() => document.querySelectorAll('.qc-selected li').length === 2);
  await audit('Explicit two-metric template'); await page.screenshot({ path: path.join(OUTPUT, 'template-form.png'), fullPage: true }); const template = await save('templates'); assert.equal(template.metricIds.length, 2); assert.equal(calls.length, 0); pass('Customer selects two indicators to create a fixed template revision, with no default template');
  await visit('/quality/inspections'); await button('新增质检');
  const selection = await frame.evaluate(() => [...document.querySelectorAll('#qc-inspection-form select')].map(el => el.value)); assert(selection.every(value => value === ''), 'No automatic template, revision or device selection');
  await field('质检模板', template.id); await field('固定模板修订', '1'); await field('受检设备', deviceId); await field('样本 / 批次标识', 'MANUAL-' + RUN);
  await recorded('碳含量'); await field('实测值', '0.300000000000000000001'); await recorded('材料外观'); await field('图像资料标识', 'file:manual-original-' + RUN); await field('外观检查结果', 'none');
  await audit('Ordinary inspection observations'); await page.screenshot({ path: path.join(OUTPUT, 'inspection-form.png'), fullPage: true }); const manual = await save('inspections'); await frame.waitForSelector('.qc-report', { visible: true }); assert.equal(manual.outcome, 'failed'); assert(manual.results.every(row => row.provenance.mode === 'manual')); assert.equal(manual.results[0].observation.value, '0.300000000000000000001'); pass('Manual entry stores precise measurements and explicit no-defect observation under the chosen template');
  await field('复核指标', carbon.id); await field('复核结论', 'passed'); await field('复核依据', '已用标准样品复测，原始读数与人工结论分别保留'); const [reviewed] = await Promise.all([page.waitForResponse(response => response.url().endsWith('/reviews') && response.request().method() === 'POST'), button('保存复核')]); assert.equal(reviewed.status(), 200); await frame.waitForFunction(() => document.querySelector('.qc-history')?.textContent.includes('标准样品'));
  const stored = await api('/api/v1/quality/inspections/' + manual.id); assert.equal(stored.body.data.outcome, 'failed'); assert.equal(stored.body.data.effectiveOutcome, 'passed'); assert.equal(stored.body.data.reviews.length, 1); assert.equal(calls.length, 0);
  await audit('Separate original judgment and review'); await page.screenshot({ path: path.join(OUTPUT, 'reviewed-report.png'), fullPage: true }); pass('Human review appends a reason and actor, retaining the original failed judgment and exact evidence');
  const downloading = page.waitForResponse(response => response.url().endsWith('/inspections/' + manual.id + '/export')); await button('下载质检报告'); assert.equal((await downloading).status(), 200); assert.equal(calls.length, 0); pass('Report download reads stored evidence without requesting a detector');
  const node = (id, kind, config = {}) => ({ id, kind, title: id, config });
  const common = { deviceId, datasourceId: sourceId, datasourceVersion: source.body.data.version, metricVersion: 1 };
  const graph = { nodes: [node('start','start'), node('appearance','qc-appearance',{ ...common, metricId: appearance.id }), node('component','qc-component',{ ...common, metricId: carbon.id }), node('merge','merge'), node('evaluate','qc-evaluate',{ deviceId, templateId: template.id, templateRevision: 1, sampleId: 'LOT-' + RUN }), node('end','end')], edges: [['start','appearance'],['appearance','component'],['appearance','merge'],['component','merge'],['merge','evaluate'],['evaluate','end']].map(([source,target],i) => ({id:'edge'+i,source,target,port:'out'})) };
  const createdFlow = await api('/api/v1/workflows', 'POST', { name: '质检验收流程 ' + RUN, domains: ['quality'], graph }); assert.equal(createdFlow.status, 201, JSON.stringify(createdFlow.body)); workflowId = createdFlow.body.data.id;
  await page.goto(BASE + '/workflows/' + workflowId, { waitUntil: 'networkidle0' }); frame = await (await page.waitForSelector('#business-module-frame')).contentFrame(); await frame.waitForSelector('.wf-editor'); await button('自动排布'); await frame.click('.wf-node[data-node-id="appearance"]'); await field('外观指标', appearance.id, '.wf-properties'); await audit('Dedicated appearance node form');
  await page.screenshot({ path: path.join(OUTPUT, 'quality-workflow.png'), fullPage: true }); await button('保存草稿'); await frame.waitForFunction(() => !document.querySelector('.wf-toolbar')?.textContent.includes('未保存')); await button('发布'); await button('确认'); await frame.waitForFunction(() => document.querySelector('.wf-toolbar')?.textContent.includes('正式'));
  await button('运行'); await field('执行版本', 'published', '.el-dialog .workflow-ui'); const executed = page.waitForResponse(response => response.url().endsWith('/runs') && response.request().method() === 'POST'); await button('确认执行'); const executionResponse = await executed; assert.equal(executionResponse.status(), 200, await executionResponse.text()); const run = (await executionResponse.json()).data; const autoId = run.result.outputs.end.qualityInspectionId; created.push(['inspections', autoId]);
  await frame.waitForSelector('.wf-result'); await button('打开质检业务报告', 'a'); await frame.waitForSelector('.qc-report', { visible: true }); const automatic = (await api('/api/v1/quality/inspections/' + autoId)).body.data; assert.equal(automatic.outcome, 'failed'); assert.equal(automatic.results[0].observation.value, '0.300000000000000000001'); assert.equal(automatic.results[1].outcome, 'passed'); assert(automatic.results.every(row => row.provenance.mode === 'detector')); assert.equal(calls.length, 2);
  await audit('Real detector report'); await page.screenshot({ path: path.join(OUTPUT, 'detector-report.png'), fullPage: true }); pass('Actual appearance and composition HTTP nodes merge into one complete per-item quality report');
  await page.setViewport({ width: 390, height: 844 }); await visit('/quality/inspections?inspection=' + autoId); await frame.waitForSelector('.qc-report', { visible: true }); await audit('390px quality report'); assert(await frame.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)); assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)); await page.screenshot({ path: path.join(OUTPUT, 'quality-mobile.png'), fullPage: true }); assert.equal(calls.length, 2); pass('390px report deep link is readable and history does not repeat device calls');
  await visit('/quality/templates'); await button('新增模板'); await audit('390px template form'); assert(await frame.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)); await page.screenshot({ path: path.join(OUTPUT, 'template-mobile.png'), fullPage: true }); pass('Mobile template configuration stays within the enterprise page with accessible labels and controls');
  assert.deepEqual(failures, []); assert.deepEqual(violations, []);
})().catch(async cause => { if (frame) try { console.error('UI failure state', await frame.evaluate(() => ({ forms: [...document.querySelectorAll('form')].map(form => ({ id: form.id, className: form.className, valid: form.checkValidity(), fields: [...form.querySelectorAll('input,select,textarea')].map(el => ({ name: el.closest('label')?.textContent.slice(0,40), value: el.value, disabled: el.disabled, validation: el.validationMessage })) })), error: [...document.querySelectorAll('.wf-error')].map(el => el.textContent) }))); } catch {} failures.push(cause.stack || String(cause)); console.error(cause); process.exitCode = 1; }).finally(async () => {
  if (page) try {
    for (const [kind, id] of [...created].reverse()) { const row = await api('/api/v1/quality/' + kind + '/' + id); if (row.status === 200) assert.equal((await api('/api/v1/quality/' + kind + '/' + id + '?version=' + row.body.data.version, 'DELETE')).status, 200); }
    if (workflowId) { const row = await api('/api/v1/workflows/' + workflowId); if (row.status === 200) assert.equal((await api('/api/v1/workflows/' + workflowId + '?version=' + row.body.data.version, 'DELETE')).status, 200); }
    if (deviceId) { const row = await api('/api/v1/alerts/devices/' + deviceId); if (row.status === 200) assert.equal((await api('/api/v1/alerts/devices/' + deviceId + '?version=' + row.body.data.version, 'DELETE')).status, 200); }
    if (sourceId) { const row = await api('/api/v1/collection/sources/' + sourceId); if (row.status === 200) assert.equal((await api('/api/v1/collection/sources/' + sourceId + '?version=' + row.body.data.version, 'DELETE')).status, 200); }
    if (failures.length) await page.screenshot({ path: path.join(OUTPUT, 'failure.png'), fullPage: true });
  } catch(cause) { failures.push('Cleanup: ' + cause.message); process.exitCode = 1; }
  fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({checks,failures,violations,actualHttpRequests:calls.length}, null, 2)); if (browser) await browser.close(); if (fixture) await new Promise(resolve => fixture.close(resolve)); console.log('Evidence ' + OUTPUT);
});
