/* 真实 Vue、ElementPlus 与 ECharts 隔离集成；只使用本轮静态回环页面与内存样例。 */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');

async function verifyCharts() {
  const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
  const root = process.cwd(), dependencies = path.join(root, 'apps/analytics/node_modules');
  const ts = require(path.join(dependencies, 'typescript'));
  const compiler = require(path.join(dependencies, '@vue/compiler-sfc'));
  const modules = {}, styles = [], failures = [], checks = [], errors = [];
  const output = process.env.TEST5_CHART_OUTPUT;
  const baseline = process.env.TEST5_CHART_BROWSER_BASELINE;
  const compile = relative => {
    const filename = 'apps/analytics/src/' + relative;
    const content = baseline ? require('node:child_process').execFileSync('git', ['show', baseline + ':' + filename], { encoding: 'utf8', cwd: root })
      : fs.readFileSync(path.join(root, filename), 'utf8');
    let code = content;
    if (relative.endsWith('.vue')) {
      const id = 'chart-precision-' + path.basename(relative).replace(/\W/g, '');
      const descriptor = compiler.parse(content, { filename }).descriptor;
      code = compiler.compileScript(descriptor, { id, inlineTemplate: true }).content;
      for (const block of descriptor.styles) {
        const style = compiler.compileStyle({ id: 'data-v-' + id, source: block.content, scoped: false });
        assert.deepEqual(style.errors, []); styles.push(style.code);
      }
    }
    modules[relative] = ts.transpileModule(code.replaceAll('import.meta.env.BASE_URL', "'/'"), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }
    }).outputText;
  };
  for (const name of ['safeContent.ts', 'dashboardTheme.js', 'dashboardData.js', 'snapshotWidgetLayout.js', 'snapshotChartOption.js', 'aiChartOption.js']) compile('utils/' + name);
  if (!baseline) compile('utils/chartPrecision.js');
  compile('components/SmartChart.vue'); compile('components/jimu-screen/JimuChart.vue');
  const browser = await require('puppeteer-core').launch({ executablePath: process.env.TEST5_EDGE_PATH
    || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const page = await browser.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.setViewport({ width: 1350, height: 1000 });
    await page.goto(process.env.TEST5_E2E_BASE_URL);
    await page.setContent('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body><div id="smart"></div><div id="jimu"></div></body></html>');
    await page.evaluate(() => {
      const snapshot = Object.getOwnPropertyDescriptors(Object.prototype);
      const nested = Object.fromEntries(Object.entries(snapshot).filter(([, item]) => typeof item.value === 'function')
        .map(([key, item]) => [key, Object.getOwnPropertyDescriptors(item.value)]));
      window.chartPrototypeDifferences = () => {
        const differences = [];
        const compare = (expected, current, prefix) => {
          for (const key of new Set([...Reflect.ownKeys(expected), ...Reflect.ownKeys(current)])) {
            for (const field of ['value', 'get', 'set', 'writable', 'enumerable', 'configurable']) {
              if (!Object.is(expected[key]?.[field], current[key]?.[field])) differences.push(prefix + String(key) + '.' + field);
            }
          }
        };
        compare(snapshot, Object.getOwnPropertyDescriptors(Object.prototype), 'Object.prototype.');
        for (const [name, expected] of Object.entries(nested)) compare(expected, Object.getOwnPropertyDescriptors(Object.prototype[name]), name + '.');
        return differences;
      };
    });
    const injectionDiagnostic = async label => {
      const before = await page.evaluate(() => ({ objectPrototypeKeys: Object.getOwnPropertyNames(Object.prototype),
        descriptors: Object.fromEntries(Object.entries(Object.getOwnPropertyDescriptors(Object.prototype)).map(([key, value]) => [key,
          { getter: typeof value.get, setter: typeof value.set, value: typeof value.value }])),
        getterProbe: Object.defineProperty({}, 'probe', { get() { return 7; } }).probe,
        prototypeDifferences: chartPrototypeDifferences() }));
      let error = null;
      try { await page.waitForFunction(() => true, { timeout: 1500 }); }
      catch (failure) { error = String(failure.stack || failure) + '\n' + String(failure.cause?.stack || ''); }
      fs.writeFileSync(path.join(output, 'injection-diagnostic.json'), JSON.stringify({ label, baseline: baseline || null, before, error }, null, 2));
      assert.equal(error, null, error || '');
      assert.deepEqual(before.prototypeDifferences, []);
      console.log(JSON.stringify({ label, injection: 'passed', output }));
    };
    if (process.env.TEST5_CHART_INJECTION_DIAGNOSTIC === 'blank') { await injectionDiagnostic('空白静态页，无Vue或ECharts'); return; }
    for (const script of ['vue/dist/vue.global.prod.js', 'element-plus/dist/index.full.min.js', '@element-plus/icons-vue/dist/index.iife.min.js', 'echarts/dist/echarts.min.js', 'dompurify/dist/purify.min.js']) {
      await page.addScriptTag({ path: path.join(dependencies, script) });
    }
    await page.addStyleTag({ path: path.join(dependencies, 'element-plus/dist/index.css') });
    await page.addStyleTag({ content: styles.join('\n') + '\nbody{font-family:Arial;margin:16px}#smart{width:750px}#jimu{width:680px;height:400px;background:#081c31;margin-top:20px}' });
    await page.evaluate(compiled => {
      const cache = {};
      const load = (name, from = '') => {
        if (name === 'vue') return Vue;
        if (name === 'echarts') return echarts;
        if (name === 'element-plus') return ElementPlus;
        if (name === '@element-plus/icons-vue') return ElementPlusIconsVue;
        if (name === 'dompurify') return { default: DOMPurify };
        let key = name.startsWith('@/') ? name.slice(2) : name;
        if (key.startsWith('./')) key = from.slice(0, from.lastIndexOf('/') + 1) + key.slice(2);
        if (!compiled[key]) key += compiled[key + '.js'] ? '.js' : '.ts';
        if (cache[key]) return cache[key];
        if (!compiled[key]) throw Error('未登记组件依赖：' + name);
        const exports = {}; cache[key] = exports;
        new Function('require', 'exports', compiled[key])(dependency => load(dependency, key), exports);
        return exports;
      };
      window.chartFixture = Vue.reactive({ smartRows: [{ category: '初始', amount: '1' }],
        rows: [{ name: '初始', value: '1' }], widget: { component: 'JBar', w: 680, h: 400, config: {} } });
      const smart = load('components/SmartChart.vue').default, jimu = load('components/jimu-screen/JimuChart.vue').default;
      window.chartLegendEvents = [];
      window.chartLegendHits = [];
      Vue.createApp({ render: () => Vue.h(smart, { data: chartFixture.smartRows,
        columns: [{ columnName: 'category', comment: '类别<img src=x>' }, { columnName: 'amount', comment: '金额<script>bad()</script>' }] }) }).use(ElementPlus).mount('#smart');
      Vue.createApp({ render: () => Vue.h(jimu, { widget: chartFixture.widget, data: chartFixture.rows,
        onDataClick: detail => { window.chartClicked = detail; }, onLegendSelect: detail => {
          window.chartLegendSelected = detail;
          window.chartLegendEvents.push(JSON.parse(JSON.stringify(detail)));
        } }) }).mount('#jimu');
      window.chartLayoutHistory = [];
      const recordLayout = () => {
        const insight = document.querySelector('#smart .chart-insight');
        const layout = { smartHeight: document.querySelector('#smart').getBoundingClientRect().height,
          jimuTop: document.querySelector('#jimu').getBoundingClientRect().top,
          loading: Boolean(document.querySelector('#smart .chart-loading')),
          insightVisible: Boolean(insight?.getBoundingClientRect().height) };
        if (JSON.stringify(layout) !== JSON.stringify(window.chartLayoutHistory.at(-1))) window.chartLayoutHistory.push(layout);
      };
      new MutationObserver(recordLayout).observe(document.querySelector('#smart'), { childList: true, subtree: true, attributes: true });
      recordLayout();
      // 只读取真实图例布局和命中；选择行为始终由浏览器鼠标点击触发。
      window.chartLegendProbe = (name, fixedPoint) => {
        const element = document.querySelector('#jimu .jimu-chart'), instance = echarts.getInstanceByDom(element);
        const view = instance.getViewOfComponentModel(instance.getModel().getComponent('legend'));
        const groups = view.getContentGroup().children();
        const label = group => group.children().find(child => child.type === 'text');
        const businessName = group => String(label(group)?.style.text || '').replaceAll('\u2060', '');
        const hitName = target => {
          for (let node = target; node; node = node.parent || node.__hostTarget) {
            if (groups.includes(node)) return businessName(node);
          }
          return null;
        };
        const group = groups.find(group => businessName(group) === name);
        if (!group) throw Error('未找到真实图例文本：' + name);
        const item = label(group), box = item.getBoundingRect().clone();
        const transform = item.getComputedTransform(); if (transform) box.applyTransform(transform);
        const host = element.getBoundingClientRect();
        const point = fixedPoint || { x: host.left + box.x + box.width / 2, y: host.top + box.y + box.height / 2 };
        const hover = instance.getZr().handler.findHover(point.x - host.left, point.y - host.top);
        if (!window.chartLegendHitObserver) {
          window.chartLegendHitObserver = true;
          instance.getZr().on('click', event => {
            // 每次点击后 ECharts 可能重建图例组，因此按当时父组读取实际命中标签。
            let group = event.target;
            while (group && group.__legendDataIndex === undefined) group = group.parent || group.__hostTarget;
            window.chartLegendHits.push({ name: group ? businessName(group) : null,
              x: event.offsetX, y: event.offsetY, trusted: event.event?.isTrusted === true });
          });
        }
        return { name, ...point, host: { left: host.left, top: host.top, width: host.width, height: host.height },
          smartHeight: document.querySelector('#smart').getBoundingClientRect().height,
          loading: Boolean(document.querySelector('#smart .chart-loading')),
          hitLegend: hitName(hover.target), domHit: element.contains(document.elementFromPoint(point.x, point.y)) };
      };
      window.chartRead = async (host, index = 0) => {
        await Vue.nextTick(); await new Promise(resolve => setTimeout(resolve, 80));
        const element = document.querySelector(host === 'smart' ? '#smart .chart-container' : '#jimu .jimu-chart');
        const instance = echarts.getInstanceByDom(element);
        if (!instance) throw Error('真实 ECharts 实例未就绪');
        instance.setOption({ animation: false });
        instance.dispatchAction({ type: 'showTip', seriesIndex: 0, dataIndex: index });
        await new Promise(resolve => setTimeout(resolve, 100));
        const option = instance.getOption();
        const text = instance.getZr().storage.getDisplayList().map(item => item.style?.text || '').filter(Boolean).join('\n');
        return { series: option.series.map(series => ({ type: series.type, data: series.data, exactValues: series.exactValues })),
          tooltipText: text, tooltipMode: option.tooltip[0]?.renderMode,
          markupNodes: document.querySelectorAll('#smart img, #smart script, #jimu img, #jimu script').length,
          renderError: document.querySelector('#jimu .jimu-chart-error')?.textContent || '',
          prototypeDifferences: chartPrototypeDifferences() };
      };
    }, modules);
    if (process.env.TEST5_CHART_INJECTION_DIAGNOSTIC === 'special') {
      await page.evaluate(async () => {
        chartFixture.smartRows = ['__proto__', 'constructor', 'toString', '__defineGetter__', '正常中文', ''].map((category, index) => ({ category, amount: String(index + 1) }));
        await Vue.nextTick(); await new Promise(resolve => setTimeout(resolve, 350));
      });
      await injectionDiagnostic('真实组件特殊类别渲染后'); return;
    }
    const check = async (name, action) => {
      if (failures.length || process.env.TEST5_CHART_CASE_FILTER && !new RegExp(process.env.TEST5_CHART_CASE_FILTER).test(name)) return;
      try { await action(); checks.push({ name, ok: true }); }
      catch (error) { failures.push({ name, message: error.message }); checks.push({ name, ok: false }); }
      const prototypeDifferences = await page.evaluate(() => chartPrototypeDifferences());
      if (prototypeDifferences.length) failures.push({ name, prototypeDifferences });
      fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({ checks, failures, errors }, null, 2));
      console.log(JSON.stringify(checks.at(-1)));
    };
    const values = item => item && typeof item === 'object' ? item.value : item;
    const setJimu = async (component, rows, extra = {}) => {
      await page.evaluate(({ component, rows, extra }) => { chartFixture.rows = rows; chartFixture.widget = { component, w: 680, h: 400, config: {}, ...extra }; }, { component, rows, extra });
      return page.evaluate(() => chartRead('jimu'));
    };
    const legendTrace = [];
    const saveLegendTrace = async () => {
      const actual = await page.evaluate(() => ({ events: chartLegendEvents, hits: chartLegendHits, layout: chartLayoutHistory,
        prototypeDifferences: chartPrototypeDifferences() }));
      fs.writeFileSync(path.join(output, 'legend-clicks.json'), JSON.stringify({ steps: legendTrace, ...actual }, null, 2));
    };
    const locateLegend = async name => {
      // SmartChart 的加载态结束后才挂载洞察区；连续两帧位置相等，再取本次点击的新坐标。
      const handle = await page.waitForFunction(name => {
        const smart = document.querySelector('#smart .chart-container');
        if (document.querySelector('#smart .chart-loading') || !smart?.getBoundingClientRect().height) return false;
        const point = chartLegendProbe(name), previous = window.chartLastLegendGeometry;
        window.chartLastLegendGeometry = point;
        return point.domHit && point.hitLegend === name && previous?.name === name
          && ['x', 'y', 'smartHeight'].every(key => point[key] === previous[key]) && point;
      }, { polling: 'raf', timeout: 1500 }, name);
      try { return await handle.jsonValue(); } finally { await handle.dispose(); }
    };
    const clickLegend = async (name, selected) => {
      const point = await locateLegend(name);
      const before = await page.evaluate(() => ({ events: chartLegendEvents.length, hits: chartLegendHits.length }));
      const step = { name, selected, point, before }; legendTrace.push(step);
      try {
        await page.mouse.click(point.x, point.y);
        await page.waitForFunction(({ name, selected, count }) => chartLegendEvents.length > count
          && window.chartLegendSelected?.name === name && window.chartLegendSelected.selected[name] === selected,
        { timeout: 1500 }, { name, selected, count: before.events });
        step.event = await page.evaluate(() => chartLegendSelected);
        step.hit = await page.evaluate(() => chartLegendHits.at(-1));
        assert.equal(step.hit?.name, name); assert.equal(step.hit?.trusted, true);
        assert.equal(await page.evaluate(() => chartLegendHits.length), before.hits + 1);
        return step;
      } catch (error) { step.error = error.message; throw error; }
      finally { await saveLegendTrace(); }
    };
    await check('图例保留字真实点击可独立取消选择', async () => {
      const rows = ['__proto__', 'constructor', 'toString', '__defineGetter__', '正常中文'].map(name => ({ name, value: '5' }));
      await setJimu('JPie', rows);
      if (process.env.TEST5_CHART_LEGEND_LAYOUT_CONTROL === '1') {
        // 测试台故障注入：控制现有洞察区占位，确定性复现初始加载结束时的同幅下移。
        await locateLegend('__proto__');
        const hidden = await page.addStyleTag({ content: '#smart .chart-insight{display:none!important}' });
        const disabled = await clickLegend('__proto__', false);
        await page.screenshot({ path: path.join(output, 'legend-control-before-shift.png'), fullPage: true });
        await hidden.evaluate(element => element.remove());
        const relocated = await locateLegend('__proto__');
        const stale = await page.evaluate(point => chartLegendProbe('__proto__', point), disabled.point);
        const control = { type: '测试台洞察占位故障注入', oldPoint: disabled.point, relocated, stale,
          shift: relocated.y - disabled.point.y }; legendTrace.push(control);
        assert(control.shift > 0); assert.equal(control.shift, relocated.smartHeight - disabled.point.smartHeight);
        assert.notEqual(stale.hitLegend, '__proto__');
        const count = await page.evaluate(() => chartLegendEvents.length);
        await page.mouse.click(disabled.point.x, disabled.point.y);
        try {
          await assert.rejects(page.waitForFunction(() => window.chartLegendSelected?.selected.__proto__ === true,
            { timeout: 1500 }), error => {
            control.oldPointFailure = { name: error.name, message: error.message };
            return error.name === 'TimeoutError';
          });
          assert.equal(await page.evaluate(() => chartLegendEvents.length), count);
          assert.equal(await page.evaluate(() => chartLegendSelected.selected.__proto__), false);
          await page.screenshot({ path: path.join(output, 'legend-control-stale-click.png'), fullPage: true });
          await clickLegend('__proto__', true);
          await page.screenshot({ path: path.join(output, 'legend-control-relocated-click.png'), fullPage: true });
        } finally { await saveLegendTrace(); }
      }
      for (const { name } of rows) {
        const { event } = await clickLegend(name, false);
        assert.equal(event.name, name); assert.equal(event.selected[name], false);
        for (const other of rows.filter(row => row.name !== name)) assert.notEqual(event.selected[other.name], false);
        await clickLegend(name, true);
        assert.equal(await page.evaluate(name => chartLegendSelected.selected[name], name), true, name);
      }
      assert.deepEqual(await page.evaluate(() => JSON.parse(JSON.stringify(chartFixture.rows))), rows);
      await page.screenshot({ path: path.join(output, 'legend-all-restored.png'), fullPage: true });
    });
    for (const [name, extra] of [
      ['保存快照', { config: { snapshotBinding: { labelField: 'name', valueField: 'value' }, option: { tooltip: { formatter: '{c}' } } } }],
      ['普通大屏', {}], ['AI图表', { visualMode: 'ai', config: { option: { xAxis: { type: 'category' }, yAxis: { type: 'value' }, series: [{ type: 'bar' }] } } }]
    ]) {
      await check(name + '真实tooltip保留大整数和安全文本', async () => {
        const actual = await setJimu('JBar', [{ name: '<img src=x>', value: '9007199254740993' }, { name: '尾零', value: '123.4500' }], extra);
        assert.equal(actual.renderError, ''); assert.equal(actual.tooltipMode, 'richText');
        assert(actual.tooltipText.includes('9007199254740993'), JSON.stringify(actual));
        assert.equal(actual.markupNodes, 0);
        const tail = await page.evaluate(() => chartRead('jimu', 1));
        assert(tail.tooltipText.includes('123.4500'), JSON.stringify(tail));
      });
    }
    for (const visualMode of [undefined, 'ai']) {
      await check((visualMode || '普通') + '仪表保持未知，雷达保持原文', async () => {
        const gauge = await setJimu('JGauge', [{ name: '未知', value: null }], { visualMode });
        assert.equal(gauge.series[0].data[0].value, null); assert.equal(gauge.renderError, '');
        const radar = await setJimu('JRadar', [{ name: '甲', value: '9007199254740993' }, { name: '乙', value: null }, { name: '丙', value: '1.2300e-20' }], { visualMode });
        assert.deepEqual(radar.series[0].data[0].exactValue, ['9007199254740993', null, '1.2300e-20']);
        assert.equal(radar.series[0].data[0].value[1], null); assert.equal(radar.renderError, '');
        assert(radar.tooltipText.includes('9007199254740993'), JSON.stringify(radar));
      });
    }
    const smartRows = [{ category: '__proto__', amount: '9007199254740993' }, { category: '__proto__', amount: '-9007199254740992' },
      { category: 'constructor', amount: '0.10' }, { category: 'constructor', amount: '0.20' }, { category: 'toString', amount: '0' },
      { category: '', amount: '2' }, { category: '<img src=x onerror=bad()>', amount: '3' }, { category: '未知', amount: null }];
    smartRows.push({ category: '__defineGetter__', amount: '4' }, { category: '正常中文', amount: '5' });
    for (const [label, type] of [['柱状图', 'bar'], ['饼图', 'pie']]) {
      await check('SmartChart真实' + label + '精确聚合和特殊类别', async () => {
        await page.evaluate(async rows => {
          chartFixture.smartRows = rows; await Vue.nextTick(); await new Promise(resolve => setTimeout(resolve, 350));
        }, smartRows);
        const controls = await page.$$('#smart .el-radio-button');
        for (const control of controls) if ((await control.evaluate(node => node.textContent)).includes(label)) await control.click();
        const actual = await page.evaluate(() => chartRead('smart'));
        assert.equal(actual.series[0].type, type);
        assert.deepEqual(actual.series[0].data.map(values), [1, 0.3, 0, 2, 3, null, 4, 5]);
        assert.deepEqual(actual.series[0].data.map(item => item.exactValue), ['1', '0.30', '0', '2', '3', null, '4', '5']);
        assert.equal(actual.markupNodes, 0); assert(actual.tooltipText.includes('__proto__'), JSON.stringify(actual));
        assert.deepEqual(actual.prototypeDifferences, []);
        await page.waitForFunction(() => chartPrototypeDifferences().length === 0, { timeout: 1500 });
      });
    }
    await check('特殊类别点击保留原name和原始数据', async () => {
      const rows = ['__proto__', 'constructor', 'toString', '__defineGetter__', '正常中文'].map(name => ({ name, value: '5.00' }));
      await setJimu('JBar', rows);
      const point = await page.evaluate(() => {
        const element = document.querySelector('#jimu .jimu-chart'), box = element.getBoundingClientRect();
        const [x, y] = echarts.getInstanceByDom(element).convertToPixel({ seriesIndex: 0 }, [0, 2.5]);
        return { x: box.left + x, y: box.top + y };
      });
      await page.mouse.click(point.x, point.y);
      const clicked = await page.evaluate(() => JSON.parse(JSON.stringify({ clicked: chartClicked, rows: chartFixture.rows })));
      assert.equal(clicked.clicked.name, '__proto__'); assert.deepEqual(clicked.clicked.data, rows[0]);
      assert.deepEqual(clicked.rows, rows);
    });
    for (const component of ['JNegativeBar', 'JStackBar', 'JPercentBar', 'JMixLineBar']) {
      await check(component + '真实图形保持输入数值及系列', async () => {
        const actual = await setJimu(component, [{ name: '甲', value: '5' }, { name: '乙', value: '7' }, { name: '丙', value: '-3' }]);
        assert.equal(actual.series.length, 1); assert.deepEqual(actual.series[0].data.map(values), [5, 7, -3]); assert.equal(actual.renderError, '');
      });
    }
    await page.screenshot({ path: path.join(output, 'chart-precision-components.png'), fullPage: true });
    fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({ checks, failures, errors,
      boundary: '真实Vue/ElementPlus/ECharts与隔离Edge；所有图表数据为内存样例；无模型、业务API或数据库请求' }, null, 2));
    assert.deepEqual(errors, []); assert.deepEqual(failures, [], JSON.stringify(failures));
    console.log(JSON.stringify({ ok: true, cases: checks.length, output }));
  } finally { await browser.close(); }
}

if (require.main === module) {
  const root = path.resolve(__dirname, '../..');
  const python = process.env.TEST5_E2E_PYTHON || path.join(root, '.venv_py312/Scripts/python.exe');
  assert(fs.existsSync(python), '必须使用现有本地隔离Python，不安装依赖');
  const output = path.join(root, '.implementation/ui-results/chart-precision-' + Date.now().toString(36));
  fs.mkdirSync(output, { recursive: true });
  const fixture = String.raw`
import json, os, subprocess, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0, str(Path('tests/dsh').resolve()))
from business_browser_acceptance import WindowsProcessJob, stop_test_process, check_network_evidence
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers(); self.wfile.write(b'<!doctype html><title>chart fixture</title>')
    def log_message(self, *_): pass
host = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=host.serve_forever, daemon=True).start()
folder = Path(os.environ['TEST5_CHART_OUTPUT']); report, permit = folder/'network.json', folder/'ready'
env = {**os.environ, 'NODE_OPTIONS': '', 'TEST5_E2E_ALLOW_WRITE': 'isolated',
       'TEST5_E2E_BASE_URL': 'http://127.0.0.1:%d' % host.server_port,
       'TEST5_E2E_NETWORK_REPORT': str(report), 'TEST5_E2E_PROCESS_GATE': str(permit)}
job, child = WindowsProcessJob(), None
try:
    child = subprocess.Popen(['node', '--import', Path('tests/dsh/local_network_only.mjs').resolve().as_uri(),
        '--require', str(Path('tests/ui/isolated_browser_guard.cjs').resolve()), '-e', env.pop('TEST5_CHART_SOURCE')],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', creationflags=0x08000000)
    job.assign(child); permit.write_text('ready')
    output, _ = child.communicate(timeout=90)
    (folder/'browser.log').write_text(output, encoding='utf-8'); print(output)
    assert child.returncode == 0, output
    check_network_evidence(report)
finally:
    (folder/'cleanup.json').write_text(json.dumps(stop_test_process(child, job)), encoding='utf-8')
    host.shutdown(); host.server_close()
`;
  const result = spawnSync(python, ['-c', fixture], { cwd: root, encoding: 'utf8', timeout: 100000, windowsHide: true,
    env: { ...process.env, PYTHONIOENCODING: 'utf-8', TEST5_CHART_OUTPUT: output,
      TEST5_CHART_SOURCE: `(${verifyCharts.toString()})().catch(error=>{console.error(error);process.exitCode=1;});` } });
  process.stdout.write(result.stdout || ''); process.stderr.write(result.stderr || '');
  console.log('RESULT_DIR ' + output);
  if (result.error) console.error(result.error);
  process.exitCode = result.status === 0 ? 0 : 1;
}
