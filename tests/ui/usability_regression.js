/*
 * 中文说明（可用性回归用例）：
 * 验证行为：界面上易被破坏的可用性细节（布局、可点击性、文案等）在真实浏览器中不回归。
 * 运行方式：需隔离服务（TEST5_E2E_BASE_URL，默认 8011）+ 本机 Edge + TEST5_E2E_PASSWORD。
 * 边界：以页面断言为主，不做数据写入假设。
 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const puppeteer = require("puppeteer-core");
const { login, clickText } = require("./browser_only_helpers");

const BASE = process.env.TEST5_E2E_BASE_URL || "http://127.0.0.1:8035";
const OUTPUT = path.resolve(".implementation/ui-results/usability-" + Date.now().toString(36));
const checks = [];
const pass = label => { checks.push(label); console.log("PASS " + label); };

(async () => {
  assert(["127.0.0.1", "localhost"].includes(new URL(BASE).hostname));
  const health = await fetch(BASE + "/api/health").then(response => response.json());
  assert.equal(health.app, "test", "Only use an isolated acceptance server");
  fs.mkdirSync(OUTPUT, { recursive: true });
  const browser = await puppeteer.launch({ executablePath: process.env.TEST5_EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", headless: true, args: ["--no-sandbox"] });
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  try {
    await page.setViewport({ width: 1440, height: 940 });
    await login(page, BASE, process.env.TEST5_E2E_PASSWORD);
    assert.equal(await page.$$eval(".enterprise-area-grid .enterprise-area-card", nodes => nodes.length), 4);
    assert.equal(await page.$$eval(".enterprise-management-strip [data-area=system]", nodes => nodes.length), 1);
    assert.equal(await page.$eval("#business-sidebar", node => node.getClientRects().length), 0);
    await page.screenshot({ path: path.join(OUTPUT, "home-desktop.png"), fullPage: true });
    pass("首页业务入口 2×2、系统管理独立、没有空侧栏");

    await page.click(".enterprise-search-button");
    await page.type("#module-search", "知识 库");
    assert(await page.$('.enterprise-search-results a[href="/knowledge"]'));
    await page.keyboard.press("ArrowDown");
    assert.equal(await page.evaluate(() => document.activeElement.getAttribute("href")), "/knowledge");
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("ArrowUp");
    assert.equal(await page.evaluate(() => document.activeElement.getAttribute("href")), "/knowledge");
    await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.keyboard.press("Enter")]);
    assert.equal(new URL(page.url()).pathname, "/knowledge");
    pass("功能搜索支持多关键词、上下选择和回车打开");
    await page.click(".enterprise-search-button");
    assert.equal(await page.$eval(".enterprise-search-results section h3", node => node.textContent), "最近访问");
    const hrefs = await page.$$eval(".enterprise-search-results a", nodes => nodes.map(node => node.getAttribute("href")));
    assert.equal(new Set(hrefs).size, hrefs.length);
    await page.evaluate(() => document.querySelector("#module-search").dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", isComposing: true, bubbles: true })));
    assert.equal(await page.$eval(".enterprise-command-dialog", node => node.open), true);
    await page.keyboard.press("Escape");
    pass("功能搜索显示最近访问、无重复入口，中文输入法确认不误跳转");

    await page.goto(BASE + "/account/profile", { waitUntil: "networkidle0" });
    assert.match(await page.title(), /^个人资料/);
    pass("个人资料浏览器标题与页面一致");
    await page.goto(BASE + "/system/users", { waitUntil: "networkidle0" });
    await clickText(page, "button", "新增用户");
    await page.waitForSelector('#platform-dialog[open] [type="submit"]', { visible: true });
    await page.click('#platform-dialog [type="submit"]');
    assert.equal(await page.$$eval('#platform-dialog [aria-invalid="true"]', nodes => nodes.length), 3);
    assert.match(await page.$eval("#platform-dialog .form-error", node => node.textContent), /3 处/);
    await page.screenshot({ path: path.join(OUTPUT, "form-validation.png"), fullPage: true });
    await page.type('#platform-dialog [name="username"]', "ux-regression");
    assert.equal(await page.$eval('#platform-dialog [name="username"]', node => node.getAttribute("aria-invalid")), null);
    await page.click("#platform-dialog [data-dialog-close]");
    assert.equal(await page.$eval("#platform-dialog .dialog-discard", node => node.hidden), false);
    await page.click("#platform-dialog [data-keep-editing]");
    assert.equal(await page.$eval('#platform-dialog [name="username"]', node => node.value), "ux-regression");
    await page.keyboard.press("Escape");
    await page.click("#platform-dialog [data-discard]");
    assert.equal(await page.$eval("#platform-dialog", node => node.open), false);
    pass("表单一次显示全部必填错误、修正即清除、关闭保护未保存内容");

    // Exercise shared dialog lifecycle with a deferred save, without business writes.
    await page.evaluate(async () => {
      const ui = await import("/assets/platform/ui.js");
      window.uxSaveCount = 0;
      ui.openDialog("保存状态回归", ui.field("名称", ui.input("name", "测试", "required")), {
        onSave: async () => { window.uxSaveCount++; await new Promise(resolve => { window.uxResolveSave = resolve; }); },
      });
    });
    await page.click('#platform-dialog [type="submit"]');
    await page.keyboard.press("Escape");
    await page.evaluate(() => document.querySelector("#platform-dialog form").requestSubmit());
    assert.equal(await page.evaluate(() => window.uxSaveCount), 1);
    assert.equal(await page.$eval("#platform-dialog", node => node.open), true);
    assert(await page.$$eval("#platform-dialog [data-dialog-close]", nodes => nodes.every(node => node.disabled)));
    await page.evaluate(() => window.uxResolveSave());
    await page.waitForFunction(() => !document.querySelector("#platform-dialog").open);
    pass("保存期间防重复提交，Escape 不会丢失保存状态");
    await page.evaluate(async () => {
      const ui = await import("/assets/platform/ui.js");
      ui.openDialog("异步选项回归", '<select name="resource" aria-label="资源" disabled><option value="">正在加载</option></select>', {
        onSave: async () => {},
        onReady: async dialog => { await Promise.resolve(); dialog.querySelector("select").disabled = false; },
      });
    });
    await page.click("#platform-dialog [data-dialog-close]");
    assert.equal(await page.$eval("#platform-dialog", node => node.open), false);
    await page.evaluate(async () => {
      const ui = await import("/assets/platform/ui.js");
      ui.openDialog("创建后显示结果", "操作说明", { onSave: async () => {
        ui.openDialog("操作已完成", "需要用户阅读的结果");
      } });
    });
    await page.click('#platform-dialog [type="submit"]');
    assert.equal(await page.$eval("#platform-dialog", node => node.open), true);
    assert.equal(await page.$eval("#dialog-title", node => node.textContent), "操作已完成");
    const modalCopy = await page.evaluate(async () => {
      const { copyText } = await import("/assets/platform/clipboard.js");
      Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
      const original = document.execCommand;
      let copied = "";
      document.execCommand = () => { copied = document.activeElement.value; return true; };
      try { await copyText("仅用于回归的文本"); return copied; }
      finally { document.execCommand = original; }
    });
    assert.equal(modalCopy, "仅用于回归的文本");
    await page.keyboard.press("Escape");
    pass("异步表单直接关闭、保存后保留结果弹窗、模态框内局域网复制可用");

    const dates = await page.evaluate(async () => {
      const ui = await import("/assets/platform/ui.js");
      const original = ui.state.settings["ui.timezone"];
      ui.state.settings["ui.timezone"] = "invalid-zone";
      const result = [ui.date("not-a-date"), ui.date("2026-09-05T00:00:00"), ui.date("2026-09-05T00:00:00Z")];
      ui.state.settings["ui.timezone"] = original;
      return result;
    });
    assert.equal(dates[0], "—");
    assert.equal(dates[1], dates[2]);
    pass("异常时间或时区配置不会使页面整体崩溃");

    await page.setViewport({ width: 390, height: 844, isMobile: true });
    await page.goto(BASE + "/system/users", { waitUntil: "networkidle0" });
    const table = await page.evaluate(() => {
      const container = document.querySelector(".table-with-actions");
      const action = container.querySelector("tbody td:last-child").getBoundingClientRect();
      const bounds = container.getBoundingClientRect();
      return { right: action.right, left: action.left, boundRight: bounds.right, overflow: document.documentElement.scrollWidth - innerWidth };
    });
    assert(table.left >= 0 && table.right <= 391, JSON.stringify(table));
    assert.equal(table.overflow, 0);
    await page.screenshot({ path: path.join(OUTPUT, "users-mobile.png"), fullPage: true });
    pass("390px 用户表格操作列始终可见、页面无横向溢出");
    await page.click("[data-toggle-navigation]");
    assert.equal(await page.$eval("[data-toggle-navigation]", node => node.getAttribute("aria-expanded")), "true");
    await page.keyboard.press("Escape");
    await page.goto(BASE, { waitUntil: "networkidle0" });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth), 0);
    await page.screenshot({ path: path.join(OUTPUT, "home-mobile.png"), fullPage: true });
    pass("390px 首页和模块菜单操作可达");
    await page.setViewport({ width: 1440, height: 940 });
    await page.goto(BASE + "/ai", { waitUntil: "networkidle0" });
    await page.waitForSelector("#composer-form");
    await page.evaluate(() => {
      state.current = { id: "ui-only-fixture", title: "回复操作回归", messages: [
        { role: "user", content: "第一条问题" }, { role: "assistant", content: "第一条回答" },
        { role: "user", content: "第二条问题" }, { role: "assistant", content: "第二条回答" },
      ] };
      renderMessages();
      Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
      window.uxOriginalCopy = document.execCommand;
      document.execCommand = command => { window.uxCopied = command === "copy" ? document.activeElement.value : ""; return true; };
    });
    await page.click('[data-msg-action="retry"]');
    assert.equal(await page.$eval("#prompt-textarea", node => node.value), "第一条问题");
    await page.click('[data-msg-action="copy"]');
    await page.waitForFunction(() => window.uxCopied === "第一条回答");
    assert.equal(await page.evaluate(() => window.uxCopied), "第一条回答");
    assert.match(await page.$eval("#toast", node => node.textContent), /已复制回答/);
    await page.evaluate(() => { document.execCommand = () => false; });
    await page.click('[data-msg-action="copy"]');
    await page.waitForFunction(() => document.querySelector("#toast").textContent.includes("复制失败"));
    assert.match(await page.$eval("#toast", node => node.textContent), /复制失败/);
    pass("旧回复重发对应原问题；局域网无 Clipboard API 时复制降级及失败反馈");
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(OUTPUT, "result.json"), JSON.stringify({ checks, errors }, null, 2));
    console.log("Evidence: " + OUTPUT);
  } finally { await browser.close(); }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
