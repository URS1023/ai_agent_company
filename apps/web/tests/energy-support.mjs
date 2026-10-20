/**
 * 能耗与系统辅助配置的本机真实UI验收；复用platform-enhancements.mjs的归属、请求与失败守卫。
 * root先核实新SQLite目录、监听PID和origin；脚本不启动服务、不访问旧库或模型。
 * 一个系统Chrome、120业务HTTP、180秒、最多6次新CSV查询；实际计划仅初始查询与一次能耗复算。
 * 任何网络/API/UI失败立即结束本次执行，不重试；导出只在内存核对后删除下载文件。
 */
import { chromium } from "playwright";
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { basename, isAbsolute, normalize } from "node:path";

const supportOnly = process.env.E2E_SUPPORT_ONLY === "1";
const origin = process.env.E2E_ORIGIN ?? "http://127.0.0.1:18080";
const dataDir = process.env.LOCAL_E2E_DATA_DIR ?? "";
const platformPid = Number(process.env.LOCAL_E2E_PLATFORM_PID);
assert.ok(
  /^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(origin),
  "只允许本轮本机隔离平台",
);
assert.ok(
  process.env.E2E_USERNAME && process.env.E2E_PASSWORD,
  "账号须由环境提供",
);
assert.ok(
  isAbsolute(dataDir) && !dataDir.startsWith("\\\\"),
  "必须指定新本机SQLite绝对目录",
);
assert.ok(
  !/[\\/]\.local[\\/]platform[\\/]?$/i.test(normalize(dataDir)),
  "禁止使用交付平台目录",
);
assert.ok(
  Number.isInteger(platformPid) &&
    platformPid > 0 &&
    process.env.LOCAL_E2E_OWNERSHIP_VERIFIED === "1",
  "root须显式核实origin/PID/新库归属",
);

const limits = { requests: 120, queries: 6, milliseconds: 180000 };
const started = performance.now();
const prefix = `web14-${Date.now().toString(36)}`;
const codePrefix = prefix.replaceAll("-", "_");
const csv =
  "timestamp,period,energy,production,load,recipe,hours,price,sequence\n2026-10-18T08:00:00+08:00,baseline,100,10,50,A,1,1,1\n2026-10-18T09:00:00+08:00,baseline,100,90,50,A,1,1,2\n2026-10-19T08:00:00+08:00,recent,120,30,50,A,1,1,3\n2026-10-19T09:00:00+08:00,recent,120,70,50,A,1,1,4\n";
const specs = {
  dictionaries: {
    label: "字典",
    kind: "dictionary",
    name: `${prefix}-设备状态`,
    code: `${codePrefix}.status`,
  },
  parameters: {
    label: "参数",
    kind: "parameter",
    name: `${prefix}-每页条数`,
    code: `${codePrefix}.page_size`,
  },
  announcements: {
    label: "公告",
    kind: "announcement",
    name: `${prefix}-检修公告`,
    code: `${codePrefix}.notice`,
  },
  forms: {
    label: "表单",
    kind: "form",
    name: `${prefix}-检查记录`,
    code: `${codePrefix}.form`,
  },
};
const summary = {
  scope:
    "新本机SQLite/单新CSV；同工况能耗与系统辅助配置真实UI，不代表企业节能效果",
  origin,
  platform_pid: platformPid,
  data_directory: basename(dataDir),
  prefix,
  started_at: new Date().toISOString(),
  limits,
  api_requests: 0,
  static_requests: 0,
  actual_query_upper_bound: 0,
  initial_queries: 0,
  energy_runs: 0,
  model_calls: 0,
  support_only: supportOnly,
  checks: [],
  fixture_ids: {},
  screenshots: [],
  exports: {},
  api_failures: [],
  blocked_requests: [],
  page_errors: [],
  network_failures: [],
};
let browser, context, page, dataset, query, device, analysis;
let csrf = "",
  phase = "preflight",
  stopped = false,
  closing = false;
const resources = new Map();
const queriesInFlight = new Set();
let noticeContent = "";

function cleanError(error) {
  let message = error instanceof Error ? error.message : "验收失败";
  for (const secret of [
    process.env.E2E_USERNAME,
    process.env.E2E_PASSWORD,
    csrf,
  ])
    if (secret) message = message.replaceAll(secret, "[REDACTED]");
  return message;
}
function ensureActive() {
  if (stopped || performance.now() - started >= limits.milliseconds) {
    stopped = true;
    throw new Error("验收已停止或达到180秒总预算");
  }
}
function sourceSql() {
  assert.ok(
    dataset && /^[A-Za-z_][A-Za-z0-9_]*$/.test(dataset.table_name),
    "只允许本轮CSV表名",
  );
  return `SELECT timestamp, period, energy, production, load, recipe, hours, price, sequence FROM "${dataset.table_name}" ORDER BY sequence`;
}
function allowed(method, path, body) {
  if (method === "GET") {
    if (
      [
        "/auth/status",
        "/me",
        "/permissions/navigation",
        "/queries",
        "/devices",
        "/maintenance-analyses",
        "/energy-analyses",
        "/operations/overview",
        "/workbench",
        "/operations/announcement-feed",
        "/operations/developer-tools/openapi",
      ].includes(path)
    )
      return true;
    if (path === `/operations/dictionary-options/${specs.dictionaries.code}`)
      return true;
    if (analysis && path === `/energy-analyses/${analysis.id}`) return true;
    return Object.keys(specs).some(
      (plural) =>
        path === `/operations/${plural}` ||
        (resources.has(plural) &&
          [
            "",
            "/revisions",
            ...(plural === "forms" ? ["/schema", "/scaffold"] : []),
          ].some(
            (suffix) =>
              path ===
              `/operations/${plural}/${resources.get(plural).id}${suffix}`,
          )),
    );
  }
  if (method === "POST" && path === "/auth/login")
    return (
      body?.username === process.env.E2E_USERNAME &&
      body?.password === process.env.E2E_PASSWORD
    );
  if (method === "POST" && path === "/datasets")
    return (
      body?.name === `${prefix}-能耗CSV` &&
      body.csv_text === csv &&
      !body.datasource_id
    );
  if (method === "POST" && path === "/queries")
    return (
      dataset &&
      body?.datasource_id === dataset.datasource_id &&
      body.sql === sourceSql() &&
      body.max_rows === 10 &&
      !Object.keys(body.parameters ?? {}).length
    );
  if (method === "POST" && path === "/devices")
    return (
      body?.name === `${prefix}-循环水泵` && body.code === `${codePrefix}_pump`
    );
  if (method === "POST" && path === "/energy-analyses")
    return (
      device &&
      query &&
      body?.device_id === device.id &&
      body.query_id === query.id &&
      body.name === `${prefix}-同工况能耗` &&
      body.min_samples === 2 &&
      body.energy_mode === "interval" &&
      body.energy_field === "energy" &&
      body.production_field === "production" &&
      body.period_field === "period" &&
      body.time_field === "timestamp" &&
      JSON.stringify(body.condition_fields) === '["recipe"]'
    );
  if (
    analysis &&
    method === "POST" &&
    path === `/energy-analyses/${analysis.id}/exports`
  )
    return true;
  for (const [plural, spec] of Object.entries(specs)) {
    const base = `/operations/${plural}`,
      resource = resources.get(plural);
    if (method === "POST" && path === base)
      return (
        body?.code === spec.code &&
        body.name === spec.name &&
        body.scope === "workspace" &&
        body.enabled === true &&
        typeof body.definition === "object"
      );
    if (!resource) continue;
    if (method === "PATCH" && path === `${base}/${resource.id}`)
      return (
        Number.isInteger(body?.revision) &&
        body.revision === resource.revision &&
        (!body.name || body.name.startsWith(prefix)) &&
        Object.keys(body).every((key) =>
          ["revision", "name", "scope", "enabled", "definition"].includes(key),
        ) &&
        (!body.scope || body.scope === "workspace")
      );
    if (method === "DELETE" && path === `${base}/${resource.id}`) return true;
    if (
      plural === "parameters" &&
      method === "POST" &&
      path === `${base}/${resource.id}/restorations`
    )
      return body?.revision === resource.revision && body.target_revision === 1;
    if (
      plural === "forms" &&
      method === "POST" &&
      path === `${base}/${resource.id}/validations`
    )
      return (
        body?.revision === resource.revision &&
        Object.keys(body.values ?? {}).every((key) =>
          ["record", "count", "shift"].includes(key),
        )
      );
  }
  return false;
}
function countRequest(method, path) {
  ensureActive();
  assert.ok(summary.api_requests < limits.requests, "达到120业务HTTP预算");
  let cost = 0;
  if (method === "POST" && path === "/queries") {
    assert.equal(summary.initial_queries, 0, "只允许一个新CSV初始查询");
    summary.initial_queries++;
    cost = 1;
  }
  if (method === "POST" && path === "/energy-analyses") {
    assert.equal(summary.energy_runs, 0, "只允许一次能耗分析，不自动复跑");
    summary.energy_runs++;
    cost = 1;
  }
  assert.ok(
    summary.actual_query_upper_bound + cost <= limits.queries,
    "达到6次新CSV查询预算",
  );
  summary.api_requests++;
  summary.actual_query_upper_bound += cost;
  if (cost) queriesInFlight.add(`${method} ${path}`);
  return cost;
}
function capture(method, path, result) {
  if (method === "POST" && path === "/datasets") dataset = result;
  else if (method === "POST" && path === "/queries") query = result;
  else if (method === "POST" && path === "/devices") device = result;
  else if (method === "POST" && path === "/energy-analyses") {
    analysis = result;
    summary.fixture_ids.energy_analysis = result.id;
  }
  for (const [plural, spec] of Object.entries(specs)) {
    if (
      result?.code === spec.code &&
      result.id &&
      (method === "POST" || method === "PATCH" || method === "DELETE")
    ) {
      resources.set(plural, result);
      summary.fixture_ids[plural] = result.id;
    }
  }
}
async function call(method, path, body) {
  const pathname = path.split("?")[0];
  assert.ok(allowed(method, pathname, body), "辅助请求超出本轮白名单");
  const cost = countRequest(method, pathname);
  let response;
  try {
    response = await context.request.fetch(`${origin}/api/v1${path}`, {
      method,
      data: body,
      headers: { "X-CSRF-Token": csrf },
      timeout: 10000,
      maxRetries: 0,
      maxRedirects: 0,
    });
  } catch {
    stopped = true;
    if (cost) summary.requires_query_stop_check = true;
    throw new Error(`${method} ${pathname} 超时或连接失败`);
  }
  if (!response.ok()) {
    stopped = true;
    summary.api_failures.push({
      method,
      path: pathname,
      status: response.status(),
    });
    if (cost) summary.requires_query_stop_check = true;
    throw new Error(`${method} ${pathname} 返回${response.status()}`);
  }
  if (cost) queriesInFlight.delete(`${method} ${pathname}`);
  const result = await response.json();
  capture(method, pathname, result);
  return result;
}
async function installGuards() {
  await context.route("**/*", async (route) => {
    const request = route.request(),
      url = new URL(request.url()),
      method = request.method();
    if (
      (url.protocol.startsWith("http") && url.origin !== origin) ||
      !["http:", "blob:", "data:"].includes(url.protocol)
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
      summary.static_requests++;
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
      cost = countRequest(method, path);
    } catch {
      stopped = true;
      await route.abort("blockedbyclient");
      return;
    }
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
        if (cost) queriesInFlight.delete(`${method} ${path}`);
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
    if (queriesInFlight.size) summary.requires_query_stop_check = true;
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
  assert.ok(response.ok(), "真实UI操作未成功");
  ensureActive();
  return response.json();
}
function escapeRegex(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
function field(scope, label) {
  // has使用相对的label后代选择器，避免把弹窗祖先重复嵌入造成零匹配。
  return scope
    .locator(".el-form-item")
    .filter({
      has: page
        .locator(".el-form-item__label")
        .filter({ hasText: new RegExp(`^${escapeRegex(label)}$`) }),
    })
    .first();
}
async function fill(scope, label, value) {
  await field(scope, label)
    .locator("input,textarea")
    .first()
    .fill(String(value));
}
async function choose(control, label) {
  ensureActive();
  await control.click();
  await page
    .locator(".el-select-dropdown:visible")
    .getByRole("option", { name: label, exact: true })
    .click();
}
async function setSwitch(scope, label, desired) {
  const container = field(scope, label).locator(".el-switch");
  const input = container.locator('input[role="switch"]');
  if ((await input.getAttribute("aria-checked")) !== String(desired))
    await container.click();
  assert.equal(await input.getAttribute("aria-checked"), String(desired));
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
async function screenshot(name) {
  await settleUi();
  const file = `test-results/energy-support-${name}-${prefix}.png`;
  await page.screenshot({ path: file, fullPage: true });
  summary.screenshots.push(file);
}
async function downloadText(action, extension, maximum = 1024 * 1024) {
  const pending = page.waitForEvent("download");
  await action();
  const result = await pending;
  assert.equal(await result.failure(), null);
  assert.ok(result.suggestedFilename().endsWith(extension));
  try {
    const stream = await result.createReadStream();
    assert.ok(stream, "实际下载不可读");
    const chunks = [];
    let size = 0;
    for await (const chunk of stream) {
      size += chunk.length;
      assert.ok(size <= maximum, "本轮小样本导出超预算");
      chunks.push(chunk);
    }
    return Buffer.concat(chunks)
      .toString("utf8")
      .replace(/^\uFEFF/, "");
  } finally {
    await result.delete();
  }
}
async function energyUi() {
  phase = "energy-ui";
  await page.goto(`${origin}/maintenance`, { waitUntil: "domcontentloaded" });
  await page.getByRole("tab", { name: "节能分析", exact: true }).click();
  await page.getByRole("button", { name: "发起节能分析", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "节能分析", exact: true });
  await fill(dialog, "分析名称", `${prefix}-同工况能耗`);
  await choose(field(dialog, "设备").locator(".el-select"), device.name);
  await choose(field(dialog, "数据查询").locator(".el-select"), query.name);
  for (const [label, column] of [
    ["观测时间字段", "timestamp"],
    ["对比期字段", "period"],
    ["期间电量字段", "energy"],
    ["同期产量字段", "production"],
    ["负载字段", "load"],
  ])
    await choose(field(dialog, label).locator(".el-select"), column);
  await choose(field(dialog, "基准期标记").locator(".el-select"), "baseline");
  await choose(field(dialog, "近期标记").locator(".el-select"), "recent");
  await dialog
    .locator(".el-collapse-item__header")
    .filter({ hasText: "比较条件与金额" })
    .click();
  await choose(field(dialog, "同工况字段").locator(".el-select"), "recipe");
  // 多选下拉保持打开，Escape先关闭，避免覆盖下一控件。
  await page.keyboard.press("Escape");
  await fill(dialog, "每期每组最少样本", 2);
  await field(dialog, "每期每组最少样本").locator("input").press("Tab");
  await choose(field(dialog, "运行时长字段").locator(".el-select"), "hours");
  await choose(
    field(dialog, "电价字段（元/kWh）").locator(".el-select"),
    "price",
  );
  const calculating = responseFor("POST", "/energy-analyses");
  await dialog.getByRole("button", { name: "开始分析", exact: true }).click();
  analysis = await jsonResponse(calculating);
  assert.equal(analysis.result.status, "opportunity");
  assert.equal(Number(analysis.result.baseline_sec), 2);
  assert.equal(Number(analysis.result.recent_sec), 2.4);
  assert.equal(Number(analysis.result.saving_space_kwh), 40);
  assert.equal(analysis.result.economics.saving_space_cny, "40.00");
  assert.equal(Number(analysis.result.coverage), 1);
  assert.equal(analysis.result.bins[0].conditions.recipe, "A");
  await page
    .getByRole("heading", { name: analysis.name, exact: true })
    .waitFor();
  assert.equal(
    await page.locator(".energy-metrics .energy-value").nth(0).innerText(),
    "2",
  );
  await page
    .locator(".energy-metrics")
    .getByText("2.4", { exact: true })
    .waitFor();
  await page
    .locator(".energy-metrics")
    .getByText("20%", { exact: true })
    .waitFor();
  const saved = await call("GET", `/energy-analyses/${analysis.id}`);
  assert.equal(saved.result.method, analysis.result.method);
  assert.equal(saved.provenance.source_query_id, query.id);
  const exported = await downloadText(
    () => page.getByRole("button", { name: "导出数据", exact: true }).click(),
    ".csv",
  );
  summary.exports.energy = {
    title: exported.includes(analysis.name),
    baseline: exported.includes("baseline_sec"),
    recent: exported.includes("recent_sec"),
    production: exported.includes("production_unit"),
    source: exported.includes(analysis.query_id),
  };
  assert.ok(
    Object.values(summary.exports.energy).every(Boolean),
    "实际CSV缺少能耗或来源字段",
  );
  await screenshot("energy");
  summary.checks.push(
    "实际UI从单新CSV选择字段与同工况，保存加权单位能耗2→2.4/变化20%/匹配电量空间40kWh，并核对真实CSV下载；不宣称企业节能收益",
  );
}
async function supportTab(plural) {
  await page
    .getByRole("tab", { name: specs[plural].label, exact: true })
    .click();
  await page
    .locator(".support-workspace")
    .getByRole("button", { name: `新增${specs[plural].label}`, exact: true })
    .waitFor();
}
function resourceRow(plural) {
  const name = resources.get(plural)?.name ?? specs[plural].name;
  return page
    .locator(".support-workspace .el-table__row")
    .filter({ has: page.getByText(name, { exact: true }) });
}
async function beginResource(plural) {
  await supportTab(plural);
  await page
    .locator(".support-workspace")
    .getByRole("button", { name: `新增${specs[plural].label}`, exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: `新增${specs[plural].label}`,
    exact: true,
  });
  await fill(dialog, "名称", specs[plural].name);
  await fill(dialog, "编码", specs[plural].code);
  return dialog;
}
async function saveResource(plural, dialog, create = true) {
  const path = `/operations/${plural}${create ? "" : `/${resources.get(plural).id}`}`;
  const saving = responseFor(create ? "POST" : "PATCH", path);
  await dialog.getByRole("button", { name: "保存", exact: true }).click();
  const saved = await jsonResponse(saving);
  assert.equal(saved.code, specs[plural].code);
  assert.equal(saved.kind, specs[plural].kind);
  await dialog.waitFor({ state: "hidden" });
  await resourceRow(plural).waitFor();
  return saved;
}
async function editResource(plural) {
  await resourceRow(plural)
    .getByRole("button", { name: "编辑", exact: true })
    .click();
  return page.getByRole("dialog", {
    name: `编辑${specs[plural].label}`,
    exact: true,
  });
}
async function toggleResource(plural, enabled) {
  const row = resourceRow(plural);
  const pending = responseFor(
    "PATCH",
    `/operations/${plural}/${resources.get(plural).id}`,
  );
  await row
    .getByRole("button", { name: enabled ? "启用" : "停用", exact: true })
    .click();
  const result = await jsonResponse(pending);
  assert.equal(result.enabled, enabled);
  await resourceRow(plural)
    .locator(".el-tag")
    .getByText(enabled ? "启用" : "停用", { exact: true })
    .waitFor();
}
async function dictionaryUi() {
  phase = "dictionary-ui";
  let dialog = await beginResource("dictionaries");
  await dialog.getByRole("button", { name: "添加选项", exact: true }).click();
  await dialog
    .getByRole("textbox", { name: "选项名称", exact: true })
    .fill("运行");
  await dialog
    .getByRole("textbox", { name: "选项值", exact: true })
    .fill("running");
  await dialog.locator(".entries-editor .el-checkbox").click();
  const created = await saveResource("dictionaries", dialog);
  assert.equal(created.definition.entries[0].is_default, true);
  dialog = await editResource("dictionaries");
  await dialog
    .getByRole("textbox", { name: "选项名称", exact: true })
    .fill("正常运行");
  await saveResource("dictionaries", dialog, false);
  await toggleResource("dictionaries", false);
  await toggleResource("dictionaries", true);
  const options = await call(
    "GET",
    `/operations/dictionary-options/${specs.dictionaries.code}`,
  );
  assert.equal(options.items[0].label, "正常运行");
  assert.equal(options.items[0].value, "running");
  summary.checks.push(
    "字典真实UI新增默认选项、修改、停用启用；实际业务选项接口读取保存内容",
  );
}
async function parameterUi() {
  phase = "parameter-ui";
  let dialog = await beginResource("parameters");
  await choose(field(dialog, "值类型").locator(".el-select"), "数值");
  await fill(dialog, "值", 12);
  await saveResource("parameters", dialog);
  dialog = await editResource("parameters");
  await fill(dialog, "值", 30);
  await saveResource("parameters", dialog, false);
  await resourceRow("parameters")
    .getByRole("button", { name: "历史", exact: true })
    .click();
  const history = page.getByRole("dialog", {
    name: `${specs.parameters.name} · 历史版本`,
    exact: true,
  });
  const version = history
    .locator(".el-table__row")
    .filter({ has: page.getByText("v1", { exact: true }) });
  await version.getByRole("button", { name: "查看", exact: true }).click();
  await history.locator("pre.snapshot").waitFor();
  assert.match(
    await history.locator("pre.snapshot").innerText(),
    /"value":\s*12/,
  );
  await version.getByRole("button", { name: "恢复", exact: true }).click();
  const restoring = responseFor(
    "POST",
    `/operations/parameters/${resources.get("parameters").id}/restorations`,
  );
  await page
    .locator(".el-message-box")
    .getByRole("button", { name: "恢复", exact: true })
    .click();
  const restored = await jsonResponse(restoring);
  assert.equal(restored.definition.value, 12);
  assert.equal(restored.revision, 3);
  await history.waitFor({ state: "hidden" });
  dialog = await editResource("parameters");
  assert.equal(await field(dialog, "值").locator("input").inputValue(), "12");
  await dialog.getByRole("button", { name: "关闭", exact: true }).click();
  await toggleResource("parameters", false);
  await toggleResource("parameters", true);
  summary.checks.push(
    "参数真实UI新增数值、修改为30、查看v1并恢复为新v3=12，重开编辑确认，停用启用",
  );
}
async function announcementUi() {
  phase = "announcement-ui";
  let dialog = await beginResource("announcements");
  await fill(dialog, "内容", "本轮本机隔离验收公告");
  await saveResource("announcements", dialog);
  dialog = await editResource("announcements");
  noticeContent = "本轮本机隔离验收：<b>设备检修安排</b>，请查看当班任务。";
  await fill(dialog, "内容", noticeContent);
  await saveResource("announcements", dialog, false);
  await toggleResource("announcements", false);
  await toggleResource("announcements", true);
  const feed = await call("GET", "/operations/announcement-feed");
  assert.equal(
    feed.items.find((row) => row.id === resources.get("announcements").id)
      ?.content,
    noticeContent,
  );
  summary.checks.push(
    "公告真实UI新增、修改、停用启用，实际已发布工作区公告流含保存正文",
  );
}
async function addField(dialog, label, key, kind = "文本", required = true) {
  await dialog.getByRole("button", { name: "添加字段", exact: true }).click();
  await fill(dialog, "字段名称", label);
  await fill(dialog, "字段编码", key);
  await choose(field(dialog, "控件").locator(".el-select"), kind);
  await setSwitch(dialog, "必填", required);
}
async function formUi() {
  phase = "form-ui";
  let dialog = await beginResource("forms");
  await addField(dialog, "现场记录", "record");
  await fill(dialog, "最多字数", 8);
  await addField(dialog, "数量", "count", "整数");
  await fill(dialog, "最小值", 2);
  await fill(dialog, "最大值", 5);
  await addField(dialog, "班次", "shift", "下拉选择");
  await dialog.getByRole("button", { name: "添加选项", exact: true }).click();
  await dialog
    .getByRole("textbox", { name: "选项名称", exact: true })
    .fill("白班");
  await dialog
    .getByRole("textbox", { name: "选项值", exact: true })
    .fill("day");
  await saveResource("forms", dialog);
  dialog = await editResource("forms");
  await fill(dialog, "备注", "本轮新字段验证");
  await saveResource("forms", dialog, false);
  await resourceRow("forms")
    .getByRole("button", { name: "预览", exact: true })
    .click();
  const preview = page.getByRole("dialog", {
    name: `${specs.forms.name} · 预览`,
    exact: true,
  });
  const initialCount = await field(preview, "数量")
    .locator("input")
    .inputValue();
  let validating = responseFor(
    "POST",
    `/operations/forms/${resources.get("forms").id}/validations`,
  );
  await preview.getByRole("button", { name: "校验", exact: true }).click();
  const invalid = await jsonResponse(validating);
  assert.equal(invalid.valid, false);
  assert.ok(
    invalid.errors.record && invalid.errors.shift,
    "空文本和未选班次应显示字段错误",
  );
  assert.equal(
    !!invalid.errors.count,
    initialCount === "",
    "数量校验须符合控件实际初值",
  );
  summary.form_initial_invalid_fields = Object.keys(invalid.errors).length;
  await field(preview, "现场记录")
    .getByText("请填写现场记录", { exact: true })
    .waitFor();
  assert.equal(
    await field(preview, "现场记录").locator("input").getAttribute("maxlength"),
    "8",
  );
  await fill(preview, "现场记录", "现场已检查");
  await fill(preview, "数量", 3);
  await choose(field(preview, "班次").locator(".el-select"), "白班");
  validating = responseFor(
    "POST",
    `/operations/forms/${resources.get("forms").id}/validations`,
  );
  await preview.getByRole("button", { name: "校验", exact: true }).click();
  assert.equal((await jsonResponse(validating)).valid, true);
  await preview.getByText("校验通过", { exact: true }).waitFor();
  const schemaText = await downloadText(
    () =>
      preview
        .getByRole("button", { name: "导出 JSON Schema", exact: true })
        .click(),
    ".json",
  );
  const schema = JSON.parse(schemaText);
  assert.equal(schema.additionalProperties, false);
  assert.equal(schema.properties.record.maxLength, 8);
  assert.equal(schema.properties.count.type, "integer");
  assert.equal(schema.properties.count.minimum, 2);
  assert.equal(schema.properties.count.maximum, 5);
  assert.deepEqual(schema.properties.shift.enum, ["day"]);
  assert.deepEqual(schema.properties.shift["x-options"], [
    { label: "白班", value: "day" },
  ]);
  assert.deepEqual(schema.required, ["record", "count", "shift"]);
  const vueText = await downloadText(async () => {
    await preview
      .getByRole("button", { name: "生成开发文件", exact: true })
      .click();
    await page.getByRole("menuitem", { name: "Vue 表单", exact: true }).click();
  }, ".vue");
  assert.ok(
    vueText.includes('<script setup lang="ts">') &&
      vueText.includes('"现场记录"') &&
      vueText.includes('"白班"') &&
      !vueText.includes("eval("),
  );
  summary.exports.form = { json_schema: true, vue: true, required_fields: 3 };
  await screenshot("form-preview");
  await preview.getByRole("button", { name: "关闭", exact: true }).click();
  await toggleResource("forms", false);
  await toggleResource("forms", true);
  summary.checks.push(
    "表单真实UI创建文本/整数/下拉字段并编辑，空值显示字段错误，控件限制及实际服务端校验通过，下载核对JSON Schema与Vue文件，停用启用",
  );
}
async function workbenchNoticeUi() {
  phase = "workbench-announcement";
  await page.goto(`${origin}/overview`, { waitUntil: "domcontentloaded" });
  const notice = page
    .locator(".announcements .el-collapse-item")
    .filter({ has: page.getByText(specs.announcements.name, { exact: true }) });
  await notice.locator(".el-collapse-item__header").click();
  await notice.locator(".notice-body").waitFor();
  assert.equal(await notice.locator(".notice-body").innerText(), noticeContent);
  assert.equal(
    await notice.locator(".notice-body b").count(),
    0,
    "公告正文被当作HTML执行",
  );
  await screenshot("workbench-announcement");
  summary.checks.push(
    "工作台实际加载并展开本轮公告，正文按纯文本显示，标签未执行",
  );
}
async function supportHome() {
  phase = "support-home";
  await page.goto(`${origin}/health`, { waitUntil: "domcontentloaded" });
  await page.getByRole("tab", { name: "系统配置", exact: true }).click();
  await page.locator(".support-workspace").waitFor();
}
async function removeResource(plural) {
  await supportTab(plural);
  const record = resources.get(plural),
    row = resourceRow(plural);
  await row.getByRole("button", { name: "删除", exact: true }).click();
  const removing = responseFor("DELETE", `/operations/${plural}/${record.id}`);
  await page
    .locator(".el-message-box")
    .getByRole("button", { name: "删除", exact: true })
    .click();
  assert.equal((await jsonResponse(removing)).deleted, true);
  await row.waitFor({ state: "hidden" });
}
async function apiDirectoryUi() {
  phase = "api-directory";
  await page.getByRole("tab", { name: "接口目录", exact: true }).click();
  const button = page.getByRole("button", {
    name: "导出 OpenAPI",
    exact: true,
  });
  await button.waitFor();
  // 等真实契约完成，不将仅出现按钮误当成可导出。
  await page.waitForFunction(() =>
    [...document.querySelectorAll("button")].some(
      (button) =>
        button.textContent.trim() === "导出 OpenAPI" && !button.disabled,
    ),
  );
  const schema = JSON.parse(
    await downloadText(() => button.click(), ".json", 4 * 1024 * 1024),
  );
  assert.ok(
    schema.paths["/api/v1/energy-analyses"] &&
      schema.paths["/api/v1/operations/forms/{id}/schema"],
  );
  summary.exports.openapi = true;
  summary.checks.push(
    "接口目录实际加载当前契约并从UI导出OpenAPI，含能耗及表单接口",
  );
}

const deadline = setTimeout(() => {
  stopped = true;
  summary.budget_exceeded = true;
  if (queriesInFlight.size) summary.requires_query_stop_check = true;
  closing = true;
  if (browser) void browser.close().catch(() => {});
}, limits.milliseconds);

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
  const me = await call("POST", "/auth/login", {
    username: process.env.E2E_USERNAME,
    password: process.env.E2E_PASSWORD,
  });
  csrf = me.csrf_token;
  assert.ok(me.permissions.includes("*"), "需要本轮隔离管理员，不调整现有权限");
  if (!supportOnly) {
  dataset = await call("POST", "/datasets", {
    name: `${prefix}-能耗CSV`,
    csv_text: csv,
  });
  query = await call("POST", "/queries", {
    name: `${prefix}-能耗查询`,
    datasource_id: dataset.datasource_id,
    sql: sourceSql(),
    parameters: {},
    max_rows: 10,
  });
  assert.equal(query.rows.length, 4);
  assert.equal(query.truncated, false);
  const listed = await call("GET", "/queries");
  const registered = listed.items.find((row) => row.id === query.id);
  assert.ok(registered?.name, "本轮查询名称未实际登记");
  query.name = registered.name;
  device = await call("POST", "/devices", {
    name: `${prefix}-循环水泵`,
    code: `${codePrefix}_pump`,
    device_type: "循环水泵",
    location: "本轮隔离验收",
  });
  summary.fixture_ids.dataset = dataset.id;
  summary.fixture_ids.query = query.id;
  summary.fixture_ids.device = device.id;
  }
  page = await context.newPage();
  page.setDefaultTimeout(10000);
  page.setDefaultNavigationTimeout(10000);
  await installGuards();
  if (!supportOnly) await energyUi();
  await supportHome();
  await dictionaryUi();
  await parameterUi();
  await announcementUi();
  await formUi();
  await workbenchNoticeUi();
  await supportHome();
  phase = "support-soft-deletions";
  for (const plural of Object.keys(specs)) await removeResource(plural);
  const finalFeed = await call("GET", "/operations/announcement-feed");
  assert.ok(
    !finalFeed.items.some(
      (row) => row.id === resources.get("announcements").id,
    ),
    "已删除公告仍在业务公告流中",
  );
  summary.checks.push(
    "四类本轮配置均从真实UI软删除并从列表消失；不清理既有数据或本轮审计历史",
  );
  await apiDirectoryUi();
  assert.equal(summary.initial_queries, supportOnly ? 0 : 1);
  assert.equal(summary.energy_runs, supportOnly ? 0 : 1);
  assert.equal(summary.actual_query_upper_bound, supportOnly ? 0 : 2);
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
  summary.error = cleanError(error);
  if (queriesInFlight.size) summary.requires_query_stop_check = true;
} finally {
  clearTimeout(deadline);
  csrf = "";
  closing = true;
  await browser?.close().catch(() => {});
  summary.elapsed_seconds =
    Math.round((performance.now() - started) / 100) / 10;
  const result = JSON.stringify(summary, null, 2);
  // 仅保存非敏感收据。每次独立prefix保留历史失败，防止下一次运行覆盖证据。
  await writeFile(`test-results/energy-support-${prefix}.json`, result);
  console.log(result);
}
if (!summary.passed) process.exitCode = 1;
