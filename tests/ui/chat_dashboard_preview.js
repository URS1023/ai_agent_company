"use strict";
// 所有请求均由离线夹具响应，加载真实 Vue 构建产物，不访问运行中的服务或数据库。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const puppeteer = require("puppeteer-core");
const ROOT = path.resolve(__dirname, "../..");
const WEB = path.join(ROOT, "apps/web");
const ORIGIN = "http://dashboard-offline.invalid";
const output = path.join(ROOT, ".implementation/ui-results/chat-dashboard-preview");
const report = {
  reportName: "付款与应付情况", version: 1, refreshSeconds: 5,
  content: JSON.stringify({ canvas: { width: 1000, height: 700, backgroundColor: "#061225", backgroundImage: "", refreshSeconds: 5 }, widgets: [
    { id: "title", component: "JText", componentName: "付款与应付情况", x: 25, y: 20, w: 950, h: 70,
      config: { dataType: 1, option: { body: { text: "付款与应付情况", fontSize: 36, color: "#e5f5ff" } } } },
    { id: "amount", component: "JStatsSummary", componentName: "应付金额", x: 25, y: 120, w: 420, h: 160,
      dataStatus: "verified", config: { dataType: 3, datasourceId: "fixture-no-connection", sql: "SELECT forbidden",
        chartDataParsed: [{ name: "应付金额", value: 1200, suffix: "元" }], option: {} } },
    { id: "rows", component: "JScrollBoard", componentName: "付款记录", x: 465, y: 120, w: 500, h: 260,
      config: { dataType: 2, datasourceId: "fixture-no-http", chartDataParsed: [{ 公司: "离线示例企业", 金额: 1200 }],
        option: { header: [{ key: "公司", label: "公司" }, { key: "金额", label: "金额" }], rowNum: 4 } } },
    { id: "missing", component: "JStatsSummary", componentName: "未保存的历史指标", x: 25, y: 330, w: 420, h: 120,
      config: { dataType: 3, datasourceId: "fixture-no-connection", sql: "SELECT forbidden" } },
    { id: "chart", component: "JBar", componentName: "支付状态", x: 25, y: 475, w: 940, h: 190,
      config: { dataType: 3, datasourceId: "fixture-no-connection", sql: "SELECT forbidden" },
      echartsOption: { dataset: { source: [{ name: "已付款", value: 800 }, { name: "待付款", value: 1200 }] } } },
  ] }),
};

async function run(viewport) {
  const browser = await puppeteer.launch({ executablePath: process.env.TEST5_EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", headless: true,
    args: ["--disable-background-networking", "--disable-component-update", "--no-first-run", "--disable-sync"] });
  const page = await browser.newPage();
  const failures = [], blocked = [], calls = [];
  let reportFails = false;
  let permitted = true;
  let authFails = false;
  await page.setViewport(viewport);
  page.on("pageerror", error => failures.push(error.message));
  await page.setRequestInterception(true);
  page.on("request", request => {
    const url = new URL(request.url());
    if (url.origin !== ORIGIN) { blocked.push(url.href); void request.abort(); return; }
    if (url.pathname === "/favicon.ico") { void request.respond({ status: 204 }); return; }
    if (url.pathname.startsWith("/api/")) {
      calls.push({ path: url.pathname, method: request.method(), headers: request.headers() });
      let data, status = 200;
      if (url.pathname === "/api/auth/me") {
        data = authFails ? { message: "登录已过期，请重新登录" } : { user: { id: "fixture-user", must_change_password: false }, workspace: { id: "global-workspace" } };
        status = authFails ? 401 : 200;
      }
      else if (url.pathname === "/api/v1/platform/navigation") data = { permissions: permitted ? ["dashboards.view"] : [] };
      else if (url.pathname === "/api/v1/reports/fixture") {
        data = reportFails ? { message: "这份大屏已经删除" } : { data: report };
        status = reportFails ? 404 : 200;
      } else { blocked.push(url.pathname); data = { message: "夹具禁止业务查询" }; status = 403; }
      void request.respond({ status, contentType: "application/json", body: JSON.stringify(data) }); return;
    }
    if (url.pathname === "/fixture") {
      void request.respond({ status: 200, contentType: "text/html", body: `<!doctype html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/assets/platform/ai-workbench.css"><link rel="stylesheet" href="/assets/platform/chat-dashboard-preview.css"><style>body{margin:0;background:#f9fbff;font-family:Arial}main{max-width:1040px;margin:24px auto;padding:8px}</style></head><body class="enterprise-ai-workbench"><main id="mount"></main><script src="/assets/platform/chat-dashboard-preview.js"></script></body></html>` }); return;
    }
    const relative = url.pathname === "/analytics-app" ? "assets/analytics/index.html" : decodeURIComponent(url.pathname).replace(/^\/+/, "");
    const file = path.resolve(WEB, relative);
    if (!file.startsWith(WEB + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) { blocked.push(url.pathname); void request.respond({ status: 404, body: "未声明资源" }); return; }
    const mime = ({ ".js": "application/javascript", ".css": "text/css", ".html": "text/html", ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2" })[path.extname(file)] || "application/octet-stream";
    void request.respond({ status: 200, contentType: mime, body: fs.readFileSync(file) });
  });
  try {
    await page.goto(ORIGIN + "/fixture", { waitUntil: "networkidle0" });
    await page.evaluate(() => {
      localStorage.setItem("agent_token", "offline-dashboard-token");
      localStorage.setItem("agent_workspace_id", "global-workspace");
      const root = document.getElementById("mount");
      root.innerHTML = ChatDashboardPreview.markup({ previewPath: "/report/view/fixture?workspace_id=report-workspace", title: "付款与应付情况", version: 1 });
      ChatDashboardPreview.hydrate(root);
    });
    await page.waitForSelector('[data-preview-state="ready"]', { timeout: 25000 });
    assert.match(await page.$eval(".dashboard-preview-bar", node => node.innerText), /大屏预览 · 已保存数据/);
    assert.match(await page.$eval(".dashboard-preview-bar a", node => node.innerText), /独立打开/);
    const frame = page.frames().find(frame => frame.url().includes("analytics-app"));
    await frame.waitForFunction(() => [...document.querySelectorAll(".jimu-chart canvas")].some(canvas => canvas.width > 50 && canvas.height > 50), { timeout: 10000 });
    const painted = await frame.$eval(".jimu-chart canvas", canvas => {
      const pixels = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data;
      return pixels.some((channel, index) => index % 4 === 3 && channel > 0);
    });
    assert.equal(painted, true, "真实 ECharts 使用已保存 dataset 绘制非空画布");
    const body = await frame.$eval("body", node => node.innerText);
    assert.match(body, /付款与应付情况/); assert.match(body, /1,?200/); assert.match(body, /离线示例企业/);
    assert.match(body, /未保存预览数据/);
    assert.equal(await frame.$$eval(".dashboard-toolbar", nodes => nodes.length), 0);
    assert(calls.every(call => call.headers.authorization === "Bearer offline-dashboard-token"));
    assert(calls.every(call => call.headers["x-workspace-id"] === "report-workspace"));
    assert.equal(await page.evaluate(() => localStorage.getItem("agent_workspace_id")), "global-workspace");
    assert.equal(await page.$eval(".dashboard-preview-bar a", node => node.getAttribute("href")), "/report/view/fixture?workspace_id=report-workspace");
    assert.equal(await page.$eval(".dashboard-preview-bar a", node => node.target), "_blank");
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    const count = calls.length;
    await page.evaluate(() => { for (let i = 0; i < 5; i++) ChatDashboardPreview.hydrate(document.getElementById("mount")); });
    await new Promise(resolve => setTimeout(resolve, 5600));
    assert.equal(calls.length, count, "多次hydrate与自动刷新间隔内不重取报表或业务数据");
    fs.mkdirSync(output, { recursive: true });
    await page.screenshot({ path: path.join(output, `preview-${viewport.width}.png`) });
    await page.evaluate(() => {
      const spacer = document.createElement("div");
      spacer.style.height = "1600px";
      document.body.append(spacer);
    });
    const bounds = await page.$eval("iframe", node => { const rect = node.getBoundingClientRect(); return { x: rect.x, y: rect.y, width: rect.width, height: rect.height }; });
    await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
    await page.mouse.wheel({ deltaY: 220 });
    await new Promise(resolve => setTimeout(resolve, 350));
    assert(await page.evaluate(() => window.scrollY > 50), "鼠标在大屏内滚轮仍可滚动外层聊天");
    await page.evaluate(() => window.scrollTo(0, 0));

    reportFails = true;
    await page.evaluate(() => document.querySelector("[data-dashboard-preview-retry]").click());
    await page.waitForSelector('[data-preview-state="error"]', { timeout: 15000 });
    assert.match(await page.$eval(".dashboard-preview-feedback", node => node.innerText), /这份大屏已经删除/);
    reportFails = false;
    await page.click("[data-dashboard-preview-retry]");
    await page.waitForSelector('[data-preview-state="ready"]', { timeout: 15000 });
    permitted = false;
    await page.evaluate(() => document.querySelector("[data-dashboard-preview-retry]").click());
    await page.waitForSelector('[data-preview-state="error"]', { timeout: 5000 });
    assert.match(await page.$eval(".dashboard-preview-feedback", node => node.innerText), /没有访问此业务模块的权限/);
    permitted = true;
    authFails = true;
    await page.click("[data-dashboard-preview-retry]");
    await page.waitForSelector('[data-preview-state="error"]', { timeout: 5000 });
    assert.match(await page.$eval(".dashboard-preview-feedback", node => node.innerText), /登录/);
    assert.equal(await page.evaluate(() => ChatDashboardPreview.markup({ previewPath: "https://foreign.invalid/report/view/id" })), "");
    assert.deepEqual(blocked, []); assert.deepEqual(failures, []);
    console.log(`PASS ${viewport.width}px：自动显示真实 Vue 大屏、快照原值/缺失提示、同源鉴权/工作区、无自动查询、错误和手动重试`);
  } finally { await browser.close(); }
}
(async () => { await run({ width: 1440, height: 960 }); await run({ width: 390, height: 844 }); })().catch(error => { console.error(error); process.exitCode = 1; });
