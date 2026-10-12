/*
 * 中文说明（工作流连线手势验收）：
 * 验证行为：从节点端口拖出连线的各类手势（吸附、取消、重连）行为正确。
 * 运行方式：需隔离服务 + 本机 Edge；不直接调用后端接口，全部通过界面操作。
 * 边界：会写入连线关系（保存后，仅隔离环境）。
 */
"use strict";
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');
const { login, fillElement, clickText, waitUiResponse } = require('./browser_only_helpers');
const BASE = process.env.TEST5_E2E_BASE_URL;
const OUTPUT = process.env.TEST5_E2E_OUTPUT;
const report = { checks: [], errors: [] };
let browser, page;
const pass = value => { report.checks.push(value); console.log('PASS ' + value); };
async function drag(fromSelector, toSelector) {
  const from = await (await page.$(fromSelector)).boundingBox();
  const to = await (await page.$(toSelector)).boundingBox();
  assert(from && to);
  const hit = await page.evaluate(({x,y}) => document.elementFromPoint(x,y)?.closest('.vue-flow__handle')?.getAttribute('data-handleid'), { x: from.x + from.width/2, y: from.y + from.height/2 });
  assert(hit, 'Drag origin is covered or outside the viewport: ' + fromSelector + ' ' + JSON.stringify(from));
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, { steps: 24 });
  await page.screenshot({ path: path.join(OUTPUT, 'dragging.png') });
  await page.mouse.up();
}
async function saved() {
  await page.waitForFunction(() => /^自动保存 \d/.test(document.querySelector('.wf-autosave-state')?.textContent || ''), { timeout: 30000 });
  await page.waitForNetworkIdle();
}
(async () => {
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, 'isolated');
  assert.equal(new URL(BASE).hostname, '127.0.0.1');
  fs.mkdirSync(OUTPUT, { recursive: true });
  browser = await puppeteer.launch({ executablePath: process.env.TEST5_EDGE_PATH, headless: true, defaultViewport: { width: 1500, height: 960 } });
  page = await browser.newPage();
  page.on('pageerror', error => report.errors.push(error.message));
  await login(page, BASE, process.env.TEST5_E2E_PASSWORD);
  await page.goto(BASE + '/workflows/new', { waitUntil: 'networkidle0' });
  await page.waitForSelector('.wf-workbench-name');
  await fillElement(page, await page.$('.wf-workbench-name'), '连线操作回归-' + Date.now());
  await page.waitForFunction(() => !location.pathname.endsWith('/new'));
  await page.waitForSelector('.wf-designer-loading', { hidden: true });
  await saved();
  await page.click('[aria-label="添加节点"]');
  await clickText(page, '.wf-palette-group > button', '模板渲染', false);
  await page.waitForSelector('.wf-properties-close');
  await page.click('.wf-properties-close');
  await page.click('[aria-label="适应画布"]');
  await saved();
  const nodes = await page.$$eval('.wf-node[data-node-id]', nodes => nodes.map(node => ({ id: node.dataset.nodeId, title: node.querySelector('.wf-node-heading span').textContent })));
  const start = nodes.find(n => n.title === '开始').id;
  const template = nodes.find(n => /模板/.test(n.title)).id;
  const end = nodes.find(n => n.title === '结束').id;
  const handle = (id, type, side) => `[data-node-id="${id}"] .wf-node-handle-${type}.wf-node-handle-${side}`;
  async function connectAndSave(from, to) {
    const response = await waitUiResponse(page, (r,u) => /\/api\/v1\/workflows\/[^/]+$/.test(u.pathname) && r.request().method() === 'PUT', () => drag(from, to), 30000);
    assert.equal(response.status(), 200, await response.text());
    await saved();
    return (await response.json()).data.graph;
  }
  // Both plus signs look like valid drop targets to the person dragging.
  await drag(handle(start, 'source', 'bottom'), handle(template, 'source', 'top'));
  await page.waitForFunction(() => document.querySelectorAll('.vue-flow__edge-path').length === 2, { timeout: 5000 });
  pass('从输出点拖到下一节点的加号，松开后连线保留');
  await saved();
  for (let i = 0; i < 4; i++) {
    await page.click('[aria-label="缩小画布"]');
    await new Promise(resolve => setTimeout(resolve, 180));
  }
  await page.waitForFunction(() => parseInt(document.querySelector('.wf-zoom-value').textContent) < 70);
  for (const sourceSide of ['top', 'right', 'bottom', 'left']) {
    for (const targetSide of ['top', 'right', 'bottom', 'left']) {
      const graph = await connectAndSave(handle(start, 'source', sourceSide), handle(template, 'source', targetSide));
      const edge = graph.edges.find(e => e.source === start && e.target === template);
      assert(edge && edge.sourceSide === sourceSide && edge.targetSide === targetSide && edge.port === 'out');
      assert.equal(graph.edges.length, 2, 'Reconnecting must move the existing edge, not add duplicates');
    }
  }
  pass('缩小画布后 16 种四向组合均可连接，重复拖线更新位置而不重复建线');
  const reverse = await connectAndSave(handle(template, 'target', 'top'), handle(start, 'source', 'bottom'));
  assert(reverse.edges.some(e => e.source === start && e.target === template && e.sourceSide === 'bottom' && e.targetSide === 'top'));
  pass('从下游输入点反向拖至上游输出，保存的执行方向正确');
  await drag(handle(template, 'source', 'right'), handle(start, 'source', 'left'));
  await page.waitForFunction(() => document.querySelector('.wf-connection-notice')?.textContent.includes('开始节点不能接入'));
  assert.equal(await page.$$eval('.vue-flow__edge-path', nodes => nodes.length), 2);
  await drag(handle(template, 'source', 'bottom'), handle(template, 'target', 'left'));
  await page.waitForFunction(() => document.querySelector('.wf-connection-notice')?.textContent.includes('自身'));
  pass('开始节点接入和节点自连接均被阻止，并显示具体提示');
  await page.click('[aria-label="添加节点"]');
  await clickText(page, '.wf-palette-group > button', '条件分支', false);
  await page.waitForSelector('.wf-properties-close');
  await page.click('.wf-properties-close');
  await page.click('[aria-label="适应画布"]');
  await saved();
  const condition = await page.$$eval('.wf-node[data-node-id]', nodes => nodes.find(n => n.querySelector('.wf-node-heading span').textContent === '条件分支').dataset.nodeId);
  await page.waitForSelector(`[data-node-id="${condition}"]`, { visible: true });
  pass('新增条件节点及连接点可见，不受悬浮工具栏隐藏样式影响');
  await page.click('[aria-label="适应画布"]');
  await new Promise(resolve => setTimeout(resolve, 350));
  const trueGraph = await connectAndSave(handle(condition, 'source', 'right') + '.wf-node-handle-true', handle(template, 'source', 'left'));
  assert(trueGraph.edges.some(e => e.source === condition && e.target === template && e.port === 'true'));
  const falseGraph = await connectAndSave(handle(condition, 'source', 'bottom') + '.wf-node-handle-false', handle(end, 'target', 'top'));
  assert(falseGraph.edges.some(e => e.source === condition && e.target === end && e.port === 'false'));
  pass('条件节点满足/不满足分支连接后保持各自的分支含义');
  await page.click('[aria-label="添加节点"]');
  const controls = await page.$$eval('.wf-palette-group', groups => groups.filter(group => group.querySelector('h3').textContent.trim() === '控制').flatMap(group => [...group.querySelectorAll('button strong')].map(node => node.textContent.trim())).filter(title => title !== '条件分支'));
  await page.click('[aria-label="关闭节点库"]');
  assert(controls.length > 0);
  for (const title of controls) {
    await page.click('[aria-label="添加节点"]');
    await clickText(page, '.wf-palette-group > button', title, false);
    await page.waitForSelector('.wf-properties-close');
    await page.click('.wf-properties-close');
    await saved();
    await page.waitForFunction(title => [...document.querySelectorAll('.wf-node')].some(node => node.querySelector('.wf-node-heading span')?.textContent === title && getComputedStyle(node).visibility === 'visible' && node.getBoundingClientRect().height > 40), {}, title);
  }
  pass(`控制分组的 ${controls.length} 个其他节点均可正常显示`);
  await page.reload({ waitUntil: 'networkidle0' });
  await page.waitForSelector('.wf-designer-loading', { hidden: true });
  await page.waitForFunction(() => document.querySelectorAll('.vue-flow__edge-path').length === 4);
  pass('刷新页面后连接仍然保留');
})().catch(async error => {
  report.errors.push(error.message); console.error(error.stack); process.exitCode = 1;
  await page?.screenshot({ path: path.join(OUTPUT, 'failure.png') }).catch(() => {});
}).finally(async () => {
  await browser?.close();
  fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify(report, null, 2));
});
