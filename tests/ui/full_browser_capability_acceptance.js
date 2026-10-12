/*
 * 中文说明（能力中心端到端浏览器验收）：
 * 验证行为：通过界面完成能力（工具/连接器）的创建、测试与发布，并校验结果落库。
 * 运行方式：需隔离服务与本机 Edge；文件内自带本地 http 桩以模拟被调用的外部程序。
 * 边界：真实执行能力会发起网络调用，必须只对隔离实例运行。
 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const puppeteer = require("puppeteer-core");

const BASE = process.env.TEST5_E2E_BASE_URL || "http://127.0.0.1:8030";
const PASSWORD = process.env.TEST5_E2E_PASSWORD;
const EDGE = process.env.TEST5_EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const RUN = Date.now().toString(36);
const OUTPUT = path.resolve(".implementation/ui-results/full-browser-capability-" + RUN);
const checks = [];
const failures = [];
const accessibilityFindings = [];
const fixtureRequests = [];
let browser;
let page;

const names = {
  connector: "终验设备状态连接器-" + RUN,
  connectorVersion: "终验设备状态连接器二版-" + RUN,
  connectorCopy: "终验设备状态连接器副本-" + RUN,
  tool: "acceptance.equipment_status_" + RUN,
  category: "终验设备能力-" + RUN,
  childCategory: "终验设备查询-" + RUN,
  emptyCategory: "终验待归档分类-" + RUN,
  skill: "终验设备状态复核-" + RUN,
  skillCopy: "终验设备状态复核副本-" + RUN,
  plugin: "终验设备状态能力包-" + RUN,
  pluginCopy: "终验设备状态能力包副本-" + RUN,
  application: "终验设备状态应用-" + RUN,
  applicationCopy: "终验设备状态应用副本-" + RUN,
};

function pass(name, detail = {}) {
  checks.push({ name, ...detail });
  console.log("PASS " + name);
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function startFixture() {
  const server = http.createServer((request, response) => {
    const chunks = [];
    request.on("data", chunk => chunks.push(chunk));
    request.on("end", () => {
      let body = {};
      try { body = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}"); }
      catch { body = {}; }
      fixtureRequests.push({ method: request.method, url: request.url, body });
      response.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
      response.end(JSON.stringify({
        equipmentId: body.equipmentId || "",
        status: "warning",
        temperature: 86.4,
        source: "local-acceptance-fixture",
      }));
    });
  });
  return {
    server,
    listen: () => new Promise(resolve => server.listen(0, "127.0.0.1", resolve)),
    close: () => new Promise(resolve => server.close(resolve)),
  };
}

async function fill(selector, value) {
  const element = await page.waitForSelector(selector, { visible: true, timeout: 90000 });
  await element.click({ clickCount: 3 });
  await page.keyboard.down("Control");
  await page.keyboard.press("KeyA");
  await page.keyboard.up("Control");
  await page.keyboard.press("Backspace");
  if (String(value)) await element.type(String(value));
}

async function clickHandle(element) {
  assert(element, "Expected a clickable element");
  await element.evaluate(node => node.scrollIntoView({ block: "center", inline: "nearest" }));
  await element.click();
}

async function clickText(selector, label, exact = true) {
  const elements = await page.$$(selector);
  for (const element of elements) {
    const shown = await element.evaluate(node => node.textContent.trim());
    if ((exact && shown === label) || (!exact && shown.includes(label))) {
      await clickHandle(element);
      return;
    }
  }
  throw new Error("Clickable text not found: " + label + " in " + selector);
}

async function selectByText(selector, label, exact = false) {
  await page.waitForSelector(selector, { visible: true, timeout: 90000 });
  const value = await page.$eval(selector, (select, args) => {
    const option = [...select.options].find(item => {
      const shown = item.textContent.trim();
      return args.exact ? shown === args.label : shown.includes(args.label);
    });
    return option?.value || "";
  }, { label, exact });
  assert(value, "Option not found: " + label + " in " + selector);
  await page.select(selector, value);
  return value;
}

async function rowByText(value, rootSelector = "#main-content") {
  const rows = await page.$$(rootSelector + " tbody tr");
  for (const row of rows) {
    if (await row.evaluate((node, expected) => node.textContent.includes(expected), value)) return row;
  }
  throw new Error("Table row not found: " + value);
}

async function openRow(value, readySelector) {
  if (new URL(page.url()).searchParams.get("id") && await page.$(readySelector) && (await page.$eval("#main-content", node => node.textContent)).includes(value)) {
    return new URL(page.url()).searchParams.get("id");
  }
  const row = await rowByText(value);
  const link = await row.$("a.cell-primary, a[href]");
  assert(link, "No open link for " + value);
  await Promise.all([
    page.waitForNavigation({ waitUntil: "networkidle0", timeout: 90000 }),
    clickHandle(link),
  ]);
  await page.waitForSelector(readySelector, { visible: true, timeout: 90000 });
  return new URL(page.url()).searchParams.get("id");
}

async function visit(route, readySelector = "#main-content") {
  const response = await page.goto(BASE + route, { waitUntil: "networkidle0", timeout: 90000 });
  assert.equal(response.status(), 200, route);
  await page.waitForSelector(readySelector, { visible: true, timeout: 90000 });
  await page.waitForFunction(() => document.querySelector("#main-content")?.textContent.trim().length > 0);
}

async function submitAndNavigate(expected = "") {
  const submit = await page.waitForSelector('#platform-dialog[open] button[type="submit"]', { visible: true, timeout: 90000 });
  await Promise.all([
    page.waitForNavigation({ waitUntil: "networkidle0", timeout: 90000 }),
    clickHandle(submit),
  ]);
  if (expected) {
    await page.waitForFunction(value => document.querySelector("#main-content")?.textContent.includes(value),
      { timeout: 90000 }, expected);
  }
}

async function confirmAndNavigate(label, expected = "") {
  await page.waitForSelector("#platform-dialog[open]", { visible: true, timeout: 90000 });
  const buttons = await page.$$("#platform-dialog[open] button");
  for (const button of buttons) {
    if (await button.evaluate(node => node.textContent.trim()) !== label) continue;
    await Promise.all([
      page.waitForNavigation({ waitUntil: "networkidle0", timeout: 90000 }),
      clickHandle(button),
    ]);
    if (expected) {
      await page.waitForFunction(value => document.querySelector("#main-content")?.textContent.includes(value),
        { timeout: 90000 }, expected);
    }
    return;
  }
  throw new Error("Confirmation button not found: " + label);
}

async function submitForResult(expectedText) {
  const submit = await page.waitForSelector('#platform-dialog[open] button[type="submit"]', { visible: true, timeout: 90000 });
  await clickHandle(submit);
  await page.waitForFunction(() => {
    const dialog = document.querySelector("#platform-dialog[open]");
    return Boolean(dialog?.querySelector(".capability-result")) ||
      Boolean(dialog?.querySelector(".form-error:not([hidden])")?.textContent.trim());
  }, { timeout: 180000 });
  const state = await page.$eval("#platform-dialog[open]", dialog => ({
    result: dialog.querySelector(".capability-result")?.textContent || "",
    error: dialog.querySelector(".form-error:not([hidden])")?.textContent.trim() || "",
  }));
  assert.equal(state.error, "", state.error);
  const result = state.result;
  assert(result.includes(expectedText), result);
  return result;
}

async function closeDialog() {
  const close = await page.waitForSelector('#platform-dialog[open] [data-dialog-close]', { visible: true, timeout: 90000 });
  await clickHandle(close);
  await page.waitForFunction(() => !document.querySelector("#platform-dialog")?.open);
}

async function audit(route) {
  await page.addScriptTag({ path: require.resolve("axe-core/axe.min.js") });
  const findings = await page.evaluate(async () => (await axe.run(document, {
    runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] },
  })).violations.map(item => ({
    id: item.id,
    impact: item.impact,
    nodes: item.nodes.map(node => ({ target: node.target, summary: node.failureSummary })),
  })));
  accessibilityFindings.push(...findings.map(item => ({ route, ...item })));
}

(async () => {
  assert(PASSWORD, "Supply TEST5_E2E_PASSWORD");
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, "isolated");
  assert(["127.0.0.1", "localhost"].includes(new URL(BASE).hostname));
  fs.mkdirSync(OUTPUT, { recursive: true });
  const fixture = startFixture();
  await fixture.listen();
  const fixtureBase = "http://127.0.0.1:" + fixture.server.address().port;

  browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: true,
    args: ["--no-first-run", "--disable-gpu", "--window-size=1440,960"],
  });
  page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 960 });
  page.on("pageerror", error => failures.push("pageerror " + error.message));
  page.on("console", message => {
    if (message.type() === "error") failures.push("console " + message.text());
  });
  page.on("requestfailed", request => failures.push("requestfailed " + request.url() + " " + request.failure()?.errorText));
  page.on("response", response => {
    if (response.status() >= 400 && response.url().includes("/api/")) {
      failures.push(response.status() + " " + response.url());
    }
  });

  try {
    await page.goto(BASE, { waitUntil: "networkidle0", timeout: 90000 });
    await fill("#login-username", "workspace-admin");
    await fill("#login-password", PASSWORD);
    await page.click("#login-btn");
    await page.waitForSelector(".enterprise-area-grid", { visible: true, timeout: 90000 });

    await visit("/capabilities?view=methods", "[data-method-new]");
    await page.click("[data-method-new]");
    await page.waitForSelector("#platform-dialog[open] [data-tool-row]", { visible: true, timeout: 90000 });
    const initialForm = await page.evaluate(() => ({
      title: document.querySelector("#dialog-title")?.textContent.trim(),
      provider: document.querySelector('#platform-dialog [name="provider"]')?.value,
      visibleProviderBlocks: [...document.querySelectorAll("#platform-dialog [data-provider]")]
        .filter(node => !node.hidden).map(node => node.dataset.provider),
      rows: document.querySelectorAll("#platform-dialog [data-tool-row]").length,
    }));
    assert.equal(initialForm.title, "新增方法");
    assert.equal(initialForm.provider, "http");
    assert.deepEqual([...new Set(initialForm.visibleProviderBlocks)], ["http"]);
    assert.equal(initialForm.rows, 1);
    await page.click("#platform-dialog [data-add-tool]");
    assert.equal(await page.$$eval("#platform-dialog [data-tool-row]", rows => rows.length), 2);
    const removeButtons = await page.$$("#platform-dialog [data-remove-tool]");
    await clickHandle(removeButtons[1]);
    assert.equal(await page.$$eval("#platform-dialog [data-tool-row]", rows => rows.length), 1);
    await fill('#platform-dialog [name="name"]', names.connector);
    await fill('#platform-dialog [name="baseUrl"]', fixtureBase);
    await fill('#platform-dialog [name="path"]', "/equipment/status");
    await page.select('#platform-dialog [name="method"]', "POST");
    await page.select('#platform-dialog [name="bodyMode"]', "json");
    const privateAccess = await page.$('#platform-dialog [name="allowPrivate"]');
    assert(privateAccess, "Admin-only private-network authorization is missing");
    await clickHandle(privateAccess);
    await fill('#platform-dialog input[name^="tool_name_"]', names.tool);
    await fill('#platform-dialog input[name^="tool_description_"]', "按设备编号读取真实状态");
    await page.click("#platform-dialog [data-tool-row] details summary");
    await fill('#platform-dialog textarea[name^="tool_schema_"]', JSON.stringify({
      type: "object",
      properties: { equipmentId: { type: "string", minLength: 1 } },
      required: ["equipmentId"],
      additionalProperties: false,
    }, null, 2));
    await audit("/methods-create-dialog");
    await submitAndNavigate(names.connector);
    const connectorId = new URL(page.url()).searchParams.get("id");
    assert(connectorId, "Connector id is missing from its visible detail URL");
    assert((await page.$eval(".capability-detail", node => node.innerText)).includes(names.tool));
    await page.screenshot({ path: path.join(OUTPUT, "connector-draft.png"), fullPage: true });
    pass("Method management can create an HTTP method through the typed source editor, including add/remove tool controls");

    await page.click("[data-connector-publish]");
    await confirmAndNavigate("确认发布", names.connector);
    assert((await page.$eval(".capability-detail", node => node.innerText)).includes("已发布"));
    await page.click("[data-connector-test]");
    await selectByText('#platform-dialog [name="toolName"]', names.tool, true);
    await fill('#platform-dialog [name="arguments"]', JSON.stringify({ equipmentId: "EQ-ACCEPT-001" }, null, 2));
    const connectorResult = await submitForResult("EQ-ACCEPT-001");
    assert(connectorResult.includes("warning"));
    assert.equal(fixtureRequests.length, 1);
    assert.deepEqual(fixtureRequests[0].body, { equipmentId: "EQ-ACCEPT-001" });
    await page.screenshot({ path: path.join(OUTPUT, "connector-real-test.png"), fullPage: true });
    await closeDialog();
    pass("Published connector executes a real local HTTP request and keeps its result dialog visible");

    await visit("/capabilities?keyword=" + encodeURIComponent(names.tool), ".table-scroll");
    assert.equal(await page.$$eval("tbody tr", rows => rows.length), 1);
    const toolIdLink = await (await rowByText(names.tool)).$("a.cell-primary");
    await Promise.all([
      page.waitForNavigation({ waitUntil: "networkidle0", timeout: 90000 }),
      clickHandle(toolIdLink),
    ]);
    await page.waitForSelector(".capability-detail", { visible: true });
    assert.equal(await page.$eval(".capability-schema", node => node.open), false);
    await page.click("[data-tool-validate]");
    await fill('#platform-dialog [name="arguments"]', JSON.stringify({ equipmentId: "EQ-VALID-002" }));
    const validationResult = await submitForResult("EQ-VALID-002");
    assert(validationResult.includes("arguments"));
    await closeDialog();
    pass("Published tool is discoverable and its JSON Schema validates customer-supplied arguments");

    await visit("/capabilities?view=categories", "[data-category-new]");
    await page.click("[data-category-new]");
    await fill('#platform-dialog [name="name"]', names.category);
    await fill('#platform-dialog [name="code"]', "equipment_" + RUN);
    await fill('#platform-dialog [name="description"]', "设备接入与状态查询能力");
    await submitAndNavigate(names.category);
    await page.click("[data-category-new]");
    await fill('#platform-dialog [name="name"]', names.childCategory);
    await fill('#platform-dialog [name="code"]', "equipment_query_" + RUN);
    await selectByText('#platform-dialog [name="parentId"]', names.category);
    await submitAndNavigate(names.childCategory);
    assert((await page.$eval("tbody", node => node.innerText)).includes(names.category + " / " + names.childCategory));
    await page.click("[data-category-new]");
    await fill('#platform-dialog [name="name"]', names.emptyCategory);
    await fill('#platform-dialog [name="code"]', "archive_" + RUN);
    await submitAndNavigate(names.emptyCategory);
    const emptyRow = await rowByText(names.emptyCategory);
    const emptyArchive = await emptyRow.$("[data-category-delete]");
    await clickHandle(emptyArchive);
    await confirmAndNavigate("确认归档", names.category);
    assert(!(await page.$eval("tbody", (node, value) => node.innerText.includes(value), names.emptyCategory)));
    pass("Tool categories support parent-child organization and honest archive constraints through the UI");

    await visit("/capabilities?view=methods&keyword=" + encodeURIComponent(names.tool), ".table-scroll");
    assert.equal(await page.$$eval("tbody tr", rows => rows.length), 1);
    const methodActions = await page.$eval(".method-row-actions", node => node.innerText);
    for (const label of ["查看", "编辑", "测试", "设置", "删除"]) assert(methodActions.includes(label), methodActions);
    await page.click("[data-method-view]");
    await page.waitForSelector("#platform-dialog[open] .capability-detail", { visible: true, timeout: 90000 });
    assert((await page.$eval("#platform-dialog .capability-detail", node => node.innerText)).includes(names.connector));
    await closeDialog();
    await page.click("[data-method-test]");
    await fill('#platform-dialog [name="arguments"]', JSON.stringify({ equipmentId: "EQ-METHOD-003" }, null, 2));
    const methodResult = await submitForResult("EQ-METHOD-003");
    assert(methodResult.includes("warning"));
    assert.deepEqual(fixtureRequests.at(-1).body, { equipmentId: "EQ-METHOD-003" });
    await closeDialog();
    await page.click("[data-method-source-edit]");
    await page.waitForSelector('#platform-dialog[open] [name="allowPrivate"]', { visible: true, timeout: 90000 });
    assert.equal(await page.$eval('#platform-dialog [name="allowPrivate"]', node => node.checked), true);
    await closeDialog();
    await page.click("[data-method-edit]");
    await selectByText('#platform-dialog [name="categoryId"]', names.childCategory);
    await fill('#platform-dialog [name="displayName"]', "终验设备状态查询");
    await fill('#platform-dialog [name="tags"]', "设备，状态，终验");
    await submitAndNavigate("终验设备状态查询");
    assert((await page.$eval("tbody", node => node.innerText)).includes(names.childCategory));
    await page.click("[data-method-select]");
    assert.equal(await page.$eval("[data-method-batch]", node => node.disabled), false);
    await page.click("[data-method-batch]");
    await page.select('#platform-dialog [name="enabled"]', "false");
    await submitAndNavigate("终验设备状态查询");
    assert((await page.$eval("tbody", node => node.innerText)).includes("停用"));
    await page.click("[data-method-edit]");
    const enabledToggle = await page.$('#platform-dialog [name="enabled"]');
    assert.equal(await enabledToggle.evaluate(node => node.checked), false);
    await clickHandle(enabledToggle);
    await submitAndNavigate("终验设备状态查询");
    assert((await page.$eval("tbody", node => node.innerText)).includes("启用"));
    await page.screenshot({ path: path.join(OUTPUT, "method-governance.png"), fullPage: true });
    pass("Method rows provide source-aware read, edit, real private-HTTP test, settings and delete controls");
    pass("Method classification, display metadata, batch disable and explicit re-enable all govern the same real tool");

    await visit("/skills", "[data-new-skill]");
    await page.click("[data-new-skill]");
    await fill('#platform-dialog [name="name"]', names.skill);
    await fill('#platform-dialog [name="description"]', "设备状态复核与处置建议");
    await fill('#platform-dialog [name="instructions"]', "读取设备编号，核验状态工具结果，输出状态、温度和处置建议。");
    await fill('#platform-dialog [name="rules"]', "不得猜测设备状态\n必须保留工具证据");
    await submitAndNavigate(names.skill);
    const skillId = await openRow(names.skill, ".capability-detail");
    assert(skillId, "Skill id is missing from its visible detail URL");
    assert((await page.$eval(".capability-detail", node => node.innerText)).includes("必须保留工具证据"));
    await page.click("[data-skill-publish]");
    await confirmAndNavigate("确认发布", names.skill);
    assert((await page.$eval(".capability-detail", node => node.innerText)).includes("已发布"));
    await page.click("[data-skill-clone]");
    await fill('#platform-dialog [name="new_name"]', names.skillCopy);
    await submitAndNavigate(names.skillCopy);
    assert((await page.$eval(".capability-detail", node => node.innerText)).includes("仅自己"));
    await page.click("[data-skill-share]");
    await confirmAndNavigate("确认修改", names.skillCopy);
    assert((await page.$eval(".capability-detail", node => node.innerText)).includes("工作区"));
    await page.click("[data-skill-archive]");
    await confirmAndNavigate("确认归档", names.skill);
    assert.equal(new URL(page.url()).pathname, "/skills");
    pass("Skill creation, publication, copy, sharing and history-preserving archive are usable without API shortcuts");

    await visit("/plugins", "[data-new-plugin]");
    await page.click("[data-new-plugin]");
    await fill('#platform-dialog [name="name"]', names.plugin);
    await fill('#platform-dialog [name="description"]', "把设备复核方法与实时状态工具组合为能力包");
    await fill('#platform-dialog [name="instructions"]', "先核验设备编号，再调用状态工具，最后按规则输出处置建议。");
    const bindings = await page.$("#platform-dialog .capability-bindings");
    await clickHandle(bindings);
    const selectedSkill = await page.$$eval('#platform-dialog input[name="skillRefs"]', (inputs, label) => {
      const input = inputs.find(item => item.closest("label")?.textContent.includes(label));
      if (!input) return false;
      input.click();
      return input.checked;
    }, names.skill);
    assert(selectedSkill, "Published Skill is missing from Plugin bindings");
    const selectedTool = await page.$$eval('#platform-dialog input[name="toolRefs"]', (inputs, toolName) => {
      const input = inputs.find(item => item.closest("label")?.textContent.includes(toolName));
      if (!input) return false;
      input.click();
      return input.checked;
    }, names.tool);
    assert(selectedTool, "Published tool is missing from Plugin bindings");
    await submitAndNavigate(names.plugin);
    const pluginId = await openRow(names.plugin, ".capability-detail");
    assert(pluginId, "Plugin id is missing from its visible detail URL");
    assert((await page.$eval(".capability-detail", node => node.innerText)).includes(names.tool));
    await page.click("[data-plugin-publish]");
    await confirmAndNavigate("确认发布", names.plugin);
    assert((await page.$eval(".capability-detail", node => node.innerText)).includes("已发布"));
    await page.click("[data-plugin-clone]");
    await fill('#platform-dialog [name="new_name"]', names.pluginCopy);
    await submitAndNavigate(names.pluginCopy);
    assert((await page.$eval(".capability-detail", node => node.innerText)).includes("仅自己"));
    await page.click("[data-plugin-share]");
    await confirmAndNavigate("确认修改", names.pluginCopy);
    await page.click("[data-plugin-archive]");
    await confirmAndNavigate("确认归档", names.plugin);
    assert.equal(new URL(page.url()).pathname, "/plugins");
    pass("Plugin creation binds the visible Skill and tool, then supports publish, copy, share and archive");

    await visit("/applications", "[data-new-app]");
    await page.click("[data-new-app]");
    await fill('#platform-dialog [name="name"]', names.application);
    await fill('#platform-dialog [name="code"]', "equipment-status-" + RUN);
    await fill('#platform-dialog [name="description"]', "设备状态查询与复核统一入口");
    await page.select('#platform-dialog [name="kind"]', "assistant");
    await page.select('#platform-dialog [name="entryType"]', "plugin");
    await selectByText('#platform-dialog [name="entryId"]', names.plugin);
    const advanced = await page.$$eval("#platform-dialog details", nodes => ({
      count: nodes.length,
      allClosed: nodes.every(node => !node.open),
    }));
    assert(advanced.count >= 3 && advanced.allClosed);
    await submitAndNavigate(names.application);
    const applicationId = new URL(page.url()).searchParams.get("id");
    assert(applicationId, "Application id is missing from its visible detail URL");
    assert((await page.$eval(".application-detail", node => node.innerText)).includes(names.plugin));
    await page.click("[data-app-publish]");
    await confirmAndNavigate("确认发布", names.application);
    assert(await page.$("[data-app-launch]"));
    await audit("/applications-published-detail");
    await page.screenshot({ path: path.join(OUTPUT, "application-published.png"), fullPage: true });
    await Promise.all([
      page.waitForNavigation({ waitUntil: "networkidle0", timeout: 90000 }),
      page.click("[data-app-launch]"),
    ]);
    const launched = new URL(page.url());
    assert.equal(launched.pathname, "/ai");
    assert.equal(launched.searchParams.get("plugin_id"), pluginId);
    await page.waitForSelector("#composer-form", { visible: true, timeout: 90000 });
    pass("Published application without parameters opens its fixed Plugin entry directly with one click");

    await visit("/applications?id=" + encodeURIComponent(applicationId), ".application-detail");
    assert((await page.$eval(".application-detail", node => node.innerText)).includes("启动记录 · 1"));
    await page.click("[data-app-clone]");
    await fill('#platform-dialog [name="name"]', names.applicationCopy);
    await fill('#platform-dialog [name="code"]', "equipment-status-copy-" + RUN);
    await submitAndNavigate(names.applicationCopy);
    const applicationCopyId = new URL(page.url()).searchParams.get("id");
    assert(applicationCopyId && applicationCopyId !== applicationId);
    await page.click("[data-app-archive]");
    await confirmAndNavigate("确认归档", names.application);
    assert.equal(new URL(page.url()).pathname, "/applications");
    pass("Application launch is traceable, and copied drafts can be archived independently");

    await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 1, isMobile: true });
    await visit("/applications?id=" + encodeURIComponent(applicationId), ".application-detail");
    await page.click("[data-app-edit]");
    await page.waitForSelector("#platform-dialog[open] .application-form", { visible: true, timeout: 90000 });
    const mobile = await page.evaluate(() => {
      const dialog = document.querySelector("#platform-dialog").getBoundingClientRect();
      return {
        overflow: document.documentElement.scrollWidth - innerWidth,
        left: dialog.left,
        right: dialog.right,
        columns: getComputedStyle(document.querySelector("#platform-dialog .application-form")).gridTemplateColumns.split(" ").length,
        primaryLinks: [...document.querySelectorAll(".enterprise-primary a")].filter(node => node.getClientRects().length).length,
      };
    });
    assert(mobile.overflow <= 1 && mobile.left >= 0 && mobile.right <= 390, JSON.stringify(mobile));
    assert.equal(mobile.columns, 1);
    assert.equal(mobile.primaryLinks, 0);
    await audit("/applications-mobile-version-dialog");
    await page.screenshot({ path: path.join(OUTPUT, "capability-mobile.png"), fullPage: true });
    await closeDialog();
    pass("Capability composition remains operable in a 390px customer viewport");

    await page.setViewport({ width: 1440, height: 960 });
    await visit("/connectors?id=" + encodeURIComponent(connectorId), ".capability-detail");
    await page.click("[data-connector-version]");
    await page.waitForSelector('#platform-dialog[open] [name="provider"]', { visible: true, timeout: 90000 });
    assert.equal(await page.$eval('#platform-dialog [name="provider"]', node => node.disabled), true);
    await fill('#platform-dialog [name="name"]', names.connectorVersion);
    await submitAndNavigate(names.connectorVersion);
    assert((await page.$eval(".capability-detail", node => node.innerText)).includes("版本记录 · 2"));
    await page.click("[data-connector-publish]");
    await confirmAndNavigate("确认发布", names.connectorVersion);
    await page.click("[data-connector-clone]");
    await fill('#platform-dialog [name="name"]', names.connectorCopy);
    await fill('#platform-dialog [name="toolPrefix"]', "acceptcopy." + RUN);
    await submitAndNavigate(names.connectorCopy);
    assert((await page.$eval(".capability-detail", node => node.innerText)).includes("仅自己"));
    await page.click("[data-connector-share]");
    await confirmAndNavigate("确认修改", names.connectorCopy);
    assert((await page.$eval(".capability-detail", node => node.innerText)).includes("工作区"));
    await page.click("[data-connector-archive]");
    await confirmAndNavigate("确认归档", names.connectorVersion);
    assert.equal(new URL(page.url()).pathname, "/connectors");
    pass("Connector versioning, provider immutability, copy, visibility change and archive preserve source history");

    await visit("/capabilities?view=methods&keyword=" + encodeURIComponent(names.tool), ".table-scroll");
    assert.equal(await page.$$eval("tbody tr", rows => rows.length), 1);
    await page.click("[data-method-delete]");
    await page.waitForSelector("#platform-dialog[open]", { visible: true, timeout: 90000 });
    await page.waitForFunction(value => document.querySelector("#platform-dialog[open]")?.innerText.includes(value),
      { timeout: 90000 }, names.connectorVersion);
    assert((await page.$eval("#platform-dialog", node => node.innerText)).includes(names.connectorVersion));
    await confirmAndNavigate("确认删除");
    await visit("/capabilities?view=methods&keyword=" + encodeURIComponent(names.tool), ".panel");
    assert.equal(await page.$$eval("tbody tr", rows => rows.length), 0);
    pass("Deleting a connector-backed method from method management archives its source and removes new execution access");

    assert.deepEqual(failures, []);
    assert.deepEqual(accessibilityFindings, []);
    fs.writeFileSync(path.join(OUTPUT, "result.json"), JSON.stringify({
      ok: true,
      checks,
      failures,
      accessibilityFindings,
      records: { connectorId, skillId, pluginId, applicationId },
      fixtureRequests,
    }, null, 2));
    console.log("RESULT_DIR " + OUTPUT);
    console.log("ACCESSIBILITY_FINDINGS " + accessibilityFindings.length);
  } finally {
    if (browser) {
      await browser.close();
      browser = null;
    }
    await fixture.close();
  }
})().catch(error => {
  failures.push(error.stack || error.message);
  fs.mkdirSync(OUTPUT, { recursive: true });
  fs.writeFileSync(path.join(OUTPUT, "result.json"), JSON.stringify({
    ok: false,
    checks,
    failures,
    accessibilityFindings,
    fixtureRequests,
  }, null, 2));
  console.error(error);
  process.exitCode = 1;
}).finally(async () => {
  if (browser) await browser.close();
});
