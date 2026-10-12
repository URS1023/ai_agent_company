/*
 * 中文说明（用户/权限历史/个人资料工作台验收）：
 * 验证行为：紧凑版身份管理页面（用户导入导出、权限变更历史、个人资料）在真实浏览器中正确工作。
 * 运行方式：需隔离服务 + 本机 Edge；夹具文件由 tests/ui/make_identity_fixture.py 生成（child_process 调用）。
 * 边界：会创建/修改用户与角色（隔离环境），导入导出文件为临时产物。
 */
/* Real Edge acceptance for compact user, permission-history and self-profile flows. */
const assert = require('node:assert/strict');
const childProcess = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');

const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011';
const PASSWORD = process.env.TEST5_E2E_PASSWORD;
const EDGE = process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const RUN = Date.now().toString(36);
const USERNAME = 'edge-batch-' + RUN;
const OUTPUT = path.resolve('.implementation/ui-results/identity-' + RUN);
const FIXTURE = path.join(OUTPUT, 'users.xlsx');
const checks = [], errors = [], violations = [];
let browser, page;

function pass(name) { checks.push(name); console.log('PASS ' + name); }
async function visible(selector) {
  return page.$$eval(selector, nodes => nodes.filter(node => node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden' && !node.closest('details:not([open])')).length);
}
async function audit(route) {
  await page.addScriptTag({ path: require.resolve('axe-core/axe.min.js') });
  const findings = await page.evaluate(async () => (await axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] },
  })).violations.map(item => ({ id: item.id, impact: item.impact, nodes: item.nodes.map(node => node.target) })));
  violations.push(...findings.map(item => ({ route, ...item })));
}
async function visit(route, selector = '#main-content > .panel') {
  const response = await page.goto(BASE + route, { waitUntil: 'networkidle0', timeout: 90000 });
  assert.equal(response.status(), 200, route);
  await page.waitForSelector(selector, { timeout: 30000 });
  const layout = await page.evaluate(() => ({
    overflow: document.documentElement.scrollWidth - innerWidth,
    panels: document.querySelectorAll('#main-content > .panel').length,
    internalSidebars: document.querySelectorAll('#main-content aside, #main-content .sidebar').length,
    heroes: document.querySelectorAll('#main-content .overview-intro, #main-content .metric-strip, #main-content .shortcut-grid').length,
    text: document.querySelector('#main-content').innerText,
  }));
  assert(layout.overflow <= 1, route + ' horizontal overflow');
  assert.equal(layout.panels, 1, route + ' should keep one primary panel');
  assert.equal(layout.internalSidebars, 0, route + ' has an internal sidebar');
  assert.equal(layout.heroes, 0, route + ' has decorative hero content');
  assert(!layout.text.includes('undefined') && !layout.text.includes('[object Object]'));
  return layout;
}

(async () => {
  assert(PASSWORD, 'Supply TEST5_E2E_PASSWORD');
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, 'isolated');
  assert(['127.0.0.1', 'localhost'].includes(new URL(BASE).hostname));
  assert(['test', 'ui-validation'].includes((await (await fetch(BASE + '/api/health')).json()).app));
  fs.mkdirSync(OUTPUT, { recursive: true });
  childProcess.execFileSync(path.resolve('.venv_py312/Scripts/python.exe'), [
    path.resolve('tests/ui/make_identity_fixture.py'), FIXTURE, USERNAME,
  ], { stdio: 'pipe' });
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

  await visit('/system/users');
  assert.equal(await visible('.enterprise-sidebar a'), 7);
  assert(await page.$('[data-testid=import-users]'));
  assert(await page.$('[data-testid=export-users]'));
  assert(await page.$('[data-testid=create-user]'));
  await page.click('[data-import-users]');
  await page.waitForSelector('#platform-dialog[open] input[type=file]');
  const templateResponse = page.waitForResponse(response => response.url().endsWith('/api/v1/system/users/import-template'));
  await page.click('[data-download-user-template]');
  const downloadedTemplate = await templateResponse;
  assert.equal(downloadedTemplate.status(), 200);
  assert(downloadedTemplate.headers()['content-type'].includes('spreadsheetml'));
  const fileInput = await page.$('#platform-dialog input[type=file]');
  await fileInput.uploadFile(FIXTURE);
  await page.click('#platform-dialog [type=submit]');
  await page.waitForFunction(() => document.querySelector('#platform-dialog[open] h2')?.textContent.includes('确认用户导入'));
  const importDialogText = await page.$eval('#platform-dialog', node => node.innerText);
  assert(importDialogText.includes('新增') && /(^|\s)1(\s|$)/.test(importDialogText));
  assert(importDialogText.includes(USERNAME));
  assert(!importDialogText.includes('Temporary12345'));
  await page.screenshot({ path: path.join(OUTPUT, 'import-preview.png'), fullPage: true });
  await page.click('#platform-dialog [type=submit]');
  await page.waitForFunction(username => document.querySelector('#main-content')?.innerText.includes(username), {}, USERNAME);
  assert.equal(await page.$eval('#platform-dialog', node => node.open), false);
  pass('XLSX template, full-file preview and atomic import work from the user page');

  const exportResponse = page.waitForResponse(response => response.url().includes('/api/v1/system/users/export?'));
  await page.click('[data-export-users]');
  const exported = await exportResponse;
  assert.equal(exported.status(), 200);
  assert(exported.headers()['content-disposition'].includes('filename*='));
  await audit('/system/users');
  await page.screenshot({ path: path.join(OUTPUT, 'users.png'), fullPage: true });
  pass('Filtered user export is available without adding persistent page clutter');

  await visit('/system/permissions?view=history&q=' + encodeURIComponent(USERNAME));
  assert.equal(await page.$$eval('.system-tool-tabs a', nodes => nodes.length), 3);
  assert((await page.$eval('#main-content', node => node.innerText)).includes(USERNAME));
  assert(await page.$('[data-history-detail]'));
  await page.click('[data-history-detail]');
  await page.waitForSelector('#platform-dialog[open] .history-snapshots');
  const snapshotText = await page.$eval('.history-snapshots', node => node.innerText);
  assert(snapshotText.includes('变更前') && snapshotText.includes('变更后'));
  await page.click('#platform-dialog [data-dialog-close]');
  await audit('/system/permissions?view=history');
  await page.screenshot({ path: path.join(OUTPUT, 'permission-history.png'), fullPage: true });
  pass('Permission history exposes actor, source and before/after snapshots in one compact panel');

  await page.click('.enterprise-account > summary');
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'networkidle0' }),
    page.click('[data-account]'),
  ]);
  assert.equal(new URL(page.url()).pathname, '/account/profile');
  await page.waitForSelector('[data-profile-form]');
  assert.equal(await visible('.enterprise-sidebar'), 0);
  assert.equal(await page.$('#platform-dialog[open]'), null);
  const displayName = '本机验收管理员-' + RUN.slice(-5);
  await page.focus('[name=display_name]');
  await page.keyboard.down('Control');
  await page.keyboard.press('KeyA');
  await page.keyboard.up('Control');
  await page.type('[name=display_name]', displayName);
  await page.click('[data-profile-form] [type=submit]');
  await page.waitForFunction(value => document.querySelector('.enterprise-user-name')?.textContent === value, {}, displayName);
  assert((await page.$eval('#main-content', node => node.innerText)).includes(displayName));
  await audit('/account/profile');
  await page.screenshot({ path: path.join(OUTPUT, 'profile.png'), fullPage: true });
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'networkidle0' }),
    page.click('.system-tool-tabs a[href*="tab=security"]'),
  ]);
  await page.waitForSelector('[data-password-form]');
  assert.equal(await page.$('#platform-dialog[open]'), null);
  pass('Account menu opens a normal full-width profile and security page, not an obstructing dialog');

  await page.setViewport({ width: 390, height: 844, isMobile: true, deviceScaleFactor: 1 });
  for (const route of ['/system/users', '/system/permissions?view=history', '/account/profile']) {
    const layout = await visit(route);
    assert(layout.overflow <= 1);
    assert.equal(await page.$$eval('.enterprise-primary a', nodes => nodes.filter(node => node.getClientRects().length).length), 0);
    await page.screenshot({ path: path.join(OUTPUT, route.replace(/[/?=&]/g, '-') + '-mobile.png'), fullPage: true });
  }
  pass('Users, permission history and profile fit a 390px viewport');

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
  console.error(error.stack || error);
  process.exitCode = 1;
}).finally(async () => { if (browser) await browser.close(); });
