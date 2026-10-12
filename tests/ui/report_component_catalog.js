/*
 * 中文说明（报表组件目录的浏览器验收）：
 * 验证行为：目录口径（135 个源类型 / 111 个可渲染叶子类型）正确；为每种源叶子类型渲染一个实例；
 *   可展开全部补充业务分类；并确认图库预览不会把演示值写入报表。
 * 约束：预览不落库——这条是防止"看一眼示例就污染报表"的关键边界。
 *
 * Browser acceptance for the complete Lynx report component catalogue.
 *
 * It verifies the corrected 135/111 catalogue contract, renders one instance
 * of every source leaf type, opens every supplemental business category, and
 * confirms that a gallery preview never persists its demo values to a report.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');

const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011';
const PASSWORD = process.env.TEST5_E2E_PASSWORD || 'admin123';
const ALLOW_NATIVE = process.env.TEST5_E2E_ALLOW_NATIVE === '1';
const EDGE = process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const RUN = Date.now().toString(36);
const OUTPUT = path.resolve('.implementation/ui-results/report-component-catalog-' + RUN);
let browser;
let page;
let reportId = '';

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
    let payload = null;
    try { payload = text ? JSON.parse(text) : null; } catch { payload = { raw: text }; }
    return { status: response.status, body: payload };
  }, url, method, body);
}

function galleryContent(componentTypes) {
  const columns = 5;
  const widgetWidth = 340;
  const widgetHeight = 180;
  const gapX = 34;
  const gapY = 34;
  const margin = 26;
  const rows = Math.ceil(componentTypes.length / columns);
  const sample = [
    { name: '华北', value: 82, description: '经营数据', date: '2026-09-08', time: '09:30' },
    { name: '华东', value: 68, description: '质量数据', date: '2026-09-18', time: '10:20' },
    { name: '华南', value: 55, description: '设备数据', date: '2026-09-26', time: '11:10' },
  ];
  return {
    canvas: {
      width: 1920,
      height: margin * 2 + rows * (widgetHeight + gapY),
      backgroundColor: '#020b16',
    },
    widgets: componentTypes.map((component, index) => ({
      id: 'catalog-' + String(index + 1).padStart(3, '0') + '-' + component,
      i: 'catalog-' + String(index + 1).padStart(3, '0') + '-' + component,
      component,
      componentName: component,
      x: margin + (index % columns) * (widgetWidth + gapX),
      y: margin + Math.floor(index / columns) * (widgetHeight + gapY),
      w: widgetWidth,
      h: widgetHeight,
      orderNum: index + 1,
      visible: true,
      locked: false,
      rotation: 0,
      config: {
        dataType: 1,
        chartData: JSON.stringify(sample),
        chartDataParsed: sample,
        option: {
          type: '7',
          title: { show: true, text: component },
          body: { text: component, color: '#d9efff', fontSize: 18 },
          header: [{ label: '区域', key: 'name' }, { label: '数值', key: 'value' }],
          series: [{}],
          color: ['#38bdf8', '#22d3a6', '#8b7cf6'],
        },
      },
    })),
  };
}

function businessShowcaseContent(items) {
  const content = galleryContent(items.map(item => item.compType));
  const columns = 4;
  const width = 450;
  const height = 230;
  const gap = 20;
  const margin = 20;
  content.canvas.height = 1080;
  content.widgets.forEach((widget, index) => {
    widget.id = 'business-source-' + String(index + 1).padStart(2, '0');
    widget.i = widget.id;
    widget.componentName = items[index].name;
    widget.x = margin + (index % columns) * (width + gap);
    widget.y = margin + Math.floor(index / columns) * (height + gap);
    widget.w = width;
    widget.h = height;
  });
  return content;
}

(async () => {
  assert(['127.0.0.1', 'localhost'].includes(new URL(BASE).hostname), 'Local acceptance only');
  const health = await fetch(BASE + '/api/health').then(response => response.json());
  assert(health.app === 'test' || (ALLOW_NATIVE && health.app === 'native-dev'),
    'Set TEST5_E2E_ALLOW_NATIVE=1 only for the local native development server');
  fs.mkdirSync(OUTPUT, { recursive: true });

  browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: true,
    args: ['--no-sandbox', '--disable-gpu'],
  });
  page = await browser.newPage();
  await page.setViewport({ width: 1680, height: 1050, deviceScaleFactor: 1 });
  const browserErrors = [];
  page.on('pageerror', error => browserErrors.push('pageerror: ' + error.message));
  page.on('console', message => {
    if (message.type() === 'error') browserErrors.push('console: ' + message.text());
  });
  page.on('response', response => {
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

  const catalogResponse = await api('/api/v1/reports/component-catalog');
  assert.equal(catalogResponse.status, 200, JSON.stringify(catalogResponse.body));
  const catalog = catalogResponse.body.data;
  const meta = catalog.meta;
  assert.equal(meta.sourceDistinctTypeIdentifiers, 135);
  assert.equal(meta.sourceCategoryOnlyTypeIdentifiers, 24);
  assert.equal(meta.sourceRenderableLeafTypes, 111);
  assert.equal(meta.primaryRenderableLeafTypes, 98);
  assert.equal(meta.supplementalRenderableLeafTypes, 13);
  assert.equal(meta.supplementalLeafRecords, 16);
  assert.equal(catalog.toolbarGroups.length, 9);
  assert.equal(new Set(meta.sourceRenderableLeafTypeNames).size, 111);
  assert(meta.sourceCategoryOnlyTypeNames.every(type => !meta.sourceRenderableLeafTypeNames.includes(type)));

  const createResponse = await api('/api/v1/reports', 'POST', {
    reportName: 'Lynx 全组件浏览器验收 ' + RUN,
    description: '自动验收后删除',
    templateMode: 'none',
    content: galleryContent(meta.sourceRenderableLeafTypeNames),
  });
  assert.equal(createResponse.status, 201, JSON.stringify(createResponse.body));
  reportId = createResponse.body.data.id;

  await page.goto(BASE + '/report/designer/' + reportId, { waitUntil: 'networkidle0', timeout: 60000 });
  const frameHandle = await page.waitForSelector('#business-module-frame');
  const frame = await frameHandle.contentFrame();
  await frame.waitForSelector('.jimu-toolbar', { visible: true, timeout: 30000 });
  await frame.waitForFunction(expected => document.querySelectorAll('.screen-widget').length === expected,
    { timeout: 30000 }, meta.sourceRenderableLeafTypes);
  await new Promise(resolve => setTimeout(resolve, 2500));

  const renderState = await frame.evaluate((componentTypes, supplementalTypes) => {
    const renderedTypes = [...document.querySelectorAll('.screen-widget')].map(node => node.dataset.component);
    return {
      widgetCount: renderedTypes.length,
      distinctTypes: new Set(renderedTypes).size,
      missingTypes: componentTypes.filter(type => !renderedTypes.includes(type)),
      genericFallbacks: document.querySelectorAll('.generic-widget').length,
      businessRenderers: document.querySelectorAll('[data-business-component]').length,
      missingSupplementalRenderers: supplementalTypes.filter(type =>
        !document.querySelector(`[data-component="${CSS.escape(type)}"] [data-business-component="${CSS.escape(type)}"]`)),
      chartCanvases: document.querySelectorAll('.jimu-chart canvas').length,
      rendererCount: document.querySelectorAll('.widget-renderer').length,
      dataErrors: [...document.querySelectorAll('.data-load-error')].map(node => node.textContent.trim()),
      zeroSizedWidgets: [...document.querySelectorAll('.screen-widget')].filter(node => {
        const rect = node.getBoundingClientRect();
        return rect.width <= 0 || rect.height <= 0;
      }).length,
    };
  }, meta.sourceRenderableLeafTypeNames, meta.supplementalRenderableLeafTypeNames);
  assert.equal(renderState.widgetCount, 111);
  assert.equal(renderState.distinctTypes, 111);
  assert.deepEqual(renderState.missingTypes, []);
  assert.equal(renderState.genericFallbacks, 0);
  assert.deepEqual(renderState.missingSupplementalRenderers, []);
  assert.equal(renderState.rendererCount, 111);
  assert.deepEqual(renderState.dataErrors, []);
  assert.equal(renderState.zeroSizedWidgets, 0);

  await frame.click('button[title="业务组件"]');
  await frame.waitForSelector('.component-library', { visible: true });
  const categoryNames = await frame.$$eval('.component-library .category-list button', nodes =>
    nodes.map(node => node.querySelector('span')?.textContent.trim()));
  assert.deepEqual(categoryNames, ['首页', '常用', '布局', '数据', '个性']);

  const categoryState = [];
  for (let index = 0; index < categoryNames.length; index += 1) {
    const selector = `.component-library .category-list button:nth-child(${index + 1})`;
    await frame.hover(selector);
    await frame.waitForFunction(name => document.querySelector('.component-library .category-list button.active span')?.textContent.trim() === name,
      { timeout: 5000 }, categoryNames[index]);
    const state = await frame.evaluate(() => ({
      name: document.querySelector('.component-library .category-list button.active span')?.textContent.trim(),
      count: document.querySelectorAll('.component-library .component-item').length,
      genericFallbacks: document.querySelectorAll('.component-library .generic-widget').length,
      components: [...document.querySelectorAll('.component-library .component-item')].map(node => node.dataset.component),
    }));
    assert.equal(state.genericFallbacks, 0, state.name + ' still contains generic previews');
    assert(state.count > 0, state.name + ' must contain source records');
    categoryState.push(state);
  }
  assert.equal(categoryState.reduce((total, item) => total + item.count, 0), 16);
  assert.equal(new Set(categoryState.flatMap(item => item.components)).size, 13);

  await frame.hover('.component-library .category-list button:nth-child(1)');
  await frame.waitForSelector('.component-library .component-item[data-component="JGrowCard"]');
  await page.screenshot({ path: path.join(OUTPUT, 'business-component-library.png'), fullPage: false });
  await frame.click('.component-library .component-item[data-component="JGrowCard"]');
  await frame.waitForFunction(() => document.querySelectorAll('.screen-widget').length === 112);

  const saveResponsePromise = page.waitForResponse(response =>
    response.url().endsWith('/api/v1/reports/' + reportId) && response.request().method() === 'PUT');
  await frame.click('button[title="保存"]');
  const saveResponse = await saveResponsePromise;
  assert.equal(saveResponse.status(), 200, await saveResponse.text());
  const reopened = await api('/api/v1/reports/' + reportId);
  assert.equal(reopened.status, 200);
  const savedContent = JSON.parse(reopened.body.data.content);
  const added = savedContent.widgets.find(widget => String(widget.id).startsWith('local-') && widget.component === 'JGrowCard');
  assert(added, 'The gallery component must be added to the real report');
  assert.equal(added.config.dataStatus, 'unbound');
  assert.equal(added.config.chartData, '[]');
  assert.deepEqual(added.config.chartDataParsed, []);
  assert(!JSON.stringify(added).includes('经营收入'), 'Preview demo values must not persist');

  await page.screenshot({ path: path.join(OUTPUT, 'all-111-components-designer.png'), fullPage: false });
  const businessGroup = catalog.toolbarGroups.find(group => group.key === 'business');
  const businessItems = businessGroup.categories.flatMap(category => category.items);
  const showcaseResponse = await api('/api/v1/reports/' + reportId, 'PUT', {
    reportName: reopened.body.data.reportName,
    content: businessShowcaseContent(businessItems),
    version: reopened.body.data.version,
  });
  assert.equal(showcaseResponse.status, 200, JSON.stringify(showcaseResponse.body));
  await page.goto(BASE + '/report/designer/' + reportId, { waitUntil: 'networkidle0', timeout: 60000 });
  const showcaseFrameHandle = await page.waitForSelector('#business-module-frame');
  const showcaseFrame = await showcaseFrameHandle.contentFrame();
  await showcaseFrame.waitForFunction(() => document.querySelectorAll('.screen-widget').length === 16,
    { timeout: 30000 });
  await new Promise(resolve => setTimeout(resolve, 1200));
  const showcaseState = await showcaseFrame.evaluate(() => ({
    widgets: document.querySelectorAll('.screen-widget').length,
    businessRenderers: document.querySelectorAll('[data-business-component]').length,
    genericFallbacks: document.querySelectorAll('.generic-widget').length,
    names: [...document.querySelectorAll('.screen-widget')].map(node => node.title),
  }));
  assert.equal(showcaseState.widgets, 16);
  assert.equal(showcaseState.businessRenderers, 16);
  assert.equal(showcaseState.genericFallbacks, 0);
  assert(['通用组件', '牛气冲天', '五行相克', '甘特图'].every(name => showcaseState.names.includes(name)));
  await page.screenshot({ path: path.join(OUTPUT, 'supplemental-business-gallery.png'), fullPage: false });

  const deletion = await api('/api/v1/reports/' + reportId, 'DELETE');
  assert.equal(deletion.status, 200);
  reportId = '';
  assert.deepEqual(browserErrors, []);

  const result = {
    ok: true,
    catalogue: {
      sourceIdentifiers: 135,
      categoryOnlyIdentifiers: 24,
      leafTypes: 111,
      primaryLeafTypes: 98,
      supplementalLeafTypes: 13,
      supplementalRecords: 16,
    },
    renderState,
    categoryState,
    showcaseState,
    previewDataCleared: true,
    screenshots: ['business-component-library.png', 'all-111-components-designer.png', 'supplemental-business-gallery.png'],
  };
  fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
  console.log('RESULT_DIR ' + OUTPUT);
})().catch(async error => {
  console.error(error.stack || error);
  if (page && !page.isClosed()) await page.screenshot({ path: path.join(OUTPUT, 'failure.png') }).catch(() => {});
  if (reportId && page && !page.isClosed()) await api('/api/v1/reports/' + reportId, 'DELETE').catch(() => {});
  process.exitCode = 1;
}).finally(async () => {
  if (browser) await browser.close();
});
