/*
 * 中文说明（工作流节点目录契约审计）：
 * 验证行为：遍历调色板中每个工作流节点及其面向客户表单，核对声明与可编辑字段一致（契约审计）。
 * 运行方式：需隔离服务与本机 Edge；不是业务语义测试，而是"节点是否都有可用表单"的横向保障。
 * 边界：会打开各节点表单但不一定保存；业务语义由对应的专项用例覆盖。
 */
/* Real Edge contract audit for every workflow node and its customer-facing form. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');

const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011';
const RUN = Date.now().toString(36);
const OUTPUT = path.resolve('.implementation/ui-results/workflow-nodes-' + RUN);
const EXPECTED_KINDS = [
  'start', 'end', 'reply', 'http-request', 'database-query', 'jsonpath-extract',
  'arithmetic', 'filter', 'condition', 'set-operation', 'rule-evaluate', 'merge',
  'report-analysis', 'alert-evaluate', 'alert-notify', 'qc-appearance', 'qc-component',
  'qc-evaluate', 'output-table', 'llm', 'agent', 'knowledge', 'question', 'classifier',
  'extractor', 'loop', 'iterator', 'while', 'parallel', 'aggregator', 'delay', 'subflow',
  'setvar', 'text', 'template', 'code', 'tool',
];
const EXPECTED_FORM_TEXT = {
  start: ['接收本次运行的输入对象'],
  end: ['保留上游输出'],
  reply: ['保留上游输出'],
  'http-request': ['HTTP 数据源', '新建 HTTP 数据源'],
  'database-query': ['数据库数据源', '只读 SQL', '最多返回行数'],
  'jsonpath-extract': ['记录 JSONPath', '保留全部顶层字段', '用响应样本识别字段'],
  arithmetic: ['输出字段', '计算公式', '添加计算字段'],
  filter: ['判断方式', '判断字段'],
  condition: ['判断方式', '判断字段'],
  'set-operation': ['集合运算', '匹配字段（逗号分隔）'],
  'rule-evaluate': ['判定用途', '规则名称', '问题等级', '问题说明'],
  merge: ['汇合实际执行的分支'],
  'report-analysis': ['分析数据源', '分析问题', '最多返回行数', '改为分析上游数据'],
  'alert-evaluate': ['查找预警策略', '预警策略', '固定策略修订'],
  'alert-notify': ['收件范围', '最低问题等级', '发送消息的报告结果'],
  'qc-appearance': ['质检设备', '外观指标', '设备检测服务', '请求参数来源'],
  'qc-component': ['质检设备', '成分指标', '设备检测服务', '请求参数来源'],
  'qc-evaluate': ['质检设备', '检验模板', '报告样本来源'],
  'output-table': ['授权写入目标', '写入方式', '写入记录 JSONPath'],
  llm: ['模型配置', '系统约束', '提示词模板', '温度'],
  agent: ['Bot / 智能体', '执行方式', '问题模板', '运行已发布版本'],
  knowledge: ['知识库', '检索问题模板', '检索方式', '返回条数', '最低相关分数'],
  question: ['等待问题', '回答字段', '必须回答后恢复'],
  classifier: ['分类编码', '分类名称', '判断方式', '未匹配编码'],
  extractor: ['模型配置', '提示词模板', '字段名', '字段口径', '添加抽取字段'],
  loop: ['固定次数', '更新字段', '计算公式', '添加字段更新', '保留每轮结果'],
  iterator: ['添加字段更新'],
  while: ['判断方式', '最大次数', '更新字段', '计算公式', '添加字段更新'],
  parallel: ['分支标签（逗号分隔）'],
  aggregator: ['分组字段（逗号分隔）', '结果字段', '聚合方式', '来源字段'],
  delay: ['等待秒数', '不重复执行上游节点'],
  subflow: ['已发布子流程', '超时秒数', '允许子流程执行其已配置写回'],
  setvar: ['保留对象输入字段', '变量名', '变量值来源', '输入 JSONPath'],
  text: ['文本操作'],
  template: ['文本模板', '不执行脚本'],
  code: ['受控 Python', '沙箱默认禁止网络和子进程'],
  tool: ['工具', '参数 JSON', '将上游值作为 input 参数'],
};

const checks = [];
const failures = [];
const violations = [];
const inspected = [];
const cancelledRequests = [];
let browser;
let page;
let frame;
let workflowId;

function pass(label) {
  checks.push(label);
  console.log('PASS ' + label);
}

async function api(url, method = 'GET', body) {
  return page.evaluate(async (url, method, body) => {
    const response = await fetch(url, {
      method,
      headers: {
        'Content-Type': 'application/json',
        Authorization: 'Bearer ' + localStorage.getItem('agent_token'),
        'X-Workspace-Id': localStorage.getItem('agent_workspace_id'),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await response.text();
    return { status: response.status, body: text ? JSON.parse(text) : null };
  }, url, method, body);
}

async function button(label, selector = 'button') {
  await frame.waitForFunction(
    (label, selector) => [...document.querySelectorAll(selector)].some(
      item => item.getClientRects().length && item.textContent.trim() === label && !item.disabled,
    ),
    {}, label, selector,
  );
  for (const item of await frame.$$(selector)) {
    if (await item.evaluate((node, label) => node.getClientRects().length && node.textContent.trim() === label, label)) {
      await item.scrollIntoView();
      await item.click();
      return;
    }
  }
  throw new Error('Button missing: ' + label);
}

async function selectNode(id, kind, requiredText) {
  const selector = `.wf-node[data-node-id="${id}"]`;
  const item = await frame.waitForSelector(selector);
  await item.evaluate(node => node.click());
  await frame.waitForFunction(
    (kind, requiredText) => {
      const form = document.querySelector('.wf-properties');
      const text = form?.textContent || '';
      return text.includes(kind) && requiredText.every(value => text.includes(value));
    },
    {}, kind, requiredText,
  );
  return frame.$eval('.wf-properties', element => element.textContent || '');
}

async function formControl(label, selector = 'input,textarea,select') {
  const handle = await frame.evaluateHandle((label, selector) => {
    const labels = [...document.querySelectorAll('.wf-properties label')];
    return labels.find(item => item.getClientRects().length && (item.textContent || '').includes(label))?.querySelector(selector) || null;
  }, label, selector);
  const element = handle.asElement();
  assert(element, 'Form control missing: ' + label);
  return element;
}

async function choose(label, value) {
  const element = await formControl(label, 'select');
  await element.select(value);
}

async function enter(label, value) {
  const element = await formControl(label, 'input,textarea');
  await element.scrollIntoView();
  await element.click({ clickCount: 3 });
  await page.keyboard.down('Control');
  await page.keyboard.press('KeyA');
  await page.keyboard.up('Control');
  await element.type(value);
  await element.press('Tab');
}

async function audit(label) {
  await frame.waitForFunction(() => !document.querySelector(
    '.el-message, [class*="enter-active"], [class*="leave-active"]',
  ));
  await frame.addScriptTag({ path: require.resolve('axe-core/axe.min.js') });
  const found = await frame.evaluate(async () => (await axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] },
  })).violations.map(item => ({
    id: item.id,
    impact: item.impact,
    nodes: item.nodes.map(node => ({ target: node.target, summary: node.failureSummary })),
  })));
  violations.push(...found.map(item => ({ label, ...item })));
}

(async () => {
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, 'isolated');
  assert(process.env.TEST5_E2E_PASSWORD);
  assert(['127.0.0.1', 'localhost'].includes(new URL(BASE).hostname));
  const health = await (await fetch(BASE + '/api/health')).json();
  assert(['test', 'ui-validation'].includes(health.app));
  fs.mkdirSync(OUTPUT, { recursive: true });

  browser = await puppeteer.launch({
    executablePath: process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    headless: true,
  });
  page = await browser.newPage();
  await page.setViewport({ width: 1600, height: 1100 });
  page.on('pageerror', error => failures.push('pageerror: ' + error.message));
  page.on('console', message => { if (message.type() === 'error') failures.push('console: ' + message.text()); });
  page.on('response', response => { if (response.status() >= 500) failures.push(response.status() + ' ' + response.url()); });
  page.on('requestfailed', request => {
    const reason = request.failure()?.errorText;
    const message = request.url() + ' · ' + reason;
    if (reason === 'net::ERR_ABORTED') cancelledRequests.push(message);
    else failures.push('requestfailed: ' + message);
  });

  await page.goto(BASE, { waitUntil: 'networkidle0' });
  await page.type('#login-username', 'workspace-admin');
  await page.type('#login-password', process.env.TEST5_E2E_PASSWORD);
  await page.click('#login-btn');
  await page.waitForSelector('.overview-intro');

  const catalogResponse = await api('/api/v1/workflows/catalog');
  assert.equal(catalogResponse.status, 200, JSON.stringify(catalogResponse.body));
  const catalog = catalogResponse.body.data;
  assert.deepEqual(catalog.map(item => item.kind), EXPECTED_KINDS);
  assert(catalog.every(item => item.available && item.title && item.description));
  pass('Catalog exposes exactly 37 executable nodes with a title, description and editable contract');

  const nodes = catalog.map((item, index) => ({
    id: 'matrix_' + item.kind.replaceAll('-', '_'),
    kind: item.kind,
    title: item.title,
    position: { x: 30 + (index % 7) * 230, y: 40 + Math.floor(index / 7) * 125 },
    inputs: {},
    config: JSON.parse(JSON.stringify(item.defaultConfig)),
  }));
  const start = nodes.find(item => item.kind === 'start');
  const end = nodes.find(item => item.kind === 'end');
  const created = await api('/api/v1/workflows', 'POST', {
    name: '全部节点界面验收 ' + RUN,
    description: '隔离环境逐节点检查，不访问外部业务系统',
    domains: ['general', 'analysis', 'alert', 'quality'],
    graph: { nodes, edges: [{ id: 'matrix_start_end', source: start.id, target: end.id, port: 'out' }] },
  });
  assert.equal(created.status, 201, JSON.stringify(created.body));
  workflowId = created.body.data.id;

  assert.equal((await page.goto(BASE + '/workflows/' + workflowId, { waitUntil: 'networkidle0' })).status(), 200);
  frame = await (await page.waitForSelector('#business-module-frame')).contentFrame();
  await frame.waitForSelector('.workflow-ui .wf-editor', { visible: true });
  await button('添加节点');
  const palette = await frame.$$eval('.wf-palette > button', items => items.map(item => ({
    title: item.querySelector('strong')?.textContent?.trim(),
    description: item.querySelector('small')?.textContent?.trim(),
  })));
  assert.equal(palette.length, 37);
  assert.deepEqual(palette.map(item => item.title), catalog.map(item => item.title));
  assert(palette.every(item => item.description));
  await page.screenshot({ path: path.join(OUTPUT, 'all-node-palette.png'), fullPage: true });
  pass('The visual node library shows all 37 catalog nodes without hidden or placeholder entries');

  for (const entry of catalog) {
    const id = 'matrix_' + entry.kind.replaceAll('-', '_');
    const formText = await selectNode(id, entry.kind, EXPECTED_FORM_TEXT[entry.kind]);
    const properties = Object.keys(entry.configSchema?.properties || {});
    if (properties.length) {
      const contractText = await frame.$eval('.wf-contract-details', item => item.textContent || '');
      for (const property of properties) assert(contractText.includes(property), `${entry.kind} does not expose contract property ${property}`);
    }
    inspected.push({ kind: entry.kind, title: entry.title, properties, formText: EXPECTED_FORM_TEXT[entry.kind] });
  }
  assert.equal(inspected.length, 37);
  pass('Every node opens its specific property form and exposes every backend contract property');

  await selectNode('matrix_condition', 'condition', EXPECTED_FORM_TEXT.condition);
  const operator = await formControl('判断方式', 'select');
  const operatorLabels = await operator.$$eval('option', items => items.map(item => item.textContent.trim()));
  for (const label of ['大于等于', '小于等于', '不等于', '范围内（含边界）', '全部满足 AND', '任一满足 OR', '属于集合', '不属于集合']) {
    assert(operatorLabels.includes(label), 'Missing comparison operator ' + label);
  }
  await operator.select('in');
  await frame.waitForFunction(() => document.querySelector('.wf-properties')?.textContent.includes('集合（JSON 数组）'));
  await enter('集合（JSON 数组）', '["华北","华东"]');
  pass('Condition form supports comparison, range, AND/OR and membership operators with JSON collections');

  await selectNode('matrix_text', 'text', EXPECTED_FORM_TEXT.text);
  const textOperation = await formControl('文本操作', 'select');
  assert.deepEqual(await textOperation.$$eval('option', items => items.map(item => item.value)), [
    'stringify', 'trim', 'upper', 'lower', 'replace', 'split', 'join',
  ]);
  await textOperation.select('replace');
  await frame.waitForFunction(() => document.querySelector('.wf-properties')?.textContent.includes('替换为'));
  await textOperation.select('split');
  await frame.waitForFunction(() => document.querySelector('.wf-properties')?.textContent.includes('分隔符'));
  pass('Text node exposes all seven operations and the operation-specific controls');

  await selectNode('matrix_setvar', 'setvar', EXPECTED_FORM_TEXT.setvar);
  await enter('变量名', 'approved');
  await choose('变量值来源', 'value');
  await frame.waitForFunction(() => document.querySelector('.wf-properties')?.textContent.includes('固定值（JSON）'));
  await enter('固定值（JSON）', '{bad');
  await frame.waitForSelector('.wf-advanced-form [role="alert"]', { visible: true });
  await enter('固定值（JSON）', '{"approved":true}');
  await frame.waitForSelector('.wf-advanced-form [role="alert"]', { hidden: true });
  pass('Literal variable editor rejects malformed JSON in place and accepts a structured fixed value');

  const saveResponse = page.waitForResponse(response =>
    new URL(response.url()).pathname === '/api/v1/workflows/' + workflowId && response.request().method() === 'PUT',
  );
  await button('保存草稿');
  assert.equal((await saveResponse).status(), 200);
  const saved = await api('/api/v1/workflows/' + workflowId);
  assert.equal(saved.status, 200);
  const savedNodes = Object.fromEntries(saved.body.data.graph.nodes.map(item => [item.kind, item]));
  assert.deepEqual(savedNodes.condition.config.expression.args[1].value, ['华北', '华东']);
  assert.deepEqual(savedNodes.setvar.config.variables, [{ name: 'approved', value: { approved: true } }]);
  assert.equal(savedNodes.setvar.config.variables[0].path, undefined);
  pass('Visual edits serialize to the saved workflow without dropping types or retaining stale source properties');

  await audit('All workflow node forms');
  await page.screenshot({ path: path.join(OUTPUT, 'set-variable-json.png'), fullPage: true });
  await page.setViewport({ width: 390, height: 844 });
  assert.equal((await page.goto(BASE + '/workflows/' + workflowId, { waitUntil: 'networkidle0' })).status(), 200);
  frame = await (await page.waitForSelector('#business-module-frame')).contentFrame();
  await frame.waitForSelector('.workflow-ui .wf-editor', { visible: true });
  const mobileNode = await frame.waitForSelector('.wf-node[data-node-id="matrix_knowledge"]');
  await mobileNode.evaluate(node => node.click());
  await frame.waitForFunction(() => document.querySelector('.wf-properties')?.textContent.includes('最低相关分数'));
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  assert(await frame.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await page.screenshot({ path: path.join(OUTPUT, 'knowledge-node-mobile.png'), fullPage: true });
  pass('Node properties remain usable at 390px with local canvas scrolling and no page-level overflow');

  assert.deepEqual(failures, []);
  assert.deepEqual(violations, []);
})().catch(error => {
  failures.push(error.stack || String(error));
  console.error(error);
  process.exitCode = 1;
}).finally(async () => {
  if (page && workflowId) {
    try {
      const row = await api('/api/v1/workflows/' + workflowId);
      if (row.status === 200) {
        const removed = await api('/api/v1/workflows/' + workflowId + '?version=' + row.body.data.version, 'DELETE');
        assert.equal(removed.status, 200);
      }
    } catch (error) {
      failures.push('cleanup: ' + error.message);
      process.exitCode = 1;
    }
  }
  fs.mkdirSync(OUTPUT, { recursive: true });
  fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({
    checks, failures, violations, inspected, cancelledRequests,
  }, null, 2));
  if (browser) await browser.close();
  console.log('Evidence ' + OUTPUT);
});
