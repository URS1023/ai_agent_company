/*
 * 中文说明（设备目录分页验收）：
 * 验证行为：设备目录按页加载且计数与后端授权范围一致，翻页不重复、不丢项。
 * 运行方式：必须显式提供 TEST5_E2E_BASE_URL（本文件没有默认地址，避免误连默认环境）+ 本机 Edge。
 * 边界：分页与计数必须来自后端；前端不得用近似值代替真实总数。
 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const puppeteer = require("puppeteer-core");

const BASE = process.env.TEST5_E2E_BASE_URL;
const PASSWORD = process.env.TEST5_E2E_PASSWORD;
const OUTPUT = path.resolve(process.env.TEST5_E2E_OUTPUT || ".implementation/ui-results/device-catalog-" + Date.now().toString(36));
const EDGE = process.env.TEST5_EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const checks = [];
const failures = [];
const seeded = [];
let token;

async function api(route, method = "GET", body) {
  const response = await fetch(BASE + route, {
    method, redirect: "error", signal: AbortSignal.timeout(10000),
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  assert(response.ok, `Isolated API ${method} ${route}: ${response.status}`);
  return response.json();
}

async function main() {
  assert.equal(BASE, "http://127.0.0.1:18080", "This fixture writes only to the dedicated isolated acceptance server");
  assert(PASSWORD, "TEST5_E2E_PASSWORD is required");
  assert.deepEqual(await api("/api/health"), { ok: true, app: "test" });
  token = (await api("/api/auth/login", "POST", { username: "workspace-admin", password: PASSWORD })).token;
  fs.mkdirSync(OUTPUT, { recursive: true });
  const prefix = "UI-PAGING-" + Date.now().toString(36);
  let browser;
  let status = "failed";
  try {
    for (let index = 0; index < 13; index++) {
      const item = (await api("/api/v1/alerts/devices", "POST", {
        code: `${prefix}-${index}`, name: `分页验收设备 ${index + 1}`, domains: ["alert", "quality"],
      })).data;
      seeded.push(item);
    }
    browser = await puppeteer.launch({ executablePath: EDGE, headless: true, args: ["--disable-gpu", "--no-first-run"] });
    const page = await browser.newPage();
    await page.setViewport({ width: 1440, height: 960 });
    await page.setRequestInterception(true);
    page.on("request", request => {
      const url = request.url();
      if (url.startsWith("data:") || url.startsWith("blob:") || new URL(url).origin === BASE) request.continue();
      else request.abort();
    });
    page.on("pageerror", error => failures.push(error.message));
    page.on("response", response => { if (response.status() >= 500) failures.push(`${response.status()} ${response.url()}`); });
    await page.goto(BASE, { waitUntil: "networkidle0" });
    await page.type("#login-username", "workspace-admin");
    await page.type("#login-password", PASSWORD);
    await page.click("#login-btn");
    await page.waitForSelector(".enterprise-area-grid");
    await page.goto(BASE + "/alerts", { waitUntil: "networkidle0" });
    const element = await page.waitForSelector("#business-module-frame");
    const frame = await element.contentFrame();
    await frame.waitForSelector(".device-search input");
    await frame.waitForFunction(() => !document.querySelector(".device-card-skeleton"));
    await frame.click(".device-search input", { clickCount: 3 });
    await frame.type(".device-search input", prefix);
    await frame.click('.device-search button[type="submit"]');
    await frame.waitForFunction(() => !document.querySelector(".device-card-skeleton") &&
      document.querySelector(".el-pagination__total")?.textContent.includes("13"));
    const ids = () => frame.$$eval("[data-device-id]", cards => cards.map(card => card.dataset.deviceId));
    const first = await ids();
    assert.equal(first.length, 12);
    assert.equal(new Set(first).size, 12);
    await frame.click(".catalog-pagination .btn-next");
    await frame.waitForFunction(() => !document.querySelector(".device-card-skeleton") &&
      document.querySelectorAll("[data-device-id]").length === 1);
    const last = await ids();
    assert(!first.includes(last[0]));
    assert.deepEqual(new Set([...first, ...last]), new Set(seeded.map(row => row.id)));
    assert(await frame.$eval(".catalog-pagination .btn-next", button => button.disabled));
    checks.push("Desktop search has exact total 13; pages contain 12 and 1 distinct real devices");
    await page.screenshot({ path: path.join(OUTPUT, "desktop-last-page.png"), fullPage: true });

    await page.setViewport({ width: 390, height: 844 });
    await frame.waitForSelector(".catalog-pagination .btn-prev");
    await frame.click(".catalog-pagination .btn-prev");
    await frame.waitForFunction(() => !document.querySelector(".device-card-skeleton") &&
      document.querySelectorAll("[data-device-id]").length === 12);
    assert.deepEqual(await ids(), first);
    await frame.click(".catalog-pagination .btn-next");
    await frame.waitForFunction(() => !document.querySelector(".device-card-skeleton") &&
      document.querySelectorAll("[data-device-id]").length === 1);
    assert.deepEqual(await ids(), last);
    const geometry = await frame.evaluate(() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth }));
    assert(geometry.scrollWidth <= geometry.width + 2, JSON.stringify(geometry));
    checks.push("390px pagination works in both directions with unchanged records and no horizontal overflow");
    await frame.$eval(".catalog-pagination", element => element.scrollIntoView({ block: "end" }));
    await page.screenshot({ path: path.join(OUTPUT, "mobile-last-page.png"), fullPage: true });
    assert.deepEqual(failures, []);
    status = "passed";
  } catch (error) {
    failures.push(error.message);
    if (browser) {
      const pages = await browser.pages();
      await pages[pages.length - 1].screenshot({ path: path.join(OUTPUT, "failure.png"), fullPage: true });
    }
    throw error;
  } finally {
    if (browser) await browser.close();
    for (const item of seeded) await api(`/api/v1/alerts/devices/${item.id}?version=${item.version}`, "DELETE");
    fs.writeFileSync(path.join(OUTPUT, "result.json"), JSON.stringify({ status, checks, failures, fixtureDevices: seeded.length,
      cleanup: "soft-deleted only devices created by this fixture", scope: "isolated server; no business database or model" }, null, 2));
  }
  console.log(JSON.stringify({ checks, failures, output: OUTPUT }));
}

main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
