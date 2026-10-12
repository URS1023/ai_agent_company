/*
 * 中文说明（内容治理工作台真实浏览器验收）：
 * 验证行为：内容治理（文件分类/版本/可见性）相关页面在真实浏览器中的展示与操作结果。
 * 运行方式：默认连 http://127.0.0.1:8027 的隔离服务（TEST5_E2E_BASE_URL 可覆盖），
 *   口令默认 ContentUiTest12345（TEST5_E2E_PASSWORD 覆盖）；结果写入 .implementation/ui-results/。
 * 边界：用例内自带本地 http 桩以配合前端；失败项集中收集便于定位。
 */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const puppeteer = require("puppeteer-core");

const BASE = process.env.TEST5_E2E_BASE_URL || "http://127.0.0.1:8027";
const PASSWORD = process.env.TEST5_E2E_PASSWORD || "ContentUiTest12345";
const OUTPUT = path.resolve(".implementation/ui-results/content-governance-" + Date.now().toString(36));
const failures = [];

async function request(page, route, method = "GET", body) {
  return page.evaluate(async (route, method, body) => {
    const headers = { Authorization: "Bearer " + localStorage.getItem("agent_token"), "X-Workspace-Id": localStorage.getItem("agent_workspace_id") };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetch(route, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    const type = response.headers.get("Content-Type") || "";
    return { status: response.status, body: type.includes("json") ? await response.json() : await response.text() };
  }, route, method, body);
}

async function login(page) {
  await page.goto(BASE, { waitUntil: "networkidle0", timeout: 60000 });
  await page.type("#login-username", "workspace-admin");
  await page.type("#login-password", PASSWORD);
  await page.click("#login-btn");
  await page.waitForSelector(".enterprise-area-grid", { timeout: 30000 });
}

async function dialogSubmit(page) {
  await page.click("#platform-dialog .dialog-form > footer .button.primary");
  await page.waitForFunction(() => !document.querySelector("#platform-dialog")?.open, { timeout: 180000 });
}

(async () => {
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, "isolated", "Write acceptance requires the isolated server");
  fs.mkdirSync(OUTPUT, { recursive: true });
  let embeddingCalls = 0;
  const fixture = http.createServer((request, response) => {
    const chunks = [];
    request.on("data", chunk => chunks.push(chunk));
    request.on("end", () => {
      if (!request.url.endsWith("/embeddings")) {
        response.writeHead(404, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: { message: "not found" } }));
        return;
      }
      embeddingCalls += 1;
      const payload = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
      const inputs = Array.isArray(payload.input) ? payload.input : [payload.input || ""];
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ data: inputs.map((_, index) => ({ index, embedding: Array.from({ length: 1024 }, (__, dimension) => Number(dimension === index % 8)) })) }));
    });
  });
  await new Promise(resolve => fixture.listen(0, "127.0.0.1", resolve));

  const browser = await puppeteer.launch({
    executablePath: process.env.TEST5_EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    headless: "new", args: ["--no-sandbox", "--disable-gpu", "--window-size=1440,960"],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 960 });
  page.on("pageerror", error => failures.push("page: " + error.message));
  page.on("console", message => { if (message.type() === "error") failures.push("console: " + message.text()); });
  page.on("response", response => { if (response.status() >= 500) failures.push(response.status() + " " + response.url()); });

  let profileId = "", categoryId = "", categoryVersion = 1, originalFileId = "", currentFileId = "", kbId = "";
  try {
    assert.equal((await (await fetch(BASE + "/api/health")).json()).app, "test");
    await login(page);
    const profiles = await request(page, "/api/models/profiles");
    const embedding = profiles.body.items.find(item => item.profile_name === "embedding");
    const configured = await request(page, "/api/models/profiles/embedding", "PUT", {
      provider: "openai-compatible", model: "local-e2e-embedding",
      endpoint: `http://127.0.0.1:${fixture.address().port}/v1`, api_key: "local-e2e-key",
      capabilities: ["embedding", "semantic-search"], enabled: true,
    });
    assert.equal(configured.status, 200); profileId = configured.body.id;

    await page.goto(BASE + "/files", { waitUntil: "networkidle0" });
    await page.waitForSelector("[data-categories]");
    assert.equal(await page.$("#pages"), null);
    assert.equal(await page.$(".metric-strip"), null);
    assert.equal(await page.$eval(".page-heading h1", element => element.textContent.trim()), "文件与生成结果");

    await page.click("[data-categories]");
    await page.click("#platform-dialog [data-category-new]");
    await page.type("#platform-dialog [name=name]", "验收资料");
    await page.type("#platform-dialog [name=description]", "隔离交互验收分类");
    const categoryPromise = page.waitForResponse(response => response.url().endsWith("/api/files/categories") && response.request().method() === "POST");
    await dialogSubmit(page);
    const categoryResponse = await categoryPromise;
    const category = await categoryResponse.json(); categoryId = category.id; categoryVersion = category.version;
    await page.waitForFunction(() => document.body.innerText.includes("验收资料"));

    await page.click("[data-upload]");
    const fileInput = await page.waitForSelector("#platform-dialog input[type=file]");
    await fileInput.uploadFile(path.resolve("tests/fixtures/knowledge-e2e.txt"));
    await page.select("#platform-dialog [name=category_id]", categoryId);
    await page.type("#platform-dialog [name=tags]", "验收，ERP");
    const uploadPromise = page.waitForResponse(response => response.url().endsWith("/api/files") && response.request().method() === "POST" && response.status() === 201);
    await dialogSubmit(page);
    originalFileId = (await (await uploadPromise).json()).id;
    currentFileId = originalFileId;
    await page.waitForFunction(() => document.querySelector("tbody")?.innerText.includes("knowledge-e2e.txt"));
    assert((await page.$eval("tbody", element => element.innerText)).includes("验收资料"));

    await page.click(`[data-file-preview="${originalFileId}"]`);
    await page.waitForSelector("#platform-dialog .content-text-preview");
    assert((await page.$eval("#platform-dialog", element => element.innerText)).includes("五十万元"));
    await page.click("#platform-dialog [data-dialog-close]");

    await page.click(`[data-file-versions="${originalFileId}"]`);
    await page.waitForSelector("#platform-dialog [data-version-new]");
    await page.click("#platform-dialog [data-version-new]");
    const versionInput = await page.waitForSelector("#platform-dialog input[type=file]");
    await versionInput.uploadFile(path.resolve("tests/fixtures/knowledge-e2e-v2.txt"));
    const versionPromise = page.waitForResponse(response => response.url().includes(`/api/files/${originalFileId}/versions`) && response.request().method() === "POST" && response.status() === 201);
    await dialogSubmit(page);
    currentFileId = (await (await versionPromise).json()).id;
    await page.waitForFunction(() => document.querySelector("tbody")?.innerText.includes("v2"));
    await page.click(`[data-file-versions="${currentFileId}"]`);
    await page.waitForSelector("#platform-dialog .table-scroll");
    await page.waitForFunction(() => document.querySelector("#platform-dialog")?.innerText.includes("v1") && document.querySelector("#platform-dialog")?.innerText.includes("v2"));
    await page.screenshot({ path: path.join(OUTPUT, "files-versions.png") });
    await page.click("#platform-dialog [data-dialog-close]");

    await page.goto(BASE + "/knowledge", { waitUntil: "networkidle0" });
    await page.waitForSelector("[data-new-kb]");
    assert.equal(await page.$(".kb-summary-item"), null, "decorative legacy summary cards must be gone");
    await page.click("[data-new-kb]");
    const suffix = Date.now().toString(36);
    const kbName = "ERP 验收知识库 " + suffix;
    await page.type("#platform-dialog [name=name]", kbName);
    await page.type("#platform-dialog [name=description]", "文件、分段、召回、同步与授权验收");
    const kbPromise = page.waitForResponse(response => response.url().endsWith("/api/knowledge") && response.request().method() === "POST" && response.status() === 201);
    await dialogSubmit(page);
    kbId = (await (await kbPromise).json()).id;
    await page.waitForSelector(`a[href="/knowledge?id=${kbId}"]`);
    await page.click(`a[href="/knowledge?id=${kbId}"]`);
    await page.waitForSelector(".content-tabs");
    assert.deepEqual(await page.$$eval(".content-tabs a", elements => elements.map(element => element.textContent.trim())), ["文档", "分段", "检索测试", "同步", "设置", "授权"]);

    await page.waitForSelector("[data-add-document]");
    await page.click("[data-add-document]");
    await page.click('#platform-dialog [name=document_source][value=existing]');
    await page.waitForFunction(() => !document.querySelector('#platform-dialog [name=file_id]').disabled);
    await page.select("#platform-dialog [name=file_id]", currentFileId);
    await dialogSubmit(page);
    await page.waitForFunction(() => document.querySelector("tbody")?.innerText.includes("knowledge-e2e-v2.txt"), { timeout: 180000 });
    assert(embeddingCalls > 0, "configured local embedding endpoint was not called");

    await page.click(`.content-tabs a[href*="tab=segments"]`);
    await page.waitForSelector("[data-segment-new]");
    await page.waitForFunction(() => document.querySelector("tbody")?.innerText.includes("六十万元"));
    await page.click("[data-segment-new]");
    await page.select("#platform-dialog [name=segment_type]", "qa");
    await page.type("#platform-dialog [name=question]", "设备温度超限怎么办？");
    await page.type("#platform-dialog [name=answer]", "立即停机并通知值班主管。");
    await dialogSubmit(page);
    await page.waitForFunction(() => document.querySelector("tbody")?.innerText.includes("设备温度超限怎么办"));

    await page.click(`.content-tabs a[href*="tab=search"]`);
    await page.waitForSelector("[data-recall-form]");
    await page.type("[data-recall-form] [name=query]", "ERP 投标保证金是多少");
    await page.select("[data-recall-form] [name=mode]", "keyword");
    await page.click("[data-recall-form] [type=submit]");
    await page.waitForFunction(() => document.querySelector("[data-search-results]")?.innerText.includes("六十万元"), { timeout: 60000 });
    await page.waitForFunction(() => document.querySelector("[data-recall-logs]")?.innerText.includes("ERP 投标保证金"), { timeout: 30000 });
    assert((await page.$eval("[data-recall-logs]", element => element.innerText)).includes("ERP 投标保证金"));
    await page.screenshot({ path: path.join(OUTPUT, "knowledge-search.png") });

    await page.click(`.content-tabs a[href*="tab=settings"]`);
    await page.waitForSelector("[data-kb-settings]");
    await page.$eval("[data-kb-settings] [name=top_k]", element => { element.value = "9"; });
    await page.click("[data-kb-settings] [type=submit]");
    await page.waitForFunction(() => {
      const submit = document.querySelector("[data-kb-settings] [type=submit]");
      return submit && !submit.disabled && document.querySelector("[data-kb-settings] [name=top_k]")?.value === "9";
    });
    assert.equal(await page.$eval("[data-kb-settings] [name=top_k]", element => element.value), "9");
    assert.equal((await request(page, `/api/knowledge/${kbId}/settings`)).body.top_k, 9);

    await page.click(`.content-tabs a[href*="tab=sync"]`);
    await page.waitForSelector("[data-sync-form]");
    await page.select("[data-sync-form] [name=category_id]", categoryId);
    await page.click("[data-sync-form] [type=submit]");
    await page.waitForFunction(() => document.querySelector("tbody")?.innerText.includes("验收资料") && document.querySelector("tbody")?.innerText.includes("1"), { timeout: 180000 });

    const roleResponse = await request(page, "/api/v1/system/roles", "POST", {
      code: "content-e2e-" + suffix, name: "内容验收角色", permissions: ["home.view", "knowledge.view"], data_scope: "self",
    });
    assert.equal(roleResponse.status, 201);
    await page.click(`.content-tabs a[href*="tab=access"]`);
    await page.waitForSelector("[data-grant-new]");
    await page.click("[data-grant-new]");
    await page.select("#platform-dialog [name=role_id]", roleResponse.body.id);
    await dialogSubmit(page);
    await page.waitForFunction(() => document.querySelector("tbody")?.innerText.includes("内容验收角色"));

    const desktop = await page.evaluate(() => ({
      width: document.documentElement.scrollWidth, viewport: innerWidth,
      cards: document.querySelectorAll(".metric-strip,.kb-summary-item,.overview-intro").length,
      sidebarLinks: [...document.querySelectorAll(".enterprise-sidebar a")].filter(element => element.getClientRects().length).length,
    }));
    assert(desktop.width <= desktop.viewport); assert.equal(desktop.cards, 0); assert(desktop.sidebarLinks <= 7);
    await page.screenshot({ path: path.join(OUTPUT, "knowledge-access.png") });

    await page.setViewport({ width: 390, height: 844, isMobile: true });
    await page.goto(BASE + `/knowledge?id=${kbId}&tab=segments`, { waitUntil: "networkidle0" });
    await page.waitForSelector(".content-tabs");
    const mobile = await page.evaluate(() => ({ width: document.documentElement.scrollWidth, viewport: innerWidth, cards: document.querySelectorAll(".metric-strip,.kb-summary-item").length }));
    assert(mobile.width <= mobile.viewport, "mobile knowledge page overflows"); assert.equal(mobile.cards, 0);
    await page.screenshot({ path: path.join(OUTPUT, "knowledge-mobile.png") });

    assert.deepEqual(failures, []);
    fs.writeFileSync(path.join(OUTPUT, "result.json"), JSON.stringify({ ok: true, embeddingCalls, desktop, mobile, failures }, null, 2));
    console.log("RESULT_DIR " + OUTPUT);
    console.log("EMBEDDING_CALLS " + embeddingCalls);
  } finally {
    if (!page.isClosed()) {
      if (kbId) await request(page, "/api/knowledge/" + kbId, "DELETE").catch(() => {});
      if (originalFileId) await request(page, "/api/files/" + originalFileId, "DELETE").catch(() => {});
      if (categoryId) await request(page, `/api/files/categories/${categoryId}?version=${categoryVersion}`, "DELETE").catch(() => {});
      if (profileId) await request(page, "/api/models/profiles/" + profileId, "DELETE").catch(() => {});
    }
    await browser.close();
    await new Promise(resolve => fixture.close(resolve));
  }
})().catch(error => {
  console.error(error.stack || error);
  console.error("RESULT_DIR " + OUTPUT);
  process.exitCode = 1;
});
