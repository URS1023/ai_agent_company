/*
 * 中文说明（分层授权资产与语义目录的真实浏览器验收）：
 * 验证行为：资产目录按层级与授权范围展示，语义信息（别名/术语/血缘）与实际可见资源一致。
 * 运行方式：需隔离服务（TEST5_E2E_BASE_URL，默认 8011）+ 本机 Edge + TEST5_E2E_PASSWORD，
 *   结果写入 .implementation/ui-results/asset-catalog-<run>/。
 * 边界：只读断言为主；目录数据必须来自后端授权结果，不允许前端拼接出未授权项。
 */
/* Real Edge acceptance for the hierarchical authorized asset and semantic catalog. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');

const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011';
const PASSWORD = process.env.TEST5_E2E_PASSWORD;
const EDGE = process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const OUTPUT = path.resolve('.implementation/ui-results/asset-catalog-' + Date.now().toString(36));
const checks = [], failures = [], violations = [];
let browser, page;

function pass(name, detail = {}) { checks.push({ name, ...detail }); console.log('PASS ' + name); }

async function call(method, pathname, body) {
  return page.evaluate(async ({ method, pathname, body }) => {
    const headers = {
      Authorization: 'Bearer ' + localStorage.getItem('agent_token'),
      'X-Workspace-Id': localStorage.getItem('agent_workspace_id'), Accept: 'application/json',
    };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const response = await fetch(pathname, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    const value = await response.json();
    if (!response.ok) throw new Error(response.status + ' ' + JSON.stringify(value));
    return value.success === true ? value.data : value;
  }, { method, pathname, body });
}

async function upload(name) {
  return page.evaluate(async ({ name }) => {
    const form = new FormData();
    form.append('file', new Blob(['catalog acceptance'], { type: 'text/plain' }), name);
    const response = await fetch('/api/files', { method: 'POST', headers: {
      Authorization: 'Bearer ' + localStorage.getItem('agent_token'),
      'X-Workspace-Id': localStorage.getItem('agent_workspace_id'),
    }, body: form });
    const value = await response.json();
    if (!response.ok) throw new Error(response.status + ' ' + JSON.stringify(value));
    return value;
  }, { name });
}

async function importDatasource(name) {
  return page.evaluate(async ({ name }) => {
    const form = new FormData();
    form.append('file', new Blob(['amount,device_code\n128.5,EQ-01\n'], { type: 'text/csv' }), name + '.csv');
    form.append('name', name);
    form.append('table_name', 'orders');
    form.append('encoding', 'utf-8-sig');
    const response = await fetch('/api/v1/analytics/datasources/import', { method: 'POST', headers: {
      Authorization: 'Bearer ' + localStorage.getItem('agent_token'),
      'X-Workspace-Id': localStorage.getItem('agent_workspace_id'), Accept: 'application/json',
    }, body: form });
    const value = await response.json();
    if (!response.ok) throw new Error(response.status + ' ' + JSON.stringify(value));
    return value.success === true ? value.data : value;
  }, { name });
}

async function seed() {
  const suffix = Date.now().toString(36);
  const source = await importDatasource('目录验收财务源-' + suffix);
  const file = await upload('目录验收说明-' + suffix + '.txt');
  const annotated = await call('PUT', `/api/v1/assets/catalog/datasource/${source.id}/annotation`, {
    aliases: ['财务主库-' + suffix], category: '核心数据', businessDomain: '财务',
    description: '正式经营分析口径', sensitivity: 'confidential', freshnessSeconds: 3600,
  });
  const term = await call('POST', '/api/v1/assets/terms', {
    code: 'CATALOG_GMV_' + suffix, name: '验收成交额-' + suffix, definition: '已支付订单金额合计',
    aliases: ['验收交易额'], businessDomain: '财务',
  });
  const metric = await call('POST', '/api/v1/assets/metrics', {
    code: 'CATALOG_AMOUNT_' + suffix, name: '验收订单金额-' + suffix, formula: 'SUM(orders.amount)',
    aggregation: 'sum', unit: '元', granularity: '日', dimensions: ['device_code'],
    sourceRefs: [{ type: 'datasource', id: source.id, field: 'orders.amount' }],
  });
  const relation = await call('POST', '/api/v1/assets/relations', {
    source: { type: 'datasource', id: source.id }, target: { type: 'file', id: file.id },
    relationKind: 'related_to', description: '说明文件',
  });
  return { suffix, source, file, annotated, term, metric, relation };
}

async function audit(route) {
  await page.addScriptTag({ path: require.resolve('axe-core/axe.min.js') });
  const report = await page.evaluate(async () => (await axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] },
  })).violations.map(item => ({ id: item.id, impact: item.impact, nodes: item.nodes.map(node => ({ target: node.target, summary: node.failureSummary })) })));
  violations.push(...report.map(item => ({ route, ...item })));
}

async function visit(route, selector) {
  const response = await page.goto(BASE + route, { waitUntil: 'networkidle0' });
  assert.equal(response.status(), 200, route);
  await page.waitForSelector(selector, { timeout: 25000 });
  const layout = await page.evaluate(() => ({
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    tabs: document.querySelectorAll('.catalog-tabs a').length,
    panels: document.querySelectorAll('#main-content > .panel').length,
    iframe: Boolean(document.querySelector('#business-module-frame')),
    summaryStrip: Boolean(document.querySelector('.resource-summary')),
    sidebarLinks: [...document.querySelectorAll('.enterprise-sidebar a')].filter(item => item.getClientRects().length).length,
  }));
  assert(layout.overflow <= 1, route + ' horizontal overflow');
  assert.equal(layout.tabs, 4); assert(layout.panels <= 2); assert.equal(layout.iframe, false);
  assert.equal(layout.summaryStrip, false); assert(layout.sidebarLinks <= 7);
  await audit(route);
  return layout;
}

(async () => {
  assert(PASSWORD, 'Supply TEST5_E2E_PASSWORD');
  assert.equal((await (await fetch(BASE + '/api/health')).json()).app, 'test');
  fs.mkdirSync(OUTPUT, { recursive: true });
  browser = await puppeteer.launch({ executablePath: EDGE, headless: true });
  page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 960 });
  page.on('pageerror', error => failures.push('pageerror ' + error.message));
  page.on('response', response => { if (response.status() >= 500) failures.push(response.status() + ' ' + response.url()); });
  await page.goto(BASE, { waitUntil: 'networkidle0' });
  await page.type('#login-username', 'workspace-admin');
  await page.type('#login-password', PASSWORD);
  await page.click('#login-btn');
  await page.waitForSelector('.enterprise-area-grid');
  const records = await seed();

  await visit(`/assets-catalog?tab=catalog&keyword=${encodeURIComponent('财务主库-' + records.suffix)}`, '.resource-directory');
  const searchHierarchy = await page.evaluate(sourceId => ({
    typeNodes: document.querySelectorAll('.resource-type-group').length,
    categoryNodes: document.querySelectorAll('.resource-category-group').length,
    typeOpen: document.querySelector('.resource-type-group')?.open,
    categoryOpen: document.querySelector('.resource-category-group')?.open,
    typeName: document.querySelector('.resource-type-group > summary')?.textContent,
    categoryName: document.querySelector('.resource-category-group > summary')?.textContent,
    sourceVisible: Boolean(document.querySelector(`.resource-directory-item[href*="${sourceId}"]`)),
    flatTable: Boolean(document.querySelector('.asset-directory-panel .table-scroll')),
  }), records.source.id);
  assert.equal(searchHierarchy.typeNodes, 1);
  assert.equal(searchHierarchy.categoryNodes, 1);
  assert(searchHierarchy.typeOpen && searchHierarchy.categoryOpen && searchHierarchy.sourceVisible);
  assert(searchHierarchy.typeName.includes('数据源') && searchHierarchy.categoryName.includes('核心数据'));
  assert.equal(searchHierarchy.flatTable, false);
  await page.screenshot({ path: path.join(OUTPUT, 'catalog-list.png'), fullPage: true });
  pass('Alias search opens the matching type and classification branch without a flat table');

  await page.evaluate(() => sessionStorage.clear());
  await visit('/assets-catalog?tab=catalog', '.resource-directory');
  assert.equal(await page.$$eval('.resource-type-group[open]', nodes => nodes.length), 0);
  await page.$eval('[data-resource-type="file"]', node => node.querySelector(':scope > summary').click());
  await page.waitForFunction(() => document.querySelector('[data-resource-type="file"]')?.dataset.loaded === 'true');
  const unassignedOpened = await page.evaluate(fileId => {
    const typeNode = document.querySelector('[data-resource-type="file"]');
    const category = [...typeNode.querySelectorAll('.resource-category-group')]
      .find(node => node.querySelector(':scope > summary')?.textContent.includes('未分配'));
    if (!category) return false;
    category.querySelector(':scope > summary').click();
    return Boolean(category.querySelector(`.resource-directory-item[href*="${fileId}"]`));
  }, records.file.id);
  assert.equal(unassignedOpened, true);
  await page.screenshot({ path: path.join(OUTPUT, 'catalog-unassigned.png'), fullPage: true });
  pass('Resource types start collapsed and uncategorized records live under 未分配');

  await visit(`/assets-catalog?tab=catalog&type=datasource&id=${records.source.id}`, '.catalog-detail-grid');
  assert((await page.$eval('.catalog-detail-panel', node => node.textContent)).includes('核心数据'));
  assert(await page.$('[data-edit-annotation]'));
  await page.screenshot({ path: path.join(OUTPUT, 'catalog-detail.png'), fullPage: true });
  pass('Resource detail shows source, classification and freshness without redundant cards');

  await visit('/assets-catalog?tab=terms', '[data-edit-term]');
  assert((await page.$eval('tbody', node => node.textContent)).includes(records.term.name));
  await page.screenshot({ path: path.join(OUTPUT, 'terms.png'), fullPage: true });
  pass('Business terms are directly manageable in a normal table');

  await visit('/assets-catalog?tab=metrics', '[data-edit-metric]');
  const metricText = await page.$eval('tbody', node => node.textContent);
  assert(metricText.includes(records.metric.name) && metricText.includes('orders.amount'));
  await page.click('[data-new-metric]');
  await page.waitForSelector('#platform-dialog[open] [data-source-ref-row] select');
  const metricSourceGroups = await page.$$eval('#platform-dialog [data-source-ref-row] select optgroup', nodes => nodes.map(node => node.label));
  assert(metricSourceGroups.includes('数据源 / 核心数据'));
  assert(metricSourceGroups.includes('资料文件 / 未分配'));
  assert(await page.$$eval('#platform-dialog [data-source-ref-row] select option[value]:not([value=""])', nodes => nodes.every(node => node.parentElement.tagName === 'OPTGROUP')));
  await page.click('#platform-dialog [data-dialog-close]');
  await page.screenshot({ path: path.join(OUTPUT, 'metrics.png'), fullPage: true });
  pass('Metric source selection uses the same type and classification hierarchy');

  await visit('/assets-catalog?tab=relations', '[data-edit-relation]');
  const relationText = await page.$eval('tbody', node => node.textContent);
  assert(relationText.includes(records.source.name) && relationText.includes(records.file.name));
  await page.click('[data-new-relation]');
  await page.waitForSelector('#platform-dialog[open] [name="source"] optgroup');
  const relationSourceGroups = await page.$$eval('#platform-dialog [name="source"] optgroup', nodes => nodes.map(node => node.label));
  assert(relationSourceGroups.includes('数据源 / 核心数据'));
  assert(relationSourceGroups.includes('资料文件 / 未分配'));
  await page.click('#platform-dialog [data-dialog-close]');
  await page.screenshot({ path: path.join(OUTPUT, 'relations.png'), fullPage: true });
  pass('Resource relation selection reuses the hierarchical authorized catalog');

  await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 1 });
  await page.evaluate(() => sessionStorage.clear());
  await visit('/assets-catalog?tab=catalog', '.resource-directory');
  const mobileDirectory = await page.evaluate(() => ({
    columns: getComputedStyle(document.querySelector('.resource-directory')).gridTemplateColumns.split(' ').length,
    overflow: document.documentElement.scrollWidth - innerWidth,
    open: document.querySelectorAll('.resource-type-group[open]').length,
  }));
  assert.equal(mobileDirectory.columns, 1); assert(mobileDirectory.overflow <= 1); assert.equal(mobileDirectory.open, 0);
  await page.screenshot({ path: path.join(OUTPUT, 'catalog-mobile.png'), fullPage: true });
  pass('Mobile catalog keeps one compact collapsed column without horizontal overflow');

  await visit(`/assets-catalog?tab=catalog&type=datasource&id=${records.source.id}`, '.catalog-detail-grid');
  await page.click('[data-edit-annotation]');
  await page.waitForSelector('#platform-dialog[open]');
  const mobile = await page.evaluate(() => {
    const dialog = document.querySelector('#platform-dialog').getBoundingClientRect();
    return { overflow: document.documentElement.scrollWidth - innerWidth, left: dialog.left, right: dialog.right,
      primaryLinks: [...document.querySelectorAll('.enterprise-primary a')].filter(item => item.getClientRects().length).length };
  });
  assert(mobile.overflow <= 1 && mobile.left >= 0 && mobile.right <= 390); assert.equal(mobile.primaryLinks, 0);
  await page.screenshot({ path: path.join(OUTPUT, 'annotation-mobile.png'), fullPage: true });
  await audit('/assets-catalog-mobile-dialog');
  pass('Mobile resource detail and annotation dialog fit without duplicate navigation');

  assert.deepEqual(failures, []);
  assert.deepEqual(violations, []);
  const result = { checks, failures, accessibilityFindings: violations, records: {
    datasourceId: records.source.id, fileId: records.file.id, termId: records.term.id,
    metricId: records.metric.id, relationId: records.relation.id,
  } };
  fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify(result, null, 2));
  console.log('RESULT_DIR ' + OUTPUT);
  console.log('ACCESSIBILITY_FINDINGS ' + violations.length);
})().catch(error => {
  failures.push(error.stack || error.message);
  if (fs.existsSync(OUTPUT)) fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({ checks, failures, accessibilityFindings: violations }, null, 2));
  console.error(error); process.exitCode = 1;
}).finally(async () => { if (browser) await browser.close(); });
