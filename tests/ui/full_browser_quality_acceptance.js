/*
 * 中文说明（质检全生命周期端到端验收）：
 * 验证行为：仅通过浏览器完成质检流程，外观/组件数据由真实的本地 HTTP 夹具提供。
 * 运行方式：需隔离服务与本机 Edge + TEST5_E2E_PASSWORD；夹具服务只提供被检测的外观/组件响应。
 * 边界：会产生质检记录（隔离环境）；不允许直接调用业务 JSON 接口绕开界面。
 */
"use strict";

/* 全部业务操作从真实页面发起，检测输入只由本轮回环 HTTP 桩提供。 */
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const puppeteer = require("puppeteer-core");
const {
  clickHandle,
  clickText,
  confirmElementPlus,
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
} = require("./browser_only_helpers");

const BASE = process.env.TEST5_E2E_BASE_URL || "http://127.0.0.1:8030";
const PASSWORD = process.env.TEST5_E2E_PASSWORD;
const EDGE = process.env.TEST5_EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const RUN = Date.now().toString(36);
const OUTPUT = path.resolve(".implementation/ui-results/full-browser-quality-" + RUN);
const NAMES = {
  appearanceSource: "终验外观检测服务-" + RUN,
  componentSource: "终验成分检测服务-" + RUN,
  device: "终验视觉光谱一体机-" + RUN,
  carbon: "终验碳含量-" + RUN,
  appearance: "终验表面外观-" + RUN,
  template: "终验外观成分标准-" + RUN,
  manualSample: "MANUAL-" + RUN,
  automaticSample: "AUTO-" + RUN,
  workflow: "终验外观成分自动检测-" + RUN,
  image: "终验质检原图-" + RUN + ".png",
};

const checks = [];
const failures = [];
const accessibilityFindings = [];
const detectorCalls = [];
const qualityRecords = [];
let browser;
let page;
let frame;
let fixture;
let appearanceSource;
let componentSource;
let device;
let workflowId = "";
let automaticInspectionId = "";
let uploadedImageId = "";
let uploadedImageName = "";

function pass(name, detail = {}) {
  checks.push({ name, ...detail });
  console.log("PASS " + name);
}

async function visit(route, ready = ".quality-ui") {
  frame = await visitBusiness(page, BASE, route, ready);
  return frame;
}

async function field(label, value, root = ".quality-ui") {
  return fillLabeled(page, frame, label, value, root);
}

async function selectLabeledOption(label, value, root = ".quality-ui") {
  const select = await labeledControl(frame, label, root, "select");
  await frame.waitForFunction((node, expected) => !node.disabled
    && [...node.options].some(option => option.value === expected), { timeout: 30000 }, select, String(value));
  await select.select(String(value));
  return select;
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
    nodes: item.nodes.slice(0, 8).map(node => ({ target: node.target, html: node.html, summary: node.failureSummary })),
  })));
  accessibilityFindings.push(...findings.map(item => ({ label, ...item })));
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

async function saveQuality(kind, method = "POST") {
  const ending = "/api/v1/quality/" + kind;
  const response = await waitUiResponse(page,
    (candidate, url) => (method === "POST" ? url.pathname === ending : url.pathname.startsWith(ending + "/"))
      && candidate.request().method() === method,
    () => clickText(frame, "button", kind === "inspections" ? "保存质检记录" : "保存配置"));
  assert.equal(response.status(), method === "POST" ? 201 : 200, await response.text());
  const row = envelopeData(await response.json());
  if (method === "POST") qualityRecords.push({ kind, id: row.id, name: row.name || row.sampleId });
  await frame.waitForSelector(kind === "inspections" ? "#qc-inspection-form" : "#qc-config-form",
    { hidden: true, timeout: 30000 });
  return row;
}

async function recorded(metricName) {
  const raw = await frame.evaluateHandle(name => [...document.querySelectorAll(".qc-observation")]
    .find(item => item.querySelector("legend")?.textContent.includes(name))
    ?.querySelector('input[type="checkbox"]') || null, metricName);
  const checkbox = raw.asElement();
  assert(checkbox, "Observation checkbox is missing for " + metricName);
  if (!await checkbox.evaluate(node => node.checked)) await clickHandle(checkbox);
}

async function qualityRow(name) {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    const rows = await frame.$$(".quality-table tbody tr");
    for (const row of rows) {
      try {
        if (await row.evaluate((node, expected) => node.textContent.includes(expected), name)) return row;
      } catch { /* List replacement can detach rows after a write. */ }
    }
    await sleep(50);
  }
  throw new Error("Quality row not found: " + name);
}

async function filterQuality(name, kind) {
  await field(kind === "inspections" ? "样本 / 批次" : "名称", name, ".wf-list-filter");
  await clickText(frame, ".wf-list-filter button", "查询");
  await frame.waitForFunction(expected => document.querySelector(".quality-table")?.textContent.includes(expected),
    { timeout: 30000 }, name);
}

async function removeQualityRecord(route, kind, name, id) {
  assert(id, "只清理已经取得创建回执的质检记录");
  await visit(route);
  const listing = await waitUiResponse(page,
    (candidate, url) => url.pathname === `/api/v1/quality/${kind}` && candidate.request().method() === "GET",
    () => filterQuality(name, kind));
  assert.equal(listing.status(), 200, await listing.text());
  // 点击前先用本次可见筛选响应核对唯一名称及创建 ID，避免仅凭相似名称删除。
  const matches = envelopeData(await listing.json()).filter(item => (item.name || item.sampleId) === name);
  assert.deepEqual(matches.map(item => item.id), [id], "待清理列表必须唯一对应本轮创建回执");
  const row = await qualityRow(name);
  const label = kind === "inspections" ? "归档" : "移除";
  const remove = await visibleByText(row, "button", label);
  assert(remove, `${label} action is missing for ${name}`);
  const response = await waitUiResponse(page,
    (candidate, url) => url.pathname === `/api/v1/quality/${kind}/${id}`
      && candidate.request().method() === "DELETE",
    async () => { await clickHandle(remove); await confirmElementPlus(frame, "确认"); });
  assert.equal(response.status(), 200, await response.text());
  return true;
}

async function selectElementPlusServices(names) {
  const raw = await frame.evaluateHandle(() => [...document.querySelectorAll("label")]
    .find(node => node.getClientRects().length && (node.textContent || "").includes("质检检测服务")) || null);
  const label = raw.asElement();
  assert(label, "Quality detector service selector is missing");
  for (const name of names) {
    const wrapper = await label.$(".el-select__wrapper");
    assert(wrapper, "Element Plus selector wrapper is missing");
    await clickHandle(wrapper);
    const optionRaw = await frame.evaluateHandle(expected => [...document.querySelectorAll(".el-select-dropdown__item")]
      .find(node => node.getClientRects().length && !node.classList.contains("is-disabled")
        && (node.textContent || "").includes(expected)) || null, name);
    const option = optionRaw.asElement();
    assert(option, "Quality service option is missing: " + name);
    await clickHandle(option);
  }
  await page.keyboard.press("Escape");
}

async function platformVisit(route) {
  const response = await page.goto(BASE + route, { waitUntil: "networkidle0", timeout: 90000 });
  assert.equal(response.status(), 200, route);
  await page.waitForSelector("#main-content", { visible: true, timeout: 90000 });
}

async function platformRow(value) {
  await page.waitForFunction(expected => [...document.querySelectorAll("#main-content tbody tr")]
    .some(row => row.textContent.includes(expected)), { timeout: 30000 }, value);
  const raw = await page.evaluateHandle(expected => [...document.querySelectorAll("#main-content tbody tr")]
    .find(row => row.textContent.includes(expected)) || null, value);
  const row = raw.asElement();
  assert(row, "Platform row not found: " + value);
  return row;
}

async function confirmPlatform(label) {
  await page.waitForSelector("#platform-dialog[open]", { visible: true });
  await clickText(page, "#platform-dialog[open] button", label);
  await page.waitForFunction(() => !document.querySelector("#platform-dialog")?.open, { timeout: 30000 });
}

async function removeUploadedImage() {
  if (!uploadedImageName) return;
  await platformVisit("/files?q=" + encodeURIComponent(uploadedImageName));
  const row = await platformRow(uploadedImageName);
  await clickHandle(await row.$(".content-action-menu summary"));
  const remove = await row.$("[data-file-delete]");
  assert(remove, "Uploaded quality image has no delete action");
  const response = await waitUiResponse(page,
    (candidate, url) => url.pathname === "/api/files/" + uploadedImageId
      && candidate.request().method() === "DELETE",
    async () => { await clickHandle(remove); await confirmPlatform("删除"); });
  assert.equal(response.status(), 200, await response.text());
}

(async () => {
  assert(PASSWORD, "Supply TEST5_E2E_PASSWORD");
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, "isolated");
  assert(["127.0.0.1", "localhost"].includes(new URL(BASE).hostname));
  assert.notEqual(new URL(BASE).port, "8011", "Never write to the user's live instance");
  fs.mkdirSync(OUTPUT, { recursive: true });
  const imagePath = path.join(OUTPUT, NAMES.image);
  fs.copyFileSync(path.resolve("apps/web/assets/platform/qknow-logo.png"), imagePath);
  const imageBytes = fs.readFileSync(imagePath);
  const imageHash = crypto.createHash("sha256").update(imageBytes).digest("hex");

  fixture = http.createServer((request, response) => {
    const chunks = [];
    request.on("data", chunk => chunks.push(chunk));
    request.on("end", () => {
      let payload = {};
      try { payload = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}"); }
      catch { payload = { invalidJson: true }; }
      detectorCalls.push({ path: request.url, method: request.method, payload });
      const item = request.url === "/appearance" ? {
        sampleId: payload.sampleId,
        defects: [{
          defectType: "表面裂纹",
          confidence: "0.970000000000000001",
          severity: "moderate",
          bbox: [0.15, 0.2, 0.4, 0.25],
          suggest: "复核并返工",
        }],
      } : {
        sampleId: payload.sampleId,
        components: [{ componentName: "C", measuredValue: "0.300000000000000000001", unit: "%" }],
      };
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ items: [item] }));
    });
  });
  await new Promise(resolve => fixture.listen(0, "127.0.0.1", resolve));
  const fixtureBase = `http://127.0.0.1:${fixture.address().port}`;

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

  ({ source: appearanceSource } = await createHttpSourceThroughUi(page, BASE, {
    name: NAMES.appearanceSource,
    description: "逐样本接收原图并返回真实 defects 数组。",
    url: fixtureBase + "/appearance",
    method: "POST",
    bodies: [{
      sampleId: "${input.sampleId}",
      imageBase64: "${input.imageBase64}",
      imageMimeType: "${input.imageMimeType}",
      imageSha256: "${input.imageSha256}",
    }],
    recordPath: "$.items",
    fields: [
      { name: "sampleId", path: "$.sampleId", type: "string", required: true, comment: "样本编号" },
      { name: "defects", path: "$.defects", type: "json", required: true, comment: "缺陷数组" },
    ],
    allowPrivate: true,
    allowAutomatic: true,
  }));
  ({ source: componentSource } = await createHttpSourceThroughUi(page, BASE, {
    name: NAMES.componentSource,
    description: "逐样本返回指定化学成分精确检测值。",
    url: fixtureBase + "/component",
    method: "POST",
    bodies: [{ sampleId: "${input.sampleId}" }],
    recordPath: "$.items",
    fields: [
      { name: "sampleId", path: "$.sampleId", type: "string", required: true, comment: "样本编号" },
      { name: "components", path: "$.components", type: "json", required: true, comment: "成分数组" },
    ],
    allowPrivate: true,
    allowAutomatic: true,
  }));
  assert.equal(detectorCalls.length, 0);
  pass("外观与成分 HTTP 服务均通过界面配置请求模板、JSONPath 和输出契约，保存阶段不调用设备");

  // 共享设备台账现在使用设备卡片；明确勾选质检用途，不绕到旧版内部表格。
  frame = await visitBusiness(page, BASE, "/alerts", '.device-entry-page[data-domain="alert"]');
  await clickText(frame, ".device-entry-actions button", "新增设备");
  await frame.waitForSelector("#business-device-form", { visible: true });
  await fillLabeled(page, frame, "设备编码", "QC-" + RUN, "#business-device-form");
  await fillLabeled(page, frame, "设备名称", NAMES.device, "#business-device-form");
  await fillLabeled(page, frame, "设备类型", "camera-spectrometer", "#business-device-form");
  await fillLabeled(page, frame, "安装位置", "质量实验室", "#business-device-form");
  await setCheckboxByText(frame, "质量检查", true, "#business-device-form");
  await selectElementPlusServices([NAMES.appearanceSource, NAMES.componentSource]);
  const deviceResponse = await waitUiResponse(page,
    (candidate, url) => url.pathname === "/api/v1/alerts/devices" && candidate.request().method() === "POST",
    () => clickText(frame, "button", "保存设备"));
  assert.equal(deviceResponse.status(), 201, await deviceResponse.text());
  device = envelopeData(await deviceResponse.json());
  assert.deepEqual(new Set(device.qualityDatasourceIds), new Set([appearanceSource.id, componentSource.id]));
  assert(device.domains.includes("quality"), "共享设备必须保留用户勾选的质量业务用途");
  await frame.waitForSelector("#business-device-form", { hidden: true });
  await frame.waitForFunction(name => [...document.querySelectorAll("[data-device-id]")]
    .some(card => card.textContent.includes(name)), {}, NAMES.device);
  const pageSizeResponse = await waitUiResponse(page,
    (candidate, url) => url.pathname === "/api/v1/alerts/device-catalogue"
      && url.searchParams.get("limit") === "24" && url.searchParams.get("offset") === "0",
    () => fillLabeled(page, frame, "每页条数", "24", ".catalog-pagination"));
  assert.equal(pageSizeResponse.status(), 200, await pageSizeResponse.text());
  assert(envelopeData(await pageSizeResponse.json()).items.some(item => item.id === device.id));
  frame = await visitBusiness(page, BASE, "/alerts", '.device-entry-page[data-domain="alert"]');
  await frame.waitForSelector('.catalog-pagination select', { visible: true });
  assert.equal(await frame.$eval('.catalog-pagination select', node => node.value), "24", "刷新后保留本工作区的每页条数");
  await runAxe("共享设备目录与业务管理入口");
  assert.equal(detectorCalls.length, 0);
  pass("共享设备台账通过可视控件关联两个质检服务，并同时保留质量检查业务用途", { deviceId: device.id });

  await visit("/quality/inspections");
  await clickText(frame, "button", "使用说明");
  await frame.waitForSelector(".business-process-guide", { visible: true });
  assert.equal(await frame.$$eval(".business-guide-flow > li", items => items.length), 5);
  assert((await frame.$eval(".business-process-guide", node => node.textContent)).includes("从检验标准到可追溯结论"));
  await page.screenshot({ path: path.join(OUTPUT, "quality-guide.png"), fullPage: true });
  await closeElementDialog(".business-process-guide");
  pass("按需使用说明完整解释指标、模板、设备、检测、复核和追溯，不遮挡日常工作区");

  await visit("/quality/metrics");
  await clickText(frame, "button", "新增指标");
  await frame.waitForSelector("#qc-config-form", { visible: true });
  await field("指标名称", NAMES.carbon);
  await field("检测类别", "component");
  await field("成分名称", "C");
  await field("单位", "%");
  await field("标准下限（含端点）", "0.100000000000000000001");
  await field("标准上限（含端点）", "0.200000000000000000001");
  await field("标准说明", "精确十进制碳含量验收标准");
  const carbon = await saveQuality("metrics");
  assert.equal(carbon.standardMax, "0.200000000000000000001");

  await clickText(frame, "button", "新增指标");
  await frame.waitForSelector("#qc-config-form", { visible: true });
  await field("指标名称", NAMES.appearance);
  await field("检测类别", "appearance");
  await field("自动判定最低置信度", "0.900000000000000001");
  await field("标准说明", "中度及重度裂纹阻断放行");
  const appearance = await saveQuality("metrics");
  assert(appearance.blockingSeverities.includes("moderate"));
  assert.equal(detectorCalls.length, 0);
  pass("新增成分与外观指标，精确上下限、置信度和阻断级别完整保存");

  await visit("/quality/templates");
  await clickText(frame, "button", "新增模板");
  await frame.waitForSelector("#qc-config-form", { visible: true });
  assert.equal(await frame.$$eval(".qc-selected li", items => items.length), 0);
  await field("模板名称", NAMES.template);
  await field("适用设备类别", "camera-spectrometer");
  await selectLabeledOption("添加检验指标", carbon.id);
  await frame.waitForFunction(name => document.querySelector(".qc-selected")?.textContent.includes(name), {}, NAMES.carbon);
  await selectLabeledOption("添加检验指标", appearance.id);
  await frame.waitForFunction(() => document.querySelectorAll(".qc-selected li").length === 2);
  await field("模板说明", "成分与外观联合放行标准");
  const template = await saveQuality("templates");
  assert.deepEqual(template.metricIds, [carbon.id, appearance.id]);
  await filterQuality(NAMES.template, "templates");
  const templateRow = await qualityRow(NAMES.template);
  await clickHandle(await visibleByText(templateRow, "button", "修订标准"));
  await frame.waitForSelector(".quality-dialog details", { visible: true });
  const revisionText = await frame.$eval(".quality-dialog details", node => node.textContent);
  assert(revisionText.includes("0.200000000000000000001") && revisionText.includes("0.900000000000000001"));
  await page.screenshot({ path: path.join(OUTPUT, "immutable-template-revision.png"), fullPage: true });
  await closeElementDialog(".quality-dialog details");
  pass("模板不默认选择指标，并以不可变修订固定两项完整标准", { templateId: template.id });

  await visit("/quality/inspections");
  await clickText(frame, "button", "新增质检");
  await frame.waitForSelector("#qc-inspection-form", { visible: true });
  const defaults = await frame.$$eval("#qc-inspection-form select", items => items.map(item => item.value));
  assert(defaults.every(value => value === ""), "Inspection form must not choose template, revision or device by default");
  await selectLabeledOption("质检模板", template.id, "#qc-inspection-form");
  await selectLabeledOption("固定模板修订", "1", "#qc-inspection-form");
  await selectLabeledOption("受检设备", device.id, "#qc-inspection-form");
  await field("样本 / 批次标识", NAMES.manualSample, "#qc-inspection-form");
  await recorded(NAMES.carbon);
  await field("实测值", "0.300000000000000000001", "#qc-inspection-form");
  await recorded(NAMES.appearance);
  await field("图像资料标识", "file:manual-inspection-" + RUN, "#qc-inspection-form");
  await field("外观检查结果", "none", "#qc-inspection-form");
  await runAxe("人工质检录入表单");
  await page.screenshot({ path: path.join(OUTPUT, "manual-inspection-form.png"), fullPage: true });
  const manual = await saveQuality("inspections");
  assert.equal(manual.outcome, "failed");
  assert.equal(manual.results.find(item => item.metricId === carbon.id).observation.value,
    "0.300000000000000000001");
  assert(manual.results.every(item => item.provenance.mode === "manual"));
  await frame.waitForSelector(".qc-report", { visible: true });
  await field("复核指标", carbon.id, ".qc-report");
  await field("复核结论", "passed", ".qc-report");
  await field("复核依据", "标准样品复测通过；原始读数与人工结论分别保留", ".qc-report");
  const reviewResponse = await waitUiResponse(page,
    (candidate, url) => url.pathname.endsWith(`/inspections/${manual.id}/reviews`)
      && candidate.request().method() === "POST",
    () => clickText(frame, ".qc-report button", "保存复核"));
  assert.equal(reviewResponse.status(), 200, await reviewResponse.text());
  const reviewed = envelopeData(await reviewResponse.json());
  assert.equal(reviewed.outcome, "failed");
  assert.equal(reviewed.effectiveOutcome, "passed");
  await frame.waitForFunction(() => document.querySelector(".qc-history")?.textContent.includes("标准样品复测通过"));
  const exportResponse = await waitUiResponse(page,
    (candidate, url) => url.pathname.endsWith(`/inspections/${manual.id}/export`)
      && candidate.request().method() === "GET",
    () => clickText(frame, ".qc-report button", "下载质检报告"));
  assert.equal(exportResponse.status(), 200, await exportResponse.text());
  assert.equal(detectorCalls.length, 0);
  pass("人工质检明确选择模板修订与设备，精确实测、原始不合格、追加复核和报告下载均可追溯");
  await closeElementDialog(".qc-report");

  await clickText(frame, "button", "配置检测流程");
  await frame.waitForSelector("#qc-workflow-wizard", { visible: true });
  await field("流程名称", NAMES.workflow, "#qc-workflow-wizard");
  await field("业务说明", "原图外观与碳含量联合自动检测", "#qc-workflow-wizard");
  await field("组名称（可选）", "联合终检组", "#qc-workflow-wizard");
  await selectLabeledOption("受检设备", device.id, "#qc-workflow-wizard");
  await selectLabeledOption("检验模板", template.id, "#qc-workflow-wizard");
  await frame.waitForFunction(() => document.querySelectorAll(".qc-detector-binding").length === 2);
  for (const [metricName, sourceId] of [[NAMES.carbon, componentSource.id], [NAMES.appearance, appearanceSource.id]]) {
    const raw = await frame.evaluateHandle(name => [...document.querySelectorAll(".qc-detector-binding")]
      .find(card => card.textContent.includes(name)) || null, metricName);
    const card = raw.asElement();
    assert(card, "Detector card is missing for " + metricName);
    const select = await card.$("select");
    assert(select, "Detector source selector is missing for " + metricName);
    await frame.waitForFunction((node, value) => [...node.options].some(option => option.value === value && !option.disabled),
      { timeout: 30000 }, select, sourceId);
    await select.select(sourceId);
    await frame.waitForFunction((node, value) => node.value === value && node.closest("article")?.textContent.includes("配置就绪"),
      {}, select, sourceId);
  }
  assert.equal(await frame.$eval('#qc-workflow-wizard button[type="submit"]', node => node.disabled), false);
  assert((await frame.$eval("#qc-workflow-wizard", node => node.textContent)).includes("指标服务2"));
  assert.equal(detectorCalls.length, 0);
  await page.setViewport({ width: 390, height: 844 });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  assert(await frame.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await runAxe("390px 自动质检配置向导");
  await page.screenshot({ path: path.join(OUTPUT, "quality-workflow-wizard-mobile.png"), fullPage: true });
  await page.setViewport({ width: 1500, height: 980 });
  pass("自动质检向导仅展示设备已授权服务，逐指标固定版本且在 390px 下完整可用");

  const workflowResponse = await waitUiResponse(page,
    (candidate, url) => url.pathname === "/api/v1/quality/workflows"
      && candidate.request().method() === "POST",
    () => clickText(frame, '#qc-workflow-wizard button[type="submit"]', "创建并发布检测流程"));
  assert.equal(workflowResponse.status(), 201, await workflowResponse.text());
  const workflowCreation = envelopeData(await workflowResponse.json());
  workflowId = workflowCreation.workflow.id;
  assert.equal(workflowCreation.workflow.status, "published");
  assert.equal(workflowCreation.workflow.publishedRevision, 1);
  assert.deepEqual(workflowCreation.workflow.graph.nodes.map(node => node.kind),
    ["start", "qc-component", "qc-appearance", "merge", "qc-evaluate", "end"]);
  assert.equal(detectorCalls.length, 0);
  await frame.waitForSelector("#qc-run-form", { visible: true, timeout: 30000 });
  pass("向导原子创建并发布外观、成分、证据汇合和逐样本判定流程，发布不调用设备", { workflowId });

  await field("样本编号 1", NAMES.automaticSample, "#qc-run-form");
  const fileInput = await frame.waitForSelector('#qc-run-form input[type="file"]');
  const uploadResponsePromise = page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/quality/images"
    && response.request().method() === "POST", { timeout: 90000 });
  await fileInput.uploadFile(imagePath);
  const uploadResponse = await uploadResponsePromise;
  assert.equal(uploadResponse.status(), 201, await uploadResponse.text());
  const uploaded = envelopeData(await uploadResponse.json());
  uploadedImageId = uploaded.id;
  uploadedImageName = uploaded.name;
  assert.equal(uploaded.sha256, imageHash);
  await frame.waitForFunction(name => [...document.querySelectorAll('#qc-run-form [role="status"]')]
    .some(node => node.textContent.includes(name)), {}, uploadedImageName);
  assert.equal(detectorCalls.length, 0);
  pass("普通运行页上传原图并核验 SHA-256，上传本身不执行缺陷识别", { imageId: uploadedImageId });

  const runResponse = await waitUiResponse(page,
    (candidate, url) => url.pathname === `/api/v1/quality/workflows/${workflowId}/runs`
      && candidate.request().method() === "POST",
    () => clickText(frame, '#qc-run-form button[type="submit"]', "开始检测并生成报告"), 330000);
  assert.equal(runResponse.status(), 200, await runResponse.text());
  const batch = envelopeData(await runResponse.json());
  automaticInspectionId = batch.samples[0].inspectionId;
  qualityRecords.push({ kind: "inspections", id: automaticInspectionId, name: NAMES.automaticSample });
  assert.equal(batch.execution.status, "completed");
  assert.equal(detectorCalls.length, 2);
  const appearanceCall = detectorCalls.find(call => call.path === "/appearance");
  const componentCall = detectorCalls.find(call => call.path === "/component");
  assert(appearanceCall && componentCall);
  assert.equal(appearanceCall.payload.sampleId, NAMES.automaticSample);
  assert.equal(appearanceCall.payload.imageSha256, imageHash);
  assert(Buffer.from(appearanceCall.payload.imageBase64, "base64").equals(imageBytes));
  assert.deepEqual(componentCall.payload, { sampleId: NAMES.automaticSample });
  await frame.waitForSelector(".qc-run-result", { visible: true });
  assert((await frame.$eval(".qc-run-result", node => node.textContent)).includes(NAMES.automaticSample));
  await clickText(frame, ".qc-run-result button", "查看报告");
  await frame.waitForSelector(".qc-report", { visible: true });
  const reportText = await frame.$eval(".qc-report", node => node.textContent);
  assert(reportText.includes("表面裂纹") && reportText.includes("0.300000000000000000001"));
  assert(reportText.includes("检测服务") && reportText.includes("不合格"));
  await frame.waitForFunction(() => document.querySelector(".qc-picture-evidence .qc-original-image")?.naturalWidth > 0);
  await runAxe("真实外观与成分检测报告");
  await page.screenshot({ path: path.join(OUTPUT, "automatic-quality-report.png"), fullPage: true });
  pass("真实调用外观与成分服务各一次，原图、缺陷框、精确成分值与服务来源汇合为一份报告",
    { inspectionId: automaticInspectionId, detectorRequests: detectorCalls.length });

  await closeElementDialog(".qc-report");
  await page.setViewport({ width: 390, height: 844 });
  await visit("/quality/inspections?inspection=" + automaticInspectionId);
  await frame.waitForSelector(".qc-report", { visible: true });
  await frame.waitForFunction(() => document.querySelector(".qc-picture-evidence .qc-original-image")?.naturalWidth > 0);
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  assert(await frame.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await page.screenshot({ path: path.join(OUTPUT, "quality-report-mobile.png"), fullPage: true });
  assert.equal(detectorCalls.length, 2);
  pass("390px 深链接可读取历史原图与报告，刷新报告不会重复调用检测设备");
  await page.setViewport({ width: 1500, height: 980 });

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
      for (const record of [...qualityRecords].filter(item => item.kind === "inspections").reverse()) {
        try { await removeQualityRecord("/quality/inspections", "inspections", record.name, record.id); }
        catch (error) { failures.push(`cleanup inspection ${record.name}: ${error.message}`); }
      }
      if (workflowId) {
        try { assert.equal(await removeWorkflowThroughUi(page, BASE, NAMES.workflow), true, "本轮质检工作流必须从页面移除"); }
        catch (error) { failures.push("cleanup workflow: " + error.message); }
      }
      for (const record of [...qualityRecords].filter(item => item.kind !== "inspections").reverse()) {
        const route = "/quality/" + record.kind;
        try { await removeQualityRecord(route, record.kind, record.name, record.id); }
        catch (error) { failures.push(`cleanup ${record.kind} ${record.name}: ${error.message}`); }
      }
      if (device) {
        try {
          frame = await visitBusiness(page, BASE, "/alerts", '.device-entry-page[data-domain="alert"]');
          await fillLabeled(page, frame, "搜索设备", NAMES.device, ".device-search");
          await clickText(frame, ".device-search button", "搜索");
          await frame.waitForFunction(id => [...document.querySelectorAll("[data-device-id]")]
            .some(card => card.getAttribute("data-device-id") === id), {}, device.id);
          const raw = await frame.evaluateHandle(id => [...document.querySelectorAll("[data-device-id]")]
            .find(card => card.getAttribute("data-device-id") === id) || null, device.id);
          const row = raw.asElement();
          assert(row, "本轮创建设备必须能从共享设备台账清理");
          if (row) {
            const remove = await visibleByText(row, "button", "移除");
            const response = await waitUiResponse(page,
              (candidate, url) => url.pathname === `/api/v1/alerts/devices/${device.id}`
                && candidate.request().method() === "DELETE",
              async () => { await clickHandle(remove); await confirmElementPlus(frame, "确认移除"); });
            assert.equal(response.status(), 200, await response.text());
          }
        } catch (error) { failures.push("cleanup device: " + error.message); }
      }
      if (appearanceSource) {
        try { assert.equal(await removeHttpSourceThroughUi(page, BASE, NAMES.appearanceSource), true, "本轮外观检测数据源必须从页面移除"); }
        catch (error) { failures.push("cleanup appearance source: " + error.message); }
      }
      if (componentSource) {
        try { assert.equal(await removeHttpSourceThroughUi(page, BASE, NAMES.componentSource), true, "本轮成分检测数据源必须从页面移除"); }
        catch (error) { failures.push("cleanup component source: " + error.message); }
      }
      if (uploadedImageId) {
        try { await removeUploadedImage(); }
        catch (error) { failures.push("cleanup image: " + error.message); }
      }
    } catch (error) {
      failures.push("cleanup: " + error.message);
      process.exitCode = 1;
    }
  }
  fs.writeFileSync(path.join(OUTPUT, "result.json"), JSON.stringify({
    checks,
    failures,
    accessibilityFindings,
    browserCreated: {
      appearanceSourceId: appearanceSource?.id || "",
      componentSourceId: componentSource?.id || "",
      deviceId: device?.id || "",
      workflowId,
      automaticInspectionId,
      uploadedImageId,
    },
    detectorCalls: detectorCalls.map(call => ({
      path: call.path,
      method: call.method,
      sampleId: call.payload.sampleId,
      imageBytes: call.payload.imageBase64 ? Buffer.from(call.payload.imageBase64, "base64").length : 0,
      imageSha256: call.payload.imageSha256 || "",
    })),
  }, null, 2));
  if (failures.length || accessibilityFindings.length) process.exitCode = 1;
  if (browser) await browser.close();
  if (fixture) await new Promise(resolve => fixture.close(resolve));
  console.log("Evidence " + OUTPUT);
});
