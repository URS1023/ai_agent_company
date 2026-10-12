/*
 * 中文说明（报表生成质量验收）：
 * 验证行为：报表生成结果的组件数量、业务指标与布局是否满足要求。
 * 运行方式：只能对已验证的隔离夹具运行，且只使用缓存的查询结果（不重新查库）。
 * 边界：避免真实业务查询；断言以缓存结果与渲染产物为准。
 */
// Run against a verified isolated fixture with cached query results only.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');

const base = process.env.TEST5_E2E_BASE_URL;
const reportPath = process.env.TEST5_QUALITY_REPORT_PATH;
const output = path.resolve(process.env.TEST5_QUALITY_OUTPUT || '.implementation/report-quality-browser');
if (!base || !reportPath || process.env.TEST5_ISOLATED_FIXTURE !== '1') throw new Error('Explicit isolated local fixture required');
assert(['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname));
assert(reportPath.startsWith('/report/view/'));

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await puppeteer.launch({ executablePath: process.env.TEST5_CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true });
  const errors = [], queries = [];
  try {
    const page = await browser.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.setRequestInterception(true);
    page.on('request', request => {
      const url = new URL(request.url());
      if (url.protocol.startsWith('http') && url.origin !== new URL(base).origin) return request.abort();
      if (/\/query\b|\/execute\b|\/send$/.test(url.pathname) && request.method() === 'POST') {
        queries.push(url.pathname); return request.abort();
      }
      request.continue();
    });
    await page.setViewport({ width: 1440, height: 960 });
    await page.goto(base, { waitUntil: 'networkidle0' });
    await page.type('#login-username', 'workspace-admin');
    await page.type('#login-password', 'LocalDashboardFixture2026!');
    await page.click('#login-btn');
    await page.waitForSelector('.enterprise-area-grid');
    await page.goto(base + '/ai', { waitUntil: 'networkidle0' });
    await page.click('#dashboard-template-btn');
    await page.waitForSelector('#dashboard-template-grid [data-dashboard-template-id]');
    assert((await page.$eval('.dashboard-template-head p', n => n.textContent)).includes('指定模板后只生成 SQL'));
    await page.click('#dashboard-template-grid [data-dashboard-template-id="lynx-source-template-48"]');
    assert((await page.$eval('#dashboard-template-summary', n => n.textContent)).includes('只生成 SQL，不修改模板'));
    await page.click('#dashboard-template-use');
    assert((await page.$eval('#dashboard-template-label', n => n.textContent)).includes('设备数字运维'));
    await page.goto(base + reportPath, { waitUntil: 'networkidle0' });
    const frame = await (await page.waitForSelector('#business-module-frame')).contentFrame();
    await frame.waitForSelector('.jimu-border, .tech-reference-panel');
    const measurements = [];
    for (const width of [1440, 1262]) {
      await page.setViewport({ width, height: 960 });
      await new Promise(resolve => setTimeout(resolve, 200));
      const result = await frame.evaluate(() => ({
        text: document.body.innerText,
        panels: document.querySelectorAll('.jimu-border, .tech-reference-panel').length,
        metrics: [...document.querySelectorAll('.stat-reading')].map(n => {
          const label = n.querySelector('.stat-label').getBoundingClientRect();
          const card = n.closest('.stat-card').getBoundingClientRect();
          return { text: n.innerText, clipped: label.bottom > card.bottom + 1 || label.right > card.right + 1 };
        }),
        cells: [...document.querySelectorAll('.scroll-row')].map(n => n.innerText),
      }));
      assert.equal(result.panels, 6);
      assert(result.text.includes('财务与设备数字大屏'));
      assert(result.metrics.some(m => m.text.includes('200') && m.text.includes('应收账款')));
      assert(result.metrics.some(m => m.text.includes('2') && m.text.includes('设备总数')));
      assert(result.metrics.every(m => !m.clipped));
      assert(result.cells.some(row => row.includes('客户甲') && row.includes('120')));
      assert(result.cells.some(row => row.includes('客户乙') && row.includes('80')));
      measurements.push(result);
      await page.screenshot({ path: path.join(output, `technology-dashboard-${width}.png`) });
    }
    assert.deepEqual(errors, []);
    assert.deepEqual(queries, []);
    fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({ passed: true, measurements, errors, queries }, null, 2));
    console.log('PASS reference selection, both business sections, real amounts, unclipped metrics, two viewport sizes; zero SQL requests');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
