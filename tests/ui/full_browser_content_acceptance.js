/*
 * 中文说明（内容/知识中心端到端浏览器验收）：
 * 验证行为：通过界面完成知识库与文件治理相关操作，配套本地 http 桩提供内容源。
 * 运行方式：需隔离服务（TEST5_E2E_BASE_URL，默认 8011）+ 本机 Edge + TEST5_E2E_PASSWORD。
 * 边界：会在隔离环境产生文件/知识库数据；桩服务只提供内容，不代替平台接口。
 */
"use strict";

const assert = require("node:assert/strict");
const { createHash } = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const puppeteer = require("puppeteer-core");

const BASE = process.env.TEST5_E2E_BASE_URL || "http://127.0.0.1:8028";
const PASSWORD = process.env.TEST5_E2E_PASSWORD;
const EDGE = process.env.TEST5_EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const RUN = Date.now().toString(36);
const OUTPUT = path.resolve(".implementation/ui-results/full-browser-content-" + RUN);
const checks = [];
const failures = [];
let browser;
let page;

const names = {
  rootCategory: "终验企业资料-" + RUN,
  childCategory: "终验设备知识-" + RUN,
  initialFile: "终验初始资料-" + RUN + ".txt",
  file: "终验泵站知识-" + RUN + ".txt",
  copiedFile: "终验泵站知识-" + RUN + "（副本）.txt",
  batchCsv: "终验批量分段-" + RUN + ".csv",
  batchText: "终验批量说明-" + RUN + ".txt",
  knowledge: "终验设备知识库-" + RUN,
  knowledgeUpdated: "终验设备与财务知识库-" + RUN,
  role: "终验内容使用者-" + RUN,
  term: "设备健康度-" + RUN,
  termUpdated: "设备综合健康度-" + RUN,
  metric: "设备异常率-" + RUN,
  metricUpdated: "关键设备异常率-" + RUN,
};

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function pass(name, detail = {}) {
  checks.push({ name, ...detail });
  console.log("PASS " + name);
}

async function text(selector, root = page) {
  return root.$eval(selector, element => element.textContent.trim());
}

async function fill(selector, value) {
  const element = await page.waitForSelector(selector, { visible: true });
  await element.click({ clickCount: 3 });
  await page.keyboard.down("Control");
  await page.keyboard.press("KeyA");
  await page.keyboard.up("Control");
  if (String(value)) await element.type(String(value));
  else await page.keyboard.press("Backspace");
}

async function clickHandle(element) {
  assert(element, "Expected a clickable element");
  await element.evaluate(node => node.scrollIntoView({ block: "center", inline: "nearest" }));
  await element.click();
}

async function selectByText(selector, label, exact = false) {
  await page.waitForSelector(selector, { visible: true, timeout: 90000 });
  // 现有文件选择器异步加载目录和知识库成员关系，必须等目标选项可用后再操作。
  await page.waitForFunction(args => {
    const select = document.querySelector(args.selector);
    return select && !select.disabled && [...select.options].some(item => !item.disabled && item.value &&
      (args.exact ? item.textContent.trim() === args.label : item.textContent.trim().includes(args.label)));
  }, { timeout: 90000 }, { selector, label, exact });
  const value = await page.$eval(selector, (select, args) => {
    const option = [...select.options].find(item => {
      const shown = item.textContent.trim();
      return !item.disabled && (args.exact ? shown === args.label : shown.includes(args.label));
    });
    return option ? option.value : "";
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

async function maybeRowByText(value, rootSelector = "#main-content") {
  try {
    return await rowByText(value, rootSelector);
  } catch {
    return null;
  }
}

async function visit(route) {
  const response = await page.goto(BASE + route, { waitUntil: "networkidle0", timeout: 90000 });
  assert.equal(response.status(), 200, route);
  await page.waitForSelector("#main-content", { visible: true });
  await page.waitForFunction(() => document.querySelector("#main-content")?.textContent.trim().length > 0);
}

async function saveDialog(expected = "", timeout = 90000) {
  const dialog = await page.waitForSelector("#platform-dialog[open]", { visible: true });
  const button = await dialog.$('button[type="submit"]');
  assert(button, "Dialog has no submit button");
  await clickHandle(button);
  await page.waitForFunction(() => {
    const current = document.querySelector("#platform-dialog");
    const error = current?.querySelector(".form-error:not([hidden])");
    return !current?.open || Boolean(error?.textContent.trim());
  }, { timeout });
  const result = await page.evaluate(() => {
    const current = document.querySelector("#platform-dialog");
    return {
      open: Boolean(current?.open),
      error: current?.querySelector(".form-error:not([hidden])")?.textContent.trim() || "",
    };
  });
  assert.equal(result.open, false, result.error || "Dialog did not close");
  if (expected) {
    await page.waitForFunction(value => document.querySelector("#main-content")?.textContent.includes(value),
      { timeout }, expected);
  }
  await sleep(100);
}

async function confirmDialog(label, expected = "", timeout = 90000) {
  const buttons = await page.$$("#platform-dialog[open] button");
  for (const button of buttons) {
    const shown = await button.evaluate(node => node.textContent.trim());
    if (shown === label) {
      await clickHandle(button);
      await page.waitForFunction(() => !document.querySelector("#platform-dialog")?.open, { timeout });
      if (expected) {
        await page.waitForFunction(value => document.querySelector("#main-content")?.textContent.includes(value),
          { timeout }, expected);
      }
      return;
    }
  }
  throw new Error("Confirmation button not found: " + label);
}

async function clickTab(fragment, readySelector) {
  const selector = '.content-tabs a[href*="' + fragment + '"]';
  await page.waitForSelector(selector, { visible: true });
  await Promise.all([
    page.waitForNavigation({ waitUntil: "networkidle0", timeout: 90000 }),
    // Knowledge-tab counters can rerender the navigation between selector
    // discovery and Puppeteer's scroll step. Dispatch from the current DOM in
    // one browser task so the real link is still used without a stale handle.
    page.$eval(selector, node => node.click()),
  ]);
  await page.waitForSelector(readySelector, { visible: true });
}

async function openFileMenu(fileName) {
  const row = await rowByText(fileName);
  const summary = await row.$(".content-action-menu summary");
  await clickHandle(summary);
  return row;
}

async function deleteFile(fileName) {
  const row = await openFileMenu(fileName);
  await clickHandle(await row.$("[data-file-delete]"));
  await page.waitForSelector("#platform-dialog[open]", { visible: true });
  await confirmDialog("删除");
  await page.waitForFunction(value => !document.querySelector("#main-content tbody")?.textContent.includes(value),
    { timeout: 90000 }, fileName);
}

function startEmbeddingFixture() {
  let calls = 0;
  const server = http.createServer((request, response) => {
    const chunks = [];
    request.on("data", chunk => chunks.push(chunk));
    request.on("end", () => {
      if (!request.url.endsWith("/embeddings")) {
        response.writeHead(404, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: { message: "not found" } }));
        return;
      }
      calls += 1;
      const payload = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
      const inputs = Array.isArray(payload.input) ? payload.input : [payload.input || ""];
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({
        data: inputs.map((value, index) => ({
          index,
          object: "embedding",
          embedding: Array.from({ length: 1024 }, (_, dimension) =>
            dimension === index % 16 || dimension === String(value).length % 16 ? 1 : 0),
        })),
        model: payload.model || "browser-e2e-embedding",
        usage: { prompt_tokens: inputs.length, total_tokens: inputs.length },
      }));
    });
  });
  return {
    server,
    calls: () => calls,
    listen: () => new Promise(resolve => server.listen(0, "127.0.0.1", resolve)),
    close: () => new Promise(resolve => server.close(resolve)),
  };
}

(async () => {
  assert(PASSWORD, "Supply TEST5_E2E_PASSWORD");
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, "isolated");
  assert(["127.0.0.1", "localhost"].includes(new URL(BASE).hostname));
  fs.mkdirSync(OUTPUT, { recursive: true });
  const initialDirectory = path.join(OUTPUT, "upload-v1");
  const versionDirectory = path.join(OUTPUT, "upload-v2");
  const batchDirectory = path.join(OUTPUT, "upload-batch");
  fs.mkdirSync(initialDirectory, { recursive: true });
  fs.mkdirSync(versionDirectory, { recursive: true });
  fs.mkdirSync(batchDirectory, { recursive: true });
  const initialUploadPath = path.join(initialDirectory, names.initialFile);
  const versionUploadPath = path.join(versionDirectory, names.file);
  const batchCsvPath = path.join(batchDirectory, names.batchCsv);
  const batchTextPath = path.join(batchDirectory, names.batchText);
  fs.copyFileSync(path.resolve("tests/fixtures/knowledge-e2e.txt"), initialUploadPath);
  fs.copyFileSync(path.resolve("tests/fixtures/knowledge-e2e-v2.txt"), versionUploadPath);
  fs.copyFileSync(path.resolve("tests/fixtures/full-browser-segments.csv"), batchCsvPath);
  fs.copyFileSync(path.resolve("tests/fixtures/knowledge-e2e-v2.txt"), batchTextPath);

  const embedding = startEmbeddingFixture();
  await embedding.listen();
  const embeddingEndpoint = "http://127.0.0.1:" + embedding.server.address().port + "/v1";

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
  page.on("response", response => {
    if (response.status() >= 500) failures.push(response.status() + " " + response.url());
  });

  try {
    await page.goto(BASE, { waitUntil: "networkidle0", timeout: 90000 });
    await fill("#login-username", "workspace-admin");
    await fill("#login-password", PASSWORD);
    await page.click("#login-btn");
    await page.waitForSelector(".enterprise-area-grid", { visible: true });

    await visit("/system/models");
    let row = await maybeRowByText("embedding");
    if (row) {
      await clickHandle(await row.$("[data-edit-model]"));
    } else {
      await page.click("[data-new-model]");
      await fill('#platform-dialog [name="profile_name"]', "embedding");
    }
    await fill('#platform-dialog [name="provider"]', "openai-compatible");
    await fill('#platform-dialog [name="model"]', "browser-e2e-embedding");
    await fill('#platform-dialog [name="endpoint"]', embeddingEndpoint);
    await fill('#platform-dialog [name="api_key"]', "browser-e2e-key");
    await fill('#platform-dialog [name="capabilities"]', "embedding\nsemantic-search");
    await saveDialog("embedding");
    row = await rowByText("embedding");
    await clickHandle(await row.$("[data-probe-model]"));
    await page.waitForFunction(() => {
      const toast = document.querySelector("#platform-toast");
      return toast && !toast.hidden && toast.textContent.includes("通过");
    }, { timeout: 90000 });
    pass("Embedding profile is configured and probed through the model screen");

    await visit("/system/roles");
    await page.click("[data-new-role]");
    await fill('#platform-dialog [name="name"]', names.role);
    await fill('#platform-dialog [name="code"]', "content-" + RUN);
    await fill('#platform-dialog [name="description"]', "内容、目录与知识库浏览器验收角色");
    await page.select('#platform-dialog [name="data_scope"]', "workspace");
    for (const permission of ["home.view", "files.view", "knowledge.view"]) {
      const checkbox = await page.$('#platform-dialog [name="permissions"][value="' + permission + '"]');
      assert(checkbox, "Missing permission " + permission);
      await checkbox.click();
    }
    await saveDialog(names.role);
    pass("A least-privilege content role is created through role management");

    await visit("/files");
    await page.click("[data-categories]");
    await page.click("#platform-dialog [data-category-new]");
    await fill('#platform-dialog [name="name"]', names.rootCategory);
    await fill('#platform-dialog [name="description"]', "浏览器终验顶级分类");
    await saveDialog(names.rootCategory);
    await page.click("[data-categories]");
    await page.click("#platform-dialog [data-category-new]");
    await fill('#platform-dialog [name="name"]', names.childCategory);
    await selectByText('#platform-dialog [name="parent_id"]', names.rootCategory, true);
    await fill('#platform-dialog [name="description"]', "泵站设备与运维知识");
    await saveDialog(names.childCategory);
    pass("Nested file categories are created through the category manager");

    await page.click("[data-upload]");
    let fileInput = await page.waitForSelector('#platform-dialog input[type="file"]');
    await fileInput.uploadFile(initialUploadPath);
    await selectByText('#platform-dialog [name="category_id"]', names.childCategory);
    await fill('#platform-dialog [name="tags"]', "设备，ERP，终验");
    await fill('#platform-dialog [name="description"]', "泵站设备和财务规则样例");
    await saveDialog(names.initialFile, 180000);

    row = await openFileMenu(names.initialFile);
    await clickHandle(await row.$("[data-file-edit]"));
    await fill('#platform-dialog [name="name"]', names.file);
    await saveDialog(names.file);

    row = await rowByText(names.file);
    await clickHandle(await row.$("[data-file-preview]"));
    await page.waitForSelector("#platform-dialog .content-text-preview", { visible: true });
    assert((await text("#platform-dialog .content-text-preview")).includes("五十万元"));
    await page.click("#platform-dialog [data-dialog-close]");

    row = await rowByText(names.file);
    await clickHandle(await row.$("[data-file-versions]"));
    await page.waitForSelector("#platform-dialog[open] [data-version-new]", { visible: true });
    await page.click("#platform-dialog [data-version-new]");
    fileInput = await page.waitForSelector('#platform-dialog input[type="file"]');
    await fileInput.uploadFile(versionUploadPath);
    await fill('#platform-dialog [name="change_note"]', "将投标保证金更新为六十万元");
    await saveDialog(names.file, 180000);
    row = await rowByText(names.file);
    await clickHandle(await row.$("[data-file-versions]"));
    await page.waitForFunction(() => {
      const dialog = document.querySelector("#platform-dialog[open]");
      return dialog?.textContent.includes("v1") && dialog?.textContent.includes("v2");
    });
    await clickHandle(await page.$('#platform-dialog [data-version-restore="1"]'));
    await page.waitForSelector("#platform-dialog[open]", { visible: true });
    await confirmDialog("恢复", names.file, 180000);
    row = await rowByText(names.file);
    await clickHandle(await row.$("[data-file-preview]"));
    await page.waitForSelector("#platform-dialog .content-text-preview", { visible: true });
    assert((await text("#platform-dialog .content-text-preview")).includes("五十万元"));
    await page.click("#platform-dialog [data-dialog-close]");
    pass("File upload, metadata edit, preview, version upload and guarded restore all work");

    await page.click("[data-batch-upload]");
    fileInput = await page.waitForSelector('#platform-dialog input[type="file"]');
    await fileInput.uploadFile(batchCsvPath, batchTextPath);
    await selectByText('#platform-dialog [name="category_id"]', names.childCategory);
    await fill('#platform-dialog [name="tags"]', "批量上传，终验");
    await saveDialog(names.batchCsv, 180000);
    await page.waitForFunction(value => document.querySelector("#main-content tbody")?.textContent.includes(value),
      { timeout: 90000 }, names.batchText);

    row = await openFileMenu(names.file);
    const copyButton = await row.$("[data-file-copy]");
    const sourceId = await copyButton.evaluate(node => node.dataset.fileCopy);
    // 副本标记插在扩展名前；同时核对新资产身份、字节摘要及真实预览，不能仅匹配列表文字。
    const [copyResponse] = await Promise.all([
      page.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === `/api/files/${sourceId}/copy`),
      clickHandle(copyButton),
    ]);
    assert.equal(copyResponse.status(), 201);
    const copied = await copyResponse.json();
    assert.equal(copied.name, names.copiedFile);
    assert.equal(copied.source_id, sourceId);
    assert.notEqual(copied.id, sourceId);
    assert.equal(copied.logical_id, copied.id);
    assert.equal(copied.revision_number, 1);
    assert.equal(copied.status, "ready");
    assert.equal(copied.sha256, createHash("sha256").update(fs.readFileSync(initialUploadPath)).digest("hex"));
    await page.waitForFunction(value => document.querySelector("#main-content tbody")?.textContent.includes(value),
      { timeout: 90000 }, names.copiedFile);
    row = await rowByText(names.copiedFile);
    assert.equal(await row.$eval("[data-file-preview]", node => node.dataset.filePreview), copied.id);
    await clickHandle(await row.$("[data-file-preview]"));
    await page.waitForSelector("#platform-dialog .content-text-preview", { visible: true });
    assert((await text("#platform-dialog .content-text-preview")).includes("五十万元"));
    await page.click("#platform-dialog [data-dialog-close]");
    pass("Batch upload and independent file copy preserve the restored bytes and preview", { sourceId, copiedFileId: copied.id });

    await visit("/knowledge");
    await page.click("[data-new-kb]");
    await fill('#platform-dialog [name="name"]', names.knowledge);
    await fill('#platform-dialog [name="description"]', "真实文件、分段、检索、同步与授权终验");
    await saveDialog(names.knowledge);
    row = await rowByText(names.knowledge);
    const kbLink = await row.$('a[href^="/knowledge?id="]');
    const kbHref = await kbLink.evaluate(node => node.getAttribute("href"));
    await Promise.all([
      page.waitForNavigation({ waitUntil: "networkidle0", timeout: 90000 }),
      clickHandle(kbLink),
    ]);
    await page.waitForSelector(".content-tabs", { visible: true });

    await page.click("[data-add-document]");
    await page.click('#platform-dialog [name=document_source][value=existing]');
    await selectByText('#platform-dialog [name="file_id"]', names.file);
    await fill('#platform-dialog [name="chunk_size"]', "300");
    await fill('#platform-dialog [name="overlap"]', "30");
    await saveDialog(names.file, 180000);
    assert(embedding.calls() > 1);
    row = await rowByText(names.file);
    await clickHandle(await row.$("[data-document-reindex]"));
    await fill('#platform-dialog [name="chunk_size"]', "320");
    await fill('#platform-dialog [name="overlap"]', "40");
    await saveDialog(names.file, 180000);
    pass("A real file is parsed, embedded and reindexed from the knowledge-base screen");

    await clickTab("tab=segments", "[data-segment-new]");
    await page.click("[data-segment-new]");
    await page.select('#platform-dialog [name="segment_type"]', "qa");
    await fill('#platform-dialog [name="question"]', "设备温度超限怎么办？");
    await fill('#platform-dialog [name="answer"]', "立即停机并通知值班主管。");
    await saveDialog("设备温度超限怎么办", 180000);
    await page.click("[data-segment-import]");
    fileInput = await page.waitForSelector('#platform-dialog input[type="file"]');
    await fileInput.uploadFile(path.resolve("tests/fixtures/full-browser-segments.csv"));
    await saveDialog("财务月结前需要核对哪些项目", 180000);
    row = await rowByText("设备温度超限怎么办");
    await clickHandle(await row.$("[data-segment-edit]"));
    await fill('#platform-dialog [name="answer"]', "立即停机、隔离设备并通知值班主管。");
    await saveDialog("立即停机、隔离设备");
    pass("Manual QA segments, CSV import and segment editing work");

    await clickTab("tab=search", "[data-recall-form]");
    await fill('[data-recall-form] [name="query"]', "设备温度超限怎么办");
    await page.select('[data-recall-form] [name="mode"]', "keyword");
    const [recallResponse] = await Promise.all([
      page.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/knowledge/search"),
      page.click('[data-recall-form] button[type="submit"]'),
    ]);
    assert.equal(recallResponse.status(), 200);
    const recall = await recallResponse.json();
    assert.equal(recall.mode, "keyword");
    assert(recall.items.some(item => item.content.includes("立即停机、隔离设备并通知值班主管。")));
    await page.waitForFunction(() => document.querySelector("[data-search-results]")?.textContent.includes("立即停机、隔离设备并通知值班主管。"),
      { timeout: 90000 });
    await page.waitForFunction(() => document.querySelector("[data-recall-logs]")?.textContent.includes("设备温度超限怎么办"),
      { timeout: 90000 });
    pass("Keyword recall returns the edited answer and records an audit log", { mode: recall.mode, hits: recall.count });

    await clickTab("tab=settings", "[data-kb-settings]");
    await page.select('[data-kb-settings] [name="retrieval_mode"]', "keyword");
    await fill('[data-kb-settings] [name="top_k"]', "9");
    await page.click('[data-kb-settings] button[type="submit"]');
    await page.waitForFunction(() => {
      const toast = document.querySelector("#platform-toast");
      return toast && !toast.hidden && toast.textContent.includes("设置已保存");
    });
    assert.equal(await page.$eval('[data-kb-settings] [name="top_k"]', node => node.value), "9");

    await clickTab("tab=sync", "[data-sync-form]");
    await selectByText('[data-sync-form] [name="category_id"]', names.childCategory);
    await page.click('[data-sync-form] button[type="submit"]');
    await page.waitForFunction(value => document.querySelector("#main-content tbody")?.textContent.includes(value),
      { timeout: 180000 }, names.childCategory);
    pass("Knowledge settings and category synchronization persist through their own forms");

    await clickTab("tab=access", "[data-grant-new]");
    await page.click("[data-grant-new]");
    await selectByText('#platform-dialog [name="role_id"]', names.role, true);
    for (const action of ["create", "edit", "execute"]) {
      await page.click('#platform-dialog [name="actions"][value="' + action + '"]');
    }
    await saveDialog(names.role);
    row = await rowByText(names.role);
    await clickHandle(await row.$("[data-grant-delete]"));
    await page.waitForSelector("#platform-dialog[open]", { visible: true });
    await confirmDialog("撤销");
    await page.waitForFunction(value => !document.querySelector("#main-content tbody")?.textContent.includes(value),
      { timeout: 90000 }, names.role);
    pass("Knowledge-base role grants can be created with explicit actions and revoked");

    await page.click("[data-edit-kb]");
    await fill('#platform-dialog [name="name"]', names.knowledgeUpdated);
    await saveDialog(names.knowledgeUpdated);

    await visit("/assets-catalog?tab=catalog");
    await fill('[data-asset-search] [name="keyword"]', names.file);
    await Promise.all([
      page.waitForNavigation({ waitUntil: "networkidle0", timeout: 90000 }),
      page.click('[data-asset-search] button[type="submit"]'),
    ]);
    await page.waitForSelector("[data-resource-directory]", { visible: true });
    const directoryText = await text("[data-resource-directory]");
    assert(directoryText.includes(names.file));
    assert(directoryText.includes(names.childCategory));
    const openedGroups = await page.$$eval("[data-resource-directory] details[open]", nodes => nodes.length);
    assert(openedGroups >= 2, "Search should expose both resource type and category hierarchy");
    const resourceLink = await page.$("[data-resource-directory] .resource-directory-item");
    await Promise.all([
      page.waitForNavigation({ waitUntil: "networkidle0", timeout: 90000 }),
      clickHandle(resourceLink),
    ]);
    await page.waitForSelector("[data-edit-annotation]", { visible: true });
    await page.click("[data-edit-annotation]");
    await fill('#platform-dialog [name="aliases"]', "主泵知识，设备手册");
    await fill('#platform-dialog [name="category"]', names.childCategory);
    await fill('#platform-dialog [name="businessDomain"]', "设备运维");
    await page.select('#platform-dialog [name="sensitivity"]', "internal");
    await fill('#platform-dialog [name="freshnessMinutes"]', "1440");
    await fill('#platform-dialog [name="tags"]', "泵站，设备，终验");
    await fill('#platform-dialog [name="description"]', "用于设备预警和质量检查的受控知识资料。");
    await saveDialog("主泵知识");
    pass("The resource directory expands by type/category and saves governed annotations");

    await visit("/assets-catalog?tab=terms");
    await page.click("[data-new-term]");
    await fill('#platform-dialog [name="code"]', "equipment.health." + RUN);
    await fill('#platform-dialog [name="name"]', names.term);
    await fill('#platform-dialog [name="businessDomain"]', "设备运维");
    await fill('#platform-dialog [name="aliases"]', "健康指数，设备评分");
    await fill('#platform-dialog [name="definition"]', "综合振动、温度、压力与报警状态形成的设备健康评价。");
    await fill('#platform-dialog [name="tags"]', "设备，指标");
    await saveDialog(names.term);
    row = await rowByText(names.term);
    await clickHandle(await row.$("[data-edit-term]"));
    await fill('#platform-dialog [name="name"]', names.termUpdated);
    await saveDialog(names.termUpdated);

    await visit("/assets-catalog?tab=metrics");
    await page.click("[data-new-metric]");
    await fill('#platform-dialog [name="code"]', "equipment.alert_rate." + RUN);
    await fill('#platform-dialog [name="name"]', names.metric);
    await page.select('#platform-dialog [name="aggregation"]', "ratio");
    await fill('#platform-dialog [name="unit"]', "%");
    await fill('#platform-dialog [name="granularity"]', "日");
    await fill('#platform-dialog [name="dimensions"]', "设备，区域，班组");
    await fill('#platform-dialog [name="formula"]', "异常设备数 / 在用设备数 * 100");
    await fill('#platform-dialog [name="description"]', "按日在用设备口径计算异常比例。");
    await selectByText('#platform-dialog [data-source-ref-row] select', names.file);
    await fill('#platform-dialog [data-source-ref-row] input', "temperature");
    await saveDialog(names.metric);
    row = await rowByText(names.metric);
    await clickHandle(await row.$("[data-edit-metric]"));
    await fill('#platform-dialog [name="name"]', names.metricUpdated);
    await saveDialog(names.metricUpdated);

    await visit("/assets-catalog?tab=relations");
    await page.click("[data-new-relation]");
    await selectByText('#platform-dialog [name="source"]', names.file);
    await selectByText('#platform-dialog [name="target"]', names.knowledgeUpdated);
    await page.select('#platform-dialog [name="relationKind"]', "derived_from");
    await fill('#platform-dialog [name="description"]', "知识库由受控设备资料解析生成。");
    await saveDialog(names.file);
    row = await rowByText(names.file);
    await clickHandle(await row.$("[data-edit-relation]"));
    await page.select('#platform-dialog [name="relationKind"]', "contains");
    await fill('#platform-dialog [name="description"]', "资料内容被知识库索引并保留来源关系。");
    await saveDialog(names.file);
    row = await rowByText(names.file);
    await clickHandle(await row.$("[data-archive-relation]"));
    await page.waitForSelector("#platform-dialog[open]", { visible: true });
    await confirmDialog("归档");

    await visit("/assets-catalog?tab=metrics");
    row = await rowByText(names.metricUpdated);
    await clickHandle(await row.$("[data-archive-metric]"));
    await page.waitForSelector("#platform-dialog[open]", { visible: true });
    await confirmDialog("归档");
    await visit("/assets-catalog?tab=terms");
    row = await rowByText(names.termUpdated);
    await clickHandle(await row.$("[data-archive-term]"));
    await page.waitForSelector("#platform-dialog[open]", { visible: true });
    await confirmDialog("归档");
    pass("Business terms, sourced metrics and resource lineage support create, edit and archive lifecycles");

    await page.setViewport({ width: 390, height: 844, isMobile: true });
    await visit(kbHref + "&tab=segments");
    const mobile = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      viewport: innerWidth,
      tabs: document.querySelectorAll(".content-tabs a").length,
    }));
    assert(mobile.scrollWidth <= mobile.viewport, "Knowledge page overflows on mobile");
    assert(mobile.tabs >= 5);
    await page.screenshot({ path: path.join(OUTPUT, "knowledge-mobile.png"), fullPage: true });
    await page.setViewport({ width: 1440, height: 960, isMobile: false });
    pass("Knowledge management remains usable without horizontal page overflow on mobile", mobile);

    await visit(kbHref);
    await page.click("[data-delete-kb]");
    await page.waitForSelector("#platform-dialog[open]", { visible: true });
    await confirmDialog("删除");
    await page.waitForFunction(value => !document.querySelector("#main-content")?.textContent.includes(value),
      { timeout: 90000 }, names.knowledgeUpdated);

    await visit("/files");
    for (const fileName of [
      names.copiedFile,
      names.file,
      names.batchCsv,
      names.batchText,
    ]) {
      if (await maybeRowByText(fileName)) await deleteFile(fileName);
    }
    for (const categoryName of [names.childCategory, names.rootCategory]) {
      await page.click("[data-categories]");
      row = await rowByText(categoryName, "#platform-dialog");
      await clickHandle(await row.$("[data-category-delete]"));
      await page.waitForSelector("#platform-dialog[open]", { visible: true });
      await confirmDialog("删除");
    }

    await visit("/system/roles");
    await fill('#main-content [data-search-form] input[name="q"]', names.role);
    // 角色筛选通过接口原地重绘，不产生导航；等待真实筛选回执并校验目标角色。
    const [roleSearchResponse] = await Promise.all([
      page.waitForResponse(response => {
        const url = new URL(response.url());
        return response.request().method() === "GET" && url.pathname === "/api/v1/system/roles" && url.searchParams.get("q") === names.role;
      }),
      page.keyboard.press("Enter"),
    ]);
    assert.equal(roleSearchResponse.status(), 200);
    const roleSearch = await roleSearchResponse.json();
    assert(roleSearch.items.some(item => item.name === names.role));
    await page.waitForFunction(value => [...document.querySelectorAll('#main-content tbody tr')]
      .every(row => row.textContent.includes(value)), { timeout: 90000 }, names.role);
    row = await rowByText(names.role);
    await clickHandle(await row.$("[data-delete-role]"));
    await page.waitForSelector("#platform-dialog[open]", { visible: true });
    await confirmDialog("删除角色");

    await visit("/system/models");
    row = await rowByText("embedding");
    const stopButton = await row.$("[data-delete-model]");
    if (stopButton) {
      await clickHandle(stopButton);
      await page.waitForSelector("#platform-dialog[open]", { visible: true });
      await confirmDialog("停用");
    }
    pass("Temporary knowledge, files, categories, role and model configuration are cleaned through their screens");

    await page.screenshot({ path: path.join(OUTPUT, "content-final.png"), fullPage: true });
    assert.deepEqual(failures, []);
    fs.writeFileSync(path.join(OUTPUT, "result.json"), JSON.stringify({
      ok: true,
      baseUrl: BASE,
      embeddingCalls: embedding.calls(),
      checks,
      failures,
      names,
    }, null, 2));
    console.log("Evidence " + OUTPUT);
  } catch (error) {
    failures.push(error.stack || String(error));
    await page.screenshot({ path: path.join(OUTPUT, "failure.png"), fullPage: true }).catch(() => {});
    fs.writeFileSync(path.join(OUTPUT, "result.json"), JSON.stringify({
      ok: false,
      baseUrl: BASE,
      embeddingCalls: embedding.calls(),
      checks,
      failures,
      names,
    }, null, 2));
    throw error;
  } finally {
    await browser?.close();
    await embedding.close();
  }
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
