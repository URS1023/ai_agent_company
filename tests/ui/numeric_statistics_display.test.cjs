/* 编译真实 Vue 统计组件并在隔离 Edge 中核验文本，不连接业务库或模型。 */
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { test } = require('node:test');

async function verifyComponents() {
  const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
  const puppeteer = require('puppeteer-core');
  const root = process.cwd(), dependencyRoot = path.join(root, 'apps/analytics/node_modules');
  const compiler = require(path.join(dependencyRoot, '@vue/compiler-sfc'));
  const ts = require(path.join(dependencyRoot, 'typescript'));
  compiler.registerTS(() => ts);
  const style = [], modules = {};
  const compile = relative => {
    const filename = path.join(root, 'apps/analytics/src', relative);
    if (relative.endsWith('.ts')) {
      modules[relative] = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
        compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
      }).outputText;
      return;
    }
    const id = 'numeric-' + path.basename(filename).replace(/\W/g, '').toLowerCase();
    const { descriptor } = compiler.parse(fs.readFileSync(filename, 'utf8'), { filename });
    const script = compiler.compileScript(descriptor, { id, inlineTemplate: true,
      fs: { fileExists: name => fs.existsSync(name) && fs.statSync(name).isFile(), readFile: name => fs.readFileSync(name, 'utf8') } });
    modules[relative] = ts.transpileModule(script.content, {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
    }).outputText + `\nexports.default.__scopeId=${JSON.stringify('data-v-' + id)};`;
    for (const block of descriptor.styles) {
      const result = compiler.compileStyle({ source: block.content, id: 'data-v-' + id, scoped: block.scoped });
      assert.equal(result.errors.length, 0, String(result.errors)); style.push(result.code);
    }
  };
  compile('components/business/QueryResultPanel.vue');
  compile('components/DataAnalysisDashboard.vue');
  if (fs.existsSync(path.join(root, 'apps/analytics/src/utils/decimalDisplay.ts'))) compile('utils/decimalDisplay.ts');
  const browser = await puppeteer.launch({ executablePath: process.env.TEST5_EDGE_PATH
    || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  const output = process.env.TEST5_NUMERIC_OUTPUT;
  const failures = [], checks = [], consoleErrors = [];
  try {
    const page = await browser.newPage();
    page.on('pageerror', error => consoleErrors.push(error.message));
    await page.setViewport({ width: 1200, height: 950 });
    await page.goto(process.env.TEST5_E2E_BASE_URL);
    await page.setContent('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body><div id="query"></div><div id="dashboard"></div></body></html>');
    await page.addScriptTag({ path: path.join(dependencyRoot, 'vue/dist/vue.global.prod.js') });
    await page.addScriptTag({ path: path.join(dependencyRoot, 'element-plus/dist/index.full.min.js') });
    await page.addStyleTag({ path: path.join(dependencyRoot, 'element-plus/dist/index.css') });
    await page.addStyleTag({ content: 'body{font-family:Arial,sans-serif;margin:20px}#query{margin-bottom:20px}details pre{white-space:pre-wrap;overflow-wrap:anywhere}' + style.join('\n') });
    await page.evaluate(compiled => {
      const cache = {};
      const empty = { render: () => null };
      const graphics = { init: () => ({ setOption() {}, dispose() {}, clear() {}, resize() {} }),
        graphic: { LinearGradient: class {} } };
      const load = name => {
        if (name === 'vue') return Vue;
        if (name === 'element-plus') return ElementPlus;
        if (name === 'echarts') return graphics;
        if (name.endsWith('/safeContent')) return { safeChartOption: value => value };
        if (name.endsWith('/request')) return { can: () => false, downloadQuery: () => { throw Error('不允许请求接口'); } };
        if (name.endsWith('/SmartChart.vue')) return { default: empty };
        const key = name.endsWith('/decimalDisplay') ? 'utils/decimalDisplay.ts'
          : name.endsWith('/DataAnalysisDashboard.vue') ? 'components/DataAnalysisDashboard.vue' : name;
        if (cache[key]) return cache[key];
        if (!compiled[key]) throw Error('未登记组件依赖：' + name);
        const exports = {}; cache[key] = exports; new Function('require', 'exports', compiled[key])(load, exports); return exports;
      };
      window.numericFixture = Vue.reactive({ result: { totalCount: 1, executionTime: 1,
        columns: [{ columnName: 'amount', dataType: 'decimal', comment: '金额' }], data: [{ amount: '1' }],
        insights: [], analysis: '本轮隔离统计样本', statistics: { columns: { amount: { mean: '1', min: '1', max: '1' } } },
        enhancedAnalysis: { metrics: [{ field: 'amount', label: '金额', trend: { direction: 'up', percentChange: '1', absoluteChange: '1' },
          projection: { value: '1', confidence: 'low', caveat: '仅本次样本参考' } }] }, generatedSql: 'SELECT fixture',
      } });
      const query = load('components/business/QueryResultPanel.vue').default;
      const dashboard = load('components/DataAnalysisDashboard.vue').default;
      Vue.createApp({ render: () => Vue.h(query, { result: numericFixture.result }) }).use(ElementPlus).mount('#query');
      Vue.createApp({ render: () => Vue.h(dashboard, { data: numericFixture.result.data, columns: numericFixture.result.columns,
        statistics: numericFixture.result.statistics }) }).use(ElementPlus).mount('#dashboard');
    }, modules);
    const tabs = await page.$$('#query [role="tab"]');
    for (const tab of tabs) if ((await tab.evaluate(node => node.textContent)).includes('统计与洞察')) await tab.click();
    const longValue = '9'.repeat(500) + '.00123';
    const cases = [
      ['大整数', '9007199254740993', '9,007,199,254,740,993'],
      ['负高精度数', '-12345678901234567890.123456789', '-12,345,678,901,234,567,890.123456789'],
      ['小数不分组', '1234.123456', '1,234.123456'],
      ['极小数', '0.0000000000000000000123', '0.0000000000000000000123'],
      ['科学计数小数', '1.234567890123456789e-100', '1.234567890123456789e-100'],
      ['巨大指数', '1e999999999', '1e999999999'],
      ['正号及末尾零', '+12345.6700', '+12,345.6700'],
      ['短数值', '12.5', '12.5'], ['零', 0, '0'], ['空值', null, '—'], ['空字符串', '', '—'],
      ['已有number小数', 12.3456, '12.3456'],
      ['不安全JSON整数', Number.MAX_SAFE_INTEGER + 1, '约 9,007,199,254,740,992'],
      ['超长值', longValue, BigInt('9'.repeat(500)).toLocaleString('en-US') + '.00123'],
    ];
    for (const [name, value, expected] of cases) {
      const actual = await page.evaluate(async value => {
        const previous = numericFixture.result;
        numericFixture.result = { ...previous, data: [{ amount: '1' }],
          statistics: { columns: { amount: { mean: value, min: '1', max: '1' } } },
          enhancedAnalysis: { metrics: [{ field: 'amount', label: '金额', trend: { direction: 'up', percentChange: value, absoluteChange: value },
            projection: { value, confidence: 'low', caveat: '仅本次样本参考' } }] } };
        await Vue.nextTick(); await Vue.nextTick();
        const card = document.querySelector('#query .enhanced-card');
        const fields = [...card.querySelectorAll('dd')];
        const metric = document.querySelector('#dashboard .metric-2 .metric-value');
        const read = node => ({ text: node.textContent.trim(), title: node.getAttribute('title'), width: node.clientWidth,
          scrollWidth: node.scrollWidth, textOverflow: getComputedStyle(node).textOverflow });
        const trend = read(fields[0].querySelector('.exact-stat-value') || fields[0]);
        const projection = read(fields[1].querySelector('.exact-stat-value') || fields[1]);
        // 基线尚未包裹数值 span 时，也只比较数值文本，不把方向/置信度标签当成精度错误。
        if (!fields[0].querySelector('.exact-stat-value')) trend.text = trend.text.replace(/^(上升|下降|持平)\s*/, '');
        if (!fields[1].querySelector('.exact-stat-value')) projection.text = [...fields[1].childNodes]
          .filter(node => node.nodeType === Node.TEXT_NODE).map(node => node.textContent).join('').trim();
        return { trend, projection, mean: read(metric),
          pageWidth: document.documentElement.scrollWidth, viewport: innerWidth };
      }, value);
      const expectedTrend = expected + (value == null ? '' : '%');
      if (actual.trend.text !== expectedTrend) failures.push({ name, component: 'QueryResultPanel趋势', expected: expectedTrend, actual: actual.trend.text });
      if (actual.projection.text !== expected) failures.push({ name, component: 'QueryResultPanel投影', expected, actual: actual.projection.text });
      const expectedMean = value == null ? '未计算' : expected;
      if (actual.mean.text !== expectedMean) failures.push({ name, component: 'DataAnalysisDashboard均值', expected: expectedMean, actual: actual.mean.text });
      if (name === '超长值') {
        for (const field of ['trend', 'projection', 'mean']) {
          if (!actual[field].title?.includes(value)) failures.push({ name, field, reason: '完整原值提示丢失' });
          if (actual[field].textOverflow !== 'ellipsis' || actual[field].scrollWidth <= actual[field].width) failures.push({ name, field, reason: '超长数值未保持卡片内省略展示' });
        }
        if (actual.pageWidth > actual.viewport) failures.push({ name, reason: '超长值导致页面横向溢出', actual });
      }
      if (name === '不安全JSON整数' && !actual.mean.title?.includes('安全整数')) failures.push({ name, reason: '未说明接收到的JSON整数已有精度边界' });
      checks.push({ name, actual });
    }
    await page.screenshot({ path: path.join(output, 'numeric-components.png'), fullPage: true });
    const result = { ok: failures.length === 0 && consoleErrors.length === 0, checks, failures, consoleErrors,
      boundary: '真实Vue+ElementPlus+Edge，图形库仅渲染桩，所有数据为内存样例，无接口或数据库请求' };
    fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify(result, null, 2));
    assert.deepEqual(consoleErrors, []);
    assert.deepEqual(failures, [], JSON.stringify(failures.slice(0, 6)));
    console.log(JSON.stringify({ ok: true, cases: checks.length, output }));
  } finally { await browser.close(); }
}

test('真实Vue统计文本保留精度、长值原文和既有卡片布局', { skip: process.platform !== 'win32' }, () => {
  const root = path.resolve(__dirname, '../..');
  const python = process.env.TEST5_E2E_PYTHON || path.join(root, '.venv_py312/Scripts/python.exe');
  assert(fs.existsSync(python), '需要现有隔离 Python，不自动安装');
  const output = path.join(root, '.implementation/ui-results/numeric-display-' + Date.now().toString(36));
  fs.mkdirSync(output, { recursive: true });
  const source = String.raw`
import json, os, subprocess, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0, str(Path('tests/dsh').resolve()))
from business_browser_acceptance import WindowsProcessJob, stop_test_process, check_network_evidence
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers(); self.wfile.write(b'<!doctype html><title>fixture</title>')
    def log_message(self, *_): pass
host = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=host.serve_forever, daemon=True).start()
folder = Path(os.environ['TEST5_NUMERIC_OUTPUT']); report, permit = folder/'network.json', folder/'ready'
env = {**os.environ, 'NODE_OPTIONS': '', 'TEST5_E2E_ALLOW_WRITE': 'isolated',
       'TEST5_E2E_BASE_URL': 'http://127.0.0.1:%d' % host.server_port,
       'TEST5_E2E_NETWORK_REPORT': str(report), 'TEST5_E2E_PROCESS_GATE': str(permit)}
job, child = WindowsProcessJob(), None
try:
    child = subprocess.Popen(['node', '--import', Path('tests/dsh/local_network_only.mjs').resolve().as_uri(),
        '--require', str(Path('tests/ui/isolated_browser_guard.cjs').resolve()), '-e', env.pop('TEST5_NUMERIC_SOURCE')],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', creationflags=0x08000000)
    job.assign(child); permit.write_text('ready')
    output, _ = child.communicate(timeout=60)
    (folder/'browser.log').write_text(output, encoding='utf-8'); print(output)
    assert child.returncode == 0, output
    check_network_evidence(report)
finally:
    (folder/'cleanup.json').write_text(json.dumps(stop_test_process(child, job)), encoding='utf-8')
    host.shutdown(); host.server_close()
`;
  const result = spawnSync(python, ['-c', source], { cwd: root, encoding: 'utf8', timeout: 75000, windowsHide: true,
    env: { ...process.env, PYTHONIOENCODING: 'utf-8', TEST5_NUMERIC_OUTPUT: output,
      TEST5_NUMERIC_SOURCE: `(${verifyComponents.toString()})().catch(error=>{console.error(error);process.exitCode=1;});` } });
  assert.equal(result.status, 0, `${result.error || ''}\n${result.stdout}\n${result.stderr}\n证据：${output}`);
  console.log(result.stdout.trim());
});
