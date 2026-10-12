/*
 * 中文说明（业务示例数据只读验收）：
 * 验证行为：由 scripts/seed_business_examples.py 经 API 播入的设备/质检/应用示例，
 *   在页面上能按预期展示，且本用例不修改这些数据。
 * 运行方式：需隔离服务（TEST5_E2E_BASE_URL，默认 8011）+ 本机 Edge + TEST5_E2E_PASSWORD，
 *   结果写入 .implementation/ui-results/business-examples-<run>/。
 * 边界：只读；示例数据缺失时应报失败而不是自动重建。
 */
/* Read-only Edge acceptance for the API-seeded equipment, quality and application example. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');

const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011';
const PASSWORD = process.env.TEST5_E2E_PASSWORD;
const EDGE = process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const OUTPUT = path.resolve('.implementation/ui-results/business-examples-' + Date.now().toString(36));
const checks = [], failures = [];
let browser, page;

function pass(name, detail = {}) {
  checks.push({ name, ...detail });
  console.log('PASS ' + name);
}

async function framePage(route, selector) {
  const response = await page.goto(BASE + route, { waitUntil: 'networkidle0', timeout: 90000 });
  assert.equal(response.status(), 200, route);
  const iframe = await page.waitForSelector('#business-module-frame');
  const frame = await iframe.contentFrame();
  await frame.waitForSelector(selector, { visible: true, timeout: 30000 });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1));
  return frame;
}

async function clickText(frame, text) {
  await frame.waitForFunction(label => [...document.querySelectorAll('button')].some(item => item.getClientRects().length && item.textContent.trim() === label && !item.disabled), {}, text);
  await frame.evaluate(label => [...document.querySelectorAll('button')].find(item => item.getClientRects().length && item.textContent.trim() === label && !item.disabled).click(), text);
}

async function expectTable(frame, text) {
  await frame.waitForFunction(expected => document.querySelector('.wf-table tbody')?.innerText.includes(expected), {}, text);
  assert((await frame.$eval('.wf-table tbody', node => node.innerText)).includes(text));
}

(async () => {
  assert(PASSWORD, 'Supply TEST5_E2E_PASSWORD');
  assert(['127.0.0.1', 'localhost'].includes(new URL(BASE).hostname), 'Read-only check is limited to the local app');
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

  let frame = await framePage('/alerts/devices', '.alerts-ui');
  await expectTable(frame, '示例·车队保养监测');
  assert.equal(await frame.$$eval('.alert-tabs a', items => items.length), 6);
  const actionLayout = await frame.evaluate(() => {
    const row = [...document.querySelectorAll('.alert-table tbody tr')].find(item => item.textContent.includes('示例·车队保养监测'));
    const cell = row.querySelector('.alert-action-cell');
    const buttons = [...cell.querySelectorAll('button')].map(item => Math.round(item.getBoundingClientRect().top));
    return { wrap: getComputedStyle(cell.querySelector('.wf-actions')).flexWrap, tops: buttons, width: Math.round(cell.getBoundingClientRect().width) };
  });
  assert.equal(actionLayout.wrap, 'nowrap');
  assert.equal(new Set(actionLayout.tops).size, 1, JSON.stringify(actionLayout));
  assert(actionLayout.width >= 169, JSON.stringify(actionLayout));
  await clickText(frame, '使用说明');
  await frame.waitForSelector('.business-process-guide', { visible: true });
  assert.equal(await frame.$$eval('.business-guide-flow > li', items => items.length), 6);
  await page.screenshot({ path: path.join(OUTPUT, 'alerts-guide-and-layout.png'), fullPage: true });
  await page.keyboard.press('Escape');
  pass('Equipment list, six tabs, aligned actions and on-demand usage guide render from real API records', actionLayout);

  for (const [route, expected] of [
    ['/alerts/points', '当前里程'],
    ['/alerts/advice', '示例·车辆到期保养处置预案'],
    ['/alerts/rules', '示例·车辆保养预警策略'],
    ['/alerts/reports', '示例·车辆保养预警策略 · 预警报告'],
    ['/alerts/problems', '示例·保养到期且里程达标'],
  ]) {
    frame = await framePage(route, '.alerts-ui');
    await expectTable(frame, expected);
  }
  await page.screenshot({ path: path.join(OUTPUT, 'alert-problems.png'), fullPage: true });
  pass('Points, expert advice, policy, executed report and problem list are all reachable');

  frame = await framePage('/quality/metrics', '.quality-ui');
  await expectTable(frame, '示例·碳含量（C）');
  await expectTable(frame, '示例·表面划痕');
  await clickText(frame, '使用说明');
  await frame.waitForSelector('.business-process-guide', { visible: true });
  assert.equal(await frame.$$eval('.business-guide-flow > li', items => items.length), 5);
  await page.keyboard.press('Escape');
  frame = await framePage('/quality/templates', '.quality-ui');
  await expectTable(frame, '示例·来料双项检验标准');
  const templateActions = await frame.evaluate(() => {
    const row = [...document.querySelectorAll('.quality-table tbody tr')].find(item => item.textContent.includes('示例·来料双项检验标准'));
    const cell = row.querySelector('.quality-action-cell');
    return [...cell.querySelectorAll('button')].map(item => Math.round(item.getBoundingClientRect().top));
  });
  assert.equal(new Set(templateActions).size, 1);
  frame = await framePage('/quality/inspections', '.quality-ui');
  await expectTable(frame, 'DEMO-QC-PASS-001');
  await expectTable(frame, 'DEMO-QC-FAIL-001');
  assert.equal(await frame.$$eval('.quality-example-tag', items => items.length), 2);
  await page.screenshot({ path: path.join(OUTPUT, 'quality-records.png'), fullPage: true });
  pass('Quality metrics, immutable template and passed/failed inspections render with aligned controls and example labels');

  let response = await page.goto(BASE + '/applications?keyword=demo-equipment-quality-solution', { waitUntil: 'networkidle0', timeout: 90000 });
  assert.equal(response.status(), 200);
  await page.waitForSelector('#main-content tbody tr');
  assert((await page.$eval('#main-content tbody', node => node.innerText)).includes('示例·设备预警与质量协同方案'));
  await page.click('[data-app-guide]');
  await page.waitForSelector('#platform-dialog[open] .application-guide');
  assert.equal(await page.$$eval('.application-guide li', items => items.length), 4);
  await page.screenshot({ path: path.join(OUTPUT, 'application-guide.png'), fullPage: true });
  await page.click('#platform-dialog [data-dialog-close]');
  pass('Published solution and focused application guide are visible without extra dashboard cards');

  response = await page.goto(BASE + '/operations', { waitUntil: 'networkidle0', timeout: 90000 });
  assert.equal(response.status(), 200);
  await page.waitForSelector('.operations-panel');
  assert.equal(await page.$eval('#main-content h1', node => node.textContent.trim()), '业务运行中心');
  assert.equal(await page.$eval('.enterprise-sidebar a[href="/operations"]', node => node.textContent.trim()), '运行记录');
  assert(await page.$('.enterprise-sidebar a[href="/system/operations"]') === null, 'System operations must not be duplicated in the business sidebar');
  await page.screenshot({ path: path.join(OUTPUT, 'business-operations.png'), fullPage: true });
  pass('Business run records stay under business applications and remain distinct from system operations');

  assert.deepEqual(failures, []);
})().catch(error => {
  failures.push(error.stack || String(error));
  console.error(error);
  process.exitCode = 1;
}).finally(async () => {
  fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({ checks, failures, baseUrl: BASE }, null, 2));
  if (browser) await browser.close();
  console.log('Evidence ' + OUTPUT);
});
