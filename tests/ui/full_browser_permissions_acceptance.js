/**
 * 成员消费授权验收：管理操作与正常消费使用真实控件；越权直达使用浏览器同源请求，单独记账。
 * 只使用 runner 的临时平台、合成文件与本进程回环桩。首个断言失败即停止，不重试业务请求。
 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const { createHash } = require("node:crypto");
const puppeteer = require("puppeteer-core");

const BASE = process.env.TEST5_E2E_BASE_URL;
const PASSWORD = process.env.TEST5_E2E_PASSWORD;
const EDGE = process.env.TEST5_EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const RUN = Date.now().toString(36);
const OUTPUT = path.join(path.dirname(process.env.TEST5_E2E_NETWORK_REPORT || "missing"), "permissions");
const names = { role: "只读文件成员-" + RUN, username: "consumer." + RUN,
  member: "消费验收成员-" + RUN, workspace: "隔离工作区-" + RUN,
  file: "权限合成资料-" + RUN + ".txt", connector: "权限回环设备-" + RUN, tool: "device_" + RUN };
const TEMP_PASSWORD = "Temporary-" + RUN + "-9A";
const MEMBER_PASSWORD = "Changed-" + RUN + "-7B";
const CONTENT = "权限消费合成资料\nCANARY-" + RUN + "\n设备编号 PERMISSION-001，仅供临时平台验收。\n";
const checks = [], failures = [], observations = [], consoleErrors = [];
const ids = {};
let browser, admin, member, fixture, activePage;
const requests = { model: [], device: [], unexpected: [] };
const sha = bytes => createHash("sha256").update(bytes).digest("hex");
const counts = () => ({ model: requests.model.length, device: requests.device.length, unexpected: requests.unexpected.length });
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
function pass(name, detail = {}) { checks.push({ name, ...detail }); console.log("PASS " + name); }

async function click(page, selector) {
  activePage = page;
  const element = await page.waitForSelector(selector, { visible: true });
  await element.evaluate(node => node.scrollIntoView({ block: "center", inline: "center" }));
  await element.click();
}
async function fill(page, selector, value) {
  await click(page, selector);
  await page.keyboard.down("Control"); await page.keyboard.press("KeyA"); await page.keyboard.up("Control");
  await page.keyboard.type(String(value));
}
async function selectText(page, selector, label) {
  await page.waitForFunction(({ selector, label }) => {
    const node = document.querySelector(selector);
    return node && !node.disabled && [...node.options].some(option => option.value && option.textContent.includes(label));
  }, {}, { selector, label });
  const value = await page.$eval(selector, (node, label) => [...node.options].find(option => option.value && option.textContent.includes(label)).value, label);
  await page.select(selector, value);
  return value;
}
async function visit(page, route, ready = "#main-content") {
  activePage = page;
  const response = await page.goto(BASE + route, { waitUntil: "networkidle0", timeout: 30000 });
  assert.equal(response.status(), 200, route);
  await page.waitForSelector(ready, { visible: true });
}
async function row(page, expected) {
  await page.waitForFunction(value => [...document.querySelectorAll("#main-content tbody tr")].some(node => node.textContent.includes(value)), {}, expected);
  for (const element of await page.$$("#main-content tbody tr")) {
    if (await element.evaluate((node, value) => node.textContent.includes(value), expected)) return element;
  }
  throw new Error("未找到表格行：" + expected);
}
async function save(page, expected = "", navigate = false) {
  if (navigate) {
    await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0", timeout: 30000 }), click(page, '#platform-dialog[open] button[type="submit"]')]);
  } else {
    await click(page, '#platform-dialog[open] button[type="submit"]');
    await page.waitForFunction(() => !document.querySelector("#platform-dialog")?.open || Boolean(document.querySelector("#platform-dialog .form-error:not([hidden])")?.textContent.trim()));
    assert.equal(await page.$eval("#platform-dialog", node => node.open ? node.querySelector(".form-error:not([hidden])")?.textContent.trim() || "弹窗仍打开" : ""), "");
  }
  if (expected) await page.waitForFunction(value => document.querySelector("#main-content")?.textContent.includes(value), {}, expected);
}
async function confirm(page, label, navigate = false) {
  await page.waitForSelector("#platform-dialog[open]", { visible: true });
  const button = (await page.$$("#platform-dialog[open] button"));
  for (const item of button) {
    if (await item.evaluate(node => node.textContent.trim()) !== label) continue;
    if (navigate) await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0", timeout: 30000 }), item.click()]);
    else { await item.click(); await page.waitForFunction(() => !document.querySelector("#platform-dialog")?.open); }
    return;
  }
  throw new Error("未找到确认按钮：" + label);
}
async function login(page, username, password, mustChange = false) {
  await visit(page, "/", "#login-username");
  await fill(page, "#login-username", username); await fill(page, "#login-password", password);
  await click(page, "#login-btn");
  await page.waitForSelector(mustChange ? "#required-password-form" : ".enterprise-area-grid", { visible: true });
}
async function makePage(context, identity) {
  const page = await context.newPage();
  page.setDefaultTimeout(30000);
  await page.setViewport({ width: 1440, height: 960 });
  page.on("pageerror", error => failures.push(identity + " pageerror: " + error.message));
  page.on("console", message => { if (message.type() === "error") consoleErrors.push({ identity, message: message.text() }); });
  page.on("response", response => { if (response.status() >= 500) failures.push(identity + " HTTP " + response.status() + " " + response.url()); });
  return page;
}

// 直达负对照确实从当前浏览器身份发出；不复用管理员令牌，不改 localStorage 或产品权限状态。
async function request(page, url, { method = "GET", body, workspace, cookieOnly = false } = {}) {
  return page.evaluate(async ({ url, method, body, workspace, cookieOnly }) => {
    const headers = {};
    if (!cookieOnly) {
      headers.Authorization = "Bearer " + localStorage.getItem("agent_token");
      headers["X-Workspace-Id"] = workspace || localStorage.getItem("agent_workspace_id");
    }
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetch(url, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), credentials: "same-origin", cache: "no-store", signal: AbortSignal.timeout(10000) });
    return { status: response.status, body: await response.text(), cacheControl: response.headers.get("cache-control") };
  }, { url, method, body, workspace, cookieOnly });
}
async function denied(label, page, url, options = {}, allowed = [403, 404]) {
  const before = counts();
  const result = await request(page, url, options);
  observations.push({ label, mode: "browser_same_origin_request", url, method: options.method || "GET", workspace: options.workspace || ids.workspace1, status: result.status, body: result.body });
  assert(allowed.includes(result.status), label + ": " + JSON.stringify(result));
  assert(!result.body.includes("CANARY-" + RUN), label + " 泄露文件正文");
  assert.deepEqual(counts(), before, label + " 引发模型或设备请求");
  return result;
}
async function staleClickDenied(label, selector, suffix) {
  const before = counts();
  const responsePromise = member.waitForResponse(response => new URL(response.url()).pathname === `/api/files/${ids.file}/${suffix}`);
  await click(member, selector);
  const response = await responsePromise;
  assert([403, 404].includes(response.status()), label + ": " + response.status());
  const body = await response.text();
  assert(!body.includes("CANARY-" + RUN));
  await member.waitForFunction(() => Boolean(document.querySelector("#platform-toast:not([hidden])")?.textContent.trim()));
  observations.push({ label, mode: "visible_stale_control", status: response.status(), body });
  assert.deepEqual(counts(), before);
}

async function startFixture() {
  const server = http.createServer((req, res) => {
    const chunks = [];
    req.on("data", chunk => chunks.push(chunk));
    req.on("end", () => {
      const body = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
      const record = { method: req.method, path: req.url, body };
      res.setHeader("Content-Type", "application/json");
      if (req.url === "/v1/embeddings") {
        requests.model.push(record);
        const values = Array.isArray(body.input) ? body.input : [body.input];
        res.end(JSON.stringify({ model: "permission-embedding", data: values.map((_, index) => ({ index, embedding: Array.from({ length: 1024 }, (_, i) => i === 0 ? 1 : 0) })), usage: { prompt_tokens: 1, total_tokens: 1 } }));
      } else if (req.url === "/equipment/status" && req.method === "POST") {
        requests.device.push(record);
        res.end(JSON.stringify({ equipmentId: body.equipmentId, status: "fixture-ready" }));
      } else {
        requests.unexpected.push(record); res.statusCode = 500; res.end(JSON.stringify({ error: "未声明的桩请求" }));
      }
    });
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  return server;
}

async function createPositiveControls() {
  const endpoint = "http://127.0.0.1:" + fixture.address().port;
  await visit(admin, "/system/models", "[data-new-model]");
  await click(admin, "[data-new-model]");
  await fill(admin, '#platform-dialog [name="profile_name"]', "permission_embedding");
  await fill(admin, '#platform-dialog [name="provider"]', "openai-compatible");
  await fill(admin, '#platform-dialog [name="model"]', "permission-embedding");
  await fill(admin, '#platform-dialog [name="endpoint"]', endpoint + "/v1");
  await fill(admin, '#platform-dialog [name="api_key"]', "permission-fixture-only");
  await fill(admin, '#platform-dialog [name="capabilities"]', "embedding");
  await save(admin, "permission_embedding");
  const modelResponse = admin.waitForResponse(response => response.url().endsWith("/api/models/probe/permission_embedding"));
  await click(admin, '[data-probe-model="permission_embedding"]');
  const probe = await modelResponse;
  assert.equal(probe.status(), 200);
  assert.equal((await probe.json()).status, "passed");
  assert.equal(requests.model.length, 1);

  await visit(admin, "/capabilities?view=methods", "[data-method-new]");
  await click(admin, "[data-method-new]");
  await fill(admin, '#platform-dialog [name="name"]', names.connector);
  await fill(admin, '#platform-dialog [name="baseUrl"]', endpoint);
  await fill(admin, '#platform-dialog [name="path"]', "/equipment/status");
  await admin.select('#platform-dialog [name="method"]', "POST");
  await admin.select('#platform-dialog [name="bodyMode"]', "json");
  await click(admin, '#platform-dialog [name="allowPrivate"]');
  await fill(admin, '#platform-dialog input[name^="tool_name_"]', names.tool);
  await fill(admin, '#platform-dialog input[name^="tool_description_"]', "只读取本轮合成设备状态");
  await click(admin, "#platform-dialog [data-tool-row] details summary");
  await fill(admin, '#platform-dialog textarea[name^="tool_schema_"]', JSON.stringify({ type: "object", properties: { equipmentId: { type: "string" } }, required: ["equipmentId"], additionalProperties: false }));
  await save(admin, names.connector, true);
  ids.connector = new URL(admin.url()).searchParams.get("id"); assert(ids.connector);
  await click(admin, "[data-connector-publish]"); await confirm(admin, "确认发布", true);
  await click(admin, "[data-connector-test]");
  await selectText(admin, '#platform-dialog [name="toolName"]', names.tool);
  await fill(admin, '#platform-dialog [name="arguments"]', JSON.stringify({ equipmentId: "PERMISSION-001" }));
  await click(admin, '#platform-dialog button[type="submit"]');
  await admin.waitForSelector("#platform-dialog .capability-result", { visible: true });
  assert((await admin.$eval("#platform-dialog .capability-result", node => node.textContent)).includes("fixture-ready"));
  assert.equal(requests.device.length, 1);
  assert.equal(requests.unexpected.length, 0);
  await click(admin, "#platform-dialog [data-dialog-close]");
  pass("管理员真实控件完成模型与设备回环正对照，各一次实际请求", { mode: "visible_ui", counts: counts() });
}

(async () => {
  assert(BASE && PASSWORD && process.env.TEST5_E2E_NETWORK_REPORT);
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, "isolated");
  assert.equal(new URL(BASE).hostname, "127.0.0.1");
  assert(![8011, 8012].includes(Number(new URL(BASE).port)));
  fs.mkdirSync(OUTPUT, { recursive: true });
  const upload = path.join(OUTPUT, names.file); fs.writeFileSync(upload, CONTENT, "utf8");
  const downloads = path.join(OUTPUT, "downloads"); fs.mkdirSync(downloads);
  try {
    fixture = await startFixture();
    browser = await puppeteer.launch({ executablePath: EDGE, headless: true, args: ["--no-first-run", "--disable-gpu"] });
    const adminContext = await browser.createBrowserContext(), memberContext = await browser.createBrowserContext();
    admin = await makePage(adminContext, "admin"); member = await makePage(memberContext, "member");
    await login(admin, "workspace-admin", PASSWORD);
    ids.workspace1 = await admin.evaluate(() => localStorage.getItem("agent_workspace_id"));
    await createPositiveControls();

    await visit(admin, "/system/roles", "[data-new-role]"); await click(admin, "[data-new-role]");
    await fill(admin, '#platform-dialog [name="name"]', names.role);
    await fill(admin, '#platform-dialog [name="code"]', "consumer-" + RUN);
    await admin.select('#platform-dialog [name="data_scope"]', "self");
    for (const code of ["home.view", "files.view", "files.export"]) await click(admin, `#platform-dialog [name="permissions"][value="${code}"]`);
    await save(admin, names.role);
    await visit(admin, "/system/users", "[data-new-user]"); await click(admin, "[data-new-user]");
    await fill(admin, '#platform-dialog [name="username"]', names.username);
    await fill(admin, '#platform-dialog [name="display_name"]', names.member);
    await fill(admin, '#platform-dialog [name="password"]', TEMP_PASSWORD);
    for (const label of await admin.$$("#platform-dialog label")) {
      if ((await label.evaluate(node => node.textContent)).includes(names.role)) { await (await label.$('input[name="role_ids"]')).click(); break; }
    }
    await save(admin, names.member);
    ids.member = await (await row(admin, names.username)).$eval("[data-edit-user]", node => node.dataset.editUser);
    await login(member, names.username, TEMP_PASSWORD, true);
    await fill(member, '#required-password-form [name="current_password"]', TEMP_PASSWORD);
    await fill(member, '#required-password-form [name="new_password"]', MEMBER_PASSWORD);
    await fill(member, '#required-password-form [name="confirm_password"]', MEMBER_PASSWORD);
    await click(member, '#required-password-form button[type="submit"]');
    await member.waitForSelector("#login-username", { visible: true });
    await login(member, names.username, MEMBER_PASSWORD);
    const me = JSON.parse((await request(member, "/api/auth/me")).body);
    assert.equal(me.user.id, ids.member); assert.equal(me.role, "member");
    assert.deepEqual(me.permissions.sort(), ["files.export", "files.view", "home.view"]);
    assert.equal(me.workspace.id, ids.workspace1);
    pass("独立浏览器成员完成强制改密和真实登录，仅有三个明确功能权限", { mode: "visible_ui", permissions: me.permissions });

    await visit(admin, "/system/workspaces", "[data-new-workspace]"); await click(admin, "[data-new-workspace]");
    await fill(admin, '#platform-dialog [name="code"]', "consumer-space-" + RUN);
    await fill(admin, '#platform-dialog [name="name"]', names.workspace); await save(admin, names.workspace);
    ids.workspace2 = await (await row(admin, names.workspace)).$eval("[data-switch-workspace]", node => node.dataset.switchWorkspace);
    assert.notEqual(ids.workspace1, ids.workspace2);
    await visit(admin, "/system/workspaces?tab=members", `[data-transfer-user="${ids.member}"]`);
    await click(admin, `[data-transfer-user="${ids.member}"]`);
    await admin.select('#platform-dialog [name="target_workspace_id"]', ids.workspace2);
    await admin.select('#platform-dialog [name="mode"]', "copy");
    await admin.select('#platform-dialog [name="target_role_code"]', "member"); await save(admin);

    await visit(admin, "/files", "[data-upload]"); await click(admin, "[data-upload]");
    await (await admin.waitForSelector('#platform-dialog input[type="file"]')).uploadFile(upload);
    await save(admin, names.file);
    ids.file = await (await row(admin, names.file)).$eval("[data-file-preview]", node => node.dataset.filePreview);
    await visit(member, "/files");
    assert(!(await member.$eval("#main-content", node => node.textContent)).includes(names.file));
    await denied("授权前预览直达", member, `/api/files/${ids.file}/text`);
    await denied("授权前下载直达", member, `/api/files/${ids.file}/download`);
    await denied("成员模型调用拒绝", member, "/api/models/probe/permission_embedding", { method: "POST", body: {} }, [403]);
    await denied("成员设备调用拒绝", member, `/api/connectors/${ids.connector}/test-invoke`, { method: "POST", body: { tool_name: names.tool, arguments: { equipmentId: "PERMISSION-001" } } }, [403]);
    pass("授权前列表隐藏、正文和下载拒绝，模型和设备实际新增请求均为零", { mode: "visible_ui_and_browser_same_origin_request", counts: counts() });

    await visit(admin, "/system/permissions?view=grants", "[data-new-grant]"); await click(admin, "[data-new-grant]");
    await selectText(admin, '#platform-dialog [name="resource_id"]', names.file);
    await selectText(admin, '#platform-dialog [name="subject_id"]', names.member);
    await click(admin, '#platform-dialog [name="actions"][value="export"]'); await save(admin, ids.file);
    ids.grant = await (await row(admin, ids.file)).$eval("[data-revoke-grant]", node => node.dataset.revokeGrant);
    await visit(member, "/files"); await row(member, names.file);
    assert.equal(await member.$("[data-file-edit], [data-file-delete], [data-upload], [data-file-copy]"), null);
    await click(member, `[data-file-preview="${ids.file}"]`);
    await member.waitForSelector("#platform-dialog .content-text-preview", { visible: true });
    assert.equal(await member.$eval("#platform-dialog .content-text-preview", node => node.textContent), CONTENT);
    await member.screenshot({ path: path.join(OUTPUT, "member-authorized-preview.png"), fullPage: true });
    await click(member, "#platform-dialog [data-dialog-close]");
    const cdp = await member.createCDPSession(); await cdp.send("Page.setDownloadBehavior", { behavior: "allow", downloadPath: downloads });
    await (await (await row(member, names.file)).$(".content-action-menu summary")).click();
    const downloading = member.waitForResponse(response => new URL(response.url()).pathname === `/api/files/${ids.file}/download`);
    await click(member, `[data-file-download="${ids.file}"]`);
    const downloaded = await downloading;
    assert.equal(downloaded.status(), 200); assert.equal(downloaded.headers()["cache-control"], "private, no-store");
    ids.oldDownloadUrl = downloaded.url();
    const destination = path.join(downloads, names.file);
    const deadline = Date.now() + 10000;
    while (!fs.existsSync(destination) && Date.now() < deadline) await sleep(100);
    assert.equal(fs.readFileSync(destination, "utf8"), CONTENT);
    await denied("只读成员不得修改已授权文件", member, `/api/files/${ids.file}/metadata`, { method: "PATCH", body: { version: 1, name: "越权改名" } }, [403]);
    pass("单资源授权后真实预览和下载字节正确；修改控件隐藏且直达修改403", { mode: "visible_ui_and_browser_same_origin_request", downloadSha256: sha(fs.readFileSync(destination)) });

    // 在授权仍有效时做跨租户负对照，避免把撤权造成的404冒充工作区隔离。
    const second = await request(member, "/api/auth/me", { workspace: ids.workspace2 });
    assert.equal(second.status, 200);
    const secondMe = JSON.parse(second.body);
    assert.equal(secondMe.user.id, ids.member); assert.equal(secondMe.workspace.id, ids.workspace2);
    assert.equal(secondMe.role, "member"); assert(secondMe.permissions.includes("files.export"));
    await denied("工作区二成员读取工作区一文件ID", member, `/api/files/${ids.file}/text`, { workspace: ids.workspace2 }, [404]);
    await denied("工作区二成员读取工作区一旧下载地址", member, ids.oldDownloadUrl, { workspace: ids.workspace2 }, [404]);
    await visit(admin, "/system/workspaces", `[data-switch-workspace="${ids.workspace2}"]`);
    await Promise.all([admin.waitForNavigation({ waitUntil: "networkidle0" }), click(admin, `[data-switch-workspace="${ids.workspace2}"]`)]);
    assert.equal(await admin.evaluate(() => localStorage.getItem("agent_workspace_id")), ids.workspace2);
    await denied("工作区二管理员也不可跨工作区下载", admin, ids.oldDownloadUrl, { workspace: ids.workspace2 }, [404]);
    await visit(admin, "/system/workspaces", `[data-switch-workspace="${ids.workspace1}"]`);
    await Promise.all([admin.waitForNavigation({ waitUntil: "networkidle0" }), click(admin, `[data-switch-workspace="${ids.workspace1}"]`)]);
    const stillAllowed = await request(member, ids.oldDownloadUrl);
    assert.equal(stillAllowed.status, 200); assert.equal(stillAllowed.body, CONTENT);
    pass("授权有效期间跨工作区ID和旧链接404；同身份回到原工作区仍200", { mode: "admin_visible_workspace_switch_and_member_explicit_header_request" });

    await visit(admin, "/system/permissions?view=grants", `[data-revoke-grant="${ids.grant}"]`);
    await click(admin, `[data-revoke-grant="${ids.grant}"]`); await confirm(admin, "撤销授权");
    await admin.waitForFunction(id => !document.querySelector(`[data-revoke-grant="${id}"]`), {}, ids.grant);
    // 成员页面保持授权后的旧DOM；撤权后立即点击原控件，强制走新的服务端鉴权。
    assert(await member.$(`[data-file-preview="${ids.file}"]`));
    await staleClickDenied("撤权后已开页面预览拒绝", `[data-file-preview="${ids.file}"]`, "text");
    await staleClickDenied("撤权后已开页面下载拒绝", `[data-file-download="${ids.file}"]`, "download");
    await denied("撤权后原下载地址拒绝", member, ids.oldDownloadUrl);
    await denied("撤权后内容Cookie旧链接拒绝", member, ids.oldDownloadUrl + "?workspace_id=" + ids.workspace1, { cookieOnly: true });
    await member.screenshot({ path: path.join(OUTPUT, "member-stale-page-denied.png"), fullPage: true });
    await visit(member, "/files");
    assert(!(await member.$eval("#main-content", node => node.textContent)).includes(names.file));
    await denied("撤权后模型调用仍拒绝", member, "/api/models/probe/permission_embedding", { method: "POST", body: {} }, [403]);
    await denied("撤权后设备调用仍拒绝", member, `/api/connectors/${ids.connector}/test-invoke`, { method: "POST", body: { tool_name: names.tool, arguments: { equipmentId: "PERMISSION-001" } } }, [403]);
    assert.deepEqual(counts(), { model: 1, device: 1, unexpected: 0 });
    const adminOriginal = await request(admin, ids.oldDownloadUrl);
    assert.equal(adminOriginal.status, 200); assert.equal(adminOriginal.body, CONTENT);
    pass("撤权后旧页面控件和旧URL拒绝，刷新隐藏；管理员原件不变且拒绝阶段模型/设备零请求", { mode: "visible_ui_and_browser_same_origin_request", totalCountsIncludingPositiveControls: counts() });
    assert.deepEqual(failures, []);
  } catch (error) {
    failures.push(error.stack || String(error));
    await activePage?.screenshot({ path: path.join(OUTPUT, "failure.png"), fullPage: true }).catch(() => {});
    throw error;
  } finally {
    fs.writeFileSync(path.join(OUTPUT, "result.json"), JSON.stringify({ ok: failures.length === 0, baseUrl: BASE, checks, failures, consoleErrors, observations, ids, requests,
      scope: "真实双身份文件消费及工作区隔离；浏览器直接请求单列。模型/设备各一次管理员正对照，成员拒绝阶段零请求；未覆盖知识召回、插件复用或所有资源类型。",
      sourceSha256: Object.fromEntries([__filename, path.resolve("tests/dsh/business_browser_acceptance.py")].map(file => [path.relative(process.cwd(), file), sha(fs.readFileSync(file))])) }, null, 2));
    console.log("Evidence " + OUTPUT);
    try { await browser?.close(); } finally { if (fixture) await new Promise(resolve => fixture.close(resolve)); }
  }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
