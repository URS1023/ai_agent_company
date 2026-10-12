/*
 * 中文说明（客户体验与可访问性审计）：
 * 验证行为：真实浏览器中登录遍历主要页面，用 axe-core 做可访问性扫描并检查关键体验项，
 *   输出违规清单作为审计证据。
 * 运行方式：需隔离服务（TEST5_E2E_BASE_URL，默认 8011）+ 本机 Edge，口令默认 admin123
 *   （TEST5_E2E_PASSWORD 覆盖）；axe-core 从依赖解析加载（需先 npm install）。
 * 边界：审计不改数据；违规项按严重级别记录，不因存在违规而中断后续页面。
 */
"use strict";
const puppeteer = require("puppeteer-core");
const fs = require("fs");
const path = require("path");

const EDGE = process.env.TEST5_EDGE_PATH || "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const BASE_URL = process.env.TEST5_E2E_BASE_URL || "http://127.0.0.1:8011";
const PASSWORD = process.env.TEST5_E2E_PASSWORD || "admin123";
const AXE_SOURCE = fs.readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");

async function login(page) {
  await page.goto(BASE_URL + "/", { waitUntil: "networkidle2", timeout: 60000 });
  const loginVisible = await page.$eval("#login-username", (element) => Boolean(element.getClientRects().length)).catch(() => false);
  if (loginVisible) {
    await page.type("#login-username", "workspace-admin");
    await page.type("#login-password", PASSWORD);
    await page.click("#login-btn");
    await page.waitForSelector(".enterprise-area-grid", { timeout: 20000 });
  }
}

async function auditRoute(page, route, selector) {
  await page.goto(BASE_URL + route, { waitUntil: "networkidle2", timeout: 60000 });
  await page.waitForSelector(selector, { timeout: 30000 });
  await page.evaluate(AXE_SOURCE);
  return await page.evaluate(async (routeValue) => {
    const result = await axe.run(document, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] },
    });
    const unnamedButtons = Array.from(document.querySelectorAll("button")).filter((button) => {
      const name = (button.getAttribute("aria-label") || button.getAttribute("title") || button.textContent || "").trim();
      return !name;
    }).length;
    const controlsWithoutLabel = Array.from(document.querySelectorAll("input:not([type=hidden]), textarea, select")).filter((control) => {
      return !control.labels?.length && !control.getAttribute("aria-label") && !control.getAttribute("aria-labelledby") && !control.getAttribute("title") && !control.getAttribute("placeholder");
    }).length;
    return {
      route: routeValue,
      title: document.querySelector("h1")?.textContent.trim() || document.querySelector(".welcome h2")?.textContent.trim() || "",
      violations: result.violations.map((violation) => ({
        id: violation.id,
        impact: violation.impact,
        nodes: violation.nodes.length,
        help: violation.help,
        examples: violation.nodes.slice(0, 5).map((node) => ({
          target: node.target.join(" "),
          summary: node.failureSummary,
        })),
      })),
      serious: result.violations.filter((violation) => ["serious", "critical"].includes(violation.impact)).length,
      unnamedButtons,
      controlsWithoutLabel,
      horizontalOverflow: Math.max(0, document.documentElement.scrollWidth - window.innerWidth),
      technicalTerms: (document.body.innerText.match(/PluginSpec|ToolSpec|Schema|Trace|provider|MCP|Connector|Skill|JSON/g) || []).length,
    };
  }, route);
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: "new",
    args: ["--no-sandbox", "--disable-gpu", "--window-size=1440,940"],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 940 });
  await login(page);
  const routes = [
    ["/", ".enterprise-area-grid"],
    ["/ai", "#composer-form"],
    ["/system/users", "#main-content > .panel"],
    ["/system/roles", "#main-content > .panel"],
    ["/system/permissions", "#main-content > .panel"],
    ["/system/settings", "#main-content > .panel"],
    ["/system/api", "#main-content > .panel"],
    ["/capabilities", "#main-content > .panel"],
    ["/connectors", "#main-content > .panel"],
    ["/skills", "#main-content > .panel"],
    ["/plugins", "#main-content > .panel"],
    ["/applications", "#main-content > .panel"],
    ["/operations", ".operations-panel"],
    ["/admin-audit.html", "#content-admin-audit .panel"],
    ["/admin-secrets.html", "#content-admin-secrets .panel"],
    ["/system/models", "#main-content > .panel"],
  ];
  const requestedRoutes = (process.env.AUDIT_ROUTES || "").split(",").map((item) => item.trim()).filter(Boolean);
  const selectedRoutes = requestedRoutes.length ? routes.filter(([route]) => requestedRoutes.includes(route)) : routes;
  const desktop = [];
  for (const [route, selector] of selectedRoutes) desktop.push(await auditRoute(page, route, selector));

  const mobile = await browser.newPage();
  await mobile.setViewport({ width: 390, height: 844, isMobile: true });
  const mobileResults = [];
  for (const [route, selector] of selectedRoutes.filter(([route]) => ["/", "/ai", "/system/users", "/capabilities", "/system/models"].includes(route))) {
    mobileResults.push(await auditRoute(mobile, route, selector));
  }
  await mobile.close();
  await browser.close();

  const summary = {
    desktop,
    mobile: mobileResults,
    totals: {
      serious: desktop.reduce((sum, item) => sum + item.serious, 0) + mobileResults.reduce((sum, item) => sum + item.serious, 0),
      unnamedButtons: desktop.reduce((sum, item) => sum + item.unnamedButtons, 0) + mobileResults.reduce((sum, item) => sum + item.unnamedButtons, 0),
      controlsWithoutLabel: desktop.reduce((sum, item) => sum + item.controlsWithoutLabel, 0) + mobileResults.reduce((sum, item) => sum + item.controlsWithoutLabel, 0),
      overflowRoutes: [...desktop, ...mobileResults].filter((item) => item.horizontalOverflow > 0).map((item) => item.route),
      technicalTermsOnCustomerPages: desktop.filter((item) => !item.route.startsWith("/admin")).reduce((sum, item) => sum + item.technicalTerms, 0),
    },
  };
  console.log(JSON.stringify(summary, null, 2));
  if (process.env.AUDIT_STRICT === "1") {
    if (summary.totals.serious || summary.totals.unnamedButtons || summary.totals.controlsWithoutLabel || summary.totals.overflowRoutes.length) {
      throw new Error("customer experience audit failed");
    }
  }
})().catch((error) => {
  console.error(error.stack || error.message);
  process.exit(1);
});
