/*
 * 中文说明（企业版外壳只读验收）：
 * 验证行为：统一企业外壳（顶栏/侧栏/上下文条）的几何与导航契约在各页面一致。
 * 运行方式：需隔离服务（TEST5_E2E_BASE_URL，默认 8011）+ 本机 Edge；结果写入
 *   .implementation/ui-results/enterprise-layout-<run>/。
 * 边界：只读断言；tests/ui/business_navigation.js 是它的兼容转发入口。
 */
/* Read-only browser acceptance for the unified Cloud Brain enterprise frame. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');
const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011';
const OUTPUT = path.resolve('.implementation/ui-results/enterprise-layout-' + Date.now().toString(36));
const checks = [], failures = [], violations = [];
let browser, page;
const visible = async selector => page.$$eval(selector, elements => elements.filter(element => element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden' && !element.closest('details:not([open])')).length);
function pass(name, detail = {}) { checks.push({ name, ...detail }); console.log('PASS ' + name); }
async function audit(route) {
  await page.addScriptTag({ path: require.resolve('axe-core/axe.min.js') });
  const report = await page.evaluate(async () => {
    const report = await axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] } });
    return report.violations.map(item => ({ id: item.id, impact: item.impact, nodes: item.nodes.map(node => ({ target: node.target, summary: node.failureSummary })) }));
  });
  violations.push(...report.map(item => ({ route, ...item })));
}
async function visit(route, legacy = false) {
  const response = await page.goto(BASE + route, { waitUntil: 'networkidle0' });
  assert.equal(response.status(), 200, route);
  await page.waitForSelector('.enterprise-area-link.active', { timeout: 25000 });
  if (legacy) await page.waitForSelector('.page-content .panel', { timeout: 25000 });
  else if (route.startsWith('/system/') || ['/files', '/knowledge'].includes(route)) await page.waitForSelector('.platform-content .panel');
  else if (['/analytics', '/datasources', '/dashboards', '/templates', '/prompts', '/workflows', '/alerts', '/quality'].includes(route)) {
    const frame = await (await page.waitForSelector('#business-module-frame')).contentFrame();
    await frame.waitForFunction(() => document.body.innerText.trim().length > 30);
  }
}

(async () => {
  assert(process.env.TEST5_E2E_PASSWORD, 'Supply the isolated test password');
  const health = await (await fetch(BASE + '/api/health')).json();
  if (process.env.TEST5_E2E_ALLOW_NATIVE === '1') assert(['test', 'native-dev'].includes(health.app), 'Unexpected app mode: ' + health.app);
  else assert.equal(health.app, 'test');
  fs.mkdirSync(OUTPUT, { recursive: true });
  browser = await puppeteer.launch({ executablePath: process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 960 });
  page.on('pageerror', error => failures.push(error.message));
  page.on('response', response => { if (response.status() >= 500) failures.push(response.status() + ' ' + response.url()); });
  await page.goto(BASE, { waitUntil: 'networkidle0' });
  await page.type('#login-username', 'workspace-admin'); await page.type('#login-password', process.env.TEST5_E2E_PASSWORD);
  await page.click('#login-btn'); await page.waitForSelector('.enterprise-area-grid');
  assert.equal(await visible('.enterprise-sidebar'), 0, 'Home must not have a module-wall sidebar');
  assert.equal(await visible('.enterprise-area-link'), 6);
  assert.equal(await page.$('#composer-form'), null);
  assert.equal(await page.$eval('.platform-workspace', element => element.getBoundingClientRect().x), 0);
  assert.equal((await page.$eval('.enterprise-brand', element => element.textContent.trim())), '智慧云脑 Cloud Brain');
  for (const path of ['/system/users', '/system/roles', '/system/permissions']) {
    assert(await page.$('.enterprise-area-card-links a[href="' + path + '"]'), 'Identity administration missing from the home shortcuts');
  }
  await page.screenshot({ path: path.join(OUTPUT, 'home-desktop.png') });
  await audit('/');
  pass('Full-width Cloud Brain home with six top-level areas and one AI module');

  await page.keyboard.down('Control'); await page.keyboard.press('KeyK'); await page.keyboard.up('Control');
  await page.waitForSelector('.enterprise-command-dialog[open]');
  await page.type('#module-search', '用户');
  assert.equal(await page.$$eval('.enterprise-search-results a', elements => elements.length), 1);
  await page.keyboard.press('Enter');
  await page.waitForSelector('[data-testid=create-user]');
  assert.equal(new URL(page.url()).pathname, '/system/users');
  assert.equal(await visible('.enterprise-sidebar a'), 7, 'Only the current identity subsection is expanded, including workspace and login security');
  assert.equal(await page.$eval('.enterprise-area-link.active', element => element.dataset.businessArea), 'system');
  await page.screenshot({ path: path.join(OUTPUT, 'users-desktop.png') });
  await audit('/system/users');
  pass('Permission-aware global search opens users with one seven-item identity subsection');

  await page.click('[data-toggle-navigation]');
  assert.equal(await page.$eval('.platform-workspace', element => element.getBoundingClientRect().x), 0);
  await page.click('[data-toggle-navigation]');
  assert.equal(await page.$eval('.platform-workspace', element => element.getBoundingClientRect().x), 210);
  pass('Desktop context menu collapses and returns without losing the page');

  for (const route of ['/system/roles', '/system/workspaces', '/system/departments', '/system/positions', '/system/permissions', '/system/menus', '/system/settings', '/system/sessions', '/system/security', '/system/models', '/system/api', '/datasources', '/analytics', '/dashboards', '/templates', '/prompts', '/workflows', '/alerts', '/quality']) {
    await visit(route);
    const menuCount = await visible('.enterprise-sidebar a');
    assert(menuCount <= 7, route + ' has a crowded sidebar: ' + menuCount);
    const geometry = await page.evaluate(() => ({
      viewport: innerWidth, document: document.documentElement.scrollWidth,
      contentX: document.querySelector('.platform-workspace').getBoundingClientRect().x,
      sidebarWidth: document.querySelector('.enterprise-sidebar').getBoundingClientRect().width,
      topArea: document.querySelector('.enterprise-area-link.active').dataset.businessArea,
    }));
    assert.equal(geometry.sidebarWidth, 210); assert.equal(geometry.contentX, 210);
    assert(geometry.document <= geometry.viewport);
    await audit(route);
    if (['/system/roles', '/analytics', '/dashboards', '/workflows', '/alerts', '/quality'].includes(route)) await page.screenshot({ path: path.join(OUTPUT, route.replaceAll('/', '_') + '-desktop.png') });
    pass('Cloud Brain normal module: ' + route, { geometry, menuCount });
  }
  await page.click('.enterprise-area-link[data-business-area=knowledge]');
  await page.waitForSelector('.platform-content .panel');
  assert.equal(new URL(page.url()).pathname, '/knowledge');
  assert.equal(await page.$eval('.enterprise-area-link.active', element => element.dataset.businessArea), 'knowledge');
  pass('Top-level business switch navigates to its authorized child module');
  for (const route of ['/files', '/knowledge']) {
    await visit(route);
    assert.equal(await page.$('#composer-form'), null, 'Business pages must not include chat');
    assert.equal(await page.$('#pages'), null, 'Canonical content pages must not render the legacy wrapper');
    assert((await visible('.enterprise-sidebar a')) <= 7);
    assert.equal(await page.$eval('.platform-workspace', element => element.getBoundingClientRect().x), 210);
    await audit(route);
    await page.screenshot({ path: path.join(OUTPUT, route.replaceAll('/', '_') + '-desktop.png') });
    pass('Canonical compact content page: ' + route);
  }
  for (const route of ['/runs', '/admin-secrets.html', '/admin-audit.html', '/admin-ops.html']) {
    await visit(route, true);
    assert.equal(await page.$('#composer-form'), null, 'Old business pages must not include chat');
    assert((await visible('.enterprise-sidebar a')) <= 7);
    assert.equal(await page.$eval('#pages', element => element.getBoundingClientRect().x), 210);
    assert.equal(await page.$eval('.enterprise-contextbar', element => element.getBoundingClientRect().y), 60, 'Legacy header must not have duplicate top padding');
    await audit(route);
    pass('Same Cloud Brain frame on retained page: ' + route);
  }
  await page.goto(BASE + '/admin-models.html', { waitUntil: 'networkidle0' });
  await page.waitForSelector('#main-content > .panel');
  assert.equal(new URL(page.url()).pathname, '/system/models');
  pass('Legacy model URL redirects to the canonical governance page');
  for (const route of ['/capabilities', '/connectors', '/skills', '/plugins']) {
    await visit(route);
    await page.waitForSelector('#main-content > .panel');
    assert.equal(await page.$('#pages'), null);
    assert((await visible('.enterprise-sidebar a')) <= 7);
  }
  assert.equal(await page.$$eval('.enterprise-page-tab', elements => elements.length), 0);
  assert((await page.$eval('.enterprise-context-label', element => element.textContent)).includes('Plugins 插件'));
  assert.equal(await page.$$eval('.enterprise-sidebar-footer small', elements => elements.length), 0);
  pass('Compact breadcrumb replaces duplicate page tabs and the workspace footer uses one line');

  await visit('/applications');
  await page.waitForSelector('#main-content > .panel');
  assert.equal(await page.$('.enterprise-pending'), null);
  assert((await visible('.enterprise-sidebar a')) <= 7);
  pass('Unavailable modules are omitted instead of occupying the sidebar with migration placeholders');

  await visit('/ai');
  await page.waitForSelector('#composer-form');
  assert.equal(await page.$eval('.enterprise-area-link.active', element => element.dataset.businessArea), 'ai');
  assert.equal(await page.$eval('.conversation-rail-title strong', element => element.textContent), '对话记录');
  assert.deepEqual(await page.$$eval('#sidebar .side-menu > *', elements => elements.map(element => element.textContent.trim())), ['新聊天']);
  assert.equal(await visible('.enterprise-search-button'), 1);
  assert.equal(await page.$('.enterprise-workspace-name'), null);
  assert.equal(await page.$('.ai-module-sections'), null);
  assert.equal(await page.$('.user-footer'), null);
  assert.equal(await page.$('#ppt-preset'), null);
  assert.equal(await page.$('.enterprise-sidebar'), null);
  assert.equal(await page.$eval('#app', element => element.getBoundingClientRect().height), 960);
  await page.screenshot({ path: path.join(OUTPUT, 'cloud-brain-module-desktop.png') });
  await audit('/ai');
  pass('Cloud Brain chat is an independent AI workbench under the platform header');

  for (const width of [1024, 1280, 1920]) {
    await page.setViewport({ width, height: 960 });
    await visit('/system/users');
    const geometry = await page.evaluate(() => {
      const links = [...document.querySelectorAll('.enterprise-area-link')].map(link => {
        const box = link.getBoundingClientRect();
        const top = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
        return { label: link.textContent, visible: top && link.contains(top) };
      });
      return { content: document.documentElement.scrollWidth, viewport: innerWidth, links };
    });
    assert(geometry.content <= geometry.viewport, 'Desktop viewport overflow: ' + width);
    assert(geometry.links.every(link => link.visible), 'Primary navigation clipped at ' + width + ': ' + JSON.stringify(geometry.links));
    if (width === 1280) await page.screenshot({ path: path.join(OUTPUT, 'users-1280px.png') });
    pass('Top-level domains remain readable and clickable at ' + width + ' px');
  }

  await page.setViewport({ width: 390, height: 844, isMobile: true });
  for (const [route, legacy] of [['/', false], ['/system/users', false], ['/datasources', false], ['/knowledge', false], ['/system/models', false]]) {
    await visit(route, legacy);
    const width = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth }));
    assert(width.document <= width.viewport, route + ' mobile overflow');
    assert.equal(await visible('.enterprise-primary a'), 0);
    assert.equal(await visible('.enterprise-area-select select'), 1);
    if (route !== '/') {
      await page.click('[data-toggle-navigation]');
      await page.waitForFunction(() => document.querySelector('.enterprise-sidebar').getBoundingClientRect().left >= 0);
      assert.equal(await page.$eval('[data-toggle-navigation]', element => element.getAttribute('aria-expanded')), 'true');
      await page.keyboard.press('Escape');
      assert.equal(await page.$eval('[data-toggle-navigation]', element => element.getAttribute('aria-expanded')), 'false');
      assert.equal(await page.$eval('.enterprise-sidebar', element => getComputedStyle(element).visibility), 'hidden');
    }
    await page.screenshot({ path: path.join(OUTPUT, (route === '/' ? 'home' : route.replaceAll('/', '_')) + '-mobile.png') });
    pass('Mobile domain selector, no overflow and accessible contextual drawer: ' + route);
  }
  await page.select('.enterprise-area-select select', '/analytics');
  await page.waitForSelector('#business-module-frame');
  assert.equal(new URL(page.url()).pathname, '/analytics');
  pass('Mobile primary-domain selector performs real navigation');

  assert.deepEqual(failures, []);
  const serious = violations.filter(item => ['critical', 'serious'].includes(item.impact));
  fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({ ok: serious.length === 0, checks, failures, violations }, null, 2));
  console.log('RESULT_DIR ' + OUTPUT);
  console.log('ACCESSIBILITY_FINDINGS ' + violations.length);
  assert.equal(serious.length, 0, 'Serious/critical accessibility findings remain');
})().catch(async error => {
  console.error(error.stack || error);
  if (page && !page.isClosed()) await page.screenshot({ path: path.join(OUTPUT, 'failure.png') }).catch(() => {});
  if (fs.existsSync(OUTPUT)) fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({ ok: false, checks, failures, violations, error: String(error) }, null, 2));
  console.error('RESULT_DIR ' + OUTPUT); process.exitCode = 1;
}).finally(async () => { if (browser) await browser.close(); });
