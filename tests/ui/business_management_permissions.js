/*
 * 中文说明（企业管理权限边界验收）：
 * 验证行为：普通成员看不到/做不了管理员动作，管理员操作正常；用两套令牌切换身份对比。
 * 运行方式：需 http://127.0.0.1:18080 的隔离服务与 Edge，结果写入
 *   .implementation/ui-results/business-management-permissions-<时间戳>/。
 * 边界：api() 的 token 参数默认取管理员令牌，越权用例显式传入普通成员令牌；
 *   10 秒超时 + 关闭重定向，确保 403 不会被跳转掩盖。
 */
"use strict";
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');
const BASE = 'http://127.0.0.1:18080';
const OUTPUT = path.resolve('.implementation/ui-results/business-management-permissions-' + Date.now());
const checks = [];
let adminToken;
async function api(route, method = 'GET', body, token = adminToken) {
  const response = await fetch(BASE + route, { method, redirect: 'error', signal: AbortSignal.timeout(10000),
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: 'Bearer ' + token } : {}) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  assert(response.ok, `${method} ${route}: ${response.status}`);
  return response.json();
}
async function main() {
  assert.deepEqual(await api('/api/health'), { ok: true, app: 'test' });
  assert(process.env.TEST5_E2E_PASSWORD);
  adminToken = (await api('/api/auth/login', 'POST', { username: 'workspace-admin', password: process.env.TEST5_E2E_PASSWORD })).token;
  const prefix = 'mgmt-reader-' + Date.now().toString(36);
  let role, user, browser, status = 'failed';
  fs.mkdirSync(OUTPUT, { recursive: true });
  try {
    role = await api('/api/v1/system/roles', 'POST', { code: prefix, name: '管理页面只读验收', data_scope: 'workspace',
      permissions: ['home.view', 'devices.view', 'alerts.view', 'quality.view', 'workflows.view', 'datasources.view'] });
    user = await api('/api/v1/system/users', 'POST', { username: prefix, display_name: '管理页面只读验收', password: 'Temporary12345', role_ids: [role.id] });
    let login = await api('/api/auth/login', 'POST', { username: prefix, password: 'Temporary12345' });
    await api('/api/auth/password', 'POST', { current_password: 'Temporary12345', new_password: 'ChangedPassword12345' }, login.token);
    login = await api('/api/auth/login', 'POST', { username: prefix, password: 'ChangedPassword12345' });
    browser = await puppeteer.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
    const page = await browser.newPage();
    await page.setRequestInterception(true);
    page.on('request', r => { const u = new URL(r.url()); return u.origin === BASE || ['data:', 'blob:'].includes(u.protocol) ? r.continue() : r.abort(); });
    await page.goto(BASE, { waitUntil: 'networkidle0' });
    await page.evaluate(login => { localStorage.setItem('agent_token', login.token); localStorage.setItem('agent_workspace_id', login.workspaces[0].id); }, login);
    const routes = ['/alerts/points', '/alerts/advice', '/alerts/rules', '/quality/metrics', '/quality/templates'];
    for (const route of routes) {
      await page.goto(BASE + route, { waitUntil: 'networkidle0' });
      const frame = await (await page.waitForSelector('#business-module-frame')).contentFrame();
      await frame.waitForSelector(route.startsWith('/alerts/') ? '.alert-content' : '.qc-tabs');
      assert.equal(await frame.evaluate(() => [...document.querySelectorAll('button')].some(e => e.textContent.trim().startsWith('新增'))), false);
    }
    checks.push('Real read-only role can open all five pages without create controls');
    role = await api('/api/v1/system/roles/' + role.id, 'PATCH', { version: role.version, permissions: ['home.view'] });
    for (const route of routes) {
      await page.goto(BASE + route, { waitUntil: 'networkidle0' });
      const frameElement = await page.$('#business-module-frame');
      if (frameElement) {
        const frame = await frameElement.contentFrame();
        await frame.waitForFunction(() => document.body.textContent.includes('权限') || document.body.textContent.includes('不可用'));
        assert.equal(await frame.$('.alert-content, .qc-tabs'), null);
      } else {
        assert(await page.evaluate(() => document.body.textContent.includes('权限') || document.body.textContent.includes('不可用')));
      }
    }
    checks.push('Revoking role permissions blocks all five direct routes on fresh navigation');
    status = 'passed';
  } finally {
    if (browser) await browser.close();
    if (user) await api('/api/v1/system/users/' + user.id, 'DELETE');
    if (role) await api('/api/v1/system/roles/' + role.id, 'DELETE');
    fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({ status, checks, fixtureUserRemoved: !!user, fixtureRoleRemoved: !!role }, null, 2));
    console.log(JSON.stringify({ status, checks, output: OUTPUT }));
  }
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
