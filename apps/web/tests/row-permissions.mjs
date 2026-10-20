/**
 * 行权限真实UI：单新CSV两产线4行、最多3取数/80业务HTTP/180秒/1浏览器/0模型。
 * 只接收已核验18080/PID/新SQLite的匿名管道输入，不创建账号/角色、不改已有数据源。
 * 查询先预留次数并记录在途标识；历史GET不重跑，不自动重试或恢复失败阶段。
 */
import assert from "node:assert/strict";
import { readFile, writeFile, appendFile } from "node:fs/promises";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const origin = "http://127.0.0.1:18080";
const expectedData = resolve(repo, ".local/acceptance/r34-20261021");
const started = performance.now();
const maximumTime = 180000;
const csv =
  "line,serial,temperature\nA,A-01,45.0000\nA,A-02,46.0000\nB,B-01,57.0000\nB,B-02,58.0000\n";
const summary = {
  scope: "owned18080新CSV两产线四行；当前admin显式user规则；0企业源/0模型",
  origin,
  started_at: new Date().toISOString(),
  api_requests: 0,
  static_requests: 0,
  actual_query_upper_bound: 0,
  datasets_created: 0,
  policy_creates: 0,
  policy_updates: 0,
  model_calls: 0,
  checks: [],
  screenshots: [],
  fixture_ids: {},
  expected_rejections: [],
  failures: [],
  blocked_requests: [],
  requires_query_stop_check: false,
};
let input,
  browser,
  context,
  page,
  me,
  dataset,
  source,
  subjects,
  policy,
  beforeColumns;
let prefix = "",
  output = "",
  csrf = "",
  phase = "preflight",
  sql = "";
let stopped = false,
  closing = false,
  queryInFlight = false;
let currentQueryStage = "";
const queries = new Map();
function halt(code, detail = {}) {
  if (stopped) return;
  stopped = true;
  closing = true;
  summary.failures.push({ phase, code, ...detail });
  summary.first_failure = { phase, code };
  void browser?.close().catch(() => {});
}
function active() {
  if (stopped || performance.now() - started >= maximumTime) {
    stopped = true;
    throw new Error("row_verification_stopped");
  }
}
function timeout() {
  active();
  return Math.max(
    1,
    Math.min(10000, maximumTime - (performance.now() - started)),
  );
}
function countRequest() {
  active();
  assert.ok(summary.api_requests < 80, "row_http_budget_exhausted");
  summary.api_requests++;
}
function sourceBase() {
  return dataset ? `/datasources/${dataset.datasource_id}` : "";
}
function sameKeys(value, keys) {
  return value && Object.keys(value).sort().join() === [...keys].sort().join();
}
function expectedRules(line) {
  return [
    {
      subject_type: "user",
      subject_ids: [me.user.id],
      conditions: [
        { field: "line", operator: "eq", value_source: "literal", value: line },
      ],
    },
  ];
}
function allowed(method, url, body) {
  const path = url.pathname.slice(7);
  if (method === "GET") {
    if (
      ["/auth/status", "/me", "/permissions/navigation", "/roles"].includes(
        path,
      )
    )
      return !url.search;
    if (path === "/datasources")
      return (
        url.searchParams.get("limit") === "200" &&
        [...url.searchParams].length === 1
      );
    if (
      dataset &&
      [
        sourceBase(),
        `${sourceBase()}/row-policies`,
        `${sourceBase()}/row-policy-subjects`,
      ].includes(path)
    )
      return !url.search;
    if (path === "/queries")
      return (
        url.searchParams.get("q") === prefix &&
        url.searchParams.get("limit") === "100" &&
        [...url.searchParams].length === 2
      );
    return [...queries.values()].some(
      (query) => path === `/queries/${query.id}` && !url.search,
    );
  }
  if (method === "POST" && path === "/auth/login")
    return (
      !url.search &&
      sameKeys(body, ["username", "password"]) &&
      body.username === input.username &&
      body.password === input.password
    );
  if (method === "POST" && path === "/datasets")
    return (
      phase === "prepare_csv" &&
      !summary.datasets_created &&
      !url.search &&
      sameKeys(body, ["name", "csv_text"]) &&
      body.name === `${prefix}-两产线样本` &&
      body.csv_text === csv
    );
  if (method === "POST" && path === "/queries")
    return (
      !url.search &&
      dataset &&
      ["full", "A", "B"][summary.actual_query_upper_bound] ===
        currentQueryStage &&
      sameKeys(body, [
        "name",
        "datasource_id",
        "sql",
        "parameters",
        "max_rows",
      ]) &&
      body.name === `${prefix}-${currentQueryStage}` &&
      body.datasource_id === dataset.datasource_id &&
      body.sql === sql &&
      JSON.stringify(body.parameters) === "{}" &&
      body.max_rows === 10
    );
  if (method === "POST" && dataset && path === `${sourceBase()}/row-policies`)
    return (
      phase === "save_A" &&
      !summary.policy_creates &&
      !url.search &&
      sameKeys(body, ["table_name", "mode", "rules"]) &&
      body.table_name === dataset.table_name &&
      body.mode === "rules" &&
      JSON.stringify(body.rules) === JSON.stringify(expectedRules("A"))
    );
  if (method === "PATCH" && policy && path === `/row-policies/${policy.id}`)
    return (
      phase === "save_B" &&
      !summary.policy_updates &&
      !url.search &&
      sameKeys(body, ["revision", "mode", "rules"]) &&
      body.revision === policy.revision &&
      body.mode === "rules" &&
      JSON.stringify(body.rules) === JSON.stringify(expectedRules("B"))
    );
  return false;
}
async function checkpoint(step) {
  summary.last_step = step;
  if (output)
    await appendFile(
      resolve(output, "stages.jsonl"),
      JSON.stringify({
        at: new Date().toISOString(),
        phase,
        step,
        api_requests: summary.api_requests,
        actual_query_upper_bound: summary.actual_query_upper_bound,
        query_in_flight: queryInFlight,
        fixture_ids: summary.fixture_ids,
      }) + "\n",
    );
}
function claim(method, path) {
  countRequest();
  if (method === "POST" && path === "/datasets") summary.datasets_created++;
  if (method === "POST" && path === "/queries") {
    assert.ok(
      summary.actual_query_upper_bound < 3,
      "row_query_budget_exhausted",
    );
    summary.actual_query_upper_bound++;
    queryInFlight = true;
  }
  if (method === "POST" && path === `${sourceBase()}/row-policies`)
    summary.policy_creates++;
  if (method === "PATCH" && policy && path === `/row-policies/${policy.id}`)
    summary.policy_updates++;
}
function capture(method, path, result) {
  if (method === "POST" && path === "/datasets") {
    assert.ok(
      result.id &&
        result.datasource_id &&
        /^[A-Za-z0-9_]+$/.test(result.table_name),
      "row_dataset_identity_invalid",
    );
    dataset = result;
    summary.fixture_ids.dataset = result.id;
    summary.fixture_ids.datasource = result.datasource_id;
    summary.fixture_ids.table = result.table_name;
    sql = `SELECT line, serial, temperature, COUNT(*) OVER () AS visible_count FROM "${result.table_name}" ORDER BY serial`;
  } else if (
    method === "GET" &&
    path === `${sourceBase()}/row-policy-subjects`
  ) {
    assert.ok(
      Array.isArray(result.users) && Array.isArray(result.roles),
      "row_subjects_missing",
    );
    assert.ok(
      result.users.some((user) => user.id === me.user.id),
      "row_current_user_not_selectable",
    );
    subjects = result;
  } else if (
    (method === "POST" && path === `${sourceBase()}/row-policies`) ||
    (policy && method === "PATCH" && path === `/row-policies/${policy.id}`)
  ) {
    assert.ok(
      result.id && Number.isInteger(result.revision),
      "row_policy_identity_invalid",
    );
    assert.equal(
      result.datasource_id,
      dataset.datasource_id,
      "row_policy_wrong_source",
    );
    assert.equal(
      result.table_name,
      dataset.table_name,
      "row_policy_wrong_table",
    );
    policy = result;
    summary.fixture_ids.policy = result.id;
  } else if (method === "POST" && path === "/queries") {
    assert.ok(
      result.id && Array.isArray(result.rows ?? result.result?.rows),
      "row_query_result_missing",
    );
    queryInFlight = false;
    queries.set(currentQueryStage, result);
    summary.fixture_ids[`query_${currentQueryStage}`] = result.id;
  }
}
async function apiCall(method, path, body, expectedCode) {
  const url = new URL(`/api/v1${path}`, origin);
  assert.ok(allowed(method, url, body), "row_api_outside_fixture_scope");
  claim(method, url.pathname.slice(7));
  await checkpoint(`${method} ${url.pathname}`);
  let response;
  try {
    response = await context.request.fetch(url.href, {
      method,
      ...(body === undefined ? {} : { data: body }),
      headers: { "X-CSRF-Token": csrf, Origin: origin },
      timeout: timeout(),
      maxRetries: 0,
      maxRedirects: 0,
    });
  } catch {
    stopped = true;
    throw new Error("row_api_timeout_or_connection");
  }
  if (expectedCode) {
    assert.equal(response.status(), 403, "row_history_not_forbidden");
    const result = await response.json();
    assert.equal(
      result.error?.code,
      expectedCode,
      "row_history_wrong_rejection",
    );
    summary.expected_rejections.push({
      method,
      path: url.pathname.slice(7),
      status: 403,
      code: expectedCode,
    });
    await response.dispose();
    return result;
  }
  if (!response.ok()) {
    stopped = true;
    summary.failures.push({
      phase,
      method,
      path: url.pathname,
      status: response.status(),
    });
    throw new Error("row_unexpected_api_status");
  }
  const result = await response.json();
  capture(method, url.pathname.slice(7), result);
  await response.dispose();
  return result;
}
async function installGuards() {
  await context.route("**/*", async (route) => {
    const request = route.request(),
      url = new URL(request.url()),
      method = request.method();
    const apiPath = url.pathname.startsWith("/api/v1/")
      ? url.pathname.slice(7)
      : null;
    let body;
    try {
      body = request.postDataJSON();
    } catch {
      body = undefined;
    }
    const safeAsset =
      apiPath === null &&
      method === "GET" &&
      !url.search &&
      (url.pathname === "/access" ||
        url.pathname.startsWith("/assets/") ||
        /^\/[A-Za-z0-9_./-]+\.(?:png|jpe?g|svg|ico|css|js|woff2?|ttf)$/.test(
          url.pathname,
        ));
    if (
      stopped ||
      url.origin !== origin ||
      url.protocol !== "http:" ||
      !(safeAsset || (apiPath && allowed(method, url, body)))
    ) {
      summary.blocked_requests.push({ phase, method, path: url.pathname });
      halt("row_request_outside_fixture_scope");
      await route.abort("blockedbyclient").catch(() => {});
      return;
    }
    try {
      if (apiPath) {
        claim(method, apiPath);
        await checkpoint(`UI ${method} ${url.pathname}`);
      } else {
        assert.ok(summary.static_requests < 200, "row_static_budget_exhausted");
        summary.static_requests++;
      }
      const response = await route.fetch({
        timeout: timeout(),
        maxRetries: 0,
        maxRedirects: 0,
      });
      if (!response.ok()) {
        summary.failures.push({
          phase,
          method,
          path: url.pathname,
          status: response.status(),
        });
        throw new Error("row_ui_http_failed");
      }
      if (apiPath) capture(method, apiPath, await response.json());
      await route.fulfill({ response });
      await response.dispose();
    } catch {
      halt("row_ui_http_or_contract_failed", { method, path: url.pathname });
      await route.abort("failed").catch(() => {});
    }
  });
  await context.routeWebSocket("**/*", (socket) => {
    summary.blocked_requests.push({ phase, method: "WEBSOCKET" });
    socket.close({ code: 1008, reason: "outside_e2e_scope" });
    halt("row_unexpected_websocket");
  });
  context.on("page", (created) => {
    if (page && created !== page) {
      halt("row_unexpected_second_page");
      void created.close();
    }
  });
  page.on("pageerror", () => {
    halt("row_page_error");
  });
  page.on("requestfailed", () => {
    if (!closing) {
      halt("row_network_failure");
    }
  });
}
function responseFor(method, path) {
  active();
  return page.waitForResponse(
    (response) =>
      response.request().method() === method &&
      new URL(response.url()).pathname === `/api/v1${path}`,
    { timeout: timeout() },
  );
}
async function uiResult(pending) {
  const response = await pending;
  assert.ok(response.ok(), "row_ui_operation_failed");
  active();
  return response.json();
}
function panel() {
  return page.locator(".row-policy-panel");
}
async function selectOption(select, label) {
  active();
  await page.waitForFunction(
    (element) => !element.classList.contains("is-disabled"),
    await select.elementHandle(),
    { timeout: timeout() },
  );
  await select.click();
  await page
    .getByRole("option")
    .filter({ has: page.getByText(label, { exact: true }) })
    .first()
    .click();
  active();
}
async function settle() {
  await page.waitForFunction(
    () =>
      ![
        ...document.querySelectorAll(
          ".el-loading-mask,.el-message,.el-button.is-loading",
        ),
      ].some((node) => node.getClientRects().length),
    undefined,
    { timeout: timeout() },
  );
  active();
}
async function screenshot(name) {
  await settle();
  const safe = await page.evaluate(
    () =>
      !/PRIVATE KEY|service_[A-Za-z0-9_-]{30,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\./.test(
        document.body.innerText,
      ),
  );
  assert.ok(safe, "row_sensitive_page_rejected");
  const path = resolve(output, `${name}.png`);
  await page.screenshot({ path, timeout: timeout() });
  summary.screenshots.push(path);
}
function rowsOf(query) {
  return query.rows ?? query.result?.rows;
}
function columnSnapshot(schema) {
  // 行控会移除全表rowCount；仅比较列定义/列授权，避免把正确隐藏总数误判为列权限变化。
  return JSON.stringify(
    schema.map((table) => ({
      tableName: table.tableName,
      columns: table.columns.map((column) => ({
        name: column.columnName,
        type: column.dataType ?? column.type ?? "",
        allowed_roles: column.allowed_roles ?? [],
      })),
    })),
  );
}
async function execute(label, expectedLine) {
  phase = `query_${label}`;
  currentQueryStage = label;
  const query = await apiCall("POST", "/queries", {
    name: `${prefix}-${label}`,
    datasource_id: dataset.datasource_id,
    sql,
    parameters: {},
    max_rows: 10,
  });
  const rows = rowsOf(query),
    count = expectedLine ? 2 : 4;
  assert.equal(rows.length, count, "row_query_row_count_wrong");
  assert.ok(
    rows.every((row) => Number(row.visible_count) === count),
    "row_window_count_leaked_rows",
  );
  if (expectedLine) {
    assert.ok(
      rows.every((row) => row.line === expectedLine),
      "row_policy_did_not_filter_line",
    );
    assert.deepEqual(
      rows.map((row) => row.serial),
      [`${expectedLine}-01`, `${expectedLine}-02`],
      "row_wrong_serials",
    );
  }
  assert.equal(
    query.truncated ?? query.result?.truncated,
    false,
    "row_query_truncated",
  );
  await checkpoint(`query_${label}_complete`);
}
async function configureUi() {
  phase = "open_permissions";
  await page.goto(`${origin}/access`, { timeout: timeout() });
  const loaded = responseFor("GET", sourceBase());
  await selectOption(
    page.locator(".page-stack > .panel.toolbar .el-select"),
    `${prefix}-两产线样本`,
  );
  await uiResult(loaded);
  await page
    .getByRole("tab", { name: "字段权限", exact: true })
    .waitFor({ state: "visible", timeout: timeout() });
  assert.equal(
    await page
      .getByRole("button", { name: "保存权限", exact: true })
      .isDisabled(),
    true,
    "row_column_permissions_unexpectedly_dirty",
  );
  const policyList = responseFor("GET", `${sourceBase()}/row-policies`);
  const subjectList = responseFor("GET", `${sourceBase()}/row-policy-subjects`);
  await page.getByRole("tab", { name: "行权限", exact: true }).click();
  const initial = await uiResult(policyList);
  await uiResult(subjectList);
  assert.equal(initial.total, 0, "row_new_source_already_has_policy");
  await panel()
    .getByRole("button", { name: "保存行权限", exact: true })
    .waitFor({ state: "visible", timeout: timeout() });
  await panel()
    .locator(".el-radio-button")
    .filter({ hasText: "按规则" })
    .click();
  const rule = panel().locator(".row-policy-rule");
  await rule.waitFor({ state: "visible", timeout: timeout() });
  assert.equal(await rule.count(), 1, "row_unexpected_initial_rule_count");
  await selectOption(
    rule.locator(".rule-subjects .el-select").first(),
    "指定用户",
  );
  const actorIndex = subjects.users.findIndex((user) => user.id === me.user.id);
  assert.ok(actorIndex >= 0, "row_actor_not_in_subject_options");
  await rule.locator(".subject-picker .el-select").click();
  await page.getByRole("option").nth(actorIndex).click();
  await page.keyboard.press("Escape");
  await selectOption(rule.locator(".row-condition .el-select").first(), "line");
  await rule.locator(".condition-value input").fill("A");
  phase = "save_A";
  const created = responseFor("POST", `${sourceBase()}/row-policies`);
  await panel()
    .getByRole("button", { name: "保存行权限", exact: true })
    .click();
  const savedA = await uiResult(created);
  assert.deepEqual(
    savedA.rules,
    expectedRules("A"),
    "row_saved_A_payload_wrong",
  );
  await settle();
  summary.checks.push(
    "真实行权限UI以当前admin用户显式保存A产线，原列权限未提交修改",
  );
  await screenshot("row-policy-A");
  await execute("A", "A");

  phase = "edit_B";
  await panel().locator(".condition-value input").fill("B");
  phase = "save_B";
  const changed = responseFor("PATCH", `/row-policies/${policy.id}`);
  await panel()
    .getByRole("button", { name: "保存行权限", exact: true })
    .click();
  const savedB = await uiResult(changed);
  assert.deepEqual(
    savedB.rules,
    expectedRules("B"),
    "row_saved_B_payload_wrong",
  );
  phase = "refresh_B";
  const reloaded = responseFor("GET", `${sourceBase()}/row-policies`);
  const resubjects = responseFor("GET", `${sourceBase()}/row-policy-subjects`);
  await panel().getByRole("button", { name: "刷新", exact: true }).click();
  const policySnapshot = await uiResult(reloaded);
  await uiResult(resubjects);
  const persisted = policySnapshot.items.find((item) => item.id === policy.id);
  assert.deepEqual(
    persisted?.rules,
    expectedRules("B"),
    "row_B_not_persisted_after_reload",
  );
  await settle();
  assert.equal(
    await panel().locator(".condition-value input").inputValue(),
    "B",
    "row_B_not_visible_after_reload",
  );
  assert.equal(
    await panel()
      .getByRole("button", { name: "保存行权限", exact: true })
      .isDisabled(),
    true,
    "row_saved_B_still_dirty",
  );
  summary.checks.push(
    "真实UI修改为B并保存，刷新后规则及控件均为B且无未保存状态",
  );
  await screenshot("row-policy-B");
}

const deadline = setTimeout(() => {
  halt("row_overall_deadline");
}, maximumTime);
try {
  const chunks = [];
  let bytes = 0;
  for await (const chunk of process.stdin) {
    bytes += chunk.length;
    assert.ok(bytes <= 32768, "row_stdin_budget_exhausted");
    chunks.push(chunk);
  }
  const raw = Buffer.concat(chunks);
  input = JSON.parse(raw.toString("utf8"));
  raw.fill(0);
  chunks.length = 0;
  assert.ok(
    input.source === "verified-row-permissions-helper-v1" &&
      input.origin === origin &&
      input.ownership?.verified === true &&
      input.ownership.component === "r34-acceptance" &&
      Number.isInteger(input.ownership.platform_pid) &&
      input.ownership.platform_pid > 0,
    "row_local_proof_required",
  );
  assert.equal(
    resolve(input.ownership.data_directory).toLowerCase(),
    expectedData.toLowerCase(),
    "row_wrong_acceptance_directory",
  );
  assert.ok(
    typeof input.username === "string" &&
      input.username &&
      typeof input.password === "string" &&
      input.password,
    "row_encrypted_access_required",
  );
  assert.match(
    input.prefix,
    /^row-permissions-[a-f0-9]{12}$/,
    "row_prefix_invalid",
  );
  prefix = input.prefix;
  output = resolve(input.output_directory);
  assert.equal(
    output.toLowerCase(),
    resolve(expectedData, prefix).toLowerCase(),
    "row_output_outside_run_directory",
  );
  const marker = JSON.parse(
    await readFile(resolve(output, "started.json"), "utf8"),
  );
  assert.equal(marker.prefix, prefix, "row_exclusive_start_marker_missing");
  summary.platform_pid = input.ownership.platform_pid;
  summary.receipt = resolve(output, "acceptance.json");
  await checkpoint("owned_platform_verified");
  browser = await chromium.launch({
    headless: true,
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
  });
  context = await browser.newContext({
    viewport: { width: 1700, height: 1100 },
    reducedMotion: "reduce",
    serviceWorkers: "block",
  });
  phase = "login_owned_platform";
  me = await apiCall("POST", "/auth/login", {
    username: input.username,
    password: input.password,
  });
  assert.ok(
    me.permissions?.includes("*") && me.user?.id,
    "row_isolated_admin_required",
  );
  csrf = me.csrf_token;
  input.username = "";
  input.password = "";
  summary.fixture_ids.principal = me.user.id;
  phase = "prepare_csv";
  await apiCall("POST", "/datasets", {
    name: `${prefix}-两产线样本`,
    csv_text: csv,
  });
  source = await apiCall("GET", sourceBase());
  assert.equal(source.provider, "sqlite", "row_fixture_not_local_sqlite");
  assert.equal(source.status, "active", "row_fixture_source_inactive");
  assert.equal(
    source.schema_snapshot.length,
    1,
    "row_fixture_not_isolated_single_table",
  );
  assert.equal(
    source.schema_snapshot[0].tableName,
    dataset.table_name,
    "row_fixture_schema_mismatch",
  );
  beforeColumns = columnSnapshot(source.schema_snapshot);
  await execute("full");
  summary.checks.push("新受控CSV仅两产线四行，无行限制时返回4行且窗口计数为4");
  page = await context.newPage();
  page.setDefaultTimeout(10000);
  page.setDefaultNavigationTimeout(10000);
  await installGuards();
  await configureUi();

  phase = "old_results_forbidden";
  for (const label of ["full", "A"])
    await apiCall(
      "GET",
      `/queries/${queries.get(label).id}`,
      undefined,
      "query_scope_denied",
    );
  const directoryPath = `/queries?q=${encodeURIComponent(prefix)}&limit=100`;
  const hidden = await apiCall("GET", directoryPath);
  assert.equal(hidden.total, 0, "row_query_directory_leaked_total");
  assert.deepEqual(hidden.items, [], "row_query_directory_leaked_old_records");
  summary.checks.push(
    "规则B下旧全量和A结果均403，查询目录及总数不泄露旧结果，读取未重跑SQL",
  );
  await execute("B", "B");
  const visible = await apiCall("GET", directoryPath);
  assert.equal(visible.total, 1, "row_query_directory_current_total_wrong");
  assert.deepEqual(
    visible.items.map((item) => item.id),
    [queries.get("B").id],
    "row_query_directory_current_scope_wrong",
  );
  const afterSource = await apiCall("GET", sourceBase());
  assert.equal(
    columnSnapshot(afterSource.schema_snapshot),
    beforeColumns,
    "row_column_permissions_changed",
  );
  assert.ok(
    !("rowCount" in afterSource.schema_snapshot[0]),
    "row_source_total_count_leaked",
  );
  summary.checks.push(
    "当前admin没有隐式全行豁免，B只返回两行且窗口计数2；目录只见B，列权限保持原值",
  );
  assert.equal(
    summary.actual_query_upper_bound,
    3,
    "row_final_query_count_invalid",
  );
  assert.equal(summary.datasets_created, 1, "row_final_dataset_count_invalid");
  assert.equal(
    summary.policy_creates,
    1,
    "row_final_policy_create_count_invalid",
  );
  assert.equal(
    summary.policy_updates,
    1,
    "row_final_policy_update_count_invalid",
  );
  assert.equal(summary.failures.length, 0, "row_final_failure_present");
  assert.equal(
    summary.blocked_requests.length,
    0,
    "row_final_blocked_request_present",
  );
  active();
  summary.passed = true;
  await checkpoint("completed");
} catch (error) {
  stopped = true;
  summary.passed = false;
  summary.failed_phase = phase;
  const firstLine = (error?.message ?? "").split("\n")[0];
  summary.error_code =
    summary.first_failure?.code ??
    (/^[a-z][a-z0-9_]{1,99}$/.test(firstLine)
      ? firstLine
      : "row_ui_verification_failed");
  if (output) await checkpoint("failed_without_retry").catch(() => {});
} finally {
  clearTimeout(deadline);
  closing = true;
  summary.requires_query_stop_check = queryInFlight;
  csrf = "";
  if (input) {
    input.username = "";
    input.password = "";
  }
  await browser?.close().catch(() => {});
  summary.elapsed_seconds =
    Math.round((performance.now() - started) / 100) / 10;
  const serialized = JSON.stringify(summary, null, 2);
  if (summary.receipt)
    await writeFile(summary.receipt, serialized, { flag: "wx" });
  console.log(serialized);
  if (!summary.passed) process.exitCode = 1;
}
