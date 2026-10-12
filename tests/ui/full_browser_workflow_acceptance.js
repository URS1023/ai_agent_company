"use strict";

/*
 * 中文说明（工作流端到端浏览器验收）：
 * 验证行为：所有业务变更都从可见的浏览器控件发起；文件内唯一的 HTTP 服务代表客户设备与
 *   OpenAI 兼容模型提供方，不调用 Cloud Brain 应用接口。
 * 运行方式：需隔离服务与本机 Edge；会产生工作流/运行记录（限隔离环境）。
 *
 * Authoritative workflow acceptance: every business mutation is initiated from
 * a visible browser control. The only HTTP server created here represents a
 * customer device and an OpenAI-compatible model provider; it never calls the
 * Test5 application API directly.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const puppeteer = require("puppeteer-core");
const {
  clickHandle,
  clickText,
  createHttpSourceThroughUi,
  envelopeData,
  fillElement,
  fillLabeled,
  labeledControl,
  login,
  removeHttpSourceThroughUi,
  removeWorkflowThroughUi,
  sleep,
  visibleByText,
  visitBusiness,
  waitUiResponse,
} = require("./browser_only_helpers");

const BASE = process.env.TEST5_E2E_BASE_URL || "http://127.0.0.1:8030";
const PASSWORD = process.env.TEST5_E2E_PASSWORD;
const EDGE = process.env.TEST5_EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const RUN = Date.now().toString(36);
const OUTPUT = path.resolve(".implementation/ui-results/full-browser-workflow-" + RUN);
const SOURCE_NAME = "终验工作流设备源-" + RUN;
const FLOW_NAME = "终验设备计算流程-" + RUN;
const IMPORT_NAME = "终验导入流程-" + RUN;
const MATRIX_NAME = "终验全部节点契约-" + RUN;
const AI_NAME = "终验 AI 可运行流程-" + RUN;

const NODE_FORMS = [
  ["start", "开始", ["输入字段", "添加第一个输入字段"]],
  ["end", "结束", ["工作流要返回的输出变量", "完整输出"]],
  ["business-report", "整理业务报告", ["报告标题", "业务类型", "AI 解读已判定结果", "分析模型"]],
  ["business-notify", "业务报告通知", ["收件范围", "仅通知新问题、问题变化和已确认恢复"]],
  ["read-values", "读取值", ["从哪里读取", "选择数据源"]],
  ["calculate-values", "计算值", ["结果名称", "怎样计算", "第一个值", "第二个值", "添加计算"]],
  ["output-values", "输出结果", ["结果用途", "输出结果", "说明", "输出哪些字段"]],
  ["document-extractor", "文档提取器", ["文件间分隔符", "提取结果输出为 text"]],
  ["list-operator", "列表操作", ["过滤条件", "排序", "取前 N 项"]],
  ["reply", "回复输出", ["保留上游输出"]],
  ["http-request", "HTTP 采集", ["HTTP 数据源", "新建 HTTP 数据源"]],
  ["database-query", "数据库查询", ["数据库数据源", "只读 SQL", "最多返回行数"]],
  ["jsonpath-extract", "字段提取", ["记录 JSONPath", "保留全部顶层字段", "用响应样本识别字段"]],
  ["arithmetic", "计算字段", ["输出字段", "计算公式", "添加计算字段"]],
  ["filter", "记录筛选", ["判断方式", "判断字段"]],
  ["condition", "条件分支", ["判断方式", "判断字段"]],
  ["set-operation", "交并差", ["集合运算", "匹配字段（逗号分隔）"]],
  ["rule-evaluate", "逐项规则判定", ["判定用途", "规则名称", "问题等级", "问题说明"]],
  ["merge", "结果汇合", ["汇合实际执行的分支"]],
  ["report-analysis", "AI 数据分析", ["分析数据源", "分析问题", "最多返回行数", "改为分析上游数据"]],
  ["alert-evaluate", "设备预警业务", ["查找策略", "预警策略", "请选择策略"]],
  ["alert-notify", "预警站内通知", ["收件范围", "最低问题等级", "发送消息的报告结果"]],
  ["qc-appearance", "外观缺陷识别", ["质检设备", "外观指标", "设备检测服务", "请求参数来源"]],
  ["qc-component", "成分检测", ["质检设备", "成分指标", "设备检测服务", "请求参数来源"]],
  ["qc-evaluate", "质检业务判定", ["质检设备", "检验模板", "报告样本来源"]],
  ["output-table", "目标表写入", ["授权写入目标", "写入方式", "写入记录 JSONPath"]],
  ["llm", "大模型", ["模型配置", "系统约束", "提示词模板", "温度"]],
  ["agent", "智能体", ["Bot / 智能体", "执行方式", "问题模板", "运行已发布版本"]],
  ["knowledge", "知识检索", ["知识库", "检索问题模板", "检索方式", "返回条数", "最低相关分数"]],
  ["question", "提问", ["等待问题", "回答字段", "必须回答后恢复"]],
  ["classifier", "问题分类器", ["分类方式", "分类编码", "分类名称", "分类说明", "未匹配编码"]],
  ["extractor", "信息抽取", ["模型配置", "提示词模板", "字段名", "字段口径", "添加抽取字段"]],
  ["loop", "循环", ["固定次数", "更新字段", "计算公式", "添加字段更新", "保留每轮结果"]],
  ["iterator", "迭代", ["添加字段更新"]],
  ["while", "条件循环", ["判断方式", "最大次数", "更新字段", "计算公式", "添加字段更新"]],
  ["parallel", "并行分支", ["分支标签（逗号分隔）"]],
  ["aggregator", "聚合", ["按哪些字段分组", "结果名称", "怎样汇总", "计算哪个字段", "添加汇总计算"]],
  ["delay", "延迟", ["等待秒数", "不重复执行上游节点"]],
  ["subflow", "子流程", ["已发布子流程", "超时秒数", "允许子流程执行其已配置写回"]],
  ["setvar", "设置变量", ["保留对象输入字段", "变量名", "变量值来源", "输入 JSONPath"]],
  ["text", "文本处理", ["文本操作"]],
  ["template", "模板渲染", ["文本模板", "不执行脚本"]],
  ["code", "代码", ["受控 Python", "超时秒数", "把结果赋给 result"]],
  ["tool", "工具调用", ["工具", "参数 JSON", "将上游值作为 input 参数"]],
];

const checks = [];
const failures = [];
const consoleErrors = [];
const accessibilityFindings = [];
const deviceCalls = [];
const modelCalls = [];
const observedWorkflows = new Map();
const createdWorkflows = new Map();
const observationTasks = new Set();
let observedCatalog = [];
let browser;
let page;
let frame;
let fixture;
let downloadClient;
const downloads = new Map();
let source;
let flowId = "";
let importedId = "";
let matrixId = "";
let aiFlowId = "";
let importingWorkflow = false;

function pass(name, detail = {}) {
  checks.push({ name, ...detail });
  console.log("PASS " + name);
}

function phase(name) {
  console.log("PHASE " + name);
}

async function bodyOf(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf8");
}

function modelWorkflow() {
  assert(source?.id, "AI fixture needs the browser-created source");
  return {
    name: AI_NAME,
    description: "由实际模型接口返回、已绑定授权数据源的可编辑流程",
    domains: ["analysis", "alert"],
    graph: {
      nodes: [
        { id: "ai_start", kind: "start", title: "开始", position: { x: 40, y: 80 }, inputs: {}, config: {} },
        { id: "ai_collect", kind: "http-request", title: "采集设备数据", position: { x: 280, y: 80 }, inputs: {}, config: { datasourceId: source.id, datasourceVersion: source.version } },
        { id: "ai_math", kind: "arithmetic", title: "温度折算", position: { x: 520, y: 80 }, inputs: {}, config: { fields: [{ name: "temperatureF", formula: "temperature * 1.8 + 32" }] } },
        { id: "ai_rule", kind: "rule-evaluate", title: "判断高温", position: { x: 760, y: 80 }, inputs: {}, config: { mode: "alert", rules: [{ id: "hot", name: "高温", severity: "warning", message: "检查冷却系统", expression: { op: "gte", args: [{ field: "temperature" }, { value: "80.000000000000000001" }] } }] } },
        { id: "ai_reply", kind: "reply", title: "回复输出", position: { x: 1000, y: 80 }, inputs: {}, config: {} },
      ],
      edges: [
        { id: "ai_e1", source: "ai_start", target: "ai_collect", port: "out" },
        { id: "ai_e2", source: "ai_collect", target: "ai_math", port: "out" },
        { id: "ai_e3", source: "ai_math", target: "ai_rule", port: "out" },
        { id: "ai_e4", source: "ai_rule", target: "ai_reply", port: "out" },
      ],
    },
  };
}

function modelMessageText(content) {
  // 原生适配器可能分开发送 JSON 格式约束和业务系统提示，也可能使用文本块数组。
  return typeof content === "string" ? content : (Array.isArray(content)
    ? content.map(part => part?.text || "").join("\n") : "");
}

function writeModelCompletion(response, payload, content) {
  const common = { id: "browser-workflow-" + RUN, created: Math.floor(Date.now() / 1000),
    model: payload.model || "browser-workflow-model" };
  const usage = { prompt_tokens: 42, completion_tokens: 28, total_tokens: 70 };
  if (!payload.stream) {
    response.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
    response.end(JSON.stringify({ ...common, object: "chat.completion",
      choices: [{ index: 0, message: { role: "assistant", content }, finish_reason: "stop" }], usage }));
    return;
  }
  // DSH 的辅助调用统一使用流式协议；按代码点分块，验证中文和 JSON 可以完整重组。
  response.writeHead(200, { "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-cache" });
  const emit = (delta, finish = null) => response.write("data: " + JSON.stringify({ ...common,
    object: "chat.completion.chunk", choices: [{ index: 0, delta, finish_reason: finish }] }) + "\n\n");
  emit({ role: "assistant", content: "" });
  const characters = Array.from(content);
  for (let index = 0; index < characters.length; index += 64) emit({ content: characters.slice(index, index + 64).join("") });
  emit({}, "stop");
  if (payload.stream_options?.include_usage) response.write("data: " + JSON.stringify({ ...common,
    object: "chat.completion.chunk", choices: [], usage }) + "\n\n");
  response.end("data: [DONE]\n\n");
}

function startFixture() {
  fixture = http.createServer(async (request, response) => {
    const raw = await bodyOf(request);
    if (request.url === "/measurements") {
      deviceCalls.push({ method: request.method, raw });
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ items: [{ device: "COMP-A", temperature: "82.50", pressure: "1.25" }] }));
      return;
    }
    if (request.url === "/v1/chat/completions") {
      const payload = JSON.parse(raw || "{}");
      modelCalls.push(payload);
      const messages = payload.messages || [];
      const system = messages.filter(item => item.role === "system").map(item => modelMessageText(item.content)).join("\n");
      const content = system.includes("企业工作流架构师") ? JSON.stringify(modelWorkflow()) : "ok";
      writeModelCompletion(response, payload, content);
      return;
    }
    response.writeHead(404, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ error: { message: "fixture route not found" } }));
  });
  return new Promise(resolve => fixture.listen(0, "127.0.0.1", resolve));
}

async function platformVisit(route, ready = "#main-content") {
  const response = await page.goto(BASE + route, { waitUntil: "networkidle0", timeout: 90000 });
  assert.equal(response.status(), 200, route);
  await page.waitForSelector(ready, { visible: true, timeout: 90000 });
  await page.waitForFunction(selector => document.querySelector(selector)?.textContent.trim().length > 0, {}, ready);
}

async function configureReasoningModel(endpoint) {
  await platformVisit("/system/models", ".governance-panel");
  const existing = await page.$('[data-edit-model="reasoning"]');
  if (existing) {
    await clickHandle(existing);
  } else {
    await clickHandle(await page.$("[data-new-model]"));
    await fillElement(page, await page.waitForSelector('#platform-dialog [name="profile_name"]', { visible: true }), "reasoning");
  }
  await fillElement(page, await page.waitForSelector('#platform-dialog [name="provider"]', { visible: true }), "openai-compatible");
  await fillElement(page, await page.$('#platform-dialog [name="model"]'), "browser-workflow-model");
  await fillElement(page, await page.$('#platform-dialog [name="endpoint"]'), endpoint);
  await fillElement(page, await page.$('#platform-dialog [name="api_key"]'), "browser-only-secret");
  await fillElement(page, await page.$('#platform-dialog [name="capabilities"]'), "chat\nreasoning");
  await page.select('#platform-dialog [name="protocol"]', "openai-completions");
  await page.select('#platform-dialog [name="thinking"]', "disabled");
  await page.select('#platform-dialog [name="reasoning_effort"]', "off");
  const saved = await waitUiResponse(page,
    (response, url) => url.pathname.startsWith("/api/models/profiles") && ["POST", "PUT"].includes(response.request().method()),
    async () => clickHandle(await page.$('#platform-dialog button[type="submit"]')));
  assert([200, 201].includes(saved.status()), await saved.text());
  await page.waitForFunction(() => !document.querySelector("#platform-dialog")?.open, { timeout: 90000 });
  const probe = await waitUiResponse(page,
    (response, url) => url.pathname === "/api/models/probe/reasoning" && response.request().method() === "POST",
    async () => clickHandle(await page.$('[data-probe-model="reasoning"]')));
  assert.equal(probe.status(), 200, await probe.text());
  const probeBody = await probe.json();
  assert.equal(probeBody.status, "passed", JSON.stringify(probeBody));
  await page.waitForFunction(() => document.querySelector("#platform-toast")?.textContent.includes("通过"), { timeout: 90000 });
  pass("通过模型管理界面配置并实测 reasoning 模型", { endpoint });
}

async function workflowButton(label, selector = "button") {
  const controls = { "添加节点": 'button[aria-label="添加节点"]', "运行": ".wf-toolbar-run",
    "检查配置": '[aria-label="检查配置"]', "自动排布": 'button[aria-label="自动排布"]',
    "适应画布": 'button[aria-label="适应画布"]', "版本与运行": 'button[aria-label="版本与运行"]' };
  if (controls[label]) return clickHandle(await frame.waitForSelector(controls[label], { visible: true }));
  if (["AI 生成", "导出定义", "保存草稿"].includes(label)) {
    const menu = await frame.$(".wf-more-actions");
    if (!await menu.evaluate(node => node.open)) await clickHandle(await menu.$("summary"));
    return clickText(frame, ".wf-more-actions button", label);
  }
  await clickText(frame, selector, label);
}

async function workflowField(label, value, root = ".workflow-ui") {
  return fillLabeled(page, frame, label, value, root);
}

async function addNode(title) {
  if (!await frame.$(".wf-palette")) await workflowButton("添加节点");
  await clickText(frame, ".wf-palette-tabs button", "节点");
  const raw = await frame.evaluateHandle(expected => [...document.querySelectorAll(".wf-palette-group > button")]
    .find(item => item.getClientRects().length && item.querySelector("strong")?.textContent.trim() === expected) || null, title);
  const item = raw.asElement();
  assert(item, "Workflow node is missing from the palette: " + title);
  await clickHandle(item);
  await frame.waitForFunction(expected => document.querySelector(".wf-node.selected span")?.textContent.trim() === expected, {}, title);
  return frame.$eval(".wf-node.selected", node => node.dataset.nodeId);
}

async function observeWorkflowResponse(response) {
  const route = new URL(response.url()).pathname;
  if (!response.ok()) return;
  // 目录在本轮首次加载时完整核对，后续整页跳转可能销毁旧 iframe 的 CDP 响应缓存。
  if (route === "/api/v1/workflows/catalog" && !observedCatalog.length) observedCatalog = envelopeData(await response.json());
  if (/^\/api\/v1\/workflows(?:\/[^/]+)?$/.test(route)
    && ["POST", "PUT"].includes(response.request().method())) {
    // 导入成功会立即整页打开新流程；该路径由导入步骤核对状态和新页面重新读取的持久化记录。
    if (importingWorkflow && route === "/api/v1/workflows" && response.request().method() === "POST") return;
    const record = envelopeData(await response.json());
    if (record?.id) {
      observedWorkflows.set(record.id, record);
      if ((route === "/api/v1/workflows" && response.request().method() === "POST" && response.status() === 201)
        || createdWorkflows.has(record.id)) createdWorkflows.set(record.id, record);
    }
  }
}

async function waitSaved(name) {
  // 等界面显示持久化完成，再核对由真实页面请求得到的最新保存回执。
  await frame.waitForFunction(() => {
    const state = document.querySelector(".wf-autosave-state");
    return state && !state.classList.contains("is-saving") && !state.classList.contains("is-error")
      && state.textContent.includes("自动保存 ");
  }, { timeout: 90000 });
  await Promise.all([...observationTasks]);
  const record = [...observedWorkflows.values()].find(item => item.name === name);
  assert(record?.id, "没有观察到工作流真实保存回执：" + name);
  return record;
}

async function openNewWorkflow(name) {
  frame = await visitBusiness(page, BASE, "/workflows/new", ".wf-workbench-name");
  await fillElement(page, await frame.$(".wf-workbench-name"), name);
  await waitSaved(name);
  await frame.waitForFunction(() => !location.pathname.endsWith("/new"));
}

async function closeProperties() {
  const close = await frame.$(".wf-properties-close");
  if (close) await clickHandle(close);
}

async function connectNodes(from, to) {
  const sourceSelector = `[data-node-id="${from}"] .wf-node-handle-source.wf-node-handle-right`;
  const targetSelector = `[data-node-id="${to}"] .wf-node-handle-target.wf-node-handle-left`;
  const sourceHandle = await frame.$(sourceSelector), targetHandle = await frame.$(targetSelector);
  const sourceBox = await sourceHandle?.boundingBox(), targetBox = await targetHandle?.boundingBox();
  assert(sourceBox && targetBox, "连线端口必须在当前画布上可操作");
  await page.mouse.move(sourceBox.x + sourceBox.width / 2, sourceBox.y + sourceBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(targetBox.x + targetBox.width / 2, targetBox.y + targetBox.height / 2, { steps: 16 });
  await page.mouse.up();
  await frame.waitForFunction((left, right) => document.querySelector(left)?.classList.contains("connected")
    && document.querySelector(right)?.classList.contains("connected"), {}, sourceSelector, targetSelector);
}

async function publishWorkflow(id) {
  const response = await waitUiResponse(page,
    (candidate, url) => url.pathname === `/api/v1/workflows/${id}/publish` && candidate.request().method() === "POST",
    async () => { await workflowButton("发布"); await clickText(frame, ".el-message-box button", "确认"); });
  assert.equal(response.status(), 200, await response.text());
  const published = envelopeData(await response.json());
  assert.equal(published.status, "published");
  assert(published.publishedRevision > 0);
  await frame.waitForFunction(() => document.querySelector(".wf-autosave-state")?.textContent.includes("已发布"));
  return published;
}

async function validateWorkflow(expectedValid) {
  const response = await waitUiResponse(page,
    (candidate, url) => url.pathname === "/api/v1/workflows/validate" && candidate.request().method() === "POST",
    () => workflowButton("检查配置"));
  assert.equal(response.status(), 200, await response.text());
  const result = envelopeData(await response.json());
  assert.equal(result.valid, expectedValid, JSON.stringify(result));
  return result;
}

async function runPublishedWorkflow(id, inputs = {}) {
  await workflowButton("运行");
  await frame.waitForSelector(".wf-debug-panel", { visible: true });
  await workflowField("执行版本", "published", ".wf-debug-panel");
  await frame.waitForSelector(".wf-debug-panel textarea:not([disabled])", { visible: true });
  await fillElement(page, await frame.$(".wf-debug-panel textarea"), JSON.stringify(inputs));
  const response = await waitUiResponse(page,
    (candidate, url) => url.pathname === `/api/v1/workflows/${id}/runs` && candidate.request().method() === "POST",
    async () => clickHandle(await frame.$(".wf-debug-panel > footer button")));
  assert.equal(response.status(), 200, await response.text());
  const run = envelopeData(await response.json());
  assert.equal(run.status, "completed", run.errorMessage || JSON.stringify(run));
  await frame.waitForFunction(() => document.querySelector(".wf-result h2")?.textContent.includes("执行完成"));
  await clickText(frame, ".wf-result summary", "运行元信息与节点原始输出");
  return run;
}

async function closeDebugPanel() {
  await clickHandle(await frame.$('[aria-label="关闭调试和预览"]'));
  await frame.waitForSelector(".wf-debug-panel", { hidden: true });
}

async function closeVisibleElementPlusDialog(selector) {
  const raw = await frame.evaluateHandle(current => {
    const node = [...document.querySelectorAll(current)].find(item => item.getClientRects().length);
    const dialog = node?.closest(".el-dialog") || node;
    return dialog?.querySelector(".el-dialog__headerbtn") || null;
  }, selector);
  const close = raw.asElement();
  assert(close, "Visible dialog close button is missing: " + selector);
  await clickHandle(close);
  await frame.waitForSelector(selector, { hidden: true, timeout: 30000 });
}

async function runAxe(label) {
  await frame.waitForFunction(() => !document.querySelector(
    ".is-closing, [class*='enter-active'], [class*='leave-active']",
  ), { timeout: 30000 });
  await frame.addScriptTag({ path: require.resolve("axe-core/axe.min.js") });
  const findings = await frame.evaluate(async () => (await axe.run(document, {
    runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] },
  })).violations.map(item => ({
    id: item.id,
    impact: item.impact,
    nodes: item.nodes.slice(0, 8).map(node => ({ target: node.target, summary: node.failureSummary })),
  })));
  accessibilityFindings.push(...findings.map(item => ({ label, ...item })));
}

async function createPrimaryWorkflow() {
  await openNewWorkflow(FLOW_NAME);
  // 当前画布通过端口连线；先用可见连线的 Delete 操作删除初始直连。
  const initialEdge = await frame.$(".vue-flow__edge-path");
  const point = await initialEdge.evaluate(node => {
    const local = node.getPointAtLength(node.getTotalLength() / 4);
    const screen = new DOMPoint(local.x, local.y).matrixTransform(node.getScreenCTM());
    return { x: screen.x, y: screen.y };
  });
  await page.mouse.click(point.x, point.y);
  await page.keyboard.press("Delete");
  await frame.waitForFunction(() => !document.querySelector(".vue-flow__edge-path"));

  const httpId = await addNode("HTTP 采集");
  await workflowField("HTTP 数据源", source.id, ".wf-properties");
  const sourceSummary = await frame.$eval(".wf-source-summary", node => node.textContent);
  assert(sourceSummary.includes("GET") && sourceSummary.includes("/measurements") && sourceSummary.includes("自动只读采集"));
  assert.equal(await frame.$$eval(".wf-source-actions button", items => items.filter(item =>
    ["新建 HTTP 数据源", "编辑请求配置", "测试采集"].includes(item.textContent.trim())).length), 3);
  await workflowButton("编辑请求配置");
  await frame.waitForSelector(".http-config-dialog .http-form", { visible: true });
  assert.equal(await frame.$eval('.http-config-dialog input[type="url"]', item => item.value),
    `http://127.0.0.1:${fixture.address().port}/measurements`);
  await closeVisibleElementPlusDialog(".http-config-dialog");

  const arithmeticId = await addNode("计算字段");
  await workflowField("输出字段", "temperatureHalf", ".wf-properties");
  await workflowField("计算公式", "temperature / 2", ".wf-properties");
  const ruleId = await addNode("逐项规则判定");
  await workflowField("规则名称", "折算温度超限", ".wf-properties");
  await workflowField("判断字段", "temperatureHalf", ".wf-properties");
  await fillElement(page, await frame.$('.wf-expression input[aria-label="与什么比较"]'), "40");
  await workflowField("问题说明", "检查冷却系统", ".wf-properties");
  await clickText(frame, ".wf-expression button", "编辑表达式 JSON");
  await workflowField("完整表达式", '{"op":"gte","args":[{"field":"temperatureHalf"},{"value":9007199254740993}]}', ".wf-expression");
  await clickText(frame, ".wf-expression button", "应用表达式");
  await frame.waitForFunction(() => document.querySelector('.wf-expression [role="alert"]')?.textContent.includes("未应用该输入"));
  assert.equal(await frame.$eval('.wf-expression input[aria-label="与什么比较"]', node => node.value), "40");
  // 当前值编辑器将精确小数作为文本传递，后端数值比较按 Decimal 解释，不能先在浏览器舍入。
  await frame.select('.wf-expression select[aria-label="与什么比较来源"]', "text");
  await fillElement(page, await frame.$('.wf-expression input[aria-label="与什么比较"]'), "40.000000000000000001");
  await clickText(frame, ".wf-expression button", "应用表达式");
  await frame.waitForSelector('.wf-expression [role="alert"]', { hidden: true });
  await clickText(frame, ".wf-expression button", "收起表达式 JSON");
  const ids = await frame.$$eval(".wf-node", nodes => Object.fromEntries(nodes.map(node =>
    [node.querySelector(".wf-node-heading span").textContent.trim(), node.dataset.nodeId])));
  assert.equal(ids["HTTP 采集"], httpId);
  assert.equal(ids["计算字段"], arithmeticId);
  assert.equal(ids["逐项规则判定"], ruleId);
  await closeProperties();
  await workflowButton("自动排布");
  await workflowButton("适应画布");
  await sleep(300);
  for (const [from, to] of [["开始", "HTTP 采集"], ["HTTP 采集", "计算字段"], ["计算字段", "逐项规则判定"], ["逐项规则判定", "结束"]]) {
    await connectNodes(ids[from], ids[to]);
  }
  const savedFlow = await waitSaved(FLOW_NAME);
  flowId = savedFlow.id;
  assert.equal(savedFlow.graph.nodes.find(node => node.kind === "http-request").config.datasourceVersion, source.version);
  assert.equal(savedFlow.graph.nodes.find(node => node.kind === "rule-evaluate").config.rules[0].expression.args[1].value,
    "40.000000000000000001");
  assert.equal(savedFlow.graph.edges.length, 4);
  assert.equal(deviceCalls.length, 0);
  pass("浏览器创建、端口连线并自动保存可执行 HTTP、计算和逐项规则流程，编辑不触发设备", { flowId });

  await validateWorkflow(true);
  await frame.waitForFunction(() => document.querySelector(".wf-toolbar-checklist .wf-validation")?.textContent.includes("结构检查通过"));
  await runAxe("工作流编辑器");
  await page.screenshot({ path: path.join(OUTPUT, "workflow-editor.png"), fullPage: true });
  await clickHandle(await frame.$('[aria-label="关闭配置检查"]'));
  const published = await publishWorkflow(flowId);
  const run = await runPublishedWorkflow(flowId, { amount: "0.10000000000000000001" });
  const resultText = await frame.$eval(".wf-result", node => node.textContent);
  assert(resultText.includes("41.25") && resultText.includes("检查冷却系统") && resultText.includes("COMP-A"));
  assert.equal(deviceCalls.length, 1);
  assert.equal(Object.keys(run.result.nodes).length, 5);
  assert.equal(run.revision, published.publishedRevision);
  await runAxe("工作流真实运行结果");
  await page.screenshot({ path: path.join(OUTPUT, "workflow-result.png"), fullPage: true });
  pass("正式版本真实采集一次设备数据并展示精确计算、问题列表和节点追踪");

  await closeDebugPanel();
  await workflowButton("版本与运行");
  await clickText(frame, ".el-dialog button", "查看结果");
  await frame.waitForSelector(".wf-result", { visible: true });
  assert.equal(deviceCalls.length, 1);
  await closeDebugPanel();
  pass("从版本与运行历史重开持久化结果，不会重复调用设备");

  frame = await visitBusiness(page, BASE, "/workflows/" + flowId, ".workflow-ui .wf-editor");
  const startNode = savedFlow.graph.nodes.find(node => node.kind === "start");
  const first = await frame.$(`[data-node-id="${startNode.id}"]`);
  await first.focus();
  await first.press("ArrowRight");
  const edited = await waitSaved(FLOW_NAME);
  assert.equal(edited.graph.nodes.find(node => node.id === startNode.id).position.x, startNode.position.x + 20);
  assert.equal(edited.publishedRevision, published.publishedRevision);
  assert(edited.version > published.version);
  pass("键盘移动节点后自动保存新草稿，已发布正式版本保持独立");

  const downloadedIds = new Set(downloads.keys());
  const exportedResponse = await waitUiResponse(page,
    (response, url) => url.pathname.endsWith(`/workflows/${flowId}/export`) && response.request().method() === "GET",
    () => workflowButton("导出定义"));
  assert.equal(exportedResponse.status(), 200, "工作流导出请求应成功");
  // 直接核对用户实际下载的文件，避免把浏览器下载导航的 CDP 响应缓存当成交付结果。
  const deadline = Date.now() + 30000;
  let downloaded;
  while (!downloaded && Date.now() < deadline) {
    downloaded = [...downloads.values()].find(item => !downloadedIds.has(item.guid)
      && item.suggestedFilename === `workflow-${flowId}.json` && item.state === "completed");
    if (!downloaded) await sleep(50);
  }
  assert(downloaded, "导出的工作流 JSON 文件必须真正下载完成");
  const exported = JSON.parse(fs.readFileSync(path.join(OUTPUT, "downloads", downloaded.guid), "utf8"));
  assert.equal(exported.schemaVersion, 1);
  assert.equal(exported.workflow.graph.nodes.length, 5);
  const importFile = path.join(OUTPUT, "workflow-import.json");
  fs.writeFileSync(importFile, JSON.stringify({
    ...exported,
    workflow: { ...exported.workflow, id: flowId, name: IMPORT_NAME, version: 999 },
  }));
  frame = await visitBusiness(page, BASE, "/workflows", ".workflow-ui");
  importingWorkflow = true;
  const importedReload = page.waitForResponse(response => {
    const url = new URL(response.url());
    return /^\/api\/v1\/workflows\/[0-9A-HJKMNP-TV-Z]{26}$/.test(url.pathname)
      && url.pathname.split("/").pop() !== flowId
      && response.request().method() === "GET";
  });
  importedReload.catch(() => {});
  const imported = await waitUiResponse(page,
    (response, url) => url.pathname === "/api/v1/workflows" && response.request().method() === "POST",
    async () => {
      const input = await frame.$('input[type="file"][accept=".json"]');
      assert(input, "Workflow import input is missing");
      await input.uploadFile(importFile);
    });
  assert.equal(imported.status(), 201, "导入必须成功创建新流程");
  const reloadedResponse = await importedReload;
  assert.equal(reloadedResponse.status(), 200, "新页面必须成功重读导入的持久化流程");
  const importedFlow = envelopeData(await reloadedResponse.json());
  importingWorkflow = false;
  importedId = importedFlow.id;
  createdWorkflows.set(importedFlow.id, importedFlow);
  assert.notEqual(importedId, flowId);
  assert.equal(importedFlow.name, IMPORT_NAME);
  assert.notEqual(importedFlow.version, 999);
  pass("通过文件选择导出并重新导入独立副本，外部 ID 与版本不会覆盖原流程", { importedId });
}

async function createAndInspectAllNodes() {
  phase(`全部 ${NODE_FORMS.length} 类节点的可见契约`);
  await openNewWorkflow(MATRIX_NAME);
  await Promise.all([...observationTasks]);
  // 显式维护每种节点的表单预期，并与真实页面取得的后端目录逐项对照，不能只放宽数量。
  const expected = NODE_FORMS.map(([kind, title]) => ({ kind, title })).sort((a, b) => a.kind.localeCompare(b.kind));
  const available = observedCatalog.filter(item => item.available).map(({ kind, title }) => ({ kind, title }))
    .sort((a, b) => a.kind.localeCompare(b.kind));
  assert.deepEqual(available, expected, "后端可用节点与浏览器逐项验收合同不一致");
  await workflowButton("添加节点");
  const palette = await frame.$$eval(".wf-palette-group > button", nodes => nodes.map(node => ({
    title: node.querySelector("strong")?.textContent.trim(),
    description: node.querySelector("small")?.textContent.trim(),
  })));
  assert.deepEqual(palette.map(item => item.title).sort(), NODE_FORMS.filter(([kind]) => kind !== "start").map(item => item[1]).sort());
  assert(palette.every(item => item.description));
  await clickText(frame, ".wf-palette-tabs button", "开始");
  assert.deepEqual(await frame.$$eval(".wf-palette-group > button strong", nodes => nodes.map(node => node.textContent.trim())), ["开始"]);
  await clickText(frame, ".wf-palette-tabs button", "节点");

  const nodeIds = await frame.$$eval(".wf-node", nodes => Object.fromEntries(nodes.map(node => [
    node.querySelector("span").textContent.trim() === "开始" ? "start" : "end",
    node.dataset.nodeId,
  ])));
  for (const [kind, title] of NODE_FORMS.slice(2)) nodeIds[kind] = await addNode(title);
  assert.equal(await frame.$$eval(".wf-node", nodes => nodes.length), NODE_FORMS.length);
  for (const [kind, title, required] of NODE_FORMS) {
    phase(`检查节点表单：${title}（${kind}）`);
    const selector = `.wf-node[data-node-id="${nodeIds[kind]}"]`;
    await clickHandle(await frame.$(selector));
    await frame.waitForFunction(expectedTitle => document.querySelector(".wf-panel-title small")?.textContent.trim() === expectedTitle, {}, title);
    if (["loop", "iterator", "while"].includes(kind)) {
      await clickText(frame, ".wf-properties button", "添加字段更新");
      await workflowField("更新字段", "iterationValue", ".wf-properties");
      await workflowField("计算公式", "1 + 1", ".wf-properties");
    }
    if (kind === "alert-evaluate") {
      // 空白平台没有选定预警策略，修订选择器只应在真实选择策略后出现，不能伪造默认修订。
      assert.equal(await frame.$eval(".alert-policy-select select", node => node.value), "");
      assert.equal(await frame.$$eval(".alert-policy-select label", nodes => nodes.some(node =>
        node.textContent.trim().startsWith("固定策略修订"))), false);
    }
    try {
      await frame.waitForFunction(words => {
        const shown = document.querySelector(".wf-panel-content")?.innerText || "";
        return words.every(word => shown.includes(word));
      }, { timeout: 10000 }, required);
    } catch (error) {
      const shown = await frame.$eval(".wf-panel-content", node => node.innerText);
      throw new Error(`${title}（${kind}）表单缺少预期字段：${required.filter(word => !shown.includes(word)).join("、")}；实际表单：${shown}`, { cause: error });
    }
  }

  await clickHandle(await frame.$(`.wf-node[data-node-id="${nodeIds.condition}"]`));
  const operator = await labeledControl(frame, "判断方式", ".wf-properties");
  const labels = await operator.$$eval("option", items => items.map(item => item.textContent.trim()));
  for (const expected of ["大于等于", "小于等于", "不等于", "范围内（含边界）", "全部满足 AND", "任一满足 OR", "属于集合", "不属于集合"]) {
    assert(labels.includes(expected), "Missing condition operator: " + expected);
  }
  await operator.select("in");
  await frame.waitForFunction(() => document.querySelector(".wf-properties")?.textContent.includes("集合（JSON 数组）"));
  await workflowField("集合（JSON 数组）", '["华北","华东"]', ".wf-properties");

  await clickHandle(await frame.$(`.wf-node[data-node-id="${nodeIds.setvar}"]`));
  await workflowField("变量名", "approved", ".wf-properties");
  await workflowField("变量值来源", "value", ".wf-properties");
  await workflowField("固定值（JSON）", "{bad", ".wf-properties");
  await frame.waitForSelector('.wf-advanced-form [role="alert"]', { visible: true });
  await workflowField("固定值（JSON）", '{"approved":true}', ".wf-properties");
  await frame.waitForSelector('.wf-advanced-form [role="alert"]', { hidden: true });

  const record = await waitSaved(MATRIX_NAME);
  matrixId = record.id;
  assert.deepEqual(record.graph.nodes.map(node => node.kind).sort(), NODE_FORMS.map(item => item[0]).sort());
  const serialized = Object.fromEntries(record.graph.nodes.map(node => [node.kind, node]));
  assert.deepEqual(serialized.condition.config.expression.args[1].value, ["华北", "华东"]);
  assert.deepEqual(serialized.setvar.config.variables, [{ name: "approved", value: { approved: true } }]);
  await validateWorkflow(false);
  await frame.waitForFunction(() => document.querySelector(".wf-toolbar-checklist .wf-checklist-issues"));
  const validation = await frame.$eval(".wf-toolbar-checklist .wf-validation", node => node.textContent);
  assert(validation.includes("datasourceId") && validation.includes("节点不能从开始节点到达"));
  await runAxe("全部工作流节点属性表单");
  await page.screenshot({ path: path.join(OUTPUT, "all-workflow-nodes.png"), fullPage: true });
  pass(`${NODE_FORMS.length} 类节点均可从节点库添加、逐一打开专用属性表单并保存类型化配置`, { nodeCount: NODE_FORMS.length, kinds: NODE_FORMS.map(item => item[0]) });
  pass("配置检查阻止不可运行草稿发布，并给出资源字段与连线级修正原因");

  await page.setViewport({ width: 390, height: 844 });
  frame = await visitBusiness(page, BASE, "/workflows/" + matrixId, ".workflow-ui .wf-editor");
  for (const [kind, title] of NODE_FORMS) {
    await clickHandle(await frame.$(`.wf-node[data-node-id="${nodeIds[kind]}"]`));
    await frame.waitForFunction(expected => document.querySelector(".wf-panel-title small")?.textContent.trim() === expected, {}, title);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), title);
    assert(await frame.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), title);
  }
  await page.screenshot({ path: path.join(OUTPUT, "workflow-nodes-mobile.png"), fullPage: true });
  pass("全部节点编辑器在 390px 下使用局部画布滚动，页面无横向溢出");
  await page.setViewport({ width: 1500, height: 980 });
}

async function generateAndRunAiWorkflow() {
  phase("实际模型生成可运行工作流");
  frame = await visitBusiness(page, BASE, "/workflows/new", ".workflow-ui .wf-editor");
  await workflowButton("AI 生成");
  await frame.waitForSelector(".wf-ai-generator", { visible: true });
  const cards = await frame.$$(".wf-ai-resource-card");
  const card = await (async () => {
    for (const current of cards) if (await current.evaluate((node, name) => node.textContent.includes(name), SOURCE_NAME)) return current;
    return null;
  })();
  assert(card, "AI resource selector does not show the browser-created source");
  const checkbox = await card.$('input[type="checkbox"]');
  assert.equal(await checkbox.evaluate(node => node.checked), false, "AI generator must not preselect a source");
  await clickHandle(checkbox);
  await workflowField("草稿名称", AI_NAME, ".wf-ai-generator");
  await workflowField("业务目标", "采集设备温度，计算华氏温度，判断高温并返回问题列表", ".wf-ai-generator");
  const generatedResponse = await waitUiResponse(page,
    (response, url) => url.pathname === "/api/v1/workflows/generate" && response.request().method() === "POST",
    () => workflowButton("生成草稿"), 120000);
  assert.equal(generatedResponse.status(), 200, await generatedResponse.text());
  const generated = envelopeData(await generatedResponse.json());
  assert.equal(generated.workflow.validation.valid, true, JSON.stringify(generated.workflow.validation));
  await frame.waitForFunction(() => document.querySelectorAll(".wf-node").length === 5, { timeout: 90000 });
  assert(!(await frame.$eval(".workflow-ui", node => node.textContent)).includes("服务处理失败"));
  assert.notEqual(generated.generation?.mode, "fallback", "模型桩必须走真实 DSH 生成链路，不能由安全草稿兜底掩盖协议错误");
  const aiFlow = await waitSaved(AI_NAME);
  aiFlowId = aiFlow.id;
  assert.equal(aiFlow.graph.nodes.find(node => node.kind === "http-request").config.datasourceId, source.id);
  // 自动保存后刷新页面，验证生成结果已落盘，而不是只保留在前端状态。
  const restored = await waitUiResponse(page,
    (candidate, url) => url.pathname === `/api/v1/workflows/${aiFlowId}` && candidate.request().method() === "GET",
    async () => { frame = await visitBusiness(page, BASE, "/workflows/" + aiFlowId, ".workflow-ui .wf-editor"); });
  assert.equal(restored.status(), 200, await restored.text());
  assert.deepEqual(envelopeData(await restored.json()).graph, aiFlow.graph);
  assert.equal(await frame.$$eval(".wf-node", nodes => nodes.length), aiFlow.graph.nodes.length);
  assert.equal(await frame.$eval(".wf-workbench-name", node => node.value), AI_NAME);
  await validateWorkflow(true);
  await frame.waitForFunction(() => document.querySelector(".wf-toolbar-checklist .wf-validation")?.textContent.includes("结构检查通过"));
  await clickHandle(await frame.$('[aria-label="关闭配置检查"]'));
  await publishWorkflow(aiFlowId);
  await runPublishedWorkflow(aiFlowId);
  const shown = await frame.$eval(".wf-result", node => node.textContent);
  assert(shown.includes("180.5") && shown.includes("检查冷却系统"));
  assert(modelCalls.some(call => call.stream && (call.messages || []).some(message => modelMessageText(message.content).includes("企业工作流架构师"))));
  assert.equal(deviceCalls.length, 2);
  await page.screenshot({ path: path.join(OUTPUT, "ai-workflow-result.png"), fullPage: true });
  pass("AI 经本机模型桩 SSE 生成完整流程，自动保存与刷新一致、校验、发布并执行设备桩成功", { aiFlowId });
}

async function removeCreatedRecord(expectedPath, removeThroughUi) {
  const receipts = [];
  const observeDelete = response => {
    const pathname = new URL(response.url()).pathname;
    if (response.request().method() === "DELETE"
      && (pathname.startsWith("/api/v1/workflows/") || pathname.startsWith("/api/v1/collection/sources/"))) {
      receipts.push({ pathname, status: response.status() });
    }
  };
  page.on("response", observeDelete);
  try {
    // 已取得创建回执的资源必须由可见界面真正删除，不能将查不到行的 false 当作清理成功。
    assert.equal(await removeThroughUi(), true, `本轮创建资源未完成界面清理：${expectedPath}`);
    assert.deepEqual(receipts, [{ pathname: expectedPath, status: 200 }], "删除回执必须精确对应本轮创建的资源 ID");
  } finally {
    page.off("response", observeDelete);
  }
}

if (require.main === module) (async () => {
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, "isolated");
  assert(["127.0.0.1", "localhost"].includes(new URL(BASE).hostname));
  assert.notEqual(new URL(BASE).port, "8011", "Never run destructive acceptance against the user's live port");
  fs.mkdirSync(OUTPUT, { recursive: true });
  await startFixture();
  browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: true,
    args: ["--no-first-run", "--disable-gpu", "--window-size=1500,980"],
  });
  page = await browser.newPage();
  const downloadDirectory = path.join(OUTPUT, "downloads");
  fs.mkdirSync(downloadDirectory, { recursive: true });
  downloadClient = await browser.target().createCDPSession();
  downloadClient.on("Browser.downloadWillBegin", event => downloads.set(event.guid, { ...event, state: "inProgress" }));
  downloadClient.on("Browser.downloadProgress", event => {
    const record = downloads.get(event.guid);
    if (record) Object.assign(record, event);
  });
  await downloadClient.send("Browser.setDownloadBehavior", { behavior: "allowAndName", downloadPath: downloadDirectory, eventsEnabled: true });
  page.setDefaultTimeout(90000);
  await page.setViewport({ width: 1500, height: 980, deviceScaleFactor: 1 });
  page.on("pageerror", error => failures.push("pageerror: " + error.message));
  page.on("console", message => {
    if (message.type() !== "error") return;
    let sourcePath = "";
    try { sourcePath = new URL(message.location().url).pathname; } catch { /* 内联脚本可能没有来源 URL。 */ }
    // 仅允许浏览器可选图标的明确 404；所有应用错误保留原文并使验收失败。
    const classification = sourcePath === "/favicon.ico" && /404/.test(message.text()) ? "optional_favicon" : "application";
    consoleErrors.push({ classification, sourcePath, text: message.text() });
    if (classification === "application") failures.push("console: " + message.text());
  });
  page.on("dialog", dialog => dialog.accept());
  page.on("response", response => {
    const url = new URL(response.url());
    if (response.status() >= 500) failures.push(`${response.status()} ${response.request().method()} ${url.pathname}`);
    const pending = observeWorkflowResponse(response).catch(error => failures.push(
      `读取真实页面回执失败：${response.request().method()} ${url.pathname}：${error.message}`));
    observationTasks.add(pending);
    pending.finally(() => observationTasks.delete(pending));
  });

  await login(page, BASE, PASSWORD);
  await configureReasoningModel(`http://127.0.0.1:${fixture.address().port}/v1`);
  ({ frame, source } = await createHttpSourceThroughUi(page, BASE, {
    name: SOURCE_NAME,
    description: "压缩机温度与压力；浏览器创建后供工作流和 AI 显式选择。",
    url: `http://127.0.0.1:${fixture.address().port}/measurements`,
    method: "GET",
    recordPath: "$.items",
    fields: [
      { name: "device", path: "$.device", type: "string", required: true, comment: "设备编码" },
      { name: "temperature", path: "$.temperature", type: "decimal", required: true, comment: "摄氏温度" },
      { name: "pressure", path: "$.pressure", type: "decimal", required: true, comment: "压力" },
    ],
    allowPrivate: true,
    allowAutomatic: true,
  }));
  assert.equal(deviceCalls.length, 0);
  pass("通过 HTTP 数据源界面配置字段、JSONPath 与内网自动采集授权，保存阶段零请求", { sourceId: source.id });

  await createPrimaryWorkflow();
  await createAndInspectAllNodes();
  await generateAndRunAiWorkflow();
  assert.deepEqual(failures, []);
  assert.deepEqual(accessibilityFindings, []);
})().catch(error => {
  failures.push(error.stack || String(error));
  console.error(error);
  process.exitCode = 1;
}).finally(async () => {
  if (page) {
    try {
      if (failures.length) await page.screenshot({ path: path.join(OUTPUT, "failure.png"), fullPage: true });
      await Promise.all([...observationTasks]);
      for (const record of [...createdWorkflows.values()].reverse()) {
        try {
          await removeCreatedRecord(`/api/v1/workflows/${record.id}`,
            () => removeWorkflowThroughUi(page, BASE, record.name));
        } catch (error) { failures.push(`cleanup workflow ${record.id} ${record.name}: ${error.message}`); }
      }
      if (source) {
        try {
          await removeCreatedRecord(`/api/v1/collection/sources/${source.id}`,
            () => removeHttpSourceThroughUi(page, BASE, SOURCE_NAME));
        } catch (error) { failures.push(`cleanup source ${source.id}: ${error.message}`); }
      }
    } catch (error) {
      failures.push("cleanup: " + error.message);
      process.exitCode = 1;
    }
  }
  fs.writeFileSync(path.join(OUTPUT, "result.json"), JSON.stringify({
    checks,
    failures,
    consoleErrors,
    accessibilityFindings,
    browserCreated: { sourceId: source?.id || "", flowId, importedId, matrixId, aiFlowId },
    externalDeviceRequests: deviceCalls.length,
    externalModelRequests: modelCalls.length,
    observedNodeKinds: observedCatalog.filter(item => item.available).map(item => item.kind),
  }, null, 2));
  if (failures.length) process.exitCode = 1;
  if (browser) await browser.close();
  if (fixture) await new Promise(resolve => fixture.close(resolve));
  console.log("Evidence " + OUTPUT);
});

module.exports = { NODE_FORMS, modelMessageText, writeModelCompletion };
