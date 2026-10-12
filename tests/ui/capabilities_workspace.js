/*
 * 中文说明（能力中心工作台真实浏览器验收）：
 * 验证行为：紧凑版 Qknow 风格的能力（工具/连接器）工作台的配置、版本与测试入口按权限正确工作。
 * 运行方式：默认连 http://127.0.0.1:8027 的隔离服务（可用 TEST5_E2E_BASE_URL 覆盖）+ 本机 Edge
 *   与 TEST5_E2E_PASSWORD；结果写入 .implementation/ui-results/capabilities-<run>/。
 * 边界：真实执行测试能力会发起外部调用，必须只在隔离实例上运行。
 */
/* Real Edge acceptance for the compact Qknow capability workspace. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');

const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8027';
const PASSWORD = process.env.TEST5_E2E_PASSWORD;
const EDGE = process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const OUTPUT = path.resolve('.implementation/ui-results/capabilities-' + Date.now().toString(36));
const checks = [], failures = [], violations = [];
let browser, page;

function pass(name, detail = {}) {
  checks.push({ name, ...detail });
  console.log('PASS ' + name);
}

async function call(method, pathname, body) {
  return page.evaluate(async ({ method, pathname, body }) => {
    const headers = {
      Authorization: 'Bearer ' + localStorage.getItem('agent_token'),
      'X-Workspace-Id': localStorage.getItem('agent_workspace_id'),
      Accept: 'application/json',
    };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const response = await fetch(pathname, {
      method, headers, body: body === undefined ? undefined : JSON.stringify(body),
    });
    const value = await response.json();
    if (!response.ok) throw new Error(response.status + ' ' + JSON.stringify(value));
    return value.success === true ? value.data : value;
  }, { method, pathname, body });
}

async function seed() {
  const suffix = Date.now().toString(36);
  const toolName = 'ui.equipment_status_' + suffix;
  const connector = await call('POST', '/api/v1/capabilities/connectors', {
    name: '设备状态接口-' + suffix,
    description: '浏览器验收使用的固定 HTTP 接口。',
    provider: 'http',
    visibility: 'workspace',
    config: {
      baseUrl: 'https://example.com', path: '/api/equipment/status', method: 'POST',
      bodyMode: 'json', headers: {}, auth: { type: 'none' }, timeoutSeconds: 30,
      allowPrivate: false,
    },
    tools: [{
      name: toolName, description: '按设备编号读取状态', timeoutSeconds: 30,
      riskLevel: 'low', permissions: [],
      inputSchema: {
        type: 'object', properties: { equipmentId: { type: 'string', minLength: 1 } },
        required: ['equipmentId'], additionalProperties: false,
      },
      outputSchema: { type: 'object', properties: { status: { type: 'string' } } },
    }],
  });
  const published = await call('POST', `/api/v1/capabilities/connectors/${connector.id}/publish`, { version: 1 });
  const skill = await call('POST', '/api/skills', {
    name: '设备状态复核-' + suffix,
    description: '核对设备状态并给出简洁结论。',
    spec: {
      name: '设备状态复核-' + suffix,
      description: '核对设备状态并给出简洁结论。',
      instructions: '读取设备编号，调用状态工具，返回状态与复核建议。',
      rules: ['不得猜测不存在的设备', '保留工具结果来源'],
      recommended_tools: [toolName], required_resources: [], examples: [],
    },
  });
  const plugin = await call('POST', '/api/plugins', {
    name: '设备状态能力包-' + suffix,
    description: '把设备复核步骤和状态工具组合为业务能力。',
    spec: {
      name: '设备状态能力包-' + suffix,
      description: '把设备复核步骤和状态工具组合为业务能力。',
      instructions: '先核验设备编号，再读取状态并输出结论。',
      skill_refs: [skill.skill_id], tool_refs: [toolName], resource_bindings: [],
      input_schema: { type: 'object', properties: {}, additionalProperties: false },
      output_schema: {},
    },
  });
  return { suffix, toolName, connector: published, skillId: skill.skill_id, pluginId: plugin.plugin_id };
}

async function audit(route) {
  await page.addScriptTag({ path: require.resolve('axe-core/axe.min.js') });
  const report = await page.evaluate(async () => (await axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] },
  })).violations.map(item => ({
    id: item.id, impact: item.impact,
    nodes: item.nodes.map(node => ({ target: node.target, summary: node.failureSummary })),
  })));
  violations.push(...report.map(item => ({ route, ...item })));
}

async function visit(route, selector = '#main-content > .panel', expectedTabs = 0) {
  const response = await page.goto(BASE + route, { waitUntil: 'networkidle0', timeout: 90000 });
  assert.equal(response.status(), 200, route);
  await page.waitForSelector(selector, { timeout: 30000 });
  const layout = await page.evaluate(() => ({
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    panels: document.querySelectorAll('#main-content > .panel').length,
    tabs: document.querySelectorAll('#main-content .catalog-tabs').length,
    iframe: Boolean(document.querySelector('#business-module-frame')),
    hero: Boolean(document.querySelector('#main-content .overview-intro, #main-content .metric-strip')),
    summaryStrip: Boolean(document.querySelector('#main-content .resource-summary')),
    headingParagraphs: document.querySelectorAll('#main-content > .page-heading p').length,
    sidebarLinks: [...document.querySelectorAll('.enterprise-sidebar a')].filter(node => node.getClientRects().length).length,
    bodyText: document.querySelector('#main-content').innerText,
  }));
  assert(layout.overflow <= 1, route + ' horizontal overflow');
  assert.equal(layout.panels, 1, route + ' must keep one primary panel');
  assert.equal(layout.tabs, expectedTabs, route + ' page-level tab count');
  assert.equal(layout.iframe, false); assert.equal(layout.hero, false); assert.equal(layout.summaryStrip, false);
  assert(layout.headingParagraphs <= 1); assert(layout.sidebarLinks <= 7);
  assert(!layout.bodyText.includes('undefined')); assert(!layout.bodyText.includes('[object Object]'));
  await audit(route);
  return layout;
}

(async () => {
  assert(PASSWORD, 'Supply TEST5_E2E_PASSWORD');
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, 'isolated', 'Capability acceptance requires the isolated server');
  assert.equal((await (await fetch(BASE + '/api/health')).json()).app, 'test');
  fs.mkdirSync(OUTPUT, { recursive: true });
  browser = await puppeteer.launch({ executablePath: EDGE, headless: true });
  page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 960 });
  page.on('pageerror', error => failures.push('pageerror ' + error.message));
  page.on('console', message => { if (message.type() === 'error') failures.push('console ' + message.text()); });
  page.on('response', response => { if (response.status() >= 500) failures.push(response.status() + ' ' + response.url()); });

  await page.goto(BASE, { waitUntil: 'networkidle0' });
  await page.type('#login-username', 'workspace-admin');
  await page.type('#login-password', PASSWORD);
  await page.click('#login-btn');
  await page.waitForSelector('.enterprise-area-grid');
  const records = await seed();

  await visit(`/capabilities?keyword=${encodeURIComponent(records.toolName)}`, '.table-scroll', 1);
  assert.equal(await page.$$eval('tbody tr', rows => rows.length), 1);
  assert((await page.$eval('tbody', node => node.innerText)).includes(records.toolName));
  await page.screenshot({ path: path.join(OUTPUT, 'tools-list.png'), fullPage: true });
  pass('Tool catalog is one compact table with three page-level management tabs and no hero or summary cards');

  await visit(`/capabilities?tool=${encodeURIComponent(records.toolName)}`, '.capability-detail');
  assert.equal(await page.$eval('.capability-schema', node => node.open), false);
  await page.click('[data-tool-validate]');
  await page.waitForSelector('#platform-dialog[open] textarea[name=arguments]');
  await page.$eval('#platform-dialog textarea[name=arguments]', node => { node.value = '{"equipmentId":"EQ-001"}'; });
  await page.click('#platform-dialog button[type=submit]');
  await page.waitForSelector('#platform-dialog[open] .capability-result');
  assert((await page.$eval('.capability-result', node => node.innerText)).includes('EQ-001'));
  await audit('/capabilities-validate-dialog');
  await page.screenshot({ path: path.join(OUTPUT, 'tool-validation.png'), fullPage: true });
  await page.click('#platform-dialog [data-dialog-close]');
  pass('Tool detail keeps schemas folded and validates typed JSON in one dialog');

  await visit('/capabilities?view=categories', '.catalog-tabs', 1);
  await page.click('[data-category-new]');
  await page.waitForSelector('#platform-dialog[open] [name=name]');
  await page.type('#platform-dialog [name=name]', '设备方法-' + records.suffix);
  await page.type('#platform-dialog [name=code]', 'equipment_' + records.suffix);
  await page.type('#platform-dialog [name=description]', '设备状态方法分类');
  const categoryResponse = page.waitForResponse(response =>
    response.url().endsWith('/api/v1/capabilities/categories') &&
    response.request().method() === 'POST' && response.status() === 201
  );
  await page.click('#platform-dialog button[type=submit]');
  const categoryPayload = await (await categoryResponse).json();
  records.categoryId = categoryPayload.data.id;
  await page.waitForFunction(() => location.search.includes('view=categories'));
  await page.waitForSelector('.table-scroll');
  assert((await page.$eval('tbody', node => node.innerText)).includes('设备方法-' + records.suffix));
  await page.screenshot({ path: path.join(OUTPUT, 'tool-categories.png'), fullPage: true });
  pass('Tool categories use a compact tree table and a normal management dialog');

  await visit('/capabilities?view=methods&keyword=' + encodeURIComponent(records.toolName), '.table-scroll', 1);
  assert.equal(await page.$$eval('tbody tr', rows => rows.length), 1);
  await page.click('[data-method-select]');
  assert.equal(await page.$eval('[data-method-batch]', button => button.disabled), false);
  await page.click('[data-method-batch]');
  await page.waitForSelector('#platform-dialog[open] [name=categoryId]');
  await page.select('#platform-dialog [name=categoryId]', records.categoryId);
  await page.select('#platform-dialog [name=enabled]', 'false');
  const batchResponse = page.waitForResponse(response =>
    response.url().endsWith('/api/v1/capabilities/methods/batch') &&
    response.request().method() === 'POST' && response.status() === 200
  );
  await page.click('#platform-dialog button[type=submit]');
  await batchResponse;
  await page.waitForFunction(() => location.search.includes('view=methods'));
  const disabledTools = await call('GET', '/api/v1/capabilities/tools?keyword=' + encodeURIComponent(records.toolName));
  assert.equal(disabledTools.items.length, 0);

  await visit('/capabilities?view=methods&keyword=' + encodeURIComponent(records.toolName), '.table-scroll', 1);
  assert((await page.$eval('tbody', node => node.innerText)).includes('设备方法-' + records.suffix));
  await page.click('[data-method-edit]');
  await page.waitForSelector('#platform-dialog[open] [name=enabled]');
  assert.equal(await page.$eval('#platform-dialog [name=enabled]', node => node.checked), false);
  await page.click('#platform-dialog [name=enabled]');
  await page.type('#platform-dialog [name=displayName]', '设备状态查询');
  await page.type('#platform-dialog [name=tags]', '设备，状态');
  const methodResponse = page.waitForResponse(response =>
    response.url().includes('/api/v1/capabilities/methods/') &&
    response.request().method() === 'PATCH' && response.status() === 200
  );
  await page.click('#platform-dialog button[type=submit]');
  await methodResponse;
  await page.waitForFunction(() => location.search.includes('view=methods'));
  const enabledTools = await call('GET', '/api/v1/capabilities/tools?keyword=' + encodeURIComponent(records.toolName));
  assert.equal(enabledTools.items.length, 1);
  assert.equal(enabledTools.items[0].displayName, '设备状态查询');
  await visit('/capabilities?view=methods&keyword=' + encodeURIComponent(records.toolName), '.table-scroll', 1);
  await page.screenshot({ path: path.join(OUTPUT, 'tool-methods.png'), fullPage: true });
  pass('Method classification, batch disable and explicit re-enable govern the executable tool catalog');

  await visit(`/connectors?keyword=${encodeURIComponent(records.suffix)}`, '.table-scroll');
  assert.equal(await page.$$eval('tbody tr', rows => rows.length), 1);
  await page.screenshot({ path: path.join(OUTPUT, 'connectors-list.png'), fullPage: true });
  await visit(`/connectors?id=${records.connector.id}`, '.capability-detail');
  assert.equal(await page.$eval('.capability-history', node => node.open), false);
  assert.equal(await page.$$eval('.capability-detail > .table-scroll', nodes => nodes.length), 1);
  await page.click('[data-connector-version]');
  await page.waitForSelector('#platform-dialog[open] [name=baseUrl]');
  const connectorForm = await page.evaluate(() => ({
    provider: document.querySelector('#platform-dialog [name=provider]').value,
    providerLocked: document.querySelector('#platform-dialog [name=provider]').disabled,
    visibleFields: [...document.querySelectorAll('#platform-dialog .form-field')].filter(node => !node.hidden).length,
    tools: document.querySelectorAll('#platform-dialog [data-tool-row]').length,
  }));
  assert.equal(connectorForm.provider, 'http'); assert.equal(connectorForm.providerLocked, true);
  assert.equal(connectorForm.tools, 1); assert(connectorForm.visibleFields < 12);
  await audit('/connectors-version-dialog');
  await page.screenshot({ path: path.join(OUTPUT, 'connector-version-dialog.png'), fullPage: true });
  await page.click('#platform-dialog [data-dialog-close]');
  pass('Connector detail and typed version dialog reveal only the active provider fields');

  await page.click('[data-connector-clone]');
  await page.waitForSelector('#platform-dialog[open] [name=toolPrefix]');
  await page.$eval('#platform-dialog [name=name]', (node, value) => { node.value = value; }, '设备状态接口副本-' + records.suffix);
  await page.type('#platform-dialog [name=toolPrefix]', 'ui_copy_' + records.suffix);
  const cloneConnectorResponse = page.waitForResponse(response =>
    response.url().endsWith('/clone') && response.url().includes('/api/v1/capabilities/connectors/') &&
    response.request().method() === 'POST' && response.status() === 201
  );
  await page.click('#platform-dialog button[type=submit]');
  await cloneConnectorResponse;
  await page.waitForFunction(sourceId => new URL(location.href).searchParams.get('id') !== sourceId, {}, records.connector.id);
  records.clonedConnectorId = new URL(page.url()).searchParams.get('id');
  await page.waitForSelector('[data-connector-share]');
  assert((await page.$eval('.capability-detail', node => node.innerText)).includes('仅自己'));
  await page.click('[data-connector-share]');
  await page.waitForSelector('#platform-dialog[open]');
  const shareConnectorResponse = page.waitForResponse(response =>
    response.url().endsWith('/share') && response.url().includes(records.clonedConnectorId) &&
    response.status() === 200
  );
  await page.click('#platform-dialog button[type=submit]');
  await shareConnectorResponse;
  await page.waitForFunction(id => location.search.includes(id), {}, records.clonedConnectorId);
  await page.waitForSelector('.capability-detail');
  assert((await page.$eval('.capability-detail', node => node.innerText)).includes('工作区'));
  pass('Connector clone creates unique method contracts and sharing changes only visibility');

  await visit(`/skills?id=${records.skillId}`, '.capability-detail');
  assert.equal(await page.$eval('.capability-history', node => node.open), false);
  await page.screenshot({ path: path.join(OUTPUT, 'skill-detail.png'), fullPage: true });
  pass('Skill detail uses one concise record panel with folded history');

  await page.click('[data-skill-clone]');
  await page.waitForSelector('#platform-dialog[open] [name=new_name]');
  await page.$eval('#platform-dialog [name=new_name]', (node, value) => { node.value = value; }, '设备状态复核副本-' + records.suffix);
  const cloneSkillResponse = page.waitForResponse(response =>
    response.url().endsWith('/clone') && response.url().includes('/api/skills/') &&
    response.status() === 201
  );
  await page.click('#platform-dialog button[type=submit]');
  await cloneSkillResponse;
  await page.waitForFunction(sourceId => new URL(location.href).searchParams.get('id') !== sourceId, {}, records.skillId);
  records.clonedSkillId = new URL(page.url()).searchParams.get('id');
  await page.waitForSelector('[data-skill-share]');
  assert((await page.$eval('.capability-detail', node => node.innerText)).includes('仅自己'));
  await page.click('[data-skill-share]');
  await page.waitForSelector('#platform-dialog[open]');
  const shareSkillResponse = page.waitForResponse(response =>
    response.url().endsWith('/share') && response.url().includes(records.clonedSkillId) && response.status() === 200
  );
  await page.click('#platform-dialog button[type=submit]');
  await shareSkillResponse;
  await page.waitForSelector('[data-skill-archive]');
  await page.click('[data-skill-archive]');
  await page.waitForSelector('#platform-dialog[open]');
  const archiveSkillResponse = page.waitForResponse(response =>
    response.url().endsWith('/archive') && response.url().includes(records.clonedSkillId) && response.status() === 200
  );
  await page.click('#platform-dialog button[type=submit]');
  await archiveSkillResponse;
  await page.waitForFunction(() => location.pathname === '/skills' && !location.search);
  pass('Skill clone, sharing and history-preserving archive are reachable from the detail page');

  await visit(`/plugins?id=${records.pluginId}`, '.capability-detail');
  assert.equal(await page.$eval('.capability-history', node => node.open), false);
  assert((await page.$eval('.capability-detail', node => node.innerText)).includes(records.toolName));
  await page.screenshot({ path: path.join(OUTPUT, 'plugin-detail.png'), fullPage: true });
  pass('Plugin detail keeps the capability bindings visible without a card wall');

  await page.click('[data-plugin-clone]');
  await page.waitForSelector('#platform-dialog[open] [name=new_name]');
  await page.$eval('#platform-dialog [name=new_name]', (node, value) => { node.value = value; }, '设备状态能力包副本-' + records.suffix);
  const clonePluginResponse = page.waitForResponse(response =>
    response.url().endsWith('/clone') && response.url().includes('/api/plugins/') &&
    response.status() === 201
  );
  await page.click('#platform-dialog button[type=submit]');
  await clonePluginResponse;
  await page.waitForFunction(sourceId => new URL(location.href).searchParams.get('id') !== sourceId, {}, records.pluginId);
  records.clonedPluginId = new URL(page.url()).searchParams.get('id');
  await page.waitForSelector('[data-plugin-share]');
  assert((await page.$eval('.capability-detail', node => node.innerText)).includes('仅自己'));
  await page.click('[data-plugin-share]');
  await page.waitForSelector('#platform-dialog[open]');
  const sharePluginResponse = page.waitForResponse(response =>
    response.url().endsWith('/share') && response.url().includes(records.clonedPluginId) && response.status() === 200
  );
  await page.click('#platform-dialog button[type=submit]');
  await sharePluginResponse;
  await page.waitForSelector('[data-plugin-archive]');
  await page.click('[data-plugin-archive]');
  await page.waitForSelector('#platform-dialog[open]');
  const archivePluginResponse = page.waitForResponse(response =>
    response.url().endsWith('/archive') && response.url().includes(records.clonedPluginId) && response.status() === 200
  );
  await page.click('#platform-dialog button[type=submit]');
  await archivePluginResponse;
  await page.waitForFunction(() => location.pathname === '/plugins' && !location.search);
  pass('Plugin clone, sharing and history-preserving archive are reachable from the detail page');

  await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 1 });
  for (const route of ['/capabilities', '/connectors', '/skills', '/plugins']) {
    await visit(route, '#main-content > .panel', route === '/capabilities' ? 1 : 0);
  }
  await page.goto(BASE + `/connectors?id=${records.connector.id}`, { waitUntil: 'networkidle0' });
  await page.waitForSelector('[data-connector-version]');
  await page.click('[data-connector-version]');
  await page.waitForSelector('#platform-dialog[open]');
  const mobile = await page.evaluate(() => {
    const rect = document.querySelector('#platform-dialog').getBoundingClientRect();
    return {
      overflow: document.documentElement.scrollWidth - innerWidth,
      left: rect.left, right: rect.right,
      toolColumns: getComputedStyle(document.querySelector('.capability-tool-row > div')).gridTemplateColumns.split(' ').length,
      primaryLinks: [...document.querySelectorAll('.enterprise-primary a')].filter(node => node.getClientRects().length).length,
    };
  });
  assert(mobile.overflow <= 1 && mobile.left >= 0 && mobile.right <= 390);
  assert.equal(mobile.toolColumns, 1); assert.equal(mobile.primaryLinks, 0);
  await audit('/connectors-mobile-dialog');
  await page.screenshot({ path: path.join(OUTPUT, 'connector-mobile.png'), fullPage: true });
  pass('All capability pages and the connector form fit a 390px viewport without duplicate navigation');

  assert.deepEqual(failures, []);
  assert.deepEqual(violations, []);
  fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({
    checks, failures, accessibilityFindings: violations,
    records: {
      connectorId: records.connector.id, clonedConnectorId: records.clonedConnectorId,
      categoryId: records.categoryId, skillId: records.skillId, clonedSkillId: records.clonedSkillId,
      pluginId: records.pluginId, clonedPluginId: records.clonedPluginId,
    },
  }, null, 2));
  console.log('RESULT_DIR ' + OUTPUT);
  console.log('ACCESSIBILITY_FINDINGS ' + violations.length);
})().catch(error => {
  failures.push(error.stack || error.message);
  if (fs.existsSync(OUTPUT)) fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({ checks, failures, accessibilityFindings: violations }, null, 2));
  console.error(error); process.exitCode = 1;
}).finally(async () => { if (browser) await browser.close(); });
