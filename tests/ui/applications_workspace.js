/*
 * 中文说明（应用工作台真实浏览器验收）：
 * 验证行为：紧凑版应用（Qknow 风格）工作台的列表、发布与运行入口按权限与状态正确展示。
 * 运行方式：需隔离服务（TEST5_E2E_BASE_URL，默认 8011）+ 本机 Edge + TEST5_E2E_PASSWORD，
 *   结果写入 .implementation/ui-results/applications-<run>/。
 * 边界：需要隔离库可写（会创建/发布应用资源）。
 */
/* Real Edge acceptance for the compact Qknow application workspace. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');

const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011';
const PASSWORD = process.env.TEST5_E2E_PASSWORD;
const EDGE = process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const OUTPUT = path.resolve('.implementation/ui-results/applications-' + Date.now().toString(36));
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
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const value = await response.json();
    if (!response.ok) throw new Error(response.status + ' ' + JSON.stringify(value));
    return value.success === true ? value.data : value;
  }, { method, pathname, body });
}

async function seedPlugin() {
  const suffix = Date.now().toString(36);
  const plugin = await call('POST', '/api/plugins', {
    name: '设备预警入口-' + suffix,
    description: '应用中心浏览器验收使用的正式业务入口。',
    spec: {
      name: '设备预警入口-' + suffix,
      description: '查询设备预警并进入 AI 处置。',
      instructions: '读取已生成预警，不重复请求设备。',
      skill_refs: [], tool_refs: [], resource_bindings: [],
      input_schema: { type: 'object', properties: {}, additionalProperties: false },
      output_schema: { type: 'object', properties: {} },
    },
  });
  await call('POST', `/api/plugins/${plugin.plugin_id}/publish`, {});
  return { suffix, pluginId: plugin.plugin_id };
}

async function audit(route) {
  await page.addScriptTag({ path: require.resolve('axe-core/axe.min.js') });
  const report = await page.evaluate(async () => (await axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] },
  })).violations.map(item => ({
    id: item.id,
    impact: item.impact,
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
    tabs: document.querySelectorAll('#main-content .catalog-tabs, .enterprise-page-tab').length,
    iframe: Boolean(document.querySelector('#business-module-frame')),
    hero: Boolean(document.querySelector('#main-content .overview-intro, #main-content .metric-strip')),
    summaryStrip: Boolean(document.querySelector('#main-content .resource-summary')),
    cards: document.querySelectorAll('#main-content .overview-card, #main-content .resource-card').length,
    headingParagraphs: document.querySelectorAll('#main-content > .page-heading p').length,
    sidebarLinks: [...document.querySelectorAll('.enterprise-sidebar a')].filter(node => node.getClientRects().length).length,
    bodyText: document.querySelector('#main-content').innerText,
  }));
  assert(layout.overflow <= 1, route + ' horizontal overflow');
  assert.equal(layout.panels, 1, route + ' must keep one primary panel');
  assert.equal(layout.tabs, 0, route + ' must not duplicate enterprise navigation');
  assert.equal(layout.iframe, false);
  assert.equal(layout.hero, false);
  assert.equal(layout.summaryStrip, false);
  assert.equal(layout.cards, 0);
  assert(layout.headingParagraphs <= 1);
  assert(layout.sidebarLinks <= 7);
  assert(!layout.bodyText.includes('undefined'));
  assert(!layout.bodyText.includes('[object Object]'));
  await audit(route);
  return layout;
}

async function setValue(selector, value) {
  await page.$eval(selector, (node, next) => {
    node.value = next;
    node.dispatchEvent(new Event('input', { bubbles: true }));
    node.dispatchEvent(new Event('change', { bubbles: true }));
  }, value);
}

(async () => {
  assert(PASSWORD, 'Supply TEST5_E2E_PASSWORD');
  assert.equal((await (await fetch(BASE + '/api/health')).json()).app, 'test');
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
  const seed = await seedPlugin();
  const applicationName = '设备预警应用-' + seed.suffix;
  const applicationCode = 'equipment-alert-' + seed.suffix;

  await visit(`/applications?keyword=${encodeURIComponent(seed.suffix)}`, '#main-content > .panel');
  assert.equal(await page.$$eval('tbody tr', rows => rows.length), 0);
  await page.click('[data-app-guide]');
  await page.waitForSelector('#platform-dialog[open] .application-guide');
  assert.equal(await page.$$eval('.application-guide li', items => items.length), 4);
  assert((await page.$eval('.application-guide', node => node.innerText)).includes('把已有能力组合成一个可发布入口'));
  await page.screenshot({ path: path.join(OUTPUT, 'application-usage-guide.png'), fullPage: true });
  await page.click('#platform-dialog [data-dialog-close]');
  pass('Application guidance explains composition, audience, publication and traceability on demand');
  await page.click('[data-new-app]');
  await page.waitForSelector('#platform-dialog[open] [name=entryType]');
  const createForm = await page.evaluate(() => ({
    title: document.querySelector('#dialog-title').textContent.trim(),
    details: [...document.querySelectorAll('#platform-dialog details')].map(node => ({
      title: node.querySelector('summary').textContent.trim(), open: node.open, hidden: node.hidden,
    })),
    visibleFields: [...document.querySelectorAll('#platform-dialog .form-field')].filter(node =>
      !node.hidden && node.getClientRects().length && !node.closest('details:not([open])')
    ).length,
    cards: document.querySelectorAll('#platform-dialog .overview-card, #platform-dialog .resource-card').length,
  }));
  assert.equal(createForm.title, '新建应用');
  assert.equal(createForm.cards, 0);
  assert(createForm.visibleFields <= 10, JSON.stringify(createForm));
  assert(createForm.details.every(item => !item.open));
  assert.equal(createForm.details.find(item => item.title === '指定发布受众').hidden, true);
  await setValue('#platform-dialog [name=name]', applicationName);
  await setValue('#platform-dialog [name=code]', applicationCode);
  await setValue('#platform-dialog [name=description]', '查看并处置设备预警');
  await page.select('#platform-dialog [name=kind]', 'automation');
  await page.select('#platform-dialog [name=entryType]', 'plugin');
  await page.waitForFunction(id => [...document.querySelectorAll('#platform-dialog [name=entryId] option')].some(option => option.value === id), {}, seed.pluginId);
  await page.select('#platform-dialog [name=entryId]', seed.pluginId);
  await audit('/applications-create-dialog');
  await page.screenshot({ path: path.join(OUTPUT, 'application-create.png'), fullPage: true });
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'networkidle0', timeout: 90000 }),
    page.click('#platform-dialog button[type=submit]'),
  ]);
  await page.waitForSelector('.application-detail');
  const applicationId = new URL(page.url()).searchParams.get('id');
  assert(applicationId);
  pass('Application creation exposes only active fields and folds advanced settings');

  await visit(`/applications?id=${applicationId}`, '.application-detail');
  const draftDetail = await page.evaluate(() => ({
    panels: document.querySelectorAll('#main-content > .panel').length,
    rows: document.querySelectorAll('.application-detail > .table-scroll tbody tr').length,
    histories: [...document.querySelectorAll('.application-detail > details')].map(node => node.open),
    text: document.querySelector('.application-detail').innerText,
    publish: Boolean(document.querySelector('[data-app-publish]')),
    launch: Boolean(document.querySelector('[data-app-launch]')),
  }));
  assert.equal(draftDetail.panels, 1);
  assert.equal(draftDetail.rows, 1);
  assert(draftDetail.histories.every(open => !open));
  assert(draftDetail.text.includes('设备预警入口-' + seed.suffix));
  assert.equal(draftDetail.publish, true);
  assert.equal(draftDetail.launch, false);
  await page.screenshot({ path: path.join(OUTPUT, 'application-draft-detail.png'), fullPage: true });
  pass('Draft detail is one record panel with one binding table and folded history');

  await page.click('[data-app-publish]');
  await page.waitForSelector('#platform-dialog[open]');
  assert.equal(await page.$eval('#dialog-title', node => node.textContent.trim()), '发布应用');
  await audit('/applications-publish-confirmation');
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'networkidle0', timeout: 90000 }),
    page.click('#platform-dialog button[type=submit]'),
  ]);
  await page.waitForSelector('[data-app-launch]');
  assert((await page.$eval('.application-detail', node => node.innerText)).includes('已发布'));
  pass('Explicit confirmation publishes the immutable application version');

  await page.click('[data-app-launch]');
  await page.waitForSelector('#platform-dialog[open] [name=arguments]');
  const launchForm = await page.evaluate(() => ({
    title: document.querySelector('#dialog-title').textContent.trim(),
    schemaOpen: document.querySelector('#platform-dialog details').open,
    version: document.querySelector('#platform-dialog input[disabled]').value,
    overflow: document.querySelector('#platform-dialog').scrollWidth - document.querySelector('#platform-dialog').clientWidth,
  }));
  assert.equal(launchForm.title, '启动正式应用');
  assert.equal(launchForm.schemaOpen, false);
  assert.equal(launchForm.version, 'v1');
  assert(launchForm.overflow <= 1);
  await audit('/applications-launch-dialog');
  await page.screenshot({ path: path.join(OUTPUT, 'application-launch.png'), fullPage: true });
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'networkidle0', timeout: 90000 }),
    page.click('#platform-dialog button[type=submit]'),
  ]);
  const launchedUrl = new URL(page.url());
  assert.equal(launchedUrl.pathname, '/ai');
  assert.equal(launchedUrl.searchParams.get('plugin_id'), seed.pluginId);
  await page.waitForSelector('#composer-form');
  const launches = await call('GET', `/api/v1/applications/${applicationId}/launches?limit=20`);
  assert.equal(launches.length, 1);
  assert.equal(launches[0].status, 'completed');
  assert(launches[0].runId);
  pass('Launching opens a fixed internal path and records a completed platform run');

  await visit(`/applications?id=${applicationId}`, '.application-detail');
  assert.equal(await page.$$eval('.application-detail > details', nodes => nodes.length), 2);
  assert((await page.$eval('.application-detail', node => node.innerText)).includes('启动记录 · 1'));
  await page.screenshot({ path: path.join(OUTPUT, 'application-published-detail.png'), fullPage: true });
  await page.click('[data-app-edit]');
  await page.waitForSelector('#platform-dialog[open] [name=entryType]');
  assert.equal(await page.$eval('#dialog-title', node => node.textContent.trim()), '创建应用新版本');
  assert.equal(await page.$eval('#platform-dialog [name=entryId]', node => node.value), seed.pluginId);
  assert(await page.$$eval('#platform-dialog details', nodes => nodes.every(node => !node.open)));
  await page.click('#platform-dialog [data-dialog-close]');
  pass('Published detail retains trace history and opens a prefilled immutable-version form');

  await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 1, isMobile: true });
  await visit(`/applications?keyword=${encodeURIComponent(seed.suffix)}`, '#main-content > .panel');
  assert.equal(await page.$$eval('tbody tr', rows => rows.length), 1);
  await visit(`/applications?id=${applicationId}`, '.application-detail');
  await page.click('[data-app-edit]');
  await page.waitForSelector('#platform-dialog[open]');
  const mobile = await page.evaluate(() => {
    const dialog = document.querySelector('#platform-dialog').getBoundingClientRect();
    return {
      overflow: document.documentElement.scrollWidth - innerWidth,
      left: dialog.left, right: dialog.right,
      formColumns: getComputedStyle(document.querySelector('#platform-dialog .application-form')).gridTemplateColumns.split(' ').length,
      primaryLinks: [...document.querySelectorAll('.enterprise-primary a')].filter(node => node.getClientRects().length).length,
    };
  });
  assert(mobile.overflow <= 1 && mobile.left >= 0 && mobile.right <= 390, JSON.stringify(mobile));
  assert.equal(mobile.formColumns, 1);
  assert.equal(mobile.primaryLinks, 0);
  await audit('/applications-mobile-version-dialog');
  await page.screenshot({ path: path.join(OUTPUT, 'application-mobile.png'), fullPage: true });
  pass('Application list, detail and version form fit a 390px viewport');

  assert.deepEqual(failures, []);
  assert.deepEqual(violations, []);
  fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({
    checks, failures, accessibilityFindings: violations,
    records: { applicationId, pluginId: seed.pluginId, runId: launches[0].runId },
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
