/*
 * 中文说明（数据源治理界面验收）：
 * 验证行为：数据源健康状态、限流/配额策略与凭据安全的导入导出界面按后端返回正确展示。
 * 运行方式：需隔离服务（TEST5_E2E_BASE_URL，默认 8011）+ 本机 Edge，口令默认 admin123。
 * 边界：凭据不得在页面上以明文出现（导出/导入流程以脱敏为准），用例对此做断言。
 */
/* Local acceptance for data-source health, limits and credential-safe transfer UI. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');

const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011';
const PASSWORD = process.env.TEST5_E2E_PASSWORD || 'admin123';
const EDGE = process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const RUN = Date.now().toString(36);
const OUTPUT = path.resolve('.implementation/ui-results/datasource-governance-' + RUN);
let browser;
let page;
let frame;

async function api(url, method = 'GET', body) {
  return page.evaluate(async (url, method, body) => {
    const response = await fetch(url, {
      method,
      headers: {
        Authorization: 'Bearer ' + localStorage.getItem('agent_token'),
        'X-Workspace-Id': localStorage.getItem('agent_workspace_id'),
        'Content-Type': 'application/json',
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await response.text();
    return { status: response.status, body: text ? JSON.parse(text) : null };
  }, url, method, body);
}

async function visibleButton(target, label) {
  for (const handle of await target.$$('button')) {
    const matches = await handle.evaluate((element, text) => (
      Boolean(element.getClientRects().length) && element.textContent.trim() === text
    ), label);
    if (matches) {
      await handle.click();
      return;
    }
  }
  throw new Error('Visible button not found: ' + label);
}

async function loadDataSources(viewport) {
  await page.setViewport(viewport);
  const response = await page.goto(BASE + '/datasources', { waitUntil: 'networkidle0', timeout: 60000 });
  assert.equal(response.status(), 200);
  const frameElement = await page.waitForSelector('#business-module-frame');
  frame = await frameElement.contentFrame();
  await frame.waitForSelector('.datasource-workspace .datasource-card', { visible: true, timeout: 30000 });
}

async function sourceCard(name) {
  const handle = await frame.evaluateHandle((sourceName) => (
    [...document.querySelectorAll('.datasource-card')]
      .find((card) => card.querySelector('h3')?.textContent.trim() === sourceName)
  ), name);
  const element = handle.asElement();
  assert(element, 'Data source card not found: ' + name);
  return element;
}

(async () => {
  assert(['127.0.0.1', 'localhost'].includes(new URL(BASE).hostname), 'Local acceptance only');
  const health = await fetch(BASE + '/api/health').then((response) => response.json());
  assert(['native-dev', 'test'].includes(health.app));
  fs.mkdirSync(OUTPUT, { recursive: true });

  browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: true,
    args: ['--no-sandbox', '--disable-gpu'],
  });
  page = await browser.newPage();
  const browserErrors = [];
  page.on('pageerror', (error) => browserErrors.push('pageerror: ' + error.message));
  page.on('response', (response) => {
    if (response.status() >= 500) browserErrors.push(response.status() + ' ' + response.url());
  });

  await page.goto(BASE, { waitUntil: 'networkidle0', timeout: 60000 });
  await page.type('#login-username', 'workspace-admin');
  await page.type('#login-password', PASSWORD);
  await Promise.all([
    page.click('#login-btn'),
    page.waitForFunction(() => Boolean(localStorage.getItem('agent_token')), { timeout: 20000 }),
  ]);
  await page.waitForSelector('[data-testid="business-shell"]');

  const sourceResponse = await api('/api/v1/analytics/datasources');
  assert.equal(sourceResponse.status, 200);
  assert(sourceResponse.body.data.length > 0, 'Authorized data-source catalog must not be empty');
  const source = sourceResponse.body.data.find((item) => item.name === 'variantorm')
    || sourceResponse.body.data.find((item) => item.enabled);
  assert(source, 'At least one enabled data source is required');
  assert(source.description || source.environmentSummary, 'Data source must expose business or environment description');

  await loadDataSources({ width: 1440, height: 940, deviceScaleFactor: 1 });
  let card = await sourceCard(source.name);
  assert((await card.evaluate((element) => element.innerText)).includes(source.type.toUpperCase()));
  const healthButton = await card.$('.health');
  assert(healthButton, 'Health action must exist on the data-source card');
  const healthRequest = page.waitForResponse((response) => (
    response.url().endsWith(`/api/v1/analytics/datasources/${source.id}/health-checks`)
      && response.request().method() === 'POST'
  ));
  await healthButton.click();
  const healthResponse = await healthRequest;
  assert.equal(healthResponse.status(), 200, await healthResponse.text());
  await frame.waitForFunction((name) => {
    const current = [...document.querySelectorAll('.datasource-card')]
      .find((item) => item.querySelector('h3')?.textContent.trim() === name);
    return current?.innerText.includes('连接正常') && current?.innerText.includes('检查于');
  }, {}, source.name);

  card = await sourceCard(source.name);
  await visibleButton(card, '治理策略');
  await frame.waitForSelector('.policy-content', { visible: true });
  await frame.waitForSelector('.runtime-facts', { visible: true });
  const policyText = await frame.$eval('.policy-content', (element) => element.innerText);
  for (const label of ['并发查询', '每日查询上限', '自动健康检查', '检查间隔', '健康检查历史']) {
    assert(policyText.includes(label), 'Missing policy control: ' + label);
  }
  assert.equal(await frame.$$eval('.runtime-facts > div', (items) => items.length), 4);

  const policyResponse = await api(`/api/v1/analytics/datasources/${source.id}/policy`);
  assert.equal(policyResponse.status, 200);
  assert(policyResponse.body.data.maxConcurrency >= 1);
  assert(policyResponse.body.data.dailyQueryLimit >= 1);
  assert.equal(typeof policyResponse.body.data.activeQueries, 'number');
  assert.equal(typeof policyResponse.body.data.queriesToday, 'number');
  const historyResponse = await api(`/api/v1/analytics/datasources/${source.id}/health-checks`);
  assert.equal(historyResponse.status, 200);
  assert(historyResponse.body.data.some((item) => item.status === 'healthy'));
  await page.screenshot({ path: path.join(OUTPUT, 'policy-and-health.png') });
  await page.keyboard.press('Escape');
  await frame.waitForSelector('.policy-content', { hidden: true });

  await visibleButton(frame, '配置迁移');
  await frame.waitForSelector('.transfer-grid', { visible: true });
  const transferText = await frame.$eval('.transfer-grid', (element) => element.closest('.el-dialog')?.innerText || '');
  assert(transferText.includes('不会导入密码'));
  assert(transferText.includes('重新安全填写凭据'));
  const exportResponse = await api('/api/v1/analytics/datasources/config-export');
  assert.equal(exportResponse.status, 200);
  const serialized = JSON.stringify(exportResponse.body);
  assert(!/password|secretId|secret_id/i.test(serialized), 'Export must not contain credential fields');
  assert(exportResponse.body.items.length > 0);
  const bundlePath = path.join(OUTPUT, 'datasource-config.json');
  fs.writeFileSync(bundlePath, JSON.stringify(exportResponse.body, null, 2));
  const fileInput = await frame.$('.transfer-grid input[type=file]');
  assert(fileInput);
  await fileInput.uploadFile(bundlePath);
  const dryRunRequest = page.waitForResponse((response) => (
    response.url().endsWith('/api/v1/analytics/datasources/config-import')
      && response.request().method() === 'POST'
  ));
  await visibleButton(frame, '预检');
  const dryRunResponse = await dryRunRequest;
  assert.equal(dryRunResponse.status(), 200, await dryRunResponse.text());
  const dryRunBody = await dryRunResponse.json();
  assert.equal(dryRunBody.data.dryRun, true);
  await frame.waitForSelector('.preview-summary', { visible: true });
  await page.screenshot({ path: path.join(OUTPUT, 'credential-safe-transfer.png') });

  await frame.addScriptTag({ path: require.resolve('axe-core/axe.min.js') });
  const violations = await frame.evaluate(async () => {
    const results = await axe.run(document, {
      runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] },
    });
    return results.violations.map((item) => ({
      id: item.id,
      impact: item.impact,
      nodes: item.nodes.length,
      examples: item.nodes.slice(0, 12).map((node) => ({
        target: node.target,
        summary: node.failureSummary,
        html: node.html,
      })),
    }));
  });
  assert.equal(violations.filter((item) => ['serious', 'critical'].includes(item.impact)).length, 0,
    JSON.stringify(violations));
  await page.keyboard.press('Escape');

  await loadDataSources({ width: 390, height: 844, deviceScaleFactor: 1, isMobile: true });
  const mobileSize = await frame.evaluate(() => ({
    content: document.documentElement.scrollWidth,
    viewport: window.innerWidth,
  }));
  assert(mobileSize.content <= mobileSize.viewport + 1, 'Mobile horizontal overflow: ' + JSON.stringify(mobileSize));
  await page.screenshot({ path: path.join(OUTPUT, 'datasources-mobile.png') });

  const refreshed = await api('/api/v1/analytics/datasources');
  const refreshedSource = refreshed.body.data.find((item) => item.id === source.id);
  assert.equal(refreshedSource.healthStatus, 'healthy');
  assert(refreshedSource.lastHealthCheckAt);
  assert.deepEqual(browserErrors, []);

  const result = {
    ok: true,
    source: { id: source.id, name: source.name },
    health: refreshedSource.healthStatus,
    policy: policyResponse.body.data,
    historyCount: historyResponse.body.data.length,
    dryRun: dryRunBody.data,
    accessibilityFindings: violations,
    mobileSize,
  };
  fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
  console.log('RESULT_DIR ' + OUTPUT);
})().catch(async (error) => {
  console.error(error.stack || error);
  if (page && !page.isClosed()) await page.screenshot({ path: path.join(OUTPUT, 'failure.png') }).catch(() => {});
  process.exitCode = 1;
}).finally(async () => {
  if (browser) await browser.close();
});
