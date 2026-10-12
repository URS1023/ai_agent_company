/*
 * 中文说明（数据分析模块的端到端浏览器验收）：
 * 验证行为：从界面入口完成的查询/大屏等分析流程结果与后端数据一致。
 * 运行方式：默认连 http://127.0.0.1:8030 的隔离实例（可用 TEST5_E2E_BASE_URL 覆盖）+ 本机 Edge。
 * 边界：会产生查询历史与分析产物；断言以页面可见结果为准。
 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const puppeteer = require("puppeteer-core");

const BASE = process.env.TEST5_E2E_BASE_URL || "http://127.0.0.1:8030";
const PASSWORD = process.env.TEST5_E2E_PASSWORD;
const EDGE = process.env.TEST5_EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const RUN = Date.now().toString(36);
const OUTPUT = path.resolve(".implementation/ui-results/full-browser-analytics-" + RUN);
const FIXTURE = path.resolve("tests/fixtures/full-browser-business-data.csv");
const SOURCE_NAME = "终验财务销售设备数据-" + RUN;
const SOURCE_DESCRIPTION = "财务、销售与设备运行联合分析数据；用于收入、利润、订单、状态、温度、压力和预警趋势。";
const TABLE_NAME = "business_metrics";
const TEMPLATE_REPORT_NAME = "终验智慧云脑空数据模板报表-" + RUN;
const BLANK_REPORT_NAME = "终验可拖动报表-" + RUN;
const checks = [];
const failures = [];
const consoleErrors = [];
const dragEvidence = [];
const reportCleanupReceipts = [];
const confirmationReceipts = [];
const accessibilityFindings = [];
let browser;
let page;
let frame;

function pass(name, detail = {}) {
  checks.push({ name, ...detail });
  console.log("PASS " + name);
}

function phase(name) {
  console.log("PHASE " + name);
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function envelopeData(body) {
  return body && Object.prototype.hasOwnProperty.call(body, "data") ? body.data : body;
}

async function clickHandle(handle) {
  assert(handle, "Expected a clickable element");
  await handle.evaluate(node => node.scrollIntoView({ block: "center", inline: "nearest" }));
  // The analytics application is embedded in a same-origin iframe. Chromium can
  // occasionally use a stale outer-frame offset for ElementHandle.click() after
  // an inner-frame scroll, so invoke the visible control's native click instead.
  // This still exercises the real UI event handler and never calls an app API.
  await handle.evaluate(node => node.click());
}

async function visibleByText(target, selector, label, exact = true) {
  for (const handle of await target.$$(selector)) {
    const state = await handle.evaluate((node, expected, useExact) => {
      const text = node.textContent.trim();
      return node.getClientRects().length > 0 && (useExact ? text === expected : text.includes(expected));
    }, label, exact);
    if (state) return handle;
  }
  return null;
}

async function clickText(target, selector, label, exact = true) {
  const handle = await visibleByText(target, selector, label, exact);
  assert(handle, `Visible ${selector} not found: ${label}`);
  await clickHandle(handle);
}

async function fillElement(handle, value) {
  assert(handle, "Expected an editable element");
  await handle.evaluate(node => node.scrollIntoView({ block: "center", inline: "nearest" }));
  await handle.focus();
  await page.keyboard.down("Control");
  await page.keyboard.press("KeyA");
  await page.keyboard.up("Control");
  await page.keyboard.press("Backspace");
  if (String(value)) await handle.type(String(value));
}

async function fillDialogField(label, value) {
  const raw = await frame.evaluateHandle(expected => {
    const dialogs = [...document.querySelectorAll(".el-dialog")]
      .filter(node => node.getClientRects().length > 0);
    for (const dialog of dialogs.reverse()) {
      const item = [...dialog.querySelectorAll(".el-form-item")].find(node =>
        node.querySelector(".el-form-item__label")?.textContent.replace("*", "").trim() === expected);
      const input = item?.querySelector("input:not([type=file]), textarea");
      if (input) return input;
    }
    return null;
  }, label);
  const handle = raw.asElement();
  assert(handle, "Dialog field not found: " + label);
  await fillElement(handle, value);
}

async function closeTopDialog() {
  const raw = await frame.evaluateHandle(() => {
    const dialogs = [...document.querySelectorAll(".el-dialog")]
      .filter(node => node.getClientRects().length > 0);
    return dialogs.at(-1)?.querySelector(".el-dialog__headerbtn") || null;
  });
  await clickHandle(raw.asElement());
}

async function cardByHeading(cardSelector, headingSelector, text) {
  const raw = await frame.evaluateHandle((cards, heading, expected) =>
    [...document.querySelectorAll(cards)].find(card => card.querySelector(heading)?.textContent.trim() === expected) || null,
  cardSelector, headingSelector, text);
  const card = raw.asElement();
  assert(card, "Card not found: " + text);
  return card;
}

async function visit(route, readySelector) {
  const response = await page.goto(BASE + route, { waitUntil: "networkidle0", timeout: 90000 });
  assert.equal(response.status(), 200, route);
  const frameHandle = await page.waitForSelector("#business-module-frame", { visible: true, timeout: 90000 });
  frame = await frameHandle.contentFrame();
  assert(frame, "Business module iframe is unavailable");
  await frame.waitForSelector(readySelector, { visible: true, timeout: 90000 });
}

async function waitUiResponse(predicate, action, timeout = 90000) {
  const responsePromise = page.waitForResponse(response => {
    try { return predicate(response, new URL(response.url())); }
    catch { return false; }
  }, { timeout });
  await action();
  return responsePromise;
}

async function confirmMessage({ title, message, label = "确定" }, target = frame) {
  assert(title && message, "确认操作必须指定完整标题与正文，不能误点正在退场的旧框");
  const expected = { title, message, label };
  // Element Plus 的旧框在退场动画中仍有矩形；必须定位本次内容及最上层可交互按钮。
  const raw = await target.waitForFunction(value => {
    for (const dialog of [...document.querySelectorAll(".el-message-box")].reverse()) {
      if (dialog.querySelector(".el-message-box__title")?.textContent.trim() !== value.title
        || dialog.querySelector(".el-message-box__message")?.textContent.trim() !== value.message
        || dialog.closest('[class*="-leave-active"], [class*="-leave-to"]')) continue;
      const button = [...dialog.querySelectorAll("button")].find(node =>
        node.textContent.trim() === value.label && !node.disabled && node.getAttribute("aria-disabled") !== "true");
      if (!button) continue;
      const rect = button.getBoundingClientRect();
      const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
      if (rect.width > 0 && rect.height > 0 && hit && button.contains(hit)) return button;
    }
    return false;
  }, { timeout: 30000 }, expected);
  const button = raw.asElement();
  const dialog = await button.evaluateHandle(node => node.closest(".el-message-box"));
  try {
    const clicked = await button.evaluate((node, value) => {
      const box = node.closest(".el-message-box");
      const rect = node.getBoundingClientRect();
      const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
      if (!node.isConnected || node.disabled || node.getAttribute("aria-disabled") === "true"
        || !hit || !node.contains(hit) || box?.closest('[class*="-leave-active"], [class*="-leave-to"]')
        || box?.querySelector(".el-message-box__title")?.textContent.trim() !== value.title
        || box?.querySelector(".el-message-box__message")?.textContent.trim() !== value.message) return false;
      // 原生点击真实按钮事件；不调用业务接口，也不绕过确认框状态。
      node.click();
      return true;
    }, expected);
    assert(clicked, "确认框内容或遮挡发生变化，已停止本次点击");
    await target.waitForFunction(node => !node.isConnected || node.getClientRects().length === 0,
      { timeout: 30000 }, dialog);
    confirmationReceipts.push({ ...expected, closed: true });
  } finally {
    await dialog.dispose();
    await raw.dispose();
  }
}

async function runAxe(route) {
  await frame.addScriptTag({ path: require.resolve("axe-core/axe.min.js") });
  const findings = await frame.evaluate(async () => (await axe.run(document, {
    runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] },
  })).violations.map(item => ({
    id: item.id,
    impact: item.impact,
    nodes: item.nodes.slice(0, 10).map(node => ({ target: node.target, summary: node.failureSummary })),
  })));
  accessibilityFindings.push(...findings.map(item => ({ route, ...item })));
}

async function realDrag(handle, dx, dy) {
  const initialHit = await handle.evaluate(node => {
    const rect = node.getBoundingClientRect();
    const hit = document.elementFromPoint(rect.x + Math.min(rect.width / 2, 40), rect.y + Math.min(rect.height / 2, 30));
    return { component: node.dataset.component, hitTag: hit?.tagName, hitClass: String(hit?.className || ""), ownsHit: Boolean(hit && node.contains(hit)) };
  });
  // 创建/恢复模板的瞬时提示会盖住画布；先等可见遮挡消失，再用命中检测定位真实可拖动区域。
  await frame.waitForFunction(() => ![...document.querySelectorAll(".el-message")].some(node => node.getClientRects().length), { timeout: 10000 });
  await handle.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const box = await handle.boundingBox();
  assert(box && box.width > 4 && box.height > 4, "Drag target has no usable bounds");
  const point = await handle.evaluate(node => {
    const rect = node.getBoundingClientRect();
    for (const y of [Math.min(rect.height / 2, 30), rect.height / 2, rect.height * 0.8]) {
      for (const x of [Math.min(rect.width / 2, 40), rect.width / 2, rect.width * 0.8]) {
        const hit = document.elementFromPoint(rect.x + x, rect.y + y);
        if (hit?.closest(".screen-widget") === node && !hit.closest("button,input,textarea,select,a")) {
          return { x, y, hitTag: hit.tagName, hitClass: String(hit.className || "") };
        }
      }
    }
    return null;
  });
  dragEvidence.push({ initialHit, point, dx, dy });
  assert(point, "组件必须存在未被其他元素遮挡的鼠标拖动起点");
  const before = await handle.evaluate(node => node.style.transform);
  const x = box.x + point.x, y = box.y + point.y;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + dx, y + dy, { steps: 8 });
  await page.mouse.up();
  await frame.waitForFunction((node, previous) => node.style.transform !== previous, { timeout: 10000 }, handle, before);
}

async function deleteReportByName(name, expectedId) {
  assert(expectedId, "只能删除已取得创建回执 ID 的本轮报表");
  await frame.waitForFunction(expected => [...document.querySelectorAll("[data-report-id] .name")]
    .some(node => node.textContent.trim() === expected), { timeout: 30000 }, name);
  const card = await cardByHeading("[data-report-id]", ".name", name);
  // 点击前核对可见卡片身份，不能把同名卡片或其他删除响应当成本轮报表清理成功。
  assert.equal(await card.evaluate(node => node.dataset.reportId), expectedId, "删除卡片必须对应本轮创建回执的报表 ID");
  const button = await card.$('button[aria-label="删除报表"]');
  const expectedPath = `/api/v1/reports/${expectedId}`;
  const response = await waitUiResponse(
    (candidate, url) => url.pathname === expectedPath && candidate.request().method() === "DELETE",
    async () => { await clickHandle(button); await confirmMessage({ title: "提示", message: `确定删除报表「${name}」吗？` }); },
  );
  assert.equal(response.status(), 200, await response.text());
  reportCleanupReceipts.push({ id: expectedId, name, pathname: new URL(response.url()).pathname, status: response.status() });
  await frame.waitForFunction((expected, id) => ![...document.querySelectorAll("[data-report-id]")]
    .some(node => node.dataset.reportId === id || node.querySelector(".name")?.textContent.trim() === expected),
  { timeout: 30000 }, name, expectedId);
  return true;
}

async function main() {
  assert(PASSWORD, "Supply TEST5_E2E_PASSWORD");
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, "isolated");
  assert(["127.0.0.1", "localhost"].includes(new URL(BASE).hostname));
  assert(fs.existsSync(FIXTURE), "Business CSV fixture is missing");
  fs.mkdirSync(OUTPUT, { recursive: true });

  browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: true,
    args: ["--no-first-run", "--disable-gpu", "--window-size=1500,980"],
  });
  page = await browser.newPage();
  page.setDefaultTimeout(90000);
  await page.setViewport({ width: 1500, height: 980, deviceScaleFactor: 1 });
  page.on("pageerror", error => failures.push("pageerror: " + error.message));
  page.on("console", message => {
    if (message.type() !== "error") return;
    let sourcePath = "";
    try { sourcePath = new URL(message.location().url).pathname; } catch { /* 内联脚本可能没有来源 URL。 */ }
    // 可选图标的已知 404 单独保留证据，业务控制台错误仍严格失败。
    const classification = sourcePath === "/favicon.ico" && /404/.test(message.text()) ? "optional_favicon" : "application";
    consoleErrors.push({ classification, sourcePath, text: message.text() });
    if (classification === "application") failures.push("console: " + message.text());
  });
  page.on("response", response => {
    const url = new URL(response.url());
    if (response.status() >= 400 && url.pathname !== "/favicon.ico") {
      failures.push(`${response.status()} ${response.request().method()} ${url.pathname}`);
    }
  });

  await page.goto(BASE, { waitUntil: "networkidle0", timeout: 90000 });
  await page.type("#login-username", "workspace-admin");
  await page.type("#login-password", PASSWORD);
  await Promise.all([
    page.waitForSelector(".overview-intro", { visible: true, timeout: 90000 }),
    page.click("#login-btn"),
  ]);
  pass("通过登录界面进入隔离工作区");

  await visit("/datasources", ".datasource-workspace");
  await clickText(frame, "button", "从 CSV / Excel 导入");
  await frame.waitForFunction(() => [...document.querySelectorAll(".el-dialog")]
    .some(node => node.getClientRects().length && node.textContent.includes("从表格创建分析数据源")));
  const uploadInput = await frame.$('input[aria-label="选择 CSV 或 XLSX 数据文件"]');
  assert(uploadInput, "CSV upload input is missing");
  await uploadInput.uploadFile(FIXTURE);
  await fillDialogField("数据源名称", SOURCE_NAME);
  await fillDialogField("数据表名称", TABLE_NAME);
  const importResponse = await waitUiResponse(
    (response, url) => url.pathname === "/api/v1/analytics/datasources/import" && response.request().method() === "POST",
    () => clickText(frame, ".el-dialog button", "导入"),
  );
  assert.equal(importResponse.status(), 201, await importResponse.text());
  const importedSource = envelopeData(await importResponse.json());
  assert(importedSource?.id, "Import response did not return a data source id");
  await frame.waitForFunction(name => [...document.querySelectorAll(".datasource-card h3")]
    .some(node => node.textContent.trim() === name), { timeout: 90000 }, SOURCE_NAME);
  let sourceCard = await cardByHeading(".datasource-card", "h3", SOURCE_NAME);
  const sourceText = await sourceCard.evaluate(node => node.textContent);
  assert(sourceText.includes("已同步"));
  assert(sourceText.includes("1") && sourceText.includes("业务表"));
  pass("通过页面上传 CSV 并自动同步元数据", { sourceId: importedSource.id });

  phase("编辑数据源业务说明");
  let editButton = await sourceCard.$('button[aria-label="编辑数据源"]');
  await editButton.evaluate(node => node.click());
  await frame.waitForFunction(() => [...document.querySelectorAll(".el-dialog")]
    .some(node => node.getClientRects().length && node.textContent.includes("编辑数据源")));
  await fillDialogField("业务用途说明", SOURCE_DESCRIPTION);
  const editResponse = await waitUiResponse(
    (response, url) => url.pathname.startsWith("/api/v1/analytics/datasources/") && response.request().method() === "PUT",
    () => clickText(frame, ".el-dialog button", "保存"),
  );
  const editBody = await editResponse.json();
  assert.equal(editResponse.status(), 200, JSON.stringify(editBody));
  const editedSource = envelopeData(editBody);
  assert.equal(editedSource?.id, importedSource.id, "Edited a different data source");
  await frame.waitForFunction((name, description) => [...document.querySelectorAll(".datasource-card")]
    .some(card => card.querySelector("h3")?.textContent.trim() === name && card.textContent.includes(description)),
  { timeout: 90000 }, SOURCE_NAME, "财务、销售与设备运行联合分析数据");

  phase("检查数据源连接并同步语义索引");
  sourceCard = await cardByHeading(".datasource-card", "h3", SOURCE_NAME);
  const healthButton = await sourceCard.$("button.health");
  const healthResponse = await waitUiResponse(
    (response, url) => url.pathname.endsWith(`/datasources/${importedSource.id}/health-checks`) && response.request().method() === "POST",
    () => clickHandle(healthButton),
  );
  assert.equal(healthResponse.status(), 200, await healthResponse.text());
  await frame.waitForFunction(name => [...document.querySelectorAll(".datasource-card")]
    .some(card => card.querySelector("h3")?.textContent.trim() === name && card.textContent.includes("连接正常")),
  { timeout: 30000 }, SOURCE_NAME);
  sourceCard = await cardByHeading(".datasource-card", "h3", SOURCE_NAME);
  const syncButton = await visibleByText(sourceCard, "button", "同步注释与索引");
  const syncResponse = await waitUiResponse(
    (response, url) => url.pathname.endsWith(`/datasources/${importedSource.id}/sync`) && response.request().method() === "POST",
    () => clickHandle(syncButton),
  );
  assert.equal(syncResponse.status(), 200, await syncResponse.text());
  pass("数据源说明、健康检查与语义同步均可用");

  sourceCard = await cardByHeading(".datasource-card", "h3", SOURCE_NAME);
  await clickHandle(await visibleByText(sourceCard, "button", "查看结构"));
  await frame.waitForFunction(table => [...document.querySelectorAll(".el-dialog .table-name")]
    .some(node => node.textContent.includes(table)), { timeout: 30000 }, TABLE_NAME);
  await clickText(frame, ".el-dialog button", "查看字段");
  await frame.waitForFunction(() => [...document.querySelectorAll(".el-dialog")]
    .some(dialog => dialog.getClientRects().length && dialog.textContent.includes("字段结构")
      && dialog.textContent.includes("revenue") && dialog.textContent.includes("temperature")));
  await closeTopDialog();
  await frame.waitForFunction(() => [...document.querySelectorAll(".el-dialog")]
    .filter(node => node.getClientRects().length > 0).length === 1);
  await clickText(frame, ".el-dialog button", "查询");
  await frame.waitForSelector(".intelligent-query", { visible: true, timeout: 30000 });
  await frame.waitForFunction(() => document.querySelector(".connection-status")?.textContent.includes("表结构已加载"));
  const sourceContext = await frame.$eval(".source-context", node => node.textContent);
  assert(sourceContext.includes("财务、销售与设备运行联合分析数据"));
  assert(sourceContext.includes("1 张业务表"));
  pass("结构、字段和查询入口通过界面连通");

  phase("执行真实聚合 SQL 并检查统计洞察");
  await clickHandle(await frame.$("#query-mode"));
  await frame.waitForFunction(() => [...document.querySelectorAll(".el-select-dropdown__item")]
    .some(node => node.getClientRects().length && node.textContent.trim() === "手工 SQL 查询"));
  await clickText(frame, ".el-select-dropdown__item", "手工 SQL 查询");
  const sql = `SELECT department, SUM(CAST(revenue AS REAL)) AS revenue, SUM(CAST(profit AS REAL)) AS profit, SUM(CAST(warning_count AS INTEGER)) AS warnings FROM "${TABLE_NAME}" GROUP BY department ORDER BY revenue DESC`;
  await fillElement(await frame.$('textarea[aria-label="查询问题或只读 SQL"]'), sql);
  const queryResponse = await waitUiResponse(
    (response, url) => url.pathname === "/api/v1/analytics/query" && response.request().method() === "POST",
    () => clickText(frame, "button", "执行查询"),
    180000,
  );
  assert.equal(queryResponse.status(), 200, await queryResponse.text());
  const queryResult = envelopeData(await queryResponse.json());
  assert(queryResult.totalCount >= 4, "Expected grouped finance and equipment rows");
  await frame.waitForSelector(".query-result-panel", { visible: true, timeout: 30000 });
  const resultText = await frame.$eval(".query-result-panel", node => node.textContent);
  assert(resultText.includes("销售一部"));
  assert(resultText.includes("revenue") || resultText.includes("收入"));
  const insightTab = await visibleByText(frame, '[role="tab"]', "统计与洞察");
  if (insightTab) {
    await clickHandle(insightTab);
    await frame.waitForFunction(() => [...document.querySelectorAll(".query-result-panel details")]
      .some(node => node.getClientRects().length > 0 && node.textContent.includes("逐列统计详情")));
    const insightText = await frame.$eval(".query-result-panel", node => node.textContent);
    assert(insightText.includes("统计覆盖本次 SQL 返回的全部结果"));
  }
  await page.screenshot({ path: path.join(OUTPUT, "real-query-result.png"), fullPage: false });
  await runAxe("/analytics");
  pass("真实财务销售设备 SQL 查询、结果与洞察可用", { queryId: queryResult.queryId, rows: queryResult.totalCount });

  phase("核验 33 套智慧云脑模板、真实缩略图与回收站");
  await visit("/templates", "[data-template-id]");
  await frame.waitForFunction(() => document.querySelectorAll("[data-template-id]").length === 33, { timeout: 30000 });
  const templateSummary = await frame.evaluate(() => {
    const cards = [...document.querySelectorAll("[data-template-id]")];
    const first = cards[0];
    const image = first.querySelector(".template-image img");
    return {
      count: cards.length,
      id: first.dataset.templateId,
      name: first.querySelector("h2")?.textContent.trim() || "",
      source: first.textContent.includes("智慧云脑模板"),
      locked: first.textContent.includes("视觉锁定") && first.textContent.includes("只填数据"),
      preview: image?.getAttribute("src") || "",
    };
  });
  assert(templateSummary.id && templateSummary.name);
  assert.equal(templateSummary.source, true);
  assert.equal(templateSummary.locked, true);
  assert(/report-thumbnails\/lynx-\d+\.png$/.test(new URL(templateSummary.preview, BASE).pathname));
  const firstTemplateCard = await frame.$("[data-template-id]");
  await clickHandle(await firstTemplateCard.$(".template-image"));
  await frame.waitForFunction(() => {
    const image = [...document.querySelectorAll(".el-dialog img.large-template-preview")]
      .find(node => node.getClientRects().length > 0);
    return Boolean(image?.complete && image.naturalWidth > 0 && image.naturalHeight > 0);
  });
  await page.screenshot({ path: path.join(OUTPUT, "lynx-template-preview.png"), fullPage: false });
  await closeTopDialog();

  let templateCard = await frame.$(`[data-template-id="${templateSummary.id}"]`);
  const templateDelete = await visibleByText(templateCard, "button", "删除");
  const deleteTemplateResponse = await waitUiResponse(
    (response, url) => url.pathname === `/api/v1/report-templates/${templateSummary.id}` && response.request().method() === "DELETE",
    async () => { await clickHandle(templateDelete); await confirmMessage({ title: "删除模板", message: "删除后模板会进入当前工作区回收站，已有报表不受影响。", label: "删除" }); },
  );
  assert.equal(deleteTemplateResponse.status(), 200, await deleteTemplateResponse.text());
  await frame.waitForFunction(() => document.querySelectorAll("[data-template-id]").length === 32);
  await clickText(frame, "button", "回收站");
  await frame.waitForFunction(name => [...document.querySelectorAll(".template-recycle-list > div")]
    .some(node => node.textContent.includes(name)), {}, templateSummary.name);
  const recycleRowRaw = await frame.evaluateHandle(name => [...document.querySelectorAll(".template-recycle-list > div")]
    .find(node => node.textContent.includes(name)) || null, templateSummary.name);
  const recycleRow = recycleRowRaw.asElement();
  const recoverResponse = await waitUiResponse(
    (response, url) => url.pathname === `/api/v1/report-templates/${templateSummary.id}/recover` && response.request().method() === "POST",
    async () => clickHandle(await recycleRow.$("button")),
  );
  assert.equal(recoverResponse.status(), 200, await recoverResponse.text());
  await closeTopDialog();
  await frame.waitForFunction(() => document.querySelectorAll("[data-template-id]").length === 33);
  pass("33 套真实智慧云脑模板、截图预览、删除与回收恢复可用");

  phase("使用模板创建空数据报表并核对视觉锁定");
  templateCard = await frame.$(`[data-template-id="${templateSummary.id}"]`);
  await clickHandle(await visibleByText(templateCard, "button", "使用此模板"));
  await frame.waitForFunction(name => [...document.querySelectorAll(".el-dialog")]
    .some(node => node.getClientRects().length && node.textContent.includes(`使用「${name}」`)), {}, templateSummary.name);
  const useDialogState = await frame.evaluate(() => {
    const dialog = [...document.querySelectorAll(".el-dialog")].find(node =>
      node.getClientRects().length > 0 && node.textContent.includes("空数据副本") && node.textContent.includes("按客户需求填充"));
    if (!dialog) return null;
    return {
      emptySelected: dialog.querySelector('[role="radio"][aria-checked="true"]')?.textContent.includes("空数据副本") || false,
      modes: dialog.querySelectorAll('[role="radio"]').length,
      sourcesVisible: Boolean(dialog.querySelector(".source-selection")),
    };
  });
  assert(useDialogState, "The template-use dialog is not visible");
  assert.deepEqual(useDialogState, { emptySelected: true, modes: 2, sourcesVisible: false });
  await fillDialogField("报表名称", TEMPLATE_REPORT_NAME);
  const createTemplateReportResponse = await waitUiResponse(
    (response, url) => url.pathname === "/api/v1/reports" && response.request().method() === "POST",
    () => clickText(frame, ".el-dialog button", "创建空数据报表"),
  );
  assert.equal(createTemplateReportResponse.status(), 201, await createTemplateReportResponse.text());
  const templateReport = envelopeData(await createTemplateReportResponse.json());
  assert.equal(templateReport.reportName, TEMPLATE_REPORT_NAME);
  const templateContent = JSON.parse(templateReport.content);
  assert.equal(templateReport.templateId, templateSummary.id);
  assert.equal(templateReport.templateMode, "manual");
  assert(templateContent.widgets.length > 0);
  assert(templateReport.lineage?.templateVisualHash);
  assert(templateContent.widgets.every(widget => {
    const config = widget.config || {};
    return !config.datasourceId && !config.sql && !widget.datasourceId && !widget.query;
  }), "The empty template copy unexpectedly contains source bindings");
  await frame.waitForSelector(".jimu-replica .screen-widget", { visible: true, timeout: 90000 });
  await frame.waitForSelector(".template-style-lock", { visible: true });
  const designerCount = await frame.$$eval(".screen-widget", nodes => nodes.length);
  assert.equal(designerCount, templateContent.widgets.length);
  assert.match(await frame.$eval(".template-style-lock", node => node.textContent), /可自由编辑/);
  pass("模板空数据副本完整保留组件并进入可自由编辑设计器", { widgets: designerCount });

  let templateWidget = await frame.$(".screen-widget");
  const templateTransformBefore = await templateWidget.evaluate(node => node.style.transform);
  await realDrag(templateWidget, 72, 44);
  const templateTransformAfter = await templateWidget.evaluate(node => node.style.transform);
  assert.notEqual(templateTransformAfter, templateTransformBefore, "Template-report component did not move");

  const dataWidgetRaw = await frame.evaluateHandle(() => [...document.querySelectorAll(".screen-widget")].find(node =>
    /Bar|Line|Area|Ring|Pie|Table|Board|Count|Gauge|Chart/i.test(node.dataset.component || "")) || null);
  const dataWidget = dataWidgetRaw.asElement();
  assert(dataWidget, "No data-capable widget exists in the selected Cloud Brain template");
  await clickHandle(dataWidget);
  await frame.waitForSelector('.property-panel .config-tabs button[title="数据"]', { visible: true });
  await clickHandle(await frame.$('.property-panel .config-tabs button[title="数据"]'));
  await frame.waitForSelector(".property-panel .data-settings", { visible: true, timeout: 30000 });
  await clickText(frame, ".property-panel .data-mode button", "数据库");
  await frame.waitForSelector('.widget-binding[aria-label="组件数据库数据绑定"]', { visible: true });
  await frame.waitForFunction(sourceId => [...document.querySelectorAll(".widget-binding select:first-of-type option")]
    .some(option => option.value === sourceId), {}, importedSource.id);
  await frame.select(".widget-binding label:nth-of-type(1) select", importedSource.id);
  await frame.waitForFunction(table => [...document.querySelectorAll(".widget-binding label:nth-of-type(2) select option")]
    .some(option => option.value === table), {}, TABLE_NAME);
  await frame.select(".widget-binding label:nth-of-type(2) select", TABLE_NAME);
  await frame.waitForSelector(".widget-binding fieldset", { visible: true });
  await clickText(frame, ".widget-binding fieldset button", "全选字段");
  await clickText(frame, ".widget-binding fieldset button", "生成只读 SQL");
  await frame.waitForFunction(() => document.querySelector(".widget-binding textarea")?.value.includes("SELECT"));
  const widgetQueryResponse = await waitUiResponse(
    (response, url) => url.pathname === "/api/v1/reports/query" && response.request().method() === "POST",
    () => clickText(frame, ".widget-binding button", "试运行并更新组件数据"),
  );
  assert.equal(widgetQueryResponse.status(), 200, await widgetQueryResponse.text());
  await frame.waitForFunction(() => document.querySelector(".widget-binding")?.textContent.includes("已返回 16 行"));
  const saveTemplateReportResponse = await waitUiResponse(
    (response, url) => url.pathname === `/api/v1/reports/${templateReport.id}` && response.request().method() === "PUT",
    async () => clickHandle(await frame.$('button[aria-label="保存报表"]')),
  );
  assert.equal(saveTemplateReportResponse.status(), 200, await saveTemplateReportResponse.text());
  const boundTemplateReport = envelopeData(await saveTemplateReportResponse.json());
  const boundContent = JSON.parse(boundTemplateReport.content);
  assert.equal(boundTemplateReport.lineage.templateVisualHash, templateReport.lineage.templateVisualHash);
  assert.equal(boundContent.widgets.length, templateContent.widgets.length);
  assert(boundContent.widgets.some(widget => widget.config?.datasourceId === importedSource.id && widget.config?.sql));
  assert.equal(await templateWidget.evaluate(node => node.style.transform), templateTransformAfter);
  pass("模板报表可调整位置并绑定真实数据，保存保留模板来源信息");

  phase("预览并发布模板报表");
  await clickHandle(await frame.$('button[aria-label="预览选项"]'));
  await frame.waitForSelector(".preview-menu", { visible: true });
  await clickText(frame, ".preview-menu button", "本窗口");
  await frame.waitForSelector(".canvas-viewport.is-preview", { visible: true, timeout: 90000 });
  assert.equal(await frame.$$eval(".screen-widget", nodes => nodes.length), templateContent.widgets.length);
  await page.screenshot({ path: path.join(OUTPUT, "bound-template-preview.png"), fullPage: false });
  await clickHandle(await frame.$(".exit-preview"));
  await frame.waitForSelector(".jimu-toolbar", { visible: true });

  await clickHandle(await frame.$('button[aria-label="发布报表"]'));
  await confirmMessage({ title: "发布报表", message: "保存并发布当前报表？仅有相应数据权限的用户可以访问，不会公开到互联网。" });
  await frame.waitForSelector('button[aria-label="撤回为草稿"]', { visible: true, timeout: 90000 });
  pass("报表本窗口预览与发布可用");

  phase("核验设计器 AI 数据源与模板选择");
  await clickHandle(await frame.$(".ai-generate-tool"));
  await frame.waitForSelector(".ai-dialog", { visible: true, timeout: 30000 });
  await frame.waitForFunction(() => Boolean(document.querySelector('select[aria-label="分析数据源"] option')));
  const aiSourceOptions = await frame.$$eval('select[aria-label="分析数据源"] option', nodes => nodes.map(node => node.textContent.trim()));
  assert(aiSourceOptions.some(text => text.includes(SOURCE_NAME)));
  await frame.waitForFunction(() => Boolean(document.querySelector('select[aria-label="模板策略"] option[value="manual"]')));
  const strategyValues = await frame.$$eval('select[aria-label="模板策略"] option', nodes => nodes.map(node => node.value));
  // 设计器整屏生成现有两种语义：自动参考模板，或指定模板且仅生成 SQL；空白报表仍在创建入口提供。
  assert.deepEqual(strategyValues, ["auto", "manual"]);
  assert.deepEqual(await frame.$$eval('select[aria-label="模板策略"] option', nodes => nodes.map(node => node.textContent.trim())),
    ["自动参考模板", "指定模板 · 只生成 SQL"]);
  assert.equal(await frame.$eval('select[aria-label="模板策略"]', node => node.value), "manual");
  await frame.waitForSelector('select[aria-label="指定模板"]', { visible: true });
  assert.equal(await frame.$eval('select[aria-label="指定模板"]', node => node.value), templateSummary.id);
  await frame.select('select[aria-label="分析数据源"]', importedSource.id);
  await frame.select('select[aria-label="模板策略"]', "auto");
  await frame.waitForSelector('select[aria-label="指定模板"]', { hidden: true });
  await frame.waitForSelector(".ai-template-reference-banner", { hidden: true });
  assert((await frame.$eval(".ai-composer-head p", node => node.textContent)).includes("AI 会参考模板生成查询、组件与布局"));
  await frame.select('select[aria-label="模板策略"]', "manual");
  await frame.waitForSelector('select[aria-label="指定模板"]', { visible: true });
  const manualTemplateSelect = await frame.$('select[aria-label="指定模板"]');
  assert.equal(await manualTemplateSelect.evaluate(node => node.value), "");
  assert((await frame.$eval(".ai-composer-head p", node => node.textContent)).includes("只生成 SQL，保留所选模板的布局、组件和样式"));
  // 手工模式清空选择后必须在界面阻止生成，不能偷偷采用原模板或发起模型任务。
  const generationRequests = [];
  const observeGeneration = request => {
    if (request.method() === "POST" && /^\/api\/v1\/reports\/(?:screen|widget)\/ai-generate$/.test(new URL(request.url()).pathname)) {
      generationRequests.push(request.url());
    }
  };
  page.on("request", observeGeneration);
  try {
    await fillElement(await frame.$(".ai-requirement textarea"), "基于财务销售设备数据生成经营指标看板");
    await clickText(frame, ".ai-dialog .dialog-actions button", "生成看板");
    await frame.waitForFunction(() => document.querySelector(".ai-idle-notice")?.textContent.includes("手动模板模式需要明确选择一套模板"));
    assert.deepEqual(generationRequests, [], "未指定模板时不能创建生成任务");
    assert.equal(await frame.$$eval(".screen-widget", nodes => nodes.length), templateContent.widgets.length);
    assert.equal(await frame.$eval('select[aria-label="分析数据源"]', node => node.value), importedSource.id);
  } finally {
    page.off("request", observeGeneration);
  }
  await manualTemplateSelect.select(templateSummary.id);
  await frame.waitForFunction(name => document.querySelector(".ai-template-reference-banner")?.textContent.includes(name), {}, templateSummary.name);
  assert((await frame.$eval(".ai-template-reference-banner", node => node.textContent)).includes("只生成 SQL，保留原有画布、组件、标题、单位和样式"));
  await page.screenshot({ path: path.join(OUTPUT, "designer-ai-template-picker.png"), fullPage: false });
  await clickHandle(await frame.$('.ai-dialog-header button[title="关闭"]'));
  pass("设计器 AI 支持自动参考与指定模板仅生成 SQL，切换清除旧选择，未选模板时阻止任务且保留画布与数据源");

  phase("创建可自由拖动的空白报表");
  const templateListResponse = await waitUiResponse(
    (response, url) => url.pathname === "/api/v1/reports/list" && response.request().method() === "GET",
    async () => clickHandle(await frame.$('button[aria-label="返回报表列表"]')),
  );
  const templateList = envelopeData(await templateListResponse.json());
  assert(templateList.some(item => item.id === templateReport.id), "Created template report is missing from the list response");
  await frame.waitForSelector(".report-workspace", { visible: true, timeout: 30000 });
  await frame.waitForFunction(reportId => Boolean(document.querySelector(`[data-report-id="${reportId}"]`)), {}, templateReport.id);
  await clickHandle(await frame.$(".create-btn"));
  await frame.waitForSelector(".report-create-dialog", { visible: true });
  const createState = await frame.evaluate(() => ({
    modes: document.querySelectorAll('.report-create-mode [role="radio"]').length,
    selectedTemplates: document.querySelectorAll(".report-template-card.selected").length,
    blankSelected: document.querySelector('.report-create-mode [aria-checked="true"]')?.textContent.includes("空白报表") || false,
  }));
  assert.deepEqual(createState, { modes: 2, selectedTemplates: 0, blankSelected: true });
  await frame.evaluate(() => [...document.querySelectorAll('.report-create-mode [role="radio"]')]
    .find(node => node.textContent.includes("参考模板"))?.click());
  await frame.waitForSelector(".report-template-card", { visible: true });
  assert.equal(await frame.$$eval(".report-template-card", nodes => nodes.length), 33);
  assert.equal(await frame.$$eval(".report-template-card.selected", nodes => nodes.length), 0);
  await frame.evaluate(() => [...document.querySelectorAll('.report-create-mode [role="radio"]')]
    .find(node => node.textContent.includes("空白报表"))?.click());
  await fillDialogField("报表名称", BLANK_REPORT_NAME);
  const blankCreateResponse = await waitUiResponse(
    (response, url) => url.pathname === "/api/v1/reports" && response.request().method() === "POST",
    () => clickText(frame, ".report-create-dialog button", "确定"),
  );
  assert.equal(blankCreateResponse.status(), 201, await blankCreateResponse.text());
  const blankReport = envelopeData(await blankCreateResponse.json());
  assert.equal(blankReport.reportName, BLANK_REPORT_NAME);
  await frame.waitForSelector(".jimu-replica .jimu-toolbar", { visible: true, timeout: 90000 });
  assert.equal(await frame.$$eval(".screen-widget", nodes => nodes.length), 0);
  await clickHandle(await frame.$('.component-trigger[aria-label="图表"]'));
  await frame.waitForSelector(".component-library", { visible: true });
  const category = await frame.$(".component-library .category-list button");
  await category.hover();
  await frame.waitForSelector(".component-library .component-item", { visible: true });
  await clickHandle(await frame.$(".component-library .component-item"));
  await frame.waitForSelector(".screen-widget", { visible: true });
  let movableWidget = await frame.$(".screen-widget");
  const movableBefore = await movableWidget.evaluate(node => node.style.transform);
  await realDrag(movableWidget, 84, 52);
  const movableAfter = await movableWidget.evaluate(node => node.style.transform);
  assert.notEqual(movableAfter, movableBefore, "Blank-report component did not move");
  const blankSaveResponse = await waitUiResponse(
    (response, url) => url.pathname === `/api/v1/reports/${blankReport.id}` && response.request().method() === "PUT",
    async () => clickHandle(await frame.$('button[aria-label="保存报表"]')),
  );
  assert.equal(blankSaveResponse.status(), 200, await blankSaveResponse.text());
  const blankListResponse = await waitUiResponse(
    (response, url) => url.pathname === "/api/v1/reports/list" && response.request().method() === "GET",
    async () => clickHandle(await frame.$('button[aria-label="返回报表列表"]')),
  );
  const blankList = envelopeData(await blankListResponse.json());
  assert(blankList.some(item => item.id === blankReport.id), "Created blank report is missing from the list response");
  await frame.waitForSelector(".report-workspace", { visible: true, timeout: 30000 });
  await frame.waitForFunction(reportId => Boolean(document.querySelector(`[data-report-id="${reportId}"]`)), {}, blankReport.id);
  const blankCard = await cardByHeading("[data-report-id]", ".name", BLANK_REPORT_NAME);
  await clickHandle(await blankCard.$('button[aria-label="打开完整设计器"]'));
  await frame.waitForSelector(".screen-widget", { visible: true, timeout: 30000 });
  movableWidget = await frame.$(".screen-widget");
  assert.equal(await movableWidget.evaluate(node => node.style.transform), movableAfter);
  pass("空白报表组件可拖动、保存并重新打开保持位置");

  await clickHandle(await frame.$('button[aria-label="返回报表列表"]'));
  await frame.waitForSelector(".report-workspace", { visible: true, timeout: 30000 });
  assert.equal(await deleteReportByName(BLANK_REPORT_NAME, blankReport.id), true);
  assert.equal(await deleteReportByName(TEMPLATE_REPORT_NAME, templateReport.id), true);
  pass("报表删除按钮可用且不会产生重复交付报表");

  await visit("/datasources", ".datasource-workspace");
  sourceCard = await cardByHeading(".datasource-card", "h3", SOURCE_NAME);
  const sourceDelete = await sourceCard.$('button[aria-label="删除数据源"]');
  const removeSourceResponse = await waitUiResponse(
    (response, url) => url.pathname === `/api/v1/analytics/datasources/${importedSource.id}` && response.request().method() === "DELETE",
    async () => { await clickHandle(sourceDelete); await confirmMessage({ title: `移除数据源「${SOURCE_NAME}」`, message: "移除后停止新查询，但不会删除外部数据库、历史结果或已生成报表。", label: "确认移除" }); },
  );
  assert.equal(removeSourceResponse.status(), 200, await removeSourceResponse.text());
  await frame.waitForFunction(name => ![...document.querySelectorAll(".datasource-card h3")]
    .some(node => node.textContent.trim() === name), { timeout: 30000 }, SOURCE_NAME);
  pass("数据源移除流程可用");

  await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 1, isMobile: true });
  await visit("/templates", ".business-page");
  await frame.waitForSelector("[data-template-id]", { visible: true, timeout: 30000 });
  const mobile = await frame.evaluate(() => ({
    viewport: innerWidth,
    content: document.documentElement.scrollWidth,
    cardRight: document.querySelector("[data-template-id]")?.getBoundingClientRect().right || 0,
    cards: document.querySelectorAll("[data-template-id]").length,
  }));
  assert(mobile.content <= mobile.viewport + 1, "Template center overflows on mobile: " + JSON.stringify(mobile));
  assert(mobile.cardRight <= mobile.viewport + 1);
  assert.equal(mobile.cards, 33);
  await runAxe("/templates-mobile");
  await page.screenshot({ path: path.join(OUTPUT, "templates-mobile.png"), fullPage: false });
  pass("模板中心移动端无横向溢出", mobile);

  const serious = accessibilityFindings.filter(item => ["serious", "critical"].includes(item.impact));
  assert.deepEqual(serious, [], JSON.stringify(serious, null, 2));
  assert.deepEqual(failures, [], JSON.stringify(failures, null, 2));
  const result = {
    ok: true,
    checks,
    failures,
    consoleErrors,
    dragEvidence,
    reportCleanupReceipts,
    confirmationReceipts,
    accessibilityFindings,
    source: { name: SOURCE_NAME, id: importedSource.id, table: TABLE_NAME },
    query: { id: queryResult.queryId, rows: queryResult.totalCount },
    template: templateSummary,
    templateReport: { id: templateReport.id, widgets: templateContent.widgets.length },
    blankReport: { id: blankReport.id, before: movableBefore, after: movableAfter },
    mobile,
  };
  fs.writeFileSync(path.join(OUTPUT, "result.json"), JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ ok: true, checks: checks.length, failures: 0, output: OUTPUT }));
}

// 窄回归复用同一确认逻辑，导入模块时不启动完整业务验收。
module.exports = { confirmMessage };

if (require.main === module) main().catch(async error => {
  console.error(error.stack || error);
  if (page && !page.isClosed()) {
    await page.screenshot({ path: path.join(OUTPUT, "failure.png"), fullPage: false }).catch(() => {});
  }
  if (fs.existsSync(OUTPUT)) {
    fs.writeFileSync(path.join(OUTPUT, "result.json"), JSON.stringify({
      ok: false,
      error: String(error.stack || error),
      checks,
      failures,
      consoleErrors,
      dragEvidence,
      reportCleanupReceipts,
      confirmationReceipts,
      accessibilityFindings,
    }, null, 2));
  }
  process.exitCode = 1;
}).finally(async () => {
  if (browser) await browser.close();
});
