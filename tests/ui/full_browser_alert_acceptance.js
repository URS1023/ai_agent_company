/*
 * 中文说明（设备预警全生命周期端到端验收）：
 * 验证行为：仅通过浏览器完成设备预警的完整生命周期，配套真实的本机设备数据桩。
 * 运行方式：需隔离服务与本机 Edge，并按需设置 TEST5_E2E_PASSWORD；结果写入 .implementation/ui-results/。
 * 边界：会创建并处理预警记录（仅限隔离环境）；桩服务只提供设备数据，不代替平台接口。
 */
"use strict";

// 全程使用真实产品页面与本轮回环设备桩。
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
  setCheckboxByText,
  sleep,
  visibleByText,
  visitBusiness,
  waitUiResponse,
  confirmElementPlus,
} = require("./browser_only_helpers");

const BASE = process.env.TEST5_E2E_BASE_URL || "http://127.0.0.1:8030";
const PASSWORD = process.env.TEST5_E2E_PASSWORD;
const EDGE = process.env.TEST5_EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const RUN = Date.now().toString(36);
const OUTPUT = path.resolve(".implementation/ui-results/full-browser-alert-" + RUN);
const NAMES = {
  source: "终验批量设备接口-" + RUN,
  device: "终验压缩机-" + RUN,
  pointTemperature: "终验冷却温度-" + RUN,
  pointDevice: "终验设备编号-" + RUN,
  advice: "终验冷却预案-" + RUN,
  rule: "终验温度超限策略-" + RUN,
  workflow: "终验设备预警闭环-" + RUN,
};

const checks = [];
const failures = [];
const cleanupObservations = [];
const accessibilityFindings = [];
const deviceCalls = [];
let browser;
let page;
let frame;
let fixture;
let source;
let workflowId = "";
let scheduleName = "";
let reportId = "";
let scheduleArchived = false;
const createdRecords = [];
const createdWorkflows = [];

function pass(name, detail = {}) {
  checks.push({ name, ...detail });
  console.log("PASS " + name);
}

async function workflowButton(label, selector = "button") {
  return clickText(frame, selector, label);
}

async function field(label, value, root = ".alerts-ui") {
  return fillLabeled(page, frame, label, value, root);
}

async function visit(route, ready = route === "/alerts" || route === "/alerts/devices" ? ".device-entry-page" : ".alerts-ui") {
  frame = await visitBusiness(page, BASE, route, ready);
  return frame;
}

async function currentBusinessFrame(ready) {
  frame = await page.$("#analytics-app") ? page : await (await page.waitForSelector("#business-module-frame", { visible: true })).contentFrame();
  assert(frame, "业务页面不存在");
  await frame.waitForSelector(ready, { visible: true });
  return frame;
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

async function saveAlert(kind) {
  const response = await waitUiResponse(page,
    (candidate, url) => url.pathname === `/api/v1/alerts/${kind}` && candidate.request().method() === "POST",
    () => workflowButton("保存配置"));
  assert.equal(response.status(), 201, await response.text());
  const row = envelopeData(await response.json());
  createdRecords.push({ kind, id: row.id, name: row.name });
  await frame.waitForSelector("#equipment-edit-form", { hidden: true, timeout: 30000 });
  return row;
}

async function filterAlert(name) {
  await field("名称", name, ".alert-filter");
  await workflowButton("查询");
  await frame.waitForFunction(expected => document.querySelector(".alert-table")?.textContent.includes(expected), {}, name);
}

async function removeAlertRecord(route, name, id) {
  await visit(route);
  await field("名称", name, ".alert-filter");
  // 查询会先清空表格；核对本次响应及其真实渲染，不能把加载中的空表当成记录丢失。
  const endpoint = `/api/v1/alerts/${route.split("/").at(-1)}`;
  const queried = await waitUiResponse(page,
    (candidate, url) => candidate.request().method() === "GET" && url.pathname === endpoint
      && url.searchParams.get("keyword") === name && url.searchParams.get("offset") === "0",
    () => workflowButton("查询"), 30000);
  const observed = { kind: route.split("/").at(-1), id, name, searchStatus: queried.status() };
  cleanupObservations.push(observed);
  assert.equal(queried.status(), 200, await queried.text());
  const records = envelopeData(await queried.json());
  assert(Array.isArray(records), "清理查询未返回记录数组：" + name);
  observed.returnedIds = records.map(record => record.id);
  assert(records.some(record => record.id === id && record.name === name), "本轮已创建记录未在查询响应中找到：" + name);
  const raw = await frame.waitForFunction(expected => {
    const rows = [...document.querySelectorAll(".alert-table tbody tr")]
      .filter(row => [...row.querySelectorAll("td")].some(cell => cell.textContent.trim() === expected));
    return rows.length === 1 ? rows[0] : false;
  }, { timeout: 30000 }, name);
  const row = raw.asElement();
  assert(row, "查询结果未渲染唯一记录行：" + name);
  observed.rendered = true;
  const remove = await visibleByText(row, "button", "移除");
  assert(remove, "Alert remove action is missing for " + name);
  const response = await waitUiResponse(page,
    (candidate, url) => url.pathname === `/api/v1/alerts/${route.split("/").at(-1)}/${id}` && candidate.request().method() === "DELETE",
    async () => { await clickHandle(remove); await confirmElementPlus(frame, "确认移除"); });
  observed.deleteStatus = response.status();
  assert.equal(response.status(), 200, await response.text());
  return true;
}

async function removeCreatedDevice(record) {
  await visit("/alerts");
  await field("搜索设备", record.name, ".device-search");
  const filtered = await waitUiResponse(page,
    (response, url) => response.request().method() === "GET" && url.pathname === "/api/v1/alerts/device-catalogue" && url.searchParams.get("keyword") === record.name,
    () => workflowButton("搜索", ".device-search button"));
  assert.equal(filtered.status(), 200);
  const card = await frame.waitForSelector(`[data-device-id="${record.id}"]`, { visible: true });
  const deleted = await waitUiResponse(page,
    (response, url) => response.request().method() === "DELETE" && url.pathname === `/api/v1/alerts/devices/${record.id}`,
    async () => { await clickText(card, "button", "移除"); await confirmElementPlus(frame, "确认移除"); });
  assert.equal(deleted.status(), 200, await deleted.text());
  await frame.waitForSelector(`[data-device-id="${record.id}"]`, { hidden: true });
}

async function closeElementDialog(containingSelector) {
  const raw = await frame.evaluateHandle(selector => {
    const child = [...document.querySelectorAll(selector)].find(node => node.getClientRects().length);
    return child?.closest(".el-dialog")?.querySelector(".el-dialog__headerbtn") || null;
  }, containingSelector);
  const close = raw.asElement();
  assert(close, "Dialog close button is missing for " + containingSelector);
  await clickHandle(close);
  await frame.waitForSelector(containingSelector, { hidden: true, timeout: 30000 });
}

async function platformVisit(route) {
  const response = await page.goto(BASE + route, { waitUntil: "networkidle0", timeout: 90000 });
  assert.equal(response.status(), 200, route);
  await page.waitForSelector("#main-content", { visible: true, timeout: 90000 });
  await page.waitForFunction(() => document.querySelector("#main-content")?.textContent.trim().length > 0);
}

async function platformRow(value) {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    const rows = await page.$$("#main-content tbody tr");
    for (const row of rows) {
      try {
        if (await row.evaluate((node, expected) => node.textContent.includes(expected), value)) return row;
      } catch { /* 系统操作完成后会异步替换表格，等待下一次可用行。 */ }
    }
    await sleep(50);
  }
  throw new Error("Platform row not found: " + value);
}

async function archiveSchedule() {
  if (!scheduleName) return;
  await platformVisit("/system/operations?tab=jobs");
  const row = await platformRow(scheduleName);
  const shown = await row.evaluate(node => node.textContent);
  assert(shown.includes("alert") && (shown.includes("paused") || shown.includes("暂停")));
  const action = await row.$('[data-job-action="archive"]');
  assert(action, "Schedule archive action is missing");
  const response = await waitUiResponse(page,
    (candidate, url) => url.pathname.endsWith("/status/archive") && candidate.request().method() === "POST",
    () => clickHandle(action));
  assert.equal(response.status(), 200, await response.text());
  scheduleArchived = true;
  pass("预警向导创建的调度在运行与运维中可见、默认暂停并可归档");
}

async function verifyAndArchiveMessage() {
  await platformVisit("/system/operations?tab=messages");
  const row = await platformRow(reportId);
  const shown = await row.evaluate(node => node.textContent);
  assert(shown.includes("alert") && shown.includes(NAMES.rule));
  const read = await row.$("[data-read-message]");
  if (read) {
    const response = await waitUiResponse(page,
      (candidate, url) => url.pathname.endsWith("/read") && candidate.request().method() === "POST",
      () => clickHandle(read));
    assert.equal(response.status(), 200, await response.text());
  }
  const refreshed = await platformRow(reportId);
  const archive = await refreshed.$("[data-archive-message]");
  assert(archive, "Alert inbox archive action is missing");
  const response = await waitUiResponse(page,
    (candidate, url) => url.pathname.endsWith("/archive") && candidate.request().method() === "POST",
    () => clickHandle(archive));
  assert.equal(response.status(), 200, await response.text());
  await page.waitForFunction(value => !document.querySelector("#main-content tbody")?.textContent.includes(value),
    { timeout: 90000 }, reportId);
  pass("预警站内消息通过系统消息界面完成查看、已读和归档");
}

(async () => {
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, "isolated");
  assert(["127.0.0.1", "localhost"].includes(new URL(BASE).hostname));
  assert.notEqual(new URL(BASE).port, "8011", "Never write to the user's live instance");
  fs.mkdirSync(OUTPUT, { recursive: true });

  fixture = http.createServer((request, response) => {
    const chunks = [];
    request.on("data", chunk => chunks.push(chunk));
    request.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf8");
      const payload = raw ? JSON.parse(raw) : {};
      deviceCalls.push({ url: request.url, method: request.method, payload });
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({
        items: [{ device: payload.device || "COMP-A", temperature: "82.50", pressure: "1.25" }],
      }));
    });
  });
  await new Promise(resolve => fixture.listen(0, "127.0.0.1", resolve));

  browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: true,
    args: ["--no-first-run", "--disable-gpu", "--window-size=1500,980"],
  });
  page = await browser.newPage();
  page.setDefaultTimeout(90000);
  await page.setViewport({ width: 1500, height: 980, deviceScaleFactor: 1 });
  page.on("dialog", dialog => dialog.accept());
  page.on("pageerror", error => failures.push("pageerror: " + error.message));
  page.on("response", response => {
    const url = new URL(response.url());
    if (response.status() >= 500) failures.push(`${response.status()} ${response.request().method()} ${url.pathname}`);
  });
  await login(page, BASE, PASSWORD);

  ({ frame, source } = await createHttpSourceThroughUi(page, BASE, {
    name: NAMES.source,
    description: "两台压缩机批量请求；提取设备编码、温度和压力。",
    url: `http://127.0.0.1:${fixture.address().port}/measurements`,
    method: "POST",
    bodies: [{ device: "COMP-A" }, { device: "COMP-B" }],
    recordPath: "$.items",
    fields: [
      { name: "device", path: "$.device", type: "string", required: true, comment: "设备编码" },
      { name: "temperature", path: "$.temperature", type: "decimal", required: true, comment: "实时温度" },
      { name: "pressure", path: "$.pressure", type: "decimal", required: true, comment: "实时压力" },
    ],
    allowPrivate: true,
    allowAutomatic: true,
    continueOnError: true,
  }));
  assert.equal(deviceCalls.length, 0);
  pass("通过界面配置批量 POST、JSONPath、字段类型和自动采集授权，保存不请求设备", { sourceId: source.id });

  await visit("/alerts");
  for (const label of ["测点管理", "专家经验", "预警策略"]) assert(await visibleByText(frame, ".management-links a", label));
  await workflowButton("新增设备");
  await frame.waitForSelector("#business-device-form", { visible: true });
  await field("设备编码", "COMP-" + RUN);
  await field("设备名称", NAMES.device);
  await field("设备类型", "压缩机");
  await field("安装位置", "一号车间");
  await field("预警 / 通用主采集源", source.id);
  const savedDevice = await waitUiResponse(page,
    (response, url) => url.pathname === "/api/v1/alerts/devices" && response.request().method() === "POST",
    () => workflowButton("保存设备"));
  assert.equal(savedDevice.status(), 201, await savedDevice.text());
  const device = envelopeData(await savedDevice.json());
  createdRecords.push({ kind: "devices", id: device.id, name: device.name });
  assert.equal(device.datasourceId, source.id);
  await frame.waitForSelector("#business-device-form", { hidden: true });
  const deviceCard = await frame.waitForSelector(`[data-device-id="${device.id}"]`, { visible: true });
  const cardText = await deviceCard.evaluate(node => node.textContent);
  assert(cardText.includes(NAMES.device) && cardText.includes("COMP-" + RUN) && cardText.includes("一号车间"));
  const actions = await deviceCard.$$eval(".catalog-card-actions button", nodes => nodes.map(node => ({ text: node.textContent.trim(), width: node.getBoundingClientRect().width, height: node.getBoundingClientRect().height })));
  assert(actions.length === 3 && actions.every(item => item.width >= 24 && item.height >= 24), JSON.stringify(actions));
  assert.equal(deviceCalls.length, 0);
  pass("设备主页面卡片保存采集源、设备信息及可点击的设置/移除/工作流入口", { deviceId: device.id });

  const entryResponse = await waitUiResponse(page,
    (response, url) => response.request().method() === "POST" && url.pathname === "/api/v1/workflows/device-entry",
    async () => { await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0", timeout: 90000 }), clickText(deviceCard, "button", "打开工作流")]); });
  assert.equal(entryResponse.status(), 200, await entryResponse.text());
  const entry = envelopeData(await entryResponse.json());
  if (entry.created) createdWorkflows.push({ id: entry.workflow.id, name: entry.workflow.name });
  assert.equal(entry.device.id, device.id); assert.equal(entry.domain, "alert"); assert.equal(entry.created, true);
  assert.equal(entry.workflow.status, "draft"); assert.equal(entry.workflow.publishedRevision, null);
  await currentBusinessFrame(".workflow-ui .wf-editor");
  assert.equal(new URL(page.url()).pathname, `/alerts/devices/${device.id}/workflow`);
  assert.equal(deviceCalls.length, 0);
  pass("设备卡片进入专属预警工作流，只创建绑定草稿、不发布且不采集设备", { workflowId: entry.workflow.id });

  await visit("/alerts");
  await workflowButton("测点管理", ".management-links a");
  await frame.waitForSelector(".alerts-ui .alert-content", { visible: true });
  await workflowButton("使用说明");
  await frame.waitForSelector(".business-process-guide", { visible: true });
  assert.equal(await frame.$$eval(".business-guide-flow > li", items => items.length), 6);
  assert((await frame.$eval(".business-process-guide", node => node.textContent)).includes("从真实数据到问题闭环"));
  await page.screenshot({ path: path.join(OUTPUT, "alert-guide.png"), fullPage: true });
  await closeElementDialog(".business-process-guide");
  pass("按需使用说明解释设备、测点、经验、策略、报告和问题闭环，不占用日常页面");

  await workflowButton("新增测点");
  await frame.waitForSelector("#equipment-edit-form", { visible: true });
  await field("所属设备", device.id);
  await field("测点号", "T1");
  await field("测点名称", NAMES.pointTemperature);
  await field("采集字段名", "temperature");
  await field("单位", "℃");
  await field("期望同步间隔（分钟）", "5");
  const temperaturePoint = await saveAlert("points");
  assert.equal(temperaturePoint.frequencyMinutes, 5);
  await workflowButton("新增测点");
  await frame.waitForSelector("#equipment-edit-form", { visible: true });
  await field("所属设备", device.id);
  await field("测点号", "D1");
  await field("测点名称", NAMES.pointDevice);
  await field("采集字段名", "device");
  await field("测点类型", "string");
  await field("期望同步间隔（分钟）", "5");
  await saveAlert("points");
  pass("测点通过界面维护数值与文本字段、单位、类型和同步间隔");

  await visit("/alerts/advice");
  await workflowButton("新增经验");
  await frame.waitForSelector("#equipment-edit-form", { visible: true });
  await field("经验名称", NAMES.advice);
  await field("适用工况", "持续高负载");
  await field("处置预案", "检查冷却系统及循环水");
  await field("预案简称", "冷却检查");
  const advice = await saveAlert("advice");

  await visit("/alerts/rules");
  await workflowButton("新增策略");
  await frame.waitForSelector("#equipment-edit-form", { visible: true });
  await field("策略名称", NAMES.rule);
  await field("设备", device.id);
  await field("问题定位字段", "device");
  await field("专家经验", advice.id);
  await field("规则项名称", "温度阈值");
  await field("判断字段", "temperature");
  // 精确长小数使用当前表单明确提供的固定文本类型，避免浏览器数值精度损失。
  await frame.select('#equipment-edit-form [aria-label="与什么比较来源"]', "text");
  await fillElement(page, await frame.$('#equipment-edit-form input[aria-label="与什么比较"]'), "80.000000000000000001");
  await field("问题说明", "检查冷却系统");
  const rule = await saveAlert("rules");
  assert.equal(rule.rules[0].expression.args[1].value, "80.000000000000000001");
  assert.equal(deviceCalls.length, 0);
  await runAxe("设备预警策略表单");
  pass("策略通过字段选择、专家经验和精确文本小数阈值配置，保存前不采集设备", { ruleId: rule.id });

  await filterAlert(NAMES.rule);
  await workflowButton("配置预警流程");
  await frame.waitForSelector(".alert-workflow-wizard", { visible: true });
  await frame.waitForFunction(() => document.querySelectorAll('.alert-point-grid input[type="checkbox"]').length === 2);
  assert.equal(await frame.$$eval('.alert-point-grid input[type="checkbox"]', items => items.filter(item => item.checked).length), 0);
  assert.equal(await frame.$eval('#alert-workflow-wizard button[type="submit"]', node => node.disabled), true);
  const wizardText = await frame.$eval(".alert-workflow-wizard", node => node.textContent);
  assert(wizardText.includes("2 条批量请求体") && wizardText.includes("固定策略修订"));
  assert.equal(await frame.$eval("#alert-workflow-wizard", form => [...form.querySelectorAll("label")]
    .find(label => label.textContent.includes("允许此 HTTP 来源执行非 GET 请求"))?.querySelector("input")?.checked), false);
  pass("预警向导固定策略和 HTTP 版本，展示批量请求详情且不默认选择测点");

  await page.setViewport({ width: 390, height: 844 });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  assert(await frame.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await runAxe("390px 设备预警向导");
  await page.screenshot({ path: path.join(OUTPUT, "alert-wizard-mobile.png"), fullPage: true });
  pass("完整预警向导在 390px 下无整页横向溢出");
  await page.setViewport({ width: 1500, height: 980 });

  await field("流程名称", NAMES.workflow, ".alert-workflow-wizard");
  await workflowButton("勾选必需测点");
  assert.equal(await frame.$$eval('.alert-point-grid input[type="checkbox"]', items => items.filter(item => item.checked).length), 2);
  await workflowButton("添加计算");
  await field("输出字段", "temperatureF", ".alert-workflow-wizard");
  await field("公式", "temperature * 1.8 + 32", ".alert-workflow-wizard");
  await setCheckboxByText(frame, "站内通知", true, ".alert-workflow-wizard");
  await setCheckboxByText(frame, "定时运行", true, ".alert-workflow-wizard");
  // 所有其它设置完成后仍须显式确认 HTTP 副作用；数据源允许自动采集不能替代本次许可。
  assert.equal(await frame.$eval('#alert-workflow-wizard button[type="submit"]', node => node.disabled), true);
  assert((await frame.$eval(".alert-wizard-footer", node => node.textContent)).includes("非 GET"));
  await setCheckboxByText(frame, "允许此 HTTP 来源执行非 GET 请求", true, ".alert-workflow-wizard");
  await frame.waitForFunction(() => !document.querySelector('#alert-workflow-wizard button[type="submit"]').disabled);
  const created = await waitUiResponse(page,
    (response, url) => url.pathname === "/api/v1/alerts/workflows" && response.request().method() === "POST",
    () => workflowButton("创建并发布"));
  assert.equal(created.status(), 201, await created.text());
  assert.equal(JSON.parse(created.request().postData()).confirmHttpWrites, true);
  const creation = envelopeData(await created.json());
  workflowId = creation.workflow.id;
  createdWorkflows.push({ id: workflowId, name: creation.workflow.name });
  scheduleName = creation.schedule?.name || "";
  assert.equal(creation.workflow.status, "published");
  assert.equal(creation.workflow.publishedRevision, 1);
  assert.equal(creation.schedule?.status, "paused");
  assert.deepEqual(creation.selectedFields.sort(), ["device", "temperature"]);
  assert.deepEqual(creation.workflow.graph.nodes.map(node => node.kind),
    ["start", "http-request", "jsonpath-extract", "arithmetic", "alert-evaluate", "alert-notify", "end"]);
  assert.equal(creation.workflow.graph.nodes.find(node => node.id === "collect").config.confirmWrites, true);
  assert.equal(deviceCalls.length, 0);
  pass("单一向导原子创建并发布采集、提取、计算、策略、通知和暂停调度流程", { workflowId });

  await archiveSchedule();
  frame = await visitBusiness(page, BASE, "/workflows/" + workflowId, ".workflow-ui .wf-editor");
  await clickHandle(await frame.waitForSelector(".wf-toolbar-run:not([disabled])", { visible: true }));
  await frame.waitForSelector(".wf-debug-panel", { visible: true });
  await fillLabeled(page, frame, "执行版本", "published", ".wf-debug-panel");
  await frame.waitForSelector(".wf-debug-panel > footer button:not([disabled])", { visible: true });
  const executionResponse = await waitUiResponse(page,
    (response, url) => url.pathname.endsWith(`/workflows/${workflowId}/runs`) && response.request().method() === "POST",
    async () => clickHandle(await frame.$(".wf-debug-panel > footer button")));
  assert.equal(executionResponse.status(), 200, await executionResponse.text());
  // HTTP 节点许可与数据库表写入许可独立，本轮没有目标表写入，不额外授予运行层写库权限。
  assert.equal(JSON.parse(executionResponse.request().postData()).confirmWrites, false);
  const execution = envelopeData(await executionResponse.json());
  assert.equal(execution.status, "completed", execution.errorMessage || JSON.stringify(execution));
  const reportOutput = Object.values(execution.result.outputs).find(value => value && typeof value === "object" && value.alertReportId);
  assert(reportOutput, JSON.stringify(execution));
  reportId = reportOutput.alertReportId;
  await frame.waitForSelector(".wf-result", { visible: true });
  await visit("/alerts");
  await workflowButton("查看报告与问题");
  await workflowButton("人工复核与历史检测记录", ".el-dialog summary");
  await frame.waitForFunction(name => [...document.querySelectorAll(".el-dialog .alert-table tbody tr")].some(row => row.textContent.includes(name)), {}, NAMES.device);
  const reportRow = (await frame.evaluateHandle(name => [...document.querySelectorAll(".el-dialog .alert-table tbody tr")].find(row => row.textContent.includes(name)), NAMES.device)).asElement();
  assert(reportRow, "新设备报告未出现在业务主页面的报告与问题入口");
  const reportResponse = await waitUiResponse(page,
    (response, url) => response.request().method() === "GET" && url.pathname === `/api/v1/alerts/reports/${reportId}`,
    () => clickText(reportRow, "button", "查看报告"));
  assert.equal(reportResponse.status(), 200);

  await frame.waitForSelector(".alert-report-detail", { visible: true });
  const reportText = await frame.$eval(".alert-report-detail", node => node.textContent);
  assert(reportText.includes("82.50") && reportText.includes("COMP-A") && reportText.includes("COMP-B") && reportText.includes("检查冷却系统"));
  assert.equal(deviceCalls.length, 2);
  assert.equal(reportOutput.issues.length, 2);
  await runAxe("真实设备预警报告");
  await page.screenshot({ path: path.join(OUTPUT, "alert-report.png"), fullPage: true });
  pass("正式流程执行两个已保存请求体，生成含原始证据的报告和两条问题", { reportId });

  await workflowButton("处理问题", ".alert-report-detail button");
  await frame.waitForSelector("#problem-action-form", { visible: true });
  await field("处置状态", "resolved", "#problem-action-form");
  await field("处置理由", "已检查并处理冷却回路", "#problem-action-form");
  const handled = await waitUiResponse(page,
    (response, url) => url.pathname.includes("/problems/") && response.request().method() === "POST",
    () => workflowButton("保存处置"));
  assert.equal(handled.status(), 200, await handled.text());
  await frame.waitForSelector("#problem-action-form", { hidden: true });
  await frame.waitForFunction(() => document.querySelector(".alert-report-detail .alert-table")?.textContent.includes("已解决"));
  await workflowButton("处理问题", ".alert-report-detail button");
  await frame.waitForFunction(() => document.querySelector(".alert-events")?.textContent.includes("已检查并处理冷却回路"));
  await page.screenshot({ path: path.join(OUTPUT, "alert-problem-history.png"), fullPage: true });
  await closeElementDialog("#problem-action-form");
  assert.equal(deviceCalls.length, 2);
  pass("问题处置状态、理由和不可变历史可从报告内完成，且不重新采集设备");

  const exported = await waitUiResponse(page,
    (response, url) => url.pathname.endsWith(`/alerts/reports/${reportId}/export`) && response.request().method() === "GET",
    () => workflowButton("下载报告"));
  assert.equal(exported.status(), 200, await exported.text());
  assert.equal(deviceCalls.length, 2);
  pass("下载预警报告只读取持久化证据，不重复请求设备");

  await verifyAndArchiveMessage();
  await page.setViewport({ width: 390, height: 844 });
  await visit("/alerts/reports?report=" + reportId);
  await frame.waitForSelector(".alert-report-detail", { visible: true });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  assert(await frame.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  const actualData = await frame.$('[data-testid="alert-actual-data"]');
  await actualData.focus();
  assert(await actualData.evaluate(node => document.activeElement === node));
  await actualData.press("ArrowRight");
  await frame.waitForFunction(() => document.querySelector('[data-testid="alert-actual-data"]').scrollLeft > 0);
  await page.screenshot({ path: path.join(OUTPUT, "alert-report-mobile.png"), fullPage: true });
  pass("报告深链接在 390px 可读，真实数据表可键盘横向滚动");

  await page.setViewport({ width: 1500, height: 980 });
  const legacy = await page.goto(BASE + "/alert/report", { waitUntil: "networkidle0", timeout: 90000 });
  assert.equal(legacy.status(), 200);
  assert.equal(new URL(page.url()).pathname, "/alerts/reports");
  pass("旧版 /alert/report 自动进入真实预警报告页，不再出现 404");

  assert.deepEqual(failures, []);
  assert.deepEqual(accessibilityFindings, []);
})().catch(error => {
  failures.push(error.stack || String(error));
  console.error(error);
  process.exitCode = 1;
 }).finally(async () => {
  const evidence = () => fs.writeFileSync(path.join(OUTPUT, "result.json"), JSON.stringify({
    checks, failures, accessibilityFindings, cleanupObservations,
    browserCreated: { sourceId: source?.id || "", workflowId, reportId, scheduleName, records: createdRecords, workflows: createdWorkflows },
    externalDeviceRequests: deviceCalls,
  }, null, 2));
  const cleanup = async (label, action) => {
    try { await action(); }
    catch (error) {
      failures.push(`cleanup ${label}: ${error.message}`);
      process.exitCode = 1;
      // 清理阶段也可能首次失败，关闭浏览器前独立保存当时页面，并保留原异常。
      if (page && !page.isClosed()) {
        try { await page.screenshot({ path: path.join(OUTPUT, `cleanup-failure-${failures.length}.png`), fullPage: true }); }
        catch (captureError) { failures.push(`cleanup screenshot ${label}: ${captureError.message}`); }
      }
    }
    evidence();
  };
  // 先保存失败现场，再按真实创建回执清理；未创建的记录不访问、不等待不存在的旧页面。
  evidence();
  if (page) {
    if (failures.length) await cleanup("screenshot", () => page.screenshot({ path: path.join(OUTPUT, "failure.png"), fullPage: true }));
    if (scheduleName && !scheduleArchived) await cleanup("schedule", archiveSchedule);
    for (const flow of [...createdWorkflows].reverse()) await cleanup(`workflow ${flow.id}`, async () => {
      assert(await removeWorkflowThroughUi(page, BASE, flow.name), "本轮已创建工作流未找到：" + flow.name);
    });
    for (const record of [...createdRecords].reverse()) await cleanup(`${record.kind} ${record.id}`, async () => {
      if (record.kind === "devices") await removeCreatedDevice(record);
      else assert(await removeAlertRecord(`/alerts/${record.kind}`, record.name, record.id), "本轮已创建记录未找到：" + record.name);
    });
    if (source) await cleanup("source", async () => {
      assert(await removeHttpSourceThroughUi(page, BASE, NAMES.source), "本轮已创建数据源未找到");
    });
  }
  if (browser) await cleanup("browser", () => browser.close());
  if (fixture) await cleanup("fixture", () => new Promise(resolve => fixture.close(resolve)));
  if (failures.length || accessibilityFindings.length) process.exitCode = 1;
  evidence();
  console.log("Evidence " + OUTPUT);
});
