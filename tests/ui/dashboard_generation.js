/*
 * 中文说明（数字大屏生成的浏览器端验收）：
 * 验证行为：在真实 Chrome 中触发生成大屏，用隔离的本地 SQLite 与确定性模型响应，
 *   校验草稿渲染与唯一性（重复请求不产生第二份草稿）。
 * 运行方式：先启动 tests/ui/serve_dashboard_generation.py（默认 9146 端口）再运行本文件；
 *   产物写入 .implementation/dashboard-generation-20260914/browser-<时间戳>/。
 * 边界：不加载任何生产数据源，也不调用外部模型；断言依赖夹具服务的固定响应。
 */
/* Real Chrome UI + isolated local SQLite + deterministic model, no production sources. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');

const BASE = 'http://127.0.0.1:9146';
const ROOT = path.resolve('.implementation/dashboard-generation-20260914');
const OUTPUT = path.join(ROOT, 'browser-' + Date.now());
const ORIGINAL = '生成财务和设备数字大屏，使用输入区已选择的真实数据与模板策略，并保存为唯一草稿。';

(async () => {
  assert.equal((await fetch(BASE + '/api/health').then(r => r.json())).app, 'test');
  const fixture = JSON.parse(fs.readFileSync(path.join(ROOT, 'active-fixture.json'), 'utf8'));
  const countsPath = path.join(fixture.directory, 'counts.json');
  const initial = fs.existsSync(countsPath) ? JSON.parse(fs.readFileSync(countsPath, 'utf8')) : {designs: 0, reviews: 0, generationQueries: 0};
  fs.mkdirSync(OUTPUT, {recursive: true});
  const browser = await puppeteer.launch({executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true});
  const errors = [], sends = [];
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(20000);
    await page.setViewport({width: 1440, height: 960});
    page.on('pageerror', error => errors.push(error.message));
    page.on('response', response => { if (response.status() >= 500) errors.push(response.status() + ' ' + response.url()); });
    page.on('request', request => {
      if (/\/conversations\/[^/]+\/send$/.test(request.url())) sends.push(JSON.parse(request.postData()));
    });
    await page.goto(BASE, {waitUntil: 'networkidle0'});
    await page.type('#login-username', 'workspace-admin');
    await page.type('#login-password', 'LocalDashboardFixture2026!');
    await page.click('#login-btn');
    await page.waitForSelector('.enterprise-area-grid');
    await page.goto(BASE + '/ai', {waitUntil: 'networkidle0'});
    await page.click('#business-source-btn');
    await page.waitForSelector('.datasource-option input[data-source-id]');
    const selected = await page.$eval('.datasource-option input[data-source-id]', node => node.dataset.sourceId);
    await page.click('.datasource-option input[data-source-id]');
    await page.click('[data-datasource-done]');
    assert((await page.$eval('#business-source-label', node => node.textContent)).includes('本机财务设备验证'));
    assert((await page.$eval('#dashboard-template-btn', node => node.textContent)).includes('自动参考模板'));
    await page.type('#prompt-textarea', ORIGINAL);
    await page.click('#composer-submit-button');
    await page.waitForSelector('[data-testid=dashboard-business-result]');
    await page.waitForFunction(() => !state.streaming);
    assert.equal(sends.length, 1);
    assert.equal(sends[0].content, ORIGINAL);
    assert.deepEqual(sends[0].datasource_ids, [selected]);
    assert.equal(sends[0].dashboard_template_mode, 'auto');
    assert.equal(await page.$$eval('[data-testid=dashboard-business-result]', nodes => nodes.length), 1);
    const frameElement = await page.waitForSelector('[data-testid=dashboard-inline-frame]');
    const frame = await frameElement.contentFrame();
    await frame.waitForFunction(() => document.body.innerText.includes('设备') && document.body.innerText.includes('应收金额'));
    await frame.waitForSelector('.jimu-chart canvas, .jimu-chart svg');
    await frame.waitForFunction(() => !document.body.innerText.includes('正在准备图表'));
    await page.$eval('[data-testid=dashboard-business-result]', node => node.scrollIntoView({block: 'start'}));
    await page.screenshot({path: path.join(OUTPUT, 'chat-draft.png')});
    const popupPromise = new Promise(resolve => page.once('popup', resolve));
    await page.click('[data-testid=dashboard-business-result] .dashboard-preview-bar a');
    const preview = await popupPromise;
    await preview.setViewport({width: 1440, height: 960});
    const previewFrameElement = await preview.waitForSelector('#business-module-frame');
    const previewFrame = await previewFrameElement.contentFrame();
    await previewFrame.waitForFunction(() => document.body.innerText.includes('应收金额') && document.body.innerText.includes('设备'));
    await previewFrame.waitForSelector('.jimu-chart canvas, .jimu-chart svg');
    await previewFrame.waitForFunction(() => !document.body.innerText.includes('正在准备图表'));
    await preview.screenshot({path: path.join(OUTPUT, 'standalone-preview.png')});
    const before = page.url();
    await page.reload({waitUntil: 'networkidle0'});
    await page.waitForSelector('[data-testid=dashboard-business-result]');
    assert.equal(page.url(), before);
    assert.equal(await page.$$eval('[data-testid=dashboard-business-result]', nodes => nodes.length), 1);
    assert.equal(sends.length, 1, 'Reload must restore history without regenerating');
    const finalCounts = JSON.parse(fs.readFileSync(countsPath, 'utf8'));
    const counts = Object.fromEntries(Object.keys(initial).map(key => [key, finalCounts[key] - initial[key]]));
    assert.deepEqual(counts, {designs: 2, reviews: 2, generationQueries: 2});
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({passed: true, counts, sends: sends.length, errors, checks: [
      'Select local source and auto template using UI', 'Original requirement and selected source survive model rewrite',
      'Single internal repair with two unique generation SQL queries', 'One persisted draft with inline and standalone preview',
      'Reload restores draft without resending'
    ]}, null, 2));
    console.log(JSON.stringify({passed: true, output: OUTPUT, counts}));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
