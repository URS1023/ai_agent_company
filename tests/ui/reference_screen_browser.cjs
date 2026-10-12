"use strict";
// 检查实际 Vue 构建中的 20 套参考画布与数据编辑；网络只允许本机内存夹具。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const puppeteer = require('puppeteer-core');
const { createFixture } = require('./reference_screen_fixture.cjs');
const copy = value => JSON.parse(JSON.stringify(value));
const output = path.resolve(process.argv[2] || '.implementation/hifi-reference-20261012/browser');

async function main() {
  fs.mkdirSync(output, { recursive: true });
  const root = path.resolve(__dirname, '../..');
  const frozenPaths = ['apps/web/assets/analytics/index.html', 'backend/app/modules/reports/resources/hifi_templates.json'];
  const hashes = () => Object.fromEntries(frozenPaths.map(file => [file, createHash('sha256').update(fs.readFileSync(path.join(root, file))).digest('hex')]));
  const baseline = hashes();
  const fixture = createFixture();
  await new Promise(resolve => fixture.server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${fixture.server.address().port}`;
  const browser = await puppeteer.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true });
  const errors = [], blocked = [], matrix = [], chartModules = new Set();
  const page = await browser.newPage();
  page.setDefaultTimeout(20000);
  page.on('pageerror', error => errors.push(error.message));
  await page.setRequestInterception(true);
  page.on('request', request => {
    if (/^https?:/.test(request.url()) && new URL(request.url()).origin !== origin) { blocked.push(request.url()); return request.abort(); }
    request.continue();
  });
  page.on('response', async response => {
    if (response.url().endsWith('.js') && (await response.text()).includes('_echarts_instance_')) chartModules.add(response.url());
  });
  async function show(id) {
    await page.goto(`${origin}/report/view/${id}`, { waitUntil: 'networkidle0' });
    await page.waitForSelector('.reference-skin-stage');
    // 截图只比较画布，排除查看器的返回、刷新等操作栏。
    await page.addStyleTag({ content: '.viewer-command { display: none !important; }' });
  }
  async function renderedOptions(ids) {
    return page.evaluate(async ({ ids, modules }) => {
      let init;
      for (const url of modules) {
        const exports = await import(url);
        init = Object.values(exports).find(value => typeof value === 'function' && /["']ec_["']/.test(String(value)) && /afterinit/.test(String(value)));
        if (init) break;
      }
      return ids.map(id => {
        const node = document.querySelector(`[data-widget-id="${id}"] .jimu-chart`);
        return { id, option: node && init ? init(node).getOption() : null };
      });
    }, { ids, modules: [...chartModules] });
  }
  try {
    for (const template of fixture.templates) {
      await page.setViewport({ width: template.width, height: template.height, deviceScaleFactor: 1 });
      await show(template.id);
      assert.equal(await page.$$eval('[data-reference-state="sample"]', nodes => nodes.length), template.componentCount - 1, template.id);
      assert.equal(await page.$$eval('[data-reference-state="decoration"]', nodes => nodes.length), 1);
      await (await page.$('.screen-stage')).screenshot({ path: path.join(output, `${template.id}-native.png`) });
      for (const width of [1440, 390]) {
        await page.setViewport({ width, height: 900, deviceScaleFactor: 1 });
        await page.waitForFunction(width => {
          const rect = document.querySelector('.screen-stage')?.getBoundingClientRect();
          return rect && rect.width > 0 && rect.right <= width + 1 && rect.bottom <= 901;
        }, {}, width);
        const geometry = await page.$eval('.screen-stage', node => {
          const rect = node.getBoundingClientRect();
          return { width: rect.width, height: rect.height, right: rect.right, bottom: rect.bottom };
        });
        assert(Math.abs(geometry.width / geometry.height - template.width / template.height) < .001, '必须等比缩放');
        assert(geometry.right <= width + 1 && geometry.bottom <= 901, '整屏必须位于视口内');
        if (width === 390) await (await page.$('.screen-stage')).screenshot({ path: path.join(output, `${template.id}-mobile.png`) });
      }
      const report = fixture.reports.get(template.id);
      for (const widget of report.content.widgets) {
        const kind = widget.referenceSkin.kind;
        if (kind === 'backdrop') continue;
        const data = kind === 'text' ? [{ value: kind + '-已修改' }]
          : kind === 'metric' ? [{ name: widget.componentName, value: '765.43', unit: '%' }]
          : kind === 'table' ? [{ 项目: '本轮修改项目', 金额: '765.43' }, { 项目: '本轮修改项目二', 金额: '123.45' }]
          : [{ name: '本轮类别A', value: 76 }, { name: '本轮类别B', value: 43 }];
        Object.assign(widget.config, { referenceSample: false, dataStatus: 'local', chartDataParsed: data, chartData: JSON.stringify(data) });
      }
      await page.setViewport({ width: template.width, height: template.height, deviceScaleFactor: 1 });
      await show(template.id);
      assert.equal(await page.$$eval('[data-reference-state="sample"]', nodes => nodes.length), 0);
      assert.equal(await page.$$eval('[data-reference-state="data"]', nodes => nodes.length), template.componentCount - 1);
      const metric = report.content.widgets.find(widget => widget.referenceSkin.kind === 'metric');
      if (metric) assert((await page.$eval(`[data-widget-id="${metric.id}"]`, node => node.innerText)).includes('765.43'));
      const charts = report.content.widgets.filter(widget => !['backdrop', 'metric', 'table', 'text'].includes(widget.referenceSkin.kind));
      const options = await renderedOptions(charts.map(widget => widget.id));
      for (const { id, option } of options) {
        assert(option && option.series.length, `实际图表必须存在 ${id}`);
        const kind = charts.find(widget => widget.id === id).referenceSkin.kind;
        const values = option.series[0].data.map(item => typeof item === 'object' ? item.value : item);
        assert.deepEqual(values, kind === 'gauge' ? [76] : kind === 'radar' ? [[76, 43]] : [76, 43], `实际系列必须使用本轮数据 ${id}`);
      }
      await (await page.$('.screen-stage')).screenshot({ path: path.join(output, `${template.id}-edited.png`) });
      fs.writeFileSync(path.join(output, `${template.id}-options.json`), JSON.stringify(options, null, 2));
      for (const widget of report.content.widgets.filter(widget => widget.referenceSkin.kind !== 'backdrop')) {
        Object.assign(widget.config, { chartDataParsed: [], chartData: '[]', dataStatus: 'unbound' });
      }
      await show(template.id);
      assert.equal(await page.$$eval('[data-reference-state="empty"]', nodes => nodes.length), template.componentCount - 1);
      assert.equal(await page.$$eval('[data-reference-state="sample"]', nodes => nodes.length), 0);
      matrix.push({ templateId: template.id, width: template.width, height: template.height, editableComponents: template.componentCount - 1,
        sample: true, edited: true, empty: true, responsiveWidths: [1440, 390], actualCharts: options.length });
    }
    // 通过用户实际操作验证编辑器应用数据、保存和重开，不用 API 修改替代 UI 验证。
    const template = fixture.templates[1], report = fixture.reports.get(template.id);
    report.content = copy(template.content);
    await page.setViewport({ width: 1440, height: 1000 });
    await page.goto(`${origin}/report/designer/${template.id}`, { waitUntil: 'networkidle0' });
    await page.waitForSelector('.layer-row');
    const index = await page.$$eval('.layer-row', nodes => nodes.findIndex(node => node.innerText.includes('本月计划完成率')));
    assert(index >= 0);
    await (await page.$$('.layer-row'))[index].click();
    await page.waitForSelector('.data-settings textarea');
    await page.$eval('.data-settings textarea', (node, value) => {
      node.value = value; node.dispatchEvent(new Event('input', { bubbles: true }));
    }, JSON.stringify([{ name: '本月计划完成率', value: 87.65, unit: '%' }]));
    await page.click('.data-settings button.primary');
    const metric = report.content.widgets.find(widget => widget.componentName === '本月计划完成率');
    await page.waitForFunction(id => document.querySelector(`[data-widget-id="${id}"]`)?.innerText.includes('87.65'), {}, metric.id);
    await page.click('[aria-label="保存报表"]');
    await page.waitForFunction(() => document.body.innerText.includes('报表配置已保存'));
    await page.reload({ waitUntil: 'networkidle0' });
    assert((await page.$eval(`[data-widget-id="${metric.id}"]`, node => node.innerText)).includes('87.65'));
    assert.equal(fixture.reports.get(template.id).content.widgets.find(widget => widget.id === metric.id).config.referenceSample, false);
    await page.setViewport({ width: template.width, height: template.height, deviceScaleFactor: 1 });
    await show(template.id);
    assert((await page.$eval(`[data-widget-id="${metric.id}"]`, node => node.innerText)).includes('87.65'));
    await (await page.$('.screen-stage')).screenshot({ path: path.join(output, 'metric-save-reopen.png') });
    assert.deepEqual(fixture.unexpected, []);
    assert.deepEqual(blocked, []);
    assert.deepEqual(errors, []);
    assert.deepEqual(hashes(), baseline, '验收期间构建入口和模板目录不得变化');
    const result = { passed: true, databaseConnections: 0, externalRequests: 0, matrix, editorSaveReload: true, errors,
      calls: fixture.calls, baseline, note: '原生像素截图排除查看器控制栏；动态数据的视觉差异另行评估，不按原图样例像素验收。' };
    fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify(result, null, 2));
    process.stdout.write(`PASS 20 套参考画布、${matrix.reduce((sum, row) => sum + row.editableComponents, 0)} 个数据区、实际系列和编辑保存重开\n`);
  } finally { await browser.close(); await new Promise(resolve => fixture.server.close(resolve)); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
