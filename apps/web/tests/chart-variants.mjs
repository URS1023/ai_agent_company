/**
 * 图表变体的小范围本机验收：一个浏览器、一个报表、八个组件、同一条四行查询。
 * 只消费真实服务端series_model并读取ECharts实际option；不通过构造DOM或伪造数据冒充渲染。
 * 脚本由协调者核实本轮隔离SQLite归属、服务和资源预算后手动运行，不自行启动任何服务。
 */
import { chromium } from "playwright";
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";

const origin = process.env.E2E_ORIGIN ?? "http://127.0.0.1:5173";
if (!/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(origin)) {
  throw new Error("只允许协调者已核实的本机Vite与隔离平台环境");
}
if (!process.env.E2E_USERNAME || !process.env.E2E_PASSWORD) {
  throw new Error("请通过环境变量提供本轮隔离环境的验收账号");
}

const variants = [
  { component: "JStackBar", name: "堆叠对比" },
  { component: "JMultipleBar", name: "分组对比" },
  { component: "JPercentBar", name: "占比结构" },
  { component: "JMixLineBar", name: "柱线双轴" },
  { component: "JMultipleLine", name: "多系列趋势" },
  { component: "JSmoothLine", name: "平滑趋势" },
  { component: "JStepLine", name: "阶梯趋势" },
  { component: "JNegativeBar", name: "正负变化" },
];
const expectedCategories = ["产线A", "产线B", "产线C", "产线D"];
const started = performance.now();
const maxApiCalls = 35;
const maxQueryExecutions = 9;
const summary = {
  started_at: new Date().toISOString(),
  origin,
  checks: [],
  api_calls: 0,
  initial_queries: 0,
  refresh_requests: 0,
  query_execution_upper_bound: 0,
  page_errors: [],
  api_failures: [],
  blocked_external_requests: [],
};
let browser;
let context;
let page;
let csrf = "";
let reportId = "";
let stopped = false;
let queryInFlight = false;
const deadline = setTimeout(() => {
  stopped = true;
  summary.budget_exceeded = true;
  if (queryInFlight) summary.requires_query_stop_check = true;
  // 总预算耗尽后关闭本轮浏览器，不继续等待或自动扩大超时。
  if (browser) void browser.close().catch(() => {});
}, 120000);

function checkBudget() {
  if (stopped) throw new Error("验证已停止，不继续发起请求");
  if (performance.now() - started > 120000) {
    stopped = true;
    throw new Error("达到本轮两分钟总预算，停止后续验证");
  }
  if (summary.api_calls >= maxApiCalls) {
    stopped = true;
    throw new Error("达到35次辅助API请求预算");
  }
}

async function call(method, path, data) {
  checkBudget();
  summary.api_calls++;
  if (method === "POST" && path === "/queries") {
    if (summary.initial_queries !== 0) {
      stopped = true;
      throw new Error("只允许创建并执行一条初始业务查询");
    }
    summary.initial_queries++;
    summary.query_execution_upper_bound++;
    queryInFlight = true;
  }
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
    stopped = true;
    summary.requires_query_stop_check = path === "/queries";
    // Playwright原始异常可能包含Cookie/CSRF请求头，产物只记录操作与失败类别。
    throw new Error(`${method} ${path} 超时或连接中断，停止后续操作`);
  }
  if (method === "POST" && path === "/queries") queryInFlight = false;
  if (!response.ok()) {
    stopped = true;
    summary.api_failures.push({ method, path, status: response.status() });
    throw new Error(
      `${method} ${path} 返回 ${response.status()}，停止后续操作`,
    );
  }
  return response.json();
}

async function settleUi() {
  // HTTP返回后还须等组件状态和提示退出，截图不保留白遮罩或按钮加载态。
  await page.waitForFunction(
    () =>
      ![
        ...document.querySelectorAll(
          ".el-loading-mask,.el-message,.el-button.is-loading",
        ),
      ].some((element) => element.getClientRects().length > 0),
  );
}

function coordinate(datum) {
  return datum && typeof datum === "object" && !Array.isArray(datum)
    ? datum.value
    : datum;
}

function assertApprox(actual, expected, label) {
  assert.equal(typeof actual, "number", `${label} 应为真实数值坐标`);
  assert.ok(
    Math.abs(actual - expected) < 1e-8,
    `${label}: ${actual} != ${expected}`,
  );
}

function assertPrimaryAxis(option) {
  assert.equal(option.yAxis.length, 1, `${option.component} 应共用一个主轴`);
  for (const series of option.series) assert.equal(series.yAxisIndex, 0);
}

async function readActualOptions(widgetIds) {
  return page.evaluate(async (ids) => {
    // 使用Vite已解析的相同模块URL，避免另载一份ECharts导致实例注册表不同。
    const sourceResponse = await fetch(
      "/src/modules/reports/reused/JimuChart.vue",
    );
    if (!sourceResponse.ok) throw new Error("无法读取当前Vite图表模块");
    const source = await sourceResponse.text();
    const match = source.match(
      /import\s+\*\s+as\s+echarts\s+from\s*["']([^"']+)["']/,
    );
    if (!match)
      throw new Error("该脚本需要已启动的Vite入口，未找到原ECharts模块");
    const moduleUrl = new URL(match[1], location.origin);
    if (
      moduleUrl.origin !== location.origin ||
      !moduleUrl.pathname.startsWith("/node_modules/")
    ) {
      throw new Error("ECharts模块不在当前本机应用依赖目录");
    }
    const echarts = await import(moduleUrl.href);
    return ids.map(({ id, component }) => {
      const article = document.querySelector(`[data-widget-id="${id}"]`);
      const element = article?.querySelector(".jimu-chart");
      const chart = element ? echarts.getInstanceByDom(element) : null;
      if (!chart) throw new Error(`${component} 没有实际ECharts实例`);
      const option = chart.getOption();
      const first = option.series?.[0];
      const formatter = option.tooltip?.[0]?.formatter;
      const tooltip =
        typeof formatter === "function" && first
          ? formatter({
              name: option.xAxis?.[0]?.data?.[0],
              seriesName: first.name,
              seriesIndex: 0,
              dataIndex: 0,
              data: first.data?.[0],
              value: first.data?.[0]?.value,
            })
          : "";
      return {
        id,
        component,
        tooltip_text: String(tooltip),
        xAxis: (option.xAxis ?? []).map((axis) => ({
          type: axis.type,
          data: axis.data,
        })),
        yAxis: (option.yAxis ?? []).map((axis) => ({
          type: axis.type,
          min: axis.min ?? null,
          max: axis.max ?? null,
          position: axis.position ?? null,
          formatter:
            typeof axis.axisLabel?.formatter === "string"
              ? axis.axisLabel.formatter
              : null,
          label_example:
            typeof axis.axisLabel?.formatter === "function"
              ? String(axis.axisLabel.formatter(40))
              : String(axis.axisLabel?.formatter ?? "").replaceAll(
                  "{value}",
                  "40",
                ),
        })),
        series: (option.series ?? []).map((series) => ({
          name: series.name,
          type: series.type,
          yAxisIndex: series.yAxisIndex ?? 0,
          stack: series.stack ?? null,
          smooth: series.smooth ?? false,
          step: series.step ?? false,
          connectNulls: series.connectNulls ?? false,
          data: series.data,
        })),
      };
    });
  }, widgetIds);
}

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
  page = await context.newPage();
  page.setDefaultTimeout(15000);
  page.on("pageerror", (error) => summary.page_errors.push(error.message));
  page.on("response", (response) => {
    const path = new URL(response.url()).pathname;
    if (path === `/api/v1/reports/${reportId}/data-refreshes`)
      queryInFlight = false;
    if (path.startsWith("/api/v1/") && response.status() >= 400) {
      summary.api_failures.push({ path, status: response.status() });
    }
  });
  await context.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.protocol.startsWith("http") && url.origin !== origin) {
      summary.blocked_external_requests.push(url.origin);
      stopped = true;
      await route.abort("blockedbyclient");
      return;
    }
    if (url.pathname.startsWith("/api/v1/") && request.method() === "POST") {
      if (url.pathname === "/api/v1/queries") {
        stopped = true;
        await route.abort("blockedbyclient");
        return;
      }
      if (url.pathname.endsWith("/data-refreshes")) {
        // 同一报表只允许一次刷新；按八组件各执行一次估算最坏取数上限。
        if (
          url.pathname !== `/api/v1/reports/${reportId}/data-refreshes` ||
          summary.refresh_requests !== 0
        ) {
          stopped = true;
          await route.abort("blockedbyclient");
          return;
        }
        summary.refresh_requests++;
        summary.query_execution_upper_bound += variants.length;
        if (summary.query_execution_upper_bound > maxQueryExecutions) {
          stopped = true;
          await route.abort("blockedbyclient");
          return;
        }
        queryInFlight = true;
      }
    }
    await route.continue();
  });

  const me = await call("POST", "/auth/login", {
    username: process.env.E2E_USERNAME,
    password: process.env.E2E_PASSWORD,
  });
  csrf = me.csrf_token;
  assert.ok(csrf, "登录必须返回真实CSRF");

  const catalog = await call("GET", "/report-components/catalog");
  const sourceItems = variants.map((variant) => {
    const item = catalog.items.find(
      (item) => item.compType === variant.component && item.renderer_supported,
    );
    assert.ok(item, `${variant.component} 尚未启用；停止创建数据和执行查询`);
    return item;
  });
  summary.checks.push("八种源组件均已由后端明确启用");

  const reuseReportId = process.env.E2E_REPORT_ID;
  let widgets = [];
  let report;
  if (reuseReportId) {
    assert.match(reuseReportId, /^[a-f0-9]{32}$/);
    report = await call("GET", `/reports/${reuseReportId}`);
    widgets = report.layout.widgets;
    assert.deepEqual(
      widgets.map((widget) => widget.component),
      variants.map((variant) => variant.component),
    );
    summary.reused_report_id = reuseReportId;
    summary.checks.push(
      "复用本轮已成功刷新的八组件报表，只核验显示，不重新取业务数据",
    );
  } else {
    const suffix = Date.now().toString();
    const dataset = await call("POST", "/datasets", {
      name: `图表变体验收${suffix}`,
      csv_text:
        "name,current_value,prior_value,negative_value\n产线A,20,30,-5\n产线B,0,0,0\n产线C,10,,5\n产线D,40,60,-2\n",
    });
    assert.match(
      dataset.table_name,
      /^[A-Za-z_][A-Za-z0-9_]*$/,
      "只引用平台新建受控表名",
    );
    const query = await call("POST", "/queries", {
      datasource_id: dataset.datasource_id,
      sql: `SELECT name, NULLIF(current_value, '') AS current_value, NULLIF(prior_value, '') AS prior_value, NULLIF(negative_value, '') AS negative_value FROM "${dataset.table_name}" ORDER BY name`,
      parameters: {},
      max_rows: 4,
    });
    assert.equal(query.rows.length, 4);
    assert.equal(query.truncated, false);
    assert.deepEqual(
      query.rows.map((row) => row.name),
      expectedCategories,
    );
    assert.equal(query.rows[2].prior_value, null);
    summary.checks.push("同一条受控查询返回四行，包含真实零值、缺值和负值");

    for (const [index, variant] of variants.entries()) {
      const widget = await call("POST", "/report-component-instances", {
        source_id: sourceItems[index].id,
        x: 20 + (index % 3) * 470,
        y: 20 + Math.floor(index / 3) * 350,
        order_num: index,
      });
      widget.w = 450;
      widget.h = 330;
      widget.title = variant.name;
      widget.componentName = variant.name;
      widget.binding = {
        query_id: query.id,
        label_field: "name",
        value_fields:
          variant.component === "JNegativeBar"
            ? ["negative_value"]
            : ["current_value", "prior_value"],
        ...(variant.component === "JMixLineBar"
          ? { secondary_value_fields: ["prior_value"] }
          : {}),
      };
      widgets.push(widget);
    }
    assert.equal(widgets.length, 8);
    report = await call("POST", "/reports", {
      name: `图表变体验收${suffix}`,
      layout: {
        canvas: { width: 1440, height: 1080, backgroundColor: "#07182d" },
        widgets,
      },
    });
  }
  reportId = report.id;
  summary.report_id = report.id;

  await page.goto(`${origin}/reports`);
  await page
    .locator(".el-table__row")
    .filter({ hasText: report.name })
    .getByRole("button", { name: "设计", exact: true })
    .click();
  await page.locator(".screen-widget").first().waitFor();
  assert.equal(await page.locator(".screen-widget").count(), 8);
  await settleUi();

  if (!reuseReportId) {
    const refreshResponse = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname ===
          `/api/v1/reports/${reportId}/data-refreshes` &&
        response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "刷新数据", exact: true }).click();
    const refreshedResponse = await refreshResponse;
    assert.ok(
      refreshedResponse.ok(),
      `刷新返回${refreshedResponse.status()}，停止后续验证`,
    );
    await settleUi();
    assert.equal(summary.refresh_requests, 1);
    assert.equal(summary.query_execution_upper_bound, 9);
  }

  const refreshed = await call("GET", `/reports/${reportId}`);
  assert.equal(refreshed.layout.widgets.length, 8);
  const byType = new Map(
    refreshed.layout.widgets.map((widget) => [widget.component, widget]),
  );
  for (const variant of variants) {
    const model = byType.get(variant.component)?.series_model;
    assert.equal(
      model?.status,
      "ready",
      `${variant.component} 未得到可信系列模型`,
    );
    assert.deepEqual(model.categories, expectedCategories);
  }
  summary.checks.push("八组件均由服务端真实刷新取得ready系列模型");

  await page.locator(".layer-button").filter({ hasText: "柱线双轴" }).click();
  const secondaryField = page.locator(".el-form-item").filter({
    has: page.locator(".el-form-item__label").filter({ hasText: /^右轴系列$/ }),
  });
  await secondaryField.locator(".el-select").click();
  await page
    .locator(".el-select-dropdown__item:visible")
    .filter({ hasText: /^prior_value$/ })
    .waitFor();
  const secondaryOptions = await page
    .locator(".el-select-dropdown__item:visible")
    .allTextContents();
  assert.ok(secondaryOptions.some((value) => value.trim() === "prior_value"));
  assert.ok(
    !secondaryOptions.some((value) => value.trim() === "current_value"),
    "首个柱形字段不能成为右轴系列",
  );
  await page.keyboard.press("Escape");
  summary.checks.push(
    "混合图属性控件只允许后续折线字段进入右轴，不更改绑定或增加取数",
  );

  await page.waitForFunction(
    () =>
      document.querySelectorAll(
        ".screen-widget .jimu-chart[_echarts_instance_]",
      ).length === 8,
  );
  const actual = await readActualOptions(
    widgets.map((widget) => ({ id: widget.id, component: widget.component })),
  );
  assert.equal(actual.length, 8);
  const options = new Map(actual.map((option) => [option.component, option]));
  for (const option of actual) {
    assert.deepEqual(option.xAxis[0].data, expectedCategories);
    const expectedCount = option.component === "JNegativeBar" ? 1 : 2;
    assert.equal(option.series.length, expectedCount);
    for (const series of option.series) assert.equal(series.data.length, 4);
  }

  const stacked = options.get("JStackBar");
  assertPrimaryAxis(stacked);
  assert.ok(
    stacked.series.every(
      (series) => series.type === "bar" && series.stack === "total",
    ),
  );
  assertApprox(coordinate(stacked.series[0].data[0]), 20, "堆叠第一系列原值");
  assertApprox(coordinate(stacked.series[1].data[0]), 30, "堆叠第二系列原值");
  summary.checks.push("堆叠图：两真实系列、同主轴、stack=total");

  const multiple = options.get("JMultipleBar");
  assertPrimaryAxis(multiple);
  assert.ok(
    multiple.series.every((series) => series.type === "bar" && !series.stack),
  );
  summary.checks.push("分组图：同主轴且未被错误堆叠或改成折线");

  const percent = options.get("JPercentBar");
  assertPrimaryAxis(percent);
  assert.equal(percent.yAxis[0].min, 0);
  assert.equal(percent.yAxis[0].max, 100);
  assert.equal(percent.yAxis[0].label_example, "40%");
  assertApprox(coordinate(percent.series[0].data[0]), 40, "占比第一系列");
  assertApprox(coordinate(percent.series[1].data[0]), 60, "占比第二系列");
  for (const series of percent.series) {
    assert.equal(series.stack, "total");
    assert.equal(coordinate(series.data[1]), null, "全零分类必须保留gap");
    assert.equal(
      coordinate(series.data[2]),
      null,
      "缺一组成时整分类必须保留gap",
    );
  }
  assert.match(percent.tooltip_text, /20/);
  assert.match(percent.tooltip_text, /%/);
  summary.checks.push("百分比图：真实40/60、零值与缺失保持gap，提示保留原值");

  const mixed = options.get("JMixLineBar");
  assert.equal(mixed.yAxis.length, 2);
  assert.equal(mixed.series[0].type, "bar");
  assert.equal(mixed.series[0].yAxisIndex, 0);
  assert.equal(mixed.series[1].type, "line");
  assert.equal(mixed.series[1].yAxisIndex, 1);
  assert.equal(mixed.yAxis[1].position, "right");
  summary.checks.push("混合图：首系列柱形主轴，显式选择的后续折线使用右轴");

  const lines = options.get("JMultipleLine");
  assertPrimaryAxis(lines);
  assert.ok(lines.series.every((series) => series.type === "line"));
  assert.equal(coordinate(lines.series[1].data[2]), null);
  assert.equal(lines.series[1].connectNulls, false);
  summary.checks.push("多折线：同主轴、缺值不补零且不跨空值连线");

  const smooth = options.get("JSmoothLine");
  assertPrimaryAxis(smooth);
  assert.ok(
    smooth.series.every(
      (series) => series.type === "line" && series.smooth === true,
    ),
  );
  summary.checks.push("平滑线：真实ECharts option启用smooth");

  const stepped = options.get("JStepLine");
  assertPrimaryAxis(stepped);
  const stepModel = byType.get("JStepLine").series_model;
  stepped.series.forEach((series, index) => {
    assert.equal(series.type, "line");
    assert.ok(["start", "middle", "end"].includes(series.step));
    assert.equal(series.step, stepModel.series[index].step);
    assert.equal(series.smooth, false);
  });
  summary.checks.push("阶梯线：step与服务端可信模型一致，未混用smooth");

  const negative = options.get("JNegativeBar");
  assertPrimaryAxis(negative);
  assert.equal(negative.series[0].type, "bar");
  assertApprox(coordinate(negative.series[0].data[0]), -5, "负值第一点");
  assertApprox(coordinate(negative.series[0].data[3]), -2, "负值第四点");
  assert.ok(negative.yAxis[0].min === null || negative.yAxis[0].min < 0);
  summary.checks.push("正负柱形：保留真实负值，坐标轴未强制截成零");

  const zoomInput = page.locator(".zoom-area input[type=number]");
  await zoomInput.fill("80");
  await zoomInput.press("Tab");
  await page.getByRole("button", { name: "预览", exact: true }).click();
  await settleUi();
  await page.screenshot({ path: "test-results/chart-variants.png" });
  await page
    .locator(".report-canvas")
    .screenshot({ path: "test-results/chart-variants-canvas.png" });

  assert.equal(summary.page_errors.length, 0);
  assert.equal(summary.api_failures.length, 0);
  assert.equal(summary.blocked_external_requests.length, 0);
  assert.equal(stopped, false);
  summary.passed = true;
  summary.options = actual;
} catch (error) {
  stopped = true;
  summary.passed = false;
  let diagnostic = error instanceof Error ? error.message : String(error);
  for (const secret of [process.env.E2E_PASSWORD, csrf].filter(Boolean)) {
    diagnostic = diagnostic.split(secret).join("[已隐藏]");
  }
  summary.error = diagnostic
    .replace(/(agent_session=)[^;\s]+/gi, "$1[已隐藏]")
    .replace(/(X-CSRF-Token:\s*)[^\r\n]+/gi, "$1[已隐藏]");
  if (
    summary.refresh_requests &&
    /timeout|timed out|超时|中断|ECONN|net::/i.test(summary.error)
  ) {
    summary.requires_query_stop_check = true;
  }
  if (queryInFlight) summary.requires_query_stop_check = true;
  // 失败立即停止业务请求，不自动刷新、重发查询或扩大预算。
  if (page)
    await page
      .screenshot({ path: "test-results/chart-variants-failure.png" })
      .catch(() => {});
} finally {
  clearTimeout(deadline);
  summary.finished_at = new Date().toISOString();
  summary.elapsed_ms = Math.round(performance.now() - started);
  await browser?.close();
  await writeFile(
    "test-results/chart-variants.json",
    JSON.stringify(summary, null, 2),
  );
  console.log(JSON.stringify({ ...summary, options: undefined }, null, 2));
}
if (!summary.passed) process.exitCode = 1;
