/*
 * 中文说明（全站路由浏览器审计）：
 * 验证行为：从可见的全局搜索弹窗发现已授权模块，逐个点击其真实渲染的链接打开；
 *   历史 URL 按用户在地址栏输入的方式访问，再检查渲染后的落地页。
 * 约束：从不直接调用 JSON 接口，因此路由/权限问题不会被"接口能通"掩盖。
 *
 * Full browser-only route acceptance.
 *
 * The suite discovers authorized modules from the visible global-search dialog
 * and opens every module by clicking its rendered link.  It never calls a JSON
 * API directly.  Legacy URLs are opened as a user would enter them in the
 * address bar, then their rendered destination is inspected.
 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const puppeteer = require("puppeteer-core");

const BASE = process.env.TEST5_E2E_BASE_URL || "http://127.0.0.1:8011";
const PASSWORD = process.env.TEST5_E2E_PASSWORD;
const EDGE = process.env.TEST5_EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const PRODUCT_NAME = "智慧云脑 Cloud Brain";
const RUN = Date.now().toString(36);
const OUTPUT = path.resolve(".implementation", "ui-results", "full-routes-" + RUN);
const AXE_SOURCE = fs.readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");

const EXPECTED_MODULES = [
  "/", "/ai",
  "/assets-catalog", "/datasources", "/analytics", "/dashboards", "/templates", "/prompts",
  "/files", "/knowledge", "/ontology", "/extraction", "/graphs", "/documents",
  "/bots", "/workflows", "/skills", "/plugins", "/capabilities", "/connectors", "/applications",
  "/alerts", "/quality", "/operations", "/system/models", "/admin-secrets.html",
  "/system/users", "/system/workspaces", "/system/departments", "/system/positions", "/system/roles",
  "/system/permissions", "/system/menus", "/system/sessions", "/system/security", "/system/settings",
  "/system/operations", "/system/development",
];

const LEGACY_PATHS = [
  "/app", "/data-query", "/analytics/sources", "/report", "/runs",
  "/admin-ops.html", "/admin-audit.html", "/admin-models.html", "/system/api",
];

const ERROR_TEXT = [
  /404\s*错误/i, /找不到网页/, /页面不存在/, /没有登记这个业务页面/,
  /模块(?:加载|初始化)失败/, /服务处理失败/, /系统接口\s*500/, /Internal Server Error/i,
  /Traceback \(most recent call last\)/,
];

function safeName(value) {
  return String(value || "root").replace(/^\/+/, "").replace(/[^a-zA-Z0-9_-]+/g, "-") || "root";
}

async function login(page) {
  const response = await page.goto(BASE + "/", { waitUntil: "networkidle2", timeout: 90000 });
  assert.equal(response.status(), 200, "login page");
  const login = await page.$("#platform-login-form");
  if (login) {
    await page.click("#login-username");
    await page.keyboard.down("Control");
    await page.keyboard.press("KeyA");
    await page.keyboard.up("Control");
    await page.type("#login-username", "workspace-admin");
    await page.type("#login-password", PASSWORD);
    await page.click("#login-btn");
  }
  await page.waitForSelector(".enterprise-header", { visible: true, timeout: 30000 });
  await page.waitForFunction(() => !document.querySelector("#main-content")?.innerText.includes("正在读取业务数据"));
}

async function openSearch(page) {
  await page.keyboard.down("Control");
  await page.keyboard.press("KeyK");
  await page.keyboard.up("Control");
  await page.waitForSelector(".enterprise-command-dialog[open] .enterprise-search-results a", { visible: true, timeout: 15000 });
}

async function discoverModules(page) {
  await openSearch(page);
  const entries = await page.$$eval(".enterprise-search-results a", (links) => links.map((link) => ({
    href: link.getAttribute("href"),
    label: link.textContent.trim().replace(/\s+/g, " "),
  })));
  await page.keyboard.press("Escape");
  return entries.filter((entry, index, all) => entry.href && all.findIndex((item) => item.href === entry.href) === index);
}

async function waitForRenderedPage(page) {
  await page.waitForFunction(() => {
    const platform = document.querySelector("#main-content");
    const legacy = document.querySelector("#app, #composer-form, [id^=content-admin-]");
    if (!platform && !legacy) return false;
    const text = (platform || legacy).innerText || document.body.innerText;
    return !/正在读取业务数据|正在打开数字大屏/.test(text);
  }, { timeout: 45000 });
  const frameElement = await page.$("#business-module-frame");
  if (!frameElement) return null;
  const frame = await frameElement.contentFrame();
  assert(frame, "embedded business frame is unavailable");
  await frame.waitForFunction(() => {
    const root = document.querySelector("#analytics-app");
    return root && root.children.length > 0 && !/正在打开|正在加载/.test(root.innerText);
  }, { timeout: 45000 });
  return frame;
}

async function clickSearchEntry(page, entry) {
  await openSearch(page);
  const links = await page.$$(".enterprise-search-results a");
  let selected = null;
  for (const link of links) {
    const href = await link.evaluate((element) => element.getAttribute("href"));
    if (href === entry.href) { selected = link; break; }
  }
  assert(selected, "global-search entry disappeared: " + entry.href);
  await Promise.all([
    page.waitForNavigation({ waitUntil: "domcontentloaded", timeout: 45000 }),
    selected.click(),
  ]);
  return waitForRenderedPage(page);
}

async function documentAudit(target) {
  await target.addScriptTag({ content: AXE_SOURCE });
  return target.evaluate(async () => {
    const bodyText = document.body.innerText;
    const visible = (element) => Boolean(element.getClientRects().length);
    const unnamedButtons = [...document.querySelectorAll("button")].filter((button) => visible(button)
      && !(button.getAttribute("aria-label") || button.getAttribute("title") || button.textContent || "").trim()).length;
    const unlabeledControls = [...document.querySelectorAll("input:not([type=hidden]), textarea, select")].filter((control) => visible(control)
      && !control.labels?.length && !control.getAttribute("aria-label") && !control.getAttribute("aria-labelledby")
      && !control.getAttribute("title") && !control.getAttribute("placeholder")).length;
    const axeResult = await axe.run(document, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] },
    });
    return {
      documentTitle: document.title,
      visibleLegacyBrands: [...new Set(bodyText.match(/(?:qKnow|Qknow|Test5|Lynx|企业智能平台|企业\s*AI\s*工作平台)/gi) || [])],
      title: document.querySelector("h1")?.textContent.trim()
        || document.querySelector(".page-heading h1")?.textContent.trim()
        || document.title,
      bodyText: bodyText.slice(0, 50000),
      horizontalOverflow: Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth),
      unnamedButtons,
      unlabeledControls,
      seriousA11y: axeResult.violations.filter((item) => ["serious", "critical"].includes(item.impact)).map((item) => ({
        id: item.id, impact: item.impact, nodeCount: item.nodes.length, help: item.help,
        nodes: item.nodes.slice(0, 10).map((node) => ({ target: node.target, summary: node.failureSummary })),
      })),
    };
  });
}

async function inspect(page, frame, route, requestFailures, runtimeErrors, options = {}) {
  const parent = await documentAudit(page);
  const child = frame ? await documentAudit(frame) : null;
  const combinedText = parent.bodyText + "\n" + (child?.bodyText || "");
  const visibleErrors = options.allowMissing
    ? []
    : ERROR_TEXT.filter((pattern) => pattern.test(combinedText)).map((pattern) => pattern.source);
  const result = {
    route,
    finalPath: new URL(page.url()).pathname + new URL(page.url()).search,
    title: child?.title || parent.title,
    embedded: Boolean(frame),
    requestFailures: [...requestFailures],
    runtimeErrors: [...runtimeErrors],
    visibleErrors,
    brand: {
      titleUsesProductName: parent.documentTitle.includes(PRODUCT_NAME),
      legacyNames: [...new Set([...parent.visibleLegacyBrands, ...(child?.visibleLegacyBrands || [])])],
    },
    horizontalOverflow: { parent: parent.horizontalOverflow, child: child?.horizontalOverflow || 0 },
    unnamedButtons: parent.unnamedButtons + (child?.unnamedButtons || 0),
    unlabeledControls: parent.unlabeledControls + (child?.unlabeledControls || 0),
    seriousA11y: [...parent.seriousA11y, ...(child?.seriousA11y || [])],
  };
  await page.screenshot({ path: path.join(OUTPUT, safeName(route) + ".jpg"), type: "jpeg", quality: 72 });
  return result;
}

(async () => {
  assert(PASSWORD, "TEST5_E2E_PASSWORD is required");
  const origin = new URL(BASE);
  const lanReadOnly = process.env.TEST5_E2E_ALLOW_LAN_READONLY === "1"
    && origin.protocol === "https:" && origin.hostname === "192.168.6.16";
  assert(["127.0.0.1", "localhost"].includes(origin.hostname) || lanReadOnly, "Acceptance requires localhost or the explicitly enabled LAN HTTPS deployment");
  fs.mkdirSync(OUTPUT, { recursive: true });

  const browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: "new",
    args: ["--no-sandbox", "--disable-gpu", "--window-size=1440,940"],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 940 });

  let requestFailures = [];
  let runtimeErrors = [];
  page.on("response", (response) => {
    if (response.status() >= 400) requestFailures.push({ status: response.status(), method: response.request().method(), url: response.url() });
  });
  page.on("pageerror", (error) => runtimeErrors.push("pageerror: " + error.message));
  page.on("console", (message) => { if (message.type() === "error") runtimeErrors.push("console: " + message.text()); });

  await login(page);
  const discovered = await discoverModules(page);
  const discoveredPaths = discovered.map((entry) => entry.href);
  const missingFromNavigation = EXPECTED_MODULES.filter((route) => route !== "/" && !discoveredPaths.includes(route));
  const results = [];

  requestFailures = []; runtimeErrors = [];
  await Promise.all([
    page.waitForNavigation({ waitUntil: "domcontentloaded", timeout: 45000 }),
    page.click(".enterprise-brand"),
  ]).catch(() => {});
  const homeFrame = await waitForRenderedPage(page);
  results.push(await inspect(page, homeFrame, "/", requestFailures, runtimeErrors));

  for (const entry of discovered) {
    requestFailures = []; runtimeErrors = [];
    try {
      const frame = await clickSearchEntry(page, entry);
      results.push(await inspect(page, frame, entry.href, requestFailures, runtimeErrors));
      console.log("PASS route " + entry.href + " · " + entry.label);
    } catch (error) {
      results.push({ route: entry.href, fatal: error.stack || error.message, requestFailures, runtimeErrors });
      console.error("FAIL route " + entry.href + " · " + error.message);
      await login(page).catch(() => {});
    }
  }

  for (const route of LEGACY_PATHS) {
    requestFailures = []; runtimeErrors = [];
    try {
      const response = await page.goto(BASE + route, { waitUntil: "domcontentloaded", timeout: 45000 });
      assert(response && response.status() < 400, route + " returned " + response?.status());
      const frame = await waitForRenderedPage(page);
      results.push(await inspect(page, frame, route, requestFailures, runtimeErrors));
      console.log("PASS legacy " + route);
    } catch (error) {
      results.push({ route, fatal: error.stack || error.message, requestFailures, runtimeErrors });
      console.error("FAIL legacy " + route + " · " + error.message);
      await login(page).catch(() => {});
    }
  }

  const mobile = await browser.newPage();
  await mobile.setViewport({ width: 390, height: 844, isMobile: true });
  await login(mobile);
  const mobileResults = [];
  for (const route of ["/", "/ai", "/dashboards", "/workflows", "/alerts", "/quality", "/system/users"]) {
    const response = await mobile.goto(BASE + route, { waitUntil: "domcontentloaded", timeout: 45000 });
    assert(response && response.status() < 400, route + " mobile navigation");
    const frame = await waitForRenderedPage(mobile);
    const parent = await documentAudit(mobile);
    const child = frame ? await documentAudit(frame) : null;
    mobileResults.push({ route, parentOverflow: parent.horizontalOverflow, childOverflow: child?.horizontalOverflow || 0,
      titleUsesProductName: parent.documentTitle.includes(PRODUCT_NAME),
      legacyNames: [...new Set([...parent.visibleLegacyBrands, ...(child?.visibleLegacyBrands || [])])],
      seriousA11y: [...parent.seriousA11y, ...(child?.seriousA11y || [])] });
  }
  await mobile.close();
  await browser.close();

  const failures = results.filter((result) => result.fatal || result.visibleErrors?.length || result.requestFailures?.length
    || result.runtimeErrors?.length || !result.brand?.titleUsesProductName || result.brand?.legacyNames?.length
    || result.horizontalOverflow?.parent || result.horizontalOverflow?.child
    || result.unnamedButtons || result.unlabeledControls || result.seriousA11y?.length);
  const mobileFailures = mobileResults.filter((result) => !result.titleUsesProductName || result.legacyNames.length
    || result.parentOverflow || result.childOverflow || result.seriousA11y.length);
  const report = {
    baseUrl: BASE,
    discoveredModules: discovered,
    expectedModuleCount: EXPECTED_MODULES.length,
    missingFromNavigation,
    results,
    mobileResults,
    summary: {
      discovered: discovered.length,
      inspected: results.length,
      routeFailures: failures.length,
      mobileFailures: mobileFailures.length,
    },
  };
  fs.writeFileSync(path.join(OUTPUT, "result.json"), JSON.stringify(report, null, 2));
  console.log("Evidence " + OUTPUT);
  console.log(JSON.stringify(report.summary));
  if (missingFromNavigation.length || failures.length || mobileFailures.length) process.exitCode = 1;
})().catch((error) => {
  console.error(error.stack || error.message);
  process.exit(1);
});
