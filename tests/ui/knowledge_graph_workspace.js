/*
 * 中文说明（知识图谱工作台验收）：
 * 验证行为：本体 -> 抽取 -> 图谱 -> Bot 的常规页面在真实浏览器中可操作，图谱数据与后端一致。
 * 运行方式：需隔离服务（TEST5_E2E_BASE_URL，默认 8011）+ 本机 Edge + TEST5_E2E_PASSWORD。
 * 边界：抽取可能调用模型；会产生图谱与 Bot 数据（隔离环境）。
 */
/* Real Edge acceptance for the normal ontology -> extraction -> graph -> Bot pages. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');

const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011';
const PASSWORD = process.env.TEST5_E2E_PASSWORD;
const EDGE = process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const OUTPUT = path.resolve('.implementation/ui-results/knowledge-graph-' + Date.now().toString(36));
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

async function seed() {
  const suffix = Date.now().toString(36);
  const ontology = await call('POST', '/api/v1/ontologies', { name: '浏览器验收本体-' + suffix, description: '设备与部件' });
  const equipment = await call('POST', `/api/v1/ontologies/${ontology.id}/concepts`, {
    code: 'Equipment' + suffix, name: '设备', recognitionTerms: ['验收设备'],
    attributes: [{ name: 'status', label: '状态', type: 'string' }],
  });
  const component = await call('POST', `/api/v1/ontologies/${ontology.id}/concepts`, {
    code: 'Component' + suffix, name: '部件', recognitionTerms: ['验收部件'], attributes: [],
  });
  const relation = await call('POST', `/api/v1/ontologies/${ontology.id}/relation-types`, {
    code: 'HAS_PART' + suffix, name: '包含', sourceConceptId: equipment.id,
    targetConceptId: component.id, cardinality: 'one_to_many', directed: true,
  });
  const publishedOntology = await call('POST', `/api/v1/ontologies/${ontology.id}/publish`, { version: ontology.version, reason: '浏览器验收' });
  const job = await call('POST', '/api/v1/extractions', {
    name: '浏览器验收抽取-' + suffix, mode: 'structured', ontologyId: ontology.id,
    records: [{ equipment_id: 'EQ-UI', equipment_name: '验收设备', status: '运行', component_id: 'CP-UI', component_name: '验收部件' }],
    mappings: {
      entityMappings: [
        { conceptId: equipment.id, keyField: 'equipment_id', nameField: 'equipment_name', propertyFields: { status: 'status' } },
        { conceptId: component.id, keyField: 'component_id', nameField: 'component_name', propertyFields: {} },
      ],
      relationMappings: [{ relationTypeId: relation.id, sourceConceptId: equipment.id, targetConceptId: component.id,
        sourceKeyField: 'equipment_id', targetKeyField: 'component_id', propertyFields: {} }],
    },
  });
  const execution = await call('POST', `/api/v1/extractions/${job.id}/execute`, { version: job.version });
  const candidates = await call('GET', `/api/v1/extractions/${job.id}/runs/${execution.run.id}/candidates?limit=500`);
  await call('POST', `/api/v1/extractions/${job.id}/runs/${execution.run.id}/review`, { items: candidates.map(item => ({
    candidateId: item.id, version: item.version, decision: 'accepted', note: '浏览器验收',
  })) });
  const graph = await call('POST', '/api/v1/graphs', { name: '浏览器验收图谱-' + suffix, ontologyId: ontology.id });
  const materialized = await call('POST', `/api/v1/extractions/${job.id}/materialize`, {
    graphId: graph.id, graphVersion: graph.version, runId: execution.run.id,
  });
  const publishedGraph = await call('POST', `/api/v1/graphs/${graph.id}/publish`, {
    version: materialized.graph.version, reason: '浏览器验收',
  });
  const bot = await call('POST', '/api/v1/bots', {
    name: '浏览器验收助手-' + suffix, botType: 'knowledge', graphIds: [graph.id],
    instructions: '仅依据图谱回答', modelProfile: 'chat', apiEnabled: true,
  });
  const debug = await call('POST', `/api/v1/bots/${bot.id}/debug`, { query: '验收设备有什么部件', mode: 'retrieval' });
  assert(debug.references.length > 0);
  const publishedBot = await call('POST', `/api/v1/bots/${bot.id}/publish`, { version: bot.version, reason: '浏览器验收' });
  return { ontology: publishedOntology, job: execution.job, run: execution.run, graph: publishedGraph, bot: publishedBot };
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
  assert.equal(await page.$('#business-module-frame'), null, 'New Qknow module must be a normal platform page');
  const layout = await page.evaluate(() => ({
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    panels: document.querySelectorAll('.platform-content > .panel, .resource-columns > .panel').length,
    heading: document.querySelector('.page-heading h1')?.textContent.trim(),
    sidebarLinks: [...document.querySelectorAll('.enterprise-sidebar a')].filter(item => item.getClientRects().length).length,
  }));
  assert(layout.overflow <= 1, route + ' horizontal overflow');
  assert(layout.panels <= 7, route + ' contains excessive page panels');
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

  let layout = await visit(`/ontology?id=${records.ontology.id}`, '.resource-summary');
  assert.equal(layout.heading, records.ontology.name);
  assert.equal(await page.$$eval('.resource-columns .panel', nodes => nodes.length), 2);
  await page.screenshot({ path: path.join(OUTPUT, 'ontology-desktop.png'), fullPage: true });
  pass('Ontology normal page has compact concept/relation editing layout');

  layout = await visit(`/extraction?id=${records.job.id}`, '[data-candidate-check]');
  assert.equal(await page.$$eval('.evidence-details', nodes => nodes.length), 3);
  await page.click('.evidence-details summary');
  assert((await page.$eval('.evidence-details pre', node => node.textContent)).includes('rowNumber'));
  await page.screenshot({ path: path.join(OUTPUT, 'extraction-desktop.png'), fullPage: true });
  pass('Extraction page exposes review state and actual row evidence without a wizard wall');

  layout = await visit(`/graphs?id=${records.graph.id}`, '.graph-canvas svg');
  assert.equal(await page.$$eval('.graph-nodes g', nodes => nodes.length), 2);
  assert.equal(await page.$$eval('.graph-edges line', nodes => nodes.length), 1);
  await page.screenshot({ path: path.join(OUTPUT, 'graph-desktop.png'), fullPage: true });
  pass('Graph page renders persisted entities and relation in the normal enterprise UI');

  layout = await visit(`/bots?id=${records.bot.id}`, '[data-bot-debug]');
  assert.equal(await page.$$eval('.binding-summary span', nodes => nodes.length), 5);
  assert(await page.$('[data-create-bot-key]'));
  await page.screenshot({ path: path.join(OUTPUT, 'bot-desktop.png'), fullPage: true });
  pass('Bot page keeps binding, debugging, runs and API keys in two compact work areas');

  await visit('/ontology', '[data-new-ontology]');
  await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 1 });
  await page.reload({ waitUntil: 'networkidle0' });
  await page.waitForSelector('[data-new-ontology]');
  await page.click('[data-new-ontology]');
  await page.waitForSelector('#platform-dialog[open]');
  const mobile = await page.evaluate(() => {
    const dialog = document.querySelector('#platform-dialog').getBoundingClientRect();
    return { overflow: document.documentElement.scrollWidth - innerWidth, dialogLeft: dialog.left, dialogRight: dialog.right,
      primaryVisible: [...document.querySelectorAll('.enterprise-primary a')].filter(item => item.getClientRects().length).length,
      selectorVisible: Boolean(document.querySelector('.enterprise-area-select select')?.getClientRects().length) };
  });
  assert(mobile.overflow <= 1 && mobile.dialogLeft >= 0 && mobile.dialogRight <= 390);
  assert.equal(mobile.primaryVisible, 0); assert(mobile.selectorVisible);
  await page.screenshot({ path: path.join(OUTPUT, 'ontology-mobile-dialog.png'), fullPage: true });
  await audit('/ontology-mobile-dialog');
  pass('Mobile page and editing dialog fit without horizontal overflow or duplicate domain navigation');

  assert.deepEqual(failures, []);
  assert.deepEqual(violations, []);
  const result = { checks, failures, accessibilityFindings: violations, records: {
    ontologyId: records.ontology.id, extractionId: records.job.id, graphId: records.graph.id, botId: records.bot.id,
  } };
  fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify(result, null, 2));
  console.log('RESULT_DIR ' + OUTPUT);
  console.log('ACCESSIBILITY_FINDINGS ' + violations.length);
})().catch(error => {
  failures.push(error.stack || error.message);
  if (fs.existsSync(OUTPUT)) fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({ checks, failures, accessibilityFindings: violations }, null, 2));
  console.error(error); process.exitCode = 1;
}).finally(async () => { if (browser) await browser.close(); });
