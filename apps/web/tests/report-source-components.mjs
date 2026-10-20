/**
 * 12个真实来源组件的UI/HTML验收：120业务HTTP、180秒、1新CSV、最多3条单查询、0模型。
 * 数据准备复用platform-enhancements的真实查询证据流程，不导入其顶层身份/令牌验收。
 * 本脚本不启动服务；仅接收Python复用守卫核实后的18080账号和目录，失败立即停止。
 */
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { mkdir, writeFile } from "node:fs/promises";
import { basename, dirname, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const chunks = [];
let inputBytes = 0;
for await (const chunk of process.stdin) {
  inputBytes += chunk.length;
  assert.ok(inputBytes < 65536, "验收管道输入超限");
  chunks.push(chunk);
}
const input = JSON.parse(Buffer.concat(chunks).toString("utf8"));
const origin = "http://127.0.0.1:18080";
const expectedData = resolve(
  fileURLToPath(
    new URL("../../../.local/acceptance/r34-20261021/", import.meta.url),
  ),
);
assert.equal(input.source, "verified-report-source-helper-v1");
assert.equal(input.origin, origin);
assert.equal(input.ownership?.verified, true);
assert.equal(input.ownership?.component, "r34-acceptance");
assert.equal(resolve(input.ownership?.data_directory ?? ""), expectedData);
assert.ok(
  Number.isInteger(input.ownership?.platform_pid) &&
    input.ownership.platform_pid > 0,
);
assert.ok(
  input.username &&
    input.password &&
    /^report-sources-[a-f0-9]{12}$/.test(input.prefix),
);
const prefix = input.prefix;
const resume = input.resume ?? null;
if (resume) {
  assert.equal(resume.previous_prefix, "report-sources-3c5ac7be324b");
  assert.equal(
    resume.receipt_sha256,
    "88459df684511bc4e76dfd6eeecfc39024000ea8fde415856128fa571b9016bd",
  );
  assert.equal(resume.previous_api_requests, 26);
  assert.equal(resume.previous_actual_query_upper_bound, 3);
  assert.equal(resume.previous_elapsed_seconds, 14.5);
}
const maxTime = 180000 - (resume?.previous_elapsed_seconds ?? 0) * 1000;
const maxRequests = 120 - (resume?.previous_api_requests ?? 0);
const maxStatic = 400 - (resume?.previous_static_requests ?? 0);
const output = resolve(input.output_directory);
assert.equal(dirname(output), expectedData);
assert.equal(basename(output), prefix);
assert.ok(output.startsWith(expectedData + sep));
await mkdir(output, { recursive: false });

const targets = {
  capsule: "index.js:X:4:1009386233476579328:胶囊图:JCapsuleChart",
  background: "index.js:X:4:1536952329568276481:背景柱形图:JBackgroundBar",
  colorGauge: "index.js:X:4:1501138710950854657:多色仪表盘:JColorGauge",
  gradientGauge: "index.js:X:4:200300100100:渐变仪表盘:JAntvGauge",
  history: "index.js:X:4:1011136904469872896:发展历程:JDevHistory",
  permanent: "index.js:X:4:1756792729039:日历:JPermanentCalendar",
  calendar: "index.js:r:1:local-15013142797119490:日历:JCalendar",
  multiCalendar:
    "index.js:r:1:local-15013142797119491:多视图日历:JMultiViewCalendar",
  horizontal: "index.js:X:4:1757057327940:卡片滚动(横向):JCardScroll",
  vertical: "index.js:X:4:1757057327940:卡片滚动(竖向+序号):JCardScroll",
  highlight: "index.js:X:4:1757057327940:卡片滚动(高亮):JCardScroll",
  carousel: "index.js:X:4:1756869734251:卡片轮播:JCardCarousel",
};
const started = performance.now();
const summary = {
  scope: resume
    ? "复用受审新CSV/12组件报告，只续UI与HTML，0业务查询/0夹具重建/0仪表重复写入；历史26HTTP/3查询计入总预算"
    : "owned18080新验收库，1新CSV、2初始查询和1仪表刷新；真实12组件UI/静态HTML，0模型",
  origin,
  prefix,
  platform_pid: input.ownership.platform_pid,
  started_at: new Date().toISOString(),
  api_requests: 0,
  static_requests: 0,
  actual_query_upper_bound: 0,
  initial_queries: 0,
  report_refreshes: 0,
  datasets: 0,
  instances: 0,
  model_calls: 0,
  checks: [],
  fixture_ids: {},
  api_failures: [],
  blocked_requests: [],
  page_errors: [],
  screenshots: [],
  ...(resume ? { history: resume } : {}),
};
const reports = new Map();
const widgets = new Map();
const exports = new Set();
let rejectFailure;
const failureSignal = new Promise((_, reject) => {
  rejectFailure = reject;
});
// 准备阶段也可能先失败；预装拒绝处理，实际等待仍会收到同一首因。
void failureSignal.catch(() => {});
let browser,
  context,
  page,
  dataset,
  allQuery,
  gaugeQuery,
  gaugeReport,
  calendars,
  cards;
let csrf = "",
  stopped = false,
  closing = false,
  queryInFlight = false,
  phase = "preflight";
const timeout = setTimeout(() => {
  stopped = true;
  summary.budget_exceeded = true;
  if (queryInFlight) summary.requires_query_stop_check = true;
  void browser?.close().catch(() => {});
}, maxTime);

function ensureActive() {
  assert.ok(
    !stopped && performance.now() - started < maxTime,
    "验收已停止或达到180秒预算",
  );
}
function safeError(error) {
  let text =
    typeof error === "string"
      ? error
      : error instanceof Error
        ? error.message
        : "验收失败";
  for (const value of [input.username, input.password, csrf])
    if (value) text = text.replaceAll(value, "[REDACTED]");
  return text.slice(0, 1500);
}
function step(name) {
  ensureActive();
  summary.last_step = name;
  summary.steps ??= [];
  summary.steps.push({
    name,
    elapsed_ms: Math.round(performance.now() - started),
  });
}
function querySql(single = false) {
  assert.ok(dataset && /^[A-Za-z_][A-Za-z0-9_]*$/.test(dataset.table_name));
  return `SELECT name, reading, observed, note, sequence FROM "${dataset.table_name}"${single ? " WHERE sequence = '1'" : ""} ORDER BY sequence`;
}
function knownLayout(layout) {
  return (
    layout &&
    !layout.kind &&
    Array.isArray(layout.widgets) &&
    layout.widgets.length <= 6 &&
    layout.widgets.every(
      (widget) =>
        widgets.get(widget.id)?.source_component_id ===
          widget.source_component_id &&
        widgets.get(widget.id)?.component === widget.component &&
        widget.binding?.query_id ===
          (["JColorGauge", "JAntvGauge"].includes(widget.component)
            ? gaugeQuery?.id
            : allQuery?.id) &&
        widget.binding.label_field === "name" &&
        widget.binding.value_field === "reading" &&
        widget.binding.time_field === "observed" &&
        widget.binding.description_field === "note",
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
        "/reports",
        "/report-templates",
        "/queries",
        "/report-components/catalog",
      ].includes(path) ||
      (resume &&
        ([
          `/datasets/${resume.fixture_ids.dataset}`,
          ...[resume.fixture_ids.all_query, resume.fixture_ids.gauge_query].map(
            (id) => `/queries/${id}`,
          ),
        ].includes(path) ||
          (dataset && path === `/datasources/${dataset.datasource_id}`))) ||
      [...reports.keys()].some((id) =>
        [`/reports/${id}`, `/reports/${id}/image-assets`].includes(path),
      )
    );
  if (method === "POST" && path === "/auth/login")
    return (
      body?.username === input.username && body?.password === input.password
    );
  if (
    resume &&
    !(method === "POST" && /^\/reports\/[^/]+\/exports$/.test(path))
  )
    return false;
  if (method === "POST" && path === "/datasets")
    return (
      summary.datasets === 0 &&
      body?.name === `${prefix}-csv` &&
      typeof body.csv_text === "string" &&
      !body.datasource_id
    );
  if (method === "POST" && path === "/queries")
    return (
      summary.initial_queries < 2 &&
      body?.datasource_id === dataset?.datasource_id &&
      [querySql(), querySql(true)].includes(body.sql) &&
      body.max_rows === 10
    );
  if (method === "POST" && path === "/report-component-instances")
    return (
      summary.instances < 12 && Object.values(targets).includes(body?.source_id)
    );
  if (method === "POST" && path === "/reports")
    return (
      reports.size < 3 &&
      String(body?.name).startsWith(prefix) &&
      knownLayout(body.layout)
    );
  if (
    method === "PATCH" &&
    reports.has(path.split("/")[2]) &&
    path === `/reports/${path.split("/")[2]}`
  )
    return (
      Number.isInteger(body?.revision) &&
      String(body.name).startsWith(prefix) &&
      knownLayout(body.layout) &&
      Object.keys(body).every((key) =>
        ["revision", "name", "layout"].includes(key),
      )
    );
  if (
    method === "POST" &&
    gaugeReport &&
    path === `/reports/${gaugeReport.id}/data-refreshes`
  )
    return summary.report_refreshes === 0 && Number.isInteger(body?.revision);
  const reportId = path.match(/^\/reports\/([^/]+)\/exports$/)?.[1];
  return (
    method === "POST" &&
    reports.has(reportId) &&
    body?.format === "html" &&
    !exports.has(reportId)
  );
}
function count(method, path) {
  ensureActive();
  assert.ok(summary.api_requests < maxRequests, "达到累计120业务HTTP预算");
  let cost = 0;
  if (method === "POST" && path === "/queries") {
    summary.initial_queries++;
    cost = 1;
  }
  if (method === "POST" && path.endsWith("/data-refreshes")) {
    summary.report_refreshes++;
    cost = 1;
  }
  assert.ok(
    summary.actual_query_upper_bound + cost <= (resume ? 0 : 3),
    "达到累计3条单查询预算，续作禁止新增业务查询",
  );
  summary.actual_query_upper_bound += cost;
  summary.api_requests++;
  if (method === "POST" && path === "/datasets") summary.datasets++;
  if (method === "POST" && path === "/report-component-instances")
    summary.instances++;
  const exported = path.match(/^\/reports\/([^/]+)\/exports$/)?.[1];
  if (method === "POST" && exported) exports.add(exported);
  if (cost) queryInFlight = true;
  return cost;
}
function capture(path, result) {
  if (result?.layout && (path === "/reports" || reports.has(result.id)))
    reports.set(result.id, result);
}
function failure(method, path, status, cost, diagnostic = {}) {
  stopped = true;
  if (cost) summary.requires_query_stop_check = true;
  if (!summary.first_failure) {
    path = path.startsWith("/api/v1/") ? path.slice(7) : path;
    summary.first_failure = {
      method,
      path,
      status,
      phase,
      step: summary.last_step,
      ...diagnostic,
    };
    summary.api_failures.push(summary.first_failure);
    rejectFailure(new Error(failureMessage(summary.first_failure)));
  }
  return new Error(failureMessage(summary.first_failure));
}
function failureMessage(first) {
  return [first.method, first.path, first.status, first.code, first.message]
    .filter(Boolean)
    .join(" ");
}
async function responseDiagnostic(response) {
  // 只保留业务错误的安全投影，不记录完整响应、字段输入、Cookie或令牌。
  let body;
  try {
    const bytes = await response.body();
    if (bytes.length <= 65536) body = JSON.parse(bytes.toString("utf8"));
  } catch {
    body = undefined;
  }
  const error = body?.error;
  return {
    code:
      typeof error?.code === "string" &&
      /^[a-z][a-z0-9_]{0,79}$/.test(error.code)
        ? error.code
        : "unexpected_error_response",
    message:
      typeof error?.message === "string"
        ? safeError(error.message).slice(0, 240)
        : "服务端返回非预期错误，请检查对应请求",
    ...(typeof body?.trace_id === "string" &&
    /^[a-f0-9]{32}$/.test(body.trace_id)
      ? { trace_id: body.trace_id }
      : {}),
  };
}
async function call(method, path, body) {
  step(`api ${method} ${path}`);
  assert.ok(allowed(method, path, body), "准备请求超出本轮资源白名单");
  const cost = count(method, path);
  let response;
  try {
    response = await context.request.fetch(`${origin}/api/v1${path}`, {
      method,
      data: body,
      // 准备请求与真实浏览器使用同一来源校验，避免配置缺失时先写入大量夹具。
      headers: { "X-CSRF-Token": csrf, Origin: origin },
      timeout: Math.min(10000, maxTime - (performance.now() - started)),
      maxRetries: 0,
      maxRedirects: 0,
    });
  } catch {
    failure(method, path, "timeout_or_connection", cost);
    throw new Error("API超时或连接中断，已停止后续操作");
  }
  if (!response.ok()) {
    throw failure(method, path, response.status(), cost, {
      ...(await responseDiagnostic(response)),
      request_origin: origin,
      csrf_header_present: !!csrf,
    });
  }
  if (cost) queryInFlight = false;
  const result = await response.json();
  capture(path, result);
  return result;
}
async function installGuards() {
  await context.route("**/*", async (route) => {
    const request = route.request(),
      url = new URL(request.url()),
      method = request.method();
    const path = url.pathname.startsWith("/api/v1/")
      ? url.pathname.slice(7)
      : null;
    let body;
    try {
      body = request.postDataJSON();
    } catch {
      body = undefined;
    }
    if (
      stopped ||
      (url.protocol === "http:" && url.origin !== origin) ||
      !["http:", "data:", "blob:"].includes(url.protocol) ||
      (path && !allowed(method, path, body))
    ) {
      stopped = true;
      summary.blocked_requests.push({ method, path: path ?? url.pathname });
      await route.abort("blockedbyclient");
      return;
    }
    if (!path) {
      if (++summary.static_requests > maxStatic) {
        stopped = true;
        await route.abort("blockedbyclient");
        return;
      }
      await route.continue();
      return;
    }
    let cost;
    try {
      cost = count(method, path);
    } catch {
      stopped = true;
      await route.abort("blockedbyclient");
      return;
    }
    try {
      const response = await route.fetch({
        timeout: Math.min(
          10000,
          Math.max(1, maxTime - (performance.now() - started)),
        ),
        maxRetries: 0,
        maxRedirects: 0,
      });
      if (!response.ok()) {
        const headers = request.headers();
        const diagnostic = {
          ...(await responseDiagnostic(response)),
          request_origin:
            headers.origin === origin
              ? origin
              : headers.origin
                ? "unexpected_origin"
                : null,
          csrf_header_present: !!headers["x-csrf-token"],
          csrf_matches_login: !!csrf && headers["x-csrf-token"] === csrf,
        };
        await route.fulfill({ response });
        failure(method, path, response.status(), cost, diagnostic);
        return;
      } else {
        if (cost) queryInFlight = false;
        if (!path.endsWith("/exports")) capture(path, await response.json());
      }
      await route.fulfill({ response });
    } catch {
      failure(method, path, "timeout_or_connection", cost);
      await route.abort("failed").catch(() => {});
    }
  });
  await context.routeWebSocket("**/*", (socket) => {
    stopped = true;
    summary.blocked_requests.push({ method: "WEBSOCKET" });
    socket.close({ code: 1008, reason: "outside_e2e_scope" });
  });
  context.on("page", (opened) => {
    opened.on("pageerror", (error) => {
      stopped = true;
      summary.page_errors.push(safeError(error));
    });
    opened.on("requestfailed", () => {
      if (!closing) {
        stopped = true;
        summary.network_failure = true;
        if (queryInFlight) summary.requires_query_stop_check = true;
      }
    });
    opened.on("response", (response) => {
      // API错误由路由守卫读取安全code/message；此处只看静态资源，避免重复记录同一403。
      if (
        !closing &&
        response.status() >= 400 &&
        !new URL(response.url()).pathname.startsWith("/api/v1/")
      )
        failure(
          response.request().method(),
          new URL(response.url()).pathname,
          response.status(),
          queryInFlight,
        );
    });
  });
}
function responseFor(method, path) {
  ensureActive();
  return Promise.race([
    failureSignal,
    page.waitForResponse(
      (response) =>
        response.request().method() === method &&
        new URL(response.url()).pathname === `/api/v1${path}`,
    ),
  ]);
}
async function settle() {
  ensureActive();
  await page.waitForFunction(
    () =>
      ![
        ...document.querySelectorAll(
          ".el-loading-mask,.el-message,.el-button.is-loading",
        ),
      ].some((node) => node.getClientRects().length),
  );
  ensureActive();
}
function source(key) {
  return page.locator(
    `[data-source-component=${JSON.stringify(targets[key])}]`,
  );
}
async function screenshot(label, current = page) {
  step(`screenshot ${label}`);
  ensureActive();
  const filename = resolve(output, `${label}.png`);
  await current.screenshot({ path: filename });
  summary.screenshots.push(filename);
}
async function openReport(report) {
  step(`report open ${report.id}`);
  await page.goto(`${origin}/reports?report=${report.id}`);
  await page.locator(".report-studio").waitFor();
  await settle();
  assert.equal(
    await page.locator(".source-widget").count(),
    report.layout.widgets.length,
  );
}
async function preview() {
  step("report preview");
  // 适配属于编辑工具；切换预览后该按钮按产品语义隐藏。
  await page.getByTitle("适配画布", { exact: true }).click();
  await page.getByRole("button", { name: "预览", exact: true }).click();
  await page.locator(".canvas-viewport.is-preview").waitFor();
  await settle();
}
function property(label) {
  return page
    .locator(".report-properties .el-form-item")
    .filter({ has: page.getByText(label, { exact: true }) })
    .first();
}
async function editGauge(key, low, high, thresholds) {
  step(`gauge ${key} select`);
  await page
    .locator(".layer-button")
    .filter({ hasText: widgets.get(key).componentName })
    .click();
  step(`gauge ${key} minimum`);
  await property("仪表下限").locator("input").fill(low);
  step(`gauge ${key} maximum`);
  await property("仪表上限").locator("input").fill(high);
  for (const [index, value] of thresholds.entries()) {
    step(`gauge ${key} stop ${index + 1}`);
    await page.getByRole("button", { name: "添加色段", exact: true }).click();
    await page
      .getByRole("textbox", { name: "色段上限", exact: true })
      .last()
      .fill(value);
    await property(`色段 ${index + 1}`)
      .locator(".el-color-picker__trigger")
      .click();
    const picker = page.locator(".el-color-picker__panel:visible");
    await picker
      .getByRole("textbox")
      .fill(["#36d7b7", "#4a9eff", "#f7c948"][index]);
    await picker.getByRole("textbox").press("Tab");
    await picker.getByRole("button", { name: /^(确定|确认|OK)$/ }).click();
  }
  await property("仪表上限").locator("input").press("Tab");
}
async function gaugeUi() {
  phase = "gauge_range_and_stops_ui";
  await openReport(gaugeReport);
  if (!resume) {
    await editGauge("colorGauge", "-10", "50", ["10.00", "30.00", "50.00"]);
    await editGauge("gradientGauge", "0", "40", ["15.00", "40.00"]);
    step("gauge save_and_refresh");
    const [response] = await Promise.all([
      responseFor("POST", `/reports/${gaugeReport.id}/data-refreshes`),
      page.getByRole("button", { name: "刷新数据", exact: true }).click(),
    ]);
    assert.ok(response.ok());
    gaugeReport = await response.json();
    reports.set(gaugeReport.id, gaugeReport);
  }
  step("gauge trusted_model_and_svg");
  const color = gaugeReport.layout.widgets.find(
    (widget) => widget.component === "JColorGauge",
  );
  const gradient = gaugeReport.layout.widgets.find(
    (widget) => widget.component === "JAntvGauge",
  );
  assert.equal(color.config.option.min, "-10");
  assert.deepEqual(
    color.presentation_model.gauge_stops.map((stop) => stop.value),
    ["10.00", "30.00", "50.00"],
  );
  assert.equal(gradient.presentation_model.gauge_position, "0.3085");
  assert.ok(color.presentation_model.gauge_position.startsWith("0.3723"));
  await preview();
  for (const key of ["colorGauge", "gradientGauge"]) {
    await source(key).locator(".source-chart svg").waitFor();
    assert.ok(
      (await source(key).locator("svg text").allTextContents())
        .join(" ")
        .includes("12.3400"),
    );
    assert.ok((await source(key).locator("svg path").count()) > 4);
  }
  assert.ok(
    (await source("gradientGauge").locator("svg linearGradient").count()) > 0,
  );
  await screenshot("gauges-edited");
  summary.checks.push(
    resume
      ? "复核上一阶段已实际编辑和刷新的两仪表；本阶段仅UI预览精确原值/不同SVG，无重复写入/刷新"
      : "两种仪表真实编辑量程和色段，一次单查询刷新，精确原值与不同SVG显示有效",
  );
}
async function calendarUi(report) {
  phase = "calendar_and_history_ui";
  await openReport(report);
  await preview();
  for (const key of ["permanent", "calendar", "multiCalendar"]) {
    step(`calendar ${key} september_date`);
    const root = source(key);
    assert.equal(
      (await root.locator(".calendar-month").innerText()).trim(),
      "2026年9月",
    );
    await root
      .getByRole("button", { name: "2026-09-29，1项日程", exact: true })
      .click();
    assert.equal(
      (await root.locator(".agenda-name").innerText()).trim(),
      "月底复核",
    );
    step(`calendar ${key} october_date`);
    await root.getByRole("button", { name: "下个月", exact: true }).click();
    await root
      .getByRole("button", { name: "2026-10-01，1项日程", exact: true })
      .click();
    assert.equal(
      (await root.locator(".agenda-name").innerText()).trim(),
      "一线复检",
    );
    if (key === "multiCalendar") {
      step("calendar multiCalendar agenda");
      await root.getByRole("button", { name: "日程", exact: true }).click();
      assert.equal(await root.locator(".agenda-name").count(), 3);
      await root.getByRole("button", { name: "月", exact: true }).click();
    }
    await root.getByRole("button", { name: "上个月", exact: true }).click();
    assert.equal(
      (await root.locator(".calendar-month").innerText()).trim(),
      "2026年9月",
    );
    await root.getByRole("button", { name: "下个月", exact: true }).click();
    await root
      .getByRole("button", { name: "2026-10-01，1项日程", exact: true })
      .click();
  }
  const names = await source("history")
    .locator(".history-name")
    .allTextContents();
  assert.deepEqual(names, ["月底复核", "一线复检", "二线维护", "三线巡检"]);
  assert.ok((await source("history").innerText()).includes("2026-10-01 00:30"));
  assert.ok(!(await source("history").innerText()).includes("T16:30:00Z"));
  await screenshot("calendars-history");
  const current = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "numeric",
  }).formatToParts(new Date());
  const currentMonth = `${current.find((part) => part.type === "year").value}年${Number(current.find((part) => part.type === "month").value)}月`;
  for (const key of ["permanent", "calendar", "multiCalendar"]) {
    step(`calendar ${key} today`);
    await source(key)
      .getByRole("button", { name: "今天", exact: true })
      .click();
    assert.equal(
      (await source(key).locator(".calendar-month").innerText()).trim(),
      currentMonth,
    );
  }
  summary.checks.push(
    "三日历真实切月/选自然日/今天，多视图切日程；UTC跨日定位与历程实际时间排序有效，未额外取数",
  );
}
async function cardsAndBarsUi(report) {
  phase = "four_card_sequences_and_bars_ui";
  await openReport(report);
  await preview();
  for (const key of ["capsule", "background"]) {
    step(`chart ${key} svg`);
    await source(key).locator(".source-chart svg").waitFor();
    assert.ok((await source(key).locator("svg path").count()) > 3);
  }
  for (const key of ["horizontal", "vertical", "highlight", "carousel"]) {
    step(`cards ${key} pagination`);
    const root = source(key),
      carousel = key === "carousel";
    assert.equal(
      (await root.locator(".card-page").innerText()).trim(),
      carousel ? "1 / 4" : "1 / 2",
    );
    assert.equal(
      await root.locator(".sequence-card").count(),
      carousel ? 1 : 3,
    );
    await root.getByRole("button", { name: "下一页卡片", exact: true }).click();
    assert.equal(
      (await root.locator(".card-name").first().innerText()).trim(),
      carousel ? "二线维护" : "月底复核",
    );
    if (key === "vertical") {
      assert.ok((await root.locator(".card-sequence.vertical").count()) === 1);
      assert.equal(
        (await root.locator(".card-index").first().innerText()).trim(),
        "04",
      );
    }
    if (key === "highlight")
      assert.equal(await root.locator(".highlight .active").count(), 1);
    if (carousel)
      assert.equal(await root.locator(".card-sequence.carousel").count(), 1);
    await root.getByRole("button", { name: "上一页卡片", exact: true }).click();
    assert.equal(
      (await root.locator(".card-name").first().innerText()).trim(),
      "一线复检",
    );
    assert.ok((await root.innerText()).includes("12.3400"));
  }
  await screenshot("bars-four-cards");
  summary.checks.push(
    "胶囊/背景柱真实SVG；三滚动变体与轮播真实翻页，原始精度、序号、方向和高亮有效，未额外取数",
  );
}
async function exportUi(report, label) {
  phase = `html_export_${label}`;
  step(`html ${label} return_to_list`);
  await page
    .locator(".report-toolbar")
    .getByRole("button", { name: "返回", exact: true })
    .click();
  const row = page.locator(".el-table__row").filter({ hasText: report.name });
  await row.getByRole("button", { name: "导出", exact: true }).click();
  step(`html ${label} download`);
  const [response, download] = await Promise.all([
    responseFor("POST", `/reports/${report.id}/exports`),
    page.waitForEvent("download"),
    page.getByRole("menuitem", { name: "HTML", exact: true }).click(),
  ]);
  assert.ok(response.ok());
  assert.equal(await download.failure(), null);
  assert.ok(download.suggestedFilename().endsWith(".html"));
  const stream = await download.createReadStream(),
    data = [];
  let size = 0;
  assert.ok(stream);
  for await (const chunk of stream) {
    size += chunk.length;
    assert.ok(size <= 2 * 1024 * 1024);
    data.push(chunk);
  }
  const html = Buffer.concat(data).toString("utf8");
  await download.delete();
  step(`html ${label} validate_and_render`);
  assert.ok(html.includes('charset="utf-8"') && !/<script\b/i.test(html));
  if (label === "gauges")
    assert.ok(
      html.includes('data-gauge="segment"') &&
        html.includes('data-gauge="gradient"') &&
        html.includes('data-value="12.3400"'),
    );
  if (label === "calendar")
    assert.ok(
      html.includes("source-history") &&
        html.includes('data-date="2026-09-29"') &&
        html.includes('data-date="2026-09-30"'),
    );
  if (label === "cards")
    assert.ok(
      html.includes('data-background="capsule"') &&
        html.includes('data-background="column"') &&
        html.includes('data-mode="carousel"') &&
        html.includes("source-cards vertical") &&
        html.includes("source-cards horizontal highlight"),
    );
  const exported = await context.newPage();
  await exported.setContent(html, { waitUntil: "load", timeout: 10000 });
  assert.equal(
    await exported.locator("section[data-widget]").count(),
    report.layout.widgets.length,
  );
  for (const widget of report.layout.widgets)
    assert.equal(
      await exported
        .locator(`[data-widget=${JSON.stringify(widget.id)}]`)
        .count(),
      1,
    );
  assert.equal(await exported.locator("script").count(), 0);
  await screenshot(`export-${label}`, exported);
  await exported.close();
  summary.checks.push(
    `${report.layout.widgets.length}个${label}来源组件从真实UI下载HTML并离线渲染，形态与精确事实通过`,
  );
}
async function prepareReport(label, placements) {
  const rows = placements.map(([key, x, y, w, h]) => {
    const original = widgets.get(key),
      query = ["colorGauge", "gradientGauge"].includes(key)
        ? gaugeQuery
        : allQuery;
    return {
      ...structuredClone(original),
      x,
      y,
      w,
      h,
      binding: {
        query_id: query.id,
        label_field: "name",
        value_field: "reading",
        time_field: "observed",
        description_field: "note",
      },
      data: structuredClone(query.rows),
      data_provenance: {
        type: "query",
        query_id: query.id,
        datasource_id: query.datasource_id,
        truncated: query.truncated,
      },
      config: {
        ...original.config,
        chartDataParsed: structuredClone(query.rows),
        dataStatus: "bound",
      },
    };
  });
  const report = await call("POST", "/reports", {
    name: `${prefix}-${label}`,
    layout: {
      canvas: {
        width: 1400,
        height: label === "仪表" ? 700 : 930,
        backgroundColor: "#07182d",
      },
      widgets: rows,
    },
  });
  assert.ok(
    report.layout.widgets.every(
      (widget) => widget.presentation_model?.status === "ready",
    ),
  );
  return report;
}

async function loadReviewedFixtures() {
  phase = "reviewed_resume_readonly_checks";
  const ids = resume.fixture_ids;
  for (const id of [
    ids.dataset,
    ids.all_query,
    ids.gauge_query,
    ...ids.reports,
  ])
    assert.match(id, /^[a-f0-9]{32}$/);
  assert.equal(new Set(ids.reports).size, 3);
  dataset = await call("GET", `/datasets/${ids.dataset}`);
  assert.equal(dataset.name, `${resume.previous_prefix}-csv`);
  assert.equal(dataset.row_count, 4);
  assert.equal(dataset.source_kind, "csv_import");
  const dataSource = await call("GET", `/datasources/${dataset.datasource_id}`);
  assert.equal(dataSource.provider, "sqlite");
  assert.equal(dataSource.status, "active");
  assert.ok(!dataSource.connection_config?.host);
  assert.ok(
    dataSource.schema_snapshot.some(
      (table) => table.tableName === dataset.table_name,
    ),
  );
  allQuery = await call("GET", `/queries/${ids.all_query}`);
  gaugeQuery = await call("GET", `/queries/${ids.gauge_query}`);
  for (const [query, single] of [
    [allQuery, false],
    [gaugeQuery, true],
  ]) {
    assert.equal(query.datasource_id, dataset.datasource_id);
    assert.equal(query.sql, querySql(single));
    assert.equal(query.truncated, false);
    assert.equal(query.rows.length, single ? 1 : 4);
  }
  const catalog = await call("GET", "/report-components/catalog");
  assert.equal(
    catalog.items.filter((item) => item.renderer_supported).length,
    266,
  );
  for (const id of ids.reports) reports.set(id, { id });
  const loaded = [];
  const seenSources = new Set();
  for (const [index, id] of ids.reports.entries()) {
    const report = await call("GET", `/reports/${id}`);
    assert.equal(
      report.name,
      `${resume.previous_prefix}-${["仪表", "日期", "图表卡片"][index]}`,
    );
    assert.equal(report.layout.widgets.length, [2, 4, 6][index]);
    assert.ok(!report.layout.kind);
    for (const widget of report.layout.widgets) {
      const key = Object.keys(targets).find(
        (key) => targets[key] === widget.source_component_id,
      );
      assert.ok(key && !seenSources.has(widget.source_component_id));
      seenSources.add(widget.source_component_id);
      assert.equal(
        widget.component,
        widget.source_component_id.split(":").at(-1),
      );
      assert.equal(widget.componentName, `${resume.previous_prefix}-${key}`);
      const query = ["colorGauge", "gradientGauge"].includes(key)
        ? gaugeQuery
        : allQuery;
      assert.deepEqual(widget.binding, {
        query_id: query.id,
        label_field: "name",
        value_field: "reading",
        time_field: "observed",
        description_field: "note",
      });
      assert.equal(widget.data_provenance.datasource_id, dataset.datasource_id);
      assert.equal(widget.data_provenance.truncated, false);
      assert.deepEqual(widget.data, query.rows);
      assert.equal(widget.presentation_model?.status, "ready");
      assert.equal(widget.presentation_model.component, widget.component);
      widgets.set(key, widget);
      widgets.set(widget.id, widget);
    }
    loaded.push(report);
  }
  assert.equal(seenSources.size, 12);
  summary.fixture_ids = structuredClone(ids);
  summary.checks.push(
    "已核受审原3报告/12来源、原新CSV和两已存查询、当前源权限及已保存模型；仅GET读取，没有重建/刷新/执行业务SQL",
  );
  return loaded;
}

try {
  browser = await chromium.launch({
    headless: true,
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
  });
  context = await browser.newContext({
    viewport: { width: 1900, height: 1200 },
    reducedMotion: "reduce",
    serviceWorkers: "block",
    acceptDownloads: true,
  });
  await installGuards();
  const account = await call("POST", "/auth/login", {
    username: input.username,
    password: input.password,
  });
  csrf = account.csrf_token;
  assert.ok(
    account.permissions.includes("*"),
    "只使用本轮隔离管理员，不更改账号权限",
  );
  if (resume) {
    [gaugeReport, calendars, cards] = await loadReviewedFixtures();
  } else {
    phase = "new_csv_and_two_queries";
    dataset = await call("POST", "/datasets", {
      name: `${prefix}-csv`,
      csv_text:
        "name,reading,observed,note,sequence\n一线复检,12.3400,2026-09-30T16:30:00Z,本轮检查证据甲,1\n二线维护,-2.5000,2026-10-03T08:10:00+08:00,本轮检查证据乙,2\n三线巡检,,2026-10-05,本轮检查证据丙,3\n月底复核,8.0000,2026-09-29T08:10:00+08:00,本轮检查证据丁,4\n",
    });
    allQuery = await call("POST", "/queries", {
      name: `${prefix}-全行`,
      datasource_id: dataset.datasource_id,
      sql: querySql(),
      max_rows: 10,
    });
    gaugeQuery = await call("POST", "/queries", {
      name: `${prefix}-仪表`,
      datasource_id: dataset.datasource_id,
      sql: querySql(true),
      max_rows: 10,
    });
    assert.equal(allQuery.rows.length, 4);
    assert.equal(gaugeQuery.rows.length, 1);
    assert.equal(allQuery.truncated, false);
    assert.equal(gaugeQuery.truncated, false);
    const catalog = await call("GET", "/report-components/catalog");
    assert.equal(
      catalog.items.filter((item) => item.renderer_supported).length,
      266,
    );
    for (const [key, id] of Object.entries(targets)) {
      assert.ok(
        catalog.items.some((item) => item.id === id && item.renderer_supported),
      );
      const widget = await call("POST", "/report-component-instances", {
        source_id: id,
      });
      assert.equal(widget.source_component_id, id);
      assert.equal(widget.config.chartDataParsed.length, 0);
      widget.componentName = `${prefix}-${key}`;
      widgets.set(key, widget);
      widgets.set(widget.id, widget);
    }
    gaugeReport = await prepareReport("仪表", [
      ["colorGauge", 60, 100, 620, 540],
      ["gradientGauge", 720, 100, 620, 540],
    ]);
    calendars = await prepareReport("日期", [
      ["permanent", 20, 20, 660, 420],
      ["calendar", 720, 20, 660, 420],
      ["multiCalendar", 20, 480, 660, 420],
      ["history", 720, 480, 660, 420],
    ]);
    cards = await prepareReport("图表卡片", [
      ["capsule", 20, 20, 660, 280],
      ["background", 720, 20, 660, 280],
      ["horizontal", 20, 330, 660, 250],
      ["vertical", 720, 330, 660, 250],
      ["highlight", 20, 620, 660, 250],
      ["carousel", 720, 620, 660, 250],
    ]);
    summary.fixture_ids = {
      dataset: dataset.id,
      all_query: allQuery.id,
      gauge_query: gaugeQuery.id,
      reports: [gaugeReport.id, calendars.id, cards.id],
    };
  }
  page = await context.newPage();
  page.setDefaultTimeout(10000);
  page.setDefaultNavigationTimeout(10000);
  await gaugeUi();
  await exportUi(gaugeReport, "gauges");
  await calendarUi(calendars);
  await exportUi(calendars, "calendar");
  await cardsAndBarsUi(cards);
  await exportUi(cards, "cards");
  ensureActive();
  assert.equal(summary.initial_queries, resume ? 0 : 2);
  assert.equal(summary.report_refreshes, resume ? 0 : 1);
  assert.equal(summary.actual_query_upper_bound, resume ? 0 : 3);
  assert.equal(summary.datasets, resume ? 0 : 1);
  assert.equal(summary.instances, resume ? 0 : 12);
  assert.equal(summary.api_failures.length, 0);
  assert.equal(summary.blocked_requests.length, 0);
  assert.equal(summary.page_errors.length, 0);
  assert.ok(!summary.network_failure);
  summary.passed = true;
} catch (error) {
  stopped = true;
  summary.passed = false;
  summary.failed_phase = phase;
  summary.error_code = "report_source_ui_failed";
  summary.error = safeError(
    summary.first_failure ? failureMessage(summary.first_failure) : error,
  );
  if (summary.first_failure) summary.secondary_wait_error = safeError(error);
  if (queryInFlight) summary.requires_query_stop_check = true;
} finally {
  clearTimeout(timeout);
  closing = true;
  await browser?.close().catch(() => {});
  csrf = "";
  input.password = "";
  input.username = "";
  summary.elapsed_seconds =
    Math.round((performance.now() - started) / 100) / 10;
  summary.cumulative_api_requests =
    summary.api_requests + (resume?.previous_api_requests ?? 0);
  summary.cumulative_actual_query_upper_bound =
    summary.actual_query_upper_bound +
    (resume?.previous_actual_query_upper_bound ?? 0);
  summary.cumulative_elapsed_seconds =
    Math.round(
      (summary.elapsed_seconds + (resume?.previous_elapsed_seconds ?? 0)) * 10,
    ) / 10;
  summary.receipt = resolve(output, "acceptance.json");
  await writeFile(summary.receipt, JSON.stringify(summary, null, 2));
  console.log(JSON.stringify(summary));
}
if (!summary.passed) process.exitCode = 1;
