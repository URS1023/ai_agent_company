/*
 * 中文说明（报表设计器预览一致性验收）：
 * 验证行为：直接导入的 Lynx 报表在预览与编辑模式下使用同一套通用画布，避免两种模式渲染不一致。
 * 运行方式：需隔离服务 + 本机 Edge；依赖已导入的参考报表数据。
 * 边界：只读比对两种模式的渲染结果，不修改报表配置。
 */
/* Verify that a direct Lynx report uses the same generic canvas in preview and editor modes. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');

const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011';
const PASSWORD = process.env.TEST5_E2E_PASSWORD || 'admin123';
const SOURCE_ID = process.env.TEST5_SOURCE_ID || '49';
const REPORT_ID = process.env.TEST5_REPORT_ID || '';
const EDGE = process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const OUTPUT = path.resolve('.implementation/ui-results/direct-report-preview-' + Date.now().toString(36));
const GEOMETRY_TOLERANCE = 3;

function rgbFromHex(value) {
  const normalized = String(value || '').replace('#', '');
  assert.match(normalized, /^[0-9a-f]{6}$/i, 'expected a six-digit canvas background color');
  return `rgb(${parseInt(normalized.slice(0, 2), 16)}, ${parseInt(normalized.slice(2, 4), 16)}, ${parseInt(normalized.slice(4, 6), 16)})`;
}

function assertFullCanvasAmbient(result, label) {
  assert.equal(result.ambientPresent, true, `${label}: ambient backdrop is missing`);
  assert(Math.abs(result.ambient.left - result.stage.left) <= GEOMETRY_TOLERANCE, `${label}: ambient left edge is not flush`);
  assert(Math.abs(result.ambient.top - result.stage.top) <= GEOMETRY_TOLERANCE, `${label}: ambient top edge is not flush`);
  assert(Math.abs(result.ambient.width - result.stage.width) <= GEOMETRY_TOLERANCE, `${label}: ambient width does not fill the canvas`);
  assert(Math.abs(result.ambient.height - result.stage.height) <= GEOMETRY_TOLERANCE, `${label}: ambient height does not fill the canvas`);
}

async function api(pathname, options = {}) {
  const response = await fetch(BASE + pathname, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.message || body.detail || `HTTP ${response.status}`);
  return body.data ?? body;
}

(async () => {
  fs.mkdirSync(OUTPUT, { recursive: true });
  const session = await api('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: 'workspace-admin', password: PASSWORD }),
  });
  const workspaceId = session.workspaces?.[0]?.id;
  assert(session.token && workspaceId, 'login response is missing token or workspace');
  const headers = {
    Authorization: `Bearer ${session.token}`,
    'X-Workspace-Id': workspaceId,
  };
  const reports = await api('/api/v1/reports/list', { headers });
  const target = REPORT_ID
    ? reports.find((item) => String(item.id) === REPORT_ID)
    : reports.find((item) => String(item.lineage?.sourceRecordId) === SOURCE_ID);
  assert(target, REPORT_ID
    ? 'Requested report was not found: ' + REPORT_ID
    : 'Direct Lynx source report was not found: ' + SOURCE_ID);
  const targetContent = JSON.parse(target.content);
  const expectedWidgets = targetContent.widgets.length;
  const expectedCanvas = targetContent.canvas;
  const expectedStyleLock = Boolean(target.lineage?.templateVisualHash);
  const expectedAmbient = targetContent.widgets.find((widget) => widget.visualRole === 'ambient');
  assert(expectedAmbient, 'production monitoring source must include its ambient backdrop');
  assert.equal(expectedCanvas.templateId, 'factory-monitoring-center', 'source 49 must remain the production monitoring template');
  const browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: true,
    args: ['--no-sandbox', '--disable-gpu'],
    defaultViewport: { width: 1920, height: 1080, deviceScaleFactor: 1 },
  });
  const page = await browser.newPage();
  if (REPORT_ID) {
    await page.setRequestInterception(true);
    page.on('request', (request) => {
      const pathname = new URL(request.url()).pathname;
      if (request.method() === 'PUT' && pathname === `/api/v1/reports/${target.id}`) {
        void request.respond({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: target }),
        });
        return;
      }
      void request.continue();
    });
  }
  await page.evaluateOnNewDocument((token, workspace) => {
    localStorage.setItem('agent_token', token);
    localStorage.setItem('agent_workspace_id', workspace);
  }, session.token, workspaceId);
  const failures = [];
  page.on('pageerror', (error) => failures.push('pageerror: ' + error.message));
  page.on('response', (response) => {
    const pathname = new URL(response.url()).pathname;
    if (response.status() >= 400 && pathname !== '/favicon.ico') failures.push(response.status() + ' ' + pathname);
  });

  try {
    await page.goto(`${BASE}/report/designer/${target.id}?workspace_id=${workspaceId}&preview=1`, {
      waitUntil: 'networkidle0', timeout: 60000,
    });
    const frame = await (await page.waitForSelector('#business-module-frame')).contentFrame();
    await frame.waitForSelector('.screen-stage', { visible: true, timeout: 30000 });
    const preview = await frame.evaluate(() => {
      const stageNode = document.querySelector('.screen-stage');
      const ambientNode = stageNode?.querySelector('[data-role="ambient"]');
      const stage = stageNode?.getBoundingClientRect();
      const ambient = ambientNode?.getBoundingClientRect();
      return {
        title: stageNode?.querySelector('[data-component="JText"]')?.textContent.trim() || '',
        widgets: stageNode?.querySelectorAll('.screen-widget').length || 0,
        exitVisible: Boolean(document.querySelector('.exit-preview')?.getClientRects().length),
        loadError: document.querySelector('.editor-load-error')?.textContent.trim() || '',
        templateId: stageNode?.dataset.templateId || '',
        templateResourceId: stageNode?.dataset.templateResourceId || '',
        themeId: stageNode?.dataset.themeId || '',
        backgroundColor: stageNode ? getComputedStyle(stageNode).backgroundColor : '',
        ambientPresent: Boolean(ambientNode),
        stage: stage ? { left: stage.left, top: stage.top, width: stage.width, height: stage.height } : null,
        ambient: ambient ? { left: ambient.left, top: ambient.top, width: ambient.width, height: ambient.height } : null,
      };
    });
    assert(preview.title);
    assert.equal(preview.widgets, expectedWidgets);
    assert.equal(preview.exitVisible, true);
    assert.equal(preview.loadError, '');
    assert.equal(preview.templateId, expectedCanvas.templateId);
    assert.equal(preview.templateResourceId, target.templateId || '');
    assert.equal(preview.themeId, expectedCanvas.themeId);
    assert.equal(preview.backgroundColor, rgbFromHex(expectedCanvas.backgroundColor));
    assertFullCanvasAmbient(preview, 'preview');
    await page.screenshot({ path: path.join(OUTPUT, 'preview.png'), fullPage: false });

    await frame.click('.exit-preview');
    await frame.waitForSelector('.editor-workspace .screen-stage', { visible: true, timeout: 10000 });
    const editor = await frame.evaluate(() => {
      const stageNode = document.querySelector('.editor-workspace .screen-stage');
      const ambientNode = stageNode?.querySelector('[data-role="ambient"]');
      const stage = stageNode?.getBoundingClientRect();
      const ambient = ambientNode?.getBoundingClientRect();
      return {
        widgets: stageNode?.querySelectorAll('.screen-widget').length || 0,
        dataIds: stageNode?.querySelectorAll('[data-widget-id]').length || 0,
        styleLock: Boolean(document.querySelector('.template-style-lock')),
        saveVisible: Boolean(document.querySelector('[aria-label="保存报表"]')?.getClientRects().length),
        templateId: stageNode?.dataset.templateId || '',
        templateResourceId: stageNode?.dataset.templateResourceId || '',
        themeId: stageNode?.dataset.themeId || '',
        backgroundColor: stageNode ? getComputedStyle(stageNode).backgroundColor : '',
        ambientPresent: Boolean(ambientNode),
        stage: stage ? { left: stage.left, top: stage.top, width: stage.width, height: stage.height } : null,
        ambient: ambient ? { left: ambient.left, top: ambient.top, width: ambient.width, height: ambient.height } : null,
      };
    });
    assert.equal(editor.widgets, expectedWidgets);
    assert.equal(editor.dataIds, expectedWidgets);
    assert.equal(editor.styleLock, expectedStyleLock);
    assert.equal(editor.saveVisible, true);
    assert.equal(editor.templateId, expectedCanvas.templateId);
    assert.equal(editor.templateResourceId, target.templateId || '');
    assert.equal(editor.themeId, expectedCanvas.themeId);
    assert.equal(editor.backgroundColor, rgbFromHex(expectedCanvas.backgroundColor));
    assertFullCanvasAmbient(editor, 'editor');

    let saveContract = null;
    if (REPORT_ID) {
      const saveRequest = page.waitForRequest((request) => (
        request.method() === 'PUT'
        && new URL(request.url()).pathname === `/api/v1/reports/${target.id}`
      ), { timeout: 10000 });
      await frame.click('[aria-label="保存报表"]');
      const request = await saveRequest;
      saveContract = JSON.parse(request.postData() || '{}');
      const savedContent = typeof saveContract.content === 'string'
        ? JSON.parse(saveContract.content)
        : saveContract.content;
      assert.equal(saveContract.templateMode, target.templateMode);
      assert.equal(saveContract.templateId || null, target.templateId || null);
      assert.equal(savedContent.canvas.templateId, expectedCanvas.templateId);
      assert.notEqual(saveContract.templateId, savedContent.canvas.templateId);
      const savedAmbient = savedContent.widgets.find((widget) => widget.visualRole === 'ambient');
      assert(savedAmbient, 'saved report lost its ambient backdrop');
      if (expectedStyleLock) {
        assert.deepEqual(
          [savedAmbient.x, savedAmbient.y, savedAmbient.w, savedAmbient.h],
          [expectedAmbient.x, expectedAmbient.y, expectedAmbient.w, expectedAmbient.h],
          'locked template persistence changed source visual geometry',
        );
      } else {
        assert.deepEqual(
          [savedAmbient.x, savedAmbient.y, savedAmbient.w, savedAmbient.h],
          [0, 0, expectedCanvas.width, expectedCanvas.height],
          'editable direct report did not persist the repaired full-canvas backdrop',
        );
      }
    }
    assert.deepEqual(failures, []);
    await page.screenshot({ path: path.join(OUTPUT, 'editor.png'), fullPage: false });

    await page.goto(`${BASE}/report/view/${target.id}?workspace_id=${workspaceId}`, {
      waitUntil: 'networkidle0', timeout: 60000,
    });
    const viewerFrame = await (await page.waitForSelector('#business-module-frame')).contentFrame();
    await viewerFrame.waitForSelector('.report-viewer .screen-stage', { visible: true, timeout: 30000 });
    const viewer = await viewerFrame.evaluate(() => {
      const stageNode = document.querySelector('.report-viewer .screen-stage');
      const ambientNode = stageNode?.querySelector('[data-role="ambient"]');
      const stage = stageNode?.getBoundingClientRect();
      const ambient = ambientNode?.getBoundingClientRect();
      return {
        widgets: stageNode?.querySelectorAll('.screen-widget').length || 0,
        templateId: stageNode?.dataset.templateId || '',
        templateResourceId: stageNode?.dataset.templateResourceId || '',
        themeId: stageNode?.dataset.themeId || '',
        backgroundColor: stageNode ? getComputedStyle(stageNode).backgroundColor : '',
        ambientPresent: Boolean(ambientNode),
        stage: stage ? { left: stage.left, top: stage.top, width: stage.width, height: stage.height } : null,
        ambient: ambient ? { left: ambient.left, top: ambient.top, width: ambient.width, height: ambient.height } : null,
      };
    });
    assert.equal(viewer.widgets, expectedWidgets);
    assert.equal(viewer.templateId, expectedCanvas.templateId);
    assert.equal(viewer.templateResourceId, target.templateId || '');
    assert.equal(viewer.themeId, expectedCanvas.themeId);
    assert.equal(viewer.backgroundColor, rgbFromHex(expectedCanvas.backgroundColor));
    assertFullCanvasAmbient(viewer, 'viewer');
    assert.deepEqual(failures, []);
    await page.screenshot({ path: path.join(OUTPUT, 'viewer.png'), fullPage: false });

    fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({ ok: true, sourceId: SOURCE_ID, reportId: target.id, preview, editor, viewer, saveContract, failures }, null, 2));
    console.log(JSON.stringify({ ok: true, checks: REPORT_ID ? 48 : 42, reportId: target.id, output: OUTPUT }));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
