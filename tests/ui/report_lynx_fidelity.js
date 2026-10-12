/*
 * 中文说明（参考报表保真度验收）：
 * 验证行为：直接导入的参考报表在来源一致性、真实渲染与可编辑持久化三方面都能对上。
 * 运行方式：需隔离服务 + 本机 Edge；依赖已导入的参考报表与其来源快照。
 * 边界：会保存编辑结果用于验证持久化（隔离环境）。
 */
/* Direct Lynx report acceptance: source parity, real rendering, and editable persistence. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');

const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011';
const PASSWORD = process.env.TEST5_E2E_PASSWORD || 'admin123';
const EDGE = process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const EXPECTED_SOURCE_COUNT = 33;
const EDIT_SOURCE_ID = process.env.TEST5_EDIT_SOURCE_ID || '49';
const OUTPUT = path.resolve('.implementation/ui-results/lynx-direct-reports-' + Date.now().toString(36));

let browser;
let page;
let reportId = '';
let originalReport = null;
let sessionToken = '';
let workspaceId = '';
const browserFailures = [];

function reportContent(report) {
  return typeof report.content === 'string' ? JSON.parse(report.content) : structuredClone(report.content || {});
}

function widgetById(report, widgetId) {
  return (reportContent(report).widgets || []).find((widget) => String(widget.id || widget.i) === String(widgetId));
}

async function api(url, method = 'GET', body) {
  const response = await fetch(BASE + url, {
    method,
    headers: {
      Authorization: 'Bearer ' + sessionToken,
      'X-Workspace-Id': workspaceId,
      'Content-Type': 'application/json',
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  return { status: response.status, body: text ? JSON.parse(text) : null };
}

async function login() {
  const response = await fetch(BASE + '/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: 'workspace-admin', password: PASSWORD }),
  });
  const raw = await response.json().catch(() => ({}));
  assert.equal(response.status, 200, raw.message || raw.detail || 'Login failed');
  const session = raw.data ?? raw;
  sessionToken = session.token;
  workspaceId = session.workspaces?.[0]?.id || '';
  assert(sessionToken && workspaceId, 'login response is missing token or workspace');
  await page.evaluateOnNewDocument((token, workspace) => {
    localStorage.setItem('agent_token', token);
    localStorage.setItem('agent_workspace_id', workspace);
  }, sessionToken, workspaceId);
}

async function openAnalyticsRoute(route) {
  await page.goto(BASE + '/analytics-app?path=' + encodeURIComponent(route), {
    waitUntil: 'networkidle2',
    timeout: 60000,
  });
}

async function waitForReportQueries() {
  await page.waitForFunction(() => !document.querySelector('.stage-loading'), { timeout: 30000 });
  await page.waitForFunction(() => document.querySelectorAll('.data-empty-state[role="status"]').length === 0, {
    timeout: 60000,
  });
  await new Promise((resolve) => setTimeout(resolve, 350));
}

async function pointerGesture(selector, dx, dy) {
  return page.evaluate(async (selector, dx, dy) => {
    const element = document.querySelector(selector);
    if (!element) throw new Error('Gesture target not found: ' + selector);
    const rect = element.getBoundingClientRect();
    const startX = rect.left + rect.width / 2;
    const startY = rect.top + rect.height / 2;
    const init = { bubbles: true, cancelable: true, button: 0, buttons: 1, pointerId: 7, pointerType: 'mouse' };
    element.dispatchEvent(new PointerEvent('pointerdown', { ...init, clientX: startX, clientY: startY }));
    window.dispatchEvent(new PointerEvent('pointermove', {
      ...init, clientX: startX + dx, clientY: startY + dy,
    }));
    window.dispatchEvent(new PointerEvent('pointerup', {
      ...init, buttons: 0, clientX: startX + dx, clientY: startY + dy,
    }));
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    return {
      transform: element.style.transform,
      width: element.style.width,
      height: element.style.height,
    };
  }, selector, dx, dy);
}

async function restoreOriginalReport() {
  if (!page || !reportId || !originalReport) return;
  const latest = await api('/api/v1/reports/' + reportId).catch(() => null);
  const current = latest?.body?.data;
  if (!current?.version) return;
  const restored = await api('/api/v1/reports/' + reportId, 'PUT', {
    reportName: originalReport.reportName,
    description: originalReport.description || '',
    datasourceId: originalReport.datasourceId || null,
    datasourceIds: originalReport.datasourceIds || [],
    width: originalReport.width,
    height: originalReport.height,
    backgroundColor: originalReport.backgroundColor,
    backgroundImage: originalReport.backgroundImage,
    refreshSeconds: originalReport.refreshSeconds || 0,
    templateMode: 'none',
    templateId: null,
    status: originalReport.status || 'draft',
    version: current.version,
    content: originalReport.content,
  });
  assert.equal(restored.status, 200, 'Could not restore the report after the edit-persistence check');
}

(async () => {
  fs.mkdirSync(OUTPUT, { recursive: true });
  browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: true,
    args: ['--no-sandbox', '--disable-gpu'],
  });
  page = await browser.newPage();
  await page.setViewport({ width: 1920, height: 1080, deviceScaleFactor: 1 });
  page.on('pageerror', (error) => browserFailures.push('pageerror: ' + error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') browserFailures.push('console: ' + message.text());
  });
  page.on('response', (response) => {
    const parsed = new URL(response.url());
    if (response.status() >= 400 && parsed.pathname !== '/favicon.ico') {
      browserFailures.push(response.status() + ' ' + parsed.pathname);
    }
  });

  try {
    await login();
    const [reportResult, templateResult, catalogResult] = await Promise.all([
      api('/api/v1/reports/list'),
      api('/api/v1/report-templates'),
      api('/api/v1/reports/source-catalog'),
    ]);
    assert.equal(reportResult.status, 200);
    assert.equal(templateResult.status, 200);
    assert.equal(catalogResult.status, 200);

    const reports = reportResult.body.data;
    const sourceReports = reports.filter((item) => item.lineage?.entry === 'lynx_interface_import');
    const templates = templateResult.body.data;
    const catalog = catalogResult.body.data;
    assert.equal(sourceReports.length, EXPECTED_SOURCE_COUNT,
      'The workspace must contain every current Lynx source report');
    assert.equal(templates.length, 0, 'The dashboard template center must be empty');
    assert.equal(catalog.reportCount, EXPECTED_SOURCE_COUNT);
    assert.equal(new Set(sourceReports.map((item) => String(item.lineage?.sourceRecordId))).size, EXPECTED_SOURCE_COUNT);
    assert(sourceReports.every((item) => item.templateMode === 'none' && !item.templateId));

    const sourceById = new Map(catalog.records.map((item) => [String(item.sourceId), item]));
    for (const report of sourceReports) {
      const source = sourceById.get(String(report.lineage?.sourceRecordId));
      assert(source, 'Missing source record for report ' + report.id);
      assert.equal(report.lineage.sourceVisualHash, source.visualHash);
      assert.equal(report.lineage.componentCount, source.componentCount);
      assert.equal(report.lineage.sourceCatalogSha256, catalog.catalogSha256);
    }

    const thumbnailChecks = await Promise.all(sourceReports.map(async (item) => {
      const response = await fetch(BASE + item.thumbnail);
      const bytes = await response.arrayBuffer();
      return {
        sourceId: item.lineage.sourceRecordId,
        status: response.status,
        size: bytes.byteLength,
        type: response.headers.get('content-type') || '',
      };
    }));
    assert(thumbnailChecks.every((item) => item.status === 200 && item.type === 'image/png' && item.size > 100000),
      'Every report card must use a real rendered PNG thumbnail');

    const target = sourceReports.find((item) => String(item.lineage?.sourceRecordId) === EDIT_SOURCE_ID);
    assert(target, 'Editable source report not found: ' + EDIT_SOURCE_ID);
    reportId = target.id;
    const detail = await api('/api/v1/reports/' + reportId);
    assert.equal(detail.status, 200);
    originalReport = detail.body.data;
    const originalContent = reportContent(originalReport);
    const editableWidget = (originalContent.widgets || []).find((widget) => !widget.locked && widget.visible !== false && widget.type !== 'decoration');
    assert(editableWidget, 'The imported report contains no editable visible widget');
    const widgetId = String(editableWidget.id || editableWidget.i);

    await openAnalyticsRoute(`/report/view/${reportId}?workspace_id=${encodeURIComponent(workspaceId)}`);
    await page.waitForSelector('.dashboard-stage .screen-stage', { visible: true, timeout: 30000 });
    await waitForReportQueries();
    const viewer = await page.evaluate(() => ({
      title: document.querySelector('.screen-stage [data-component="JText"]')?.textContent.trim() || '',
      widgets: document.querySelectorAll('.screen-stage .screen-widget').length,
      dataErrors: document.querySelectorAll('.data-load-error').length,
      loadError: document.querySelector('.stage-error')?.textContent.trim() || '',
      loading: document.querySelectorAll('.data-empty-state[role="status"], .stage-loading').length,
      overflow: document.documentElement.scrollWidth > innerWidth + 1,
    }));
    assert.equal(viewer.widgets, originalContent.widgets.length);
    assert.equal(viewer.dataErrors, 0);
    assert.equal(viewer.loadError, '');
    assert.equal(viewer.loading, 0);
    assert.equal(viewer.overflow, false);
    await page.screenshot({ path: path.join(OUTPUT, `viewer-source-${EDIT_SOURCE_ID}.png`), fullPage: false });

    await openAnalyticsRoute(`/report/designer/${reportId}?workspace_id=${encodeURIComponent(workspaceId)}`);
    await page.waitForSelector('.editor-workspace .screen-stage', { visible: true, timeout: 30000 });
    const widgetSelector = `[data-widget-id="${widgetId.replaceAll('"', '\\"')}"]`;
    await page.waitForSelector(widgetSelector, { visible: true, timeout: 30000 });
    assert.equal(await page.$('.template-style-lock'), null, 'Direct reports must not be visually locked as templates');

    const movedDom = await pointerGesture(widgetSelector, 36, 22);
    await page.waitForSelector(widgetSelector + ' .dot-bottom-right', { visible: true, timeout: 10000 });
    const resizedDom = await pointerGesture(widgetSelector + ' .dot-bottom-right', 32, 20);
    assert.notEqual(movedDom.transform, '');
    assert.notEqual(resizedDom.width, `${editableWidget.w}px`);

    const savedResponsePromise = page.waitForResponse((response) => response.url().endsWith('/api/v1/reports/' + reportId)
      && response.request().method() === 'PUT', { timeout: 30000 });
    await page.click('[aria-label="保存报表"]');
    const savedResponse = await savedResponsePromise;
    assert.equal(savedResponse.status(), 200);

    const savedDetail = await api('/api/v1/reports/' + reportId);
    assert.equal(savedDetail.status, 200);
    const persistedWidget = widgetById(savedDetail.body.data, widgetId);
    assert(persistedWidget, 'Edited widget disappeared after save');
    assert.notEqual(persistedWidget.x, editableWidget.x, 'Horizontal move was not persisted');
    assert.notEqual(persistedWidget.y, editableWidget.y, 'Vertical move was not persisted');
    assert.notEqual(persistedWidget.w, editableWidget.w, 'Width resize was not persisted');
    assert.notEqual(persistedWidget.h, editableWidget.h, 'Height resize was not persisted');

    await page.reload({ waitUntil: 'networkidle2', timeout: 60000 });
    await page.waitForSelector(widgetSelector, { visible: true, timeout: 30000 });
    const reloaded = await page.$eval(widgetSelector, (node) => ({
      transform: node.style.transform,
      width: node.style.width,
      height: node.style.height,
    }));
    assert(reloaded.transform.includes(`translate(${persistedWidget.x}px, ${persistedWidget.y}px)`));
    assert.equal(reloaded.width, `${persistedWidget.w}px`);
    assert.equal(reloaded.height, `${persistedWidget.h}px`);
    await page.screenshot({ path: path.join(OUTPUT, `designer-source-${EDIT_SOURCE_ID}.png`), fullPage: false });

    assert.deepEqual(browserFailures, [], 'Browser/network errors occurred: ' + browserFailures.join(' | '));
    const result = {
      ok: true,
      sourceReportCount: sourceReports.length,
      totalReportCount: reports.length,
      templateCount: templates.length,
      catalogSha256: catalog.catalogSha256,
      thumbnails: thumbnailChecks,
      viewer,
      editPersistence: {
        sourceId: EDIT_SOURCE_ID,
        reportId,
        widgetId,
        before: { x: editableWidget.x, y: editableWidget.y, w: editableWidget.w, h: editableWidget.h },
        after: { x: persistedWidget.x, y: persistedWidget.y, w: persistedWidget.w, h: persistedWidget.h },
        reloaded,
      },
      browserFailures,
    };
    fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify({
      ok: true,
      output: OUTPUT,
      checks: 13,
      sourceReportCount: sourceReports.length,
      totalReportCount: reports.length,
    }));
  } finally {
    await restoreOriginalReport().catch((error) => {
      console.error('RESTORE_FAILED ' + (error.stack || error));
      process.exitCode = 1;
    });
    if (browser) await browser.close();
  }
})().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
