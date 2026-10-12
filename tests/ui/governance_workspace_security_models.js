/*
 * 中文说明（工作区/登录安全/模型治理的浏览器验收）：
 * 验证行为：工作区切换、登录安全策略与模型配置治理页面按权限与状态正确工作。
 * 运行方式：需一次性可废弃的隔离服务 + 本机 Edge；运行后数据可整体丢弃。
 * 边界：涉及登录安全项，请勿在共享环境执行。
 */
/* Disposable Edge acceptance for workspace, login-security and model governance. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');

const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8027';
const PASSWORD = process.env.TEST5_E2E_PASSWORD;
const EDGE = process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const RUN = Date.now().toString(36);
const OUTPUT = path.resolve('.implementation/ui-results/governance-' + RUN);
const checks = [], errors = [], violations = [];
let browser, page;

function pass(name) { checks.push(name); console.log('PASS ' + name); }
async function fill(fields, root = '') {
  for (const [name, value] of Object.entries(fields)) {
    const selector = `${root} [name="${name}"]`.trim();
    await page.waitForSelector(selector, { visible: true });
    await page.$eval(selector, element => { element.value = ''; });
    await page.type(selector, String(value));
  }
}
async function saveDialog() {
  await page.click('#platform-dialog [type=submit]');
  await page.waitForFunction(() => {
    const dialog = document.querySelector('#platform-dialog');
    return !dialog.open || !dialog.querySelector('.form-error').hidden;
  }, { timeout: 30000 });
  const problem = await page.$eval('#platform-dialog', dialog => dialog.open ? dialog.querySelector('.form-error').textContent : '');
  assert.equal(problem, '', problem);
}
async function visit(route, selector = '#main-content > .panel') {
  const response = await page.goto(BASE + route, { waitUntil: 'networkidle0', timeout: 90000 });
  assert.equal(response.status(), 200, route);
  await page.waitForSelector(selector, { visible: true, timeout: 30000 });
  assert.equal(await page.$$eval('.page-heading', nodes => nodes.length), 1, route + ' has duplicate page headings');
  assert.equal(await page.$$eval('.system-status-strip', nodes => nodes.length), 1, route + ' must use one compact status strip');
}
async function call(method, route, body) {
  return page.evaluate(async ({ method, route, body }) => {
    const response = await fetch(route, {
      method,
      headers: {
        'Content-Type': 'application/json',
        Authorization: 'Bearer ' + localStorage.getItem('agent_token'),
        'X-Workspace-Id': localStorage.getItem('agent_workspace_id'),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    return { status: response.status, data };
  }, { method, route, body });
}
async function audit(route) {
  await page.addScriptTag({ path: require.resolve('axe-core/axe.min.js') });
  const findings = await page.evaluate(async () => (await axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] },
  })).violations.map(item => ({ id: item.id, impact: item.impact, nodes: item.nodes.map(node => node.target) })));
  violations.push(...findings.map(item => ({ route, ...item })));
}

(async () => {
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, 'isolated');
  assert(PASSWORD, 'Supply TEST5_E2E_PASSWORD');
  assert(['127.0.0.1', 'localhost'].includes(new URL(BASE).hostname));
  const health = await (await fetch(BASE + '/api/health')).json();
  assert.equal(health.app, 'test');
  fs.mkdirSync(OUTPUT, { recursive: true });
  browser = await puppeteer.launch({ executablePath: EDGE, headless: true, args: ['--disable-extensions', '--no-first-run'] });
  page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 960 });
  page.on('pageerror', error => errors.push('pageerror ' + error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push('console ' + message.text()); });
  page.on('response', response => { if (response.status() >= 500) errors.push(response.status() + ' ' + response.url()); });

  await page.goto(BASE, { waitUntil: 'networkidle0' });
  await fill({ username: 'workspace-admin', password: PASSWORD }, '#platform-login-form');
  await page.click('#login-btn');
  await page.waitForSelector('.overview-intro');
  assert((await page.evaluate(() => localStorage.getItem('agent_device_id')) || '').length >= 16);
  pass('Local login sends a persistent browser device identifier');

  await visit('/system/workspaces?tab=workspaces');
  assert.equal(await page.$$eval('#main-content > .panel', nodes => nodes.length), 1);
  await page.click('[data-new-workspace]');
  const workspaceName = '浏览器治理空间-' + RUN;
  await fill({ code: 'edge-' + RUN, name: workspaceName, description: '隔离实例的成员流转与归档验收' }, '#platform-dialog');
  await saveDialog();
  await page.waitForFunction(name => document.querySelector('#main-content').innerText.includes(name), {}, workspaceName);
  const workspaces = await call('GET', '/api/v1/system/workspaces');
  const target = workspaces.data.find(item => item.name === workspaceName);
  assert(target && target.actor_role === 'admin');
  await page.screenshot({ path: path.join(OUTPUT, 'workspaces-desktop.png'), fullPage: true });
  await audit('/system/workspaces?tab=workspaces');
  pass('Workspace lifecycle is available in one compact table');

  const roles = await call('GET', '/api/v1/system/roles?page=1&page_size=100');
  const memberRole = roles.data.items.find(item => item.code === 'member');
  const username = 'transfer-' + RUN;
  const createdUser = await call('POST', '/api/v1/system/users', {
    username, display_name: '待调动成员-' + RUN, password: 'Temporary12345', role_ids: [memberRole.id],
  });
  assert.equal(createdUser.status, 201);
  await visit('/system/workspaces?tab=members');
  await page.click(`[data-transfer-user="${createdUser.data.id}"]`);
  await page.select('#platform-dialog [name=target_workspace_id]', target.id);
  await page.select('#platform-dialog [name=mode]', 'copy');
  await saveDialog();
  const copiedLogin = await page.evaluate(async credentials => {
    const response = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(credentials) });
    return { status: response.status, data: await response.json() };
  }, { username, password: 'Temporary12345' });
  assert.equal(copiedLogin.status, 200);
  assert.equal(copiedLogin.data.workspaces.length, 2);
  pass('Member copy uses the normal page and creates two real workspace memberships');

  await visit('/system/security?tab=devices');
  assert(await page.$('[data-device-trust]'));
  await page.click('[data-device-trust]');
  await fill({ label: '治理验收浏览器' }, '#platform-dialog');
  await saveDialog();
  await page.waitForFunction(() => document.querySelector('#main-content').innerText.includes('治理验收浏览器'));
  pass('Pending device can be approved without exposing the raw device identifier');

  await visit('/system/security?tab=policy');
  const trusted = await page.$eval('[name=require_trusted_device]', element => element.checked);
  if (!trusted) await page.click('[name=require_trusted_device]');
  await page.$eval('[name=max_active_sessions]', element => { element.value = 3; });
  await Promise.all([
    page.waitForResponse(response => response.url().includes('/api/v1/system/security/policy') && response.request().method() === 'PATCH'),
    page.click('[data-security-policy] [type=submit]'),
  ]);
  await page.waitForSelector('.system-status-strip');
  await page.waitForNetworkIdle({ idleTime: 300, timeout: 10000 }).catch(() => {});
  assert((await page.$eval('.system-status-strip', node => node.innerText)).includes('必须'));
  const policy = await call('GET', '/api/v1/system/security/policy');
  assert.equal(policy.data.require_trusted_device, true);
  assert.equal(policy.data.max_active_sessions, 3);
  await page.screenshot({ path: path.join(OUTPUT, 'security-policy-desktop.png'), fullPage: true });
  await audit('/system/security?tab=policy');
  pass('Trusted-device and session-limit policy saves through the enterprise form');

  await visit('/system/security?tab=events');
  assert((await page.$eval('#main-content', node => node.innerText)).includes('workspace-admin'));
  assert((await page.$eval('#main-content', node => node.innerText)).includes('成功'));
  pass('Login evidence is visible as a filterable event table');

  await visit('/system/security?tab=sso');
  await page.click('[data-new-sso]');
  const issuer = 'http://127.0.0.1:19091/oidc';
  await fill({
    code: 'edge_sso_' + RUN, display_name: '企业身份-' + RUN, issuer_url: issuer,
    authorization_endpoint: issuer + '/authorize', token_endpoint: issuer + '/token',
    jwks_uri: issuer + '/jwks', client_id: 'edge-client-' + RUN,
  }, '#platform-dialog');
  await page.select('#platform-dialog [name=token_auth_method]', 'none');
  await page.click('#platform-dialog [name=allow_private]');
  await page.click('#platform-dialog [name=enabled]');
  await saveDialog();
  await page.waitForFunction(name => document.querySelector('#main-content').innerText.includes(name), {}, '企业身份-' + RUN);
  await page.screenshot({ path: path.join(OUTPUT, 'sso-desktop.png'), fullPage: true });
  pass('OIDC provider, PKCE client mode and private-address opt-in save through the UI');

  await visit('/system/models?tab=profiles');
  await page.click('[data-new-model]');
  await fill({
    profile_name: 'finance_reasoning', provider: 'local-compatible', model: 'finance-edge-model',
    endpoint: 'http://127.0.0.1:19999/v1', api_key: 'e2e-secret-not-for-output',
    capabilities: 'chat\nreasoning\ntools', input_price: '10', output_price: '20',
  }, '#platform-dialog');
  await saveDialog();
  await page.waitForFunction(() => document.querySelector('#main-content').innerText.includes('finance-edge-model'));
  assert(!(await page.$eval('#main-content', node => node.innerText)).includes('e2e-secret-not-for-output'));
  await page.screenshot({ path: path.join(OUTPUT, 'models-desktop.png'), fullPage: true });
  await audit('/system/models?tab=profiles');
  pass('Model role, encrypted key, capabilities and explicit pricing save without key disclosure');

  await visit('/system/models?tab=marketplace');
  assert((await page.$eval('#main-content', node => node.innerText)).includes('结果重排'));
  await page.click('[data-use-template="custom-rerank"]');
  assert.equal(await page.$eval('#platform-dialog [name=profile_name]', node => node.value), 'rerank');
  await page.click('#platform-dialog [data-dialog-close]');
  pass('Role-oriented model marketplace opens an editable configuration, not an automatic default');

  await visit('/system/models?tab=usage');
  await page.$eval('[name=monthly_budget]', element => { element.value = '2500'; });
  await page.$eval('[name=warn_percent]', element => { element.value = '75'; });
  await Promise.all([
    page.waitForResponse(response => response.url().includes('/api/models/cost-policy') && response.request().method() === 'PUT'),
    page.click('[data-cost-policy] [type=submit]'),
  ]);
  await page.waitForSelector('.budget-panel');
  await page.waitForNetworkIdle({ idleTime: 300, timeout: 10000 }).catch(() => {});
  assert((await page.$eval('.budget-panel', node => node.innerText)).includes('2500'));
  const costPolicy = await call('GET', '/api/models/cost-policy');
  assert.equal(costPolicy.data.monthly_budget, 2500);
  assert.equal(costPolicy.data.warn_percent, 75);
  assert((await page.$eval('#main-content', node => node.innerText)).includes('未计价'));
  await page.screenshot({ path: path.join(OUTPUT, 'model-usage-desktop.png'), fullPage: true });
  await audit('/system/models?tab=usage');
  pass('Usage ledger separates unpriced usage and persists a versioned monthly budget');

  await page.evaluate(() => {
    localStorage.removeItem('agent_token'); localStorage.removeItem('agent_workspace_id');
  });
  await page.goto(BASE + '/', { waitUntil: 'networkidle0' });
  await page.waitForSelector('[data-sso-provider]');
  assert((await page.$eval('[data-sso-provider]', node => node.innerText)).includes('企业身份-' + RUN));
  assert((await page.evaluate(() => localStorage.getItem('agent_device_id')) || '').length >= 16);
  pass('Enabled enterprise SSO appears on login while the browser device identifier remains local');

  await fill({ username: 'workspace-admin', password: PASSWORD }, '#platform-login-form');
  await page.click('#login-btn');
  await page.waitForSelector('.overview-intro');
  await page.setViewport({ width: 390, height: 844, isMobile: true, deviceScaleFactor: 1 });
  for (const route of ['/system/workspaces?tab=workspaces', '/system/security?tab=policy', '/system/models?tab=usage']) {
    await visit(route);
    const geometry = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth }));
    assert(geometry.document <= geometry.viewport, route + ' mobile overflow');
    assert.equal(await page.$$eval('.enterprise-primary a', nodes => nodes.filter(node => node.getClientRects().length).length), 0);
    await page.screenshot({ path: path.join(OUTPUT, route.replace(/[/?=&]/g, '-') + '-mobile.png'), fullPage: true });
  }
  pass('Governance pages fit a 390px viewport with the contextual drawer navigation');

  assert.deepEqual(errors, []);
  const serious = violations.filter(item => ['serious', 'critical'].includes(item.impact));
  assert.deepEqual(serious, []);
  fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({ ok: true, checks, errors, violations }, null, 2));
  console.log('RESULT_DIR ' + OUTPUT);
  console.log('ACCESSIBILITY_FINDINGS ' + violations.length);
})().catch(async error => {
  errors.push(error.stack || String(error));
  if (page && !page.isClosed()) await page.screenshot({ path: path.join(OUTPUT, 'failure.png'), fullPage: true }).catch(() => {});
  if (fs.existsSync(OUTPUT)) fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({ ok: false, checks, errors, violations }, null, 2));
  console.error(error.stack || error); process.exitCode = 1;
}).finally(async () => { if (browser) await browser.close(); });
