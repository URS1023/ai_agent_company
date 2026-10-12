/*
 * 中文说明（设备工作流内联资源的浏览器验收）：
 * 验证行为：工作流详情中的内联资源（数据源/知识库/模板等）能正确展示并可在页面内查看。
 * 运行方式：需隔离服务与浏览器；通用点击/等待工具来自 tests/ui/browser_only_helpers.js。
 * 边界：只读校验既有工作流资源，不修改绑定关系。
 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const puppeteer = require("puppeteer-core");
const {
  clickHandle,
  clickText,
  fillLabeled,
  login,
  setCheckboxByText,
  visitBusiness,
  waitUiResponse,
} = require("./browser_only_helpers");

const BASE = process.env.TEST5_E2E_BASE_URL || "http://127.0.0.1:8041";
const PASSWORD = process.env.TEST5_E2E_PASSWORD;
const EDGE = process.env.TEST5_EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const OUTPUT = path.resolve(process.env.TEST5_E2E_OUTPUT || "ui_test_report/workflow_inline_resources_20260907");
const pause = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

function payload(body) {
  return body?.data ?? body;
}

async function currentFrame(page, selector) {
  if (await page.$("#analytics-app")) {
    await page.waitForSelector(selector, { visible: true, timeout: 90000 });
    return page;
  }
  const frameElement = await page.waitForSelector("#business-module-frame", { visible: true, timeout: 90000 });
  const frame = await frameElement.contentFrame();
  assert(frame, "Business module iframe is unavailable");
  await frame.waitForSelector(selector, { visible: true, timeout: 90000 });
  return frame;
}

async function selectNode(frame, title) {
  const raw = await frame.evaluateHandle(expected => [...document.querySelectorAll(".wf-node[data-node-id]")]
    .find(node => (node.textContent || "").includes(expected)) || null, title);
  const node = raw.asElement();
  assert(node, `Workflow node is missing: ${title}`);
  await clickHandle(node);
}

async function waitAutosaved(frame, previousVersion) {
  await frame.waitForFunction(previous => {
    const version = Number(document.querySelector(".wf-save-state")?.textContent.match(/v(\d+)/)?.[1] || 0);
    const state = document.querySelector(".wf-autosave-state")?.textContent || "";
    return version > previous && /^自动保存 \d{2}:\d{2}:\d{2}/.test(state);
  }, { timeout: 90000 }, previousVersion);
  return Number((await frame.$eval(".wf-save-state", node => node.textContent)).match(/v(\d+)/)?.[1] || 0);
}

async function runDraft(page, frame, input) {
  await clickHandle(await frame.$(".wf-toolbar-run"));
  await frame.waitForSelector('.wf-debug-panel[aria-label="调试和预览"]', { visible: true, timeout: 30000 });
  await fillLabeled(page, frame, "开始节点输入", JSON.stringify(input, null, 2), ".wf-debug-panel");
  const response = await waitUiResponse(page,
    (candidate, url) => /\/api\/v1\/workflows\/[^/]+\/runs$/.test(url.pathname) && candidate.request().method() === "POST",
    () => clickText(frame, ".wf-debug-panel>footer button", "运行"), 90000);
  assert.equal(response.status(), 200, await response.text());
  await frame.waitForSelector(".wf-debug-panel .wf-result", { visible: true, timeout: 30000 });
  assert.equal(await frame.$eval(".wf-result h2", node => node.textContent.trim()), "执行完成");
}

async function main() {
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, "isolated", "Inline resource acceptance only runs on an isolated server");
  assert(["127.0.0.1", "localhost"].includes(new URL(BASE).hostname), "Inline resource acceptance refuses a non-local server");
  assert(PASSWORD, "Supply TEST5_E2E_PASSWORD");
  fs.mkdirSync(OUTPUT, { recursive: true });
  const browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: process.env.TEST5_HEADFUL !== "1",
    slowMo: process.env.TEST5_HEADFUL === "1" ? 45 : 0,
    args: ["--disable-gpu", "--no-first-run", "--window-size=1500,1000"],
    defaultViewport: { width: 1500, height: 920 },
    timeout: 60000,
  });
  const page = await browser.newPage();
  const failures = [];
  page.on("pageerror", error => failures.push("pageerror: " + error.message));
  page.on("requestfailed", request => {
    if (request.failure()?.errorText !== "net::ERR_ABORTED") failures.push("requestfailed: " + request.url());
  });
  page.on("response", response => { if (response.status() >= 500) failures.push(`${response.status()} ${response.url()}`); });

  try {
    await login(page, BASE, PASSWORD);
    let frame = await visitBusiness(page, BASE, "/alerts", ".device-entry-page[data-domain=\"alert\"]");
    await clickText(frame, "button", "新增设备");
    await frame.waitForSelector("#business-device-form", { visible: true, timeout: 30000 });
    const suffix = Date.now().toString(36).slice(-6);
    const deviceCode = `INLINE-${suffix}`;
    const deviceName = `节点内配置验收设备-${suffix}`;
    await fillLabeled(page, frame, "设备编码", deviceCode, "#business-device-form");
    await fillLabeled(page, frame, "设备名称", deviceName, "#business-device-form");
    await fillLabeled(page, frame, "设备类型", "温控设备", "#business-device-form");
    await fillLabeled(page, frame, "安装位置", "隔离验收车间", "#business-device-form");
    await setCheckboxByText(frame, "质量检查", true, "#business-device-form");
    const deviceResponse = await waitUiResponse(page,
      (candidate, url) => url.pathname === "/api/v1/alerts/devices" && candidate.request().method() === "POST",
      () => clickText(frame, ".el-dialog__footer button", "保存设备"));
    assert.equal(deviceResponse.status(), 201, await deviceResponse.text());
    const device = payload(await deviceResponse.json());
    assert(device.id, "Device creation did not return an id");
    await frame.waitForFunction(name => [...document.querySelectorAll(".device-card h2")]
      .some(node => node.textContent.includes(name)), { timeout: 30000 }, deviceName);

    const alertCardRaw = await frame.evaluateHandle(name => [...document.querySelectorAll(".device-card")]
      .find(card => card.textContent.includes(name)) || null, deviceName);
    const alertCard = alertCardRaw.asElement();
    assert(alertCard, "Created alert device card is missing");
    await clickHandle(await alertCard.$(".device-open"));
    await page.waitForFunction(id => location.pathname === `/alerts/devices/${id}/workflow`, { timeout: 30000 }, device.id);
    frame = await currentFrame(page, ".wf-immersive .wf-canvas");
    await selectNode(frame, "配置预警判断");
    await frame.waitForSelector(".alert-policy-select", { visible: true, timeout: 30000 });
    assert((await frame.$eval(".alert-policy-select .wf-bound-resource", node => node.textContent)).includes(deviceName), "Alert node fixes the selected device");
    const alertVersion = Number((await frame.$eval(".wf-save-state", node => node.textContent)).match(/v(\d+)/)?.[1] || 0);
    await clickText(frame, ".alert-policy-select .wf-inline-resource-actions button", "新建策略");
    await frame.waitForSelector(".alert-policy-select .wf-inline-editor", { visible: true, timeout: 30000 });
    await fillLabeled(page, frame, "策略名称", `${deviceName}高温预警`, ".alert-policy-select .wf-inline-editor");
    await fillLabeled(page, frame, "规则项名称", "温度过高", ".alert-policy-select .wf-inline-editor");
    await fillLabeled(page, frame, "判断字段", "temperature", ".alert-policy-select .wf-inline-editor");
    await fillLabeled(page, frame, "比较值", "80", ".alert-policy-select .wf-inline-editor");
    await fillLabeled(page, frame, "问题说明", "温度超过安全上限", ".alert-policy-select .wf-inline-editor");
    const alertAutosave = page.waitForResponse(candidate => /\/api\/v1\/workflows\/[^/]+$/.test(new URL(candidate.url()).pathname)
      && candidate.request().method() === "PUT", { timeout: 90000 });
    const ruleResponse = await waitUiResponse(page,
      (candidate, url) => url.pathname === "/api/v1/alerts/rules" && candidate.request().method() === "POST",
      () => clickText(frame, ".alert-policy-select .wf-inline-editor footer button", "保存并使用"));
    assert.equal(ruleResponse.status(), 201, await ruleResponse.text());
    const rule = payload(await ruleResponse.json());
    assert.equal(rule.deviceId, device.id, "Inline alert policy stays on the entry device");
    assert.equal((await alertAutosave).status(), 200);
    await waitAutosaved(frame, alertVersion);
    await page.screenshot({ path: path.join(OUTPUT, "01-alert-inline-policy.png"), fullPage: true });
    await runDraft(page, frame, { data: [{ temperature: 95 }] });
    assert(await frame.$(".wf-result .wf-note"), "Alert report remains available inside the run result");
    assert.equal(await frame.$('.wf-result a[href*="/alerts/reports"]'), null, "Run result has no link back to a removed alert submodule");
    await page.screenshot({ path: path.join(OUTPUT, "02-alert-run-result.png"), fullPage: true });
    await clickHandle(await frame.$('.wf-debug-panel button[aria-label="重新运行"]'));
    await frame.waitForSelector('.wf-debug-input-card', { visible: true, timeout: 30000 });
    assert((await frame.$eval('.wf-debug-input-card textarea', node => node.value)).includes('temperature'), "Restart keeps the last input available for correction");
    await clickHandle(await frame.$('.wf-debug-panel button[aria-label="关闭调试和预览"]'));
    await frame.waitForFunction(() => !document.querySelector('.wf-debug-panel'), { timeout: 30000 });
    await clickHandle(await frame.$('.wf-variable-inspect>summary'));
    await frame.waitForSelector('.wf-variable-inspect[open]>section', { visible: true, timeout: 30000 });
    assert((await frame.$eval('.wf-variable-inspect pre', node => node.textContent)).includes('temperature'), "Variable inspection keeps the last real run input");
    await page.screenshot({ path: path.join(OUTPUT, "03-alert-variable-inspect.png"), fullPage: true });
    await clickHandle(await frame.$('.wf-variable-inspect button[aria-label="关闭变量检查"]'));

    frame = await visitBusiness(page, BASE, "/quality", ".device-entry-page[data-domain=\"quality\"]");
    await frame.waitForFunction(() => !document.querySelector(".device-card-skeleton"), { timeout: 30000 });
    const qualityCardRaw = await frame.evaluateHandle(name => [...document.querySelectorAll(".device-card")]
      .find(card => card.textContent.includes(name)) || null, deviceName);
    const qualityCard = qualityCardRaw.asElement();
    assert(qualityCard, "Created quality device card is missing");
    await clickHandle(await qualityCard.$(".device-open"));
    await page.waitForFunction(id => location.pathname === `/quality/devices/${id}/workflow`, { timeout: 30000 }, device.id);
    frame = await currentFrame(page, ".wf-immersive .wf-canvas");
    await selectNode(frame, "配置质量标准");
    await frame.waitForSelector(".quality-inline-template", { visible: true, timeout: 30000 });
    assert((await frame.$eval(".quality-inline-template .wf-bound-resource", node => node.textContent)).includes(deviceName), "Quality node fixes the selected device");
    let qualityVersion = Number((await frame.$eval(".wf-save-state", node => node.textContent)).match(/v(\d+)/)?.[1] || 0);
    await clickText(frame, ".quality-inline-template .wf-inline-resource-actions button", "新建模板");
    await frame.waitForSelector(".quality-inline-template .wf-inline-editor", { visible: true, timeout: 30000 });
    await fillLabeled(page, frame, "模板名称", `${deviceName}出厂标准`, ".quality-inline-template .wf-inline-editor");
    await clickText(frame, ".quality-inline-template .wf-inline-editor .wf-inline-resource-actions button", "新建指标并加入模板");
    await frame.waitForSelector(".quality-inline-template .wf-inline-subeditor", { visible: true, timeout: 30000 });
    await fillLabeled(page, frame, "指标名称", "表面温度", ".quality-inline-template .wf-inline-subeditor");
    await fillLabeled(page, frame, "单位", "℃", ".quality-inline-template .wf-inline-subeditor");
    await fillLabeled(page, frame, "标准上限（含端点）", "100", ".quality-inline-template .wf-inline-subeditor");
    const metricResponse = await waitUiResponse(page,
      (candidate, url) => url.pathname === "/api/v1/quality/metrics" && candidate.request().method() === "POST",
      () => clickText(frame, ".quality-inline-template .wf-inline-subeditor button", "保存并加入模板"));
    assert.equal(metricResponse.status(), 201, await metricResponse.text());
    const metric = payload(await metricResponse.json());
    assert(metric.id, "Inline metric creation did not return an id");
    await frame.waitForFunction(name => [...document.querySelectorAll(".quality-inline-template .qc-selected li")]
      .some(node => node.textContent.includes(name)), { timeout: 30000 }, "表面温度");
    const qualityAutosave = page.waitForResponse(candidate => /\/api\/v1\/workflows\/[^/]+$/.test(new URL(candidate.url()).pathname)
      && candidate.request().method() === "PUT", { timeout: 90000 });
    const templateResponse = await waitUiResponse(page,
      (candidate, url) => url.pathname === "/api/v1/quality/templates" && candidate.request().method() === "POST",
      () => clickText(frame, ".quality-inline-template .wf-inline-editor>footer button", "保存并使用"));
    assert.equal(templateResponse.status(), 201, await templateResponse.text());
    const template = payload(await templateResponse.json());
    assert.deepEqual(template.metricIds, [metric.id], "Inline template pins the newly created metric");
    assert.equal((await qualityAutosave).status(), 200);
    qualityVersion = await waitAutosaved(frame, qualityVersion);
    await fillLabeled(page, frame, "样本 / 批次标识", `BATCH-${suffix}`, ".wf-panel-content");
    await waitAutosaved(frame, qualityVersion);
    await page.screenshot({ path: path.join(OUTPUT, "04-quality-inline-template.png"), fullPage: true });
    await runDraft(page, frame, { data: [{ metricId: metric.id, value: "75", unit: "℃" }] });
    assert(await frame.$(".wf-result .wf-note"), "Quality report remains available inside the run result");
    assert.equal(await frame.$('.wf-result a[href*="/quality/inspections"]'), null, "Run result has no link back to a removed quality submodule");
    await page.screenshot({ path: path.join(OUTPUT, "05-quality-run-result.png"), fullPage: true });

    assert.deepEqual(failures, [], "No page errors, failed requests, or server failures occurred");
    console.log(JSON.stringify({ ok: true, output: OUTPUT, deviceId: device.id, ruleId: rule.id, templateId: template.id, metricId: metric.id }));
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
