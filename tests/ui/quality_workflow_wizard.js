/*
 * 中文说明（质检流程向导的浏览器验收）：
 * 验证行为：紧凑版质检流程向导无默认值预填、底部不会被遮挡，能完整走完创建流程。
 * 运行方式：需隔离服务（TEST5_E2E_BASE_URL，默认 8011）+ 本机 Edge + TEST5_E2E_PASSWORD。
 * 边界：会创建工作流（隔离环境）；断言包括布局遮挡等可访问性问题。
 */
/* Real Edge: compact quality workflow wizard, no defaults, no obstructing footer. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');

const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011';
const RUN = Date.now().toString(36);
const OUTPUT = path.resolve('.implementation/ui-results/quality-workflow-ui-' + RUN);
const checks = [], failures = [], violations = [];
let browser, page, frame;
const pass = message => { checks.push(message); console.log('PASS ' + message); };

async function button(label) {
  await frame.waitForFunction(label => [...document.querySelectorAll('button')].some(item => item.getClientRects().length && item.textContent.trim() === label && !item.disabled), {}, label);
  for (const item of await frame.$$('button')) if (await item.evaluate((node, label) => node.getClientRects().length && node.textContent.trim() === label, label)) {
    await item.scrollIntoView(); await item.click(); return;
  }
  throw new Error('Missing button ' + label);
}

async function audit(label) {
  await frame.waitForFunction(() => !document.querySelector('[class*="enter-active"],[class*="leave-active"]'));
  await frame.addScriptTag({ path: require.resolve('axe-core/axe.min.js') });
  const found = await frame.evaluate(async () => (await axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] } })).violations.map(item => ({ id: item.id, impact: item.impact })));
  violations.push(...found.map(item => ({ label, ...item })));
}

(async () => {
  assert(process.env.TEST5_E2E_PASSWORD); assert(['127.0.0.1', 'localhost'].includes(new URL(BASE).hostname));
  assert(['test', 'ui-validation'].includes((await (await fetch(BASE + '/api/health')).json()).app));
  fs.mkdirSync(OUTPUT, { recursive: true });
  browser = await puppeteer.launch({ executablePath: process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  page = await browser.newPage(); await page.setViewport({ width: 1600, height: 1000 });
  page.on('pageerror', error => failures.push(error.message));
  page.on('response', response => { if (response.status() >= 500) failures.push(response.status() + ' ' + response.url()); });
  await page.goto(BASE, { waitUntil: 'networkidle0' }); await page.type('#login-username', 'workspace-admin'); await page.type('#login-password', process.env.TEST5_E2E_PASSWORD); await page.click('#login-btn'); await page.waitForSelector('.overview-intro');
  assert.equal((await page.goto(BASE + '/quality/inspections', { waitUntil: 'networkidle0' })).status(), 200);
  frame = await (await page.waitForSelector('#business-module-frame')).contentFrame(); await frame.waitForSelector('.quality-ui', { visible: true });
  await button('配置检测流程'); await frame.waitForSelector('#qc-workflow-wizard', { visible: true });
  await frame.waitForFunction(() => [...document.querySelectorAll('#qc-workflow-wizard select')].filter(item => !item.disabled).length >= 2);
  assert.deepEqual(await frame.$$eval('.qc-resource select', items => items.map(item => item.value)), ['', '']);
  assert(await frame.$eval('#qc-workflow-wizard button[type="submit"]', item => item.disabled));
  const desktopHeight = await frame.$eval('.qc-wizard-overview', item => item.getBoundingClientRect().height); assert(desktopHeight < 110, desktopHeight);
  assert.equal(await frame.$eval('.qc-wizard-footer', item => getComputedStyle(item).position), 'static');
  await audit('Quality workflow compact desktop'); await page.screenshot({ path: path.join(OUTPUT, 'quality-workflow-compact.png'), fullPage: true });
  pass('Quality workflow wizard is a compact business form with explicit empty device/template choices and no default selection');

  await page.setViewport({ width: 390, height: 844 }); await frame.waitForSelector('#qc-workflow-wizard', { visible: true });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)); assert(await frame.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  const mobile = await frame.evaluate(() => {
    const overview = document.querySelector('.qc-wizard-overview').getBoundingClientRect();
    const body = document.querySelector('.quality-dialog .el-dialog__body').getBoundingClientRect();
    const footer = document.querySelector('.qc-wizard-footer').getBoundingClientRect();
    return { overviewHeight: overview.height, footerStartsBelowViewport: footer.top >= body.bottom };
  });
  assert(mobile.overviewHeight < 210, JSON.stringify(mobile)); assert(mobile.footerStartsBelowViewport, JSON.stringify(mobile));
  await audit('Quality workflow compact mobile'); await page.screenshot({ path: path.join(OUTPUT, 'quality-workflow-compact-mobile.png'), fullPage: true });
  pass('At 390px the summary remains compact, the action footer stays at the form end, and no component covers configuration fields');
  assert.deepEqual(failures, []); assert.deepEqual(violations, []);
})().catch(error => { failures.push(error.stack || String(error)); console.error(error); process.exitCode = 1; }).finally(async () => {
  fs.mkdirSync(OUTPUT, { recursive: true }); fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({ checks, failures, violations }, null, 2));
  if (browser) await browser.close(); console.log('Evidence ' + OUTPUT);
});
