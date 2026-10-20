/**
 * 只验本轮Docker Open WebUI原生登录页面，不提交登录、不创建会话、不调用模型。
 * 与先行API验收共用20次业务请求和60秒，静态资源另限80次；只用系统Chrome。
 * 协调者核实新容器归属并注入证明后手动运行；本脚本不启动容器或平台服务。
 */
import { chromium } from "playwright";
import assert from "node:assert/strict";
import { mkdir, stat, writeFile } from "node:fs/promises";
import { basename, isAbsolute, join } from "node:path";
import { fileURLToPath } from "node:url";

const origin = process.env.E2E_NATIVE_ORIGIN ?? "http://127.0.0.1:13080";
const output = fileURLToPath(new URL("../test-results/", import.meta.url));
const summary = {
  scope: "仅原生/auth登录界面与字段有效性；不代表聊天UI或模型交互通过",
  origin,
  started_at: new Date().toISOString(),
  checks: [],
  prior_api_attempts: 0,
  browser_business_http: 0,
  websocket_attempts: 0,
  combined_business_budget_used: 0,
  static_requests: 0,
  pages: 0,
  login_submissions: 0,
  ui_model_calls: 0,
  native_login_ui_passed: false,
  chat_ui_verified: false,
  blocked_requests: [],
  network_failures: [],
  api_failures: [],
  page_errors: [],
};
let proof;
let browser;
let context;
let page;
let deadline;
let stopped = false;
let closing = false;
let closePromise;
let verificationStarted = 0;
const activeRequests = new Map();
const seenBusinessRequests = new Set();

function ensureActive() {
  if (stopped) throw new Error("验证已停止，不继续操作");
  if (Date.now() - verificationStarted >= 60000) {
    throw new Error("API与UI累计达到60秒，不继续操作");
  }
}

async function closeBrowser() {
  closing = true;
  if (browser) {
    closePromise ??= browser.close().catch(() => {});
    await closePromise;
  }
}

function stop(reason) {
  if (stopped || closing) return;
  stopped = true;
  summary.stop_reason = reason;
  summary.runtime_stop_required = true;
  // 先中止浏览器以阻止原生客户端重连；容器由协调者按当前归属统一停止。
  void closeBrowser();
}

function readInputs() {
  assert.equal(origin, "http://127.0.0.1:13080", "只允许本轮固定本机原生入口");
  const attempts = process.env.E2E_NATIVE_API_ATTEMPTS ?? "";
  assert.match(attempts, /^\d+$/, "必须提供先行API实际尝试次数");
  summary.prior_api_attempts = Number(attempts);
  assert.ok(summary.prior_api_attempts < 20, "累计业务请求已无剩余额度");
  try { proof = JSON.parse(process.env.E2E_NATIVE_OWNERSHIP_PROOF ?? ""); } catch {
    throw new Error("缺少协调者新核验的非敏感容器归属证明");
  }
  assert.ok(proof && typeof proof === "object" && !Array.isArray(proof));
  assert.equal(proof.origin, origin);
  assert.equal(proof.status, "succeeded", "先行API验收未成功，不继续UI");
  assert.equal(proof.ownership_verified, true);
  if ("ui_eligible" in proof) assert.equal(proof.ui_eligible, true);
  assert.equal(proof.http_attempts, summary.prior_api_attempts);
  assert.match(String(proof.runtime_id ?? ""), /^[A-Za-z0-9._-]{1,100}$/);
  assert.match(String(proof.container_id ?? ""), /^[0-9a-f]{12,64}$/);
  assert.match(String(proof.image_id ?? ""), /^sha256:[0-9a-f]{64}$/);
  assert.ok(!proof.checkpoint_error && !proof.stop_result, "验收检查点或停止结果异常");
  verificationStarted = Date.parse(proof.verification_started_at);
  const checked = Date.parse(proof.checked_at);
  assert.ok(Number.isFinite(verificationStarted) && Number.isFinite(checked), "缺少共享计时及新归属核验时间");
  assert.ok(verificationStarted <= checked && checked <= Date.now() + 2000, "归属证明时间不一致");
  ensureActive();
  summary.verification_started_at = new Date(verificationStarted).toISOString();
  summary.ownership = {
    runtime_id: proof.runtime_id,
    container_id: proof.container_id,
    image_id: proof.image_id,
    checked_at: proof.checked_at,
  };
  summary.combined_business_budget_used = summary.prior_api_attempts;
}

async function systemChrome() {
  const specified = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
    ?? process.env.E2E_NATIVE_CHROME_EXECUTABLE;
  const candidates = specified ? [specified] : [
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    ...(process.env.LOCALAPPDATA ? [join(process.env.LOCALAPPDATA, "Google/Chrome/Application/chrome.exe")] : []),
  ];
  for (const executable of candidates) {
    assert.ok(isAbsolute(executable) && basename(executable).toLowerCase() === "chrome.exe", "只允许系统Chrome可执行文件");
    const info = await stat(executable).catch(() => null);
    if (info?.isFile()) return executable;
  }
  throw new Error("未找到系统Chrome；不下载或改用其他浏览器");
}

function staticPath(path) {
  return /^\/(?:_app|static|assets|fonts|images|locales)\//.test(path)
    || /^\/(?:favicon\.[\w.-]+|manifest\.json|opensearch\.xml|robots\.txt)$/.test(path);
}

function requestKind(request, path) {
  if (/^\/(?:api|ws)(?:\/|$)/.test(path)) return "business";
  if (["xhr", "fetch"].includes(request.resourceType()) && !staticPath(path)) return "business";
  return "static";
}

function allowBusiness(method, path, request) {
  if (method === "GET" && ["/api/config", "/api/version"].includes(path)) return true;
  // Socket.IO轮询握手可能使用POST；它与登录或模型提交严格分开。
  return ["GET", "POST"].includes(method) && path === "/ws/socket.io/"
    && Buffer.byteLength(request.postData() ?? "") <= 1024;
}

function takeBusinessSlot() {
  ensureActive();
  if (summary.combined_business_budget_used >= 20) throw new Error("达到API与UI合计20次业务请求预算");
  summary.combined_business_budget_used++;
}

function watchPage(currentPage) {
  summary.pages++;
  if (summary.pages > 2) {
    stop("超过两个页面预算");
    return;
  }
  currentPage.setDefaultTimeout(8000);
  currentPage.on("pageerror", () => {
    if (closing) return;
    summary.page_errors.push("原生页面出现JavaScript异常");
    stop("原生页面异常");
  });
  currentPage.on("response", response => {
    if (closing || response.status() < 400) return;
    const path = new URL(response.url()).pathname;
    summary.api_failures.push({ path, status: response.status() });
    stop(response.status() === 408 ? "原生请求超时，停止后续验证" : "原生资源或接口返回失败");
  });
  currentPage.on("requestfinished", request => {
    clearTimeout(activeRequests.get(request));
    activeRequests.delete(request);
  });
  currentPage.on("requestfailed", request => {
    clearTimeout(activeRequests.get(request));
    activeRequests.delete(request);
    if (closing || stopped) return;
    summary.network_failures.push({ path: new URL(request.url()).pathname, failure: "network_or_connection" });
    stop("原生网络请求失败，不重试");
  });
  currentPage.on("websocket", socket => {
    socket.on("socketerror", () => {
      if (!closing) stop("原生WebSocket网络失败，不重连");
    });
  });
}

async function installGuards() {
  await context.route("**/*", async route => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    const path = url.pathname;
    if (stopped || closing) {
      await route.abort("blockedbyclient").catch(() => {});
      return;
    }
    if (url.origin !== origin) {
      summary.blocked_requests.push({ method, path: `${url.origin}${path}`, reason: "unknown_origin" });
      await route.abort("blockedbyclient");
      stop("拦截未知外链，停止验证");
      return;
    }
    const kind = requestKind(request, path);
    if (kind === "business") {
      if (!allowBusiness(method, path, request)) {
        summary.blocked_requests.push({ method, path, reason: "outside_auth_read_scope" });
        await route.abort("blockedbyclient");
        stop("拦截登录写入、模型调用或其他范围外接口");
        return;
      }
      const key = `${method} ${path}`;
      if (path !== "/ws/socket.io/" && seenBusinessRequests.has(key)) {
        summary.blocked_requests.push({ method, path, reason: "duplicate_business_request" });
        await route.abort("blockedbyclient");
        stop("原生客户端重复请求，不自动重试");
        return;
      }
      try { takeBusinessSlot(); } catch {
        await route.abort("blockedbyclient");
        stop("累计业务请求或时间预算耗尽");
        return;
      }
      seenBusinessRequests.add(key);
      summary.browser_business_http++;
    } else {
      if (method !== "GET" || summary.static_requests >= 80) {
        summary.blocked_requests.push({ method, path, reason: "static_budget_or_method" });
        await route.abort("blockedbyclient");
        stop("静态资源达到80次预算或出现非读取操作");
        return;
      }
      summary.static_requests++;
    }
    activeRequests.set(request, setTimeout(() => {
      if (!closing) stop("单个原生请求超过10秒，不重试");
    }, 10000));
    await route.continue();
  });
  await context.routeWebSocket("**/*", socket => {
    const url = new URL(socket.url());
    if (url.origin !== origin.replace("http:", "ws:") || url.pathname !== "/ws/socket.io/" || summary.websocket_attempts > 0) {
      summary.blocked_requests.push({ path: `${url.origin}${url.pathname}`, reason: "unknown_or_reconnected_websocket" });
      void socket.close();
      stop("拦截未知WebSocket或自动重连");
      return;
    }
    try { takeBusinessSlot(); } catch {
      void socket.close();
      stop("WebSocket握手超出累计业务或时间预算");
      return;
    }
    // 将唯一WebSocket握手也计入20次预算，避免用协议升级隐藏业务访问。
    summary.websocket_attempts++;
    socket.connectToServer();
  });
}

async function visibleControl(locator) {
  await locator.waitFor({ state: "visible" });
  const bounds = await locator.boundingBox();
  assert.ok(bounds && bounds.width > 20 && bounds.height > 15, "原生控件未实际渲染");
  assert.equal(await locator.isEnabled(), true);
}

await mkdir(output, { recursive: true });
try {
  readInputs();
  deadline = setTimeout(() => stop("API与UI累计60秒预算耗尽"), Math.max(1, 60000 - (Date.now() - verificationStarted)));
  const executablePath = await systemChrome();
  ensureActive();
  browser = await chromium.launch({ executablePath, headless: true, timeout: Math.min(10000, 60000 - (Date.now() - verificationStarted)) });
  ensureActive();
  context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: "zh-CN", reducedMotion: "reduce", serviceWorkers: "block" });
  context.on("page", watchPage);
  await installGuards();
  page = await context.newPage();
  await page.goto(`${origin}/auth?form=signin`, { waitUntil: "domcontentloaded", timeout: 10000 });
  const email = page.locator('form input[type="email"][name="email"]');
  const password = page.locator('form input[type="password"][name="password"]');
  await visibleControl(email);
  await visibleControl(password);
  const form = page.locator("form").filter({ has: page.locator('input[type="email"][name="email"]') });
  const submit = form.locator('button[type="submit"]');
  await visibleControl(submit);
  assert.ok((await submit.innerText()).trim(), "主按钮没有可见名称");
  assert.equal(await password.getAttribute("autocomplete"), "current-password");
  assert.ok((await page.title()).trim(), "原生页面标题为空");
  assert.equal(new URL(page.url()).pathname, "/auth");
  summary.checks.push("真正原生登录表单、邮箱、密码及主按钮可见且具有实际尺寸");

  assert.equal(await email.evaluate(element => element.required && element.validity.valueMissing), true);
  assert.equal(await password.evaluate(element => element.required && element.validity.valueMissing), true);
  await email.fill("invalid-email");
  assert.equal(await email.evaluate(element => element.validity.typeMismatch), true);
  await email.fill("ui-check@example.invalid");
  assert.equal(await email.evaluate(element => element.checkValidity()), true);
  await password.fill("UI-only-not-submitted");
  assert.equal(await password.evaluate(element => element.checkValidity()), true);
  assert.equal(await form.evaluate(element => element.checkValidity()), true);
  // 只填合成字符串检查原生字段，不点击提交，不触发登录或模型请求。
  await email.fill("");
  await password.fill("");
  await submit.focus();
  summary.checks.push("必填空值和无效邮箱被原生字段标记；有效格式可通过，未提交表单");

  await page.waitForFunction(() => document.fonts.status === "loaded"
    && [...document.images].every(image => image.complete && image.naturalWidth > 0)
    && ![...document.querySelectorAll('[role="alert"],[data-sonner-toast],#auth-login-card .animate-spin')]
      .some(element => element.getClientRects().length > 0));
  ensureActive();
  await page.screenshot({ path: join(output, "docker-openwebui.png") });
  assert.equal(summary.pages, 1);
  assert.ok(summary.combined_business_budget_used <= 20 && summary.static_requests <= 80);
  assert.equal(summary.blocked_requests.length, 0);
  assert.equal(summary.network_failures.length, 0);
  assert.equal(summary.api_failures.length, 0);
  assert.equal(summary.page_errors.length, 0);
  ensureActive();
  summary.checks.push("原生登录界面有效截图已保存；聊天UI、流式显示和模型交互未在本脚本验证");
  summary.native_login_ui_passed = true;
  summary.passed = true;
} catch (error) {
  stopped = true;
  summary.passed = false;
  summary.runtime_stop_required = true;
  // 不保存原始网络异常或完整页面调试数据，避免会话与请求头进入产物。
  summary.error = error instanceof assert.AssertionError ? error.message
    : error instanceof Error && error.name === "TimeoutError" ? "原生UI操作超时，停止且不重试"
      : error instanceof Error && /^(只允许|必须提供|累计业务|缺少协调者|未找到系统Chrome|验证已停止|API与UI累计)/.test(error.message)
        ? error.message : "原生UI验收未通过；停止后由协调者核对归属、请求和资源";
} finally {
  clearTimeout(deadline);
  for (const timer of activeRequests.values()) clearTimeout(timer);
  activeRequests.clear();
  await closeBrowser();
  summary.finished_at = new Date().toISOString();
  if (verificationStarted) summary.combined_elapsed_ms = Date.now() - verificationStarted;
  await writeFile(join(output, "docker-openwebui.json"), JSON.stringify(summary, null, 2));
  console.log(JSON.stringify(summary, null, 2));
}
if (!summary.passed) process.exitCode = 1;
