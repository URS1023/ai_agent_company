/*
 * 中文说明（工作流主流程端到端验收）：
 * 验证行为：编辑工作流、固定数据源 HTTP 调用、发布与运行、以及运行结果追踪的完整链路。
 * 运行方式：需隔离服务 + 本机 Edge + TEST5_E2E_PASSWORD；本地 http 桩代表被调用的外部接口。
 * 边界：会发布并运行工作流（隔离环境），断言覆盖结果追踪。
 */
/* Real Edge workflow editing, fixed-source HTTP, publish/run and result tracing. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const puppeteer = require('puppeteer-core');
const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011';
const RUN = Date.now().toString(36), OUTPUT = path.resolve('.implementation/ui-results/workflows-' + RUN);
const checks = [], failures = [], violations = [], calls = [];
let browser, page, frame, fixture, sourceId, workflowId, importedId, generatedRequest;
const pass = label => { checks.push(label); console.log('PASS ' + label); };
async function button(label, selector = 'button') {
  if (['AI 生成', '检查配置', '版本与运行', '导出定义', '停用'].includes(label)) await frame.evaluate(() => { const menu = document.querySelector('.wf-more-actions'); if (menu) menu.open = true; });
  await frame.waitForFunction((label, selector) => [...document.querySelectorAll(selector)].some(el => el.getClientRects().length && el.textContent.trim() === label && !el.disabled), {}, label, selector);
  for (const element of await frame.$$(selector)) if (await element.evaluate((el, label) => el.getClientRects().length && el.textContent.trim() === label, label)) {
    await element.scrollIntoView();
    await frame.waitForFunction(el => { const rect = el.getBoundingClientRect(); return !el.disabled && el.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2)); }, {}, element);
    await element.click(); return;
  }
  throw new Error('Button not found: ' + label);
}
async function field(label, value, root = '.workflow-ui') {
  if (label === '用途说明') await frame.evaluate(() => { const details = document.querySelector('.wf-meta-details'); if (details) details.open = true; });
  const handle = await frame.evaluateHandle((label, root) => [...document.querySelectorAll(root + ' label')].find(el => el.getClientRects().length && el.firstChild?.textContent?.trim() === label)?.querySelector('input,textarea,select'), label, root);
  const element = handle.asElement(); assert(element, 'Field missing: ' + label);
  await element.scrollIntoView();
  if (await element.evaluate(el => el.tagName === 'SELECT')) return element.select(value);
  await element.click({ clickCount: 3 }); await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  await element.type(value); await element.press('Tab');
}
async function api(url, method = 'GET', body) { return page.evaluate(async (url, method, body) => { const response = await fetch(url, { method, headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + localStorage.getItem('agent_token'), 'X-Workspace-Id': localStorage.getItem('agent_workspace_id') }, body: body === undefined ? undefined : JSON.stringify(body) }); return { status: response.status, body: await response.json() }; }, url, method, body); }
async function visit(route) { assert.equal((await page.goto(BASE + route, { waitUntil: 'networkidle0' })).status(), 200); frame = await (await page.waitForSelector('#business-module-frame')).contentFrame(); await frame.waitForSelector('.workflow-ui', { visible: true }); }
async function audit(label) { await frame.waitForFunction(() => !document.querySelector('[class*="enter-active"],[class*="leave-active"]')); await frame.addScriptTag({ path: require.resolve('axe-core/axe.min.js') }); const found = await frame.evaluate(async () => (await axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] } })).violations.map(item => ({ id: item.id, impact: item.impact, nodes: item.nodes.map(node => ({ target: node.target, summary: node.failureSummary })) }))); violations.push(...found.map(item => ({ label, ...item }))); }
async function addNode(title) { await button('添加节点'); const handles = await frame.$$('.wf-palette button'); for (const handle of handles) if (await handle.$eval('strong', (el, title) => el.textContent === title, title)) { await handle.click(); await frame.waitForFunction(title => document.querySelector('.wf-node.selected span')?.textContent === title, {}, title); return; } throw new Error('Node not found ' + title); }
async function closeDialog(selector) { await page.keyboard.press('Escape'); await frame.waitForSelector(selector, { hidden: true }); }

(async () => {
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, 'isolated'); assert(process.env.TEST5_E2E_PASSWORD);
  assert(['127.0.0.1', 'localhost'].includes(new URL(BASE).hostname)); const health = await (await fetch(BASE + '/api/health')).json(); assert(['test', 'ui-validation'].includes(health.app));
  fs.mkdirSync(OUTPUT, { recursive: true });
  fixture = http.createServer((request, response) => { calls.push(request.url); response.writeHead(200, { 'Content-Type': 'application/json' }); response.end(JSON.stringify({ items: [{ device: '压缩机-真实采集', temperature: '82.50' }] })); });
  await new Promise(resolve => fixture.listen(0, '127.0.0.1', resolve));
  browser = await puppeteer.launch({ executablePath: process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  page = await browser.newPage(); await page.setViewport({ width: 1600, height: 1100 });
  const generatedFixture = { success: true, message: '', data: { runId: 'fixture-ai-run', model: 'fixture', usage: {}, workflow: { name: 'AI 合同草稿', description: '验证节点配置契约', domains: ['analysis'], status: 'draft', graph: { nodes: [
    { id: 'aistart', kind: 'start', title: '开始', position: { x: 40, y: 50 }, inputs: {}, config: {} },
    { id: 'aiquery', kind: 'database-query', title: '查询财务数据', position: { x: 260, y: 50 }, inputs: {}, config: { datasourceId: '', datasourceVersion: null, query: 'SELECT amount FROM sales', parameters: {}, maxRows: 1000 } },
    { id: 'aireply', kind: 'reply', title: '输出分析', position: { x: 480, y: 50 }, inputs: {}, config: {} },
  ], edges: [
    { id: 'aie1', source: 'aistart', target: 'aiquery', port: 'out' },
    { id: 'aie2', source: 'aiquery', target: 'aireply', port: 'out' },
  ] }, validation: { valid: false, order: ['aistart', 'aiquery', 'aireply'], nodeCount: 3, errors: [{ nodeId: 'aiquery', message: '节点配置不完整或类型错误：datasourceId：不能为空（请选择授权数据源）' }] } } } };
  await page.setRequestInterception(true);
  page.on('request', request => {
    if (new URL(request.url()).pathname === '/api/v1/workflows/generate') {
      generatedRequest = JSON.parse(request.postData() || '{}');
      return request.respond({ status: 200, contentType: 'application/json', body: JSON.stringify(generatedFixture) });
    }
    return request.continue();
  });
  page.on('pageerror', cause => failures.push(cause.message));
  page.on('response', response => { if (response.status() >= 500) failures.push(response.status() + ' ' + response.url()); });
  await page.goto(BASE, { waitUntil: 'networkidle0' }); await page.type('#login-username', 'workspace-admin'); await page.type('#login-password', process.env.TEST5_E2E_PASSWORD); await page.click('#login-btn'); await page.waitForSelector('.overview-intro');
  const specification = await api('/api/v1/platform/openapi.json'); assert.equal(specification.status, 200);
  const operations = Object.entries(specification.body.paths).filter(([route]) => route.startsWith('/api/v1/workflows')).flatMap(([, methods]) => Object.entries(methods).filter(([method]) => ['get', 'post', 'put', 'delete'].includes(method)).map(([, operation]) => operation));
  assert.equal(operations.length, 18); assert.equal(new Set(operations.map(operation => operation.operationId)).size, 18);
  assert(operations.every(operation => operation.summary && operation.description));
  const collection = await api('/api/v1/platform/api-collection'); assert.equal(collection.status, 200);
  const workflowCollection = collection.body.item.find(group => group.name === '共享可视化工作流'); assert.equal(workflowCollection.item.length, 18);
  assert(workflowCollection.item.every(item => item.request.description && (typeof item.request.url === 'string' ? item.request.url : item.request.url.raw).startsWith('{{base_url}}/api/v1/workflows')));
  pass('Authenticated API center includes all 18 workflow operations with descriptions and a grouped collection');
  const source = await api('/api/v1/collection/sources', 'POST', { name: 'workflow-ui-' + RUN, url: 'http://127.0.0.1:' + fixture.address().port + '/measurements', allowPrivate: true, allowAutomatic: true, recordPath: '$.items' });
  assert.equal(source.status, 201, JSON.stringify(source.body)); sourceId = source.body.data.id;
  await visit('/workflows');
  assert.equal(await page.$eval('[data-business-area="business"]', el => el.getAttribute('href')), '/workflows');
  assert.equal(await page.$$eval('.enterprise-sidebar a[href="/workflows"]', elements => elements.filter(el => el.getClientRects().length).length), 1);
  pass('Workflow is reachable in the business domain contextual menu, not the AI workspace');
  await button('新建工作流'); await frame.waitForSelector('.wf-editor');
  await field('流程名称', '预警与质检 UI 验收 ' + RUN, '.wf-meta'); await field('用途说明', '验证真实数据采集与逐项问题报告', '.wf-meta');
  await addNode('HTTP 采集'); await field('HTTP 数据源', sourceId, '.wf-properties'); await frame.waitForSelector('.wf-source-summary');
  const sourceSummary = await frame.$eval('.wf-source-summary', element => element.textContent); assert(sourceSummary.includes('GET') && sourceSummary.includes('/measurements') && sourceSummary.includes('可供工作流自动只读采集'));
  assert.equal(await frame.$$eval('.wf-source-actions button', items => items.filter(item => ['新建 HTTP 数据源', '编辑请求配置', '测试采集'].includes(item.textContent.trim())).length), 3);
  await button('编辑请求配置'); await frame.waitForSelector('.http-config-dialog .http-form', { visible: true }); assert.equal(await frame.$eval('.http-config-dialog input[type="url"]', item => item.value), 'http://127.0.0.1:' + fixture.address().port + '/measurements'); await closeDialog('.http-config-dialog');
  pass('HTTP workflow node exposes selected request details plus in-place create, edit and explicit test controls');
  await addNode('计算字段'); await field('输出字段', '温度折算', '.wf-properties'); await field('计算公式', 'temperature / 2', '.wf-properties');
  await addNode('逐项规则判定'); await field('规则名称', '折算温度超限', '.wf-properties'); await field('判断字段', '温度折算', '.wf-properties'); await field('比较值', '40', '.wf-properties'); await field('问题说明', '检查冷却系统', '.wf-properties');
  await field('比较值', '9007199254740993', '.wf-properties');
  await frame.waitForFunction(() => document.querySelector('.wf-expression [role="alert"]')?.textContent.includes('未应用该输入'));
  assert.equal(await frame.$eval('.wf-expression label:last-of-type input', el => el.value), '40');
  await field('比较值', '"40.000000000000000001"', '.wf-properties');
  assert.equal(await frame.$eval('.wf-expression label:last-of-type input', el => el.value), '"40.000000000000000001"');
  pass('Unsafe numeric thresholds are rejected; exact quoted decimals keep their text type while editing');
  const nodeIds = await frame.$$eval('.wf-node', nodes => Object.fromEntries(nodes.map(node => [node.querySelector('span').textContent, node.dataset.nodeId])));
  await button('连线配置 · 1 条', 'summary'); await button('移除', '.wf-edge-list button');
  for (const [a, b] of [['开始', 'HTTP 采集'], ['HTTP 采集', '计算字段'], ['计算字段', '逐项规则判定'], ['逐项规则判定', '结束']]) { await field('来源节点', nodeIds[a], '.wf-connect-form'); await field('目标节点', nodeIds[b], '.wf-connect-form'); await button('添加连线'); }
  await button('自动排布'); await button('适应画布');
  const created = page.waitForResponse(response => response.url().endsWith('/api/v1/workflows') && response.request().method() === 'POST'); await button('保存草稿'); const response = await created; assert.equal(response.status(), 201); const createdFlow = (await response.json()).data; workflowId = createdFlow.id;
  assert.equal(createdFlow.graph.nodes.find(node => node.kind === 'http-request').config.datasourceVersion, source.body.data.version);
  assert.equal(createdFlow.graph.nodes.find(node => node.kind === 'rule-evaluate').config.rules[0].expression.args[1].value, '40.000000000000000001');
  await frame.waitForFunction(() => document.querySelector('.wf-toolbar')?.textContent.includes('草稿 v1'));
  assert.equal(calls.length, 0); pass('Created and connected five nodes through normal forms; saving sends no device requests');
  await button('检查配置'); await frame.waitForFunction(() => document.querySelector('.wf-validation')?.textContent.includes('结构检查通过'));
  await button('适应画布'); await audit('Workflow graph and rule editor'); await page.screenshot({ path: path.join(OUTPUT, 'workflow-editor-desktop.png'), fullPage: true });
  await button('发布'); await button('确认'); await frame.waitForFunction(() => document.querySelector('.wf-toolbar')?.textContent.includes('正式 v2'));
  await button('运行'); await field('执行版本', 'published', '.el-dialog .workflow-ui');
  await field('开始节点输入（JSON 对象）', '{"amount":0.10000000000000000001}', '.el-dialog .workflow-ui'); await button('确认执行');
  await frame.waitForFunction(() => [...document.querySelectorAll('.el-dialog [role="alert"]')].some(el => el.textContent.includes('未应用该输入'))); assert.equal(calls.length, 0);
  await field('开始节点输入（JSON 对象）', '{"amount":"0.10000000000000000001"}', '.el-dialog .workflow-ui');
  pass('Unsafe JSON runtime input is blocked before any HTTP request, and quoted input can be corrected in place');
  const running = page.waitForResponse(response => response.url().endsWith('/runs') && response.request().method() === 'POST'); await button('确认执行'); const runResponse = await running; assert.equal(runResponse.status(), 200); const run = (await runResponse.json()).data;
  assert.equal(run.inputs.amount, '0.10000000000000000001');
  await frame.waitForSelector('.wf-result', { visible: true }); const text = await frame.$eval('.wf-result', el => el.textContent); assert(text.includes('41.25') && text.includes('检查冷却系统') && text.includes('压缩机-真实采集')); assert.equal(calls.length, 1);
  assert.equal(Object.keys(run.result.nodes).length, 5); assert.equal(run.revision, 2); assert.equal(Object.values(run.result.outputs)[0].issues.length, 1);
  await audit('Workflow real data and issue list'); await page.screenshot({ path: path.join(OUTPUT, 'workflow-real-problems.png') }); pass('Published workflow fetched actual HTTP, calculated exact decimals and displayed a problem list with trace');
  await closeDialog('.wf-result'); await button('版本与运行'); await button('查看结果'); await frame.waitForSelector('.wf-result', { visible: true }); assert.equal(calls.length, 1); await closeDialog('.wf-result'); await page.keyboard.press('Escape'); pass('Historical result reopens without repeating device calls');
  await visit('/workflows/' + workflowId); await frame.waitForSelector('.wf-node'); const first = await frame.$('.wf-node'); const before = await first.evaluate(el => parseFloat(el.style.left)); await first.focus(); await first.press('ArrowRight'); assert.equal(await first.evaluate(el => parseFloat(el.style.left)), before + 20); await button('保存草稿'); await frame.waitForFunction(() => document.querySelector('.wf-toolbar')?.textContent.includes('草稿 v3'));
  const saved = (await api('/api/v1/workflows/' + workflowId)).body.data; assert.equal(saved.publishedRevision, 2); pass('Keyboard moves nodes; edited draft preserves the separately published version');
  const exportedResponse = page.waitForResponse(response => response.url().endsWith('/export'));
  await button('导出定义'); const exported = (await (await exportedResponse).json()).data;
  assert.equal(exported.schemaVersion, 1); assert.equal(exported.workflow.graph.nodes.length, 5);
  const importedFile = path.join(OUTPUT, 'import-fixture.json');
  fs.writeFileSync(importedFile, JSON.stringify({ ...exported, workflow: { ...exported.workflow, id: workflowId, version: 3, name: '导入副本 ' + RUN } }));
  await visit('/workflows'); const importing = page.waitForResponse(response => response.url().endsWith('/api/v1/workflows') && response.request().method() === 'POST');
  await (await frame.$('input[type="file"]')).uploadFile(importedFile); const imported = await importing; assert.equal(imported.status(), 201); importedId = (await imported.json()).data.id;
  assert.notEqual(importedId, workflowId); assert.equal((await api('/api/v1/workflows/' + workflowId)).body.data.version, 3);
  pass('Export and file import create a separate draft, ignoring target IDs in the import file');
  await page.setViewport({ width: 390, height: 844 }); await visit('/workflows/' + workflowId); await frame.waitForSelector('.wf-meta input');
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)); assert(await frame.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)); await audit('390px workflow editor'); await page.screenshot({ path: path.join(OUTPUT, 'workflow-mobile.png'), fullPage: true }); pass('Mobile editor fits 390px with local canvas scrolling and accessible fields');
  await page.setViewport({ width: 1600, height: 1100 }); await visit('/workflows'); await field('流程名称', '预警与质检 UI 验收 ' + RUN, '.wf-list-filter'); await button('查询'); await frame.waitForFunction(run => document.querySelector('.wf-table')?.textContent.includes(run), {}, RUN); await audit('Workflow list'); await page.screenshot({ path: path.join(OUTPUT, 'workflow-list.png') }); pass('Independent list route, filtered lookup and deep-link refresh work');
  await visit('/workflows/' + workflowId); await button('AI 生成'); await frame.waitForSelector('.wf-ai-generator', { visible: true }); assert((await frame.$eval('.wf-ai-resources', item => item.textContent)).includes('本次可用数据源'));
  await frame.click('.wf-ai-resource-card input'); await field('业务目标', '查询财务数据并输出可编辑分析流程', '.wf-ai-generator');
  const generatedResponse = page.waitForResponse(response => response.url().endsWith('/api/v1/workflows/generate') && response.request().method() === 'POST');
  await button('生成草稿'); assert.equal((await generatedResponse).status(), 200);
  assert.deepEqual(generatedRequest.resourceIds, [sourceId]); pass('AI generator sends only the data source explicitly selected by the user');
  await frame.waitForFunction(() => document.querySelector('.wf-node.selected')?.dataset.nodeId === 'aiquery' && document.querySelector('.wf-node.invalid'));
  const contractState = await frame.$eval('.wf-properties', element => element.textContent); assert(contractState.includes('待补 1 项') && contractState.includes('当前节点尚不可运行') && contractState.includes('datasourceId'));
  await frame.click('.wf-contract-details summary'); const contractText = await frame.$eval('.wf-contract-details', element => element.textContent); assert(contractText.includes('datasourceId') && contractText.includes('启用且获准'));
  await audit('AI workflow node contract guidance'); await page.screenshot({ path: path.join(OUTPUT, 'workflow-ai-contract.png'), fullPage: true }); pass('AI draft selects the first invalid node and exposes its actionable property contract');
  assert.deepEqual(failures, []); assert.deepEqual(violations, []);
})().catch(cause => { failures.push(cause.stack || String(cause)); console.error(cause); process.exitCode = 1; }).finally(async () => {
  if (page) {
    try {
      for (const id of [importedId, workflowId].filter(Boolean)) { const row = await api('/api/v1/workflows/' + id); if (row.status === 200) assert.equal((await api('/api/v1/workflows/' + id + '?version=' + row.body.data.version, 'DELETE')).status, 200); }
      if (sourceId) { const row = await api('/api/v1/collection/sources/' + sourceId); if (row.status === 200) assert.equal((await api('/api/v1/collection/sources/' + sourceId + '?version=' + row.body.data.version, 'DELETE')).status, 200); }
      if (failures.length) await page.screenshot({ path: path.join(OUTPUT, 'failure.png'), fullPage: true });
    } catch (cause) { failures.push('Cleanup: ' + cause.message); process.exitCode = 1; }
  }
  fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({ checks, failures, violations, actualHttpRequests: calls.length }, null, 2));
  if (browser) await browser.close(); if (fixture) await new Promise(resolve => fixture.close(resolve)); console.log('Evidence ' + OUTPUT);
});
