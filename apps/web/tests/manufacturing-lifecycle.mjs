/**
 * 复用inspection-report验收保护，验证本轮隔离SQLite中的制造处置页面。
 * 一个浏览器、两个真实人类会话，120次业务HTTP/180秒/最多6次业务取数。
 * 单一新CSV区分首检/复检记录；API准备夹具，UI提交处置、创建复检、提交与独立关闭。
 * 本文件不启动服务；协调者核实本机平台及新数据库归属后手动运行，不自动重试。
 */
import { chromium } from "playwright";
import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";

const origin = process.env.E2E_ORIGIN ?? "http://127.0.0.1:5173";
if (!/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(origin))
  throw new Error("只允许协调者已核实的本机隔离平台");
if (!process.env.E2E_USERNAME || !process.env.E2E_PASSWORD)
  throw new Error("请用环境变量提供本轮隔离平台管理员账号");

const started = performance.now(),
  suffix = Date.now().toString(36);
const summary = {
  scope: "本轮新SQLite/单一新CSV；首检和复检分开取数；两名人类账号操作质量闭环",
  origin,
  started_at: new Date().toISOString(),
  checks: [],
  api_requests: 0,
  actual_query_upper_bound: 0,
  operations: {
    queries: 0,
    imports: 0,
    runs: 0,
    reviews: 0,
    reinspections: 0,
    ui_quality_actions: [],
  },
  blocked_requests: [],
  api_failures: [],
  page_errors: [],
  fixture_ids: {},
  screenshots: [],
};
let browser,
  context,
  page,
  csrf = "",
  stopped = false,
  queryInFlight = false;
let inspection, child, qualityCase, workOrder, rule, query, retestQuery;
const deadline = setTimeout(() => {
  stopped = true;
  summary.budget_exceeded = true;
  if (queryInFlight) summary.requires_query_stop_check = true;
  void browser?.close().catch(() => {});
}, 180000);

function ensureActive() {
  if (stopped || performance.now() - started >= 180000) {
    stopped = true;
    throw new Error("验证已停止或达到180秒预算，不继续发送请求");
  }
}
function timeout() {
  ensureActive();
  return Math.max(1, Math.min(10000, 180000 - (performance.now() - started)));
}
function operationFor(method, path) {
  if (method !== "POST") return undefined;
  if (path === "/queries") return "queries";
  return path.match(
    /^\/inspections\/[^/]+\/(imports|runs|reviews|reinspections)$/,
  )?.[1];
}
function countRequest(method, path) {
  ensureActive();
  if (summary.api_requests >= 120) {
    stopped = true;
    throw new Error("达到120次业务HTTP预算");
  }
  summary.api_requests++;
  const operation = operationFor(method, path);
  if (operation) {
    const limits = {
      queries: 2,
      imports: 2,
      runs: 2,
      reviews: 2,
      reinspections: 1,
    };
    if (summary.operations[operation] >= limits[operation]) {
      stopped = true;
      throw new Error(`${operation}已达到本轮次数预算`);
    }
    summary.operations[operation]++;
    if (operation === "queries" || operation === "imports") {
      if (summary.actual_query_upper_bound >= 6) {
        stopped = true;
        throw new Error("达到6次业务取数预算");
      }
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
      data,
      headers: { "X-CSRF-Token": csrf },
      timeout: timeout(),
      maxRetries: 0,
      maxRedirects: 0,
    });
  } catch {
    stopped = true;
    if (queryInFlight) summary.requires_query_stop_check = true;
    throw new Error(`${method} ${path} 超时或连接中断，停止后续操作`);
  }
  if (["queries", "imports"].includes(operationFor(method, path)))
    queryInFlight = false;
  if (!response.ok()) {
    stopped = true;
    if (["queries", "imports"].includes(operationFor(method, path)))
      summary.requires_query_stop_check = true;
    summary.api_failures.push({ method, path, status: response.status() });
    throw new Error(`${method} ${path} 返回${response.status()}`);
  }
  return response.json();
}
function allowedPageRequest(method, path, payload) {
  if (method === "GET") {
    if (
      [
        "/auth/status",
        "/me",
        "/permissions/navigation",
        "/inspections",
        "/quality-cases",
        "/quality-statistics",
        "/quality-categories",
        "/quality-category-resources",
        "/manufacturing-assignees",
        "/incidents",
        "/maintenance-work-orders",
      ].includes(path)
    )
      return true;
    if (qualityCase && path === `/quality-cases/${qualityCase.id}`) return true;
    if (workOrder && path === `/maintenance-work-orders/${workOrder.id}`)
      return true;
    return [inspection, child]
      .filter(Boolean)
      .some((row) =>
        new RegExp(
          `^/inspections/${row.id}(?:/(?:samples|results|runs|lineage))?$`,
        ).test(path),
      );
  }
  if (
    method === "POST" &&
    inspection &&
    path === `/inspections/${inspection.id}/reinspections`
  ) {
    return (
      payload?.case_id === qualityCase?.id &&
      payload?.run_id === inspection.result.run_id
    );
  }
  if (
    method === "POST" &&
    qualityCase &&
    path === `/quality-cases/${qualityCase.id}/actions`
  ) {
    const expected = ["submit", "submit_reinspection", "verify"][
      summary.operations.ui_quality_actions.length
    ];
    return payload?.action === expected;
  }
  return false;
}
async function installPageGuards() {
  await context.route("**/*", async (route) => {
    const request = route.request(),
      url = new URL(request.url()),
      method = request.method();
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
    if (method === "POST") {
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
      await route.abort("blockedbyclient");
      return;
    }
    if (method === "POST") {
      try {
        // 响应放行前先登记服务端生成的子任务ID，后续读取只能访问该真实结果。
        const response = await route.fetch({
          timeout: timeout(),
          maxRetries: 0,
          maxRedirects: 0,
        });
        if (!response.ok()) {
          stopped = true;
          summary.api_failures.push({
            method,
            path,
            status: response.status(),
          });
        } else if (path.endsWith("/reinspections"))
          child = await response.json();
        else {
          qualityCase = await response.json();
          summary.operations.ui_quality_actions.push(payload.action);
        }
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
function waitResponse(method, path) {
  ensureActive();
  return page.waitForResponse(
    (response) =>
      response.request().method() === method &&
      new URL(response.url()).pathname === `/api/v1${path}`,
  );
}
function field(dialog, label) {
  return dialog
    .locator(".el-form-item")
    .filter({
      has: page
        .locator(".el-form-item__label")
        .filter({ hasText: new RegExp(`^${label}$`) }),
    });
}
async function choose(dialog, label, option) {
  await field(dialog, label).locator(".el-select").click();
  await page.getByRole("option", { name: option, exact: true }).click();
}
async function screen(name) {
  ensureActive();
  const path = `test-results/manufacturing-${name}.png`;
  await page.screenshot({ path });
  summary.screenshots.push(path);
}
async function openCase() {
  await page.getByRole("tab", { name: "异常处置", exact: true }).click();
  await page
    .getByRole("button", { name: qualityCase.name, exact: true })
    .click();
  await settleUi();
}
async function beginAction(action) {
  await page.getByRole("button", { name: "处理", exact: true }).click();
  const dialog = page.locator(".el-dialog:visible");
  await choose(dialog, "操作", action);
  return dialog;
}
async function submitAction(dialog) {
  const response = waitResponse(
    "POST",
    `/quality-cases/${qualityCase.id}/actions`,
  );
  await dialog.getByRole("button", { name: "提交", exact: true }).click();
  assert.ok((await response).ok());
  await settleUi();
}
async function checkTabs(path, categoryName) {
  await page.goto(`${origin}${path}`);
  await settleUi();
  for (const name of ["检测任务", "异常处置", "质量统计", "类别资料"]) {
    await page.getByRole("tab", { name, exact: true }).click();
    await settleUi();
    if (name === "类别资料")
      await page
        .locator(".el-tree")
        .getByText(categoryName, { exact: true })
        .waitFor();
  }
  await screen(
    path.includes("device") ? "device-categories" : "product-categories",
  );
}
const csvCell = (value) => `"${String(value).replaceAll('"', '""')}"`;
const mappings = [
  { metric_id: "weight", value_field: "weight", unit: "g" },
  { metric_id: "protein", value_field: "protein", unit: "%" },
  {
    metric_id: "appearance",
    defects_field: "defects",
    image_ref_field: "image_ref",
  },
];
await mkdir("test-results", { recursive: true });
try {
  browser = await chromium.launch({
    headless: true,
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE }
      : {}),
  });
  context = await browser.newContext({
    viewport: { width: 1600, height: 1100 },
    reducedMotion: "reduce",
  });
  const admin = await call("POST", "/auth/login", {
    username: process.env.E2E_USERNAME,
    password: process.env.E2E_PASSWORD,
  });
  csrf = admin.csrf_token;
  const workspace = await call("POST", "/workspaces", {
    name: `本轮制造验收${suffix}`,
  });
  const selectedWorkspace = await call("POST", "/workspace-selections", {
    workspace_id: workspace.id,
  });
  csrf = selectedWorkspace.csrf_token;
  const product = await call("POST", "/products", {
    name: `闭环验收饮品${suffix}`,
    code: `LIFE-${suffix}`,
  });
  const device = await call("POST", "/devices", {
    name: `闭环验收循环泵${suffix}`,
    code: `PUMP-${suffix}`,
  });
  const standard = await call("POST", "/inspection-templates", {
    name: `三维标准${suffix}`,
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
        name: "蛋白质",
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
    ["phase", "sample_id", "weight", "protein", "defects", "image_ref"],
    [
      "first",
      "LIFE-A",
      "104",
      "16",
      JSON.stringify([
        { defect_type: "封口破损", confidence: "0.98", severity: "severe" },
      ]),
      "db://local-fixture/LIFE-A",
    ],
    [
      "first",
      "LIFE-B",
      "100.000000000000000001",
      "12",
      "[]",
      "db://local-fixture/LIFE-B",
    ],
    [
      "retest",
      "LIFE-A",
      "100.000000000000000001",
      "12",
      "[]",
      "db://local-fixture/LIFE-A-retest",
    ],
  ];
  const dataset = await call("POST", "/datasets", {
    name: `本轮闭环记录${suffix}`,
    csv_text: rows.map((row) => row.map(csvCell).join(",")).join("\n"),
  });
  query = await call("POST", "/queries", {
    name: `首检记录${suffix}`,
    datasource_id: dataset.datasource_id,
    sql: `SELECT * FROM "${dataset.table_name}" WHERE phase = 'first'`,
    max_rows: 3,
  });
  retestQuery = await call("POST", "/queries", {
    name: `复检记录${suffix}`,
    datasource_id: dataset.datasource_id,
    sql: `SELECT * FROM "${dataset.table_name}" WHERE phase = 'retest'`,
    max_rows: 2,
  });
  assert.equal(query.truncated, false);
  assert.equal(retestQuery.truncated, false);
  inspection = await call("POST", "/inspections", {
    name: `饮品首检${suffix}`,
    target_type: "product",
    product_id: product.id,
    template_id: standard.id,
    template_revision: standard.revision,
    expected_samples: 2,
  });
  let imported = await call("POST", `/inspections/${inspection.id}/imports`, {
    revision: inspection.revision,
    query_id: query.id,
    sample_field: "sample_id",
    mappings,
  });
  inspection = await call("POST", `/inspections/${inspection.id}/runs`, {
    revision: imported.revision,
  });
  assert.equal(inspection.outcome, "failed");
  inspection = (
    await call("POST", `/inspections/${inspection.id}/reviews`, {
      revision: inspection.revision,
      outcome: "failed",
      note: "首检确认净重成分及封口缺陷，返工隔离",
    })
  ).inspection;
  qualityCase = await call("POST", "/quality-cases", {
    name: `饮品返工处置${suffix}`,
    inspection_id: inspection.id,
    run_id: inspection.result.run_id,
    revision: inspection.revision,
    note: "记录超差及缺陷并安排返工",
  });
  qualityCase = await call("POST", `/quality-cases/${qualityCase.id}/actions`, {
    revision: qualityCase.revision,
    action: "assign",
    assignee_id: admin.user.id,
    note: "分派本轮制造执行人员处理",
  });
  qualityCase = await call("POST", `/quality-cases/${qualityCase.id}/actions`, {
    revision: qualityCase.revision,
    action: "start",
    note: "已接收隔离批次并开始检查",
  });
  const categoryProduct = await call("POST", "/quality-categories", {
    name: `饮品类别${suffix}`,
    target_type: "product",
    description: "本轮饮品出厂质量资料",
  });
  const categoryDevice = await call("POST", "/quality-categories", {
    name: `泵类设备${suffix}`,
    target_type: "device",
    description: "本轮循环泵设备资料",
  });
  await call("POST", "/quality-category-bindings", {
    category_id: categoryProduct.id,
    resource_type: "product",
    resource_id: product.id,
  });
  await call("POST", "/quality-category-bindings", {
    category_id: categoryDevice.id,
    resource_type: "device",
    resource_id: device.id,
  });
  const role = await call("POST", "/roles", {
    name: `本轮制造复核${suffix}`,
    permissions: [
      "m01.read",
      "m05.read",
      "m06.read",
      "m10.read",
      "m11.read",
      "m11.publish",
    ],
  });
  const reviewerName = `manufacturing-reviewer-${suffix}`,
    reviewerPassword = randomBytes(24).toString("base64url");
  const reviewer = await call("POST", "/members", {
    username: reviewerName,
    password: reviewerPassword,
    display_name: "制造独立复核人",
    role_ids: [role.id],
  });
  summary.fixture_ids = {
    workspace_id: workspace.id,
    product_id: product.id,
    device_id: device.id,
    dataset_id: dataset.id,
    inspection_id: inspection.id,
    quality_case_id: qualityCase.id,
    reviewer_id: reviewer.id,
  };

  page = await context.newPage();
  page.setDefaultTimeout(10000);
  await installPageGuards();
  await checkTabs("/device-check", categoryDevice.name);
  await checkTabs("/product-check", categoryProduct.name);
  summary.checks.push(
    "设备/产品各四个工作区tab真实加载，类别来自已绑定的本轮业务资料",
  );
  await openCase();
  let dialog = await beginAction("提交处理");
  await choose(dialog, "处置方式", "返工");
  await field(dialog, "处理结果")
    .locator("textarea")
    .fill("重新灌装并更换封口包装，准备复检");
  await field(dialog, "检查原因")
    .locator("textarea")
    .fill("定量装置与封口机构调整不到位");
  await field(dialog, "操作记录")
    .locator("textarea")
    .fill("本轮现场返工记录已核对完成");
  await submitAction(dialog);
  assert.equal(qualityCase.status, "awaiting_reinspection");
  await page.getByRole("button", { name: "创建复检", exact: true }).click();
  dialog = page.locator(".el-dialog:visible");
  const childName = `返工复检${suffix}`;
  await field(dialog, "任务名称").locator("input").fill(childName);
  await field(dialog, "复检原因")
    .locator("textarea")
    .fill("返工后只复检原异常样本并保留首检结果");
  const creating = waitResponse(
    "POST",
    `/inspections/${inspection.id}/reinspections`,
  );
  await dialog.getByRole("button", { name: "创建并进入", exact: true }).click();
  assert.ok((await creating).ok());
  assert.equal(child.expected_samples, 1);
  assert.equal(child.template_revision, standard.revision);
  assert.equal(child.standard_snapshot.name, standard.name);
  await page.getByRole("heading", { name: childName, exact: true }).waitFor();
  await settleUi();
  await screen("reinspection-draft");
  imported = await call("POST", `/inspections/${child.id}/imports`, {
    revision: child.revision,
    query_id: retestQuery.id,
    sample_field: "sample_id",
    mappings,
  });
  child = await call("POST", `/inspections/${child.id}/runs`, {
    revision: imported.revision,
  });
  assert.equal(child.outcome, "passed");
  child = (
    await call("POST", `/inspections/${child.id}/reviews`, {
      revision: child.revision,
      outcome: "passed",
      note: "返工复检各维度完整合格，确认原异常样本已恢复",
    })
  ).inspection;
  await page.getByRole("button", { name: "返回", exact: true }).click();
  await page
    .getByRole("button", { name: qualityCase.name, exact: true })
    .click();
  await settleUi();
  dialog = await beginAction("提交复检结果");
  await field(dialog, "操作记录")
    .locator("textarea")
    .fill("复检三维数据完整合格，提交另一人复核");
  await submitAction(dialog);
  assert.equal(qualityCase.status, "awaiting_review");
  await screen("quality-awaiting-review");
  summary.checks.push(
    "UI真实提交返工、按固定标准创建仅异常样本复检，并提交完成的数据库复检结果",
  );

  // 维修取数/原阈值/新证据的完整闭环已有隔离API回归，此脚本仅准备维修后待复核工单并核真实页面入口。
  rule = await call("POST", "/alert-rules", {
    name: `泵温升异常${suffix}`,
    device_id: device.id,
    operator: "gt",
    threshold: "80",
    unit: "℃",
  });
  const alert = await call("POST", `/alert-rules/${rule.id}/evaluations`, {
    value: "90",
    unit: "℃",
    observed_at: new Date().toISOString(),
    create_incident: true,
  });
  workOrder = await call(
    "POST",
    `/incidents/${alert.incident.id}/work-orders`,
    {
      revision: alert.incident.revision,
      name: `循环泵维修${suffix}`,
      note: "核对温升异常并检查冷却回路",
    },
  );
  for (const [action, details] of [
    ["assign", { assignee_id: admin.user.id }],
    ["start", {}],
    ["repair", { root_cause: "冷却回路过滤器堵塞" }],
  ]) {
    workOrder = await call(
      "POST",
      `/maintenance-work-orders/${workOrder.id}/actions`,
      {
        revision: workOrder.revision,
        action,
        note: "已清理过滤器，等待新复测数据和独立复核",
        ...details,
      },
    );
  }
  summary.fixture_ids.work_order_id = workOrder.id;

  await context.close();
  context = await browser.newContext({
    viewport: { width: 1600, height: 1100 },
    reducedMotion: "reduce",
  });
  const limited = await call("POST", "/auth/login", {
    username: reviewerName,
    password: reviewerPassword,
  });
  csrf = limited.csrf_token;
  assert.ok(limited.permissions.includes("m11.publish"));
  assert.ok(
    !limited.permissions.some((value) =>
      ["*", "m11.*", "m11.write"].includes(value),
    ),
  );
  page = await context.newPage();
  page.setDefaultTimeout(10000);
  await installPageGuards();
  await page.goto(`${origin}/product-check`);
  await openCase();
  dialog = await beginAction("复核通过");
  await field(dialog, "操作记录")
    .locator("textarea")
    .fill("独立核对原始运行和返工复检，确认关闭");
  await submitAction(dialog);
  assert.equal(qualityCase.status, "closed");
  assert.equal(qualityCase.closed_by_name, "制造独立复核人");
  assert.notEqual(qualityCase.closed_by, qualityCase.performed_by);
  await page.getByText("已关闭", { exact: true }).waitFor();
  await screen("quality-closed");
  await page.getByRole("button", { name: "返回列表", exact: true }).click();
  await page.getByRole("tab", { name: "质量统计", exact: true }).click();
  await settleUi();
  const stats = await call("GET", "/quality-statistics?target_type=product");
  assert.equal(stats.first_inspection.pass_rate, "50.00");
  assert.equal(stats.reinspection.pass_rate, "100.00");
  assert.ok((await page.locator("main").innerText()).includes("50.00%"));
  await screen("quality-statistics");
  summary.checks.push(
    "第二个人类账号以m11.publish真实UI独立关闭；首检50%与复检100%同时保留",
  );
  await page.goto(`${origin}/alerts`);
  await page.getByRole("tab", { name: "维修工单", exact: true }).click();
  await page.getByRole("button", { name: workOrder.name, exact: true }).click();
  await page
    .getByRole("heading", { name: workOrder.name, exact: true })
    .waitFor();
  await settleUi();
  assert.ok(
    (await page.locator("main").innerText()).includes("冷却回路过滤器堵塞"),
  );
  await page.getByText("待复核", { exact: true }).first().waitFor();
  await screen("maintenance-work-order");
  summary.checks.push(
    "预警维修工单独立入口展示实际责任人、原阈值、维修记录及待新证据复核状态；未把待复核写成已解决",
  );
  assert.deepEqual(summary.operations, {
    queries: 2,
    imports: 2,
    runs: 2,
    reviews: 2,
    reinspections: 1,
    ui_quality_actions: ["submit", "submit_reinspection", "verify"],
  });
  assert.equal(summary.actual_query_upper_bound, 4);
  assert.equal(summary.api_failures.length, 0);
  assert.equal(summary.blocked_requests.length, 0);
  assert.equal(summary.page_errors.length, 0);
  ensureActive();
  summary.passed = true;
} catch (error) {
  stopped = true;
  summary.passed = false;
  if (queryInFlight) summary.requires_query_stop_check = true;
  summary.error = error instanceof Error ? error.message : "验收失败";
  if (page && !page.isClosed())
    await page
      .screenshot({ path: "test-results/manufacturing-failure.png" })
      .catch(() => {});
} finally {
  clearTimeout(deadline);
  summary.elapsed_seconds =
    Math.round((performance.now() - started) / 100) / 10;
  await browser?.close().catch(() => {});
  await writeFile(
    "test-results/manufacturing-lifecycle.json",
    JSON.stringify(summary, null, 2),
  );
  console.log(JSON.stringify(summary, null, 2));
}
if (!summary.passed) process.exitCode = 1;
