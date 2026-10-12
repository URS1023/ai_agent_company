/*
 * 中文说明（质检缺陷定位的浏览器验收，固定连本机 18080 隔离服务）：
 * 验证行为：缺陷在页面上的标注/定位展示与其数据一致。
 * 运行方式：需 http://127.0.0.1:18080 的隔离服务与浏览器。
 * 边界：只读校验展示；不改动质检记录。
 */
"use strict";
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');
const BASE = 'http://127.0.0.1:18080';
const OUTPUT = path.resolve('.implementation/ui-results/quality-defect-position-' + Date.now());
const created = [], checks = [];
let token;
async function api(route, method = 'GET', body) {
  const response = await fetch(BASE + route, { method, redirect: 'error', signal: AbortSignal.timeout(10000),
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: 'Bearer ' + token } : {}) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  assert(response.ok, `${method} ${route}: ${response.status}`);
  return response.json();
}
async function seed(route, body) { const row = (await api(route, 'POST', body)).data; created.push({ route, row }); return row; }
async function main() {
  assert.deepEqual(await api('/api/health'), { ok: true, app: 'test' });
  token = (await api('/api/auth/login', 'POST', { username: 'workspace-admin', password: process.env.TEST5_E2E_PASSWORD })).token;
  fs.mkdirSync(OUTPUT, { recursive: true });
  const prefix = 'BOX-' + Date.now().toString(36);
  let browser, page, status = 'failed';
  try {
    const device = await seed('/api/v1/alerts/devices', { name: prefix, code: prefix, domains: ['quality'] });
    const metric = await seed('/api/v1/quality/metrics', { name: prefix, category: 'appearance' });
    const template = await seed('/api/v1/quality/templates', { name: prefix, metricIds: [metric.id] });
    browser = await puppeteer.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
    page = await browser.newPage();
    await page.setViewport({ width: 390, height: 844 });
    await page.setRequestInterception(true);
    page.on('request', r => { const u = new URL(r.url()); return u.origin === BASE || ['data:', 'blob:'].includes(u.protocol) ? r.continue() : r.abort(); });
    await page.goto(BASE, { waitUntil: 'networkidle0' });
    await page.type('#login-username', 'workspace-admin');
    await page.type('#login-password', process.env.TEST5_E2E_PASSWORD);
    await page.click('#login-btn');
    await page.waitForSelector('.enterprise-area-grid');
    for (const scenario of ['unapplied', 'applied', 'removed']) {
      await page.goto(BASE + '/quality/inspections', { waitUntil: 'networkidle0' });
      const frame = await (await page.waitForSelector('#business-module-frame')).contentFrame();
      const click = async text => {
        await frame.waitForFunction(text => [...document.querySelectorAll('button')].some(e => e.textContent.trim() === text && !e.disabled), {}, text);
        await frame.evaluate(text => [...document.querySelectorAll('button')].find(e => e.textContent.trim() === text && !e.disabled).click(), text);
      };
      const set = async (label, value, tag = 'input') => {
        await frame.waitForFunction((label, tag) => [...document.querySelectorAll('#qc-inspection-form label')].some(e => e.textContent.trim().startsWith(label) && e.querySelector(tag) && !e.querySelector(tag).disabled), {}, label, tag);
        await frame.evaluate((label, value, tag) => {
          const e = [...document.querySelectorAll('#qc-inspection-form label')].find(e => e.textContent.trim().startsWith(label) && e.querySelector(tag)).querySelector(tag);
          e.value = value; e.dispatchEvent(new Event(tag === 'select' ? 'change' : 'input', { bubbles: true }));
        }, label, value, tag);
      };
      await click('新增质检');
      await set('质检模板', template.id, 'select');
      await frame.waitForFunction(() => [...document.querySelectorAll('#qc-inspection-form select option')].some(e => e.textContent.startsWith('v1')));
      await set('固定模板修订', String(template.version), 'select');
      await set('受检设备', device.id, 'select');
      await set('样本 / 批次标识', prefix + '-' + scenario);
      await frame.click('#qc-inspection-form .qc-observation .qc-check input');
      await set('图像资料标识', '人工检查原图-隔离样例');
      await set('外观检查结果', 'defects', 'select');
      await set('缺陷类型', '裂纹');
      await click('标注缺陷位置');
      assert.deepEqual(await frame.$$eval('.qc-defect-position input', rows => rows.map(e => e.value)), ['', '', '', '']);
      assert(await frame.evaluate(() => [...document.querySelectorAll('button')].find(e => e.textContent.trim() === '应用位置').disabled));
      if (scenario !== 'unapplied') {
        for (const [label, value] of [['左侧 X', '0.9'], ['顶部 Y', '0.2'], ['框宽 W', '0.3'], ['框高 H', '0.4']]) await set(label, value);
        assert(await frame.evaluate(() => [...document.querySelectorAll('button')].find(e => e.textContent.trim() === '应用位置').disabled));
        await set('左侧 X', '0.1');
        await click('应用位置');
        if (scenario === 'removed') await click('移除位置标注');
      }
      await frame.$eval('.qc-defect-position', e => e.scrollIntoView({ block: 'center' }));
      await page.screenshot({ path: path.join(OUTPUT, scenario + '.png'), fullPage: true });
      const submitted = page.waitForResponse(r => new URL(r.url()).pathname === '/api/v1/quality/inspections' && r.request().method() === 'POST');
      await click('保存质检记录');
      const response = await submitted;
      assert(response.ok(), await response.text());
      const row = (await response.json()).data;
      created.push({ route: '/api/v1/quality/inspections', row });
      const saved = (await api('/api/v1/quality/inspections/' + row.id)).data;
      const expected = scenario === 'applied' ? [0.1, 0.2, 0.3, 0.4] : null;
      assert.deepEqual(saved.results[0].observation.defects[0].bbox ?? null, expected);
      checks.push(scenario + ': real persisted bbox=' + JSON.stringify(expected));
    }
    status = 'passed';
  } finally {
    if (browser) await browser.close();
    for (const { route, row } of created.reverse()) await api(route + '/' + row.id + '?version=' + row.version, 'DELETE');
    fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({ status, checks, createdRecords: created.length, businessQueries: 0 }, null, 2));
    console.log(JSON.stringify({ status, checks, output: OUTPUT }));
  }
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
