/*
 * 中文说明（旧版 Chat 工作台 + AI 模块边界的真实浏览器验收，只读）：
 * 验证行为：真实 Edge 打开 Cloud Brain 外壳与 AI 模块，业务结果行按对话历史使用的同一种
 *   tool-result 契约渲染；不调用外部模型、不创建大屏。
 * 运行方式：需先启动隔离验收服务（TEST5_E2E_BASE_URL，默认 http://127.0.0.1:8011）与
 *   本机 Edge（TEST5_EDGE_PATH），并设置 TEST5_E2E_PASSWORD；结果写入 .implementation/ui-results/。
 * 边界：只读断言，不写业务数据；外部模型与非只读动作分别由 ALLOW_NATIVE 等开关控制。
 */
/*
 * Read-only real Edge acceptance for the Cloud Brain shell and AI module boundary.
 * Business result rows are rendered from the same persisted tool-result
 * contract used by chat history; the test does not call an external model or
 * create a dashboard.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');

const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011';
const PASSWORD = process.env.TEST5_E2E_PASSWORD;
const ALLOW_NATIVE = process.env.TEST5_E2E_ALLOW_NATIVE === '1';
const EDGE = process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const OUTPUT = path.resolve('.implementation/ui-results/ai-business-' + Date.now().toString(36));
const checks = [];
const failures = [];
const violations = [];
let browser;
let page;

function pass(name, detail = {}) {
  checks.push({ name, ...detail });
  console.log('PASS ' + name);
}

async function api(pathname) {
  return page.evaluate(async (pathname) => {
    const response = await fetch(pathname, {
      headers: { Authorization: 'Bearer ' + localStorage.getItem('agent_token') },
    });
    return { status: response.status, body: await response.json() };
  }, pathname);
}

async function accessibility(route) {
  await page.addScriptTag({ path: require.resolve('axe-core/axe.min.js') });
  const findings = await page.evaluate(async () => {
    const result = await axe.run(document, {
      runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] },
    });
    return result.violations.map((item) => ({
      id: item.id,
      impact: item.impact,
      nodes: item.nodes.map((node) => ({ target: node.target, summary: node.failureSummary })),
    }));
  });
  violations.push(...findings.map((item) => ({ route, ...item })));
}

async function renderContractResults() {
  await page.evaluate(() => {
    const alertData = {
      resultType: 'alert_problem_list',
      title: '设备预警与问题',
      summary: {
        returnedReports: 2,
        returnedProblems: 2,
        statusCounts: { open: 1, resolved: 1 },
        scopeNote: '数量仅代表本次当前账号已授权的返回页',
      },
      reports: [
        {
          id: 'alert-report-01', title: '一号产线温度预警', deviceName: '热处理炉 A-01',
          outcome: 'issues', issues: 2, createTime: '2026-09-01T09:30:00',
          href: '/alerts/reports?report=alert-report-01',
        },
        {
          id: 'alert-report-02', title: '空压机巡检', deviceName: '空压机 C-02',
          outcome: 'normal', issues: 0, createTime: '2026-09-01T08:20:00',
          href: '/alerts/reports?report=alert-report-02',
        },
      ],
      problems: [
        {
          id: 'problem-01', reportId: 'alert-report-01', ruleName: '炉温上限',
          message: '当前温度高于允许上限', severity: 'critical', status: 'open',
          href: '/alerts/reports?report=alert-report-01',
        },
        {
          id: 'problem-02', reportId: 'alert-report-01', ruleName: '温升速率',
          message: '十分钟温升超过阈值', severity: 'warning', status: 'resolved',
          href: '/alerts/reports?report=alert-report-01',
        },
      ],
      actions: [
        { label: '打开预警报告', href: '/alerts/reports', kind: 'primary' },
        { label: '查看问题列表', href: '/alerts/problems', kind: 'secondary' },
      ],
    };
    const dashboardData = {
      resultType: 'dashboard', receiptId: 'receipt-contract-01', reportId: 'report-contract-01',
      generationId: 'generation-contract-01', generationRunId: 'run-contract-01',
      title: '财务与设备运营数字大屏', status: 'draft', version: 1,
      templateMode: 'auto', templateId: null,
      datasourceIds: ['finance-source', 'equipment-source'],
      datasourceNames: ['财务经营库', '设备运行库'],
      queryIds: ['query-finance-01', 'query-equipment-01'], widgetCount: 8,
      previewPath: '/report/view/report-contract-01', reused: false,
      actions: [
        { label: '交互预览', href: '/report/view/report-contract-01', kind: 'primary' },
        { label: '打开完整设计器', href: '/report/designer/report-contract-01', kind: 'secondary' },
        { label: '返回大屏列表', href: '/dashboards', kind: 'secondary' },
      ],
    };
    const sourceData = {
      resultType: 'resource_resolution', returnedCount: 2, truncated: false,
      selectionPolicy: '多个候选不会自动选择第一项',
      sources: [
        { id: 'finance-source', name: '财务经营库', provider: 'mysql', version: 3, description: '经营指标与部门费用', metadataStatus: 'ready' },
        { id: 'equipment-source', name: '设备运行库', provider: 'postgres', version: 2, description: '设备状态与采集指标', metadataStatus: 'ready' },
      ],
    };
    const parts = [
      { type: 'text', text: '已按当前账号权限读取业务结果。' },
      { type: 'tool_result', tool: 'view_device_alerts', result: { status: 'success', data: alertData } },
      { type: 'tool_result', tool: 'create_business_dashboard', result: { status: 'success', data: dashboardData } },
      { type: 'tool_result', tool: 'list_business_data_sources', result: { status: 'success', data: sourceData } },
    ];
    const messages = document.querySelector('#messages');
    messages.innerHTML = '';
    messages.appendChild(assistantRow(parts, ''));
  });
  await page.waitForSelector('[data-testid=alert-business-result]');
  await page.waitForSelector('[data-testid=dashboard-business-result]');
}

(async () => {
  assert(PASSWORD, 'Supply TEST5_E2E_PASSWORD for the isolated preview');
  const health = await fetch(BASE + '/api/health').then((response) => response.json());
  assert(
    health.app === 'test' || (ALLOW_NATIVE && health.app === 'native-dev'),
    'Refusing to run UI acceptance outside an explicitly allowed local development server',
  );
  fs.mkdirSync(OUTPUT, { recursive: true });

  browser = await puppeteer.launch({ executablePath: EDGE, headless: true });
  page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 960 });
  page.on('pageerror', (error) => failures.push('pageerror: ' + error.message));
  page.on('response', (response) => {
    if (response.status() >= 500) failures.push(response.status() + ' ' + response.url());
  });

  await page.goto(BASE, { waitUntil: 'networkidle0' });
  await page.type('#login-username', 'workspace-admin');
  await page.type('#login-password', PASSWORD);
  await page.click('#login-btn');
  await page.waitForSelector('.enterprise-area-grid');
  await page.goto(BASE + '/ai', { waitUntil: 'networkidle0' });
  await page.waitForSelector('[data-testid=ai-business-start]');

  const catalogResponse = await api('/api/v1/ai/business-actions');
  assert.equal(catalogResponse.status, 200);
  const catalog = catalogResponse.body.data;
  assert.deepEqual(new Set(catalog.map((item) => item.capabilityId)), new Set([
    'business.datasources.discover', 'alerts.overview', 'dashboard.generate',
  ]));
  const dashboardCapability = catalog.find((item) => item.capabilityId === 'dashboard.generate');
  assert(dashboardCapability.available, 'Administrator dashboard action must be available');
  assert(dashboardCapability.requiredPermissions.includes('templates.view'));
  assert.equal(dashboardCapability.resultComponent, 'dashboard');
  assert.equal(dashboardCapability.modulePath, '/dashboards');
  assert(dashboardCapability.inputSchema.properties.requestKey);
  pass('Typed business capability catalog matches the visible AI scenes');

  const shell = await page.evaluate(() => ({
    area: document.querySelector('.enterprise-area-link.active')?.dataset.businessArea,
    areaLabel: document.querySelector('.enterprise-area-link.active')?.innerText.trim(),
    conversationRail: document.querySelector('.conversation-rail-title')?.innerText.trim(),
    contextualSidebar: Boolean(document.querySelector('.enterprise-sidebar')),
    sceneTitles: [...document.querySelectorAll('.ai-scene-card strong')].map((node) => node.textContent.trim()),
    internalNavigation: [...document.querySelectorAll('#sidebar .side-menu > *')].map((node) => node.innerText.trim()),
    executionSteps: [...document.querySelectorAll('.ai-execution-rail span')].map((node) => node.innerText.trim()),
    moduleTabs: document.querySelectorAll('.ai-module-sections a').length,
    persistentSearch: Boolean(document.querySelector('.enterprise-search-button')),
    persistentWorkspace: Boolean(document.querySelector('.enterprise-workspace-name')),
    duplicateAccount: Boolean(document.querySelector('.user-footer')),
    inactivePptControl: Boolean(document.querySelector('#ppt-preset')),
    quickText: document.querySelector('[data-testid=ai-business-start]')?.innerText.trim(),
    documentWidth: document.documentElement.scrollWidth,
    viewportWidth: innerWidth,
  }));
  assert.equal(shell.area, 'ai');
  assert(shell.areaLabel.includes('Cloud Brain'));
  assert.equal(shell.conversationRail, '对话记录');
  assert.equal(shell.contextualSidebar, false, 'AI module must not render a second Cloud Brain feature sidebar');
  assert.deepEqual(shell.sceneTitles, ['查看设备预警', '生成财务与设备大屏']);
  assert.deepEqual(shell.internalNavigation, ['新聊天']);
  assert.equal(shell.executionSteps.length, 0);
  assert.equal(shell.moduleTabs, 0);
  assert.equal(shell.persistentSearch, true, 'The shared Cloud Brain header must expose one global feature search');
  assert.equal(shell.persistentWorkspace, false);
  assert.equal(shell.duplicateAccount, false);
  assert.equal(shell.inactivePptControl, false);
  assert(shell.quickText.length < 45, 'Empty state accumulated explanatory text again');
  assert(shell.documentWidth <= shell.viewportWidth);
  assert(await page.$eval('#business-source-btn', (node) => !node.classList.contains('hidden')),
    'Authorized users must see the business data-source selector in the composer');
  assert(await page.$eval('#dashboard-template-btn', (node) => !node.classList.contains('hidden')),
    'Authorized users must see the dashboard-template selector in the composer');
  assert.equal(await page.evaluate(() => businessPath('//outside.example/report')), '#');
  assert.equal(await page.evaluate(() => businessPath('javascript:alert(1)')), '#');
  pass('Cloud Brain is the application shell with one focused AI workbench module', { shell });

  await page.click('#add-menu-btn');
  await page.waitForSelector('[data-act=presentation]');
  await page.click('[data-act=presentation]');
  await page.waitForSelector('#template-modal:not(.hidden)');
  assert.equal(await page.$('#ppt-preset'), null, 'PPT control must stay absent until a template is explicitly selected');
  await page.click('#template-close');
  pass('PPT templates remain available on demand without a permanent composer control');

  await page.click('#business-source-btn');
  await page.waitForSelector('.datasource-picker-head');
  const picker = await page.evaluate(() => ({
    title: document.querySelector('.datasource-picker-head strong')?.textContent.trim(),
    rows: document.querySelectorAll('.datasource-option').length,
    names: [...document.querySelectorAll('.datasource-option-head strong')].map((node) => node.textContent.trim()),
    descriptions: [...document.querySelectorAll('.datasource-description')].map((node) => node.textContent.trim()),
    metadata: [...document.querySelectorAll('.datasource-metadata')].map((node) => node.textContent.trim()),
    prompt: document.querySelector('#prompt-textarea')?.value,
  }));
  assert.equal(picker.title, '选择本次业务数据');
  assert(picker.rows >= 3, 'The real governed catalog must expose the three authorized sources');
  assert(picker.names.includes('variantorm'), 'variantorm disappeared from the real authorized catalog');
  assert(picker.descriptions.every(Boolean), 'Every selectable source must explain its business purpose');
  const variantormIndex = picker.names.indexOf('variantorm');
  assert(variantormIndex >= 0 && /\b[1-9]\d* 表\b/.test(picker.metadata[variantormIndex]) && picker.metadata[variantormIndex].includes('向量索引'),
    'variantorm must expose its synchronized table count and semantic index status');
  assert.equal(picker.prompt, '');
  await page.screenshot({ path: path.join(OUTPUT, 'datasource-picker-desktop.png'), fullPage: false });
  const variantormId = await page.evaluate(() => {
    const row = [...document.querySelectorAll('.datasource-option')]
      .find((node) => node.querySelector('.datasource-option-head strong')?.textContent.trim() === 'variantorm');
    return row?.querySelector('input[data-source-id]')?.dataset.sourceId || '';
  });
  assert(variantormId, 'variantorm row has no selectable source identifier');
  await page.click(`input[data-source-id="${variantormId}"]`);
  await page.click('[data-datasource-done]');
  const selectedSource = await page.$eval('.attach-chip.datasource-chip', (node) => node.textContent.trim());
  assert(selectedSource.includes('variantorm'));
  assert((await page.$eval('#business-source-label', (node) => node.textContent.trim())).includes('variantorm'));
  assert.equal(await page.$$eval('#messages .msg-row', (rows) => rows.length), 0, 'Source picker must not silently submit');
  pass('Real data-source catalog exposes variantorm metadata and pins the explicit choice', { picker, variantormId });

  await page.waitForFunction(() => typeof state !== 'undefined' && Array.isArray(state.dashboardTemplates));
  const pinnedBusinessContext = await page.evaluate(() => ({
    datasourceIds: [...state.datasourceIds],
    templateCount: state.dashboardTemplates.length,
    sourceTemplateCount: state.dashboardTemplates.filter((item) => item.sourceKind === 'lynx-report-screen').length,
    realPreviewCount: state.dashboardTemplates.filter((item) => /\/report-thumbnails\/lynx-\d+\.png$/.test(item.preview || '')).length,
    dashboardTemplateMode: state.dashboardTemplateMode,
    dashboardTemplateId: state.dashboardTemplateId,
    templateButtonHidden: document.querySelector('#dashboard-template-btn')?.classList.contains('hidden'),
  }));
  assert.deepEqual(pinnedBusinessContext.datasourceIds, [variantormId]);
  assert(pinnedBusinessContext.templateCount >= 33);
  assert.equal(pinnedBusinessContext.sourceTemplateCount, 33);
  assert.equal(pinnedBusinessContext.realPreviewCount, 33);
  assert.equal(pinnedBusinessContext.dashboardTemplateMode, 'auto');
  assert.equal(pinnedBusinessContext.dashboardTemplateId, null);
  assert.equal(pinnedBusinessContext.templateButtonHidden, false);
  pass('AI composer keeps the selected datasource and exposes all real Cloud Brain dashboard templates without preselecting one', {
    pinnedBusinessContext,
  });

  await accessibility('/ai-empty-state');
  await page.screenshot({ path: path.join(OUTPUT, 'ai-workbench-desktop.png'), fullPage: false });

  await renderContractResults();
  const resultContract = await page.evaluate(() => ({
    reportLinks: [...document.querySelectorAll('[data-testid=alert-business-result] a')].map((link) => link.getAttribute('href')),
    dashboardLinks: [...document.querySelectorAll('[data-testid=dashboard-business-result] a')].map((link) => link.getAttribute('href')),
    frame: document.querySelector('[data-testid=dashboard-inline-frame]')?.getAttribute('src'),
    sources: [...document.querySelectorAll('.dashboard-source-line div span')].map((node) => node.textContent.trim()),
    metrics: [...document.querySelectorAll('.dashboard-result-meta strong')].map((node) => node.textContent.trim()),
    problemRows: document.querySelectorAll('.business-problem-list li').length,
    resultTypes: [...document.querySelectorAll('[data-result-type]')].map((node) => node.dataset.resultType),
  }));
  assert(resultContract.reportLinks.includes('/alerts/reports?report=alert-report-01'));
  assert(resultContract.dashboardLinks.includes('/report/designer/report-contract-01'));
  assert.equal(resultContract.frame, '/analytics-app?path=%2Freport%2Fview%2Freport-contract-01');
  assert.deepEqual(resultContract.sources, ['财务经营库', '设备运行库']);
  assert.equal(resultContract.problemRows, 2);
  assert.deepEqual(resultContract.resultTypes, ['alert_problem_list', 'dashboard']);
  assert([...resultContract.reportLinks, ...resultContract.dashboardLinks].every((href) => !href.includes('token=')));
  pass('Structured AI results link back to normal alert and dashboard module resources', { resultContract });
  await page.screenshot({ path: path.join(OUTPUT, 'ai-business-results-desktop.png'), fullPage: true });
  await (await page.$('[data-testid=alert-business-result]')).screenshot({
    path: path.join(OUTPUT, 'alert-result-card-desktop.png'),
  });
  await (await page.$('[data-testid=dashboard-business-result]')).screenshot({
    path: path.join(OUTPUT, 'dashboard-result-card-desktop.png'),
  });

  await page.setViewport({ width: 390, height: 844, isMobile: true });
  await page.goto(BASE + '/ai', { waitUntil: 'networkidle0' });
  await page.waitForSelector('[data-testid=ai-business-start]');
  const mobileStart = await page.evaluate(() => {
    const cards = [...document.querySelectorAll('.ai-scene-card')].map((card) => {
      const rect = card.getBoundingClientRect();
      return { left: rect.left, right: rect.right, width: rect.width };
    });
    return {
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth: innerWidth,
      primaryDomainsVisible: [...document.querySelectorAll('.enterprise-primary a')]
        .filter((item) => item.getClientRects().length && getComputedStyle(item).display !== 'none').length,
      domainSelectorVisible: Boolean(document.querySelector('.enterprise-area-select select')?.getClientRects().length),
      columns: new Set(cards.map((card) => Math.round(card.left))).size,
      cards,
    };
  });
  assert(mobileStart.documentWidth <= mobileStart.viewportWidth);
  assert.equal(mobileStart.primaryDomainsVisible, 0);
  assert(mobileStart.domainSelectorVisible);
  assert.equal(mobileStart.columns, 1);
  assert(mobileStart.cards.every((card) => card.left >= 0 && card.right <= mobileStart.viewportWidth));
  pass('390px AI workbench uses one scene column and the Cloud Brain domain selector without overflow', { mobileStart });
  await page.screenshot({ path: path.join(OUTPUT, 'ai-workbench-mobile.png'), fullPage: true });

  await renderContractResults();
  const mobileResults = await page.evaluate(() => ({
    documentWidth: document.documentElement.scrollWidth,
    viewportWidth: innerWidth,
    resultCards: [...document.querySelectorAll('.business-result-card')].map((card) => {
      const rect = card.getBoundingClientRect();
      return { left: rect.left, right: rect.right };
    }),
    tableScrollable: (() => {
      const table = document.querySelector('.business-table-wrap');
      return table && getComputedStyle(table).overflowX === 'auto';
    })(),
  }));
  assert(mobileResults.documentWidth <= mobileResults.viewportWidth);
  assert(mobileResults.resultCards.every((card) => card.left >= 0 && card.right <= mobileResults.viewportWidth));
  assert(mobileResults.tableScrollable);
  pass('Structured business results remain usable on a 390px viewport', { mobileResults });
  await page.screenshot({ path: path.join(OUTPUT, 'ai-business-results-mobile.png'), fullPage: true });

  assert.deepEqual(failures, []);
  const serious = violations.filter((item) => ['critical', 'serious'].includes(item.impact));
  const result = { ok: serious.length === 0, base: BASE, checks, failures, violations };
  fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify(result, null, 2));
  console.log('RESULT_DIR ' + OUTPUT);
  console.log('ACCESSIBILITY_FINDINGS ' + violations.length);
  assert.equal(serious.length, 0, 'Serious or critical accessibility findings remain');
})().catch(async (error) => {
  console.error(error.stack || error);
  if (page && !page.isClosed()) {
    await page.screenshot({ path: path.join(OUTPUT, 'failure.png'), fullPage: true }).catch(() => {});
  }
  if (fs.existsSync(OUTPUT)) {
    fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({ ok: false, checks, failures, violations, error: String(error) }, null, 2));
  }
  console.error('RESULT_DIR ' + OUTPUT);
  process.exitCode = 1;
}).finally(async () => {
  if (browser) await browser.close();
});
