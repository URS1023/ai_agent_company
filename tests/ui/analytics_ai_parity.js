/*
 * 中文说明（数据分析与 AI 结果一致性验收，仅对隔离测试服务运行）：
 * 验证行为：聚焦参考实现的分析流程在新平台上的展示与分析结果是否一致。
 * 运行方式：需隔离服务（TEST5_E2E_BASE_URL，默认 8011）+ 本机 Edge + TEST5_E2E_PASSWORD，
 *   结果写入 .implementation/ui-results/analytics-ai-parity-<run>/。
 * 边界：只针对 APP_ENV=test 的隔离实例；页面控制台错误会被收集用于失败定位。
 */
/* Focused Lynx-analysis parity flow against an explicitly isolated test server. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');

const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011';
const PASSWORD = process.env.TEST5_E2E_PASSWORD;
const RUN = Date.now().toString(36);
const OUTPUT = path.resolve('.implementation/ui-results/analytics-ai-parity-' + RUN);
let browser;
let page;
let frame;
const serverErrors = [];

async function visibleByText(target, selector, text) {
  for (const handle of await target.$$(selector)) {
    if (await handle.evaluate((element, expected) =>
      element.getClientRects().length > 0 && element.textContent.trim() === expected, text)) return handle;
  }
  return null;
}

async function clickText(target, selector, text) {
  const handle = await visibleByText(target, selector, text);
  assert(handle, `Visible ${selector} not found: ${text}`);
  await handle.click();
}

async function fillForm(label, value) {
  const handle = await frame.evaluateHandle((label) => {
    const items = [...document.querySelectorAll('.el-dialog .el-form-item')]
      .filter(item => item.getClientRects().length > 0);
    const item = items.find(item => item.querySelector('.el-form-item__label')
      ?.textContent.replace('*', '').trim() === label);
    return item?.querySelector('input:not([type=file]), textarea');
  }, label);
  const element = handle.asElement();
  assert(element, 'Form field not found: ' + label);
  await element.click({ clickCount: 3 });
  await page.keyboard.down('Control');
  await page.keyboard.press('KeyA');
  await page.keyboard.up('Control');
  await element.type(value);
}

async function visit(route, selector) {
  const response = await page.goto(BASE + route, { waitUntil: 'networkidle0' });
  assert.equal(response.status(), 200, route);
  await page.waitForSelector('#business-module-frame');
  frame = await (await page.$('#business-module-frame')).contentFrame();
  await frame.waitForSelector(selector, { visible: true, timeout: 25000 });
}

async function importFixture() {
  const sourceName = '分析能力验收 ' + RUN;
  const tableName = 'analysis_series_' + RUN;
  const csv = [
    'period,amount,department',
    '2026-01-01,100,设备',
    '2026-02-01,120,财务',
    '2026-03-01,140,设备',
    '2026-04-01,160,财务',
    '2026-05-01,180,设备',
    '2026-06-01,200,财务',
  ].join('\n');
  const result = await page.evaluate(async ({ sourceName, tableName, csv }) => {
    const form = new FormData();
    form.append('name', sourceName);
    form.append('table_name', tableName);
    form.append('file', new File([new TextEncoder().encode(csv)], 'series.csv', { type: 'text/csv' }));
    const response = await fetch('/api/v1/analytics/datasources/import', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer ' + localStorage.getItem('agent_token'),
        'X-Workspace-Id': localStorage.getItem('agent_workspace_id'),
      },
      body: form,
    });
    return { status: response.status, body: await response.json() };
  }, { sourceName, tableName, csv });
  assert.equal(result.status, 201, JSON.stringify(result.body));
  return { source: result.body.data, tableName };
}

(async () => {
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, 'isolated');
  assert(PASSWORD, 'TEST5_E2E_PASSWORD is required');
  assert(['127.0.0.1', 'localhost'].includes(new URL(BASE).hostname));
  assert.equal((await (await fetch(BASE + '/api/health')).json()).app, 'test');
  fs.mkdirSync(OUTPUT, { recursive: true });

  browser = await puppeteer.launch({
    executablePath: process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    headless: true,
  });
  page = await browser.newPage();
  page.setDefaultTimeout(15000);
  await page.setViewport({ width: 1440, height: 960 });
  page.on('response', response => {
    if (response.status() >= 500) serverErrors.push(response.status() + ' ' + response.url());
  });
  page.on('pageerror', error => serverErrors.push('page: ' + error.message));
  await page.goto(BASE, { waitUntil: 'networkidle0' });
  await page.type('#login-username', 'workspace-admin');
  await page.type('#login-password', PASSWORD);
  await page.click('#login-btn');
  await page.waitForSelector('.overview-intro');

  const { source, tableName } = await importFixture();
  assert(source.autoDescription || source.description, 'Imported source must receive an automatic business description');
  assert.equal(source.metadataStatus, 'ready');
  assert(['ready', 'keyword_only'].includes(source.semanticIndexStatus));

  await visit('/analytics?datasourceId=' + source.id, '.intelligent-query');
  await frame.waitForFunction(() => document.querySelector('.connection-status')?.textContent.includes('表结构已加载'));
  const sourceContext = await frame.$eval('.source-context', element => element.textContent);
  assert(sourceContext.includes('1 张业务表'));
  assert(sourceContext.includes('索引'));
  await frame.click('#query-source');
  await frame.waitForSelector('.query-source-option', { visible: true });
  const optionText = await frame.$$eval('.query-source-option', (elements, sourceName) =>
    elements.find(element => element.textContent.includes(sourceName))?.textContent || '', source.name);
  assert(optionText.includes(source.name));
  assert(optionText.includes(source.description || source.autoDescription));
  await page.keyboard.press('Escape');

  await frame.click('#query-mode');
  await frame.waitForFunction(() => [...document.querySelectorAll('.el-select-dropdown__item')]
    .some(item => item.getClientRects().length && item.textContent.trim() === '手工 SQL 查询'));
  await frame.evaluate(() => [...document.querySelectorAll('.el-select-dropdown__item')]
    .find(item => item.getClientRects().length && item.textContent.trim() === '手工 SQL 查询').click());
  const sql = `SELECT DATE(period) AS period, CAST(amount AS REAL) AS amount FROM "${tableName}" ORDER BY period`;
  const queryInput = await frame.$('.query-card textarea');
  await queryInput.type(sql);
  const queryResponse = page.waitForResponse(response => response.url().endsWith('/api/v1/analytics/query')
    && response.request().method() === 'POST');
  await clickText(frame, 'button', '执行查询');
  const response = await queryResponse;
  assert.equal(response.status(), 200);
  const queryResult = (await response.json()).data;
  assert.equal(queryResult.enhancedAnalysis.timeDimension, 'period');
  assert.equal(queryResult.enhancedAnalysis.metrics[0].trend.direction, 'up');
  assert(queryResult.enhancedAnalysis.metrics[0].projection);
  await frame.waitForSelector('.query-result-panel');
  await clickText(frame, '[role=tab]', '统计与洞察');
  await frame.waitForSelector('.enhanced-card', { visible: true });
  const enhancedText = await frame.$eval('.enhanced-card', element => element.textContent);
  assert(enhancedText.includes('上升'));
  assert(enhancedText.includes('下一观测点'));
  assert(enhancedText.includes('不代表预算或正式业务预测'));
  await page.screenshot({ path: path.join(OUTPUT, 'enhanced-analysis.png') });

  await clickText(frame, 'button', '查询模板');
  await frame.waitForSelector('.template-toolbar', { visible: true });
  const initialDialogText = await frame.$eval('.el-dialog', element => element.textContent);
  assert(!/t_user|\busers\b|\borders\b|\btransactions\b|\bproducts\b/i.test(initialDialogText));
  const addButton = await visibleByText(frame, 'button', '新增模板')
    || await visibleByText(frame, 'button', '新增第一个模板');
  assert(addButton, 'Template create button missing');
  await addButton.click();
  console.log('PHASE template editor opened');
  await fillForm('模板名称', '月度趋势 ' + RUN);
  await fillForm('分类', '财务');
  await clickText(frame, '.el-radio-button', '参数化 SQL');
  await fillForm('只读 SQL', sql);
  const createResponse = page.waitForResponse(item => item.url().endsWith('/api/v1/analytics/query-templates')
    && item.request().method() === 'POST');
  await clickText(frame, 'button', '保存');
  assert.equal((await createResponse).status(), 201);
  await frame.waitForFunction(name => document.body.innerText.includes(name), { timeout: 10000 }, '月度趋势 ' + RUN);
  console.log('PHASE template created and rendered');

  let templateItem = await frame.evaluateHandle(name => [...document.querySelectorAll('.template-item')]
    .find(item => item.querySelector('strong')?.textContent === name), '月度趋势 ' + RUN);
  const editButton = await templateItem.asElement().$('button[aria-label="编辑模板"]');
  assert(editButton, 'Template edit button missing');
  await editButton.evaluate(element => element.click());
  await frame.waitForFunction(() => [...document.querySelectorAll('.el-dialog')]
    .some(dialog => dialog.getClientRects().length && dialog.textContent.includes('编辑查询模板')));
  console.log('PHASE template edit dialog opened');
  await fillForm('模板名称', '月度趋势已更新 ' + RUN);
  const updateResponse = page.waitForResponse(item => /\/api\/v1\/analytics\/query-templates\/[A-Za-z0-9_-]+$/.test(new URL(item.url()).pathname)
    && item.request().method() === 'PUT');
  console.log('PHASE template update submitting');
  await clickText(frame, 'button', '保存');
  assert.equal((await updateResponse).status(), 200);
  await frame.waitForFunction(name => document.body.innerText.includes(name), { timeout: 10000 }, '月度趋势已更新 ' + RUN);
  console.log('PHASE template updated and rendered');

  templateItem = await frame.evaluateHandle(name => [...document.querySelectorAll('.template-item')]
    .find(item => item.querySelector('strong')?.textContent === name), '月度趋势已更新 ' + RUN);
  const useResponse = page.waitForResponse(item => /\/api\/v1\/analytics\/query-templates\/[A-Za-z0-9_-]+\/use$/.test(new URL(item.url()).pathname)
    && item.request().method() === 'POST');
  const useButton = await templateItem.asElement().$$('button').then(async buttons => {
    for (const button of buttons) if (await button.evaluate(element => element.textContent.trim() === '使用')) return button;
    return null;
  });
  assert(useButton, 'Template use button missing');
  await useButton.evaluate(element => element.click());
  assert.equal((await useResponse).status(), 200);
  await frame.waitForSelector('.template-toolbar', { hidden: true });
  assert.equal(await frame.$eval('.query-card textarea', element => element.value), sql);
  assert((await frame.$eval('.query-mode-row', element => element.textContent)).includes('手工 SQL'));

  await clickText(frame, 'button', '查询模板');
  await frame.waitForSelector('.template-toolbar', { visible: true });
  templateItem = await frame.evaluateHandle(name => [...document.querySelectorAll('.template-item')]
    .find(item => item.querySelector('strong')?.textContent === name), '月度趋势已更新 ' + RUN);
  await templateItem.asElement().$('button[aria-label="删除模板"]').then(button => button.evaluate(element => element.click()));
  await frame.waitForSelector('.el-message-box', { visible: true });
  const deleteResponse = page.waitForResponse(item => /\/api\/v1\/analytics\/query-templates\/[A-Za-z0-9_-]+$/.test(new URL(item.url()).pathname)
    && item.request().method() === 'DELETE');
  await clickText(frame, '.el-message-box button', '删除');
  assert.equal((await deleteResponse).status(), 200);
  await frame.waitForFunction(name => ![...document.querySelectorAll('.template-item strong')]
    .some(item => item.textContent === name), {}, '月度趋势已更新 ' + RUN);
  await page.screenshot({ path: path.join(OUTPUT, 'query-template-management.png') });

  assert.deepEqual(serverErrors, []);
  fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({
    ok: true,
    sourceId: source.id,
    queryId: queryResult.queryId,
    checks: [
      'automatic datasource description and semantic index visible',
      'date-backed trend and cautious projection rendered',
      'persisted query template create/update/use/delete',
      'no static demo table templates',
      'no 5xx or page errors',
    ],
  }, null, 2));
  console.log('PASS datasource description and semantic index');
  console.log('PASS enhanced trend and cautious projection');
  console.log('PASS query template CRUD and reuse without demo SQL');
  console.log('RESULT_DIR ' + OUTPUT);
})().catch(async error => {
  console.error(error.stack || error);
  if (page && !page.isClosed()) await page.screenshot({ path: path.join(OUTPUT, 'failure.png') }).catch(() => {});
  if (fs.existsSync(OUTPUT)) fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({ ok: false, error: String(error), serverErrors }, null, 2));
  process.exitCode = 1;
}).finally(async () => {
  if (browser) await browser.close();
});
