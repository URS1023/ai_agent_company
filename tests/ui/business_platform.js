/*
 * 中文说明（平台基础能力的真实浏览器检查）：
 * 验证行为：登录、外壳布局与基础接口在真实浏览器中可用（"foundation" 基线流程）。
 * 运行方式：只能对显式隔离的 APP_ENV=test 服务运行（TEST5_E2E_BASE_URL，默认 8011），
 *   需本机 Edge 与 TEST5_E2E_PASSWORD；结果写入 .implementation/ui-results/foundation-<run>/。
 * 边界：收集页面错误与步骤轨迹，失败时据此定位，不需要模型。
 */
/* Real-browser checks. Run only against an explicitly isolated APP_ENV=test server. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const puppeteer = require("puppeteer-core");

const BASE = process.env.TEST5_E2E_BASE_URL || "http://127.0.0.1:8011";
const PASSWORD = process.env.TEST5_E2E_PASSWORD;
const RUN = Date.now().toString(36);
const OUTPUT = path.resolve(".implementation", "ui-results", "foundation-" + RUN);
const errors = [];
const steps = [];
let browser;
let page;

function record(label) { steps.push(label); console.log("PASS " + label); }
async function fill(target, fields) {
  for (const [name, value] of Object.entries(fields)) {
    const selector = `[name="${name}"]`;
    await target.waitForSelector(selector, { visible: true });
    await target.$eval(selector, (element) => { element.value = ""; });
    await target.type(selector, String(value));
  }
}
async function visit(target, route, selector) {
  const response = await target.goto(BASE + route, { waitUntil: "networkidle0" });
  assert.equal(response.status(), 200, route + " must support a direct browser refresh");
  await target.waitForSelector(selector, { visible: true });
}
async function saveDialog(target = page) {
  await target.click('#platform-dialog [type="submit"]');
  await target.waitForFunction(() => {
    const dialog = document.querySelector("#platform-dialog");
    return !dialog.open || !dialog.querySelector(".form-error").hidden;
  });
  const problem = await target.$eval("#platform-dialog", (dialog) => dialog.open ? dialog.querySelector(".form-error").textContent : "");
  assert.equal(problem, "", "form submission failed: " + problem);
}
async function request(target, route, method = "GET", body) {
  return target.evaluate(async (url, method, body) => {
    const response = await fetch(url, { method, headers: {
      "Content-Type": "application/json", Authorization: "Bearer " + localStorage.getItem("agent_token"),
      "X-Workspace-Id": localStorage.getItem("agent_workspace_id"),
    }, body: body === undefined ? undefined : JSON.stringify(body) });
    return { status: response.status, data: await response.json() };
  }, route, method, body);
}
async function login(target, username, password) {
  await visit(target, "/", "#platform-login-form");
  await fill(target, { username, password });
  await target.click("#login-btn");
}
async function createOrganizationRecord(kind, name, code) {
  await visit(page, "/system/" + kind, "[data-new-record]");
  await page.click("[data-new-record]");
  await fill(page, { name, code });
  await saveDialog();
  const result = await request(page, "/api/v1/system/" + kind + "?q=" + encodeURIComponent(name));
  assert.equal(result.status, 200);
  assert.equal(result.data.total, 1);
  return result.data.items[0];
}

(async () => {
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, "isolated", "Set TEST5_E2E_ALLOW_WRITE=isolated only for the disposable test server");
  assert(PASSWORD, "An isolated bootstrap password is required");
  assert(["127.0.0.1", "localhost"].includes(new URL(BASE).hostname), "Use a local isolated server");
  const health = await (await fetch(BASE + "/api/health")).json();
  assert.equal(health.app, "test", "Never run write tests against a normal workspace");
  fs.mkdirSync(OUTPUT, { recursive: true });
  browser = await puppeteer.launch({
    executablePath: process.env.TEST5_EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    headless: true, args: ["--disable-extensions", "--no-first-run"],
  });
  page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 960 });
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("response", (response) => { if (response.status() >= 500) errors.push(response.status() + " " + response.url()); });
  await login(page, "workspace-admin", PASSWORD);
  await page.waitForSelector(".overview-intro");
  assert.equal(await page.$("#prompt-textarea"), null, "Home must not be a chat window");
  assert(await page.$('.enterprise-area-link[href="/ai"]'), "AI must remain independently accessible");
  assert(await page.$('.enterprise-area-link[href="/system/users"]'));
  await page.screenshot({ path: path.join(OUTPUT, "home-desktop.png") });
  record("ordinary business home with separate AI and user-management routes");

  const department = await createOrganizationRecord("departments", "浏览器验收部门 " + RUN, "ui-dept-" + RUN);
  const position = await createOrganizationRecord("positions", "浏览器验收岗位 " + RUN, "ui-job-" + RUN);
  record("department and position created using their own forms");

  await visit(page, "/system/roles", "[data-new-role]");
  await page.click("[data-new-role]");
  await fill(page, { name: "资料只读 " + RUN, code: "ui-reader-" + RUN, description: "隔离环境浏览器自动验收" });
  await page.click('#platform-dialog [name="permissions"][value="home.view"]');
  await page.click('#platform-dialog [name="permissions"][value="files.view"]');
  await page.select('#platform-dialog [name="data_scope"]', "self");
  await page.screenshot({ path: path.join(OUTPUT, "role-form-desktop.png") });
  await saveDialog();
  const role = (await request(page, "/api/v1/system/roles?q=" + encodeURIComponent("资料只读 " + RUN))).data.items[0];
  assert.equal(role.data_scope, "self");
  assert.deepEqual([...role.permissions].sort(), ["files.view", "home.view"]);
  record("custom role, action permissions and self-data scope saved");

  await visit(page, "/system/users", "[data-new-user]");
  await page.click("[data-new-user]");
  const username = "ui-reader-" + RUN;
  const temporaryPassword = "Temp2026#" + RUN;
  const changedPassword = "Personal2026#" + RUN;
  await fill(page, { username, display_name: "浏览器验收用户 " + RUN, password: temporaryPassword, email: "browser@example.invalid" });
  await page.select('#platform-dialog [name="department_id"]', department.id);
  await page.click(`#platform-dialog [name="position_ids"][value="${position.id}"]`);
  await page.click(`#platform-dialog [name="role_ids"][value="${role.id}"]`);
  await saveDialog();
  const user = (await request(page, "/api/v1/system/users?q=" + username)).data.items[0];
  assert.equal(user.department_id, department.id);
  assert.deepEqual(user.position_ids, [position.id]);
  assert.deepEqual(user.role_ids, [role.id]);
  await page.screenshot({ path: path.join(OUTPUT, "users-desktop.png") });
  await visit(page, "/system/users/" + user.id, ".detail-grid");
  await page.reload({ waitUntil: "networkidle0" });
  assert(await page.$(".detail-grid"));
  record("user form, organization assignment, role assignment and refreshable detail page");

  const memberContext = await browser.createBrowserContext();
  const member = await memberContext.newPage();
  member.on("pageerror", (error) => errors.push("member: " + error.message));
  await login(member, username, temporaryPassword);
  await member.waitForSelector("#required-password-form");
  await fill(member, { current_password: temporaryPassword, new_password: changedPassword, confirm_password: changedPassword });
  await member.click('#required-password-form [type="submit"]');
  await member.waitForSelector("#platform-login-form");
  await fill(member, { username, password: changedPassword });
  await member.click("#login-btn");
  await member.waitForSelector(".overview-intro");
  assert.equal(await member.$('.enterprise-area-link[data-business-area="system"]'), null);
  assert.equal(await member.$('.enterprise-area-link[data-business-area="ai"]'), null);
  assert.equal((await request(member, "/api/v1/system/users")).status, 403);
  assert.equal((await request(member, "/api/files")).status, 200);
  record("first-login password change and server-side role enforcement");

  await visit(page, "/system/permissions", "[data-new-grant]");
  await page.click("[data-new-grant]");
  await page.waitForSelector('#platform-dialog [name="resource_type"]');
  await page.select('#platform-dialog [name="resource_type"]', "plugins");
  await page.waitForFunction(() => document.querySelector('#platform-dialog [name="resource_id"]').options.length > 1);
  const pluginId = await page.$eval('#platform-dialog [name="resource_id"]', (element) => element.options[1].value);
  await page.select('#platform-dialog [name="resource_id"]', pluginId);
  await page.select('#platform-dialog [name="subject_id"]', user.id);
  await page.waitForNetworkIdle({ idleTime: 300, timeout: 5000 });
  assert.equal(
    await page.$eval('#platform-dialog [name="resource_id"]', (element) => element.value),
    pluginId,
    "A slower request for the previous resource type must not replace the selected plugin",
  );
  await saveDialog();
  const grants = (await request(page, "/api/v1/system/resource-grants")).data.items;
  const grant = grants.find((item) => item.resource_id === pluginId && item.subject_id === user.id);
  assert(grant);
  assert.equal((await request(member, "/api/plugins")).status, 403, "Resource sharing must never grant a missing action permission");
  await page.click(`[data-revoke-grant="${grant.id}"]`);
  await saveDialog();
  record("resource-grant form and revocation without permission escalation");

  await visit(page, "/system/settings", "[data-new-dictionary]");
  await page.click('[data-setting="ui.page_size"]');
  await fill(page, { value: "30" });
  await saveDialog();
  await page.click("[data-new-dictionary]");
  await fill(page, { namespace: "ui-" + RUN, key: "warning", label: "预警等级", value: "2" });
  await page.select('#platform-dialog [name="value_type"]', "number");
  await saveDialog();
  const dictionary = (await request(page, "/api/v1/system/dictionary")).data.items.find((item) => item.namespace === "ui-" + RUN);
  assert.equal(dictionary.value, 2);
  await page.click(`[data-edit-dictionary="${dictionary.id}"]`);
  await fill(page, { label: "预警等级（已修改）", value: "3" });
  await saveDialog();
  await page.click(`[data-delete-dictionary="${dictionary.id}"]`);
  await saveDialog();
  record("typed settings and dictionary create/edit/delete");

  await visit(page, "/system/menus", '[data-menu="files"]');
  await page.click('[data-menu="files"]');
  await fill(page, { label: "文件与生成结果（验收）" });
  await saveDialog();
  await page.keyboard.down("Control"); await page.keyboard.press("KeyK"); await page.keyboard.up("Control");
  await page.waitForFunction(() => document.querySelector('.enterprise-search-results a[href="/files"]')?.textContent.includes("验收"));
  await page.keyboard.press("Escape");
  await page.click('[data-menu="files"]');
  await fill(page, { label: "" });
  await saveDialog();
  record("menu labels update the real business navigation");

  await visit(page, "/system/api", "[data-download-collection]");
  await page.click('[data-operation="system_list_users"]');
  await page.waitForSelector("#platform-dialog[open] .json-preview");
  await page.click("#platform-dialog [data-dialog-close]");
  const collection = await request(page, "/api/v1/platform/api-collection");
  assert.equal(collection.status, 200);
  assert(collection.data.item.length > 0);
  record("actual API catalog and collection are accessible from a standalone page");

  await visit(page, "/system/sessions", "[data-revoke]");
  const sessions = (await request(page, "/api/v1/system/sessions")).data.items;
  const memberSession = sessions.find((item) => item.user_id === user.id);
  assert(memberSession);
  await page.click(`[data-revoke="${memberSession.id}"]`);
  await saveDialog();
  assert.equal((await request(member, "/api/auth/me")).status, 401);
  await memberContext.close();
  record("revoking a session invalidates the other browser immediately");

  await page.setViewport({ width: 390, height: 844, isMobile: true });
  await visit(page, "/", ".enterprise-area-grid");
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Mobile home must not overflow");
  await page.select('.enterprise-area-select select', '/system/users');
  await page.waitForSelector('[data-new-user]');
  await page.click("[data-toggle-navigation]");
  assert.equal(await page.$eval("[data-toggle-navigation]", (element) => element.getAttribute("aria-expanded")), "true");
  await page.screenshot({ path: path.join(OUTPUT, "home-mobile-menu.png") });
  await page.keyboard.press("Escape");
  await visit(page, "/system/users", "[data-new-user]");
  await page.click("[data-new-user]");
  assert(await page.$eval("#platform-dialog", (element) => { const rect = element.getBoundingClientRect(); return rect.left >= 0 && rect.right <= innerWidth && element.scrollWidth <= element.clientWidth + 1; }));
  await page.screenshot({ path: path.join(OUTPUT, "user-form-mobile.png") });
  await page.click("#platform-dialog [data-dialog-close]");
  record("mobile navigation, normal home and user form fit the viewport");

  await page.setViewport({ width: 1440, height: 960 });
  await visit(page, "/ai", "#prompt-textarea");
  assert(await page.$("#composer-form"));
  assert.equal(await page.$('[data-testid="business-shell"]'), null);
  await page.screenshot({ path: path.join(OUTPUT, "ai-independent-module.png") });
  record("existing AI composer preserved at its independent /ai route");
  assert.deepEqual(errors, [], "No uncaught frontend exceptions or 500 responses are allowed");
  fs.writeFileSync(path.join(OUTPUT, "result.json"), JSON.stringify({ ok: true, base: BASE, steps, errors }, null, 2));
  console.log("RESULT_DIR " + OUTPUT);
})().catch(async (error) => {
  console.error(error.stack || error);
  if (page && !page.isClosed()) await page.screenshot({ path: path.join(OUTPUT, "failure.png") }).catch(() => {});
  fs.writeFileSync(path.join(OUTPUT, "result.json"), JSON.stringify({ ok: false, steps, errors, failure: String(error) }, null, 2));
  console.error("RESULT_DIR " + OUTPUT);
  process.exitCode = 1;
}).finally(async () => { if (browser) await browser.close(); });
