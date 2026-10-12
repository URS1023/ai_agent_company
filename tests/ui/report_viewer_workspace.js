/*
 * 中文说明（报表查看工作台验收）：
 * 验证行为：报表查看器对指定报表（TEST5_REPORT_ID）的渲染与交互。
 * 运行方式：需本平台服务（TEST5_BASE_URL，默认 8011）+ 本机 Edge，口令默认 admin123。
 * 边界：只读查看；报表 id 可通过环境变量替换，默认值来自当时环境。
 */
const path = require('node:path');
const puppeteer = require('puppeteer-core');

const BASE = process.env.TEST5_BASE_URL || 'http://127.0.0.1:8011';
const PASSWORD = process.env.TEST5_E2E_PASSWORD || 'admin123';
const REPORT_ID = process.env.TEST5_REPORT_ID || '01M1G8JRRA9C81GDZ7W2Q8GBVT';
const EDGE = process.env.EDGE_PATH || 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const VIEWPORT_WIDTH = Number(process.env.TEST5_VIEWPORT_WIDTH || 1920);
const VIEWPORT_HEIGHT = Number(process.env.TEST5_VIEWPORT_HEIGHT || 1080);

(async () => {
  const browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: true,
    args: ['--no-sandbox', '--disable-gpu'],
    defaultViewport: { width: VIEWPORT_WIDTH, height: VIEWPORT_HEIGHT, deviceScaleFactor: 1 },
  });
  const page = await browser.newPage();
  const errors = [];
  const reportHeaders = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error') errors.push(message.text());
  });
  page.on('request', request => {
    if (/\/api\/(?:auth\/me|v1\/reports\/)/.test(request.url())) {
      reportHeaders.push(request.headers()['x-workspace-id'] || '');
    }
  });

  try {
    await page.goto(BASE, { waitUntil: 'networkidle0' });
    await page.type('#login-username', 'workspace-admin');
    await page.type('#login-password', PASSWORD);
    await Promise.all([
      page.click('#login-btn'),
      page.waitForFunction(() => Boolean(localStorage.getItem('agent_token'))),
    ]);
    await page.waitForSelector('[data-testid="business-shell"]');
    const workspaceId = await page.evaluate(() => localStorage.getItem('agent_workspace_id'));
    if (!workspaceId) throw new Error('登录后未建立工作区上下文');

    await page.evaluate(() => localStorage.setItem('agent_workspace_id', 'stale-workspace-selection'));
    const reportUrl = `${BASE}/report/view/${REPORT_ID}?workspace_id=${encodeURIComponent(workspaceId)}`;
    await page.goto(reportUrl, { waitUntil: 'networkidle0' });
    await page.waitForSelector('body.enterprise-immersive-report .business-module-frame');

    const frameHandle = await page.$('#business-module-frame');
    const frame = await frameHandle.contentFrame();
    await frame.waitForSelector('.report-viewer .lynx-template-19-canvas, .report-viewer .screen-stage', { timeout: 20000 });
    await new Promise(resolve => setTimeout(resolve, 1600));

    const outer = await page.evaluate(() => {
      const iframe = document.querySelector('#business-module-frame');
      const rect = iframe.getBoundingClientRect();
      return {
        immersive: document.body.classList.contains('enterprise-immersive-report'),
        headerCount: document.querySelectorAll('.enterprise-header').length,
        sidebarCount: document.querySelectorAll('.enterprise-sidebar').length,
        contextCount: document.querySelectorAll('.enterprise-contextbar').length,
        frame: { width: Math.round(rect.width), height: Math.round(rect.height) },
        workspace: localStorage.getItem('agent_workspace_id'),
      };
    });
    const inner = await frame.evaluate(() => {
      const title = document.querySelector('[data-role="dashboard-title"]');
      const ambient = (document.querySelector('.lynx-template-19-canvas') || document.querySelector('[data-role="ambient"]'))?.getBoundingClientRect();
      const labels = [...document.querySelectorAll('.lynx-t19-metric .metric-copy small, .stat-label')].map(node => node.textContent.trim()).filter(Boolean);
      const tables = [...document.querySelectorAll('.problem-board, .scroll-board')].map(board => {
        const rows = [...board.querySelectorAll('.problem-rows button, .scroll-row')].map(row => row.textContent.replace(/\s+/g, ' ').trim());
        return { rows, duplicateSingleRow: rows.length === 2 && rows[0] === rows[1] };
      });
      return {
        viewer: Boolean(document.querySelector('.report-viewer')),
        lynxTemplate19: Boolean(document.querySelector('.lynx-template-19-canvas')),
        error: document.querySelector('.stage-error')?.textContent.trim() || '',
        title: title?.textContent.trim() || '',
        ambient: ambient ? { width: Math.round(ambient.width), height: Math.round(ambient.height) } : null,
        placeholderTitle: /占位|^主标题$/.test(title?.textContent || ''),
        labels,
        badMetricLabels: labels.filter(label => /指标卡$/.test(label)),
        duplicateSingleRowTables: tables.filter(table => table.duplicateSingleRow).length,
        statusNodes: document.querySelectorAll('.status-nodes article').length,
        panelTitles: [...document.querySelectorAll('.lynx-t19-panel .panel-heading h2')].map(node => node.textContent.trim()),
        commandButtons: document.querySelectorAll('.viewer-command button').length,
      };
    });

    const screenshotName = VIEWPORT_WIDTH === 1920 && VIEWPORT_HEIGHT === 1080
      ? 'report-viewer-workspace-fixed.png'
      : `report-viewer-workspace-fixed-${VIEWPORT_WIDTH}x${VIEWPORT_HEIGHT}.png`;
    const screenshot = path.resolve('shots', screenshotName);
    await page.screenshot({ path: screenshot, fullPage: false });

    if (!outer.immersive || outer.headerCount || outer.sidebarCount || outer.contextCount) {
      throw new Error('大屏仍被平台导航区域包裹：' + JSON.stringify(outer));
    }
    if (outer.frame.width !== VIEWPORT_WIDTH || outer.frame.height !== VIEWPORT_HEIGHT) {
      throw new Error('大屏 iframe 未占满视口：' + JSON.stringify(outer.frame));
    }
    if (outer.workspace !== workspaceId) throw new Error('作用域工作区未在授权后恢复');
    if (!inner.viewer || inner.error) throw new Error('报表未成功显示：' + JSON.stringify(inner));
    if (!inner.lynxTemplate19 || inner.statusNodes !== 6) throw new Error('未启用 Lynx TEMPLATE-19 专用复刻：' + JSON.stringify(inner));
    if (!inner.title || inner.placeholderTitle) throw new Error('大屏主标题仍为空或为占位文字');
    if (inner.ambient && (
      inner.ambient.width > VIEWPORT_WIDTH + 1
      || inner.ambient.height > VIEWPORT_HEIGHT + 1
      || Math.min(VIEWPORT_WIDTH - inner.ambient.width, VIEWPORT_HEIGHT - inner.ambient.height) > 2
    )) {
      throw new Error('环境背景没有按视口完整适配：' + JSON.stringify(inner.ambient));
    }
    if (inner.badMetricLabels.length) throw new Error('指标仍显示内部组件名称：' + inner.badMetricLabels.join(','));
    if (inner.duplicateSingleRowTables) throw new Error('单行表格仍被复制成重复行');
    if (inner.commandButtons < 3 || inner.commandButtons > 4) throw new Error('大屏工具区按钮数量异常');
    if (!reportHeaders.length || reportHeaders.some(value => value !== workspaceId)) {
      throw new Error('报表请求没有稳定使用链接工作区：' + JSON.stringify(reportHeaders));
    }

    await frame.hover('.viewer-command');
    await frame.waitForFunction(() => getComputedStyle(document.querySelector('.viewer-command')).transform === 'matrix(1, 0, 0, 1, 0, 0)');
    await frame.click('button[aria-label="返回报表列表"]');
    await page.waitForFunction(() => location.pathname === '/dashboards');
    await page.waitForSelector('[data-testid="business-shell"] .enterprise-header');
    const returned = await page.evaluate(() => ({
      path: location.pathname + location.search,
      immersive: document.body.classList.contains('enterprise-immersive-report'),
      header: Boolean(document.querySelector('.enterprise-header')),
      sidebar: Boolean(document.querySelector('.enterprise-sidebar')),
    }));
    if (returned.immersive || !returned.header || !returned.sidebar || returned.path !== '/dashboards') {
      throw new Error('退出大屏后没有恢复企业导航：' + JSON.stringify(returned));
    }
    if (errors.length) throw new Error('浏览器控制台错误：' + errors.join(' | '));

    console.log(JSON.stringify({ reportUrl, screenshot, outer, inner, returned, requestWorkspaceHeaders: reportHeaders }, null, 2));
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
