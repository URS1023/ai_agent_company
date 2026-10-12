/*
 * 中文说明（企业管理页真实浏览器验收，固定连本机 18080 隔离服务）：
 * 验证行为：企业管理相关页面在真实浏览器中的创建/展示/权限行为。
 * 运行方式：需 http://127.0.0.1:18080 的隔离服务与 Edge；输出目录可用 TEST5_E2E_OUTPUT 覆盖，
 *   默认 .implementation/ui-results/business-management-<时间戳>/。
 * 边界：api() 带 10 秒超时且 redirect 关闭，避免静默跟随跳转掩盖鉴权失败；会创建业务数据。
 */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const puppeteer = require("puppeteer-core");
const BASE = "http://127.0.0.1:18080";
const OUTPUT = path.resolve(process.env.TEST5_E2E_OUTPUT || ".implementation/ui-results/business-management-" + Date.now());
const checks = [], failures = [], created = [];
let token;
async function api(route, method = "GET", body) {
  const response = await fetch(BASE + route, { method, redirect: "error", signal: AbortSignal.timeout(10000),
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  assert(response.ok, `${method} ${route}: ${response.status}`);
  return response.json();
}
async function main() {
  assert.deepEqual(await api('/api/health'), { ok: true, app: "test" });
  assert(process.env.TEST5_E2E_PASSWORD);
  token = (await api('/api/auth/login', 'POST', { username: 'workspace-admin', password: process.env.TEST5_E2E_PASSWORD })).token;
  const prefix = "MGMT-" + Date.now().toString(36);
  fs.mkdirSync(OUTPUT, { recursive: true });
  const device = (await api('/api/v1/alerts/devices', 'POST', { name: prefix + '-device', code: prefix, domains: ['alert', 'quality'] })).data;
  created.push({ route: '/api/v1/alerts/devices', row: device });
  const browser = await puppeteer.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  let status = 'failed', page;
  try {
    page = await browser.newPage();
    await page.setViewport({ width: 1440, height: 960 });
    await page.setRequestInterception(true);
    page.on('request', r => { const u = new URL(r.url()); return u.origin === BASE || ['data:', 'blob:'].includes(u.protocol) ? r.continue() : r.abort(); });
    page.on('pageerror', e => failures.push(e.message));
    page.on('dialog', d => d.accept());
    await page.goto(BASE, { waitUntil: 'networkidle0' });
    await page.type('#login-username', 'workspace-admin');
    await page.type('#login-password', process.env.TEST5_E2E_PASSWORD);
    await page.click('#login-btn');
    await page.waitForSelector('.enterprise-area-grid');
    async function visit(route) {
      await page.goto(BASE + route, { waitUntil: 'networkidle0' });
      return (await page.waitForSelector('#business-module-frame')).contentFrame();
    }
    async function button(frame, text) {
      await frame.waitForFunction(text => [...document.querySelectorAll('button')].some(e => e.textContent.trim() === text && !e.disabled), {}, text);
      await frame.evaluate(text => [...document.querySelectorAll('button')].find(e => e.textContent.trim() === text && !e.disabled).click(), text);
    }
    async function field(frame, label, value, tag = 'input') {
      await frame.waitForFunction((label, tag) => [...document.querySelectorAll('.el-dialog label')].some(e => e.textContent.trim().startsWith(label) && e.querySelector(tag) && !e.querySelector(tag).disabled), {}, label, tag);
      await frame.evaluate((label, value, tag) => {
        const element = [...document.querySelectorAll('.el-dialog label')].find(e => e.textContent.trim().startsWith(label) && e.querySelector(tag)).querySelector(tag);
        element.value = value;
        element.dispatchEvent(new Event(tag === 'select' ? 'change' : 'input', { bubbles: true }));
      }, label, value, tag);
    }
    async function save(frame, route) {
      const response = page.waitForResponse(r => new URL(r.url()).pathname === route && r.request().method() === 'POST');
      await frame.click('button[form="' + (route.includes('/quality/') ? 'qc-config-form' : 'equipment-edit-form') + '"]');
      const saved = await response;
      assert(saved.ok(), `${route}: ${saved.status()} ${await saved.text()}`);
      const row = (await saved.json()).data;
      created.push({ route, row });
      const persisted = (await api(route + '/' + row.id)).data;
      assert.equal(persisted.name, row.name);
      return row;
    }
    let frame = await visit('/alerts');
    await frame.waitForSelector('.management-links');
    assert.deepEqual(await frame.$$eval('.management-links a', a => a.map(e => e.textContent)), ['测点管理', '专家经验', '预警策略']);
    await frame.click('.management-links a');
    await frame.waitForSelector('.alert-content');
    await page.waitForFunction(() => location.pathname === '/alerts/points');
    await button(frame, '新增测点');
    await field(frame, '所属设备', device.id, 'select');
    await field(frame, '测点号', prefix + '-point');
    await field(frame, '测点名称', prefix + '-point');
    await field(frame, '采集字段名', 'temperature');
    await save(frame, '/api/v1/alerts/points');
    checks.push('Main entry navigation and real point creation/persistence');
    frame = await visit('/alerts/advice');
    await button(frame, '新增经验');
    await field(frame, '经验名称', prefix + '-advice');
    await field(frame, '经验内容', '温度异常时检查冷却系统', 'textarea');
    const advice = await save(frame, '/api/v1/alerts/advice');
    checks.push('Advice direct route and real creation/persistence');
    frame = await visit('/alerts/rules');
    await button(frame, '新增策略');
    await field(frame, '策略名称', prefix + '-rule');
    await field(frame, '设备', device.id, 'select');
    await field(frame, '专家经验', advice.id, 'select');
    await field(frame, '规则项名称', '温度过高');
    await field(frame, '判断字段', 'temperature', 'select');
    await save(frame, '/api/v1/alerts/rules');
    checks.push('Rule route and visual condition creation/persistence');
    frame = await visit('/quality');
    await frame.waitForSelector('.management-links');
    await frame.click('.management-links a');
    await button(frame, '新增指标');
    await field(frame, '指标名称', prefix + '-metric');
    await field(frame, '标准上限', '10');
    const metric = await save(frame, '/api/v1/quality/metrics');
    checks.push('Quality main entry navigation and real metric creation/persistence');
    frame = await visit('/quality/templates');
    await button(frame, '新增模板');
    await field(frame, '模板名称', prefix + '-template');
    await field(frame, '添加检验指标', metric.id, 'select');
    await frame.waitForFunction(name => document.querySelector('.qc-selected')?.textContent.includes(name), {}, metric.name);
    const template = await save(frame, '/api/v1/quality/templates');
    assert.deepEqual(template.metricIds, [metric.id]);
    checks.push('Template route and real metric association/persistence');
    await page.screenshot({ path: path.join(OUTPUT, 'desktop-templates.png'), fullPage: true });
    await page.setViewport({ width: 390, height: 844 });
    for (const route of ['/alerts/points', '/alerts/advice', '/alerts/rules', '/quality/metrics', '/quality/templates']) {
      frame = await visit(route);
      await frame.waitForSelector(route.includes('/alerts/') ? '.alert-tabs' : '.qc-tabs');
      const geometry = await frame.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth }));
      assert(geometry.scroll <= geometry.width + 2, route + JSON.stringify(geometry));
      assert.equal(new URL(page.url()).pathname, route);
    }
    checks.push('All five management routes preserve URL and fit 390px');
    await page.screenshot({ path: path.join(OUTPUT, 'mobile-templates.png'), fullPage: true });
    assert.deepEqual(failures, []);
    status = 'passed';
  } catch (error) {
    failures.push(error.message);
    if (page) await page.screenshot({ path: path.join(OUTPUT, 'failure.png'), fullPage: true });
    throw error;
  } finally {
    await browser.close();
    for (const { route, row } of created.reverse()) {
      try { await api(route + '/' + row.id + '?version=' + row.version, 'DELETE'); }
      catch (error) { failures.push('Cleanup: ' + error.message); status = 'failed'; }
    }
    fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({ status, checks, failures, createdRecords: created.length, businessQueries: 0 }, null, 2));
    console.log(JSON.stringify({ status, checks, failures, output: OUTPUT }));
    if (status !== 'passed') process.exitCode = 1;
  }
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
