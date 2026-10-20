/** 只在本项目明确指定的本机隔离环境验证；测试账号、会话、CSRF不写入产物。 */
import { chromium } from "playwright";
import { randomBytes } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import assert from "node:assert/strict";

const origin = process.env.E2E_ORIGIN ?? "http://127.0.0.1:5173";
if (!/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(origin))
  throw new Error("只允许本机隔离环境");
const username = process.env.E2E_USERNAME ?? `web_${Date.now()}`;
const password =
  process.env.E2E_PASSWORD ?? randomBytes(24).toString("base64url");
const browser = await chromium.launch({
  headless: true,
  ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
    ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE }
    : {}),
});
const context = await browser.newContext({
  viewport: { width: 1600, height: 1000 },
  reducedMotion: "reduce",
});
const page = await context.newPage();
page.setDefaultTimeout(10000);
const report = {
  origin,
  started_at: new Date().toISOString(),
  checks: [],
  page_errors: [],
  api_failures: [],
  requests: 0,
};
page.on("pageerror", (error) => report.page_errors.push(error.message));
page.on("response", (response) => {
  if (response.url().includes("/api/v1/")) {
    report.requests++;
    if (response.status() >= 400 && response.status() !== 401)
      report.api_failures.push({
        path: new URL(response.url()).pathname,
        status: response.status(),
      });
  }
});
let csrf = "";
let requiresStopCheck = false;
async function call(method, path, data) {
  if (report.requests > 400) throw new Error("达到本轮请求预算");
  let response;
  try {
    response = await context.request.fetch(`${origin}/api/v1${path}`, {
      method,
      headers: { "X-CSRF-Token": csrf },
      data,
      timeout: 15000,
      maxRetries: 0,
    });
  } catch {
    requiresStopCheck = true;
    throw new Error(`${method} ${path} 超时或连接中断，停止本轮验证`);
  }
  report.requests++;
  if (response.status() === 408) requiresStopCheck = true;
  if (!response.ok())
    throw new Error(`${method} ${path} 返回${response.status()}`);
  return response.headers()["content-type"]?.includes("json")
    ? response.json()
    : response.text();
}
async function check(name, action) {
  try {
    await action();
    report.checks.push({ name, passed: true });
    console.log(`PASS ${name}`);
  } catch (error) {
    // 原始Playwright网络错误可能含请求头，产物仅记录去除凭据的诊断。
    let diagnostic = String(error.message);
    for (const secret of [password, csrf].filter(Boolean))
      diagnostic = diagnostic.split(secret).join("[已隐藏]");
    diagnostic = diagnostic.replace(/(agent_session=)[^;\s]+/gi, "$1[已隐藏]");
    report.checks.push({ name, passed: false, error: diagnostic });
    await page
      .screenshot({ path: `test-results/failure-${report.checks.length}.png` })
      .catch(() => {});
    console.log(`FAIL ${name}: ${diagnostic}`);
    if (
      requiresStopCheck ||
      /timeout|timed out|超时|中断|ECONN|net::/i.test(diagnostic)
    ) {
      report.requires_query_stop_check = true;
      throw new Error("本轮已停止，请先核对本地查询与资源状态");
    }
  }
}
async function choose(label, value) {
  const item = page.locator(".el-form-item").filter({
    has: page
      .locator(".el-form-item__label")
      .filter({ hasText: new RegExp(`^${label}$`) }),
  });
  await item.locator(".el-select").click();
  await page
    .locator(".el-select-dropdown__item:visible")
    .filter({ hasText: value })
    .first()
    .click();
}
async function capture(path) {
  await page.waitForFunction(
    () =>
      ![
        ...document.querySelectorAll(
          ".el-loading-mask,.el-message,.el-button.is-loading",
        ),
      ].some((element) => element.getClientRects().length > 0),
  );
  await page.screenshot({ path });
}
await mkdir("test-results", { recursive: true });
try {
  await check("首次初始化或登录真实Cookie会话", async () => {
    await page.goto(origin);
    await page.getByLabel("账号", { exact: true }).fill(username);
    if (
      await page
        .getByRole("button", { name: "创建并进入", exact: true })
        .isVisible()
    ) {
      await page.getByLabel("姓名", { exact: true }).fill("前端验收");
      await page.getByLabel("工作区名称", { exact: true }).fill("本机前端验收");
    }
    await page.getByLabel("密码", { exact: true }).fill(password);
    await page.getByRole("button", { name: /^(创建并进入|登录)$/ }).click();
    await page.locator(".app-shell").waitFor();
    const me = await call("GET", "/me");
    csrf = me.csrf_token;
    assert.ok(csrf);
    assert.ok(me.workspace.id);
  });
  if (!csrf) throw new Error("登录未通过，停止后续操作");
  const suffix = Date.now().toString().slice(-7);
  let device, query, inspection, reportRecord, analysisQuery;
  await check("设备页面新增并在服务端持久化", async () => {
    await page.goto(`${origin}/devices`);
    await page.getByRole("button", { name: "新增设备", exact: true }).click();
    await page.getByLabel("名称", { exact: true }).fill(`验收循环泵${suffix}`);
    await page.getByLabel("编码", { exact: true }).fill(`UI-${suffix}`);
    await page.getByLabel("设备类型", { exact: true }).fill("循环泵");
    await page.getByLabel("位置", { exact: true }).fill("本机验证");
    const response = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/v1/devices") && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "保存", exact: true }).click();
    const result = await response;
    assert.equal(result.status(), 200);
    device = await result.json();
    await page.locator(".el-dialog:visible").waitFor({ state: "hidden" });
    assert.ok((await call("GET", `/devices/${device.id}`)).id);
  });
  await check("受控CSV导入和真实查询", async () => {
    const dataset = await call("POST", "/datasets", {
      name: `验收质检数据${suffix}`,
      csv_text: "sample_id,temperature,unit\nS01,42,℃\nS02,78,℃",
    });
    query = await call("POST", "/queries", {
      name: `验收质量查询${suffix}`,
      datasource_id: dataset.datasource_id,
      sql: `SELECT * FROM "${dataset.table_name}"`,
      parameters: {},
      max_rows: 100,
    });
    assert.equal(query.rows.length, 2);
    await page.goto(`${origin}/analysis`);
    await page
      .getByRole("button", { name: "打开", exact: true })
      .first()
      .click();
    await page.getByText("查询结果", { exact: false }).first().waitFor();
    assert.ok((await page.locator("body").innerText()).includes("S01"));
  });
  await check("检测标准、数据库映射、判定、复核完整闭环", async () => {
    assert.ok(device);
    assert.ok(query);
    const standard = await call("POST", "/inspection-templates", {
      name: `验收温度标准${suffix}`,
      target_type: "device",
      metrics: [
        {
          id: "temp",
          name: "电机温度",
          category: "numeric",
          unit: "℃",
          standard_min: "0",
          standard_max: "65",
          minimum_confidence: "0.8",
          blocking_severities: ["moderate", "severe"],
        },
      ],
    });
    inspection = await call("POST", "/inspections", {
      name: `验收设备质检${suffix}`,
      target_type: "device",
      device_id: device.id,
      template_id: standard.id,
      template_revision: standard.revision,
      expected_samples: 2,
    });
    await page.goto(`${origin}/device-check`);
    await page
      .getByRole("button", { name: inspection.name, exact: true })
      .click();
    await page.getByRole("button", { name: "从查询导入", exact: true }).click();
    await choose("数据查询", query.name);
    await choose("样本编号字段", "sample_id");
    const valueSelect = page
      .locator(".el-dialog:visible .el-table__row .el-select")
      .first();
    await valueSelect.click();
    await page
      .locator(".el-select-dropdown__item:visible")
      .filter({ hasText: /^temperature$/ })
      .click();
    const imported = page.waitForResponse((r) =>
      r.url().endsWith(`/inspections/${inspection.id}/imports`),
    );
    await page.getByRole("button", { name: "导入", exact: true }).click();
    assert.equal((await imported).status(), 200);
    await page.locator(".el-dialog:visible").waitFor({ state: "hidden" });
    const run = page.waitForResponse((r) =>
      r.url().endsWith(`/inspections/${inspection.id}/runs`),
    );
    await page.getByRole("button", { name: "执行检测", exact: true }).click();
    assert.equal((await run).status(), 200);
    await page.getByRole("button", { name: "复核", exact: true }).click();
    await page.getByText("不合格", { exact: true }).last().click();
    await page
      .getByRole("textbox", { name: "复核记录", exact: true })
      .fill("现场核对温度记录，保持不合格结论");
    const review = page.waitForResponse((r) =>
      r.url().endsWith(`/inspections/${inspection.id}/reviews`),
    );
    await page.getByRole("button", { name: "提交复核", exact: true }).click();
    assert.equal((await review).status(), 200);
    const stored = await call("GET", `/inspections/${inspection.id}`);
    assert.equal(stored.status, "reviewed");
    assert.equal(stored.outcome, "failed");
    await capture("test-results/inspection.png");
  });
  await check("维护分析读取数据库并返回工况覆盖证据", async () => {
    const rows = ["timestamp,load,temperature,period"];
    for (let index = 0; index < 20; index++)
      rows.push(
        `2026-10-${String(index + 1).padStart(2, "0")}T08:00:00+08:00,50,${index < 10 ? 40 + index / 10 : 52 + index / 10},${index < 10 ? "baseline" : "recent"}`,
      );
    const dataset = await call("POST", "/datasets", {
      name: `验收维护数据${suffix}`,
      csv_text: rows.join("\n"),
    });
    analysisQuery = await call("POST", "/queries", {
      name: `验收维护查询${suffix}`,
      datasource_id: dataset.datasource_id,
      sql: `SELECT * FROM "${dataset.table_name}"`,
      parameters: {},
      max_rows: 100,
    });
    await page.goto(`${origin}/maintenance`);
    await page.getByRole("button", { name: "发起分析", exact: true }).click();
    await page
      .getByLabel("分析名称", { exact: true })
      .fill(`验收维护分析${suffix}`);
    await choose("设备", device.name);
    await choose("数据查询", analysisQuery.name);
    for (const [label, value] of [
      ["监测值", "temperature"],
      ["采样时间", "timestamp"],
      ["工况负载", "load"],
      ["对比阶段", "period"],
    ])
      await choose(label, value);
    const validity = await page
      .locator(".el-dialog:visible form")
      .evaluate((form) => ({
        valid: form.checkValidity(),
        invalid: [...form.elements]
          .filter((el) => el.validity && !el.validity.valid)
          .map((el) => ({
            type: el.type,
            value: el.value,
            message: el.validationMessage,
          })),
      }));
    assert.ok(validity.valid, JSON.stringify(validity));
    const response = page.waitForResponse(
      (r) =>
        r.url().endsWith("/maintenance-analyses") &&
        r.request().method() === "POST",
    );
    await page
      .locator(".el-dialog:visible")
      .getByRole("button", { name: "发起分析", exact: true })
      .click();
    const data = await response;
    assert.equal(data.status(), 201);
    const result = await data.json();
    assert.equal(result.provenance.type, "query");
    assert.ok(result.result.bins.length);
    await page.locator(".el-dialog:visible").waitFor({ state: "hidden" });
    await capture("test-results/maintenance.png");
  });
  await check("报表画布添加组件、绑定真实查询、刷新和保存", async () => {
    await page.goto(`${origin}/reports`);
    await page.getByRole("button", { name: "新建报表", exact: true }).click();
    await page
      .getByLabel("报表名称", { exact: true })
      .fill(`验收报表${suffix}`);
    const created = page.waitForResponse(
      (r) => r.url().endsWith("/reports") && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "创建设计", exact: true }).click();
    const response = await created;
    assert.equal(response.status(), 201);
    reportRecord = await response.json();
    await page.getByRole("button", { name: "折线图", exact: true }).click();
    await choose("数据查询", query.name);
    await choose("维度字段", "sample_id");
    await choose("数值字段", "temperature");
    const refreshed = page.waitForResponse((r) =>
      r.url().endsWith(`/reports/${reportRecord.id}/data-refreshes`),
    );
    await page.getByRole("button", { name: "刷新数据", exact: true }).click();
    assert.equal((await refreshed).status(), 200);
    const record = await call("GET", `/reports/${reportRecord.id}`);
    assert.equal(record.layout.widgets.length, 1);
    assert.equal(record.layout.widgets[0].data.length, 2);
    await capture("test-results/report-studio.png");
  });
  await check("产品批次与数值、成分、外观三个维度复核", async () => {
    const product = await call("POST", "/products", {
      name: `验收包装产品${suffix}`,
      code: `P-${suffix}`,
    });
    const batch = await call("POST", "/product-batches", {
      name: `验收批次${suffix}`,
      product_id: product.id,
      external_ref: `B-${suffix}`,
      quantity: 2,
    });
    const standard = await call("POST", "/inspection-templates", {
      name: `验收三维标准${suffix}`,
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
          unit: "",
          minimum_confidence: "0.8",
          blocking_severities: ["moderate", "severe"],
        },
      ],
    });
    const csv = (values) =>
      values
        .map((value) => `"${String(value).replaceAll('"', '""')}"`)
        .join(",");
    const dataset = await call("POST", "/datasets", {
      name: `三维检测来源${suffix}`,
      csv_text: [
        ["sample_id", "weight", "protein", "defects", "image_ref"],
        ["A01", "100", "12", "[]", "db://inspection-images/A01"],
        [
          "A02",
          "104",
          "16",
          JSON.stringify([
            { defect_type: "封口破损", confidence: "0.98", severity: "severe" },
          ]),
          "db://inspection-images/A02",
        ],
      ]
        .map(csv)
        .join("\n"),
    });
    const source = await call("POST", "/queries", {
      name: `三维检测查询${suffix}`,
      datasource_id: dataset.datasource_id,
      sql: `SELECT * FROM "${dataset.table_name}"`,
      parameters: {},
      max_rows: 100,
    });
    const task = await call("POST", "/inspections", {
      name: `验收产品质检${suffix}`,
      target_type: "product",
      product_id: product.id,
      batch_id: batch.id,
      template_id: standard.id,
      template_revision: standard.revision,
      expected_samples: 2,
    });
    await page.goto(`${origin}/product-check`);
    await page.getByRole("button", { name: task.name, exact: true }).click();
    await page.getByRole("button", { name: "从查询导入", exact: true }).click();
    await choose("数据查询", source.name);
    await choose("样本编号字段", "sample_id");
    const rows = page.locator(".el-dialog:visible .el-table__row");
    async function map(row, index, column) {
      await rows.nth(row).locator(".el-select").nth(index).click();
      await page
        .locator(".el-select-dropdown__item:visible")
        .filter({ hasText: new RegExp(`^${column}$`) })
        .click();
    }
    await map(0, 0, "weight");
    await map(1, 0, "protein");
    await map(2, 0, "defects");
    await map(2, 1, "image_ref");
    const imported = page.waitForResponse((r) =>
      r.url().endsWith(`/inspections/${task.id}/imports`),
    );
    await page.getByRole("button", { name: "导入", exact: true }).click();
    assert.equal((await imported).status(), 200);
    await page.locator(".el-dialog:visible").waitFor({ state: "hidden" });
    const run = page.waitForResponse((r) =>
      r.url().endsWith(`/inspections/${task.id}/runs`),
    );
    await page.getByRole("button", { name: "执行检测", exact: true }).click();
    assert.equal((await run).status(), 200);
    const computed = await call("GET", `/inspections/${task.id}/results`);
    assert.equal(computed.result.samples[0].metrics.length, 3);
    assert.deepEqual(
      computed.result.samples.map((sample) => sample.outcome),
      ["passed", "failed"],
    );
    await page.getByRole("button", { name: "复核", exact: true }).click();
    await page
      .locator(".el-dialog:visible")
      .getByText("不合格", { exact: true })
      .click();
    await page
      .getByRole("textbox", { name: "复核记录", exact: true })
      .fill("净重、成分与外观记录均已核对，批次需返检");
    const reviewed = page.waitForResponse((r) =>
      r.url().endsWith(`/inspections/${task.id}/reviews`),
    );
    await page.getByRole("button", { name: "提交复核", exact: true }).click();
    assert.equal((await reviewed).status(), 200);
    await page.locator(".el-dialog:visible").waitFor({ state: "hidden" });
    await capture("test-results/product-inspection.png");
  });
  await check("图谱实际本体约束与实体新增", async () => {
    const ontology = await call("POST", "/ontologies", {
      name: `验收本体${suffix}`,
      concepts: [
        { name: "设备", properties: { 位置: "string" } },
        { name: "故障", properties: { 等级: "string" } },
      ],
      relations: [{ name: "发生", source_type: "设备", target_type: "故障" }],
    });
    const graph = await call("POST", "/graphs", {
      name: `验收图谱${suffix}`,
      ontology_id: ontology.id,
    });
    const node = await call("POST", `/graphs/${graph.id}/entities`, {
      name: "循环泵",
      type: "设备",
      properties: { 位置: "车间一" },
    });
    const problem = await call("POST", `/graphs/${graph.id}/entities`, {
      name: "温度升高",
      type: "故障",
      properties: { 等级: "需检查" },
    });
    await call("POST", `/graphs/${graph.id}/relations`, {
      source: node.id,
      target: problem.id,
      type: "发生",
      properties: {},
    });
    await page.goto(`${origin}/relations`);
    await page.locator(".graph-chart canvas").waitFor();
    await page.getByRole("button", { name: "新增实体", exact: true }).click();
    await page.getByLabel("实体名称", { exact: true }).fill("备用循环泵");
    await choose("实体类型", "设备");
    await page.getByLabel("位置", { exact: true }).fill("车间二");
    const created = page.waitForResponse(
      (r) =>
        r.url().endsWith(`/graphs/${graph.id}/entities`) &&
        r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "保存实体", exact: true }).click();
    assert.equal((await created).status(), 201);
    await page.locator(".el-dialog:visible").waitFor({ state: "hidden" });
    const stored = await call("GET", `/graphs/${graph.id}`);
    assert.equal(stored.nodes.length, 3);
    assert.equal(stored.edges.length, 1);
    await capture("test-results/graph.png");
  });
  await check("Office生成页面返回实际Word文件", async () => {
    await page.goto(`${origin}/generate`);
    await page
      .getByLabel("标题", { exact: true })
      .fill(`验收质量记录${suffix}`);
    await page.getByPlaceholder("章节标题").fill("设备检查");
    await page
      .getByPlaceholder("章节内容")
      .fill("本次文档来自本机隔离平台，检查数据与复核记录保持关联。");
    const response = page.waitForResponse(
      (r) =>
        r.url().endsWith("/document-jobs") && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "生成文档", exact: true }).click();
    const result = await response;
    assert.equal(result.status(), 201);
    const task = await result.json();
    assert.equal(task.status, "succeeded");
    assert.ok(task.outputs.file.name.endsWith(".docx"));
    const link = await call(
      "POST",
      `/files/${task.outputs.file.id}/download-links`,
      {},
    );
    const content = await context.request.get(`${origin}${link.url}`);
    assert.equal(content.status(), 200);
    assert.equal((await content.body()).subarray(0, 2).toString(), "PK");
  });
  await check("密钥页面只返回安全引用", async () => {
    await page.goto(`${origin}/credentials`);
    await page.getByRole("tab", { name: "安全凭据", exact: true }).click();
    await page.getByRole("button", { name: "新增凭据", exact: true }).click();
    await page.getByLabel("名称", { exact: true }).fill(`验收凭据${suffix}`);
    const value = `test-only-${randomBytes(18).toString("hex")}`;
    await page.getByLabel("密钥", { exact: true }).fill(value);
    const saved = page.waitForResponse(
      (r) =>
        r.url().endsWith("/secret-references") &&
        r.request().method() === "POST",
    );
    await page
      .locator(".el-dialog:visible")
      .getByRole("button", { name: "保存", exact: true })
      .click();
    const response = await saved;
    assert.equal(response.status(), 201);
    assert.ok(!JSON.stringify(await response.json()).includes(value));
    await page.locator(".el-dialog:visible").waitFor({ state: "hidden" });
  });
  await check("角色矩阵支持最小查看权限", async () => {
    await page.goto(`${origin}/roles`);
    await page.getByRole("button", { name: "新增角色", exact: true }).click();
    await page
      .getByLabel("角色名称", { exact: true })
      .fill(`质检查看${suffix}`);
    await page
      .getByRole("checkbox", { name: "工作台·查看", exact: true })
      .locator("..")
      .click();
    await page
      .getByRole("checkbox", { name: "质量·查看", exact: true })
      .locator("..")
      .click();
    const saved = page.waitForResponse(
      (r) => r.url().endsWith("/roles") && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "保存角色", exact: true }).click();
    const response = await saved;
    assert.equal(response.status(), 201);
    const role = await response.json();
    assert.deepEqual([...role.permissions].sort(), ["m01.read", "m11.read"]);
    await page.locator(".el-dialog:visible").waitFor({ state: "hidden" });
  });
  await check("全部导航页面加载且不访问外部服务", async () => {
    const paths = [
      "overview",
      "tasks",
      "notices",
      "chat",
      "history",
      "memory",
      "devices",
      "maintenance",
      "alerts",
      "inspections",
      "device-check",
      "product-check",
      "standards",
      "products",
      "sources",
      "metrics",
      "access",
      "analysis",
      "jobs",
      "quality",
      "reports",
      "templates",
      "bases",
      "ontology",
      "extraction",
      "relations",
      "agents",
      "workflows",
      "triggers",
      "tools",
      "skills",
      "plugins",
      "applications",
      "packages",
      "files",
      "generate",
      "users",
      "roles",
      "workspaces",
      "audit",
      "models",
      "credentials",
      "usage",
      "runs",
      "health",
      "releases",
      "backups",
    ];
    const before = report.api_failures.length;
    for (const path of paths) {
      await page.goto(`${origin}/${path}`);
      await page.locator(".app-shell").waitFor();
      await page.waitForLoadState("networkidle");
      assert.ok(
        await page.locator("main.main-content").innerText(),
        `${path}为空白`,
      );
    }
    assert.deepEqual(report.api_failures.slice(before), []);
  });
  await check("独立滚动边界", async () => {
    await page.goto(`${origin}/templates`);
    await page.getByText("选择模板", { exact: true }).waitFor();
    await page.waitForTimeout(200);
    const before = await page.locator(".sidebar").boundingBox();
    await page
      .locator(".main-content")
      .evaluate((element) => (element.scrollTop = 600));
    const after = await page.locator(".sidebar").boundingBox();
    assert.deepEqual(after, before);
    assert.equal(
      await page.evaluate(() => document.scrollingElement.scrollTop),
      0,
    );
    assert.equal(
      await page
        .locator(".topbar")
        .evaluate((element) => element.getBoundingClientRect().top),
      0,
    );
  });
  await check("浏览器无未捕获异常", async () =>
    assert.deepEqual(report.page_errors, []),
  );
} finally {
  report.completed_at = new Date().toISOString();
  await writeFile(
    "test-results/verification.json",
    JSON.stringify(report, null, 2),
  );
  console.log(
    JSON.stringify({
      checks: report.checks.length,
      passed: report.checks.filter((x) => x.passed).length,
      page_errors: report.page_errors.length,
      api_failures: report.api_failures,
      requests: report.requests,
    }),
  );
  await browser.close();
}
if (report.checks.some((check) => !check.passed)) process.exitCode = 1;
