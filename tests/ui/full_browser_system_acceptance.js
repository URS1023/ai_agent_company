/*
 * 中文说明（系统管理端到端浏览器验收）：
 * 验证行为：工作区/成员/角色等管理操作全部通过可见控件完成；网络事件只用于暴露服务端错误。
 * 约束：刻意不使用 fetch 或应用内 API 辅助函数——任何业务改写都必须像管理员那样点出来。
 *
 * Browser-only system-administration acceptance.
 *
 * The script deliberately does not call fetch or an application API helper.
 * Every business mutation is performed through a visible control, exactly as
 * an administrator would operate the product. Network events are observed
 * only to surface server failures.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');

const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8027';
const PASSWORD = process.env.TEST5_E2E_PASSWORD;
const EDGE = process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const RUN = Date.now().toString(36);
const OUTPUT = path.resolve('.implementation/ui-results/full-browser-system-' + RUN);
const created = {
  department: `终验质量部-${RUN}`,
  departmentUpdated: `终验质量与设备部-${RUN}`,
  position: `终验工程师-${RUN}`,
  role: `终验业务角色-${RUN}`,
  username: `browser.qa.${RUN}`,
  displayName: `浏览器终验用户-${RUN}`,
  displayNameUpdated: `浏览器终验用户已更新-${RUN}`,
  dictionary: `终验问题等级-${RUN}`,
  notice: `终验公告-${RUN}`,
  job: `终验健康检查-${RUN}`,
  workspace: `终验工作区-${RUN}`,
  client: `终验接口客户端-${RUN}`,
};
const checks = [];
const failures = [];
let browser;
let page;
let expectingLoginFailure = false;

const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
function pass(name, detail = {}) {
  checks.push({ name, ...detail });
  console.log('PASS ' + name);
}

async function text(selector, root = page) {
  return root.$eval(selector, element => element.textContent.trim());
}

async function visibleHandle(root, selector, label, exact = true) {
  const handles = await root.$$(selector);
  for (const handle of handles) {
    const matches = await handle.evaluate((element, expected, strict) => {
      const value = (element.textContent || element.getAttribute('aria-label') || '').trim();
      const style = getComputedStyle(element);
      const visible = Boolean(element.getClientRects().length) && style.visibility !== 'hidden';
      return visible && !element.disabled && (strict ? value === expected : value.includes(expected));
    }, label, exact);
    if (matches) return handle;
  }
  throw new Error(`Visible control not found: ${selector} / ${label}`);
}

async function clickText(label, selector = 'button', root = page, exact = true) {
  const handle = await visibleHandle(root, selector, label, exact);
  await clickHandle(handle);
}

async function clickHandle(handle) {
  assert(handle, 'Expected a clickable element');
  await handle.evaluate(element => element.scrollIntoView({ block: 'center', inline: 'center' }));
  await sleep(80);
  await handle.click();
}

async function fill(selector, value, root = page) {
  const handle = await root.waitForSelector(selector, { visible: true, timeout: 20000 });
  const tag = await handle.evaluate(element => element.tagName);
  if (tag === 'SELECT') {
    await handle.select(String(value));
    return;
  }
  await handle.click({ clickCount: 3 });
  await page.keyboard.down('Control');
  await page.keyboard.press('KeyA');
  await page.keyboard.up('Control');
  if (value !== '') await handle.type(String(value));
}

async function selectOptionByText(selector, optionText, root = page) {
  const handle = await root.waitForSelector(selector, { visible: true });
  const value = await handle.evaluate((element, expected) => {
    const option = [...element.options].find(item => item.textContent.trim().includes(expected));
    return option?.value || '';
  }, optionText);
  assert(value, `Option not found in ${selector}: ${optionText}`);
  await handle.select(value);
  return value;
}

async function checkByLabel(labelText, name) {
  const labels = await page.$$('#platform-dialog[open] label');
  for (const label of labels) {
    const matches = await label.evaluate((element, expected) =>
      element.getClientRects().length && element.textContent.includes(expected), labelText);
    if (!matches) continue;
    const checkbox = await label.$(`input[type="checkbox"]${name ? `[name="${name}"]` : ''}`);
    if (checkbox) {
      const checked = await checkbox.evaluate(element => element.checked);
      if (!checked) await checkbox.click();
      return;
    }
  }
  throw new Error(`Checkbox label not found: ${labelText}`);
}

async function rowByText(value) {
  const rows = await page.$$('#main-content tbody tr');
  for (const row of rows) {
    if (await row.evaluate((element, expected) => element.textContent.includes(expected), value)) return row;
  }
  throw new Error(`Table row not found: ${value}`);
}

async function waitForContent(value) {
  await page.waitForFunction(expected => document.querySelector('#main-content')?.textContent.includes(expected),
    { timeout: 30000 }, value);
}

async function saveDialog(expectedContent = '') {
  const dialog = await page.waitForSelector('#platform-dialog[open]', { visible: true });
  const submit = await dialog.$('button[type="submit"]');
  assert(submit, 'Dialog has no submit button');
  await submit.click();
  await page.waitForFunction(() => {
    const current = document.querySelector('#platform-dialog');
    const error = current?.querySelector('.form-error');
    return !current?.open || (error && !error.hidden && error.textContent.trim());
  }, { timeout: 30000 });
  const outcome = await page.evaluate(() => {
    const current = document.querySelector('#platform-dialog');
    return {
      open: Boolean(current?.open),
      error: current?.querySelector('.form-error:not([hidden])')?.textContent.trim() || '',
    };
  });
  assert.equal(outcome.open, false, outcome.error || 'Dialog did not close');
  if (expectedContent) await waitForContent(expectedContent);
  await sleep(120);
}

async function confirmDialog(expectedContent = '') {
  return saveDialog(expectedContent);
}

async function visit(route) {
  const response = await page.goto(BASE + route, { waitUntil: 'networkidle0', timeout: 90000 });
  assert.equal(response.status(), 200, route);
  await page.waitForSelector('#main-content', { visible: true });
  await page.waitForFunction(() => document.querySelector('#main-content')?.textContent.trim().length > 0);
}

async function search(value) {
  await fill('#main-content [data-search-form] input[name="q"]', value);
  const filtered = page.waitForResponse(response => {
    if (response.request().method() !== 'GET') return false;
    try { return decodeURIComponent(response.url()).includes(value); }
    catch { return false; }
  }, { timeout: 30000 });
  await page.keyboard.press('Enter');
  const response = await filtered;
  assert(response.status() < 400, `Filter failed: ${response.status()} ${response.url()}`);
  await page.waitForFunction(expected => document.querySelector('#main-content tbody')?.textContent.includes(expected),
    { timeout: 30000 }, value);
  await sleep(100);
}

async function removeMatchingRow(value, selector, confirmLabel) {
  const row = await rowByText(value);
  const action = await row.$(selector);
  assert(action, `Missing ${selector} for ${value}`);
  await clickHandle(action);
  await page.waitForSelector('#platform-dialog[open]', { visible: true });
  await clickText(confirmLabel, '#platform-dialog[open] button');
  await page.waitForFunction(() => !document.querySelector('#platform-dialog')?.open, { timeout: 30000 });
  await page.waitForFunction(expected => !document.querySelector('#main-content')?.textContent.includes(expected),
    { timeout: 30000 }, value);
}

(async () => {
  assert(PASSWORD, 'Supply TEST5_E2E_PASSWORD');
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, 'isolated');
  assert(['127.0.0.1', 'localhost'].includes(new URL(BASE).hostname));
  fs.mkdirSync(OUTPUT, { recursive: true });

  browser = await puppeteer.launch({ executablePath: EDGE, headless: true, args: ['--no-first-run'] });
  page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 960 });
  page.on('pageerror', error => failures.push('pageerror ' + error.message));
  page.on('console', message => {
    if (message.type() === 'error' && !(expectingLoginFailure && message.text().includes('401'))) {
      failures.push('console ' + message.text());
    }
  });
  page.on('response', response => {
    if (response.status() >= 500) failures.push(`${response.status()} ${response.request().method()} ${response.url()}`);
  });

  await page.goto(BASE, { waitUntil: 'networkidle0' });
  await page.type('#login-username', 'workspace-admin');
  await page.type('#login-password', 'Known-Wrong-Password-2026');
  expectingLoginFailure = true;
  await page.click('#login-btn');
  await page.waitForFunction(() => {
    const node = document.querySelector('#platform-login-form .form-error');
    return node && !node.hidden && node.textContent.trim();
  });
  expectingLoginFailure = false;
  assert((await text('#platform-login-form .form-error')).length > 0);
  await fill('#login-password', PASSWORD);
  await page.click('#login-btn');
  await page.waitForSelector('.enterprise-area-grid', { visible: true });
  pass('Invalid credentials are rejected and valid administrator login succeeds through the login form');

  await visit('/system/departments');
  await page.click('[data-new-record]');
  await fill('#platform-dialog [name="name"]', created.department);
  await fill('#platform-dialog [name="code"]', `qa-dept-${RUN}`);
  await fill('#platform-dialog [name="sort_order"]', '31');
  await saveDialog(created.department);
  await search(created.department);
  let row = await rowByText(created.department);
  await clickHandle(await row.$('[data-edit-record]'));
  await fill('#platform-dialog [name="name"]', created.departmentUpdated);
  await saveDialog();
  await visit('/system/departments');
  await search(created.departmentUpdated);
  pass('Department can be created, searched and edited');

  await visit('/system/positions');
  await page.click('[data-new-record]');
  await fill('#platform-dialog [name="name"]', created.position);
  await fill('#platform-dialog [name="code"]', `qa-position-${RUN}`);
  await fill('#platform-dialog [name="description"]', '设备巡检、预警复核与质量追踪');
  await saveDialog(created.position);
  await search(created.position);
  pass('Position can be created and found through the normal list filter');

  await visit('/system/roles');
  await page.click('[data-new-role]');
  await fill('#platform-dialog [name="name"]', created.role);
  await fill('#platform-dialog [name="code"]', `qa-role-${RUN}`);
  await fill('#platform-dialog [name="description"]', '浏览器终验所需的最小业务权限');
  await fill('#platform-dialog [name="data_scope"]', 'workspace');
  for (const permission of ['home.view', 'files.view', 'knowledge.view', 'alerts.view', 'quality.view']) {
    const checkbox = await page.$(`#platform-dialog [name="permissions"][value="${permission}"]`);
    assert(checkbox, `Missing permission ${permission}`);
    await checkbox.click();
  }
  await saveDialog(created.role);
  await search(created.role);
  pass('Role can be created with an explicit data scope and selected functional permissions');

  await visit('/system/users');
  await page.click('[data-new-user]');
  await fill('#platform-dialog [name="username"]', created.username);
  await fill('#platform-dialog [name="display_name"]', created.displayName);
  await fill('#platform-dialog [name="password"]', `Temporary-${RUN}-9A`);
  await selectOptionByText('#platform-dialog [name="department_id"]', created.departmentUpdated);
  await fill('#platform-dialog [name="employee_code"]', `EMP-${RUN}`);
  await fill('#platform-dialog [name="email"]', `${RUN}@example.invalid`);
  await checkByLabel(created.position, 'position_ids');
  await checkByLabel(created.role, 'role_ids');
  await saveDialog(created.displayName);
  await search(created.username);
  row = await rowByText(created.username);
  assert((await row.evaluate(element => element.textContent)).includes(created.role));
  await clickHandle(await row.$('[data-permissions-user]'));
  await page.waitForSelector('#platform-dialog[open] .permission-tree', { visible: true });
  const permissionText = await text('#platform-dialog .permission-tree');
  assert(permissionText.includes('files.view') && permissionText.includes('knowledge.view'));
  await page.click('#platform-dialog [data-dialog-close]');

  row = await rowByText(created.username);
  await clickHandle(await row.$('[data-edit-user]'));
  await fill('#platform-dialog [name="display_name"]', created.displayNameUpdated);
  await saveDialog(created.displayNameUpdated);
  row = await rowByText(created.username);
  await clickHandle(await row.$('[data-password-user]'));
  await fill('#platform-dialog [name="new_password"]', `Reset-${RUN}-7B`);
  await fill('#platform-dialog [name="expires_in_hours"]', '24');
  await saveDialog();
  pass('User can be created with organization, position and role, inspected, edited and assigned a new temporary password');

  row = await rowByText(created.username);
  const detailLink = await row.$('a.cell-primary');
  assert(detailLink);
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'networkidle0' }),
    clickHandle(detailLink),
  ]);
  await waitForContent(created.displayNameUpdated);
  assert((await text('#main-content')).includes(created.departmentUpdated));
  await clickText('权限记录', '#main-content a');
  await page.waitForNavigation({ waitUntil: 'networkidle0' }).catch(() => {});
  await page.waitForSelector('.history-snapshots, [data-history-detail], .system-tool-tabs', { visible: true });
  assert((await text('#main-content')).includes(created.username));
  pass('User detail and permission change history are reachable through visible links');

  await visit('/system/settings');
  await page.click('[data-new-dictionary]');
  await fill('#platform-dialog [name="namespace"]', 'qa.issue.level');
  await fill('#platform-dialog [name="key"]', `warning-${RUN}`);
  await fill('#platform-dialog [name="label"]', created.dictionary);
  await fill('#platform-dialog [name="value_type"]', 'json');
  await fill('#platform-dialog [name="value"]', '{"severity":"warning","priority":2}');
  await fill('#platform-dialog [name="sort_order"]', '20');
  await saveDialog(created.dictionary);
  row = await rowByText(created.dictionary);
  await clickHandle(await row.$('[data-edit-dictionary]'));
  await fill('#platform-dialog [name="label"]', created.dictionary + '（已验证）');
  await saveDialog(created.dictionary + '（已验证）');
  pass('JSON dictionary entry can be created and edited with typed value validation');

  await visit('/system/workspaces');
  await page.click('[data-new-workspace]');
  await fill('#platform-dialog [name="code"]', `qa-space-${RUN}`);
  await fill('#platform-dialog [name="name"]', created.workspace);
  await fill('#platform-dialog [name="description"]', '浏览器终验隔离工作区');
  await saveDialog(created.workspace);
  row = await rowByText(created.workspace);
  await clickHandle(await row.$('[data-workspace-status="archived"]'));
  await confirmDialog(created.workspace);
  row = await rowByText(created.workspace);
  assert((await row.evaluate(element => element.textContent)).includes('已归档'));
  pass('Secondary workspace can be created and archived without changing the active workspace');

  await visit('/system/operations?tab=notices');
  await page.click('[data-new-notice]');
  await fill('#platform-dialog [name="title"]', created.notice);
  await fill('#platform-dialog [name="severity"]', 'warning');
  await fill('#platform-dialog [name="body"]', '这是一条由浏览器终验创建、发布并归档的站内公告。');
  await saveDialog(created.notice);
  row = await rowByText(created.notice);
  await clickHandle(await row.$('[data-publish-notice]'));
  await confirmDialog(created.notice);
  row = await rowByText(created.notice);
  assert((await row.evaluate(element => element.textContent)).includes('已发布'));
  await clickHandle(await row.$('[data-archive-notice]'));
  await confirmDialog(created.notice);
  pass('System notice follows draft, publish and archive states through the UI');

  await visit('/system/operations?tab=jobs');
  await page.click('[data-new-job]');
  await fill('#platform-dialog [name="code"]', `qa-health-${RUN}`);
  await fill('#platform-dialog [name="name"]', created.job);
  await fill('#platform-dialog [name="task_type"]', 'health');
  await fill('#platform-dialog [name="schedule_kind"]', 'interval');
  await fill('#platform-dialog [name="schedule_expression"]', '3600');
  await fill('#platform-dialog [name="status"]', 'paused');
  await fill('#platform-dialog [name="payload"]', '{}');
  await saveDialog(created.job);
  row = await rowByText(created.job);
  await clickHandle(await row.$('[data-run-job]'));
  await confirmDialog(created.job);
  await waitForContent(created.job);
  row = await rowByText(created.job);
  await clickHandle(await row.$('[data-job-action="archive"]'));
  await sleep(250);
  assert(!(await text('#platform-toast')).includes('失败'));
  pass('Paused health-check schedule can be created, manually executed and archived');

  await visit('/system/operations?tab=backups');
  await page.click('[data-new-backup]');
  await fill('#platform-dialog [name="name"]', `终验备份-${RUN}`);
  await saveDialog(`终验备份-${RUN}`);
  row = await rowByText(`终验备份-${RUN}`);
  await clickHandle(await row.$('[data-restore-plan]'));
  await page.waitForSelector('#platform-dialog[open]', { visible: true });
  const restoreText = await text('#platform-dialog');
  assert(restoreText.includes('恢复') && restoreText.includes('校验'));
  await page.click('#platform-dialog [data-dialog-close]');
  pass('SQLite backup is created and its guarded restore plan can be inspected');

  await visit('/system/development?tab=api');
  // 精确选择业务契约，避免目录新增同名健康检查后误点第一行；宿主探活接口不属于业务 OpenAPI。
  const healthOperation = 'system_tools_health_get';
  const healthPath = '/api/v1/system-tools/health';
  await fill('#main-content [data-api-filter] [name="q"]', healthOperation);
  await page.keyboard.press('Enter');
  await page.waitForFunction(expected => document.querySelector('#main-content tbody')?.textContent.includes(expected), {}, healthPath);
  const contract = await page.$(`#main-content [data-api-detail="${healthOperation}"]`);
  assert(contract);
  await clickHandle(contract);
  await page.waitForSelector('#platform-dialog[open] pre', { visible: true });
  const contractText = await text('#platform-dialog pre');
  const contractDialogText = await text('#platform-dialog');
  assert(
    contractText.includes('"request"')
      && contractText.includes('"response"')
      && contractDialogText.includes('operationId')
      && contractDialogText.includes(healthOperation)
      && contractDialogText.includes(healthPath),
  );
  await page.click('#platform-dialog [data-dialog-close]');
  pass('Generated API catalog can be filtered and its request/response contract opened');

  await visit('/system/development?tab=clients');
  await page.click('[data-new-client]');
  await fill('#platform-dialog [name="name"]', created.client);
  await fill('#platform-dialog [name="redirect_uris"]', 'https://client.example.invalid/callback');
  await fill('#platform-dialog [name="scopes"]', 'home.view\nfiles.view');
  await page.click('#platform-dialog button[type="submit"]');
  await page.waitForFunction(() => document.querySelector('#platform-dialog[open]')?.textContent.includes('请立即保存客户端密钥'));
  const initialSecretText = await text('#platform-dialog');
  assert(initialSecretText.includes('关闭后无法再次查看'));
  assert((await text('#platform-dialog code')).trim().length >= 32);
  await page.click('#platform-dialog [data-dialog-close]');
  await waitForContent(created.client);
  row = await rowByText(created.client);
  await clickHandle(await row.$('[data-rotate-client]'));
  await page.waitForSelector('#platform-dialog[open]', { visible: true });
  await clickText('轮换密钥', '#platform-dialog[open] button');
  await page.waitForFunction(() => document.querySelector('#platform-dialog[open]')?.textContent.includes('请立即保存新密钥'));
  const rotateText = await text('#platform-dialog');
  assert(rotateText.includes('仅显示一次') || rotateText.includes('新密钥'));
  assert((await text('#platform-dialog code')).trim().length >= 32);
  await page.click('#platform-dialog [data-dialog-close]');
  row = await rowByText(created.client);
  await clickHandle(await row.$('[data-disable-client]'));
  await page.waitForFunction(expected => {
    const rows = [...document.querySelectorAll('#main-content tbody tr')];
    const current = rows.find(item => item.textContent.includes(expected));
    return current?.textContent.includes('停用');
  }, { timeout: 30000 }, created.client);
  pass('OAuth-style client can be created, its secret rotated once and the client disabled');

  await visit('/system/users');
  await search(created.username);
  await removeMatchingRow(created.username, '[data-remove-user]', '移除成员');
  await visit('/system/roles');
  await search(created.role);
  await removeMatchingRow(created.role, '[data-delete-role]', '删除角色');
  await visit('/system/positions');
  await search(created.position);
  await removeMatchingRow(created.position, '[data-delete-record]', '删除岗位');
  await visit('/system/departments');
  await search(created.departmentUpdated);
  await removeMatchingRow(created.departmentUpdated, '[data-delete-record]', '删除部门');
  await visit('/system/settings');
  await removeMatchingRow(created.dictionary + '（已验证）', '[data-delete-dictionary]', '删除字典项');
  pass('Temporary user, role, position, department and dictionary records are removed through their own screens');

  await page.screenshot({ path: path.join(OUTPUT, 'system-final.png'), fullPage: true });
  assert.deepEqual(failures, []);
  fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({
    ok: true, baseUrl: BASE, checks, failures, created,
  }, null, 2));
  console.log('Evidence ' + OUTPUT);
})().catch(async error => {
  failures.push(error.stack || String(error));
  if (page && !page.isClosed()) {
    await page.screenshot({ path: path.join(OUTPUT, 'failure.png'), fullPage: true }).catch(() => {});
  }
  fs.mkdirSync(OUTPUT, { recursive: true });
  fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({
    ok: false, baseUrl: BASE, checks, failures, created,
  }, null, 2));
  console.error(error.stack || error);
  process.exitCode = 1;
}).finally(async () => {
  if (browser) await browser.close();
});
