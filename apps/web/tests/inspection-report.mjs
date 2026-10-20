/**
 * 用本轮新建SQLite数据验证“产品三维质检→复核→正式报告”，不读取旧库或外部数据源。
 * 一个浏览器、两个隔离会话、70次API/120秒；仅一次初始查询、导入、判定、复核和生成。
 * 导入会重执行已登记查询，因此真实SQL上界为两次；报告操作只投影不可变运行，不重查业务源。
 * 协调者核实本地平台归属后手动运行；脚本不启动服务，账号仅从环境读取且不落盘。
 */
import { chromium } from "playwright";
import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";

const origin = process.env.E2E_ORIGIN ?? "http://127.0.0.1:5173";
if (!/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(origin)) {
  throw new Error("只允许协调者已核实的本机隔离平台");
}
if (!process.env.E2E_USERNAME || !process.env.E2E_PASSWORD) {
  throw new Error("请通过环境变量提供本轮隔离平台管理员账号");
}

const started = performance.now();
const suffix = Date.now().toString(36);
const summary = {
  scope: "本轮新SQLite/新CSV；一次初始查询及一次导入取数；正式报告只读运行投影",
  started_at: new Date().toISOString(),
  origin,
  checks: [],
  api_requests: 0,
  operations: { queries: 0, imports: 0, runs: 0, reviews: 0, generations: 0 },
  actual_query_upper_bound: 0,
  api_failures: [],
  blocked_requests: [],
  page_errors: [],
  fixture_ids: {},
};
let browser;
let context;
let page;
let csrf = "";
let inspection;
let report;
let stopped = false;
let queryInFlight = false;
const deadline = setTimeout(() => {
  stopped = true;
  summary.budget_exceeded = true;
  if (queryInFlight) summary.requires_query_stop_check = true;
  if (browser) void browser.close().catch(() => {});
}, 120000);

function ensureActive() {
  if (stopped || performance.now() - started >= 120000) {
    stopped = true;
    throw new Error("验证已停止或达到120秒预算，不继续发起操作");
  }
}

function operationFor(method, path) {
  if (method !== "POST") return undefined;
  if (path === "/queries") return "queries";
  if (path === "/inspection-report-generations") return "generations";
  return path.match(/^\/inspections\/[^/]+\/(imports|runs|reviews)$/)?.[1];
}

function countRequest(method, path) {
  ensureActive();
  if (summary.api_requests >= 70) {
    stopped = true;
    throw new Error("达到70次API请求预算");
  }
  summary.api_requests++;
  const operation = operationFor(method, path);
  if (operation) {
    assert.equal(summary.operations[operation], 0, `${operation}只允许一次`);
    summary.operations[operation]++;
    if (operation === "queries" || operation === "imports") {
      summary.actual_query_upper_bound++;
      queryInFlight = true;
    }
  }
}

async function call(method, path, data) {
  countRequest(method, path);
  let response;
  try {
    response = await context.request.fetch(`${origin}/api/v1${path}`, {
      method,
      headers: { "X-CSRF-Token": csrf },
      data,
      timeout: 10000,
      maxRetries: 0,
    });
  } catch {
    stopped = true;
    if (queryInFlight) summary.requires_query_stop_check = true;
    // 原始请求异常可能包含Cookie/CSRF，产物只保留操作和失败类别。
    throw new Error(`${method} ${path} 超时或连接中断，停止后续操作`);
  }
  if (["queries", "imports"].includes(operationFor(method, path)))
    queryInFlight = false;
  if (!response.ok()) {
    stopped = true;
    summary.api_failures.push({ method, path, status: response.status() });
    throw new Error(`${method} ${path} 返回${response.status()}`);
  }
  return response.json();
}

function allowedPageRequest(method, path, payload) {
  if (method === "GET") {
    return (
      [
        "/auth/status",
        "/me",
        "/permissions/navigation",
        "/inspections",
      ].includes(path) ||
      (!!inspection &&
        new RegExp(
          `^/inspections/${inspection.id}(?:/(?:samples|results|runs))?$`,
        ).test(path)) ||
      (!!report && path === `/reports/${report.id}`)
    );
  }
  if (method === "POST" && path === "/inspection-report-generations") {
    return (
      payload?.inspection_id === inspection?.id &&
      payload?.run_id === inspection?.result?.run_id &&
      payload?.inspection_revision === inspection?.revision
    );
  }
  if (!report) return false;
  if (method === "PATCH" && path === `/reports/${report.id}`) {
    return (
      Object.keys(payload ?? {})
        .sort()
        .join(",") === "name,revision"
    );
  }
  return (
    method === "POST" &&
    [
      `/reports/${report.id}/data-refreshes`,
      `/reports/${report.id}/publications`,
      `/reports/${report.id}/exports`,
    ].includes(path)
  );
}

async function installPageGuards() {
  await context.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    if (url.protocol.startsWith("http") && url.origin !== origin) {
      stopped = true;
      summary.blocked_requests.push({
        method,
        path: `${url.origin}${url.pathname}`,
      });
      await route.abort("blockedbyclient");
      return;
    }
    if (!url.pathname.startsWith("/api/v1/")) {
      await route.continue();
      return;
    }
    const path = url.pathname.slice("/api/v1".length);
    let payload;
    if (["POST", "PATCH", "PUT"].includes(method)) {
      try {
        payload = request.postDataJSON();
      } catch {
        payload = undefined;
      }
    }
    if (!allowedPageRequest(method, path, payload)) {
      stopped = true;
      summary.blocked_requests.push({ method, path });
      await route.abort("blockedbyclient");
      return;
    }
    try {
      countRequest(method, path);
    } catch {
      stopped = true;
      await route.abort("blockedbyclient");
      return;
    }
    if (method === "POST" && path === "/inspection-report-generations") {
      try {
        // 先接收唯一生成响应，再放行页面跳转，确保后续白名单只包含服务端返回的报告。
        const response = await route.fetch({
          timeout: 10000,
          maxRetries: 0,
          maxRedirects: 0,
        });
        if (response.ok()) report = await response.json();
        else stopped = true;
        await route.fulfill({ response });
      } catch {
        stopped = true;
        summary.api_failures.push({
          method,
          path,
          failure: "timeout_or_connection",
        });
        await route.abort("failed").catch(() => {});
      }
      return;
    }
    await route.continue();
  });
  page.on("pageerror", (error) => {
    stopped = true;
    summary.page_errors.push(error.message);
  });
  page.on("response", (response) => {
    const path = new URL(response.url()).pathname;
    if (path.startsWith("/api/v1/") && response.status() >= 400) {
      stopped = true;
      summary.api_failures.push({ path, status: response.status() });
    }
  });
}

function waitResponse(method, path) {
  ensureActive();
  return page.waitForResponse(
    (response) =>
      response.request().method() === method &&
      new URL(response.url()).pathname === `/api/v1${path}`,
  );
}

async function settleUi() {
  ensureActive();
  await page.waitForFunction(
    () =>
      ![
        ...document.querySelectorAll(
          ".el-loading-mask,.el-message,.el-button.is-loading",
        ),
      ].some((element) => element.getClientRects().length > 0),
  );
  ensureActive();
}

async function documentText(expectedName) {
  ensureActive();
  const frame = page.frameLocator('iframe[title="质检报告正文"]');
  await frame
    .getByRole("heading", { name: expectedName, exact: true })
    .waitFor();
  const text = await frame.locator("body").innerText();
  assert.ok(text.includes("数值检验明细"));
  assert.ok(text.includes("成分检验明细"));
  assert.ok(text.includes("外观检验明细"));
  return text;
}

const csvCell = (value) => `"${String(value).replaceAll('"', '""')}"`;
await mkdir("test-results", { recursive: true });
try {
  browser = await chromium.launch({
    headless: true,
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE }
      : {}),
  });
  context = await browser.newContext();
  const admin = await call("POST", "/auth/login", {
    username: process.env.E2E_USERNAME,
    password: process.env.E2E_PASSWORD,
  });
  csrf = admin.csrf_token;
  const product = await call("POST", "/products", {
    name: `报告验收饮品${suffix}`,
    code: `R-${suffix}`,
  });
  const batch = await call("POST", "/product-batches", {
    name: `报告验收批次${suffix}`,
    external_ref: `BATCH-${suffix}`,
    product_id: product.id,
    quantity: 2,
  });
  const standard = await call("POST", "/inspection-templates", {
    name: `饮品三维标准${suffix}`,
    target_type: "product",
    metrics: [
      {
        id: "weight",
        name: "净重",
        category: "numeric",
        unit: "g",
        standard_min: "98",
        standard_max: "102",
      },
      {
        id: "protein",
        name: "蛋白质含量",
        category: "component",
        component_name: "蛋白质",
        unit: "%",
        standard_min: "10",
        standard_max: "14",
      },
      {
        id: "appearance",
        name: "包装外观",
        category: "appearance",
        minimum_confidence: "0.8",
        blocking_severities: ["moderate", "severe"],
      },
    ],
  });
  const rows = [
    ["sample_id", "weight", "protein", "defects", "image_ref"],
    [
      "REPORT-A01",
      "100.000000000000000001",
      "12",
      "[]",
      "db://local-fixture/A01",
    ],
    [
      "REPORT-A02",
      "104",
      "16",
      JSON.stringify([
        { defect_type: "封口破损", confidence: "0.98", severity: "severe" },
      ]),
      "db://local-fixture/A02",
    ],
  ];
  const dataset = await call("POST", "/datasets", {
    name: `本轮报告验收数据${suffix}`,
    csv_text: rows.map((row) => row.map(csvCell).join(",")).join("\n"),
  });
  const query = await call("POST", "/queries", {
    name: `本轮报告验收查询${suffix}`,
    datasource_id: dataset.datasource_id,
    sql: `SELECT * FROM "${dataset.table_name}"`,
    parameters: {},
    max_rows: 3,
  });
  assert.equal(query.truncated ?? query.result?.truncated, false);
  inspection = await call("POST", "/inspections", {
    name: `饮品出厂报告验收${suffix}`,
    target_type: "product",
    product_id: product.id,
    batch_id: batch.id,
    template_id: standard.id,
    template_revision: standard.revision,
    expected_samples: 2,
  });
  const imported = await call("POST", `/inspections/${inspection.id}/imports`, {
    revision: inspection.revision,
    query_id: query.id,
    sample_field: "sample_id",
    mappings: [
      { metric_id: "weight", value_field: "weight", unit: "g" },
      { metric_id: "protein", value_field: "protein", unit: "%" },
      {
        metric_id: "appearance",
        defects_field: "defects",
        image_ref_field: "image_ref",
      },
    ],
  });
  assert.equal(imported.imported_samples, 2);
  inspection = await call("POST", `/inspections/${inspection.id}/runs`, {
    revision: imported.revision,
  });
  assert.equal(inspection.outcome, "failed");
  assert.equal(inspection.result.samples.length, 2);
  const reviewNote = "已核查两份原始样本，净重、成分及封口缺陷按标准确认";
  inspection = (
    await call("POST", `/inspections/${inspection.id}/reviews`, {
      revision: inspection.revision,
      outcome: "failed",
      note: reviewNote,
    })
  ).inspection;
  assert.ok(inspection.result.run_id);
  summary.fixture_ids = {
    product_id: product.id,
    dataset_id: dataset.id,
    query_id: query.id,
    inspection_id: inspection.id,
    run_id: inspection.result.run_id,
  };
  summary.checks.push(
    "两次实际SQL完成三维数据库映射、单次判定及复核，精度字符串保留",
  );

  const role = await call("POST", "/roles", {
    name: `报告只读质检验收${suffix}`,
    permissions: [
      "m01.read",
      "m05.read",
      "m06.read",
      "m07.read",
      "m07.write",
      "m07.publish",
      "m11.read",
    ],
  });
  const limitedUsername = `report-reader-${suffix}`;
  const limitedPassword = randomBytes(24).toString("base64url");
  await call("POST", "/members", {
    username: limitedUsername,
    password: limitedPassword,
    display_name: "报告验收人员",
    role_ids: [role.id],
  });
  await context.close();
  context = await browser.newContext({
    viewport: { width: 1600, height: 1100 },
    reducedMotion: "reduce",
  });
  const limited = await call("POST", "/auth/login", {
    username: limitedUsername,
    password: limitedPassword,
  });
  csrf = limited.csrf_token;
  assert.ok(
    limited.permissions.includes("m11.read") &&
      limited.permissions.includes("m07.write"),
  );
  assert.ok(
    !limited.permissions.some((permission) =>
      ["*", "m11.*", "m11.write"].includes(permission),
    ),
  );
  page = await context.newPage();
  page.setDefaultTimeout(10000);
  await installPageGuards();
  await page.goto(`${origin}/product-check`);
  await page
    .getByRole("button", { name: inspection.name, exact: true })
    .click();
  await settleUi();
  assert.equal(
    await page.getByRole("button", { name: "执行检测", exact: true }).count(),
    0,
  );
  await page.getByRole("button", { name: "生成报告", exact: true }).click();
  const dialog = page.locator(".el-dialog:visible");
  await dialog.locator(".el-select").waitFor();
  const displayedRun = await dialog
    .locator(".el-select__placeholder")
    .innerText();
  assert.ok(displayedRun.includes("第1次") && displayedRun.includes("不合格"));
  assert.ok(!/[0-9a-f]{8}-[0-9a-f]{4}-/i.test(displayedRun));
  const reportName = `饮品正式报告${suffix}`;
  await dialog
    .locator(".el-form-item")
    .filter({ hasText: "报告名称" })
    .locator("input")
    .fill(reportName);
  const generated = waitResponse("POST", "/inspection-report-generations");
  await dialog.getByRole("button", { name: "生成报告", exact: true }).click();
  const generationResponse = await generated;
  assert.ok(generationResponse.ok());
  report = await generationResponse.json();
  assert.equal(report.layout.kind, "inspection_report");
  assert.equal(
    report.layout.inspection_source.run_id,
    inspection.result.run_id,
  );
  assert.equal(
    report.layout.inspection_source.review_snapshot[0].note,
    reviewNote,
  );
  summary.fixture_ids.report_id = report.id;
  await page.waitForURL(
    (url) =>
      url.pathname === "/reports" &&
      url.searchParams.get("report") === report.id,
  );
  await page.locator(".inspection-report-view").waitFor();
  assert.equal(
    await page
      .locator(".report-studio,.report-properties,.screen-widget")
      .count(),
    0,
  );
  assert.equal(
    await page.locator('iframe[title="质检报告正文"]').getAttribute("sandbox"),
    "",
  );
  const body = await documentText(reportName);
  for (const text of [
    product.name,
    batch.external_ref,
    standard.name,
    "REPORT-A01",
    "REPORT-A02",
    "100.000000000000000001",
    "封口破损",
    reviewNote,
  ]) {
    assert.ok(body.includes(text), `正式正文缺少${text}`);
  }
  summary.checks.push(
    "只读质检人员可选择准确运行生成正式报告；正文含三维明细、标准、精度和复核事实",
  );

  const source = structuredClone(report.layout.inspection_source);
  const renamed = `${reportName}·出厂版`;
  await page
    .getByRole("textbox", { name: "报告名称", exact: true })
    .fill(renamed);
  assert.equal(
    await page.getByRole("button", { name: "发布", exact: true }).isDisabled(),
    true,
  );
  const nameSaving = waitResponse("PATCH", `/reports/${report.id}`);
  await page.getByRole("button", { name: "保存名称", exact: true }).click();
  const nameResponse = await nameSaving;
  assert.ok(nameResponse.ok());
  assert.deepEqual(Object.keys(nameResponse.request().postDataJSON()).sort(), [
    "name",
    "revision",
  ]);
  report = await nameResponse.json();
  await documentText(renamed);
  await settleUi();
  const refreshing = waitResponse(
    "POST",
    `/reports/${report.id}/data-refreshes`,
  );
  await page.getByRole("button", { name: "刷新内容", exact: true }).click();
  const refreshResponse = await refreshing;
  assert.ok(refreshResponse.ok());
  const refreshed = await refreshResponse.json();
  assert.ok(refreshed.revision > report.revision);
  assert.equal(refreshed.layout.inspection_source.run_id, source.run_id);
  assert.equal(
    refreshed.layout.inspection_source.result_hash,
    source.result_hash,
  );
  assert.deepEqual(
    refreshed.layout.inspection_source.review_snapshot,
    source.review_snapshot,
  );
  report = refreshed;
  await documentText(renamed);
  await settleUi();
  const publishing = waitResponse("POST", `/reports/${report.id}/publications`);
  await page.getByRole("button", { name: "发布", exact: true }).click();
  const publicationResponse = await publishing;
  assert.ok(publicationResponse.ok());
  report = await publicationResponse.json();
  assert.equal(report.status, "published");
  assert.equal(report.layout.inspection_source.result_hash, source.result_hash);
  await documentText(renamed);
  await settleUi();
  summary.checks.push("仅名称PATCH；刷新保留指定运行事实；准确复核后真实发布");

  await page.getByRole("button", { name: "导出", exact: true }).click();
  const options = page.locator(
    ".el-dropdown-menu:visible .el-dropdown-menu__item",
  );
  await options.first().waitFor();
  const formats = (await options.allTextContents()).map((text) => text.trim());
  assert.deepEqual(
    formats.sort(),
    ["CSV", "DOCX", "HTML", "JSON", "PDF", "PPTX", "XLSX"].sort(),
  );
  const downloading = page.waitForEvent("download");
  await page.getByRole("menuitem", { name: "DOCX", exact: true }).click();
  const download = await downloading;
  assert.equal(await download.failure(), null);
  await download.saveAs("test-results/inspection-report.docx");
  const bytes = await readFile("test-results/inspection-report.docx");
  assert.equal(bytes.subarray(0, 4).toString("hex"), "504b0304");
  await settleUi();
  await page.screenshot({ path: "test-results/inspection-report.png" });
  await page.reload();
  await documentText(renamed);
  await settleUi();
  assert.equal(
    await page.locator(".report-studio,.report-properties").count(),
    0,
  );
  summary.checks.push(
    "正式DOCX真实下载；有效无加载截图；刷新深链接仍打开只读正式正文",
  );
  assert.deepEqual(summary.operations, {
    queries: 1,
    imports: 1,
    runs: 1,
    reviews: 1,
    generations: 1,
  });
  assert.equal(summary.actual_query_upper_bound, 2);
  assert.equal(summary.page_errors.length, 0);
  assert.equal(summary.api_failures.length, 0);
  assert.equal(summary.blocked_requests.length, 0);
  ensureActive();
  summary.passed = true;
} catch (error) {
  stopped = true;
  summary.passed = false;
  if (queryInFlight) summary.requires_query_stop_check = true;
  summary.error = error instanceof Error ? error.message : "验收失败";
  if (page && !page.isClosed())
    await page
      .screenshot({ path: "test-results/inspection-report-failure.png" })
      .catch(() => {});
} finally {
  clearTimeout(deadline);
  summary.elapsed_seconds =
    Math.round((performance.now() - started) / 100) / 10;
  await browser?.close().catch(() => {});
  await writeFile(
    "test-results/inspection-report.json",
    JSON.stringify(summary, null, 2),
  );
  console.log(JSON.stringify(summary, null, 2));
}
if (!summary.passed) process.exitCode = 1;
