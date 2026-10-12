/*
 * 中文说明（设备工作流分类卡片的离线渲染用例）：
 * 验证行为：直接渲染真实的 Vue 卡片组件，验证设备分类（巡检/保养/维修等）展示契约。
 * 运行方式：module.exports 导出，通过 createRequire 从 apps/analytics/package.json 解析 Vue 依赖，
 *   需要先在 apps/analytics 安装依赖；不需要后端与数据库。
 * 边界：只测组件渲染，不涉及路由与后端数据。
 */
"use strict";
// Render the real Vue card offline, with no backend or database access.
module.exports = async function verifyDeviceWorkflowCategories() {
  const assert = require('node:assert/strict');
  const fs = require('node:fs');
  const path = require('node:path');
  const root = path.resolve(__dirname, '../..');
  const req = require('node:module').createRequire(path.join(root, 'apps/analytics/package.json'));
  const { parse, compileScript } = req('@vue/compiler-sfc');
  const { transformSync } = req('esbuild');
  function compile(file, name) {
    const source = fs.readFileSync(path.join(root, 'apps/analytics/src/components/business/', file), 'utf8');
    let code = compileScript(parse(source).descriptor, { id: name, inlineTemplate: true }).content;
    code = code.replace(/import\s*\{([^}]+)\}\s*from\s*['"]vue['"];?/g, (_, names) =>
      'const {' + names.replace(/\bas\b/g, ':') + '} = Vue;');
    code = code.replace(/^import[^\n]*$/gm, '').replace('export default', 'window.' + name + ' =');
    return transformSync(code, { loader: 'ts', format: 'iife' }).code;
  }
  const code = compile('BusinessDeviceCard.vue', 'BusinessDeviceCard') + compile('BusinessDeviceSelector.vue', 'DeviceSelector');
  const browser = await require('puppeteer-core').launch({
    executablePath: process.env.TEST5_EDGE_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox', '--disable-gpu'],
  });
  try {
    const page = await browser.newPage();
    await page.setRequestInterception(true);
    page.on('request', request => request.abort());
    await page.setContent('<div id="app"></div>');
    await page.addScriptTag({ path: req.resolve('vue/dist/vue.global.js') });
    await page.addScriptTag({ content: `
      const equipmentStatus = {}; const can = () => true;
      const CatalogPhoto = { template: '<span />' };
      const CatalogCard = { template: '<article><slot/><slot name="actions"/></article>' };
      ${code}
      window.opened = [];
      window.rows = Vue.reactive(Array.from({length: 6}, (_, i) => ({id: 'd'+i, name: '设备'+i, workflowEntries: {
        inspection: {id: 'inspect'+i, publishedRevision: 1}, maintenance: {id: 'maintain'+i}, repair: {id: 'repair'+i}
      }})));
      Vue.createApp({render: () => Vue.h(DeviceSelector, {domain: 'alert', rows, keyword: '', loading: false, busy: false, canOpen: true,
        canEdit: false, canCreate: false, canRemove: false, error: '', onOpen: (device, id) => opened.push(id)})}).mount('#app');
    ` });
    assert.equal(await page.$$eval('article', nodes => nodes.length), 18);
    assert.ok(await page.$$eval('article', nodes => nodes.every(n => n.querySelectorAll('.catalog-workflow').length === 1)));
    await page.$$eval('.workflow-category-filter button', nodes => nodes[2].click());
    assert.equal(await page.$$eval('article', nodes => nodes.length), 6);
    await page.$$eval('.catalog-workflow', nodes => nodes.forEach(n => n.click()));
    assert.deepEqual(await page.evaluate(() => opened), Array.from({length: 6}, (_, i) => 'maintain'+i));
    await page.$$eval('.workflow-category-filter button', nodes => nodes[3].click());
    assert.ok(await page.$$eval('.catalog-workflow', nodes => nodes.every(n => n.textContent.includes('维修'))));
    await page.$$eval('.workflow-category-filter button', nodes => nodes[0].click());
    await page.evaluate(async () => { rows.splice(0, rows.length, {id: 'legacy', name: '新设备'}); await Vue.nextTick(); });
    assert.equal(await page.$$eval('.catalog-workflow', nodes => nodes.length), 1);
    console.log('PASS 18 independent cards, one workflow per card, category filters, exact workflow IDs, legacy fallback (offline)');
  } finally { await browser.close(); }
};
