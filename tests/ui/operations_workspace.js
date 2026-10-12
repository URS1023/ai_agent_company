/*
 * 中文说明（运维工作台的真实浏览器验收）：
 * 验证行为：紧凑版 Qknow 运维工作台（服务状态、卡住任务处理等）在真实浏览器中按后端数据展示并可操作。
 * 运行方式：需隔离服务（TEST5_E2E_BASE_URL，默认 8011）+ 本机 Edge + TEST5_E2E_PASSWORD。
 * 边界：清理类操作会写库（仅隔离环境）。
 */
/* Real Edge acceptance for the compact Qknow operations workspace. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');

const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011';
const PASSWORD = process.env.TEST5_E2E_PASSWORD;
const EDGE = process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const OUTPUT = path.resolve('.implementation/ui-results/operations-' + Date.now().toString(36));
const checks = [], failures = [], violations = [];
let browser, page;

function pass(name, detail = {}) {
  checks.push({ name, ...detail });
  console.log('PASS ' + name);
}

async function call(method, pathname, body) {
  return page.evaluate(async ({ method, pathname, body }) => {
    const headers = {
      Authorization: 'Bearer ' + localStorage.getItem('agent_token'),
      'X-Workspace-Id': localStorage.getItem('agent_workspace_id'),
      Accept: 'application/json',
    };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const response = await fetch(pathname, {
      method, headers, body: body === undefined ? undefined : JSON.stringify(body),
    });
    const value = await response.json();
    if (!response.ok) throw new Error(response.status + ' ' + JSON.stringify(value));
    return value.success === true ? value.data : value;
  }, { method, pathname, body });
}

async function seedRun() {
  const suffix = Date.now().toString(36);
  const plugin = await call('POST', '/api/plugins', {
    name: '运维验收入口-' + suffix,
    description: '生成一条可追踪的正式应用运行。',
    spec: {
      name: '运维验收入口-' + suffix,
      description: '本机运维验收入口。', instructions: '打开正式站内入口。',
      skill_refs: [], tool_refs: [], resource_bindings: [],
      input_schema: { type: 'object', properties: {}, additionalProperties: false },
      output_schema: { type: 'object', properties: {} },
    },
  });
  await call('POST', `/api/plugins/${plugin.plugin_id}/publish`, {});
  const application = await call('POST', '/api/v1/applications', {
    code: 'operations-' + suffix,
    name: '运维验收应用-' + suffix,
    description: '用于验证运行链和审计链。', kind: 'automation', visibility: 'workspace',
    spec: {
      welcomeMessage: '', instructions: '',
      bindings: [{ type: 'plugin', id: plugin.plugin_id, role: 'entry', purpose: '正式入口', required: true }],
      audience: { mode: 'workspace', roleIds: [], userIds: [] },
      inputSchema: { type: 'object', properties: {}, additionalProperties: false },
      outputSchema: { type: 'object', properties: {} },
    },
  });
  const published = await call('POST', `/api/v1/applications/${application.id}/publish`, {
    version: application.recordVersion, releaseVersion: 1,
  });
  const launched = await call('POST', `/api/v1/applications/${application.id}/launch`, {
    channel: 'api', input: {},
  });
  return { suffix, pluginId: plugin.plugin_id, applicationId: application.id, runId: launched.runId, published };
}

async function audit(route) {
  await page.addScriptTag({ path: require.resolve('axe-core/axe.min.js') });
  const report = await page.evaluate(async () => (await axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] },
  })).violations.map(item => ({
    id: item.id, impact: item.impact,
    nodes: item.nodes.map(node => ({ target: node.target, summary: node.failureSummary })),
  })));
  violations.push(...report.map(item => ({ route, ...item })));
}

async function visit(route, selector = '#main-content > .panel') {
  const response = await page.goto(BASE + route, { waitUntil: 'networkidle0', timeout: 90000 });
  assert.equal(response.status(), 200, route);
  await page.waitForSelector(selector, { timeout: 30000 });
  const layout = await page.evaluate(() => ({
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    panels: document.querySelectorAll('#main-content > .panel').length,
    iframe: Boolean(document.querySelector('#business-module-frame')),
    hero: Boolean(document.querySelector('#main-content .overview-intro, #main-content .metric-strip')),
    summaryCards: document.querySelectorAll('#main-content .overview-card, #main-content .resource-card, #main-content .run-summary-strip > div').length,
    duplicateTabs: document.querySelectorAll('#main-content .catalog-tabs, .enterprise-page-tab').length,
    sidebarLinks: [...document.querySelectorAll('.enterprise-sidebar a')].filter(node => node.getClientRects().length).length,
    bodyText: document.querySelector('#main-content').innerText,
  }));
  assert(layout.overflow <= 1, route + ' horizontal overflow');
  assert.equal(layout.panels, 1, route + ' must keep one primary panel');
  assert.equal(layout.iframe, false);
  assert.equal(layout.hero, false);
  assert.equal(layout.summaryCards, 0);
  assert.equal(layout.duplicateTabs, 0);
  assert(layout.sidebarLinks <= 7);
  assert(!layout.bodyText.includes('undefined'));
  assert(!layout.bodyText.includes('[object Object]'));
  await audit(route);
  return layout;
}

(async () => {
  assert(PASSWORD, 'Supply TEST5_E2E_PASSWORD');
  const health = await (await fetch(BASE + '/api/health')).json();
  assert(['test', 'ui-validation'].includes(health.app));
  fs.mkdirSync(OUTPUT, { recursive: true });
  browser = await puppeteer.launch({ executablePath: EDGE, headless: true });
  page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 960 });
  page.on('pageerror', error => failures.push('pageerror ' + error.message));
  page.on('console', message => { if (message.type() === 'error') failures.push('console ' + message.text()); });
  page.on('response', response => { if (response.status() >= 500) failures.push(response.status() + ' ' + response.url()); });

  await page.goto(BASE, { waitUntil: 'networkidle0' });
  await page.type('#login-username', 'workspace-admin');
  await page.type('#login-password', PASSWORD);
  await page.click('#login-btn');
  await page.waitForSelector('.enterprise-area-grid');
  const seed = await seedRun();

  await visit(`/operations?keyword=${encodeURIComponent(seed.runId)}`, '.operations-panel');
  assert.equal(await page.$$eval('.operations-panel tbody tr', rows => rows.length), 1);
  assert((await page.$eval('.operations-statusbar', node => node.innerText)).includes('服务与数据库正常'));
  assert.equal(await page.$$eval('.operations-switch a', links => links.length), 3);
  assert.equal(await page.$eval('#main-content h1', node => node.textContent.trim()), '业务运行中心');
  assert.equal(await page.$eval('.enterprise-area-link.active', node => node.dataset.businessArea), 'business');
  assert(await page.$('.enterprise-sidebar a[href="/operations"]'));
  assert.equal(await page.$eval('.enterprise-sidebar a[href="/operations"]', node => node.textContent.trim()), '运行记录');
  assert.equal(await page.$('.enterprise-sidebar a[href="/runs"]'), null);
  assert.equal(await page.$('.enterprise-sidebar a[href="/admin-ops.html"]'), null);
  assert.equal(await page.$('.enterprise-pending'), null);
  await page.screenshot({ path: path.join(OUTPUT, 'operations-runs.png'), fullPage: true });
  pass('Run workspace is one compact table with inline health and no legacy menu duplication');

  await visit(`/operations?id=${seed.runId}`, '.operations-detail');
  assert.equal(await page.$$eval('.operations-detail > .table-scroll tbody tr', rows => rows.length), 1);
  assert(await page.$$eval('.operations-detail > details', nodes => nodes.every(node => !node.open)));
  assert((await page.$eval('#main-content h1', node => node.innerText)).includes('应用启动'));
  assert.equal(await page.$('.operations-detail a[href^="/ai?cid="]'), null);
  await page.click('[data-run-context]');
  await page.waitForSelector('#platform-dialog[open] .operations-json-dialog');
  assert((await page.$eval('#platform-dialog', node => node.innerText)).includes(seed.applicationId));
  await audit('/operations-context-dialog');
  await page.screenshot({ path: path.join(OUTPUT, 'operations-run-detail.png'), fullPage: true });
  await page.click('#platform-dialog [data-dialog-close]');
  pass('Run detail keeps one step table and reveals authorized context only on demand');

  await visit(`/operations?view=audit&keyword=${encodeURIComponent(seed.applicationId)}`, '.operations-panel');
  const auditRows = await page.$$eval('.operations-panel tbody tr', rows => rows.length);
  assert(auditRows >= 3, 'Expected create, publish and launch audit rows');
  assert.equal(await page.$eval('.operations-switch a.active', node => node.textContent.trim()), '审计日志');
  await page.click('[data-audit-json]');
  await page.waitForSelector('#platform-dialog[open] .operations-json-dialog');
  assert((await page.$eval('#platform-dialog', node => node.innerText)).includes(seed.applicationId));
  await audit('/operations-audit-dialog');
  await page.screenshot({ path: path.join(OUTPUT, 'operations-audit.png'), fullPage: true });
  await page.click('#platform-dialog [data-dialog-close]');
  pass('Audit review uses the same workspace and folds metadata into a focused dialog');

  await visit(`/operations?view=business&domain=applications&objectId=${seed.applicationId}&days=30`, '.business-operations-panel');
  assert.equal(await page.$eval('.operations-switch a.active', node => node.textContent.trim()), '业务运营');
  assert.equal(await page.$$eval('.operations-business-metrics > div', nodes => nodes.length), 4);
  assert.equal(await page.$$eval('.operations-business-toolbar nav a', nodes => nodes.length), 3);
  assert.equal(await page.$eval('.operations-business-toolbar nav a[aria-current="page"]', node => node.textContent.trim()), '应用');
  assert.equal(await page.$eval('[name=objectId]', node => node.value), seed.applicationId);
  assert(await page.$('.business-trend svg[role="img"]'));
  assert.equal(await page.$eval('.business-chart-data', node => node.open), false);
  const completeTrend = await call('GET', `/api/v1/operations/business?domain=applications&objectId=${seed.applicationId}&days=30`);
  assert.equal(completeTrend.trend.categories.length, 30);
  assert(completeTrend.trend.series.every(series => series.data.length === 30));
  assert(completeTrend.topItems.some(item => item.id === seed.applicationId));
  await page.screenshot({ path: path.join(OUTPUT, 'operations-business.png'), fullPage: true });
  pass('Business operations uses real application records and complete 30-day chart series');

  const summary = await call('GET', '/api/v1/operations/summary');
  assert.equal(summary.service.database, 'healthy');
  const detail = await call('GET', `/api/v1/operations/runs/${seed.runId}`);
  assert.equal(detail.steps.length, 1);
  assert.equal(detail.context.application.applicationId, seed.applicationId);
  assert.equal(detail.conversationId, '');
  assert.equal(detail.conversationPath, '');
  pass('Operations APIs return real scoped run and health data');

  await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 1, isMobile: true });
  await visit(`/operations?keyword=${encodeURIComponent(seed.runId)}`, '.operations-panel');
  await visit(`/operations?view=business&domain=applications&objectId=${seed.applicationId}&days=7`, '.business-operations-panel');
  assert.equal(await page.$$eval('.operations-business-metrics > div', nodes => nodes.length), 4);
  assert.equal(await page.$eval('.business-chart-data summary', node => node.textContent.includes('7 天')), true);
  await page.screenshot({ path: path.join(OUTPUT, 'operations-business-mobile.png'), fullPage: true });
  await visit(`/operations?id=${seed.runId}`, '.operations-detail');
  await page.click('[data-run-context]');
  await page.waitForSelector('#platform-dialog[open]');
  const mobile = await page.evaluate(() => {
    const dialog = document.querySelector('#platform-dialog').getBoundingClientRect();
    return {
      overflow: document.documentElement.scrollWidth - innerWidth,
      left: dialog.left, right: dialog.right,
      primaryLinks: [...document.querySelectorAll('.enterprise-primary a')].filter(node => node.getClientRects().length).length,
    };
  });
  assert(mobile.overflow <= 1 && mobile.left >= 0 && mobile.right <= 390, JSON.stringify(mobile));
  assert.equal(mobile.primaryLinks, 0);
  await audit('/operations-mobile-context');
  await page.screenshot({ path: path.join(OUTPUT, 'operations-mobile.png'), fullPage: true });
  pass('Run list, detail and trace dialog fit a 390px viewport');

  assert.deepEqual(failures, []);
  assert.deepEqual(violations, []);
  fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({
    checks, failures, accessibilityFindings: violations,
    records: { applicationId: seed.applicationId, pluginId: seed.pluginId, runId: seed.runId },
  }, null, 2));
  console.log('RESULT_DIR ' + OUTPUT);
  console.log('ACCESSIBILITY_FINDINGS ' + violations.length);
})().catch(error => {
  failures.push(error.stack || error.message);
  if (fs.existsSync(OUTPUT)) {
    fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({ checks, failures, accessibilityFindings: violations }, null, 2));
  }
  console.error(error);
  process.exitCode = 1;
}).finally(async () => { if (browser) await browser.close(); });
