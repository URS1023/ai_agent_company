/*
 * 中文说明（工作流画布在 LAN 部署下的验收）：
 * 验证行为：在真实部署环境中打开工作流画布并操作节点/连线，校验画布与后端图数据一致。
 * 运行方式：需已有可访问的服务；通用交互工具来自 tests/ui/browser_only_helpers.js。
 * 边界：保存画布会写入工作流图（隔离环境执行）。
 */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const puppeteer = require("puppeteer-core");
const { login, fillElement, clickText, waitUiResponse } = require("./browser_only_helpers");
const BASE = process.env.TEST5_E2E_BASE_URL;
const OUTPUT = process.env.TEST5_E2E_OUTPUT;
const checks = [], errors = [];
let browser, page, saved;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const pass = text => { checks.push(text); console.log("PASS " + text); };
async function waitSaved() {
  await page.waitForFunction(() => /^自动保存 \d{2}:\d{2}:\d{2}/.test(document.querySelector('.wf-autosave-state')?.textContent || ''), { timeout: 30000 });
}
async function connect(source, target) {
  const from = await (await page.$(source)).boundingBox();
  const to = await (await page.$(target)).boundingBox();
  assert(from && to);
  await page.mouse.move(from.x + from.width/2, from.y + from.height/2);
  await page.mouse.down();
  await page.mouse.move(to.x + to.width/2, to.y + to.height/2, { steps: 20 });
  await page.mouse.up();
  await page.waitForFunction((a,b) => document.querySelector(a)?.classList.contains('connected') && document.querySelector(b)?.classList.contains('connected'), {}, source, target);
}
(async () => {
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, "isolated");
  assert.equal(new URL(BASE).hostname, "127.0.0.1");
  fs.mkdirSync(OUTPUT, { recursive: true });
  browser = await puppeteer.launch({ executablePath: process.env.TEST5_EDGE_PATH, headless: true, defaultViewport: { width: 1500, height: 960 } });
  page = await browser.newPage();
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', async response => {
    const route = new URL(response.url()).pathname;
    if (response.status() >= 500) errors.push(`${response.status()} ${route}`);
    if (/^\/api\/v1\/workflows(?:\/[^/]+)?$/.test(route) && ['POST','PUT'].includes(response.request().method()) && response.ok()) {
      const body = await response.json(); saved = body.data;
    }
  });
  await login(page, BASE, process.env.TEST5_E2E_PASSWORD);
  await page.goto(BASE + '/workflows/new', { waitUntil: 'networkidle0' });
  await page.waitForSelector('.wf-canvas');
  assert.equal(await page.$('#business-module-frame'), null);
  assert(await page.$('html.workflow-standalone #analytics-app'));
  await page.waitForFunction(() => document.querySelectorAll('.wf-node[data-node-id]').length === 2);
  await fillElement(page, await page.$('.wf-workbench-name'), '局域网画布验收-' + Date.now());
  await waitSaved();
  await page.waitForFunction(() => !location.pathname.endsWith('/new'), { timeout: 30000 });
  await page.waitForSelector('button[aria-label="添加节点"]', { visible: true, timeout: 30000 });
  await page.waitForSelector('.wf-designer-loading', { hidden: true, timeout: 30000 });
  await page.waitForNetworkIdle();
  pass('工作流独立页面和无限画布正常，初始草稿自动保存');

  const directEdge = await page.$('.vue-flow__edge-path');
  const edgePoint = await directEdge.evaluate(node => {
    const midpoint = node.getPointAtLength(node.getTotalLength() / 4);
    const point = new DOMPoint(midpoint.x, midpoint.y).matrixTransform(node.getScreenCTM());
    return { x: point.x, y: point.y };
  });
  await page.mouse.click(edgePoint.x, edgePoint.y);
  assert(await page.$eval('.wf-canvas-section', node => document.activeElement === node));
  await page.keyboard.press('Delete');
  await page.waitForFunction(() => document.querySelectorAll('.vue-flow__edge-path').length === 0);
  pass('鼠标选中连线后画布获得焦点，Delete 键删除连线');

  await page.click('button[aria-label="添加节点"]');
  await page.waitForSelector('.wf-palette-dialog');
  const titles = await page.$$eval('.wf-palette-group > button strong', nodes => nodes.map(node => node.textContent.trim()));
  assert(titles.length >= 30);
  await clickText(page, '.wf-palette-group > button', '模板渲染', false);
  await page.waitForFunction(() => document.querySelectorAll('.wf-node[data-node-id]').length === 3);
  await page.waitForSelector('.wf-properties', { visible: true });
  const nodes = await page.$$eval('.wf-node[data-node-id]', nodes => nodes.map(node => ({ id: node.dataset.nodeId, title: node.querySelector('.wf-node-heading span')?.textContent.trim() })));
  const start = nodes.find(node => /开始|用户输入/.test(node.title));
  const end = nodes.find(node => /结束|输出/.test(node.title));
  const template = nodes.find(node => /模板/.test(node.title));
  assert(start && end && template, JSON.stringify(nodes));
  const templateInput = await page.$('.wf-panel-content textarea');
  if (templateInput) await fillElement(page, templateInput, '局域网流程 {{ input.message }}');
  await page.click('.wf-properties-close');
  await page.click('button[aria-label="适应画布"]');
  await pause(350);
  await connect(`[data-node-id="${start.id}"] .wf-node-handle-source.wf-node-handle-top`, `[data-node-id="${template.id}"] .wf-node-handle-target.wf-node-handle-bottom`);
  await connect(`[data-node-id="${template.id}"] .wf-node-handle-source.wf-node-handle-left`, `[data-node-id="${end.id}"] .wf-node-handle-target.wf-node-handle-right`);
  await waitSaved();
  await pause(200);
  assert(saved.graph.edges.some(edge => edge.sourceSide === 'top' && edge.targetSide === 'bottom'));
  assert(saved.graph.edges.some(edge => edge.sourceSide === 'left' && edge.targetSide === 'right'));
  const paths = await page.$$eval('.vue-flow__edge-path', nodes => nodes.map(node => ({ d: node.getAttribute('d'), stroke: getComputedStyle(node).stroke })));
  assert(paths.length >= 2 && paths.every(item => item.d && item.stroke !== 'none'));
  pass(`节点库 ${titles.length} 个选项可见，上下/左右拖线可见且已保存`);
  await page.screenshot({ path: path.join(OUTPUT, 'canvas.png'), fullPage: true });

  await page.click('.wf-toolbar-run');
  await page.waitForSelector('.wf-debug-panel textarea', { visible: true });
  await fillElement(page, await page.$('.wf-debug-panel textarea'), '{invalid');
  assert(await page.$eval('.wf-debug-panel > footer button', button => button.disabled));
  await fillElement(page, await page.$('.wf-debug-panel textarea'), JSON.stringify({ message: '测试成功' }));
  const response = await waitUiResponse(page, (r,u) => /\/workflows\/[^/]+\/runs$/.test(u.pathname) && r.request().method() === 'POST', () => page.click('.wf-debug-panel > footer button'));
  assert([200,202].includes(response.status()), await response.text());
  await page.waitForFunction(() => document.querySelector('.wf-result h2')?.textContent.includes('执行完成'), { timeout: 30000 });
  pass('非法 JSON 阻止执行，合法输入完成真实工作流运行并显示结果');
  await page.click('[aria-label="关闭调试和预览"]');
  await page.click('.wf-toolbar-publish');
  const confirmation = await page.waitForSelector('.el-message-box', { visible: true, timeout: 1500 }).catch(() => null);
  if (confirmation) await clickText(page, '.el-message-box button', '确认');
  await page.waitForFunction(() => document.querySelector('.wf-autosave-state')?.textContent.includes('已发布'), { timeout: 30000 });
  const workflowPath = new URL(page.url()).pathname;
  for (const [label, suffix] of [['访问点','access-point'],['日志','logs'],['监测','overview']]) {
    await page.click(`.wf-app-rail [aria-label="${label}"]`);
    await page.waitForFunction(suffix => location.pathname.endsWith('/' + suffix), {}, suffix);
    await page.waitForNetworkIdle();
    assert((await page.$eval('#analytics-app', node => node.innerText)).length > 30);
  }
  await page.goto(BASE + workflowPath, { waitUntil: 'networkidle0' });
  await page.waitForSelector('.wf-node[data-node-id]');
  assert.equal(await page.$$eval('.wf-node[data-node-id]', nodes => nodes.length), 3);
  pass('发布、访问点、日志和监测页面可用，重新打开保留节点与连线');
  assert.deepEqual(errors, []);
})().catch(async error => {
  errors.push(error.message); console.error(error.stack); process.exitCode = 1;
  await page?.screenshot({ path: path.join(OUTPUT, 'failure.png'), fullPage: true }).catch(() => {});
}).finally(async () => {
  await browser?.close();
  fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({ checks, errors }, null, 2));
});
