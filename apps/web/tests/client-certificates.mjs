/**
 * 复用现有本机浏览器守卫：120业务HTTP/180秒/0业务数据源/0模型。
 * 仅由verify_client_certificates_ui.py核18080/PID/新SQLite后通过stdin匿名管道传入账号及临时EC密钥。
 * 两张公钥证书通过真实UI登记和轮换；私钥、JWT、Bearer不进页面、剪贴板、截图、日志或文件。
 * 不启动服务、不重试，不沿服务端返回的8080 audience跳转；签名aud与HTTP实际地址分别使用。
 */
import { chromium } from "playwright";
import assert from "node:assert/strict";
import { createPrivateKey, randomBytes, sign } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const origin = "http://127.0.0.1:18080";
const started = performance.now();
const maxTime = 180000;
const maxRequests = 120;
const tokenPath = "/client-auth/token";
const summary = {
  scope: "root核定18080新SQLite；证书真实UI/本机签名；0业务源/0模型",
  origin,
  started_at: new Date().toISOString(),
  api_requests: 0,
  actual_query_upper_bound: 0,
  model_requests: 0,
  token_attempts: 0,
  tokens_issued: 0,
  expected_rejections: [],
  checks: [],
  fixture_ids: {},
  screenshots: [],
  blocked_requests: [],
  api_failures: [],
  page_errors: 0,
  network_failures: 0,
};
let browser, context, page, identity, authInfo, input;
let csrf = "";
let prefix = "";
let phase = "preflight";
let stopped = false;
let closing = false;
let activeAssertion = "";
let receipt = "";
const materials = [];
const certificates = new Map();
const tokens = new Set();
const downloads = new Set();

function active() {
  if (stopped || performance.now() - started >= maxTime) {
    stopped = true;
    throw new Error("verification_stopped");
  }
}
function timeout() {
  active();
  return Math.max(1, Math.min(10000, maxTime - (performance.now() - started)));
}
function countRequest() {
  active();
  assert.ok(summary.api_requests < maxRequests, "http_budget_exhausted");
  summary.api_requests++;
}
function ownBase() {
  assert.ok(identity?.id, "identity_not_created");
  return `/service-identities/${identity.id}`;
}
function certBase() {
  return `${ownBase()}/certificates`;
}
function publicMaterial(pem) {
  return (
    typeof pem === "string" &&
    materials.some((material) => material.pem.trim() === pem)
  );
}
function allowed(method, path, body) {
  if (method === "GET") {
    if (
      [
        "/auth/status",
        "/me",
        "/permissions/navigation",
        "/roles",
        "/service-identities",
      ].includes(path)
    )
      return true;
    if (!identity) return false;
    if ([certBase(), `${ownBase()}/client-auth`].includes(path)) return true;
    return [...certificates.keys()].some(
      (id) => path === `${certBase()}/${id}/public-certificate`,
    );
  }
  if (method === "POST" && path === "/auth/login")
    return (
      body?.username === input?.username && body?.password === input?.password
    );
  if (method === "POST" && path === "/service-identities")
    return (
      body?.name === `${prefix}-调用方` &&
      body?.caller_kind === "integration" &&
      JSON.stringify(body.permissions) === '["m14.read"]'
    );
  if (!identity) return false;
  if (method === "PATCH" && path === ownBase())
    return (
      Number.isInteger(body?.revision) &&
      Object.keys(body).every((key) =>
        ["revision", "permissions", "enabled"].includes(key),
      ) &&
      (!body.permissions ||
        JSON.stringify(body.permissions) === '["m01.read"]') &&
      (body.enabled === undefined || body.enabled === false)
    );
  if (method === "POST" && path === certBase())
    return (
      body?.name === `${prefix}-A` &&
      publicMaterial(body?.certificate_pem) &&
      Object.keys(body).length === 2
    );
  for (const id of certificates.keys()) {
    if (method === "PATCH" && path === `${certBase()}/${id}`)
      return (
        Number.isInteger(body?.revision) &&
        Object.keys(body).every((key) =>
          ["revision", "name", "enabled"].includes(key),
        ) &&
        (body.name === undefined || body.name === `${prefix}-A已改名`) &&
        (body.enabled === undefined || typeof body.enabled === "boolean")
      );
    if (method === "POST" && path === `${certBase()}/${id}/rotations`)
      return (
        body?.name === `${prefix}-B` &&
        body?.certificate_pem === materials[1]?.pem.trim() &&
        Number.isInteger(body.revision) &&
        Object.keys(body).length === 3
      );
    if (method === "POST" && path === `${certBase()}/${id}/revocations`)
      return (
        body?.reason === "本轮证书验收完成" &&
        Number.isInteger(body.revision) &&
        Object.keys(body).length === 2
      );
  }
  return false;
}
function capture(method, path, result) {
  if (
    (method === "POST" && path === "/service-identities") ||
    (identity && method === "PATCH" && path === ownBase())
  ) {
    identity = result;
    summary.fixture_ids.identity = result.id;
  } else if (identity && method === "GET" && path === certBase()) {
    for (const row of result.items) certificates.set(row.id, row);
  } else if (identity && path.startsWith(certBase() + "/") && method !== "GET")
    certificates.set(result.id, result);
  else if (identity && method === "POST" && path === certBase())
    certificates.set(result.id, result);
}
async function apiCall(method, path, body, options = {}) {
  const isToken = path === tokenPath && method === "POST";
  if (isToken) {
    assert.ok(
      identity &&
        body?.client_id === identity.id &&
        body?.grant_type === "client_credentials" &&
        body?.scope === "m14.read" &&
        body?.client_assertion_type ===
          "urn:ietf:params:oauth:client-assertion-type:jwt-bearer" &&
        typeof body?.client_assertion === "string" &&
        body.client_assertion === activeAssertion,
      "token_request_outside_fixture_scope",
    );
    summary.token_attempts++;
  }
  assert.ok(
    isToken || allowed(method, path, body),
    "request_outside_fixture_scope",
  );
  countRequest();
  let response;
  try {
    response = await context.request.fetch(`${origin}/api/v1${path}`, {
      method,
      ...(isToken ? { form: body } : body ? { data: body } : {}),
      headers: options.bearer
        ? { Authorization: `Bearer ${options.bearer}` }
        : { "X-CSRF-Token": csrf },
      timeout: timeout(),
      maxRetries: 0,
      maxRedirects: 0,
    });
  } catch {
    stopped = true;
    throw new Error("api_timeout_or_connection");
  }
  const status = response.status();
  if (options.expectedStatus !== undefined) {
    assert.equal(status, options.expectedStatus, "unexpected_rejection_status");
    const body = await response.json();
    if (options.expectedCode)
      assert.equal(
        typeof body.error === "string" ? body.error : body.error?.code,
        options.expectedCode,
        "unexpected_rejection_code",
      );
    summary.expected_rejections.push({ phase, method, path, status });
    await response.dispose();
    return body;
  }
  if (!response.ok()) {
    stopped = true;
    summary.api_failures.push({ phase, method, path, status });
    throw new Error("unexpected_api_failure");
  }
  const result = await response.json();
  if (!isToken) capture(method, path, result);
  await response.dispose();
  return result;
}
async function installGuards() {
  await context.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    const path = url.pathname.startsWith("/api/v1/")
      ? url.pathname.slice(7)
      : null;
    // 本页生成的公钥下载使用blob；其内容已由受控下载API决定，不是新的网络请求。
    if (url.protocol === "blob:" && url.origin === origin && !stopped) {
      await route.continue();
      return;
    }
    if (
      url.origin !== origin ||
      !["http:", "data:", "blob:"].includes(url.protocol)
    ) {
      stopped = true;
      summary.blocked_requests.push({ method, path: url.pathname });
      await route.abort("blockedbyclient");
      return;
    }
    if (path === null) {
      const safeAsset =
        method === "GET" &&
        (url.pathname === "/roles" ||
          url.pathname.startsWith("/assets/") ||
          /\.(?:svg|png|jpe?g|ico|css|js|woff2?|ttf)$/.test(url.pathname));
      if (!safeAsset || stopped) {
        stopped = true;
        summary.blocked_requests.push({ method, path: url.pathname });
        await route.abort("blockedbyclient");
      } else await route.continue();
      return;
    }
    let body;
    try {
      body = request.postDataJSON();
    } catch {
      body = undefined;
    }
    if (!allowed(method, path, body)) {
      stopped = true;
      summary.blocked_requests.push({ method, path });
      await route.abort("blockedbyclient");
      return;
    }
    try {
      countRequest();
      const response = await route.fetch({
        timeout: timeout(),
        maxRetries: 0,
        maxRedirects: 0,
      });
      if (!response.ok()) {
        stopped = true;
        summary.api_failures.push({ method, path, status: response.status() });
      } else if (!path.endsWith("/public-certificate"))
        capture(method, path, await response.json());
      await route.fulfill({ response });
    } catch {
      stopped = true;
      summary.api_failures.push({ method, path, failure: "fetch_or_budget" });
      await route.abort("failed").catch(() => {});
    }
  });
  await context.routeWebSocket("**/*", (socket) => {
    stopped = true;
    summary.blocked_requests.push({ method: "WEBSOCKET" });
    socket.close({ code: 1008, reason: "outside_e2e_scope" });
  });
  page.on("pageerror", () => {
    stopped = true;
    summary.page_errors++;
  });
  page.on("requestfailed", () => {
    if (!closing) {
      stopped = true;
      summary.network_failures++;
    }
  });
  page.on("response", (response) => {
    if (
      !closing &&
      response.status() >= 400 &&
      !new URL(response.url()).pathname.startsWith("/api/v1/")
    ) {
      stopped = true;
      summary.api_failures.push({ type: "static", status: response.status() });
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
async function responseJson(pending) {
  const response = await pending;
  assert.ok(response.ok(), "ui_operation_failed");
  active();
  return response.json();
}
function field(dialog, label) {
  return dialog
    .locator(".el-form-item")
    .filter({ has: page.getByText(label, { exact: true }) })
    .first();
}
function drawer() {
  return page.locator(".el-drawer:visible");
}
function row(name) {
  return drawer().locator(".el-table__row").filter({ hasText: name });
}
async function menu(name, action) {
  active();
  await row(name).getByRole("button", { name: "更多", exact: true }).click();
  await page.getByRole("menuitem", { name: action, exact: true }).click();
}
async function safeScreenshot(name) {
  active();
  const safe = await page.evaluate(() => ({
    fields: ![...document.querySelectorAll("input,textarea")].some((element) =>
      /PRIVATE KEY|service_[A-Za-z0-9_-]{30,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\./.test(
        element.value,
      ),
    ),
    storage: ![localStorage, sessionStorage].some((storage) =>
      Object.values(storage).some((value) =>
        /PRIVATE KEY|service_[A-Za-z0-9_-]{30,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\./.test(
          value,
        ),
      ),
    ),
    text: !/PRIVATE KEY|service_[A-Za-z0-9_-]{30,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\./.test(
      document.body.innerText,
    ),
  }));
  assert.ok(
    safe.fields && safe.storage && safe.text,
    "sensitive_browser_state",
  );
  const filename = `test-results/${prefix}-${name}.png`;
  await page.screenshot({ path: filename });
  summary.screenshots.push(filename);
}
function signedAssertion(certificate, materialIndex) {
  active();
  assert.equal(certificate.algorithm, "ES256", "unexpected_fixture_algorithm");
  assert.ok(authInfo?.audience, "server_audience_missing");
  const now = Math.floor(Date.now() / 1000);
  const header = Buffer.from(
    JSON.stringify({ alg: "ES256", typ: "JWT", kid: certificate.id }),
  ).toString("base64url");
  const payload = Buffer.from(
    JSON.stringify({
      iss: identity.id,
      sub: identity.id,
      aud: authInfo.audience,
      iat: now,
      nbf: now,
      exp: now + 120,
      jti: randomBytes(24).toString("hex"),
    }),
  ).toString("base64url");
  const unsigned = `${header}.${payload}`;
  const signature = sign("sha256", Buffer.from(unsigned), {
    key: materials[materialIndex].key,
    dsaEncoding: "ieee-p1363",
  }).toString("base64url");
  return `${unsigned}.${signature}`;
}
async function exchange(
  certificate,
  materialIndex,
  expectedStatus,
  suppliedAssertion,
) {
  activeAssertion =
    suppliedAssertion ?? signedAssertion(certificate, materialIndex);
  const result = await apiCall(
    "POST",
    tokenPath,
    {
      grant_type: "client_credentials",
      client_id: identity.id,
      client_assertion_type:
        "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
      client_assertion: activeAssertion,
      scope: "m14.read",
    },
    expectedStatus ? { expectedStatus, expectedCode: "invalid_client" } : {},
  );
  if (expectedStatus) {
    activeAssertion = "";
    return null;
  }
  assert.equal(result.token_type, "Bearer");
  assert.ok(
    typeof result.access_token === "string" &&
      result.access_token.startsWith("service_"),
    "access_token_missing",
  );
  assert.ok(
    result.expires_in > 0 && result.expires_in <= 600,
    "invalid_token_lifetime",
  );
  tokens.add(result.access_token);
  summary.tokens_issued++;
  return result.access_token;
}
async function bearerCheck(token, expectedStatus) {
  return apiCall("GET", certBase(), undefined, {
    bearer: token,
    ...(expectedStatus ? { expectedStatus } : {}),
  });
}

async function runUi() {
  phase = "open_certificate_workspace";
  await page.goto(`${origin}/roles`);
  const loading = responseFor("GET", "/service-identities");
  await page.getByRole("tab", { name: "服务身份", exact: true }).click();
  await responseJson(loading);
  const certificateList = responseFor("GET", certBase());
  await page
    .locator(".service-identities .el-table__row")
    .filter({ hasText: identity.name })
    .getByRole("button", { name: "证书", exact: true })
    .click();
  assert.equal((await responseJson(certificateList)).total, 0);

  phase = "register_certificate_ui";
  await drawer().getByRole("button", { name: "登记证书", exact: true }).click();
  let dialog = page.getByRole("dialog", {
    name: "登记客户端证书",
    exact: true,
  });
  await field(dialog, "名称").locator("input").fill(`${prefix}-A`);
  await dialog
    .getByRole("textbox", { name: "公钥证书PEM", exact: true })
    .fill(materials[0].pem);
  const creating = responseFor("POST", certBase());
  let reloading = responseFor("GET", certBase());
  await dialog.getByRole("button", { name: "登记", exact: true }).click();
  let first = await responseJson(creating);
  await responseJson(reloading);
  await dialog.waitFor({ state: "hidden" });
  summary.fixture_ids.certificate_a = first.id;
  assert.equal(first.status, "active");

  phase = "rename_certificate_ui";
  await menu(`${prefix}-A`, "编辑名称");
  dialog = page.getByRole("dialog", { name: "编辑证书名称", exact: true });
  await field(dialog, "名称").locator("input").fill(`${prefix}-A已改名`);
  let changing = responseFor("PATCH", `${certBase()}/${first.id}`);
  reloading = responseFor("GET", certBase());
  await dialog.getByRole("button", { name: "保存", exact: true }).click();
  first = await responseJson(changing);
  await responseJson(reloading);
  await dialog.waitFor({ state: "hidden" });
  assert.equal(first.name, `${prefix}-A已改名`);
  await safeScreenshot("registered");

  phase = "connection_parameters_ui";
  const gettingInfo = responseFor("GET", `${ownBase()}/client-auth`);
  await row(first.name)
    .getByRole("button", { name: "接入", exact: true })
    .click();
  authInfo = await responseJson(gettingInfo);
  assert.equal(authInfo.client_id, identity.id);
  assert.equal(authInfo.token_auth_method, "private_key_jwt");
  summary.audience_source = "server_client_auth";
  assert.equal(
    new URL(authInfo.audience).pathname,
    "/api/v1/client-auth/token",
  );
  dialog = page.getByRole("dialog", { name: "客户端接入", exact: true });
  assert.ok((await dialog.innerText()).includes(authInfo.audience));
  await dialog
    .locator("tr")
    .filter({ hasText: "客户端ID" })
    .getByRole("button", { name: "复制", exact: true })
    .click();
  assert.equal(
    (await page.evaluate(() => window.__certificateClipboard)).calls,
    1,
  );
  await safeScreenshot("connection");
  await dialog.locator(".el-dialog__headerbtn").click();
  await dialog.waitFor({ state: "hidden" });

  phase = "public_certificate_download_ui";
  const downloading = page.waitForEvent("download", { timeout: timeout() });
  await menu(first.name, "下载公钥证书");
  const download = await downloading;
  downloads.add(download);
  try {
    assert.equal(await download.failure(), null);
    const stream = await download.createReadStream();
    assert.ok(stream, "public_certificate_download_missing");
    const chunks = [];
    let size = 0;
    for await (const chunk of stream) {
      size += chunk.length;
      assert.ok(size <= 24000, "public_certificate_download_budget");
      chunks.push(chunk);
    }
    assert.ok(
      Buffer.concat(chunks).toString("utf8") === materials[0].pem,
      "public_certificate_download_mismatch",
    );
    assert.ok(download.suggestedFilename().endsWith(".pem"));
  } finally {
    await download.delete();
    downloads.delete(download);
  }
  summary.checks.push(
    "真实UI登记、改名、公钥证书下载内容一致；接入参数复制只调用内存替身",
  );

  phase = "signature_exchange_and_replay";
  const tokenA = await exchange(first, 0);
  const firstAssertion = activeAssertion;
  await bearerCheck(tokenA);
  await exchange(first, 0, 401, firstAssertion);
  summary.checks.push(
    "本机真实EC签名认证成功；同断言重放被401 invalid_client拒绝",
  );

  phase = "disable_and_reenable_certificate_ui";
  await menu(first.name, "停用");
  changing = responseFor("PATCH", `${certBase()}/${first.id}`);
  reloading = responseFor("GET", certBase());
  await page
    .locator(".el-message-box:visible")
    .getByRole("button", { name: "停用", exact: true })
    .click();
  first = await responseJson(changing);
  await responseJson(reloading);
  assert.equal(first.enabled, false);
  await bearerCheck(tokenA, 401);
  await exchange(first, 0, 401);
  changing = responseFor("PATCH", `${certBase()}/${first.id}`);
  reloading = responseFor("GET", certBase());
  await menu(first.name, "启用");
  first = await responseJson(changing);
  await responseJson(reloading);
  assert.equal(first.enabled, true);
  await bearerCheck(tokenA, 401);
  const nextTokenA = await exchange(first, 0);
  await bearerCheck(nextTokenA);
  summary.checks.push(
    "真实UI停用立即阻断证书和已发令牌；重新启用不会复活旧令牌，重新认证可用",
  );

  phase = "rotate_certificate_ui";
  await menu(first.name, "轮换证书");
  dialog = page.getByRole("dialog", { name: "轮换客户端证书", exact: true });
  await field(dialog, "名称").locator("input").fill(`${prefix}-B`);
  await dialog
    .getByRole("textbox", { name: "公钥证书PEM", exact: true })
    .fill(materials[1].pem);
  const rotating = responseFor("POST", `${certBase()}/${first.id}/rotations`);
  reloading = responseFor("GET", certBase());
  await dialog.getByRole("button", { name: "轮换", exact: true }).click();
  await page
    .locator(".el-message-box:visible")
    .getByRole("button", { name: "轮换", exact: true })
    .click();
  const second = await responseJson(rotating);
  await responseJson(reloading);
  await dialog.waitFor({ state: "hidden" });
  summary.fixture_ids.certificate_b = second.id;
  assert.equal(second.status, "active");
  assert.equal(certificates.get(first.id).replaced_by_id, second.id);
  assert.equal(certificates.get(first.id).status, "revoked");
  await bearerCheck(nextTokenA, 401);
  const tokenB = await exchange(second, 1);
  activeAssertion = "";
  await bearerCheck(tokenB);
  await safeScreenshot("rotated");
  summary.checks.push(
    "真实UI原子轮换保留旧公钥历史，原令牌失效，新证书认证成功",
  );

  phase = "latest_service_permission_revocation";
  await apiCall("PATCH", ownBase(), {
    revision: identity.revision,
    permissions: ["m01.read"],
  });
  await bearerCheck(tokenB, 403);
  summary.checks.push("专属验收身份缩减当前权限，已有Bearer下一请求立即403");

  phase = "revoke_certificate_ui";
  await menu(second.name, "撤销证书");
  const confirmation = page.locator(".el-message-box:visible");
  await confirmation.locator("textarea").fill("本轮证书验收完成");
  const revoking = responseFor(
    "POST",
    `${certBase()}/${second.id}/revocations`,
  );
  reloading = responseFor("GET", certBase());
  await confirmation.getByRole("button", { name: "撤销", exact: true }).click();
  const revoked = await responseJson(revoking);
  await responseJson(reloading);
  assert.equal(revoked.status, "revoked");
  await bearerCheck(tokenB, 401);
  await exchange(second, 1, 401);
  await safeScreenshot("revoked");
  await drawer().locator(".el-drawer__close-btn").click();
  await apiCall("PATCH", ownBase(), {
    revision: identity.revision,
    enabled: false,
  });
  summary.checks.push(
    "真实UI撤销轮换证书，旧Bearer和新断言均被拒绝；专属身份已停用保留审计",
  );
}

const deadline = setTimeout(() => {
  stopped = true;
  closing = true;
  summary.budget_exceeded = true;
  void browser?.close().catch(() => {});
}, maxTime);

try {
  const chunks = [];
  let bytes = 0;
  for await (const chunk of process.stdin) {
    bytes += chunk.length;
    assert.ok(bytes <= 65536, "stdin_budget_exhausted");
    chunks.push(chunk);
  }
  const raw = Buffer.concat(chunks);
  input = JSON.parse(raw.toString("utf8"));
  raw.fill(0);
  chunks.length = 0;
  assert.ok(
    input.source === "verified-client-certificate-helper-v1" &&
      input.origin === origin &&
      input.ownership?.verified === true &&
      input.ownership.component === "r34-acceptance" &&
      Number.isInteger(input.ownership.platform_pid) &&
      input.ownership.platform_pid > 0,
    "verified_local_proof_required",
  );
  assert.equal(
    resolve(input.ownership.data_directory).toLowerCase(),
    resolve(repo, ".local/acceptance/r34-20261021").toLowerCase(),
    "wrong_acceptance_directory",
  );
  assert.ok(
    typeof input.username === "string" &&
      input.username &&
      typeof input.password === "string" &&
      input.password,
    "encrypted_access_required",
  );
  assert.ok(
    /^cert-ui-[a-f0-9]{12}$/.test(input.prefix) &&
      input.materials?.length === 2,
    "invalid_fixture_material",
  );
  prefix = input.prefix;
  receipt = `test-results/${prefix}.json`;
  summary.receipt = receipt;
  summary.platform_pid = input.ownership.platform_pid;
  for (const material of input.materials) {
    materials.push({
      pem: material.certificate_pem,
      key: createPrivateKey(material.private_key_pem),
    });
    material.private_key_pem = "";
  }
  input.materials = [];
  await mkdir("test-results", { recursive: true });
  browser = await chromium.launch({
    headless: true,
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
  });
  context = await browser.newContext({
    viewport: { width: 1700, height: 1100 },
    reducedMotion: "reduce",
    serviceWorkers: "block",
    acceptDownloads: true,
  });
  await context.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: async (value) => {
          const previous = window.__certificateClipboard?.calls ?? 0;
          window.__certificateClipboard = {
            calls: previous + 1,
            length: value.length,
          };
        },
      },
    });
  });
  phase = "login_owned_acceptance";
  const me = await apiCall("POST", "/auth/login", {
    username: input.username,
    password: input.password,
  });
  assert.ok(me.permissions.includes("*"), "isolated_admin_required");
  csrf = me.csrf_token;
  input.username = "";
  input.password = "";
  await apiCall("POST", "/service-identities", {
    name: `${prefix}-调用方`,
    caller_kind: "integration",
    permissions: ["m14.read"],
  });
  page = await context.newPage();
  page.setDefaultTimeout(10000);
  page.setDefaultNavigationTimeout(10000);
  await installGuards();
  await runUi();
  assert.equal(summary.token_attempts, 6);
  assert.equal(summary.tokens_issued, 3);
  assert.equal(summary.expected_rejections.length, 8);
  assert.equal(summary.blocked_requests.length, 0);
  assert.equal(summary.api_failures.length, 0);
  assert.equal(summary.page_errors, 0);
  assert.equal(summary.network_failures, 0);
  assert.equal(summary.actual_query_upper_bound, 0);
  active();
  summary.passed = true;
} catch {
  stopped = true;
  summary.passed = false;
  summary.failed_phase = phase;
  summary.error_code = "certificate_ui_verification_failed";
  // 不序列化异常正文或截图失败现场，断言差异/请求错误可能含认证材料。
} finally {
  clearTimeout(deadline);
  activeAssertion = "";
  csrf = "";
  tokens.clear();
  materials.length = 0;
  if (input) {
    input.username = "";
    input.password = "";
    input.materials = [];
  }
  closing = true;
  for (const download of downloads) await download.delete().catch(() => {});
  await browser?.close().catch(() => {});
  summary.elapsed_seconds =
    Math.round((performance.now() - started) / 100) / 10;
  const serialized = JSON.stringify(summary, null, 2);
  if (
    /PRIVATE KEY|service_[A-Za-z0-9_-]{30,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\./.test(
      serialized,
    )
  ) {
    console.log(
      JSON.stringify({ passed: false, error_code: "unsafe_receipt_rejected" }),
    );
    process.exitCode = 1;
  } else {
    if (receipt) await writeFile(receipt, serialized, { flag: "wx" });
    console.log(serialized);
    if (!summary.passed) process.exitCode = 1;
  }
}
