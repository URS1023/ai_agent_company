/*
 * 中文说明（参考大屏截图采集工具，不是断言型用例）：
 * 用途：登录本平台与参考项目（LYNX_BASE），打开指定报表并把渲染结果截图保存，
 *   作为人工比对的视觉基线。
 * 运行方式：需本平台（TEST5_BASE_URL，默认 8011）与参考项目（LYNX_BASE_URL，默认 5173/lynx-ai）
 *   同时在运行，并配置 TEST5_REPORT_ID、TEST5_E2E_PASSWORD 与浏览器路径（EDGE_PATH）。
 * 副作用：只读取页面并写图片文件，不修改任何业务数据；截图产物不要提交为测试证据以外的用途。
 */
const path = require('node:path');
const puppeteer = require('puppeteer-core');

const TEST5_BASE = process.env.TEST5_BASE_URL || 'http://127.0.0.1:8011';
const LYNX_BASE = process.env.LYNX_BASE_URL || 'http://127.0.0.1:5173/lynx-ai';
const REPORT_ID = process.env.TEST5_REPORT_ID || '01M1G8JRRA9C81GDZ7W2Q8GBVT';
const PASSWORD = process.env.TEST5_E2E_PASSWORD || 'admin123';
const EDGE = process.env.EDGE_PATH || 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';

async function loadReportFixture() {
  const loginResponse = await fetch(`${TEST5_BASE}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: 'workspace-admin', password: PASSWORD }),
  });
  if (!loginResponse.ok) throw new Error(`Test5 登录失败：HTTP ${loginResponse.status}`);
  const session = await loginResponse.json();
  const workspaceId = session.workspaces?.[0]?.id;
  const reportResponse = await fetch(`${TEST5_BASE}/api/v1/reports/${REPORT_ID}`, {
    headers: {
      Authorization: `Bearer ${session.token}`,
      'X-Workspace-Id': workspaceId,
    },
  });
  if (!reportResponse.ok) throw new Error(`读取 Test5 报表失败：HTTP ${reportResponse.status}`);
  return reportResponse.json();
}

(async () => {
  const reportFixture = await loadReportFixture();
  const browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: true,
    args: ['--no-sandbox', '--disable-gpu'],
    defaultViewport: { width: 1920, height: 1080, deviceScaleFactor: 1 },
  });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error' && !/Failed to load resource/.test(message.text())) errors.push(message.text());
  });
  await page.evaluateOnNewDocument(() => {
    localStorage.setItem('token', 'visual-reference-token');
    localStorage.setItem('nickname', '视觉基准');
  });
  await page.setRequestInterception(true);
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname.endsWith(`/api/report/${REPORT_ID}`)) {
      request.respond({
        status: 200,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify(reportFixture),
      });
      return;
    }
    if (url.pathname.endsWith('/api/data-analysis/execute-sql')) {
      request.respond({
        status: 200,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify({ success: true, data: { untouched: true } }),
      });
      return;
    }
    request.continue();
  });

  try {
    await page.goto(`${LYNX_BASE}/report/view/${REPORT_ID}`, { waitUntil: 'networkidle0' });
    await page.waitForSelector('.report-viewer .screen-stage', { timeout: 20000 });
    await new Promise(resolve => setTimeout(resolve, 1200));
    const screenshot = path.resolve('shots', 'lynx-report-viewer-same-data.png');
    await page.screenshot({ path: screenshot, fullPage: false });
    const metrics = await page.evaluate(() => ({
      title: document.querySelector('[data-role="dashboard-title"]')?.textContent?.trim() || '',
      stage: (() => {
        const rect = document.querySelector('.screen-stage')?.getBoundingClientRect();
        return rect ? { width: Math.round(rect.width), height: Math.round(rect.height) } : null;
      })(),
      widgetCount: document.querySelectorAll('.screen-widget').length,
      bodyBackground: getComputedStyle(document.body).backgroundColor,
    }));
    console.log(JSON.stringify({ screenshot, metrics, errors }, null, 2));
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
