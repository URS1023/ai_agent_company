/**
 * 服务身份与报表增强的本机UI验收：一个浏览器、120业务HTTP、180秒、至多4次实际查询。
 * root先核origin/Python PID/新SQLite目录归属，再显式提供环境证明；脚本不启动服务或数据库。
 * 业务API全部真实请求。复制仅使用内存剪贴板替身，防止Windows剪贴板历史保存临时令牌。
 * 原文令牌不截图、下载、日志或持久化；任何失败立即停止，不自动重试或改换数据源。
 */
import { chromium } from "playwright";
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { basename, isAbsolute, normalize } from "node:path";

const origin = process.env.E2E_ORIGIN ?? "http://127.0.0.1:18080";
const dataDir = process.env.LOCAL_E2E_DATA_DIR ?? "";
const platformPid = Number(process.env.LOCAL_E2E_PLATFORM_PID);
assert.ok(
  /^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(origin),
  "只允许本轮本机隔离平台",
);
assert.ok(
  process.env.E2E_USERNAME && process.env.E2E_PASSWORD,
  "验收账号须由环境提供",
);
assert.ok(
  isAbsolute(dataDir) && !dataDir.startsWith("\\\\"),
  "必须指定新建本机SQLite绝对目录",
);
assert.ok(
  !/[\\/]\.local[\\/]platform[\\/]?$/i.test(normalize(dataDir)),
  "禁止使用交付中的平台目录",
);
assert.ok(
  Number.isInteger(platformPid) &&
    platformPid > 0 &&
    process.env.LOCAL_E2E_OWNERSHIP_VERIFIED === "1",
  "root须核实本机origin/PID/新SQLite归属后显式提供证明",
);

const maxRequests = 120;
const maxQueries = 4;
const maxTime = 180000;
const started = performance.now();
const prefix = `enhance-${Date.now().toString(36)}`;
const summary = {
  scope: "新本机SQLite/新CSV，真实身份与报表UI；复制只验内存API替身",
  origin,
  platform_pid: platformPid,
  data_directory: basename(dataDir),
  prefix,
  started_at: new Date().toISOString(),
  checks: [],
  fixture_ids: {},
  api_requests: 0,
  actual_query_upper_bound: 0,
  initial_queries: 0,
  report_refreshes: 0,
  report_generations: 0,
  token_issues: 0,
  api_failures: [],
  blocked_requests: [],
  page_errors: [],
  network_failures: [],
  screenshots: [],
};
let browser;
let context;
let page;
let csrf = "";
let stopped = false;
let closing = false;
let queryInFlight = false;
let phase = "preflight";
let dataset;
let query;
let queryLabel = "";
let identity;
let tokenId = "";
let issuedValue = "";
let mainReport;
let generatedReport;
let customTemplate;
const reports = new Map();
const templates = new Set();
const sources = new Map();
const createdWidgets = [];

function cleanError(error) {
  let message = error instanceof Error ? error.message : "验收失败";
  for (const secret of [
    process.env.E2E_USERNAME,
    process.env.E2E_PASSWORD,
    csrf,
    issuedValue,
  ]) {
    if (secret) message = message.replaceAll(secret, "[REDACTED]");
  }
  return message.replace(/service_[A-Za-z0-9_-]+/g, "[REDACTED_TOKEN]");
}
function ensureActive() {
  if (stopped || performance.now() - started >= maxTime) {
    stopped = true;
    throw new Error("验证已停止或达到180秒总预算");
  }
}
function queryCost(method, path, body) {
  if (method !== "POST") return 0;
  if (path === "/queries") {
    assert.equal(summary.initial_queries, 0, "只允许一次初始查询");
    summary.initial_queries++;
    return 1;
  }
  if (path === "/report-generations") {
    assert.equal(summary.report_generations, 0, "只允许一次模板生成");
    summary.report_generations++;
    return new Set(
      Object.values(body.bindings ?? {}).map((binding) =>
        typeof binding === "string" ? binding : binding.query_id,
      ),
    ).size;
  }
  const id = path.match(/^\/reports\/([^/]+)\/data-refreshes$/)?.[1];
  if (id) {
    assert.equal(summary.report_refreshes, 0, "只允许一次报表刷新");
    summary.report_refreshes++;
    return new Set(
      reports
        .get(id)
        ?.layout.widgets.flatMap((widget) =>
          widget.binding?.query_id ? [widget.binding.query_id] : [],
        ),
    ).size;
  }
  return 0;
}
function countRequest(method, path, body) {
  ensureActive();
  assert.ok(summary.api_requests < maxRequests, "达到120次业务HTTP预算");
  const cost = queryCost(method, path, body);
  assert.ok(
    summary.actual_query_upper_bound + cost <= maxQueries,
    "达到4次实际查询预算",
  );
  summary.api_requests++;
  summary.actual_query_upper_bound += cost;
  if (cost) queryInFlight = true;
  return cost;
}
function knownBinding(binding) {
  return (
    binding &&
    binding.query_id === query?.id &&
    !binding.source_type &&
    !binding.inspection_id &&
    !binding.run_id
  );
}
function knownLayout(layout) {
  return (
    layout &&
    !layout.kind &&
    Array.isArray(layout.widgets) &&
    layout.widgets.length <= 3 &&
    layout.widgets.every(
      (widget) =>
        ["JRankingList", "JListProgress", "JFunnel"].includes(
          widget.component,
        ) &&
        (!widget.binding || knownBinding(widget.binding)),
    )
  );
}
function allowed(method, path, body) {
  if (method === "GET")
    return (
      [
        "/auth/status",
        "/me",
        "/permissions/navigation",
        "/roles",
        "/service-identities",
        "/reports",
        "/report-templates",
        "/queries",
        "/report-components/catalog",
      ].includes(path) ||
      (!!identity && path === `/service-identities/${identity.id}/tokens`) ||
      [...reports.keys()].some((id) =>
        [`/reports/${id}`, `/reports/${id}/image-assets`].includes(path),
      )
    );
  if (method === "POST" && path === "/auth/login") return true;
  if (method === "POST" && path === "/datasets")
    return (
      body.name === `${prefix}-csv` &&
      typeof body.csv_text === "string" &&
      !body.datasource_id
    );
  if (method === "POST" && path === "/queries")
    return (
      dataset &&
      body.datasource_id === dataset.datasource_id &&
      body.sql === querySql() &&
      body.max_rows === 10
    );
  if (method === "POST" && path === "/service-identities")
    return (
      String(body.name).startsWith(prefix) &&
      body.caller_kind === "automation" &&
      JSON.stringify(body.permissions) === '["m11.read"]'
    );
  if (
    identity &&
    method === "PATCH" &&
    path === `/service-identities/${identity.id}`
  )
    return (
      Number.isInteger(body.revision) &&
      Object.keys(body).every((key) =>
        ["revision", "name", "description", "permissions", "enabled"].includes(
          key,
        ),
      ) &&
      (!body.name || body.name.startsWith(prefix)) &&
      (!body.permissions || JSON.stringify(body.permissions) === '["m11.read"]')
    );
  if (
    identity &&
    method === "POST" &&
    path === `/service-identities/${identity.id}/tokens`
  )
    return (
      summary.token_issues === 0 &&
      body.name === `${prefix}-短令牌` &&
      body.ttl_minutes === 5 &&
      JSON.stringify(body.scopes) === '["m11.read"]'
    );
  if (
    identity &&
    tokenId &&
    method === "DELETE" &&
    path === `/service-identities/${identity.id}/tokens/${tokenId}`
  )
    return true;
  if (method === "POST" && path === "/reports")
    return (
      body.name === `${prefix}-组件画布` &&
      knownLayout(body.layout) &&
      body.layout.widgets.length === 0
    );
  if (method === "POST" && path === "/report-component-instances")
    return sources.has(body.source_id);
  if (method === "POST" && path === "/report-templates")
    return (
      body.name === `${prefix}-映射模板` &&
      knownLayout(body.layout) &&
      body.layout.widgets.length === 1
    );
  if (method === "POST" && path === "/report-generations")
    return (
      templates.has(body.template_id) &&
      body.name === `${prefix}-换数报表` &&
      Object.values(body.bindings ?? {}).length === 1 &&
      Object.values(body.bindings).every(
        (binding) =>
          knownBinding(binding) &&
          binding.label_field === "team" &&
          binding.value_field === "output",
      )
    );
  for (const id of reports.keys()) {
    if (method === "PATCH" && path === `/reports/${id}`)
      return (
        String(body.name).startsWith(prefix) &&
        knownLayout(body.layout) &&
        Number.isInteger(body.revision)
      );
    if (method === "POST" && path === `/reports/${id}/data-refreshes`)
      return Number.isInteger(body.revision);
    if (method === "POST" && path === `/reports/${id}/exports`)
      return body.format === "html";
  }
  return false;
}
function querySql() {
  assert.ok(
    dataset && /^[A-Za-z_][A-Za-z0-9_]*$/.test(dataset.table_name),
    "只允许引用本轮新CSV受控表名",
  );
  return `SELECT team, output, target, note, observed, sequence FROM "${dataset.table_name}" ORDER BY sequence`;
}
function capture(method, path, result) {
  // 只保留令牌ID；签发原文不得进入summary或通用资源集合。
  if (method === "POST" && path === "/service-identities") {
    identity = result;
    summary.fixture_ids.service_identity = result.id;
  } else if (
    identity &&
    method === "PATCH" &&
    path === `/service-identities/${identity.id}`
  )
    identity = result;
  else if (
    identity &&
    method === "POST" &&
    path === `/service-identities/${identity.id}/tokens`
  ) {
    tokenId = result.id;
    summary.fixture_ids.service_token = result.id;
  } else if (method === "POST" && path === "/report-component-instances")
    createdWidgets.push(result);
  else if (
    (method === "POST" && ["/reports", "/report-generations"].includes(path)) ||
    (result?.layout && reports.has(result.id))
  ) {
    reports.set(result.id, result);
    if (path === "/report-generations") generatedReport = result;
  }
}
async function call(method, path, body) {
  assert.ok(allowed(method, path, body), "准备请求超出本轮新资源白名单");
  const cost = countRequest(method, path, body);
  let response;
  try {
    response = await context.request.fetch(`${origin}/api/v1${path}`, {
      method,
      headers: { "X-CSRF-Token": csrf },
      data: body,
      timeout: 10000,
      maxRetries: 0,
      maxRedirects: 0,
    });
  } catch {
    stopped = true;
    if (cost) summary.requires_query_stop_check = true;
    throw new Error(`${method} ${path} 超时或连接中断，停止后续操作`);
  }
  if (!response.ok()) {
    stopped = true;
    summary.api_failures.push({ method, path, status: response.status() });
    if (cost) summary.requires_query_stop_check = true;
    throw new Error(`${method} ${path} 返回${response.status()}`);
  }
  if (cost) queryInFlight = false;
  const result = await response.json();
  capture(method, path, result);
  return result;
}
async function installGuards() {
  await context.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    if (
      (url.protocol.startsWith("http") && url.origin !== origin) ||
      !["http:", "data:", "blob:"].includes(url.protocol)
    ) {
      stopped = true;
      summary.blocked_requests.push({
        method,
        path: `${url.origin}${url.pathname}`,
      });
      await route.abort("blockedbyclient");
      return;
    }
    if (!url.pathname.startsWith("/api/v1/")) {
      if (stopped) await route.abort("blockedbyclient");
      else await route.continue();
      return;
    }
    const path = url.pathname.slice(7);
    let body;
    if (["POST", "PATCH", "PUT"].includes(method)) {
      try {
        body = request.postDataJSON();
      } catch {
        body = undefined;
      }
    }
    if (!allowed(method, path, body)) {
      stopped = true;
      summary.blocked_requests.push({ method, path });
      await route.abort("blockedbyclient");
      return;
    }
    let cost;
    try {
      cost = countRequest(method, path, body);
    } catch {
      stopped = true;
      await route.abort("blockedbyclient");
      return;
    }
    if (
      identity &&
      method === "POST" &&
      path === `/service-identities/${identity.id}/tokens`
    )
      summary.token_issues++;
    try {
      const response = await route.fetch({
        timeout: 10000,
        maxRetries: 0,
        maxRedirects: 0,
      });
      if (!response.ok()) {
        stopped = true;
        summary.api_failures.push({ method, path, status: response.status() });
        if (cost) summary.requires_query_stop_check = true;
      } else {
        if (cost) queryInFlight = false;
        if (method !== "GET" && !path.endsWith("/exports"))
          capture(method, path, await response.json());
      }
      await route.fulfill({ response });
    } catch {
      stopped = true;
      if (cost) summary.requires_query_stop_check = true;
      summary.api_failures.push({
        method,
        path,
        failure: "timeout_or_connection",
      });
      await route.abort("failed").catch(() => {});
    }
  });
  await context.routeWebSocket("**/*", (socket) => {
    stopped = true;
    const url = new URL(socket.url());
    summary.blocked_requests.push({
      method: "WEBSOCKET",
      path: `${url.origin}${url.pathname}`,
    });
    socket.close({ code: 1008, reason: "outside_e2e_scope" });
  });
  page.on("pageerror", (error) => {
    stopped = true;
    summary.page_errors.push(cleanError(error));
  });
  page.on("requestfailed", (request) => {
    if (closing) return;
    stopped = true;
    summary.network_failures.push({
      type: request.resourceType(),
      path: new URL(request.url()).pathname,
    });
    if (queryInFlight) summary.requires_query_stop_check = true;
  });
  page.on("response", (response) => {
    const path = new URL(response.url()).pathname;
    if (!closing && !path.startsWith("/api/v1/") && response.status() >= 400) {
      stopped = true;
      summary.api_failures.push({
        method: response.request().method(),
        path,
        status: response.status(),
      });
    }
  });
}
function responseFor(method, path) {
  ensureActive();
  return page.waitForResponse(
    (response) =>
      response.request().method() === method &&
      new URL(response.url()).pathname === `/api/v1${path}`,
  );
}
async function jsonResponse(pending) {
  const response = await pending;
  assert.ok(response.ok(), "真实业务操作失败");
  ensureActive();
  return response.json();
}
async function settleUi() {
  ensureActive();
  await page.waitForFunction(
    () =>
      ![
        ...document.querySelectorAll(
          ".el-loading-mask,.el-message,.el-button.is-loading",
        ),
      ].some((element) => element.getClientRects().length),
  );
  ensureActive();
}
function field(scope, label) {
  return scope
    .locator(".el-form-item")
    .filter({ has: page.getByText(label, { exact: true }) })
    .first();
}
async function choose(control, label) {
  ensureActive();
  await control.click();
  await page
    .locator(".el-select-dropdown:visible")
    .getByRole("option", { name: label, exact: true })
    .click();
}
async function fillNumber(scope, label, value) {
  const input = field(scope, label).locator("input");
  await input.fill(String(value));
  await input.press("Tab");
}
async function noSensitiveState() {
  const check = await page.evaluate(() => ({
    dom: [...document.querySelectorAll("input,textarea")].some((element) =>
      /^service_[A-Za-z0-9_-]{30,}$/.test(element.value),
    ),
    storage: [localStorage, sessionStorage].some((storage) =>
      Object.values(storage).some((value) =>
        /service_[A-Za-z0-9_-]{30,}/.test(value),
      ),
    ),
  }));
  assert.ok(!check.dom && !check.storage, "令牌关闭后仍留在DOM或应用存储");
}
async function screenshot(name) {
  await settleUi();
  await noSensitiveState();
  const filename = `test-results/platform-enhancements-${name}.png`;
  await page.screenshot({ path: filename });
  summary.screenshots.push(filename);
}
async function serviceIdentityUi() {
  phase = "service_identity_ui";
  await page.goto(`${origin}/roles`);
  const loadingIdentities = responseFor("GET", "/service-identities");
  await page.getByRole("tab", { name: "服务身份", exact: true }).click();
  await jsonResponse(loadingIdentities);
  const workspace = page.locator(".service-identities");
  await workspace
    .getByRole("button", { name: "新增服务身份", exact: true })
    .click();
  let dialog = page.getByRole("dialog", { name: "新增服务身份", exact: true });
  await field(dialog, "名称").locator("input").fill(`${prefix}-服务身份`);
  await field(dialog, "说明")
    .locator("textarea")
    .fill("本轮隔离验收用质量只读调用方");
  await choose(field(dialog, "用途").locator(".el-select"), "自动化");
  const permission = dialog.getByRole("checkbox", {
    name: "质量·查看",
    exact: true,
  });
  await dialog
    .locator("label.el-checkbox")
    .filter({ has: page.getByRole("checkbox", { name: "质量·查看", exact: true }) })
    .click();
  assert.ok(await permission.isChecked());
  const creating = responseFor("POST", "/service-identities");
  await dialog.getByRole("button", { name: "保存", exact: true }).click();
  identity = await jsonResponse(creating);
  assert.deepEqual(identity.permissions, ["m11.read"]);
  let row = workspace
    .locator(".el-table__row")
    .filter({ hasText: identity.name });
  await row.getByRole("button", { name: "编辑", exact: true }).click();
  dialog = page.getByRole("dialog", { name: "编辑服务身份", exact: true });
  await field(dialog, "名称").locator("input").fill(`${prefix}-服务身份已改`);
  const previousRevision = identity.revision;
  const patching = responseFor("PATCH", `/service-identities/${identity.id}`);
  await dialog.getByRole("button", { name: "保存", exact: true }).click();
  identity = await jsonResponse(patching);
  assert.ok(identity.revision > previousRevision);
  row = workspace.locator(".el-table__row").filter({ hasText: identity.name });
  const loadingTokens = responseFor(
    "GET",
    `/service-identities/${identity.id}/tokens`,
  );
  await row.getByRole("button", { name: "令牌", exact: true }).click();
  await jsonResponse(loadingTokens);
  const drawer = page.locator(".el-drawer:visible");
  await drawer.getByRole("button", { name: "签发令牌", exact: true }).click();
  dialog = page.getByRole("dialog", { name: "签发令牌", exact: true });
  await field(dialog, "名称").locator("input").fill(`${prefix}-短令牌`);
  await fillNumber(dialog, "有效期（分钟）", 5);
  const issuing = responseFor(
    "POST",
    `/service-identities/${identity.id}/tokens`,
  );
  await dialog.getByRole("button", { name: "签发", exact: true }).click();
  const issuedResponse = await issuing;
  assert.ok(issuedResponse.ok());
  // 不读签发响应正文到通用断言或产物，原文仅从当次UI的局部变量取得。
  dialog = page.getByRole("dialog", { name: "令牌已签发", exact: true });
  issuedValue = await dialog
    .getByRole("textbox", { name: "新签发令牌", exact: true })
    .inputValue();
  assert.ok(
    /^service_[A-Za-z0-9_-]{30,}$/.test(issuedValue),
    "未展示有效的新签发令牌",
  );
  assert.ok((await dialog.innerText()).includes("失效时间"));
  await dialog.getByRole("button", { name: "复制令牌", exact: true }).click();
  const copied = await page.evaluate(() => window.__enhancementClipboard);
  assert.ok(
    copied?.called && copied?.validPrefix && copied?.length > 30,
    "复制按钮未调用内存剪贴板替身",
  );
  await dialog.getByRole("button", { name: "完成", exact: true }).click();
  await dialog.waitFor({ state: "hidden" });
  await noSensitiveState();
  issuedValue = "";
  const tokenRow = drawer
    .locator(".el-table__row")
    .filter({ hasText: `${prefix}-短令牌` });
  assert.ok((await tokenRow.innerText()).includes("生效中"));
  const revoking = responseFor(
    "DELETE",
    `/service-identities/${identity.id}/tokens/${tokenId}`,
  );
  await tokenRow.getByRole("button", { name: "撤销", exact: true }).click();
  await page
    .locator(".el-message-box:visible")
    .getByRole("button", { name: "撤销", exact: true })
    .click();
  assert.ok((await jsonResponse(revoking)).revoked);
  await page.waitForFunction(
    (name) =>
      [...document.querySelectorAll(".el-drawer .el-table__row")].some(
        (element) =>
          element.textContent.includes(name) &&
          element.textContent.includes("已撤销"),
      ),
    `${prefix}-短令牌`,
  );
  await drawer.locator(".el-drawer__close-btn").click();
  await drawer.waitFor({ state: "hidden" });
  const disabling = responseFor("PATCH", `/service-identities/${identity.id}`);
  await row.getByRole("button", { name: "停用", exact: true }).click();
  await page
    .locator(".el-message-box:visible")
    .getByRole("button", { name: "停用", exact: true })
    .click();
  identity = await jsonResponse(disabling);
  assert.equal(identity.enabled, false);
  await screenshot("service-identities");
  await page.getByRole("tab", { name: "角色", exact: true }).click();
  await noSensitiveState();
  summary.checks.push(
    "真实UI创建/修改/签发/关闭清空/撤销/停用；令牌不截图落盘，复制只验内存替身",
  );
}

async function addReportWidget(component, title, bounds) {
  const source = sources.get(component);
  assert.ok(source, "组件目录没有本轮待验组件");
  await page.getByRole("button", { name: "全部组件", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "组件目录", exact: true });
  await dialog.getByPlaceholder("搜索组件").fill(source.name);
  const creating = responseFor("POST", "/report-component-instances");
  await dialog
    .getByTitle(source.path, { exact: true })
    .click();
  const widget = await jsonResponse(creating);
  assert.equal(widget.component, component);
  const properties = page.locator(".report-properties");
  await field(properties, "名称").locator("input").fill(title);
  for (const [label, value] of Object.entries(bounds))
    await fillNumber(properties, label, value);
  await choose(field(properties, "数据查询").locator(".el-select"), queryLabel);
  await choose(field(properties, "维度字段").locator(".el-select"), "team");
  await choose(field(properties, "数值字段").locator(".el-select"), "output");
  if (component === "JListProgress")
    await choose(field(properties, "目标字段").locator(".el-select"), "target");
  return widget;
}
async function reportUi() {
  phase = "report_components_ui";
  await page.goto(`${origin}/reports?report=${mainReport.id}`);
  await page.locator(".report-studio").waitFor();
  const properties = page.locator(".report-properties");
  await fillNumber(properties, "宽度", 1200);
  await fillNumber(properties, "高度", 850);
  await field(properties, "背景颜色")
    .locator(".el-color-picker__trigger")
    .click();
  const color = page.locator(".el-color-picker__panel:visible");
  await color.getByRole("textbox").fill("#101f3a");
  await color.getByRole("textbox").press("Tab");
  await color.getByRole("button", { name: /^(确定|确认|OK)$/ }).click();
  const stage = page.locator(".screen-stage");
  await page.waitForFunction(() => {
    const element = document.querySelector(".screen-stage");
    return (
      element?.style.width === "1200px" &&
      element?.style.height === "850px" &&
      getComputedStyle(element).backgroundColor === "rgb(16, 31, 58)"
    );
  });
  await page.getByTitle("适配画布", { exact: true }).click();
  assert.ok(await stage.isVisible());
  const ranking = await addReportWidget("JRankingList", "真实产线排名", {
    X: 30,
    Y: 30,
    宽度: 500,
    高度: 420,
  });
  const nameInput = field(properties, "名称").locator("input");
  await nameInput.fill("待撤销名称");
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await page.waitForFunction(
    () =>
      document.querySelector(".report-properties .el-input__inner")?.value ===
      "真实产线排名",
  );
  await page.getByRole("button", { name: "重做", exact: true }).click();
  await page.waitForFunction(
    () =>
      document.querySelector(".report-properties .el-input__inner")?.value ===
      "待撤销名称",
  );
  await nameInput.fill("真实产线排名");
  await nameInput.press("Tab");
  await addReportWidget("JListProgress", "实际目标进度", {
    X: 560,
    Y: 30,
    宽度: 600,
    高度: 300,
  });
  const funnel = await addReportWidget("JFunnel", "真实产线漏斗", {
    X: 560,
    Y: 380,
    宽度: 600,
    高度: 380,
  });
  const refreshing = responseFor(
    "POST",
    `/reports/${mainReport.id}/data-refreshes`,
  );
  await page.getByRole("button", { name: "刷新数据", exact: true }).click();
  mainReport = await jsonResponse(refreshing);
  assert.equal(mainReport.layout.canvas.width, 1200);
  assert.equal(mainReport.layout.canvas.height, 850);
  assert.equal(
    mainReport.layout.canvas.backgroundColor.toLowerCase(),
    "#101f3a",
  );
  assert.equal(mainReport.layout.widgets.length, 3);
  assert.ok(
    mainReport.layout.widgets.every(
      (widget) => widget.presentation_model?.status === "ready",
    ),
  );
  const rankArticle = page.locator(`[data-widget-id="${ranking.id}"]`);
  await rankArticle.getByText("装配二线", { exact: true }).waitFor();
  assert.equal(
    await rankArticle
      .locator(".list-row")
      .first()
      .locator("span")
      .first()
      .innerText(),
    "装配二线",
  );
  assert.ok((await rankArticle.innerText()).includes("23.0000"));
  const progress = page.locator('[data-component="JListProgress"]');
  assert.ok((await progress.innerText()).includes("17.0000/40"));
  const hasRealGeometry = await page
    .locator(`[data-widget-id="${funnel.id}"] .jimu-chart canvas`)
    .evaluate((canvas) => {
      const data = canvas
        .getContext("2d")
        .getImageData(0, 0, canvas.width, canvas.height).data;
      let visible = 0;
      for (let index = 3; index < data.length; index += 4)
        if (data[index]) visible++;
      return visible > 500;
    });
  assert.ok(hasRealGeometry, "专用漏斗画布未实际绘制");
  assert.equal(await page.locator(".jimu-chart-error:visible").count(), 0);
  await page.getByRole("button", { name: "预览", exact: true }).click();
  await screenshot("report-components");
  summary.fixture_ids.main_report = mainReport.id;
  summary.checks.push(
    "UI创建排名/目标进度/漏斗，真实查询后显示精度、排名与图形；画布尺寸底色、属性撤销重做有效",
  );
}

async function templateUi() {
  phase = "template_mapping_ui";
  const original = mainReport.layout.widgets.find(
    (widget) => widget.component === "JRankingList",
  );
  const seed = structuredClone(original);
  seed.id = `template-rank-${prefix}`;
  seed.i = seed.id;
  seed.title = "模板排名";
  seed.componentName = "模板排名";
  seed.config.chartDataParsed = [];
  seed.config.chartData = "[]";
  for (const key of [
    "binding",
    "data",
    "data_provenance",
    "series_model",
    "presentation_model",
  ])
    delete seed[key];
  customTemplate = await call("POST", "/report-templates", {
    name: `${prefix}-映射模板`,
    layout: {
      canvas: { width: 800, height: 550, backgroundColor: "#101f3a" },
      widgets: [seed],
    },
  });
  templates.add(customTemplate.id);
  summary.fixture_ids.template = customTemplate.id;
  await page.getByRole("button", { name: "返回", exact: true }).click();
  const card = page
    .locator(".template-card")
    .filter({
      has: page.getByRole("heading", {
        name: customTemplate.name,
        exact: true,
      }),
    });
  await card.getByRole("button", { name: "使用模板", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "新建报表", exact: true });
  await field(dialog, "报表名称").locator("input").fill(`${prefix}-换数报表`);
  await choose(dialog.locator(".bind-all .el-select"), queryLabel);
  await dialog.getByRole("button", { name: "应用全部", exact: true }).click();
  await dialog
    .locator(".el-collapse-item__header")
    .filter({ hasText: "模板排名" })
    .click();
  await choose(field(dialog, "维度字段").locator(".el-select"), "team");
  await choose(field(dialog, "数值字段").locator(".el-select"), "output");
  const generating = responseFor("POST", "/report-generations");
  await dialog.getByRole("button", { name: "生成报表", exact: true }).click();
  generatedReport = await jsonResponse(generating);
  assert.equal(generatedReport.layout.widgets[0].binding.label_field, "team");
  assert.equal(generatedReport.layout.widgets[0].binding.value_field, "output");
  assert.equal(
    generatedReport.layout.widgets[0].presentation_model.rows[0].value,
    "23.0000",
  );
  await page.waitForURL(
    (url) =>
      url.pathname === "/reports" &&
      url.searchParams.get("report") === generatedReport.id,
  );
  await page
    .locator(".screen-widget .list-row")
    .first()
    .getByText("装配二线", { exact: true })
    .waitFor();
  await page.getByRole("button", { name: "预览", exact: true }).click();
  await screenshot("template-data");
  await page.getByRole("button", { name: "返回", exact: true }).click();
  const row = page
    .locator(".el-table__row")
    .filter({ hasText: generatedReport.name });
  await row.getByRole("button", { name: "导出", exact: true }).click();
  const exporting = responseFor(
    "POST",
    `/reports/${generatedReport.id}/exports`,
  );
  const downloading = page.waitForEvent("download");
  await page.getByRole("menuitem", { name: "HTML", exact: true }).click();
  const response = await exporting;
  assert.ok(response.ok());
  const download = await downloading;
  assert.equal(await download.failure(), null);
  assert.ok(download.suggestedFilename().endsWith(".html"));
  // 核对用户实际下载的UTF-8文件，避免把浏览器中转响应的解码行为当成导出结果。
  const stream = await download.createReadStream();
  assert.ok(stream, "下载文件不可读取");
  const chunks = [];
  let size = 0;
  for await (const chunk of stream) {
    size += chunk.length;
    assert.ok(size <= 1024 * 1024, "本轮小报表导出超过预期大小");
    chunks.push(chunk);
  }
  const html = Buffer.concat(chunks).toString("utf8");
  summary.export_content_checks = {
    utf8_header: html.includes('charset="utf-8"'),
    value: html.includes("23.0000"),
    label: html.includes("装配二线"),
    title: html.includes("模板排名"),
  };
  assert.ok(
    Object.values(summary.export_content_checks).every(Boolean),
    "实际下载HTML缺少模板换数内容",
  );
  await download.delete();
  summary.fixture_ids.generated_report = generatedReport.id;
  summary.checks.push(
    "模板表单真实选择team/output字段换数、显示排名并从UI下载真实HTML；下载已删除，未启动打印Chrome",
  );
}

const deadline = setTimeout(() => {
  stopped = true;
  summary.budget_exceeded = true;
  if (queryInFlight) summary.requires_query_stop_check = true;
  closing = true;
  if (browser) void browser.close().catch(() => {});
}, maxTime);

await mkdir("test-results", { recursive: true });
try {
  browser = await chromium.launch({
    headless: true,
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE }
      : { channel: "chrome" }),
  });
  context = await browser.newContext({
    viewport: { width: 1700, height: 1100 },
    reducedMotion: "reduce",
    serviceWorkers: "block",
  });
  // 只记录复制API被调用的布尔/长度信息，不把原文令牌写入系统剪贴板或任何全局字符串。
  await context.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: async (value) => {
          window.__enhancementClipboard = {
            called: true,
            validPrefix: /^service_/.test(value),
            length: value.length,
          };
        },
      },
    });
  });
  const me = await call("POST", "/auth/login", {
    username: process.env.E2E_USERNAME,
    password: process.env.E2E_PASSWORD,
  });
  csrf = me.csrf_token;
  assert.ok(
    me.permissions.includes("*"),
    "该脚本需要本轮隔离管理员，不修改当前账号权限",
  );
  dataset = await call("POST", "/datasets", {
    name: `${prefix}-csv`,
    csv_text:
      "team,output,target,note,observed,sequence\n装配一线,17.0000,40,本轮新CSV,2026-10-21T08:00:00+08:00,1\n装配二线,23.0000,40,本轮新CSV,2026-10-21T08:00:00+08:00,2\n装配三线,10.0000,40,本轮新CSV,2026-10-21T08:00:00+08:00,3\n",
  });
  query = await call("POST", "/queries", {
    name: `${prefix}-产线查询`,
    datasource_id: dataset.datasource_id,
    sql: querySql(),
    parameters: {},
    max_rows: 10,
  });
  assert.equal(query.rows.length, 3);
  assert.equal(query.truncated, false);
  const queryList = await call("GET", "/queries");
  const registeredQuery = queryList.items.find((item) => item.id === query.id);
  assert.ok(registeredQuery, "本轮查询未登记");
  queryLabel =
    registeredQuery.name ?? registeredQuery.sql ?? registeredQuery.id;
  const catalog = await call("GET", "/report-components/catalog");
  for (const component of ["JRankingList", "JListProgress", "JFunnel"]) {
    const source = catalog.items.find(
      (item) => item.compType === component && item.renderer_supported,
    );
    assert.ok(source, "新增组件未在本机后端启用");
    sources.set(component, source);
    sources.set(source.id, source);
  }
  mainReport = await call("POST", "/reports", {
    name: `${prefix}-组件画布`,
    layout: {
      canvas: { width: 1000, height: 700, backgroundColor: "#07182d" },
      widgets: [],
    },
  });
  summary.fixture_ids.dataset = dataset.id;
  summary.fixture_ids.query = query.id;
  page = await context.newPage();
  page.setDefaultTimeout(10000);
  page.setDefaultNavigationTimeout(10000);
  await installGuards();
  await serviceIdentityUi();
  await reportUi();
  await templateUi();
  assert.equal(summary.token_issues, 1);
  assert.equal(summary.actual_query_upper_bound, 3);
  assert.equal(summary.api_failures.length, 0);
  assert.equal(summary.blocked_requests.length, 0);
  assert.equal(summary.page_errors.length, 0);
  assert.equal(summary.network_failures.length, 0);
  ensureActive();
  summary.passed = true;
} catch (error) {
  stopped = true;
  summary.passed = false;
  summary.failed_phase = phase;
  if (queryInFlight) summary.requires_query_stop_check = true;
  summary.error = cleanError(error);
  // 失败时绝不拍照，防止当次签发的原文尚在弹窗中。
} finally {
  clearTimeout(deadline);
  issuedValue = "";
  csrf = "";
  closing = true;
  await browser?.close().catch(() => {});
  summary.elapsed_seconds =
    Math.round((performance.now() - started) / 100) / 10;
  const serialized = JSON.stringify(summary, null, 2);
  assert.ok(
    !/service_[A-Za-z0-9_-]{30,}/.test(serialized),
    "结果包含疑似原文令牌，拒绝写入",
  );
  await writeFile("test-results/platform-enhancements.json", serialized);
  console.log(serialized);
}
if (!summary.passed) process.exitCode = 1;
