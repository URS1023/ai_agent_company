/*
 * 中文说明（Chat 外壳几何与交互的验收脚本）：
 * 验证行为：Codex 风格对话外壳的布局几何、侧栏/输入区尺寸与主要交互契约（含 SSE 流式渲染）。
 * 运行方式：需隔离服务（TEST5_E2E_BASE_URL，默认 http://127.0.0.1:8011）、本机 Edge
 *   （TEST5_EDGE_PATH）与 TEST5_E2E_PASSWORD；结果写入 .implementation/ui-results/chat-shell-<run>/。
 * 约束：改动外壳布局或交互行为时必须同步更新本文件（AGENTS.md 中的 UI 约定）。
 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const puppeteer = require("puppeteer-core");

const EDGE = process.env.TEST5_EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const BASE = process.env.TEST5_E2E_BASE_URL || "http://127.0.0.1:8011";
const PASSWORD = process.env.TEST5_E2E_PASSWORD;
const ALLOW_NATIVE = process.env.TEST5_E2E_ALLOW_NATIVE === "1";
const MODULES_ONLY = process.env.TEST5_E2E_MODULES_ONLY === "1";
const OUTPUT = path.resolve(".implementation/ui-results/chat-shell-" + Date.now().toString(36));
const checks = [];
const failures = [];
const violations = [];
let browser;
let page;

function pass(label) {
  checks.push(label);
  console.log("PASS " + label);
}

async function verifyComposerScrolling() {
  const probe = await browser.newPage();
  try {
    await probe.setRequestInterception(true);
    probe.on("request", request => request.abort());
    await require("./chat_execution_recovery").verifyStopWithPage(probe);
    pass("Stop generation synchronizes terminal results and keeps following an unconfirmed executor");
    await require("./chat_stream_recovery.test").verifyProductionRecovery();
    pass("同一任务断线恢复保留游标；Harness 正文快照撤回失败半句并保留工具回执，不重复提交");
    await require("./chat_business_questions").verifyWithPage(probe);
    pass("Business clarifications render as ordinary text without choice cards or auto-send controls");
    for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
      await probe.setViewport(viewport);
      await probe.setContent(require("./chat_scroll_layout").fixtureHtml());
      const result = await probe.evaluate(() => window.runChatLayoutChecks());
      assert.equal(result.checks.length, 19);
    }
    pass("Messages remain visible above growing composers on desktop and mobile; reading position is preserved");
    await require("./chat_stream_scroll").main();
    pass("持续 SSE 期间可滚动阅读历史，已挂载大屏与交付附件保持稳定，旧任务不再堆叠");
  } finally { await probe.close(); }
}

async function audit(label) {
  await page.addScriptTag({ path: require.resolve("axe-core/axe.min.js") });
  const findings = await page.evaluate(async () => {
    const result = await axe.run(document, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] },
    });
    return result.violations.map((item) => ({
      id: item.id,
      impact: item.impact,
      nodes: item.nodes.map((node) => ({ target: node.target, summary: node.failureSummary })),
    }));
  });
  violations.push(...findings.map((item) => ({ label, ...item })));
}

async function visitAi() {
  await page.goto(BASE + "/ai", { waitUntil: "networkidle0", timeout: 60000 });
  await page.waitForSelector("[data-testid=ai-business-start]");
  await page.waitForSelector("#composer-form");
  await page.waitForSelector('#chat-model-select[aria-label="对话模型"]');
  assert.equal(await page.$eval('#chat-model-select', node => node.closest('.composer-bar') !== null), true);
}

async function verifyBusinessModules() {
  const expected = ["设备", "质检", "财务", "销售", "采购", "能耗", "仓储"];
  let sends = 0;
  const creations = [];
  const observe = request => {
    if (request.method() !== "POST") return;
    const pathname = new URL(request.url()).pathname;
    if (/\/api\/chat\/conversations\/[^/]+\/send$/.test(pathname)) sends++;
    if (pathname === "/api/chat/conversations") creations.push(JSON.parse(request.postData()));
  };
  page.on("request", observe);
  await page.setViewport({ width: 1262, height: 738 });
  await page.waitForSelector("[data-business-module]", { visible: true });
  assert.deepEqual(await page.$$eval("[data-business-module]", nodes => nodes.map(node => node.textContent.trim().slice(-2))), expected);
  assert(await page.$eval(".history-section", node => node.getBoundingClientRect().height >= 180));
  const ids = await page.$$eval("[data-business-module]", nodes => nodes.map(node => node.dataset.businessModule));
  const conversations = [];
  for (let index = 0; index < ids.length; index++) {
    await page.click('[data-business-module="' + ids[index] + '"]');
    await page.waitForFunction(id => state.current?.business_module === id && !state.openingChat, {}, ids[index]);
    assert.equal(await page.$eval("#prompt-textarea", node => node.value), "");
    assert.equal(await page.$eval(".business-chat-welcome h2", node => node.textContent), expected[index] + "助手");
    assert.equal(await page.$eval("#business-chat-badge", node => node.textContent), expected[index] + "对话");
    assert.equal(await page.$eval("#composer-submit-button", node => node.disabled), true);
    assert.equal(await page.$("#business-prompt-dialog"), null);
    const cid = await page.evaluate(() => state.current.id);
    conversations.push(cid);
    assert(page.url().includes("cid=" + cid));
    if (index === 0) await page.screenshot({ path: path.join(OUTPUT, "business-chat-equipment.png") });
  }
  assert.equal(creations.length, 7);
  assert(creations.every((payload, index) => payload.business_module === ids[index] && !payload.content && !payload.system_prompt));
  await page.click('[data-business-module="inventory"]');
  assert.equal(creations.length, 7, "Re-selecting an empty module conversation must not create duplicates");
  pass("Seven modules directly open empty business conversations; only module identifiers are submitted");

  await page.type("#prompt-textarea", "分析 8 月份的实际业务数据。");
  const context = await page.evaluate(() => {
    state.datasourceIds = ["module-fixture-source"];
    state.fileIds = [{ id: "module-fixture-file", name: "本机测试附件.csv" }];
    state.dashboardTemplateMode = "manual"; state.dashboardTemplateId = "module-fixture-template";
    return JSON.stringify({ datasourceIds: state.datasourceIds, fileIds: state.fileIds, mode: state.dashboardTemplateMode, template: state.dashboardTemplateId });
  });
  await page.click('[data-business-module="quality"]');
  await page.waitForFunction(() => state.current?.business_module === "quality" && !state.openingChat);
  assert.equal(await page.$eval("#prompt-textarea", node => node.value), "分析 8 月份的实际业务数据。");
  assert.equal(await page.evaluate(() => JSON.stringify({ datasourceIds: state.datasourceIds, fileIds: state.fileIds, mode: state.dashboardTemplateMode, template: state.dashboardTemplateId })), context);
  const qualityUrl = page.url();
  // 原生会话的 SSE 会持续连接；以页面加载和下方业务状态作为恢复完成依据。
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => state.current?.business_module === "quality" && !state.openingChat);
  assert.equal(page.url(), qualityUrl);
  assert.equal(await page.$eval("#prompt-textarea", node => node.value), "");
  assert.equal(await page.$eval('[data-business-module="quality"]', node => node.getAttribute("aria-pressed")), "true");
  await page.screenshot({ path: path.join(OUTPUT, "business-chat-quality.png") });
  await page.click('[data-conversation-id="' + conversations[0] + '"] .history-title');
  await page.waitForFunction(() => state.current?.business_module === "equipment" && !state.openingChat);
  assert.equal(await page.$eval("#business-chat-badge", node => node.textContent), "设备对话");
  pass("Module switching preserves the draft and resources; reload and history restore persisted business context");

  await page.evaluate(() => { state.streaming = true; });
  await page.click('[data-business-module="energy"]');
  // DSH 运行归属会话，允许切换到新会话；旧执行循环才需要阻止切换。
  if (ALLOW_NATIVE) {
    await page.waitForFunction(() => state.current?.business_module === "energy" && !state.openingChat);
  } else {
    assert.equal(await page.evaluate(() => state.current.business_module), "equipment");
  }
  await page.evaluate(() => { state.streaming = false; });
  await page.click("#new-chat-btn");
  await page.waitForFunction(() => state.current?.business_module === null && !state.openingChat);
  assert.equal(await page.$eval("#business-chat-badge", node => node.hidden), true);
  assert.equal(await page.$eval("#prompt-textarea", node => node.placeholder), "询问企业 AI");
  await page.click(".business-chat-toggle");
  assert.equal(await page.$eval("#business-chat-grid", node => node.hidden), true);
  await page.click(".business-chat-toggle");
  await page.click("#sidebar-collapse-btn");
  await page.waitForSelector("#mobile-sidebar-btn", { visible: true });
  await page.click("#mobile-sidebar-btn");
  await page.waitForSelector("[data-business-module]", { visible: true });
  pass("General new chat clears business context; streaming, category collapse and sidebar recovery work");

  const restoredConversationId = await page.evaluate(() => state.current.id);
  await page.setViewport({ width: 390, height: 844, isMobile: true });
  await page.waitForSelector("#mobile-sidebar-btn", { visible: true });
  // Mobile emulation reloads the page; restoration renders the conversation
  // and closes its sidebar. Finish restoration before opening the menu.
  await page.waitForFunction(id => state.current?.id === id && !state.openingChat && !state.streaming &&
    document.querySelector('[data-business-module="sales"]')?.disabled === false, {}, restoredConversationId);
  await page.evaluate(() => { document.body.classList.remove("mobile-sidebar-open"); });
  await page.waitForFunction(() => document.querySelector("#sidebar").getBoundingClientRect().right <= 0);
  await page.tap("#mobile-sidebar-btn");
  await page.waitForFunction(() => Math.abs(document.querySelector("#sidebar").getBoundingClientRect().left) < 1);
  await page.tap('[data-business-module="sales"]');
  await page.waitForFunction(() => state.current?.business_module === "sales" && !state.openingChat);
  await page.waitForFunction(() => document.querySelector("#sidebar").getBoundingClientRect().right <= 0);
  assert.equal(await page.evaluate(() => document.body.classList.contains("mobile-sidebar-open")), false);
  assert(await page.$eval("#prompt-textarea", node => !node.value && node === document.activeElement));
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
  await page.waitForSelector("#toast.hidden");
  await page.screenshot({ path: path.join(OUTPUT, "business-chat-mobile.png") });
  assert.equal(sends, 0, "Module entry must never submit an AI request or query business data");
  page.off("request", observe);
  await page.setViewport({ width: 1440, height: 960 });
  await page.click("#new-chat-btn").catch(async () => {
    await page.click("#mobile-sidebar-btn");
    await page.click("#new-chat-btn");
  });
  await page.waitForFunction(() => state.current?.business_module === null && !state.openingChat);
  pass("Mobile module entry focuses the empty composer; no AI or business query was submitted");
}

(async () => {
  // DSH 重构用整页内存夹具验收，不启动服务、登录或访问业务数据库。
  if (process.env.TEST5_E2E_DSH_OFFLINE === "1") {
    await require("./dsh_chat_browser").main();
    await require("./dsh_native_interactions").main();
    return;
  }
  if (process.env.TEST5_E2E_DEVICE_CATEGORIES_ONLY === "1") {
    await require("./device_workflow_categories")();
    return;
  }
  if (process.env.TEST5_E2E_STREAM_MARKDOWN_ONLY === "1") {
    await require("./chat_stream_markdown")();
    return;
  }
  if (process.env.TEST5_E2E_FOLLOWUPS_ONLY === "1") {
    await require("./chat_followups")();
    return;
  }
  assert(PASSWORD, "Supply TEST5_E2E_PASSWORD for the isolated preview");
  const health = await fetch(BASE + "/api/health").then((response) => response.json());
  assert(health.app === "test" || (ALLOW_NATIVE && health.app === "native-dev"), "Refusing to run UI acceptance outside an explicitly allowed local development server");
  fs.mkdirSync(OUTPUT, { recursive: true });

  browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: true,
    args: ["--no-sandbox", "--disable-gpu", "--window-size=1440,960"],
  });
  await verifyComposerScrolling();
  page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 960 });
  page.on("pageerror", (error) => failures.push("pageerror: " + error.message));
  page.on("response", (response) => {
    if (response.status() >= 500) failures.push(response.status() + " " + response.url());
  });

  await page.goto(BASE + "/", { waitUntil: "networkidle0", timeout: 60000 });
  await page.type("#login-username", "workspace-admin");
  await page.type("#login-password", PASSWORD);
  await page.click("#login-btn");
  await page.waitForSelector(".enterprise-area-grid");
  if (!MODULES_ONLY) {
    for (const [route, destination, selector] of [
      ["/alerts", "/alerts/points", ".alert-content"],
      ["/quality", "/quality/metrics", ".qc-tabs"],
    ]) {
      await page.goto(BASE + route, { waitUntil: "networkidle0" });
      const moduleFrame = await (await page.waitForSelector("#business-module-frame")).contentFrame();
      await moduleFrame.waitForSelector(".management-links a");
      await moduleFrame.click(".management-links a");
      await moduleFrame.waitForSelector(selector);
      await page.waitForFunction(destination => location.pathname === destination, {}, destination);
    }
    pass("Business module links open management pages without redirecting to the home page");
  }
  if (process.env.TEST5_E2E_RECOVERY_FIXTURE) {
    await require("./chat_execution_recovery").verifyOperations(page, BASE, process.env.TEST5_E2E_RECOVERY_FIXTURE, OUTPUT);
    pass("Operations recovery marks only confirmed abandoned runs and preserves waiting and unconfirmed records");
  }
  await visitAi();

  await verifyBusinessModules();
  if (MODULES_ONLY) {
    assert.deepEqual(failures, []);
    fs.writeFileSync(path.join(OUTPUT, "result.json"), JSON.stringify({ checks, failures }, null, 2));
    console.log("RESULT_DIR " + OUTPUT);
    return;
  }

  // The module check changes mobile emulation back to desktop, triggering a
  // navigation. Inspect the general shell only after its startup has settled.
  await visitAi();
  assert.equal(await page.$eval('#voice-input-feedback', el => el.hidden), true);
  assert.equal(await page.$eval('#voice-input-feedback', el => el.getAttribute('aria-live')), 'polite');

  const shell = await page.evaluate(() => ({
    activeArea: document.querySelector(".enterprise-area-link.active")?.dataset.businessArea,
    topAreas: document.querySelectorAll(".enterprise-area-link").length,
    conversationTitle: document.querySelector(".conversation-rail-title")?.textContent.trim(),
    resourceLinks: [...document.querySelectorAll("#ai-resource-links a")].map((node) => ({ label: node.textContent.trim(), href: node.getAttribute("href") })),
    sceneTitles: [...document.querySelectorAll(".ai-scene-card strong")].map((node) => node.textContent.trim()),
    internalItems: [...document.querySelectorAll("#sidebar .side-menu > *")].map((node) => node.textContent.trim()),
    duplicateContextSidebar: Boolean(document.querySelector(".enterprise-sidebar")),
    duplicateAccount: Boolean(document.querySelector(".user-footer")),
    moduleTabs: document.querySelectorAll(".ai-module-sections a").length,
    executionRail: Boolean(document.querySelector(".ai-execution-rail")),
    persistentPpt: Boolean(document.querySelector("#ppt-preset")),
    reasoningControl: Boolean(document.querySelector("#reasoning-mode-btn")),
    dashboardTemplateControl: Boolean(document.querySelector("#dashboard-template-btn")?.getClientRects().length),
    emptyText: document.querySelector("[data-testid=ai-business-start]")?.textContent.trim() || "",
    overflow: document.documentElement.scrollWidth - innerWidth,
  }));
  assert.equal(shell.activeArea, "ai");
  assert.equal(shell.topAreas, 6);
  assert.equal(shell.conversationTitle, "对话记录");
  assert.deepEqual(shell.resourceLinks, [
    { label: "技能", href: "/skills" },
    { label: "插件", href: "/plugins" },
    { label: "智能体", href: "/bots" },
  ]);
  assert.deepEqual(shell.sceneTitles, ["查看设备预警", "生成财务与设备大屏"]);
  assert.deepEqual(shell.internalItems, ["新聊天"]);
  assert.equal(shell.duplicateContextSidebar, false);
  assert.equal(shell.duplicateAccount, false);
  assert.equal(shell.moduleTabs, 0);
  assert.equal(shell.executionRail, false);
  assert.equal(shell.persistentPpt, false);
  assert.equal(shell.reasoningControl, false);
  assert.equal(shell.dashboardTemplateControl, true);
  const dashboardMarkup = await page.evaluate(() => {
    const html = dashboardBusinessResult({reportId:'preview-fixture',title:'付款应付',version:2,
      datasourceNames:['本地验证库'],queryIds:['verified-query'],templateMode:'none',
      previewPath:'/report/view/preview-fixture',widgetCount:1});
    const document = new DOMParser().parseFromString(html, 'text/html');
    return {frames:document.querySelectorAll('iframe').length,
      preview:document.querySelector('[data-dashboard-frame]')?.textContent,
      source:document.querySelector('.dashboard-source-line')?.textContent};
  });
  assert.equal(dashboardMarkup.frames,0,'Opening chat history must not automatically run dashboard queries');
  assert.equal(dashboardMarkup.preview,'打开交互预览');
  assert(dashboardMarkup.source.includes('本地验证库'));
  assert(shell.emptyText.length < 45, "AI empty state contains excessive explanatory copy");
  assert(shell.overflow <= 1);
  pass("Test5 is one compact AI module without duplicate navigation or permanent controls");
  await page.screenshot({ path: path.join(OUTPUT, "ai-workbench-desktop.png"), fullPage: false });

  await page.click("#add-menu-btn");
  await page.waitForSelector("#menu-panel:not(.hidden) [data-act=presentation]");
  const addMenu = await page.$eval("#menu-panel", (panel) => {
    const rect = panel.getBoundingClientRect();
    return { className: panel.className, width: rect.width, left: rect.left, right: rect.right };
  });
  assert(addMenu.className.includes("add-menu"));
  assert(addMenu.width <= 370, "Add menu must stay compact instead of spanning the composer");
  assert(addMenu.left >= 0 && addMenu.right <= 1440);
  await page.screenshot({ path: path.join(OUTPUT, "ai-add-menu-compact.png"), fullPage: false });
  const menuItems = await page.$$eval("#menu-panel .menu-item", (items) => items.filter((item) => item.getClientRects().length).map((item) => item.textContent.trim()));
  // 技能与业务能力是 DSH 对话入口，加入菜单后允许一个额外的能力入口。
  assert(menuItems.length <= 10, "Composer menu accumulated unrelated actions");
  assert.equal(new Set(menuItems).size, menuItems.length, "Composer menu contains duplicate actions");
  await page.click("#menu-panel [data-act=presentation]");
  await page.waitForSelector("#template-modal:not(.hidden)");
  await page.waitForFunction(() => document.querySelectorAll(".template-card:not([data-template^='custom:'])").length === 20);
  const templates = await page.evaluate(() => {
    const dialog = document.querySelector(".template-dialog");
    const rect = dialog.getBoundingClientRect();
    const builtin = [...document.querySelectorAll(".template-card:not([data-template^='custom:'])")];
    return {
      builtin: builtin.length,
      previews: builtin.filter((card) => card.querySelector(".template-preview img")).length,
      selected: document.querySelectorAll(".template-card.selected, .template-card[aria-pressed=true]").length,
      persistentPpt: Boolean(document.querySelector("#ppt-preset")),
      pageChoices: document.querySelectorAll("#template-page-count option").length,
      left: rect.left,
      right: rect.right,
      overflow: dialog.scrollWidth - dialog.clientWidth,
    };
  });
  assert.equal(templates.builtin, 20);
  assert.equal(templates.previews, 20);
  assert.equal(templates.selected, 0, "Opening the template library must not silently select a default template");
  assert.equal(templates.persistentPpt, false);
  assert(templates.pageChoices >= 31);
  assert(templates.left >= 0 && templates.right <= 1440 && templates.overflow <= 1);
  await audit("desktop-template-dialog");
  await page.screenshot({ path: path.join(OUTPUT, "ai-template-on-demand.png"), fullPage: false });
  await page.click("#template-close");
  pass("Twenty PPT templates remain available on demand with no default selection");

  await page.click("#dashboard-template-btn");
  await page.waitForSelector("#dashboard-template-modal:not(.hidden)");
  await page.waitForFunction(() => {
    const grid = document.querySelector("#dashboard-template-grid");
    return grid && (grid.querySelector("[data-dashboard-template-id]") || !grid.textContent.includes("正在读取"));
  });
  const dashboardPicker = await page.evaluate(() => ({
    strategies: [...document.querySelectorAll("#dashboard-template-strategy [data-dashboard-mode]")].map(node => node.dataset.dashboardMode),
    cards: document.querySelectorAll("#dashboard-template-grid [data-dashboard-template-id]").length,
    emptyState: document.querySelector("#dashboard-template-grid .dashboard-template-empty")?.textContent.trim() || "",
  }));
  // Current product UI offers automatic reference and a locked manual template.
  // The former none option remains an explicit product-contract audit item.
  assert.deepEqual(dashboardPicker.strategies, ["auto", "manual"]);
  assert((await page.$eval('.dashboard-template-head p', node => node.textContent)).includes('参考现有模板'));
  if (dashboardPicker.cards) {
    await page.click('#dashboard-template-grid [data-dashboard-template-id]');
    assert((await page.$eval('#dashboard-template-summary', node => node.textContent)).includes('不修改模板布局、组件与样式'));
  }
  assert(dashboardPicker.cards > 0 || dashboardPicker.emptyState.includes("还没有可用的大屏模板"));
  await page.click("#dashboard-template-close");
  pass("Dashboard template selection remains reachable even when the catalog is empty");

  await page.setViewport({ width: 390, height: 844, isMobile: true });
  await visitAi();
  const mobile = await page.evaluate(() => ({
    overflow: document.documentElement.scrollWidth - innerWidth,
    visibleTopLinks: [...document.querySelectorAll(".enterprise-primary a")].filter((node) => node.getClientRects().length).length,
    domainSelect: Boolean(document.querySelector(".enterprise-area-select select")?.getClientRects().length),
    sceneColumns: new Set([...document.querySelectorAll(".ai-scene-card")].map((card) => Math.round(card.getBoundingClientRect().left))).size,
    composer: (() => { const rect = document.querySelector("#composer-form").getBoundingClientRect(); return { left: rect.left, right: rect.right }; })(),
  }));
  assert(mobile.overflow <= 1);
  assert.equal(mobile.visibleTopLinks, 0);
  assert(mobile.domainSelect);
  assert.equal(mobile.sceneColumns, 1);
  assert(mobile.composer.left >= 0 && mobile.composer.right <= 390);
  await page.click("#add-menu-btn");
  await page.click("#menu-panel [data-act=presentation]");
  await page.waitForSelector("#template-modal:not(.hidden)");
  const mobileDialog = await page.$eval(".template-dialog", (dialog) => {
    const rect = dialog.getBoundingClientRect();
    return { left: rect.left, right: rect.right, overflow: dialog.scrollWidth - dialog.clientWidth };
  });
  assert(mobileDialog.left >= 0 && mobileDialog.right <= 390 && mobileDialog.overflow <= 1);
  await audit("mobile-template-dialog");
  await page.screenshot({ path: path.join(OUTPUT, "ai-mobile-template.png"), fullPage: false });
  pass("AI composer and on-demand template library fit a 390px viewport");

  // 长任务与精简结果区域使用内存接口验收，不触发当前服务的业务查询。
  await require("./chat_output_compact").main();
  pass("桌面与移动端保留处理过程及文件交付，不再显示数据源候选卡片和历史任务列表");

  assert.deepEqual(failures, []);
  assert.deepEqual(violations, []);
  fs.writeFileSync(path.join(OUTPUT, "result.json"), JSON.stringify({ checks, failures, violations, shell, templates, mobile }, null, 2));
  console.log("RESULT_DIR " + OUTPUT);
  console.log("ACCESSIBILITY_FINDINGS " + violations.length);
})().catch(async (error) => {
  console.error(error.stack || error.message);
  if (page && !page.isClosed()) console.error("UI_FAILURE_STATE " + JSON.stringify(await page.evaluate(() => ({
    module: typeof state !== "undefined" ? state.current?.business_module : null,
    openingChat: typeof state !== "undefined" ? state.openingChat : null,
    streaming: typeof state !== "undefined" ? state.streaming : null,
    sidebar: document.querySelector("#sidebar")?.getBoundingClientRect().toJSON(),
    sidebarOpen: document.body.classList.contains("mobile-sidebar-open"),
  })).catch(() => ({}))));
  if (page && !page.isClosed()) await page.screenshot({ path: path.join(OUTPUT, "failure.png"), fullPage: true }).catch(() => {});
  if (fs.existsSync(OUTPUT)) fs.writeFileSync(path.join(OUTPUT, "result.json"), JSON.stringify({ checks, failures, violations, error: String(error) }, null, 2));
  process.exitCode = 1;
}).finally(async () => {
  if (browser) await browser.close();
});
