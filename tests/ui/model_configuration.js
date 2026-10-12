/*
 * 中文说明（模型治理页面与视觉模型探测的浏览器验收）：
 * 验证行为：模型配置页按角色正确展示，并用一次真实的视觉模型探测验证连通性。
 * 运行方式：需一次性可废弃的隔离服务 + 本机 Edge（文件内自带本地 http 桩作为提供方）。
 * 边界：探测会真实调用外部模型（有计量），请勿在生产配置上执行。
 */
"use strict";

/* Disposable Edge acceptance for the canonical model-governance page and a real vision probe. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const puppeteer = require("puppeteer-core");

const BASE_URL = process.env.TEST5_E2E_BASE_URL || "http://127.0.0.1:8027";
const PASSWORD = process.env.TEST5_E2E_PASSWORD;
const EDGE = process.env.TEST5_EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const OUTPUT = path.resolve(".implementation/ui-results/model-configuration-" + Date.now().toString(36));

async function login(page) {
  await page.goto(BASE_URL + "/", { waitUntil: "networkidle0", timeout: 60000 });
  await page.type("#login-username", "workspace-admin");
  await page.type("#login-password", PASSWORD);
  await page.click("#login-btn");
  await page.waitForSelector(".enterprise-area-grid", { timeout: 30000 });
}

async function platformApi(page, route, options = {}) {
  return page.evaluate(async ({ route, options }) => {
    const response = await fetch(route, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        Authorization: "Bearer " + localStorage.getItem("agent_token"),
        "X-Workspace-Id": localStorage.getItem("agent_workspace_id"),
        ...(options.headers || {}),
      },
    });
    return { status: response.status, data: await response.json().catch(() => ({})) };
  }, { route, options });
}

(async () => {
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, "isolated", "Model configuration must use an isolated server");
  assert(PASSWORD, "Supply TEST5_E2E_PASSWORD");
  assert(["127.0.0.1", "localhost"].includes(new URL(BASE_URL).hostname));
  assert.equal((await (await fetch(BASE_URL + "/api/health")).json()).app, "test");
  fs.mkdirSync(OUTPUT, { recursive: true });

  const probeRequests = [];
  const fixture = http.createServer((request, response) => {
    const chunks = [];
    request.on("data", chunk => chunks.push(chunk));
    request.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf8");
      probeRequests.push({ url: request.url, raw });
      if (!request.url.endsWith("/chat/completions")) {
        response.writeHead(404, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: { message: "fixture endpoint not found" } }));
        return;
      }
      const payload = JSON.parse(raw || "{}");
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({
        id: "vision-probe-e2e",
        object: "chat.completion",
        created: Math.floor(Date.now() / 1000),
        model: payload.model || "local-vision-e2e",
        choices: [{
          index: 0,
          message: { role: "assistant", content: "测试图表包含 42.5M 总量与 18% 增长率。" },
          finish_reason: "stop",
        }],
        usage: { prompt_tokens: 10, completion_tokens: 8, total_tokens: 18 },
      }));
    });
  });
  await new Promise(resolve => fixture.listen(0, "127.0.0.1", resolve));

  let browser;
  let page;
  let profileId = null;
  const errors = [];
  try {
    browser = await puppeteer.launch({
      executablePath: EDGE,
      headless: true,
      args: ["--no-first-run", "--window-size=1440,940"],
    });
    page = await browser.newPage();
    await page.setViewport({ width: 1440, height: 940 });
    page.on("pageerror", error => errors.push("pageerror " + error.message));
    page.on("console", message => { if (message.type() === "error") errors.push("console " + message.text()); });
    page.on("response", response => { if (response.status() >= 500) errors.push(response.status() + " " + response.url()); });
    await login(page);

    const legacyResponse = await page.goto(BASE_URL + "/admin-models.html", { waitUntil: "networkidle0", timeout: 60000 });
    assert.equal(legacyResponse.status(), 200);
    await page.waitForSelector('[data-edit-model="vision"]', { timeout: 30000 });
    assert.equal(new URL(page.url()).pathname, "/system/models", "legacy model URL must redirect to the canonical page");
    const overview = await page.evaluate(() => ({
      heading: document.querySelector(".page-heading h1")?.textContent.trim(),
      tabs: Array.from(document.querySelectorAll(".system-tool-tabs a")).map(item => item.textContent.trim()),
      roles: Array.from(document.querySelectorAll("[data-edit-model]")).map(item => item.dataset.editModel),
      bodyWidth: document.documentElement.scrollWidth,
      viewport: innerWidth,
      containsSecret: document.querySelector("#main-content").innerText.includes("sk-"),
    }));
    assert.equal(overview.heading, "模型与用量");
    assert.deepEqual(overview.tabs, ["模型配置", "模型市场", "用量与成本"]);
    assert(overview.roles.includes("vision") && overview.roles.includes("reasoning") && overview.roles.includes("embedding"));
    assert(overview.bodyWidth <= overview.viewport, "desktop model page overflows");
    assert.equal(overview.containsSecret, false, "raw API key leaked into the page");
    await page.screenshot({ path: path.join(OUTPUT, "model-overview.png"), fullPage: true });

    await page.click('[data-edit-model="vision"]');
    await page.waitForSelector("#platform-dialog[open] [name=endpoint]");
    const editor = await page.evaluate(() => ({
      role: document.querySelector("#platform-dialog [name=profile_name]")?.value,
      key: document.querySelector("#platform-dialog [name=api_key]")?.value,
      keyHint: document.querySelector("#platform-dialog [name=api_key]")?.closest("label")?.innerText,
    }));
    assert.equal(editor.role, "vision");
    assert.equal(editor.key, "");
    assert(editor.keyHint.includes("密钥") && !editor.keyHint.includes("sk-"));

    const fixtureEndpoint = "http://127.0.0.1:" + fixture.address().port + "/v1";
    const values = {
      provider: "local-compatible",
      model: "local-vision-e2e",
      endpoint: fixtureEndpoint,
      api_key: "local-e2e-key",
      capabilities: "vision\nocr\ncharts\ndocuments",
      input_price: "10",
      output_price: "20",
    };
    for (const [name, value] of Object.entries(values)) {
      await page.$eval(`#platform-dialog [name="${name}"]`, (element, next) => {
        element.value = next;
        element.dispatchEvent(new Event("input", { bubbles: true }));
      }, value);
    }
    await page.click("#platform-dialog [type=submit]");
    await page.waitForFunction(() => !document.querySelector("#platform-dialog").open, { timeout: 30000 });
    await page.waitForSelector('[data-probe-model="vision"]');

    const profiles = await platformApi(page, "/api/models/profiles");
    assert.equal(profiles.status, 200);
    const configured = profiles.data.items.find(item => item.profile_name === "vision");
    assert(configured && configured.source === "database");
    assert.equal(configured.endpoint, fixtureEndpoint);
    assert.equal(configured.model, "local-vision-e2e");
    assert.equal(configured.api_key_configured, true);
    assert.equal(configured.api_key, undefined, "profile API must not return the raw key");
    assert.equal(configured.pricing.configured, true);
    profileId = configured.id;

    const probeResponse = page.waitForResponse(response => response.url().endsWith("/api/models/probe/vision") && response.request().method() === "POST");
    await page.click('[data-probe-model="vision"]');
    const probe = await (await probeResponse).json();
    assert.equal(probe.status, "passed");
    assert.equal(probe.details.probe_type, "vision");
    assert(probe.details.reply.includes("42.5M") && probe.details.reply.includes("18%"));
    await page.waitForFunction(() => document.querySelector("#platform-toast")?.textContent.includes("模型能力测试通过"));
    await page.waitForFunction(() => document.querySelector('[data-edit-model="vision"]')?.closest("tr")?.innerText.includes("通过"));
    assert.equal(probeRequests.length, 1);
    assert(probeRequests[0].url.endsWith("/chat/completions"));
    assert(probeRequests[0].raw.includes("data:image/png") && probeRequests[0].raw.includes("image_url"));

    const usage = await platformApi(page, "/api/models/usage?profile_name=vision");
    assert.equal(usage.status, 200);
    assert(usage.data.totals.calls >= 1 && usage.data.totals.total_tokens >= 18);
    assert.equal(usage.data.totals.unpriced_calls, 0);
    assert(Number(usage.data.totals.costs.CNY) > 0);
    await page.screenshot({ path: path.join(OUTPUT, "vision-probe.png"), fullPage: true });

    await page.setViewport({ width: 390, height: 844, isMobile: true, deviceScaleFactor: 1 });
    await page.goto(BASE_URL + "/system/models?tab=usage", { waitUntil: "networkidle0", timeout: 60000 });
    await page.waitForSelector(".governance-usage-layout");
    const mobile = await page.evaluate(() => ({
      overflow: document.documentElement.scrollWidth - innerWidth,
      statusStrips: document.querySelectorAll(".system-status-strip").length,
      tabs: document.querySelectorAll(".system-tool-tabs a").length,
      charts: document.querySelectorAll(".usage-chart").length,
    }));
    assert(mobile.overflow <= 1, "mobile model page overflows");
    assert.deepEqual([mobile.statusStrips, mobile.tabs, mobile.charts], [1, 3, 1]);
    await page.screenshot({ path: path.join(OUTPUT, "model-usage-mobile.png"), fullPage: true });

    const disabled = await platformApi(page, "/api/models/profiles/" + encodeURIComponent(profileId), { method: "DELETE" });
    assert.equal(disabled.status, 200);
    profileId = null;
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(OUTPUT, "result.json"), JSON.stringify({
      ok: true, overview, probe, usage: usage.data.totals, mobile, errors,
    }, null, 2));
    console.log("PASS canonical model governance, encrypted configuration, real vision probe and usage accounting");
    console.log("RESULT_DIR " + OUTPUT);
  } finally {
    if (profileId && page && !page.isClosed()) {
      await platformApi(page, "/api/models/profiles/" + encodeURIComponent(profileId), { method: "DELETE" }).catch(() => {});
    }
    if (browser) await browser.close();
    await new Promise(resolve => fixture.close(resolve));
  }
})().catch(error => {
  console.error(error.stack || error.message);
  process.exit(1);
});
