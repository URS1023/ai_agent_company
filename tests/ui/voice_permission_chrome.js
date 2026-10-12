/*
 * 中文说明（Chrome 麦克风权限下的语音输入验收）：
 * 验证行为：在真实 Chrome 中按权限授予/拒绝两种情形验证语音输入可用性与提示。
 * 运行方式：需本机 Chrome 与可访问服务；会 spawn 子进程准备权限环境。
 * 边界：涉及真实麦克风权限，只能在受控的本机环境执行。
 */
"use strict";
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { spawn } = require('node:child_process');
const puppeteer = require('puppeteer-core');
const ROOT = path.resolve(__dirname, '../..');
const OUTPUT = path.join(ROOT, '.implementation/ui-results/voice-permission-' + Date.now());
const PASSWORD = 'IsolatedVoicePermission12345';
const errors = [], steps = [];
let server, browser, page;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function login(base) {
  await page.goto(base + '/ai', { waitUntil: 'networkidle0' });
  await page.click('#login-username');
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  await page.type('#login-username', 'workspace-admin');
  await page.type('#login-password', PASSWORD);
  await page.click('#login-btn');
  await page.waitForSelector('#login-view.hidden');
  await page.waitForSelector('#voice-input-btn', { visible: true });
  await page.waitForSelector('[data-testid=ai-business-start]', { visible: true });
}
async function record(label) {
  const screenshot = (steps.length + 1) + '.png';
  await page.screenshot({ path: path.join(OUTPUT, screenshot), fullPage: true });
  steps.push({ label, screenshot }); console.log('PASS ' + label);
}
(async () => {
  fs.mkdirSync(OUTPUT, { recursive: true });
  const probe = http.createServer();
  await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve));
  const port = probe.address().port;
  await new Promise(resolve => probe.close(resolve));
  const base = `http://localhost:${port}`, insecure = `http://voice-test.invalid:${port}`;
  const log = fs.openSync(path.join(OUTPUT, 'server.log'), 'w');
  server = spawn(path.join(ROOT, '.venv_py312/Scripts/python.exe'), ['run.py'], {
    cwd: path.join(ROOT, 'backend'), windowsHide: true, stdio: ['ignore', log, log],
    env: { ...process.env, HOST: '127.0.0.1', PORT: String(port), APP_ENV: 'test', DATABASE_URL: 'sqlite:///' + path.join(OUTPUT, 'test.sqlite').replaceAll('\\', '/'), LOCAL_STORAGE_ROOT: path.join(OUTPUT, 'storage'), BOOTSTRAP_ADMIN_PASSWORD: PASSWORD, BOOTSTRAP_LYNX_REPORTS: '0', SCHEDULER_ENABLED: '0', AUTO_MIGRATE: '1' },
  });
  fs.closeSync(log);
  let ready = false;
  for (let i = 0; i < 90; i++) {
    try { ready = (await (await fetch(base + '/api/health')).json()).app === 'test'; } catch {}
    if (ready) break;
    if (server.exitCode !== null) throw new Error('Isolated server failed');
    await sleep(500);
  }
  assert(ready);
  browser = await puppeteer.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true, args: ['--disable-gpu', '--no-proxy-server', '--host-resolver-rules=MAP voice-test.invalid 127.0.0.1'] });
  page = await browser.newPage();
  page.on('pageerror', error => errors.push(error.message));
  await page.setViewport({ width: 1440, height: 960 });
  await login(insecure);
  assert.equal(await page.evaluate(() => isSecureContext), false);
  await page.click('#voice-input-btn');
  await page.waitForSelector('#voice-input-feedback:not([hidden])');
  assert.match(await page.$eval('#voice-input-feedback', el => el.textContent), /其他电脑访问需要配置 HTTPS/);
  await record('普通HTTP地址点击麦克风显示安全连接处理步骤');
  await page.setViewport({ width: 390, height: 844 });
  await page.reload({ waitUntil: 'networkidle0' });
  await page.waitForSelector('[data-testid=ai-business-start]', { visible: true });
  await page.click('#voice-input-btn');
  await page.waitForSelector('#voice-input-feedback:not([hidden])');
  const geometry = await page.$eval('#voice-input-feedback', el => ({ left: el.getBoundingClientRect().left, right: el.getBoundingClientRect().right, viewport: innerWidth, scroll: el.scrollWidth, client: el.clientWidth }));
  assert(geometry.left >= 0 && geometry.right <= geometry.viewport + 1 && geometry.scroll <= geometry.client + 1, JSON.stringify(geometry));
  await record('390px下权限说明完整可读');
  await page.click('#voice-input-feedback button');
  assert(await page.$eval('#voice-input-feedback', el => el.hidden));
  await page.setViewport({ width: 1440, height: 960 });
  await login(base);
  assert.equal(await page.evaluate(() => isSecureContext), true);
  assert(await page.evaluate(() => Boolean(window.SpeechRecognition || window.webkitSpeechRecognition)));
  // Deny microphone in this disposable browser context. No real audio is captured.
  await browser.defaultBrowserContext().overridePermissions(base, []);
  await page.click('#voice-input-btn');
  await page.waitForSelector('#voice-input-feedback:not([hidden])', { timeout: 20000 });
  assert.match(await page.$eval('#voice-input-feedback', el => el.textContent), /浏览器或系统未允许使用麦克风/);
  assert.equal(await page.$eval('#voice-input-btn', el => el.getAttribute('aria-pressed')), 'false');
  await record('真实Chrome拒绝麦克风权限后显示网站与系统授权步骤');
  await page.click('#voice-input-feedback button');
  await page.click('#voice-input-btn');
  await page.waitForSelector('#voice-input-feedback:not([hidden])');
  assert.deepEqual(errors, []);
  await record('权限失败后可再次操作，没有未处理页面异常');
})().catch(async error => {
  errors.push(error.stack); console.error(error.stack); process.exitCode = 1;
  if (page) { await page.screenshot({ path: path.join(OUTPUT, 'failure.png'), fullPage: true }).catch(() => {}); fs.writeFileSync(path.join(OUTPUT, 'failure.txt'), await page.evaluate(() => document.body.innerText).catch(() => '')); }
}).finally(async () => {
  await browser?.close(); server?.kill();
  fs.writeFileSync(path.join(OUTPUT, 'results.json'), JSON.stringify({ mode: 'Native Chrome; isolated app and database; insecure HTTP origin and native denied microphone permission; no real speech recording', steps, errors }, null, 2));
  console.log(OUTPUT);
});
