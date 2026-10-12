/*
 * 中文说明（数据分析平台模块表单与持久化数据验收）：
 * 验证行为：真实模块表单提交后，落库数据与界面展示一致（api() 辅助函数直接在页面内调用后端）。
 * 运行方式：只能对显式隔离的 APP_ENV=test 服务运行（TEST5_E2E_BASE_URL，默认 8011），需本机 Edge。
 * 边界：会产生业务数据（仅限隔离实例）；违规项与失败项分别收集。
 */
/* Real module forms and persisted data, only against an isolated APP_ENV=test server. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');
const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011';
const RUN = Date.now().toString(36);
const OUTPUT = path.resolve('.implementation/ui-results/analytics-' + RUN);
const checks = [], failures = [], violations = [];
let browser, page, frame;
function pass(label) { checks.push(label); console.log('PASS ' + label); }
async function api(url, method = 'GET', body) {
  return page.evaluate(async (url, method, body) => {
    const response = await fetch(url, { method, headers: {
      Authorization: 'Bearer ' + localStorage.getItem('agent_token'),
      'X-Workspace-Id': localStorage.getItem('agent_workspace_id'), 'Content-Type': 'application/json',
    }, body: body === undefined ? undefined : JSON.stringify(body) });
    return { status: response.status, body: await response.json() };
  }, url, method, body);
}
async function button(target, label, root = 'button') {
  for (const handle of await target.$$(root)) {
    if (await handle.evaluate((el, text) => el.getClientRects().length && el.textContent.trim() === text, label)) {
      await handle.click(); return;
    }
  }
  throw new Error('Visible button not found: ' + label);
}
async function field(label, value) {
  const handle = await frame.evaluateHandle(label => {
    const item = [...document.querySelectorAll('.el-dialog .el-form-item')]
      .find(item => item.querySelector('.el-form-item__label')?.textContent.replace('*', '').trim() === label);
    return item?.querySelector('input:not([type=file]),textarea');
  }, label);
  const element = handle.asElement();
  assert(element, 'Field not found: ' + label);
  await element.click({ clickCount: 3 });
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  await element.type(value);
}
async function nativeField(label, value, root) {
  const handle = await frame.evaluateHandle((label, root) => {
    const item = [...document.querySelectorAll(root + ' label')].find(item => item.firstChild?.textContent?.trim() === label);
    return item?.querySelector('input,textarea,select');
  }, label, root);
  const element = handle.asElement();
  assert(element, 'Native field not found: ' + label);
  const tag = await element.evaluate(el => el.tagName);
  if (tag === 'SELECT') { await element.select(value); return; }
  await element.click({ clickCount: 3 });
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  await element.type(value); await element.press('Tab');
}
async function visit(route, selector) {
  const response = await page.goto(BASE + route, { waitUntil: 'networkidle0' });
  assert.equal(response.status(), 200, route);
  await page.waitForSelector('#business-module-frame');
  frame = await (await page.$('#business-module-frame')).contentFrame();
  await frame.waitForSelector(selector, { visible: true, timeout: 25000 });
  const text = await frame.evaluate(() => document.body.innerText);
  assert(!/模块加载失败|模块初始化失败/.test(text), route + ': ' + text.slice(0, 250));
  assert.equal(await page.$('#composer-form'), null, 'Normal modules must not be the AI conversation shell');
}
async function audit(route) {
  await frame.addScriptTag({ path: require.resolve('axe-core/axe.min.js') });
  const result = await frame.evaluate(async () => {
    const result = await axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] } });
    return result.violations.map(item => ({ id: item.id, impact: item.impact, nodes: item.nodes.map(node => ({ target: node.target, summary: node.failureSummary })) }));
  });
  violations.push(...result.map(item => ({ route, ...item })));
}
(async () => {
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, 'isolated');
  assert(process.env.TEST5_E2E_PASSWORD, 'Isolated test password required');
  assert(['127.0.0.1', 'localhost'].includes(new URL(BASE).hostname));
  assert.equal((await (await fetch(BASE + '/api/health')).json()).app, 'test');
  fs.mkdirSync(OUTPUT, { recursive: true });
  browser = await puppeteer.launch({ executablePath: process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  page = await browser.newPage(); await page.setViewport({ width: 1440, height: 960 });
  page.on('pageerror', error => failures.push(error.message));
  page.on('response', response => { if (response.status() >= 500) failures.push(response.status() + ' ' + response.url()); });
  await page.goto(BASE, { waitUntil: 'networkidle0' });
  await page.type('#login-username', 'workspace-admin');
  await page.type('#login-password', process.env.TEST5_E2E_PASSWORD);
  await page.click('#login-btn'); await page.waitForSelector('.overview-intro');
  await page.keyboard.down('Control'); await page.keyboard.press('KeyK'); await page.keyboard.up('Control');
  await page.waitForSelector('.enterprise-command-dialog[open]');
  for (const route of ['/analytics', '/dashboards', '/templates', '/prompts', '/system/users', '/system/roles', '/ai']) {
    assert(await page.$('.enterprise-search-results a[href="' + route + '"]'), 'Authorized module missing: ' + route);
  }
  await page.keyboard.press('Escape');
  pass('Business navigation exposes separate analytics, dashboards, templates, prompts, users, roles and AI');

  for (const [route, selector] of [
    ['/datasources', '.datasource-workspace'], ['/analytics', '.intelligent-query'],
    ['/analytics/sql', '.sql-workspace'], ['/analytics/history', '.business-panel'],
    ['/analytics/quality', '.business-panel'], ['/dashboards', '.datasource-manage'],
    ['/templates', '.template-grid'], ['/prompts', '.prompt-layout'],
  ]) {
    await visit(route, selector); await audit(route);
    await page.screenshot({ path: path.join(OUTPUT, route.replaceAll('/', '_') + '.png') });
    pass('Direct URL and independent business interface: ' + route);
  }
  const initialTemplates = (await api('/api/v1/report-templates')).body.data;
  assert.equal(initialTemplates.length, 33);
  assert(initialTemplates.every(item => item.sourceKind === 'lynx-report-screen' && item.styleLocked));
  assert(initialTemplates.every(item => new RegExp(`/report-thumbnails/lynx-${item.sourceRecordId}\\.png$`).test(item.preview)));
  pass('All 33 real Lynx dashboards are exposed as visually locked templates with real screenshots');

  await visit('/templates', '.template-grid');
  assert.equal(await frame.$$eval('[data-template-id]', items => items.length), 33);
  assert.equal(await frame.$$eval('.template-image > img', items => items.length), 33);
  assert((await frame.evaluate(() => document.body.innerText)).includes('缩略图来自真实报表截图'));
  pass('Template center renders every real dashboard screenshot as a selectable template card');

  const templateFixture = (await api('/api/v1/report-templates', 'POST', {
    name: '浏览器自定义模板 ' + RUN,
    description: '隔离验收创建的单组件模板',
    category: '验收',
    tags: ['自定义'],
    content: {
      canvas: { width: 1000, height: 700, backgroundColor: '#102238' },
      widgets: [{ id: 'fixture-title', component: 'JText', componentName: '验收标题', x: 80, y: 70, w: 520, h: 80, config: { dataType: 1, option: { body: { text: '验收标题' } } } }],
    },
  })).body.data;

  await visit('/datasources', '.datasource-workspace');
  await button(frame, '从 CSV / Excel 导入');
  await frame.waitForSelector('.el-dialog input[type=file]', { visible: true });
  const csv = path.join(OUTPUT, 'finance-fixture.csv');
  fs.writeFileSync(csv, '\uFEFFdepartment,amount,status\n设备,125.50,正常\n财务,240.25,正常\n设备,0,预警\n', 'utf8');
  await (await frame.$('.el-dialog input[type=file]')).uploadFile(csv);
  await field('数据源名称', '浏览器数据 ' + RUN);
  await field('数据表名称', 'browser_finance');
  const uploaded = page.waitForResponse(response => response.url().endsWith('/api/v1/analytics/datasources/import') && response.request().method() === 'POST');
  await button(frame, '导入');
  assert.equal((await uploaded).status(), 201);
  await frame.waitForSelector('.el-dialog', { hidden: true });
  const source = (await api('/api/v1/analytics/datasources')).body.data.find(item => item.name === '浏览器数据 ' + RUN);
  assert(source);
  pass('CSV import form created an actual isolated database with original decimal and zero values');

  await visit('/analytics?datasourceId=' + source.id, '.intelligent-query');
  await frame.waitForFunction(() => document.querySelector('.connection-status')?.textContent.includes('表结构已加载'));
  assert.equal(await frame.$eval('.intelligent-query', element => getComputedStyle(element).backgroundImage), 'none');
  await frame.click('.query-metadata summary');
  assert((await frame.$eval('.query-metadata', element => element.textContent)).includes('browser_finance'));
  assert((await frame.$eval('.query-metadata', element => element.textContent)).includes('amount'));
  await frame.click('#query-mode');
  await frame.waitForFunction(() => [...document.querySelectorAll('.el-select-dropdown__item')].some(element => element.getClientRects().length && element.textContent.trim() === '手工 SQL 查询'));
  await frame.evaluate(() => [...document.querySelectorAll('.el-select-dropdown__item')].find(element => element.getClientRects().length && element.textContent.trim() === '手工 SQL 查询').click());
  await button(frame, '查看 browser_finance 数据');
  const smartQuery = page.waitForResponse(response => response.url().endsWith('/api/v1/analytics/query') && response.request().method() === 'POST');
  await button(frame, '执行查询');
  const smartResponse = await smartQuery;
  assert.equal(smartResponse.status(), 200);
  assert.equal((await smartResponse.json()).data.totalCount, 3);
  await frame.waitForSelector('.query-result-panel .el-table__row');
  await button(frame, 'SQL 与来源', '[role=tab]');
  assert((await frame.$eval('.sql-preview', element => element.textContent)).includes('browser_finance'));
  assert(await frame.$('.result-source-actions button'));
  await button(frame, '数据结果', '[role=tab]');
  await page.screenshot({ path: path.join(OUTPUT, 'enterprise-query-real-result.png') });
  await audit('/analytics real SQL result');
  pass('Enterprise query page uses current source metadata and manual SQL with real results, source details and reusable result tabs');

  const sql = 'SELECT department, CAST(amount AS REAL) AS amount, status FROM browser_finance';
  await visit('/analytics/sql?datasourceId=' + source.id + '&sql=' + encodeURIComponent(sql), '.sql-workspace');
  const query = page.waitForResponse(response => response.url().endsWith('/api/v1/analytics/query') && response.request().method() === 'POST');
  await button(frame, '执行查询');
  const queryResponse = await query; assert.equal(queryResponse.status(), 200);
  const result = (await queryResponse.json()).data;
  assert.equal(result.totalCount, 3);
  assert.equal(result.statistics.columns.amount.sum, '365.75');
  assert.equal(result.data[2].amount, 0);
  await frame.waitForSelector('.query-result-panel .el-table__row');
  assert((await frame.evaluate(() => document.body.innerText)).includes('125.5'));
  await button(frame, '统计与洞察', '[role=tab]');
  await button(frame, '可视化看板', '[role=tab]');
  await frame.waitForSelector('.data-analysis-dashboard');
  await page.screenshot({ path: path.join(OUTPUT, 'real-query-dashboard.png') });
  await button(frame, '推荐图表', '[role=tab]');
  await frame.waitForSelector('canvas');
  pass('SQL page produced actual rows, exact decimal statistics, chart and analysis dashboard');
  await visit('/analytics/history', '.business-panel');
  assert((await frame.evaluate(() => document.body.innerText)).includes('browser_finance'));
  pass('Query history survives navigation and does not require an AI conversation');

  await visit('/dashboards', '.datasource-manage'); await button(frame, '新增报表');
  await frame.waitForSelector('.report-create-dialog', { visible: true });
  await frame.click('[role=radio]:nth-child(2)');
  await frame.waitForSelector('.report-template-card');
  assert.equal(await frame.$$eval('.report-template-card.selected', items => items.length), 0);
  await frame.click(`[data-template-id="${templateFixture.id}"]`);
  await field('报表名称', '界面模板回归 ' + RUN);
  const creation = page.waitForResponse(response => response.url().endsWith('/api/v1/reports') && response.request().method() === 'POST');
  await button(frame, '确定');
  const createdResponse = await creation; assert.equal(createdResponse.status(), 201);
  const created = (await createdResponse.json()).data;
  await frame.waitForSelector('.jimu-toolbar', { visible: true });
  const report = (await api('/api/v1/reports/' + created.id)).body.data;
  assert.equal(report.templateMode, 'manual');
  assert.equal(report.templateId, templateFixture.id);
  const before = JSON.parse(report.content);
  assert.equal(before.widgets.length, templateFixture.content.widgets.length);
  assert.equal(await frame.$$eval('.screen-widget', items => items.length), before.widgets.length);
  await page.screenshot({ path: path.join(OUTPUT, 'full-template-designer.png') });
  pass('Full designer loaded with ' + before.widgets.length + ' editable custom-template components');
  await frame.waitForFunction(() => {
    const save = document.querySelector('button[title="保存"]'), rect = save?.getBoundingClientRect();
    return rect && save.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2));
  });
  const saved = page.waitForResponse(response => response.url().endsWith('/api/v1/reports/' + created.id) && response.request().method() === 'PUT');
  await frame.click('button[title="保存"]');
  const savedResponse = await saved;
  const savedBody = await savedResponse.text();
  if (savedResponse.status() !== 200) {
    fs.writeFileSync(path.join(OUTPUT, 'failed-save-payload.json'), savedResponse.request().postData() || '');
  }
  assert.equal(savedResponse.status(), 200, savedBody);
  const after = (await api('/api/v1/reports/' + created.id)).body.data;
  assert(after.version > report.version);
  assert.equal(JSON.parse(after.content).widgets.length, before.widgets.length);
  await visit('/report/designer/' + created.id, '.jimu-toolbar');
  assert.equal(await frame.$$eval('.screen-widget', items => items.length), before.widgets.length);
  await visit('/report/view/' + created.id, '.viewer-tools');
  assert.equal(await frame.$$eval('.screen-widget', items => items.length), before.widgets.length);
  await button(frame, '刷新数据');
  pass('Explicit custom template preserved all components across actual form creation, editor save, reload and viewer');

  const bound = (await api('/api/v1/reports', 'POST', {
    reportName: '组件绑定回归 ' + RUN, templateMode: 'none', refreshSeconds: 0,
    content: { canvas: { width: 1000, height: 700, backgroundColor: '#102238' }, widgets: [{
      id: 'bound-table', component: 'JScrollBoard', componentName: '设备与财务记录',
      x: 80, y: 100, w: 800, h: 400, config: { dataType: 1, chartData: '[]', option: { rowNum: 8, waitTime: 100000 } },
    }] },
  })).body.data;
  await visit('/report/designer/' + bound.id, '.jimu-toolbar');
  await frame.click('.screen-widget');
  await frame.click('.config-tabs button[title="数据"]');
  await button(frame, '数据库');
  await frame.waitForSelector('.widget-binding select option[value="' + source.id + '"]');
  await nativeField('数据源', source.id, '.widget-binding');
  await frame.waitForSelector('.widget-binding select option[value="browser_finance"]');
  await nativeField('数据表', 'browser_finance', '.widget-binding');
  await frame.waitForSelector('.field-choice input[value=department]');
  await frame.click('.field-choice input[value=department]');
  await frame.click('.field-choice input[value=amount]');
  await button(frame, '生成只读 SQL');
  await button(frame, '试运行并更新组件数据');
  await frame.waitForFunction(() => document.querySelector('.widget-binding')?.innerText.includes('已返回 3 行'));
  await frame.click('.config-tabs button[title="联动"]');
  await nativeField('钻取标题', '设备明细', '.link-settings');
  await nativeField('取值字段', 'department', '.link-settings');
  await nativeField('只读明细 SQL', 'SELECT department, amount FROM browser_finance WHERE department=:point', '.link-settings');
  const bindingSaved = page.waitForResponse(response => response.url().endsWith('/api/v1/reports/' + bound.id) && response.request().method() === 'PUT');
  await frame.click('button[title="保存"]');
  assert.equal((await bindingSaved).status(), 200);
  await visit('/report/view/' + bound.id, '.viewer-tools');
  await frame.waitForSelector('.scroll-row', { visible: true });
  assert((await frame.evaluate(() => document.body.innerText)).includes('125.50'));
  const drill = page.waitForResponse(response => response.url().endsWith('/api/v1/reports/drilldown'));
  await frame.click('.scroll-row');
  assert.equal((await drill).status(), 200);
  await frame.waitForFunction(() => document.querySelector('.drilldown-cockpit')?.innerText.includes('查询完成'));
  assert.equal(await frame.$$eval('.drilldown-cockpit .table-row', rows => rows.length), 2);
  await page.screenshot({ path: path.join(OUTPUT, 'real-saved-drilldown.png') });
  await frame.click('[aria-label="关闭数据明细"]');
  pass('Database source, table, field selection, preview query and saved drilldown all operated through editor controls');

  const role = await api('/api/v1/system/roles', 'POST', {
    code: 'screen-reader-' + RUN, name: '大屏只读 ' + RUN,
    permissions: ['home.view', 'dashboards.view', 'datasources.view'], data_scope: 'workspace',
  });
  assert.equal(role.status, 201);
  const username = 'screen-reader-' + RUN, temporary = 'ReaderTemp2026#' + RUN, personal = 'ReaderChanged2026#' + RUN;
  assert.equal((await api('/api/v1/system/users', 'POST', {
    username, display_name: '大屏只读验证用户', password: temporary, role_ids: [role.body.id],
  })).status, 201);
  const adminPage = page;
  const readerContext = await browser.createBrowserContext();
  page = await readerContext.newPage(); await page.setViewport({ width: 1440, height: 960 });
  page.on('pageerror', error => failures.push('reader: ' + error.message));
  await page.goto(BASE, { waitUntil: 'networkidle0' });
  await page.type('#login-username', username); await page.type('#login-password', temporary);
  await page.click('#login-btn'); await page.waitForSelector('#required-password-form');
  await page.type('[name=current_password]', temporary); await page.type('[name=new_password]', personal); await page.type('[name=confirm_password]', personal);
  await page.click('#required-password-form [type=submit]');
  await page.waitForSelector('#platform-login-form');
  await page.type('#login-username', username); await page.type('#login-password', personal); await page.click('#login-btn');
  await page.waitForSelector('.overview-intro');
  await page.click('[data-global-search]');
  assert.equal(await page.$('.enterprise-search-results a[href="/analytics"]'), null);
  await page.keyboard.press('Escape');
  await visit('/report/view/' + bound.id, '.viewer-tools');
  await frame.waitForSelector('.scroll-row');
  assert.equal(await frame.$$eval('.viewer-tools button', items => items.filter(item => item.textContent === '编辑').length), 0);
  const readerDrill = page.waitForResponse(response => response.url().endsWith('/api/v1/reports/drilldown'));
  await frame.click('.scroll-row'); assert.equal((await readerDrill).status(), 200);
  await frame.waitForFunction(() => document.querySelector('.drilldown-cockpit')?.innerText.includes('查询完成'));
  assert.equal(await frame.$$eval('.header-actions button', items => items.filter(item => item.textContent.includes('导出')).length), 0);
  assert.equal((await api('/api/v1/reports/query', 'POST', { datasourceId: source.id, query: sql, mode: 'sql' })).status, 403);
  assert.equal((await api('/api/v1/reports/' + bound.id, 'PUT', { reportName: '不允许修改', version: 1 })).status, 403);
  await readerContext.close(); page = adminPage;
  pass('Real readonly login can refresh and drill saved reports but cannot edit, export or execute arbitrary SQL');

  await visit('/report/designer/not-found', '.editor-load-error');
  assert((await frame.evaluate(() => document.body.innerText)).includes('无法打开报表'));
  pass('Missing report shows a persistent recoverable error, not a blank usable-looking editor');
  await page.setViewport({ width: 390, height: 844 });
  for (const [route, selector] of [
    ['/datasources', '.datasource-workspace'], ['/analytics', '.intelligent-query'],
    ['/analytics/sql', '.sql-workspace'], ['/analytics/history', '.business-panel'],
    ['/analytics/quality', '.business-panel'], ['/dashboards', '.datasource-manage'],
    ['/templates', '.template-grid'], ['/prompts', '.prompt-layout'],
  ]) {
    await visit(route, selector);
    const size = await frame.evaluate(() => ({ content: document.documentElement.scrollWidth, viewport: innerWidth }));
    assert(size.content <= size.viewport + 1, route + ' overflows its mobile module viewport: ' + JSON.stringify(size));
    await page.screenshot({ path: path.join(OUTPUT, route.replaceAll('/', '_') + '-mobile.png') });
    pass('Embedded business page fits mobile width: ' + route);
  }
  const unknown = await page.goto(BASE + '/analytics/not-a-page');
  assert.equal(unknown.status(), 404);
  pass('Unknown business URL returns real 404');
  assert.deepEqual(failures, []);
  assert.equal(violations.filter(item => ['serious', 'critical'].includes(item.impact)).length, 0, 'Automated accessibility findings remain');
  fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({ ok: true, checks, failures, violations, scope: 'manual SQLite module workflows; no live model/provider certification' }, null, 2));
  console.log('RESULT_DIR ' + OUTPUT);
  console.log('ACCESSIBILITY_FINDINGS ' + violations.length);
})().catch(async error => {
  console.error(error.stack || error);
  if (page && !page.isClosed()) await page.screenshot({ path: path.join(OUTPUT, 'failure.png') }).catch(() => {});
  if (fs.existsSync(OUTPUT)) fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({ ok: false, checks, failures, violations, error: String(error) }, null, 2));
  console.error('RESULT_DIR ' + OUTPUT);
  process.exitCode = 1;
}).finally(async () => { if (browser) await browser.close(); });
