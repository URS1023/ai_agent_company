/*
 * 中文说明（工作流可用性回归）：
 * 验证行为：紧凑版工作流编辑器的布局、AI 身份标识、进度展示与知识表单在隔离环境中不回归。
 * 运行方式：需隔离服务 + 本机 Edge；产生工作流编辑数据（隔离环境）。
 * 边界："Isolated" 指应用与数据库都是隔离实例，不得指向生产。
 */
/* Isolated regression: compact workflow editor, AI identity, progress, and knowledge forms. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');
const BASE = process.env.TEST5_E2E_BASE_URL;
const OUTPUT = path.resolve('.implementation/ui-results/workflow-usability-' + Date.now().toString(36));

(async () => {
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, 'isolated');
  assert(BASE && ['localhost', '127.0.0.1'].includes(new URL(BASE).hostname));
  assert.equal((await (await fetch(BASE + '/api/health')).json()).app, 'test');
  fs.mkdirSync(OUTPUT, { recursive: true });
  const browser = await puppeteer.launch({ executablePath: process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  const page = await browser.newPage();
  const errors = [], checks = [];
  const pass = message => { checks.push(message); console.log('PASS ' + message); };
  let workflowId, kbId, frame, generated, mockRun = false, resultRequests = 0;
  page.on('pageerror', error => errors.push(error.message));
  const api = (route, method = 'GET', body) => page.evaluate(async (route, method, body) => {
    const response = await fetch(route, { method, headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + localStorage.getItem('agent_token'), 'X-Workspace-Id': localStorage.getItem('agent_workspace_id') }, body: body === undefined ? undefined : JSON.stringify(body) });
    return { status: response.status, body: await response.json() };
  }, route, method, body);
  async function button(text, context = frame) {
    if (['AI 生成', '检查配置', '版本与运行', '导出定义', '停用'].includes(text)) await context.evaluate(() => { document.querySelector('.wf-more-actions').open = true; });
    const handle = await context.evaluateHandle(text => [...document.querySelectorAll('button')].find(element => element.getClientRects().length && element.textContent.trim() === text && !element.disabled), text);
    const element = handle.asElement(); assert(element, 'Missing enabled button: ' + text);
    await element.scrollIntoView();
    await context.waitForFunction(element => {
      const rect = element.getBoundingClientRect();
      return element.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2));
    }, {}, element);
    await element.click();
  }
  async function visit(route) {
    await page.goto(BASE + route, { waitUntil: 'networkidle0' });
    frame = await (await page.waitForSelector('#business-module-frame')).contentFrame();
    await frame.waitForSelector('.wf-editor');
  }
  async function change(selector, value, context = frame) {
    await context.$eval(selector, (element, value) => { element.value = value; element.dispatchEvent(new Event('input', { bubbles: true })); element.dispatchEvent(new Event('change', { bubbles: true })); }, value);
  }
  try {
    await page.setViewport({ width: 1262, height: 568 });
    await page.goto(BASE, { waitUntil: 'networkidle0' });
    await page.type('#login-username', 'workspace-admin');
    await page.type('#login-password', process.env.TEST5_E2E_PASSWORD);
    await page.click('#login-btn');
    await page.waitForFunction(() => localStorage.getItem('agent_token'));
    const nodes = [
      { id: 'start', kind: 'start', title: '开始', position: { x: 40, y: 50 }, inputs: {}, config: {} },
      { id: 'end', kind: 'end', title: '结束', position: { x: 720, y: 50 }, inputs: {}, config: {} },
    ];
    const graph = { nodes, edges: [{ id: 'edge', source: 'start', target: 'end', port: 'out' }] };
    const created = await api('/api/v1/workflows', 'POST', { name: '可用性回归 ' + Date.now(), description: '', domains: ['general'], graph });
    assert.equal(created.status, 201, JSON.stringify(created.body)); workflowId = created.body.data.id;
    await visit('/workflows/' + workflowId);
    const frameTop = await page.$eval('#business-module-frame', element => element.getBoundingClientRect().top);
    const geometry = await frame.evaluate(() => {
      const canvas = document.querySelector('.wf-canvas-scroll').getBoundingClientRect();
      const node = document.querySelector('.wf-node').getBoundingClientRect();
      return { canvasTop: canvas.top, nodeBottom: node.bottom, viewport: innerHeight, detailsOpen: document.querySelector('.wf-meta-details').open, visibleButtons: [...document.querySelectorAll('.wf-main-actions button')].filter(element => element.getClientRects().length).map(element => element.textContent.trim()) };
    });
    assert(geometry.nodeBottom + frameTop <= 568, JSON.stringify({ ...geometry, frameTop }));
    assert.equal(geometry.detailsOpen, false);
    assert.deepEqual(geometry.visibleButtons, ['保存草稿', '运行', '发布']);
    await page.screenshot({ path: path.join(OUTPUT, 'workflow-short-screen.png') });
    pass('1262×568 首屏看到工作流节点，基础信息和低频操作已折叠');

    await change('.wf-meta input', '尚未保存的回归修改');
    const cancelLeave = new Promise(resolve => page.once('dialog', async dialog => { assert.equal(dialog.type(), 'confirm'); await dialog.dismiss(); resolve(); }));
    await button('返回流程列表'); await cancelLeave;
    assert.equal(await frame.$eval('.wf-meta input', element => element.value), '尚未保存的回归修改');
    await page.waitForFunction(id => location.pathname === '/workflows/' + id, {}, workflowId);
    pass('拒绝离开时保留当前编辑内容');
    await button('保存草稿');
    await frame.waitForFunction(() => document.querySelector('.wf-version-state').textContent.includes('修改已保存'));

    generated = { name: 'AI 修改同一个流程', description: 'fixture', domains: ['general'], status: 'draft', graph, validation: { valid: true, errors: [], order: ['start', 'end'] } };
    await page.setRequestInterception(true);
    page.on('request', request => {
      const url = new URL(request.url());
      if (url.pathname === '/api/v1/workflows/generate') return request.respond({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, data: { workflow: generated, generation: { mode: 'model', warnings: [] } } }) });
      if (mockRun && url.pathname === `/api/v1/workflows/${workflowId}/runs` && request.method() === 'POST') return request.respond({ status: 202, contentType: 'application/json', body: JSON.stringify({ success: true, data: { id: 'fixture-progress', workflowId, revision: 2, version: 1, runId: 'fixture', status: 'running', durationMs: 0, result: { nodes: {}, outputs: {} } } }) });
      if (mockRun && url.pathname === `/api/v1/workflows/${workflowId}/runs/fixture-progress`) {
        resultRequests++;
        return request.respond({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, data: { id: 'fixture-progress', workflowId, revision: 2, version: 2, runId: 'fixture', status: 'completed', durationMs: 10, result: { nodes: { end: { nodeId: 'end', title: '结束', status: 'completed', output: { summary: '自动更新完成' } } }, outputs: { end: {} } } } }) });
      }
      if (mockRun && url.searchParams.get('executionId') === 'fixture-progress' && url.pathname.includes('write')) return request.respond({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, data: [] }) });
      return request.continue();
    });
    await button('AI 生成');
    await frame.waitForSelector('.wf-ai-generator', { visible: true });
    await change('.wf-ai-generator textarea', '创建开始到结束的工作流，不访问外部服务。');
    await button('生成草稿');
    await frame.waitForFunction(() => document.querySelector('.wf-meta input')?.value === 'AI 修改同一个流程');
    const saveResponse = page.waitForResponse(response => response.url().endsWith('/api/v1/workflows/' + workflowId) && response.request().method() === 'PUT');
    await button('保存草稿');
    assert.equal((await saveResponse).status(), 200);
    await frame.waitForFunction(() => document.querySelector('.wf-version-state').textContent.includes('修改已保存'));
    assert.equal((await api('/api/v1/workflows/' + workflowId)).body.data.name, generated.name);
    pass('AI 改写已有流程后保存使用原 ID 更新，没有悄悄新增流程');

    mockRun = true;
    await button('运行'); await button('确认执行');
    await frame.waitForFunction(() => document.querySelector('.wf-result h2')?.textContent.includes('执行完成'), { timeout: 12000 });
    assert(resultRequests > 0);
    assert.equal(await frame.$eval('.wf-result select', element => element.value), 'end');
    pass('运行中状态自动更新为完成，新增节点结果可直接查看');
    await page.keyboard.press('Escape');
    await page.setViewport({ width: 390, height: 844 });
    await visit('/workflows/' + workflowId);
    const mobile = await frame.evaluate(() => ({ width: document.documentElement.scrollWidth, viewport: innerWidth, nodeWidth: document.querySelector('.wf-node').getBoundingClientRect().width }));
    assert(mobile.nodeWidth >= 144, 'Initial zoom should keep node labels readable');
    assert(mobile.width <= mobile.viewport, JSON.stringify(mobile));
    await frame.addScriptTag({ path: require.resolve('axe-core/axe.min.js') });
    const violations = await frame.evaluate(async () => (await axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] } })).violations.map(item => ({ id: item.id, nodes: item.nodes.map(node => node.target) })));
    assert.deepEqual(violations, []);
    await page.screenshot({ path: path.join(OUTPUT, 'workflow-mobile.png') });
    pass('390px 移动端没有页面横向溢出，并通过 WCAG 2.2 AA 检查');

    await page.setViewport({ width: 1280, height: 800 });
    const kb = await api('/api/knowledge', 'POST', { name: '知识可用性回归 ' + Date.now(), description: '' });
    assert.equal(kb.status, 201); kbId = kb.body.id;
    await page.goto(BASE + '/knowledge?id=' + kbId, { waitUntil: 'networkidle0' });
    await page.waitForSelector('[data-add-document]');
    const imports = await page.evaluate(async () => {
      const { api } = await import('/assets/platform/api.js');
      const { state } = await import('/assets/platform/ui.js');
      const { renderKnowledge } = await import('/assets/platform/knowledge.js');
      const actualGet = api.get, calls = [];
      api.get = function (path, ...args) { calls.push(path); return actualGet.call(this, path, ...args); };
      state.permissions.delete('files.view');
      try { await renderKnowledge(document.querySelector('[data-add-document]').closest('main')); }
      finally { api.get = actualGet; state.permissions.add('files.view'); }
      return calls;
    });
    assert(!imports.some(route => route.startsWith('/api/files')));
    pass('知识库文档页不再依赖文件库权限或加载文件库列表');
    await page.goto(BASE + '/knowledge?id=' + kbId + '&tab=segments', { waitUntil: 'networkidle0' });
    await page.click('[data-segment-new]');
    await page.select('#platform-dialog [name=segment_type]', 'qa');
    const fields = await page.$$eval('#platform-dialog textarea', elements => elements.map(element => ({ name: element.name, required: element.required, disabled: element.disabled, label: element.closest('label')?.textContent.trim() })));
    assert.deepEqual(fields.map(field => [field.name, field.required, field.disabled]), [['text', false, true], ['question', true, false], ['answer', true, false]]);
    assert(fields.every(field => field.label));
    await page.click('#platform-dialog button[type=submit]');
    assert.equal(await page.$eval('#platform-dialog', element => element.open), true);
    await page.screenshot({ path: path.join(OUTPUT, 'knowledge-validation.png') });
    pass('问答分段正确关联字段标签，只校验当前类型的必填项');
    await page.click('#platform-dialog [data-dialog-close]');
    if (await page.$eval('#platform-dialog .dialog-discard', element => !element.hidden)) await page.click('#platform-dialog [data-discard]');
    await page.goto(BASE + '/knowledge?id=' + kbId + '&tab=settings', { waitUntil: 'networkidle0' });
    await page.click('[data-kb-settings] [name=chunk_size]');
    await change('[data-kb-settings] [name=chunk_size]', '200', page);
    await change('[data-kb-settings] [name=overlap]', '200', page);
    await button('保存', page);
    await page.waitForFunction(() => document.querySelector('#platform-toast').textContent.includes('重叠字符必须小于分段长度'));
    assert.equal(await page.$eval('[data-kb-settings] [name=chunk_size]', element => element.value), '200');
    const stayOnSettings = new Promise(resolve => page.once('dialog', async dialog => { assert.equal(dialog.type(), 'beforeunload'); await dialog.dismiss(); resolve(); }));
    await page.click('.content-tabs a[href*=documents]'); await stayOnSettings;
    assert(page.url().includes('tab=settings'));
    await change('[data-kb-settings] [name=overlap]', '100', page);
    await button('保存', page);
    await page.waitForFunction(() => document.querySelector('#platform-toast').textContent.includes('知识库设置已保存'));
    await page.waitForFunction(() => !document.querySelector('[data-settings-state]')?.textContent);
    const savedSettings = (await api('/api/knowledge/' + kbId + '/settings')).body;
    assert.equal(savedSettings.chunk_size, 200); assert.equal(savedSettings.overlap, 100);
    pass('知识库设置阻止无效重叠、离开时保留修改，并可修正保存');
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(OUTPUT, 'report.json'), JSON.stringify({ checks, errors }, null, 2));
    console.log('Evidence: ' + OUTPUT);
  } catch (error) {
    await page.screenshot({ path: path.join(OUTPUT, 'failure.png') }).catch(() => {});
    console.error('Failed URL: ' + page.url());
    console.error(await page.evaluate(() => document.body.innerText.slice(-1200)).catch(() => ''));
    throw error;
  } finally {
    page.removeAllListeners('dialog');
    page.on('dialog', dialog => dialog.accept());
    if (kbId) await api('/api/knowledge/' + kbId, 'DELETE').catch(() => {});
    if (workflowId) {
      const current = await api('/api/v1/workflows/' + workflowId).catch(() => null);
      if (current?.body?.data?.version) await api(`/api/v1/workflows/${workflowId}?version=${current.body.data.version}`, 'DELETE').catch(() => {});
    }
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
