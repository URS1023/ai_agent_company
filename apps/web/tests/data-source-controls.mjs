/**
 * 仅验收Elasticsearch离线配置与批准控件，不验证连接、同步或业务查询。
 * 协调者核实本轮隔离平台归属后手动运行；脚本不启动服务，也不连接19200端口。
 */
import { chromium } from "playwright";
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";

const origin = process.env.E2E_ORIGIN ?? "http://127.0.0.1:5173";
if (!/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(origin)) {
  throw new Error("只允许本轮已核实的本机隔离平台");
}
if (!process.env.E2E_USERNAME || !process.env.E2E_PASSWORD) {
  throw new Error("请通过环境变量提供隔离平台验收账号");
}

const target = { host: "127.0.0.1", port: 19200, database: "offline-ui-only" };
const started = performance.now();
const summary = {
  scope: "仅离线配置与批准控件；不算Elasticsearch连接验证",
  started_at: new Date().toISOString(),
  origin,
  checks: [],
  api_requests: 0,
  approval_posts: 0,
  blocked_requests: [],
  api_failures: [],
  page_errors: [],
};
let browser;
let context;
let page;
let csrf = "";
let sourceId = "";
let stopped = false;
let deleted = false;
const deadline = setTimeout(() => {
  stopped = true;
  summary.budget_exceeded = true;
  if (browser) void browser.close().catch(() => {});
}, 60000);

function countRequest() {
  if (
    stopped ||
    performance.now() - started >= 60000 ||
    summary.api_requests >= 20
  ) {
    stopped = true;
    throw new Error("达到60秒或20次API请求预算，停止后续操作");
  }
  summary.api_requests++;
}

function allowedRequest(method, path, payload) {
  if (method === "GET") {
    return (
      [
        "/auth/status",
        "/me",
        "/datasources",
        "/queries",
        "/secret-references",
      ].includes(path) ||
      (sourceId && path === `/datasources/${sourceId}`)
    );
  }
  if (method === "POST" && path === "/auth/login") return true;
  if (method === "POST" && path === "/datasources") {
    return (
      payload?.provider === "elasticsearch" &&
      String(payload?.name).startsWith("离线配置验收") &&
      payload?.connection_config?.host === target.host &&
      payload?.connection_config?.port === target.port &&
      payload?.connection_config?.database === target.database &&
      !payload?.credential_ref
    );
  }
  if (
    method === "POST" &&
    sourceId &&
    path === `/datasources/${sourceId}/target-approvals`
  ) {
    return (
      payload?.host === target.host &&
      payload?.port === target.port &&
      payload?.database === target.database &&
      Array.isArray(payload?.scope) &&
      payload.scope.length === 1 &&
      payload.scope[0] === "metadata" &&
      summary.approval_posts === 0
    );
  }
  return (
    method === "DELETE" && !!sourceId && path === `/datasources/${sourceId}`
  );
}

async function call(method, path, data) {
  if (!allowedRequest(method, path, data))
    throw new Error(`禁止的离线验收操作：${method} ${path}`);
  countRequest();
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
    // 不保存Playwright原始请求异常，避免其中的Cookie/CSRF进入产物。
    throw new Error(`${method} ${path} 超时或连接中断，停止后续操作`);
  }
  if (!response.ok()) {
    stopped = true;
    summary.api_failures.push({ method, path, status: response.status() });
    throw new Error(`${method} ${path} 返回${response.status()}`);
  }
  return response.json();
}

function field(container, label) {
  return container.locator(".el-form-item").filter({
    has: page
      .locator(".el-form-item__label")
      .filter({ hasText: new RegExp(`^${label}$`) }),
  });
}

async function settleUi() {
  await page.waitForFunction(
    () =>
      ![
        ...document.querySelectorAll(
          ".el-loading-mask,.el-message,.el-button.is-loading",
        ),
      ].some((element) => element.getClientRects().length > 0),
  );
}

async function setChecked(label, checked) {
  const input = label.locator("input[type=checkbox]");
  if ((await input.isChecked()) !== checked) await label.click();
  assert.equal(await input.isChecked(), checked);
}

function localDateText(date) {
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

async function attemptInvalidExpiry(input, value, submit, blurTarget) {
  const previous = await input.inputValue();
  await input.fill(value);
  await input.press("Tab");
  await blurTarget.focus();
  const displayed = await input.inputValue();
  if (displayed !== previous) {
    const parsed = new Date(displayed.replace(" ", "T"));
    const valid =
      Number.isFinite(parsed.getTime()) &&
      parsed.getTime() > Date.now() &&
      parsed.getTime() <= Date.now() + 86400000;
    if (!valid)
      assert.equal(await submit.isDisabled(), true, "无效有效期不得允许提交");
  }
  // 组件可以拒绝无效输入并保留原有效值；也可以接收后禁用提交，两者都符合约束。
  await input.fill(previous);
  await input.press("Tab");
  await blurTarget.focus();
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
  page.setDefaultTimeout(8000);
  page.on("pageerror", (error) => summary.page_errors.push(error.message));
  page.on("response", (response) => {
    const path = new URL(response.url()).pathname;
    if (path.startsWith("/api/v1/") && response.status() >= 400) {
      summary.api_failures.push({ path, status: response.status() });
    }
  });
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
    if (!allowedRequest(method, path, payload)) {
      stopped = true;
      summary.blocked_requests.push({ method, path });
      await route.abort("blockedbyclient");
      return;
    }
    try {
      countRequest();
    } catch {
      await route.abort("blockedbyclient");
      return;
    }
    if (method === "POST" && path === "/datasources")
      summary.creation_submitted = true;
    if (method === "POST" && path.endsWith("/target-approvals"))
      summary.approval_posts++;
    await route.continue();
  });

  const me = await call("POST", "/auth/login", {
    username: process.env.E2E_USERNAME,
    password: process.env.E2E_PASSWORD,
  });
  csrf = me.csrf_token;
  assert.ok(csrf);
  await page.goto(`${origin}/sources`);
  await settleUi();
  await page.getByRole("button", { name: "新增数据源", exact: true }).click();
  const sourceDialog = page.locator(".el-dialog:visible").filter({
    has: page.getByRole("button", { name: "保存数据源", exact: true }),
  });
  await field(sourceDialog, "类型").locator(".el-select").click();
  await page
    .locator(".el-select-dropdown__item:visible")
    .filter({ hasText: /^Elasticsearch$/ })
    .click();
  const portInput = field(sourceDialog, "端口").locator("input");
  assert.equal(await portInput.inputValue(), "9200");
  assert.equal(
    await field(sourceDialog, "索引").locator("input").isVisible(),
    true,
  );
  const tlsField = field(sourceDialog, "TLS");
  const tlsState = tlsField.getByRole("switch");
  const tlsControl = tlsField.locator(".el-switch");
  assert.equal(await tlsState.getAttribute("aria-checked"), "true");
  // Element Plus将原生输入藏在容器内；点击可见控件，只从原生输入读取状态。
  assert.equal(await tlsControl.isVisible(), true);
  await tlsControl.click();
  assert.equal(await tlsState.getAttribute("aria-checked"), "false");
  const secrets = field(sourceDialog, "凭据").locator(".secret-picker");
  assert.equal(await secrets.locator(".el-select").isVisible(), true);
  await secrets.getByRole("button", { name: "新增", exact: true }).click();
  const secretDialog = page.locator(".el-dialog:visible").filter({
    has: page.getByRole("button", { name: "保存凭据", exact: true }),
  });
  assert.equal(
    await field(secretDialog, "密钥").locator("input").getAttribute("type"),
    "password",
  );
  await secretDialog.locator(".el-dialog__headerbtn").click();
  await secretDialog.waitFor({ state: "hidden" });
  summary.checks.push(
    "Elasticsearch默认9200、索引、TLS切换及SecretPicker控件存在，未创建或填写凭据",
  );

  const name = `离线配置验收${Date.now()}`;
  summary.fixture_name = name;
  await field(sourceDialog, "名称").locator("input").fill(name);
  await field(sourceDialog, "主机").locator("input").fill(target.host);
  await portInput.fill(String(target.port));
  await portInput.press("Tab");
  await field(sourceDialog, "索引").locator("input").fill(target.database);
  await field(sourceDialog, "备注")
    .locator("textarea")
    .fill("仅离线配置元数据，无Elasticsearch服务，不检查连接、同步或查询。");
  const creating = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/v1/datasources" &&
      response.request().method() === "POST",
  );
  await sourceDialog
    .getByRole("button", { name: "保存数据源", exact: true })
    .click();
  const createdResponse = await creating;
  assert.ok(createdResponse.ok());
  const created = await createdResponse.json();
  sourceId = created.id;
  summary.source_id = sourceId;
  assert.equal(created.status, "pending_approval");
  assert.equal(created.connection_config.tls, false);
  await sourceDialog.waitFor({ state: "hidden" });
  await settleUi();

  const row = page.locator(".el-table__row").filter({ hasText: name });
  assert.equal(
    await row
      .getByRole("button", { name: "检查连接", exact: true })
      .isDisabled(),
    true,
  );
  assert.equal(
    await row
      .getByRole("button", { name: "同步元数据", exact: true })
      .isDisabled(),
    true,
  );
  await row.getByRole("button", { name: "批准访问", exact: true }).click();
  const approval = page
    .locator(".el-dialog:visible")
    .filter({ has: page.getByText("服务归属", { exact: true }) });
  const submit = approval.getByRole("button", {
    name: "批准访问",
    exact: true,
  });
  for (const [label, expected] of [
    ["主机", target.host],
    ["端口", String(target.port)],
    ["索引", target.database],
  ]) {
    const input = field(approval, label).locator("input");
    assert.equal(await input.inputValue(), expected);
    assert.equal(await input.evaluate((element) => element.readOnly), true);
  }
  assert.equal(await submit.isDisabled(), true);
  const owner = field(approval, "服务归属").locator("input");
  const purpose = field(approval, "访问用途").locator("textarea");
  const expiry = field(approval, "有效期至").locator("input");
  const metadata = approval
    .locator("label.el-checkbox")
    .filter({ hasText: "读取元数据" });
  const query = approval
    .locator("label.el-checkbox")
    .filter({ hasText: "查询数据" });
  await owner.fill("本机离线配置（未部署Elasticsearch服务）");
  await purpose.fill("仅验证离线批准元数据，不连接端口，不同步或执行查询。");
  assert.equal(await submit.isEnabled(), true);
  await owner.fill("本机");
  assert.equal(await submit.isDisabled(), true);
  await owner.fill("本机离线配置（未部署Elasticsearch服务）");
  await purpose.fill("");
  assert.equal(await submit.isDisabled(), true);
  await purpose.fill("仅验证离线批准元数据，不连接端口，不同步或执行查询。");
  await setChecked(metadata, false);
  await setChecked(query, false);
  assert.equal(await submit.isDisabled(), true);
  await setChecked(metadata, true);
  await attemptInvalidExpiry(expiry, "", submit, owner);
  await attemptInvalidExpiry(
    expiry,
    localDateText(new Date(Date.now() - 86400000)),
    submit,
    owner,
  );
  await attemptInvalidExpiry(
    expiry,
    localDateText(new Date(Date.now() + 2 * 86400000)),
    submit,
    owner,
  );
  assert.equal(await submit.isEnabled(), true);
  summary.checks.push(
    "批准目标只读；服务归属、用途、范围和有效期受控，无效输入不能获准提交",
  );
  await settleUi();
  await page.screenshot({ path: "test-results/data-source-approval.png" });

  await submit.click();
  const confirmation = page.locator(".el-message-box:visible");
  await confirmation.waitFor();
  assert.match(await confirmation.innerText(), /127\.0\.0\.1:19200/);
  assert.match(await confirmation.innerText(), /offline-ui-only/);
  assert.equal(summary.approval_posts, 0, "提交确认之前不得保存批准");
  const approving = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname ===
        `/api/v1/datasources/${sourceId}/target-approvals` &&
      response.request().method() === "POST",
  );
  await confirmation
    .getByRole("button", { name: "批准访问", exact: true })
    .click();
  assert.ok((await approving).ok());
  await approval.waitFor({ state: "hidden" });
  await settleUi();

  const approved = await call("GET", `/datasources/${sourceId}`);
  assert.equal(approved.revision, created.revision + 1);
  assert.equal(approved.status, "active");
  assert.equal(approved.target_approval.host, target.host);
  assert.equal(approved.target_approval.port, target.port);
  assert.equal(approved.target_approval.database, target.database);
  assert.deepEqual(approved.target_approval.scope, ["metadata"]);
  const expires = new Date(approved.target_approval.expires_at).getTime();
  assert.ok(expires > Date.now() && expires <= Date.now() + 86400000);
  assert.equal(summary.approval_posts, 1);
  summary.checks.push(
    "确认后仅保存一次批准元数据，读取证实revision递增与active配置状态；未验证连接",
  );
  await page.screenshot({ path: "test-results/data-source-controls.png" });

  const removing = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === `/api/v1/datasources/${sourceId}` &&
      response.request().method() === "DELETE",
  );
  const reloaded = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/v1/datasources" &&
      response.request().method() === "GET",
  );
  await row.getByRole("button", { name: "删除", exact: true }).click();
  await page
    .locator(".el-message-box:visible")
    .getByRole("button", { name: "确定", exact: true })
    .click();
  const removedResponse = await removing;
  assert.ok(removedResponse.ok());
  assert.equal(
    new URL(removedResponse.url()).searchParams.get("revision"),
    String(approved.revision),
  );
  const remaining = await (await reloaded).json();
  assert.ok(!remaining.items.some((item) => item.id === sourceId));
  deleted = true;
  await settleUi();
  summary.checks.push("按最新revision软删本轮离线配置，列表已不含该条目");
  assert.equal(summary.blocked_requests.length, 0);
  assert.equal(summary.api_failures.length, 0);
  assert.equal(summary.page_errors.length, 0);
  assert.ok(summary.api_requests <= 20);
  assert.equal(stopped, false);
  summary.passed = true;
} catch (error) {
  stopped = true;
  summary.passed = false;
  let diagnostic = error instanceof Error ? error.message : String(error);
  for (const secret of [process.env.E2E_PASSWORD, csrf].filter(Boolean))
    diagnostic = diagnostic.split(secret).join("[已隐藏]");
  summary.error = diagnostic
    .replace(/(agent_session=)[^;\s]+/gi, "$1[已隐藏]")
    .replace(/(X-CSRF-Token:\s*)[^\r\n]+/gi, "$1[已隐藏]");
  // 超时/失败后不补发清理请求；保留准确ID供协调者核对与后续显式清理。
  summary.cleanup_required = !!summary.creation_submitted && !deleted;
  if (page)
    await page
      .screenshot({ path: "test-results/data-source-controls-failure.png" })
      .catch(() => {});
} finally {
  clearTimeout(deadline);
  summary.finished_at = new Date().toISOString();
  summary.elapsed_ms = Math.round(performance.now() - started);
  summary.soft_deleted = deleted;
  await browser?.close();
  await writeFile(
    "test-results/data-source-controls.json",
    JSON.stringify(summary, null, 2),
  );
  console.log(JSON.stringify(summary, null, 2));
}
if (!summary.passed) process.exitCode = 1;
