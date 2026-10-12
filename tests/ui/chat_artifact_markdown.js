/*
 * 中文说明（聊天产物与 markdown 渲染的真实浏览器验收）：
 * 验证行为：生成产物在对话中以鉴权后的预览/下载地址渲染，markdown 中的图片/链接不会暴露
 *   沙箱路径或未授权地址。
 * 运行方式：需隔离服务（TEST5_E2E_BASE_URL，默认 8011）+ 本机 Edge + TEST5_E2E_PASSWORD，
 *   并用 TEST5_E2E_ARTIFACT_ID 指定已存在的产物；结果写入 .implementation/ui-results/chat-artifact-<run>/。
 * 边界：只读校验既有产物；缺少产物 id 时用例无法运行。
 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const puppeteer = require("puppeteer-core");

const BASE = process.env.TEST5_E2E_BASE_URL || "http://127.0.0.1:8011";
const PASSWORD = process.env.TEST5_E2E_PASSWORD;
const ARTIFACT_ID = process.env.TEST5_E2E_ARTIFACT_ID;
const EDGE = process.env.TEST5_EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const OUTPUT = path.resolve(".implementation/ui-results/chat-artifact-" + Date.now().toString(36));
const failures = [];
const requests = [];
let browser;
let page;

async function audit() {
  await page.addScriptTag({ path: require.resolve("axe-core/axe.min.js") });
  return page.evaluate(async () => {
    const result = await axe.run(document.querySelector("#messages"), {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] },
    });
    return result.violations.map((item) => ({
      id: item.id,
      impact: item.impact,
      targets: item.nodes.map((node) => node.target),
    }));
  });
}

(async () => {
  assert(PASSWORD, "Supply TEST5_E2E_PASSWORD for the isolated preview");
  assert(ARTIFACT_ID, "Supply TEST5_E2E_ARTIFACT_ID from seed_chat_artifact.py");
  const health = await fetch(BASE + "/api/health").then((response) => response.json());
  assert.equal(health.app, "test", "Refusing to run artifact acceptance outside an isolated test server");
  fs.mkdirSync(OUTPUT, { recursive: true });

  browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: true,
    args: ["--no-sandbox", "--disable-gpu", "--window-size=1440,960"],
  });
  page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 960 });
  page.on("pageerror", (error) => failures.push("pageerror: " + error.message));
  page.on("request", (request) => requests.push(request.url()));
  page.on("response", (response) => {
    if (response.status() >= 400) failures.push(response.status() + " " + response.url());
  });

  await page.goto(BASE + "/", { waitUntil: "networkidle0", timeout: 60000 });
  await page.type("#login-username", "workspace-admin");
  await page.type("#login-password", PASSWORD);
  await page.click("#login-btn");
  await page.waitForSelector(".enterprise-area-grid");
  await page.goto(BASE + "/ai", { waitUntil: "networkidle0", timeout: 60000 });
  await page.waitForSelector("#messages");

  await page.evaluate((artifactId) => {
    const artifact = {
      id: artifactId,
      type: "chart",
      name: "north_china_analysis.png",
      mime_type: "image/png",
      size: 4096,
      preview_url: "/api/artifacts/" + artifactId + "/content",
      download_url: "/api/artifacts/" + artifactId + "/download",
    };
    const parts = [
      {
        type: "text",
        text: "## 华北地区销售分析\n\n图表来自本次 Python 分析结果。\n\n![华北地区销售趋势](/mnt/data/north_china_analysis.png)",
      },
      { type: "artifact", artifact },
    ];
    const messages = document.querySelector("#messages");
    messages.innerHTML = "";
    messages.appendChild(assistantRow(parts, ""));
  }, ARTIFACT_ID);

  await page.waitForSelector(".md-image-figure img[data-artifact-image]");
  await page.$eval(".md-image-figure", (node) => node.scrollIntoView({ block: "center" }));
  await page.waitForFunction(() => {
    const image = document.querySelector(".md-image-figure img[data-artifact-image]");
    return image && image.complete && image.naturalWidth > 0;
  }, { timeout: 15000 });

  const desktop = await page.evaluate((artifactId) => {
    const figure = document.querySelector(".md-image-figure");
    const image = figure.querySelector("img");
    const download = figure.querySelector("figcaption a");
    const source = new URL(image.src);
    const downloadUrl = new URL(download.href);
    return {
      figures: document.querySelectorAll(".md-image-figure").length,
      duplicateCards: document.querySelectorAll(".artifact-result").length,
      nestedInParagraph: Boolean(figure.closest("p")),
      naturalWidth: image.naturalWidth,
      naturalHeight: image.naturalHeight,
      alt: image.alt,
      contentPath: source.pathname,
      tokenCount: source.searchParams.getAll("token").length,
      workspaceCount: source.searchParams.getAll("workspace_id").length,
      downloadPath: downloadUrl.pathname,
      artifactId: figure.dataset.artifactId,
      errorFallback: Boolean(figure.querySelector(".artifact-image-error")),
    };
  }, ARTIFACT_ID);
  assert.equal(desktop.figures, 1);
  assert.equal(desktop.duplicateCards, 0, "Markdown preview and artifact card must not duplicate the same image");
  assert.equal(desktop.nestedInParagraph, false, "Figure must use valid standalone HTML structure");
  assert(desktop.naturalWidth > 0 && desktop.naturalHeight > 0);
  assert.equal(desktop.alt, "华北地区销售趋势");
  assert.equal(desktop.contentPath, "/api/artifacts/" + ARTIFACT_ID + "/content");
  assert.equal(desktop.downloadPath, "/api/artifacts/" + ARTIFACT_ID + "/download");
  assert.equal(desktop.tokenCount, 1);
  assert.equal(desktop.workspaceCount, 1);
  assert.equal(desktop.artifactId, ARTIFACT_ID);
  assert.equal(desktop.errorFallback, false);
  assert.equal(requests.some((url) => url.includes("/mnt/data/")), false, "Browser must never request sandbox-only paths");
  await page.screenshot({ path: path.join(OUTPUT, "chat-artifact-desktop.png"), fullPage: false });

  await page.evaluate(() => {
    document.querySelector("#messages").appendChild(assistantRow([
      { type: "text", text: "![缺失图片](/mnt/data/not-persisted.png)" },
    ], ""));
  });
  assert.equal(await page.$eval(".md-image-missing", (node) => node.textContent.trim()), "图片附件不可用：not-persisted.png");
  assert.equal(requests.some((url) => url.includes("not-persisted.png")), false);

  const violations = await audit();
  assert.deepEqual(violations, []);
  await page.evaluate(() => document.querySelector("#messages .msg-row:last-child")?.remove());

  await page.setViewport({ width: 390, height: 844 });
  await new Promise((resolve) => setTimeout(resolve, 350));
  const mobile = await page.evaluate(() => {
    const figure = document.querySelector(".md-image-figure");
    const rect = figure.getBoundingClientRect();
    return {
      overflow: document.documentElement.scrollWidth - innerWidth,
      left: rect.left,
      right: rect.right,
      width: rect.width,
      imageWidth: figure.querySelector("img").getBoundingClientRect().width,
    };
  });
  assert(mobile.overflow <= 1);
  assert(mobile.left >= 0 && mobile.right <= 390);
  assert(mobile.imageWidth <= mobile.width + 1);
  await page.screenshot({ path: path.join(OUTPUT, "chat-artifact-mobile.png"), fullPage: false });

  assert.deepEqual(failures, []);
  fs.writeFileSync(path.join(OUTPUT, "result.json"), JSON.stringify({ desktop, mobile, failures, violations }, null, 2));
  console.log("PASS Markdown image resolves to one authenticated artifact preview");
  console.log("PASS Missing sandbox image degrades without a 404 request");
  console.log("PASS Desktop and 390px mobile layouts have no overflow");
  console.log("RESULT_DIR " + OUTPUT);
})().catch(async (error) => {
  console.error(error.stack || error.message);
  if (page && !page.isClosed()) await page.screenshot({ path: path.join(OUTPUT, "failure.png"), fullPage: true }).catch(() => {});
  process.exitCode = 1;
}).finally(async () => {
  if (browser) await browser.close();
});
