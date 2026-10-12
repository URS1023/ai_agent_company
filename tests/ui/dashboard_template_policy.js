/*
 * 中文说明（大屏模板策略验收，固定连 9147 隔离夹具）：
 * 验证行为：显式模板只生成 SQL（不改布局/组件/样式），未指定模板时按视觉参考生成。
 * 运行方式：先启动 tests/ui/serve_dashboard_template_policy.py（默认 9147 端口）再运行本文件，
 *   夹具数据从 .implementation/dashboard-generation-20260914/active-fixture.json 读取。
 * 边界：使用确定性模型响应，不连外部数据库与外部模型。
 */
/* Real Chrome, dedicated isolated SQLite fixture on 9147; no external DB or model. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');
const BASE = 'http://127.0.0.1:9147';
const artifacts = path.resolve('.implementation/dashboard-generation-20260914');
const fixture = JSON.parse(fs.readFileSync(path.join(artifacts, 'active-fixture.json'), 'utf8'));
const statePath = path.join(fixture.directory, 'template-policy.json');
const policy = JSON.parse(fs.readFileSync(statePath, 'utf8'));
const output = path.join(fixture.directory, 'browser-policy');

(async () => {
  assert.equal((await fetch(BASE + '/api/health').then(r => r.json())).app, 'test');
  fs.mkdirSync(output, {recursive: true});
  const browser = await puppeteer.launch({executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true});
  const errors = [], sends = [];
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(25000);
    page.on('pageerror', error => errors.push(error.message));
    await page.setRequestInterception(true);
    page.on('request', request => {
      const url = new URL(request.url());
      if (url.protocol.startsWith('http') && url.origin !== BASE) return request.abort();
      if (/\/conversations\/[^/]+\/send$/.test(url.pathname)) sends.push(JSON.parse(request.postData()));
      request.continue();
    });
    await page.setViewport({width: 1440, height: 960});
    await page.goto(BASE, {waitUntil: 'networkidle0'});
    await page.type('#login-username', 'workspace-admin');
    await page.type('#login-password', 'LocalDashboardFixture2026!');
    await page.click('#login-btn');
    await page.waitForSelector('.enterprise-area-grid');
    for (const mode of ['auto', 'manual']) {
      await page.goto(BASE + '/ai', {waitUntil: 'networkidle0'});
      await page.click('#business-source-btn');
      await page.waitForSelector('.datasource-option input[data-source-id]');
      if (!(await page.$eval('.datasource-option input[data-source-id]', n => n.checked)))
        await page.click('.datasource-option input[data-source-id]');
      await page.click('[data-datasource-done]');
      await page.click('#dashboard-template-btn');
      await page.waitForSelector('#dashboard-template-grid [data-dashboard-template-id]');
      assert.deepEqual(await page.$$eval('[data-dashboard-mode]', nodes => nodes.map(n => n.dataset.dashboardMode)), ['auto', 'manual']);
      if (mode === 'manual') {
        await page.click(`[data-dashboard-template-id="${policy.templateId}"]`);
        assert((await page.$eval('#dashboard-template-summary', n => n.textContent)).includes('只生成 SQL'));
      } else await page.click('[data-dashboard-mode="auto"]');
      await page.click('#dashboard-template-use');
      await page.type('#prompt-textarea', '生成财务和设备数字大屏，使用输入区已选择的真实数据与模板策略，并保存为唯一草稿。');
      await page.click('#composer-submit-button');
      await page.waitForSelector('[data-testid=dashboard-business-result]');
      await page.waitForFunction(() => !state.streaming);
      assert.equal(sends.at(-1).dashboard_template_mode, mode);
      const frame = await (await page.waitForSelector('[data-testid=dashboard-inline-frame]')).contentFrame();
      await frame.waitForFunction(() => document.body.innerText.includes('应收金额') && document.body.innerText.includes('设备总数'));
      const text = await frame.$eval('body', n => n.innerText);
      assert(text.includes('200') && text.includes('2'));
      assert(!text.includes('不应出现的模型标题'));
      await page.screenshot({path: path.join(output, mode + '.png')});
      await page.reload({waitUntil: 'networkidle0'});
      await page.waitForSelector('[data-testid=dashboard-business-result]');
      assert.equal(await page.$$eval('[data-testid=dashboard-business-result]', nodes => nodes.length), 1);
    }
    assert.equal(sends.length, 2, 'Reload must not start another generation');
    const final = JSON.parse(fs.readFileSync(statePath, 'utf8'));
    assert.deepEqual(final.saved.map(s => s.manual), [false, true]);
    assert.equal(final.designs, 2);
    assert.equal(final.reviews, 2);
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({passed: true, ...final, sends: sends.length, errors}, null, 2));
    console.log(JSON.stringify({passed: true, output, designs: final.designs, saved: final.saved}));
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
