/*
 * 中文说明（模板生命周期验收）：
 * 验证行为：模板版本修订、使用记录、已验证包导入与紧凑版模板界面的一致链路。
 * 运行方式：需隔离服务 + 本机 Edge（puppeteer）；会产生模板与版本数据。
 * 边界：导入包必须是已验证来源；本用例会写库，仅限隔离环境。
 */
/* Template lifecycle acceptance: revisions, usage, verified package import and compact UI. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');

const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011';
const PASSWORD = process.env.TEST5_E2E_PASSWORD || 'admin123';
const ALLOW_WRITE = process.env.TEST5_E2E_ALLOW_WRITE === 'isolated';
const EDGE = process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const AXE = require.resolve('axe-core/axe.min.js');
const RUN = Date.now().toString(36);
const OUTPUT = path.resolve('.implementation/ui-results/template-lifecycle-' + RUN);
let browser;
let page;
let templateId = '';
let reportId = '';
const importedIds = [];

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

async function clickText(frame, selector, text) {
  await frame.waitForFunction((selector, text) => [...document.querySelectorAll(selector)]
    .some((node) => node.getClientRects().length && node.textContent.trim().includes(text)), {}, selector, text);
  await frame.evaluate((selector, text) => [...document.querySelectorAll(selector)]
    .find((node) => node.getClientRects().length && node.textContent.trim().includes(text)).click(), selector, text);
}

(async () => {
  assert(ALLOW_WRITE, 'Set TEST5_E2E_ALLOW_WRITE=isolated for this mutating acceptance test');
  assert(['127.0.0.1', 'localhost'].includes(new URL(BASE).hostname), 'Local acceptance only');
  const health = await fetch(BASE + '/api/health').then((response) => response.json());
  assert.equal(health.app, 'test', 'Template lifecycle acceptance requires APP_ENV=test');
  fs.mkdirSync(OUTPUT, { recursive: true });

  browser = await puppeteer.launch({ executablePath: EDGE, headless: true, args: ['--no-sandbox', '--disable-gpu'] });
  page = await browser.newPage();
  await page.setViewport({ width: 1500, height: 980, deviceScaleFactor: 1 });
  const browserErrors = [];
  page.on('pageerror', (error) => browserErrors.push('pageerror: ' + error.message));
  page.on('response', (response) => { if (response.status() >= 500) browserErrors.push(response.status() + ' ' + response.url()); });

  await page.goto(BASE, { waitUntil: 'networkidle0', timeout: 60000 });
  await page.type('#login-username', 'workspace-admin');
  await page.type('#login-password', PASSWORD);
  await Promise.all([
    page.click('#login-btn'),
    page.waitForFunction(() => Boolean(localStorage.getItem('agent_token')), { timeout: 20000 }),
  ]);

  const name = '模板生命周期验收 ' + RUN;
  const content = {
    canvas: { width: 1920, height: 1080, backgroundColor: '#071b35' },
    widgets: [{
      id: 'sales-total', component: 'JBar', componentName: '销售汇总',
      x: 80, y: 120, w: 720, h: 420,
      config: { option: { title: { text: '销售汇总' }, series: [{ type: 'bar' }] } },
    }],
  };
  const created = await api('/api/v1/report-templates', 'POST', {
    name, description: '浏览器端模板版本与使用记录验收', category: '验收', tags: ['版本', '导入导出'], content,
  });
  assert.equal(created.status, 201, JSON.stringify(created.body));
  templateId = created.body.data.id;
  const v2Content = structuredClone(created.body.data.content);
  v2Content.widgets[0].x = 120;
  const updated = await api('/api/v1/report-templates/' + templateId, 'PUT', {
    name, description: '浏览器端模板版本与使用记录验收', category: '验收', tags: ['版本', '导入导出'],
    content: v2Content, enabled: true, version: created.body.data.version,
  });
  assert.equal(updated.status, 200, JSON.stringify(updated.body));
  const report = await api('/api/v1/reports', 'POST', {
    reportName: '模板使用记录 ' + RUN, templateMode: 'manual', templateId,
  });
  assert.equal(report.status, 201, JSON.stringify(report.body));
  reportId = report.body.data.id;

  const exported = await api('/api/v1/report-templates/' + templateId + '/export');
  assert.equal(exported.status, 200, JSON.stringify(exported.body));
  const packageFile = path.join(OUTPUT, 'template-package.json');
  fs.writeFileSync(packageFile, JSON.stringify(exported.body.data, null, 2), 'utf8');

  await page.goto(BASE + '/templates', { waitUntil: 'networkidle0', timeout: 60000 });
  const frameHandle = await page.waitForSelector('#business-module-frame');
  const frame = await frameHandle.contentFrame();
  const cardSelector = `[data-template-id="${templateId}"]`;
  await frame.waitForSelector(cardSelector, { timeout: 30000 });
  const cardText = await frame.$eval(cardSelector, (node) => node.innerText);
  assert(cardText.includes('v2') && cardText.includes('1 次使用'));
  assert.equal(await frame.$$eval(cardSelector + ' .template-actions > *', (nodes) => nodes.length), 3,
    'Card must keep one compact management control, one explicit delete button and one primary use action');
  assert(await frame.$(cardSelector + ' .template-actions .el-button--danger'),
    'Every manageable template card must expose a visible delete button');

  await frame.click(cardSelector + ' summary[aria-label^="管理"]');
  await clickText(frame, cardSelector + ' .template-manage-menu [role="menuitem"]', '版本与使用');
  await frame.waitForSelector('.template-lifecycle-summary');
  assert((await frame.$eval('.template-lifecycle-summary', (node) => node.innerText)).includes('1 次实际使用'));
  await frame.waitForFunction(() => [...document.querySelectorAll('.lifecycle-table')]
    .some((table) => table.getClientRects().length && table.querySelectorAll('tbody tr').length === 2));
  assert.equal(await frame.$$eval('.lifecycle-table', (tables) => tables
    .find((table) => table.getClientRects().length).querySelectorAll('tbody tr').length), 2);
  await clickText(frame, '.el-tabs__item', '使用记录');
  await frame.waitForFunction(() => [...document.querySelectorAll('.lifecycle-table')]
    .some((table) => table.getClientRects().length && table.querySelectorAll('tbody tr').length === 1));
  const usageText = await frame.$$eval('.lifecycle-table', (tables) => tables
    .find((table) => table.getClientRects().length).innerText);
  assert(usageText.includes('v2') && usageText.includes(name));

  await frame.addScriptTag({ path: AXE });
  const violations = await frame.evaluate(async () => (await axe.run(document)).violations.map((item) => ({
    id: item.id,
    impact: item.impact,
    nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })),
  })));
  assert.deepEqual(violations, [], JSON.stringify(violations));
  await page.screenshot({ path: path.join(OUTPUT, 'template-version-and-usage.png'), fullPage: false });

  await frame.click('.el-dialog__headerbtn');

  const importResponse = page.waitForResponse((response) => response.url().endsWith('/api/v1/report-templates/import'));
  const fileInput = await frame.$('input[aria-label="导入大屏模板包"]');
  assert(fileInput, 'Template package input must be available in the normal page');
  await fileInput.uploadFile(packageFile);
  assert.equal((await importResponse).status(), 201);
  const allTemplates = (await api('/api/v1/report-templates')).body.data;
  importedIds.push(...allTemplates.filter((item) => !item.builtin && item.id !== templateId && item.name === name).map((item) => item.id));
  assert.equal(importedIds.length, 1, 'Verified package import must create exactly one custom template');
  assert.deepEqual(browserErrors, []);

  fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({
    passed: true, checks: 9, templateId, reportId, importedIds, violations, browserErrors,
  }, null, 2));
  console.log(JSON.stringify({ passed: true, checks: 9, output: OUTPUT }));
})().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
}).finally(async () => {
  if (page) {
    if (reportId) await api('/api/v1/reports/' + reportId, 'DELETE').catch(() => {});
    for (const id of importedIds) await api('/api/v1/report-templates/' + id, 'DELETE').catch(() => {});
    if (templateId) await api('/api/v1/report-templates/' + templateId, 'DELETE').catch(() => {});
  }
  if (browser) await browser.close();
});
