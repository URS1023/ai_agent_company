/*
 * 中文说明（文档/PPT 中心的真实浏览器验收）：
 * 验证行为：紧凑版文档中心里模板选择、生成与结果下载的完整交互。
 * 运行方式：需隔离服务（TEST5_E2E_BASE_URL，默认 8011）+ 本机 Edge + TEST5_E2E_PASSWORD。
 * 边界：会真实生成文档产物（写隔离环境存储），依赖模板与生成能力可用。
 */
/* Real Edge acceptance for the compact document and PPT center. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');

const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011';
const PASSWORD = process.env.TEST5_E2E_PASSWORD;
const EDGE = process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const OUTPUT = path.resolve('.implementation/ui-results/documents-' + Date.now().toString(36));
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

function richPresentation(title) {
  return { title, subtitle: '浏览器真实产物验收', slides: [
    { title: '经营趋势', layout: 'chart', key_message: '收入与设备效率同步改善，趋势已依据验收数据形成。', items: [{
      type: 'chart', chart_type: 'column', categories: ['一月', '二月', '三月'],
      series: [{ name: '收入', values: [120, 138, 156] }], source: '浏览器验收数据',
    }] },
    { title: '设备状态', layout: 'cards', key_message: '设备总体稳定，异常波动已明确责任和复核时间。', items: [
      { title: '运行', description: '关键设备连续运行时间符合本月生产计划要求。' },
      { title: '温度', description: '短时温度波动已进入下一班次重点复核清单。' },
      { title: '维护', description: '预防性维护按期完成，备件库存满足近期计划。' },
    ] },
    { title: '下月行动', layout: 'content', key_message: '围绕效率、可靠性和成本三个方向逐周复盘。', items: [
      { title: '效率', description: '优化换线节拍并追踪单位工时的产出变化。' },
      { title: '可靠性', description: '增加异常设备巡检频率并指定人工复核责任人。' },
      { title: '成本', description: '按设备归集能耗与备件消耗并分析趋势。' },
    ] },
  ] };
}

async function seed() {
  const suffix = Date.now().toString(36);
  const doc = await call('POST', '/api/v1/documents/generations', {
    name: '浏览器验收设备报告-' + suffix, outputType: 'document', mode: 'manual', spec: {
      title: '浏览器验收设备报告-' + suffix,
      sections: [{ title: '运行结论', blocks: [
        { type: 'p', text: '设备运行稳定，关键异常已完成复核。' },
        { type: 'p', text: '本报告用于验证真实 Word 产物和预览界面。' },
      ] }],
    },
  });
  const completedDoc = await call('POST', `/api/v1/documents/generations/${doc.id}/execute`, { version: doc.version });
  const ppt = await call('POST', '/api/v1/documents/generations', {
    name: '浏览器验收经营汇报-' + suffix, outputType: 'presentation', mode: 'manual',
    templateId: 'executive-ivory', pageCount: 5, spec: richPresentation('浏览器验收经营汇报-' + suffix),
  });
  const completedPpt = await call('POST', `/api/v1/documents/generations/${ppt.id}/execute`, { version: ppt.version });
  return { suffix, doc: completedDoc, ppt: completedPpt };
}

async function audit(route) {
  await page.addScriptTag({ path: require.resolve('axe-core/axe.min.js') });
  const report = await page.evaluate(async () => (await axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] },
  })).violations.map(item => ({ id: item.id, impact: item.impact, nodes: item.nodes.map(node => ({ target: node.target, summary: node.failureSummary })) })));
  violations.push(...report.map(item => ({ route, ...item })));
}

async function visit(route, selector) {
  const response = await page.goto(BASE + route, { waitUntil: 'networkidle0', timeout: 90000 });
  assert.equal(response.status(), 200, route);
  await page.waitForSelector(selector, { timeout: 30000 });
  const layout = await page.evaluate(() => ({
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    tabs: document.querySelectorAll('.catalog-tabs a').length,
    directPanels: document.querySelectorAll('#main-content > .panel').length,
    iframe: Boolean(document.querySelector('#business-module-frame')),
    summaryStrip: Boolean(document.querySelector('.resource-summary')),
    hero: Boolean(document.querySelector('.overview-intro, .metric-strip')),
    sidebarLinks: [...document.querySelectorAll('.enterprise-sidebar a')].filter(item => item.getClientRects().length).length,
  }));
  assert(layout.overflow <= 1, route + ' horizontal overflow');
  assert.equal(layout.tabs, 2); assert(layout.directPanels <= 1); assert.equal(layout.iframe, false);
  assert.equal(layout.summaryStrip, false); assert.equal(layout.hero, false); assert(layout.sidebarLinks <= 7);
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

  await visit(`/documents?tab=generations&keyword=${encodeURIComponent(records.suffix)}`, '.table-scroll');
  assert.equal(await page.$$eval('tbody tr', rows => rows.length), 2);
  const listText = await page.$eval('tbody', node => node.textContent);
  assert(listText.includes(records.doc.name) && listText.includes(records.ppt.name));
  await page.screenshot({ path: path.join(OUTPUT, 'generation-list.png'), fullPage: true });
  pass('Generation list is one compact table with no hero, summary strip or iframe');

  await visit(`/documents?tab=generations&id=${records.ppt.id}`, '.catalog-detail-panel');
  await page.waitForFunction(() => {
    const image = document.querySelector('[data-generation-preview]');
    return image && image.complete && image.naturalWidth > 0;
  }, { timeout: 90000 });
  const detail = await page.evaluate(() => ({
    panels: document.querySelectorAll('#main-content > .panel').length,
    previewWidth: document.querySelector('[data-generation-preview]').naturalWidth,
    historyOpen: document.querySelector('.resource-history')?.open,
    buttons: [...document.querySelectorAll('.heading-actions .button')].map(node => node.textContent.trim()),
  }));
  assert.equal(detail.panels, 1); assert(detail.previewWidth > 0); assert.equal(detail.historyOpen, false);
  assert(detail.buttons.includes('下载') && detail.buttons.includes('复制'));
  await page.screenshot({ path: path.join(OUTPUT, 'ppt-detail.png'), fullPage: true });
  pass('Completed PPT detail keeps one panel and loads a real authenticated preview');

  await visit('/documents?tab=templates', '.document-template-grid');
  const templates = await page.evaluate(() => {
    const nodes = [...document.querySelectorAll('.document-template-item')];
    return { count: nodes.length, maxHeight: Math.max(...nodes.map(node => node.getBoundingClientRect().height)), selected: nodes.filter(node => node.matches('[aria-current], .selected')).length };
  });
  assert(templates.count >= 20); assert(templates.maxHeight < 210); assert.equal(templates.selected, 0);
  await page.screenshot({ path: path.join(OUTPUT, 'templates.png'), fullPage: true });
  pass('Twenty built-in templates use a compact thumbnail grid and none is selected by default');

  await visit('/documents?tab=generations', '[data-new-generation]');
  await page.click('[data-new-generation]');
  await page.waitForSelector('#platform-dialog[open]');
  await page.select('#platform-dialog [name="outputType"]', 'presentation');
  const formState = await page.evaluate(() => ({
    templateValue: document.querySelector('#platform-dialog [name="templateId"]').value,
    pptVisible: [...document.querySelectorAll('#platform-dialog [data-ppt-option]')].every(node => !node.hidden),
    bodyHidden: document.querySelector('#platform-dialog [data-document-option]').hidden,
    sourceCollapsed: !document.querySelector('#platform-dialog .document-sources').open,
    sourceGroups: [...document.querySelectorAll('#platform-dialog [data-document-source] select optgroup')].map(node => node.label),
    ungroupedSources: [...document.querySelectorAll('#platform-dialog [data-document-source] select option[value]:not([value=""])')]
      .filter(node => node.parentElement.tagName !== 'OPTGROUP').length,
  }));
  assert.equal(formState.templateValue, ''); assert(formState.pptVisible && formState.bodyHidden && formState.sourceCollapsed);
  assert(formState.sourceGroups.length > 0 && formState.sourceGroups.every(label => label.includes(' / ')));
  assert.equal(formState.ungroupedSources, 0);
  await page.screenshot({ path: path.join(OUTPUT, 'create-dialog.png'), fullPage: true });
  await audit('/documents-create-dialog');
  pass('Create dialog keeps optional fields folded and groups business sources by type and classification');
  await page.click('#platform-dialog [data-dialog-close]');

  await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 1 });
  await visit('/documents?tab=generations', '[data-new-generation]');
  await page.click('[data-new-generation]');
  await page.waitForSelector('#platform-dialog[open]');
  const mobile = await page.evaluate(() => {
    const rect = document.querySelector('#platform-dialog').getBoundingClientRect();
    return { overflow: document.documentElement.scrollWidth - innerWidth, left: rect.left, right: rect.right,
      primaryLinks: [...document.querySelectorAll('.enterprise-primary a')].filter(node => node.getClientRects().length).length };
  });
  assert(mobile.overflow <= 1 && mobile.left >= 0 && mobile.right <= 390); assert.equal(mobile.primaryLinks, 0);
  await page.screenshot({ path: path.join(OUTPUT, 'create-mobile.png'), fullPage: true });
  await audit('/documents-mobile-dialog');
  pass('Mobile generation dialog fits the viewport without duplicate navigation');

  assert.deepEqual(failures, []);
  assert.deepEqual(violations, []);
  fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({ checks, failures, accessibilityFindings: violations,
    records: { documentId: records.doc.id, presentationId: records.ppt.id } }, null, 2));
  console.log('RESULT_DIR ' + OUTPUT);
  console.log('ACCESSIBILITY_FINDINGS ' + violations.length);
})().catch(error => {
  failures.push(error.stack || error.message);
  if (fs.existsSync(OUTPUT)) fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({ checks, failures, accessibilityFindings: violations }, null, 2));
  console.error(error); process.exitCode = 1;
}).finally(async () => { if (browser) await browser.close(); });
