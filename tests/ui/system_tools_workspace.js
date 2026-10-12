/*
 * 中文说明（系统工具页面浏览器验收）：
 * 验证行为：M23（运维/调度等）与 M24（开发者集成等）紧凑页面的展示与操作。
 * 运行方式：需隔离服务与浏览器；会产生调度/工具配置变更（隔离环境）。
 * 边界：断言以页面可见状态与后端返回一致为准。
 */
/* Real-browser acceptance for compact M23/M24 system pages. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');

const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011';
const PASSWORD = process.env.TEST5_E2E_PASSWORD;
const EDGE = process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const OUTPUT = path.resolve('.implementation/ui-results/system-tools-' + Date.now().toString(36));
const errors = [], checks = [], violations = [];
let browser, page;
function pass(name) { checks.push(name); console.log('PASS ' + name); }

async function call(method, pathname, body) {
  return page.evaluate(async ({ method, pathname, body }) => {
    const headers = { Authorization: 'Bearer ' + localStorage.getItem('agent_token'), 'X-Workspace-Id': localStorage.getItem('agent_workspace_id'), Accept: 'application/json' };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const response = await fetch(pathname, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    const content = response.headers.get('content-type')?.includes('json') ? await response.json() : await response.text();
    if (!response.ok) throw new Error(response.status + ' ' + JSON.stringify(content));
    return content;
  }, { method, pathname, body });
}

async function visit(route, selector = '#main-content > .panel') {
  const response = await page.goto(BASE + route, { waitUntil: 'networkidle0', timeout: 90000 });
  assert.equal(response.status(), 200, route);
  await page.waitForSelector(selector, { timeout: 30000 });
  const layout = await page.evaluate(() => ({
    overflow: document.documentElement.scrollWidth - innerWidth,
    sidebarLinks: [...document.querySelectorAll('.enterprise-sidebar a')].filter(item => item.getClientRects().length && !item.closest('details:not([open])')).length,
    internalSidebars: document.querySelectorAll('#main-content aside, #main-content .sidebar').length,
    heroes: document.querySelectorAll('#main-content .overview-intro, #main-content .metric-strip, #main-content .shortcut-grid').length,
    tabs: document.querySelectorAll('.system-tool-tabs a').length,
    panels: document.querySelectorAll('#main-content > .panel').length,
    text: document.querySelector('#main-content').innerText,
  }));
  assert(layout.overflow <= 1, route + ' horizontal overflow');
  assert(layout.sidebarLinks <= 5, route + ' crowded contextual navigation');
  assert.equal(layout.internalSidebars, 0, route + ' has duplicate internal sidebar');
  assert.equal(layout.heroes, 0, route + ' has decorative hero/card wall');
  assert.equal(layout.panels, 1, route + ' should have one primary panel');
  assert(!layout.text.includes('undefined') && !layout.text.includes('[object Object]'));
  return layout;
}

async function audit(route) {
  await page.addScriptTag({ path: require.resolve('axe-core/axe.min.js') });
  const findings = await page.evaluate(async () => (await axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] } })).violations.map(item => ({ id: item.id, impact: item.impact, nodes: item.nodes.map(node => node.target) })));
  violations.push(...findings.map(item => ({ route, ...item })));
}

async function submitDialog() {
  await page.click('#platform-dialog [type=submit]');
  await page.waitForFunction(() => {
    const dialog = document.querySelector('#platform-dialog');
    return !dialog.open || Boolean(dialog.querySelector('.one-time-secret')) || !dialog.querySelector('.form-error').hidden;
  });
  const error = await page.$eval('#platform-dialog', dialog => dialog.open ? dialog.querySelector('.form-error').textContent : '');
  assert.equal(error, '');
}

(async () => {
  assert(PASSWORD, 'Supply TEST5_E2E_PASSWORD');
  fs.mkdirSync(OUTPUT, { recursive: true });
  const health = await (await fetch(BASE + '/api/health')).json();
  assert(['test', 'ui-validation'].includes(health.app));
  browser = await puppeteer.launch({ executablePath: EDGE, headless: true, args: ['--no-first-run'] });
  page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 960 });
  page.on('pageerror', error => errors.push('pageerror ' + error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push('console ' + message.text()); });
  page.on('response', response => { if (response.status() >= 500) errors.push(response.status() + ' ' + response.url()); });
  await page.goto(BASE, { waitUntil: 'networkidle0' });
  await page.type('#login-username', 'workspace-admin');
  await page.type('#login-password', PASSWORD);
  await page.click('#login-btn');
  await page.waitForSelector('.enterprise-area-grid');

  let layout = await visit('/system/operations?tab=health');
  assert.equal(layout.tabs, 5);
  assert.equal(await page.$eval('.enterprise-area-link.active', node => node.dataset.businessArea), 'system');
  assert.equal(await page.$eval('.system-status-strip span:first-child', node => node.innerText.includes('平台')), true);
  assert.equal(await page.$$eval('.system-tools-panel tbody tr', rows => rows.length), 4);
  await audit('/system/operations?tab=health');
  await page.screenshot({ path: path.join(OUTPUT, 'system-operations-health.png'), fullPage: true });
  pass('Health page uses one compact status strip and one dependency table');

  const suffix = Date.now().toString(36);
  await visit('/system/operations?tab=notices');
  await page.click('[data-new-notice]');
  await page.waitForSelector('#platform-dialog[open] [name=title]');
  await page.type('#platform-dialog [name=title]', '浏览器维护公告-' + suffix);
  await page.type('#platform-dialog [name=body]', '用于验证公告投递和消息读取。');
  await page.select('#platform-dialog [name=severity]', 'warning');
  await submitDialog();
  await page.waitForSelector('[data-publish-notice]');
  await page.click('[data-publish-notice]');
  await page.waitForSelector('#platform-dialog[open]');
  await submitDialog();
  await page.waitForFunction(title => document.querySelector('#main-content').innerText.includes(title) && document.querySelector('#main-content').innerText.includes('已发布'), {}, '浏览器维护公告-' + suffix);
  await visit('/system/operations?tab=messages');
  assert((await page.$eval('#main-content', node => node.innerText)).includes('浏览器维护公告-' + suffix));
  assert(await page.$('[data-read-message]'));
  await page.click('[data-read-message]');
  await page.waitForFunction(() => document.querySelector('#main-content').innerText.includes('已读'));
  await page.screenshot({ path: path.join(OUTPUT, 'system-messages.png'), fullPage: true });
  pass('Announcement publishes a real recipient-scoped inbox message');

  await visit('/system/operations?tab=jobs');
  await page.click('[data-new-job]');
  await page.waitForSelector('#platform-dialog[open] [name=code]');
  await page.type('#platform-dialog [name=code]', 'health.browser-' + suffix);
  await page.type('#platform-dialog [name=name]', '浏览器健康巡检-' + suffix);
  await submitDialog();
  await page.waitForSelector('[data-run-job]');
  await page.click('[data-run-job]');
  await page.waitForSelector('#platform-dialog[open]');
  await submitDialog();
  await page.waitForFunction(name => document.querySelector('#main-content').innerText.includes(name), {}, '浏览器健康巡检-' + suffix);
  const jobs = await call('GET', BASE_PATH('/jobs'));
  assert(jobs.items.some(item => item.code === 'health.browser-' + suffix && item.lastRunAt));
  await audit('/system/operations?tab=jobs');
  pass('Health schedule is created and executed from the normal business page');

  await visit('/system/operations?tab=backups');
  await page.click('[data-new-backup]');
  await page.waitForSelector('#platform-dialog[open] [name=name]');
  await page.type('#platform-dialog [name=name]', '浏览器验收备份-' + suffix);
  await submitDialog();
  await page.waitForFunction(name => document.querySelector('#main-content').innerText.includes(name), {}, '浏览器验收备份-' + suffix);
  assert(await page.$('[data-download-backup]'));
  await page.screenshot({ path: path.join(OUTPUT, 'system-backups.png'), fullPage: true });
  pass('Verified database backup is available from a compact table');

  layout = await visit('/system/development?tab=api');
  assert.equal(layout.tabs, 4);
  const metrics = await call('GET', BASE_PATH('/api-directory'));
  assert.equal(metrics.quality.operations, metrics.quality.describedOperations);
  assert.equal(metrics.quality.requestBodies, metrics.quality.requestBodiesWithExamples);
  assert(metrics.quality.operations > 390);
  await page.click('[data-api-detail]');
  await page.waitForSelector('#platform-dialog[open] .json-preview');
  await page.click('#platform-dialog [data-dialog-close]');
  await audit('/system/development?tab=api');
  await page.screenshot({ path: path.join(OUTPUT, 'system-development-api.png'), fullPage: true });
  pass('API page exposes complete descriptions and request/response examples');

  await visit('/system/development?tab=clients');
  await page.click('[data-new-client]');
  await page.waitForSelector('#platform-dialog[open] [name=name]');
  await page.type('#platform-dialog [name=name]', '浏览器只读客户端-' + suffix);
  await page.type('#platform-dialog [name=scopes]', 'system.operations.view');
  await submitDialog();
  await page.waitForSelector('#platform-dialog[open] .one-time-secret');
  const secret = await page.$eval('.one-time-secret code', node => node.textContent);
  assert(secret.startsWith('t5c_'));
  await page.click('#platform-dialog [data-dialog-close]');
  const clients = await call('GET', BASE_PATH('/integration-clients'));
  const created = clients.items.find(item => item.name === '浏览器只读客户端-' + suffix);
  assert(created && !('clientSecret' in created));
  const exchange = await page.evaluate(async ({ clientId, secret, workspaceId }) => {
    const response = await fetch('/api/auth/client-token', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ clientId, clientSecret: secret, workspaceId }) });
    return { status: response.status, body: await response.json() };
  }, { clientId: created.clientId, secret, workspaceId: await page.evaluate(() => localStorage.getItem('agent_workspace_id')) });
  assert.equal(exchange.status, 200);
  const scoped = await page.evaluate(async token => {
    const response = await fetch('/api/v1/system-tools/health', { headers: { Authorization: 'Bearer ' + token } });
    return response.status;
  }, exchange.body.accessToken);
  assert.equal(scoped, 200);
  await page.screenshot({ path: path.join(OUTPUT, 'system-clients.png'), fullPage: true });
  pass('Client secret is shown once and exchanges for a real scoped token');

  await visit('/system/development?tab=migration');
  const sourceData = await call('GET', BASE_PATH('/migration/sources'));
  assert(sourceData.readOnlyPolicy && sourceData.credentialsExposed === false);
  assert(sourceData.schemas.includes('qknow_dev') && sourceData.schemas.includes('lynx_ai_import_20260901'));
  assert(!(await page.$eval('#main-content', node => node.innerText)).includes('MYSQL_PASSWORD'));
  pass('Migration page lists only whitelisted source names and no credentials');

  await page.setViewport({ width: 390, height: 844, isMobile: true, deviceScaleFactor: 1 });
  for (const route of ['/system/operations?tab=health', '/system/operations?tab=jobs', '/system/development?tab=api', '/system/development?tab=clients']) {
    const mobile = await visit(route);
    assert.equal(mobile.overflow <= 1, true);
    assert.equal(await page.$$eval('.enterprise-primary a', nodes => nodes.filter(node => node.getClientRects().length).length), 0);
    await page.screenshot({ path: path.join(OUTPUT, route.replace(/[/?=&]/g, '-') + '-mobile.png'), fullPage: true });
  }
  pass('Operations and development pages fit a 390px viewport without duplicate navigation');

  assert.deepEqual(errors, []);
  const serious = violations.filter(item => ['serious', 'critical'].includes(item.impact));
  assert.deepEqual(serious, []);
  fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({ ok: true, checks, errors, violations }, null, 2));
  console.log('RESULT_DIR ' + OUTPUT);
  console.log('ACCESSIBILITY_FINDINGS ' + violations.length);
})().catch(async error => {
  errors.push(error.stack || String(error));
  if (page && !page.isClosed()) await page.screenshot({ path: path.join(OUTPUT, 'failure.png'), fullPage: true }).catch(() => {});
  if (fs.existsSync(OUTPUT)) fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({ ok: false, checks, errors, violations }, null, 2));
  console.error(error.stack || error); process.exitCode = 1;
}).finally(async () => { if (browser) await browser.close(); });

function BASE_PATH(pathname) { return '/api/v1/system-tools' + pathname; }
