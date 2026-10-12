/*
 * 中文说明（工作流画布交互验收）：
 * 验证行为：画布上的拖拽、选中、缩放与右键等交互在真实浏览器中符合预期。
 * 运行方式：需隔离服务 + 本机 Edge；通用控件来自 tests/ui/browser_only_helpers.js。
 * 边界：交互会改动画布（保存后写库，仅隔离环境）。
 */
"use strict";
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');
const { login, fillElement, clickText, waitUiResponse } = require('./browser_only_helpers');
const BASE = process.env.TEST5_E2E_BASE_URL, OUTPUT = process.env.TEST5_E2E_OUTPUT;
const report = { checks: [], failures: [], errors: [] };
let browser, page;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function check(name, action) {
  try { await action(); report.checks.push(name); console.log('PASS ' + name); }
  catch (error) {
    report.failures.push({ name, error: error.message }); console.log('FAIL ' + name + ': ' + error.message);
    await page.screenshot({ path: path.join(OUTPUT, `failure-${report.failures.length}.png`) });
  }
}
async function saved() {
  await page.waitForFunction(() => /^自动保存 \d/.test(document.querySelector('.wf-autosave-state')?.textContent || ''), { timeout: 30000 });
  await page.waitForNetworkIdle();
}
const transform = () => page.$eval('.vue-flow__transformationpane', node => node.style.transform);
async function drag(from, dx, dy, button = 'left') {
  await page.mouse.move(from.x, from.y); await page.mouse.down({ button });
  await page.mouse.move(from.x + dx, from.y + dy, { steps: 16 }); await page.mouse.up({ button });
  await pause(250);
}
async function addTemplate() {
  await page.click('[aria-label="添加节点"]');
  await clickText(page, '.wf-palette-group > button', '模板渲染', false);
  await page.waitForSelector('.wf-properties-close');
  const id = await page.$eval('.wf-node.selected', node => node.dataset.nodeId);
  await page.click('.wf-properties-close'); await saved();
  await page.click('[aria-label="适应画布"]'); await pause(350);
  return id;
}
(async () => {
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, 'isolated');
  assert.equal(new URL(BASE).hostname, '127.0.0.1');
  fs.mkdirSync(OUTPUT, { recursive: true });
  browser = await puppeteer.launch({ executablePath: process.env.TEST5_EDGE_PATH, headless: true, defaultViewport: { width: 1500, height: 960 } });
  page = await browser.newPage(); page.on('pageerror', error => report.errors.push(error.message));
  await login(page, BASE, process.env.TEST5_E2E_PASSWORD);
  await page.goto(BASE + '/workflows/new', { waitUntil: 'networkidle0' });
  await page.waitForSelector('.wf-workbench-name');
  await fillElement(page, await page.$('.wf-workbench-name'), '画布操作验收-' + Date.now());
  await page.waitForFunction(() => !location.pathname.endsWith('/new'));
  await page.waitForSelector('.wf-designer-loading', { hidden: true }); await saved();
  await check('指针模式左键拖动空白处可左右上下平移', async () => {
    const before = await transform(); await drag({ x: 650, y: 700 }, 180, -100);
    assert.notEqual(await transform(), before);
    assert(await page.$('.vue-flow__background pattern circle'), 'Canvas dot pattern is absent');
    const beforeX = await page.$eval('.vue-flow__transformationpane', node => new DOMMatrix(getComputedStyle(node).transform).m41);
    for (let i = 0; i < 5; i++) await drag({ x: 650, y: 700 }, 250, 0);
    const afterX = await page.$eval('.vue-flow__transformationpane', node => new DOMMatrix(getComputedStyle(node).transform).m41);
    assert(afterX - beforeX > 1200, 'Canvas stopped panning at a fixed boundary');
    for (let i = 0; i < 5; i++) await drag({ x: 650, y: 700 }, -250, 0);
    const beforeWheel = await transform(); await page.mouse.wheel({ deltaX: 160, deltaY: 0 }); await pause(300);
    assert.notEqual(await transform(), beforeWheel, 'Horizontal wheel did not move canvas');
  });
  await page.click('[aria-label="适应画布"]'); await pause(350);
  const endId = await page.$$eval('.wf-node', nodes => nodes.find(node => node.querySelector('.wf-node-heading span')?.textContent === '结束').dataset.nodeId);
  await check('结束节点可配置输出变量并实际返回结果', async () => {
    await page.click(`[data-node-id="${endId}"] .wf-node-heading`);
    await page.waitForSelector('[aria-label="添加输出变量"]');
    await page.click('[aria-label="添加输出变量"]');
    await page.click('.wf-binding-options button'); await saved();
    await page.click('.wf-properties-close');
    await page.click('.wf-toolbar-run');
    await page.waitForSelector('.wf-debug-panel textarea');
    await fillElement(page, await page.$('.wf-debug-panel textarea'), JSON.stringify({ message: '结束输出验收' }));
    const response = await waitUiResponse(page, (r,u) => /\/workflows\/[^/]+\/runs$/.test(u.pathname) && r.request().method() === 'POST', () => page.click('.wf-debug-panel > footer button'));
    assert.equal(response.status(), 200, await response.text());
    const body = await response.json();
    assert.deepEqual(body.data.result.outputs[endId], { result: { message: '结束输出验收' } });
    await page.waitForFunction(() => document.querySelector('.wf-result h2')?.textContent.includes('执行完成'));
    await page.click('[aria-label="关闭调试和预览"]');
  });
  if (await page.$('[aria-label="关闭调试和预览"]')) await page.click('[aria-label="关闭调试和预览"]');
  await check('结束节点可以删除、保存并撤销恢复连线与输出配置', async () => {
    await page.click(`[data-node-id="${endId}"] .wf-node-heading`); await page.keyboard.press('Delete');
    await page.waitForSelector(`[data-node-id="${endId}"]`, { hidden: true, timeout: 4000 }); await saved();
    assert.equal(await page.$$eval('.vue-flow__edge-path', nodes => nodes.length), 0);
    await page.click('[aria-label="撤销"]');
    await page.waitForSelector(`[data-node-id="${endId}"]`, { visible: true }); await saved();
    await page.click(`[data-node-id="${endId}"] .wf-node-heading`);
    assert(await page.$('[aria-label="修改输出 result"]'));
    await page.click('.wf-properties-close');
  });
  let id = await addTemplate();
  await check('点击节点后 Delete 删除节点并保存', async () => {
    await page.click(`[data-node-id="${id}"] .wf-node-heading`); await page.waitForSelector('.wf-properties');
    await page.keyboard.press('Delete');
    await page.waitForSelector(`[data-node-id="${id}"]`, { hidden: true, timeout: 4000 }); await saved();
  });
  if (!await page.$(`[data-node-id="${id}"]`)) id = await addTemplate();
  if (await page.$('.wf-properties-close')) await page.click('.wf-properties-close');
  await check('节点更多菜单删除可用', async () => {
    const node = await page.$(`[data-node-id="${id}"]`); await node.hover();
    await (await node.$('.wf-node-tools summary')).click();
    await clickText(page, `[data-node-id="${id}"] .wf-node-menu button`, '删除');
    await page.waitForSelector(`[data-node-id="${id}"]`, { hidden: true, timeout: 4000 }); await saved();
  });
  id = await addTemplate();
  await check('右键节点显示操作菜单并可打开属性', async () => {
    await page.click(`[data-node-id="${id}"] .wf-node-heading`, { button: 'right' });
    await page.waitForSelector('.wf-node-context-menu', { visible: true, timeout: 4000 });
    await clickText(page, '.wf-node-context-menu button', '属性');
    await page.waitForSelector('.wf-properties', { visible: true, timeout: 4000 });
  });
  if (await page.$('.wf-properties-close')) await page.click('.wf-properties-close');
  await check('手型模式拖动节点区域只移动画布', async () => {
    await page.click('[aria-label="手型模式"]');
    const node = await page.$(`[data-node-id="${id}"]`);
    const beforeNode = await node.evaluate(n => n.closest('.vue-flow__node').style.transform);
    const beforeCanvas = await transform();
    const box = await (await node.$('.wf-node-heading')).boundingBox();
    await drag({ x: box.x + box.width/2, y: box.y + box.height/2 }, -150, 90);
    assert.notEqual(await transform(), beforeCanvas);
    assert.equal(await node.evaluate(n => n.closest('.vue-flow__node').style.transform), beforeNode);
  });
  await page.screenshot({ path: path.join(OUTPUT, 'canvas.png') });
  assert.deepEqual(report.failures, []); assert.deepEqual(report.errors, []);
})().catch(error => { report.errors.push(error.message); console.error(error.stack); process.exitCode = 1; })
.finally(async () => { await browser?.close(); fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify(report, null, 2)); });
