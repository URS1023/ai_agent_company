/*
 * 中文说明（局域网 HTTP 与旧版 Web Crypto 环境的浏览器兼容验收）：
 * 验证行为：在非安全上下文（LAN http）与较旧浏览器下，前端生成 UUID/加密相关代码仍可用
 *   （如 crypto.randomUUID 缺失时的回退路径）。
 * 运行方式：需隔离服务（TEST5_E2E_BASE_URL，默认 8011）+ 本机 Edge，口令默认 admin123
 *   （TEST5_E2E_PASSWORD 覆盖）；结果写入 .implementation/ui-results/crypto-uuid-compatibility-<run>/。
 * 边界：只读断言；不改动浏览器信任设置或系统证书。
 */
/* Browser compatibility acceptance for LAN HTTP and legacy Web Crypto contexts. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');

const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011';
const PASSWORD = process.env.TEST5_E2E_PASSWORD || 'admin123';
const EDGE = process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const RUN = Date.now().toString(36);
const OUTPUT = path.resolve('.implementation/ui-results/crypto-uuid-compatibility-' + RUN);
let browser;

async function visibleButton(frame, label) {
  await frame.waitForFunction((text) => [...document.querySelectorAll('button')]
    .some((node) => node.getClientRects().length && node.textContent.trim() === text), {}, label);
  await frame.evaluate((text) => [...document.querySelectorAll('button')]
    .find((node) => node.getClientRects().length && node.textContent.trim() === text).click(), label);
}

async function visit(page, route, selector) {
  const response = await page.goto(BASE + route, { waitUntil: 'networkidle0', timeout: 60000 });
  assert.equal(response.status(), 200, route);
  const frameHandle = await page.$('#business-module-frame');
  const frame = frameHandle ? await frameHandle.contentFrame() : page;
  await frame.waitForSelector(selector, { visible: true, timeout: 30000 });
  assert.equal(await frame.evaluate(() => typeof globalThis.crypto?.randomUUID), 'undefined',
    'The acceptance fixture must remove native crypto.randomUUID in the business module');
  const text = await frame.evaluate(() => document.body.innerText);
  assert(!/模块加载失败|模块初始化失败|randomUUID is not a function/.test(text), route + ': ' + text.slice(0, 300));
  return frame;
}

(async () => {
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, 'isolated', 'Use an isolated APP_ENV=test server');
  assert(['127.0.0.1', 'localhost'].includes(new URL(BASE).hostname), 'Local acceptance only');
  assert.equal((await (await fetch(BASE + '/api/health')).json()).app, 'test');
  fs.mkdirSync(OUTPUT, { recursive: true });

  browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: true,
    args: ['--no-sandbox', '--disable-gpu'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 960, deviceScaleFactor: 1 });
  await page.evaluateOnNewDocument(() => {
    const cryptoObject = globalThis.crypto;
    if (!cryptoObject) return;
    try {
      Object.defineProperty(cryptoObject, 'randomUUID', {
        configurable: true,
        value: undefined,
      });
    } catch {
      try {
        Object.defineProperty(Object.getPrototypeOf(cryptoObject), 'randomUUID', {
          configurable: true,
          value: undefined,
        });
      } catch {
        // The explicit assertion in visit() fails if the browser cannot be patched.
      }
    }
  });

  const failures = [];
  page.on('pageerror', (error) => failures.push('pageerror: ' + error.message));
  page.on('response', (response) => {
    if (response.status() >= 500) failures.push(response.status() + ' ' + response.url());
  });
  page.on('dialog', async (dialog) => dialog.accept());

  await page.goto(BASE, { waitUntil: 'networkidle0', timeout: 60000 });
  assert.equal(await page.evaluate(() => typeof globalThis.crypto?.randomUUID), 'undefined');
  await page.type('#login-username', 'workspace-admin');
  await page.type('#login-password', PASSWORD);
  await Promise.all([
    page.click('#login-btn'),
    page.waitForFunction(() => Boolean(localStorage.getItem('agent_token')), { timeout: 20000 }),
  ]);

  await visit(page, '/analytics', '.intelligent-query');
  const workflow = await visit(page, '/workflows/new', '.wf-editor');
  assert.equal(await workflow.$$eval('.wf-node', (nodes) => nodes.length), 2,
    'Fallback IDs must allow the initial workflow graph to be created');

  const alerts = await visit(page, '/alerts/rules', '.alerts-ui');
  await visibleButton(alerts, '新增策略');
  await alerts.waitForSelector('.alert-rule-form', { visible: true });
  assert.equal(await alerts.$$eval('.wf-rule-card', (nodes) => nodes.length), 1,
    'Fallback IDs must create the first alert condition');

  const quality = await visit(page, '/quality/inspections', '.quality-ui');
  await visibleButton(quality, '新增质检');
  await quality.waitForSelector('#qc-inspection-form', { visible: true });
  await page.screenshot({ path: path.join(OUTPUT, 'quality-form-without-randomuuid.png') });
  await page.goto(BASE + '/quality/inspections', { waitUntil: 'networkidle0', timeout: 60000 });
  const qualityFrame = await (await page.waitForSelector('#business-module-frame')).contentFrame();
  await qualityFrame.waitForSelector('.quality-ui', { visible: true });
  await visibleButton(qualityFrame, '配置检测流程');
  await qualityFrame.waitForSelector('.qc-workflow-wizard', { visible: true });
  assert.equal(await qualityFrame.$$eval('.qc-workflow-group', (nodes) => nodes.length), 1,
    'Fallback IDs must create the first quality workflow group');

  await page.screenshot({ path: path.join(OUTPUT, 'quality-workflow-without-randomuuid.png') });
  assert.deepEqual(failures, [], failures.join('\n'));
  const result = {
    passed: true,
    nativeRandomUUID: false,
    routes: ['/analytics', '/workflows/new', '/alerts/rules', '/quality/inspections'],
    checks: 7,
    failures,
  };
  fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ ...result, output: OUTPUT }));
})().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
}).finally(async () => {
  if (browser) await browser.close();
});
