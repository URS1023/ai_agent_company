/*
 * 中文说明（质量目录提示的真实浏览器渲染验证）：
 * 验证行为：在隔离应用上渲染质量目录页面，核对目录缺失/待处理等提示文案与状态。
 * 约束：目录响应来自显式夹具，不执行任何已保存工作流或业务 SQL。
 * 运行方式：需隔离应用 + 本机浏览器。
 */
"use strict";
// Real browser/rendering against the isolated app; catalogue responses are
// explicit fixtures. This does not execute any saved workflow or business SQL.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const puppeteer = require("puppeteer-core");
const BASE = "http://127.0.0.1:18080";
const OUTPUT = path.resolve(process.env.TEST5_E2E_OUTPUT || ".implementation/ui-results/quality-catalog-" + Date.now());

async function main() {
  const health = await fetch(BASE + "/api/health").then(r => r.json());
  assert.deepEqual(health, { ok: true, app: "test" });
  assert(process.env.TEST5_E2E_PASSWORD);
  fs.mkdirSync(OUTPUT, { recursive: true });
  const catalogue = JSON.parse(fs.readFileSync("configurations/quality_catalog.json", "utf8"));
  const failures = [], checks = [];
  let mode = "admin";
  const browser = await puppeteer.launch({ executablePath: "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", headless: true });
  let status = "failed";
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1440, height: 960 });
    await page.setRequestInterception(true);
    page.on("pageerror", error => failures.push(error.message));
    page.on("request", request => {
      const url = new URL(request.url());
      if (url.origin !== BASE && !["data:", "blob:"].includes(url.protocol)) return request.abort();
      if (url.pathname === "/api/v1/quality/products") {
        if (mode === "error") return request.respond({ status: 503, contentType: "application/json", body: JSON.stringify({ code: 'quality_catalog_invalid', message: '产品目录配置无法读取，请联系管理员检查目录后重试。' }) });
        const data = { ...catalogue, entries: [], pendingEntries: mode === "admin" ? catalogue.pendingEntries : [],
          issues: mode === "admin" ? [{ code: "workflow_unavailable", count: 12, message: "12 个产品流程暂不可用，请检查流程是否已导入当前工作区及其授权。" }] : [] };
        if (mode === "available") data.entries = catalogue.entries.map(e => ({ ...e, workflowName: e.templateName, status: "published", publishedRevision: 1 }));
        return request.respond({ status: 200, contentType: "application/json", body: JSON.stringify({ data }) });
      }
      return request.continue();
    });
    await page.goto(BASE, { waitUntil: "networkidle0" });
    await page.type("#login-username", "workspace-admin");
    await page.type("#login-password", process.env.TEST5_E2E_PASSWORD);
    await page.click("#login-btn");
    await page.waitForSelector(".enterprise-area-grid");
    await page.goto(BASE + "/quality", { waitUntil: "networkidle0" });
    const frame = await (await page.waitForSelector("#business-module-frame")).contentFrame();
    await frame.waitForSelector(".catalogue-notice");
    const notice = await frame.$eval(".catalogue-notice", e => e.textContent);
    assert(notice.includes("12 个产品流程暂不可用"));
    assert(notice.includes("2 张历史单据") && notice.includes("模板检验类别缺失"));
    checks.push("Administrator sees missing workflows and retained ambiguous source records");
    await page.screenshot({ path: path.join(OUTPUT, "desktop-notice.png"), fullPage: true });
    await page.setViewport({ width: 390, height: 844 });
    const geometry = await frame.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth }));
    assert(geometry.scroll <= geometry.width + 2, JSON.stringify(geometry));
    await page.screenshot({ path: path.join(OUTPUT, "mobile-notice.png"), fullPage: true });
    checks.push("390px notice wraps without horizontal overflow");
    mode = "available";
    await frame.evaluate(() => [...document.querySelectorAll("button")].find(e => e.textContent.trim() === "刷新").click());
    await frame.waitForFunction(() => document.querySelectorAll("[data-product-id]").length === 6);
    assert.equal(await frame.$(".catalogue-notice"), null);
    checks.push("Available workflows render six product cards and remove stale diagnostics on refresh");
    mode = "reader";
    await frame.evaluate(() => [...document.querySelectorAll("button")].find(e => e.textContent.trim() === "刷新").click());
    await frame.waitForFunction(() => document.querySelector(".quality-feedback")?.textContent.includes("尚未配置产品工作流"));
    assert.equal(await frame.$(".catalogue-notice"), null);
    checks.push("Ordinary response does not display administrative diagnostics");
    mode = "error";
    await page.goto(BASE + '/quality', { waitUntil: 'networkidle0' });
    const errorFrame = await (await page.waitForSelector('#business-module-frame')).contentFrame();
    await errorFrame.waitForSelector('.concession-products-error');
    assert((await errorFrame.$eval('.concession-products-error', e => e.textContent)).includes('产品目录加载失败'));
    mode = 'available';
    await errorFrame.click('.concession-products-error button');
    await errorFrame.waitForFunction(() => !document.querySelector('.concession-products-error'));
    checks.push('Concession product selector reports catalogue failure and recovers on explicit retry');
    assert.deepEqual(failures, []);
    status = "passed";
  } catch (error) {
    failures.push(error.message);
    throw error;
  } finally {
    await browser.close();
    fs.writeFileSync(path.join(OUTPUT, "result.json"), JSON.stringify({ status, checks, failures, catalogueResponses: "explicit browser fixtures", businessQueries: 0 }, null, 2));
    console.log(JSON.stringify({ status, checks, failures, output: OUTPUT }));
  }
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
