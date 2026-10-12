/* 确认框时序的真实浏览器窄回归：仅使用本轮回环 HTML 夹具，不连接应用或数据库。 */
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { test } = require('node:test');

async function runBrowserCases() {
  const assert = require('node:assert/strict');
  const fs = require('node:fs');
  const path = require('node:path');
  const puppeteer = require('puppeteer-core');
  const { confirmMessage } = require(path.resolve('tests/ui/full_browser_analytics_acceptance.js'));
  const output = process.env.TEST5_CONFIRM_OUTPUT;
  const browser = await puppeteer.launch({ executablePath: process.env.TEST5_EDGE_PATH
    || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  const checks = [];
  try {
    const page = await browser.newPage();
    await page.goto(process.env.TEST5_E2E_BASE_URL);
    await page.setContent(`<!doctype html><html lang="zh-CN"><meta charset="utf-8">
      <style>.overlay{position:fixed;inset:0;background:#fff8;display:grid;place-items:center}
      .el-message-box{background:white;padding:30px;border:1px solid #555}button{padding:12px}</style>
      <body><h1>本轮确认框生命周期夹具</h1></body></html>`);
    await page.evaluate(() => {
      window.clicks = [];
      window.openDialog = ({ id, title, message, z = 10, leaving = false, disabled = false }) => {
        const overlay = document.createElement('div');
        overlay.id = id;
        overlay.className = 'overlay' + (leaving ? 'fade-leave-active' : '');
        overlay.style.zIndex = String(z);
        const dialog = document.createElement('div');
        dialog.className = 'el-message-box';
        const heading = document.createElement('h2');
        heading.className = 'el-message-box__title'; heading.textContent = title;
        const content = document.createElement('p');
        content.className = 'el-message-box__message'; content.textContent = message;
        const button = document.createElement('button');
        button.textContent = '确定'; button.disabled = disabled;
        button.addEventListener('click', () => {
          window.clicks.push(id);
          overlay.classList.add('fade-leave-active');
          // 由测试显式结束退场，稳定复现两框共存，不依赖机器快慢或固定等待。
          overlay.dataset.closing = 'true';
        });
        dialog.append(heading, content, button); overlay.append(dialog); document.body.append(overlay);
      };
      window.clearDialogs = () => {
        document.querySelectorAll('.overlay').forEach(node => node.remove()); window.clicks = [];
      };
    });
    const expected = { title: '提示', message: '确定删除报表「第二份」吗？' };
    const open = data => page.evaluate(value => window.openDialog(value), data);
    const clicks = () => page.evaluate(() => window.clicks);
    const render = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const finish = id => page.evaluate(value => document.getElementById(value).remove(), id);
    const clicked = id => page.waitForFunction(value => window.clicks.includes(value), { timeout: 5000 }, id);

    // 第一份仍可见且新框尚未挂载：旧的全局“首个可见按钮”会在这里误点。
    await open({ id: 'retiring-first', title: '提示', message: '确定删除报表「第一份」吗？', leaving: true });
    let settled = false;
    let confirmation = confirmMessage(expected, page.mainFrame()).then(() => { settled = true; });
    await render();
    assert.deepEqual(await clicks(), [], '新框未出现时不能操作旧框');
    await open({ id: 'current-second', ...expected, z: 20 });
    await clicked('current-second');
    assert.deepEqual(await clicks(), ['current-second']);
    await render();
    assert.equal(settled, false, '点击后必须等待该确认框真正退出');
    await page.screenshot({ path: path.join(output, 'old-and-current-dialogs.png') });
    await finish('current-second');
    await confirmation;
    assert.equal(await page.$eval('#retiring-first', node => node.isConnected), true,
      '只等待本次实例退出，不依赖不相关旧框的退出时机');
    checks.push('旧框仍有矩形且新框稍后出现，只点击本次正文对应按钮并等待本次退场');

    // 同文案旧框排在 DOM 末尾，不能靠反转集合猜测哪个是新框。
    await page.evaluate(() => window.clearDialogs());
    await open({ id: 'current-same', ...expected, z: 20 });
    await open({ id: 'retiring-same', ...expected, z: 10, leaving: true });
    confirmation = confirmMessage(expected, page.mainFrame());
    await clicked('current-same');
    assert.deepEqual(await clicks(), ['current-same']);
    await finish('current-same'); await confirmation;
    checks.push('同标题同正文的退场旧框不能因 DOM 顺序而被点击');

    // 标题也必须匹配；遮挡中的目标按钮不应被程序化点击穿透。
    await page.evaluate(() => window.clearDialogs());
    await open({ id: 'covered-current', ...expected, z: 10 });
    await open({ id: 'different-title', ...expected, title: '其它操作', z: 20 });
    confirmation = confirmMessage(expected, page.mainFrame());
    await render(); assert.deepEqual(await clicks(), []);
    await finish('different-title'); await clicked('covered-current');
    assert.deepEqual(await clicks(), ['covered-current']);
    await finish('covered-current'); await confirmation;
    checks.push('标题不符的框不能被确认，也不能穿透上层框点击被遮挡按钮');

    await page.evaluate(() => window.clearDialogs());
    await open({ id: 'disabled-current', ...expected, disabled: true });
    confirmation = confirmMessage(expected, page.mainFrame());
    await render(); assert.deepEqual(await clicks(), []);
    await page.$eval('#disabled-current button', node => { node.disabled = false; });
    await clicked('disabled-current'); await finish('disabled-current'); await confirmation;
    checks.push('确认按钮禁用期间等待，恢复可交互后仅点击一次');
    fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({ ok: true, checks }, null, 2));
    console.log(JSON.stringify({ ok: true, checks: checks.length, output }));
  } catch (error) {
    fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({ ok: false, checks, error: String(error.stack || error) }, null, 2));
    throw error;
  } finally {
    await browser.close();
  }
}

test('真实 Edge 按内容和生命周期定位确认框，旧框不会吞掉新删除操作', {
  skip: process.platform !== 'win32',
}, () => {
  const root = path.resolve(__dirname, '../..');
  const python = process.env.TEST5_E2E_PYTHON || path.join(root, '.venv_py312/Scripts/python.exe');
  assert(fs.existsSync(python), '需要现有隔离 Python，不自动安装');
  const output = path.join(root, '.implementation/ui-results/analytics-confirmation-' + Date.now().toString(36));
  fs.mkdirSync(output, { recursive: true });
  // 复用现有网络代理与专属 Windows Job，超时或异常均只回收本轮浏览器进程树。
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
folder = Path(os.environ['TEST5_CONFIRM_OUTPUT'])
report, permit = folder/'network.json', folder/'ready'
env = {**os.environ, 'NODE_OPTIONS': '', 'TEST5_E2E_ALLOW_WRITE': 'isolated',
       'TEST5_E2E_BASE_URL': 'http://127.0.0.1:%d' % host.server_port,
       'TEST5_E2E_NETWORK_REPORT': str(report), 'TEST5_E2E_PROCESS_GATE': str(permit)}
job, child = WindowsProcessJob(), None
try:
    child = subprocess.Popen(['node', '--import', Path('tests/dsh/local_network_only.mjs').resolve().as_uri(),
        '--require', str(Path('tests/ui/isolated_browser_guard.cjs').resolve()), '-e', env.pop('TEST5_CONFIRM_SOURCE')],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', creationflags=0x08000000)
    job.assign(child); permit.write_text('ready')
    output, _ = child.communicate(timeout=60)
    (folder/'browser.log').write_text(output, encoding='utf-8')
    print(output)
    assert child.returncode == 0, output
    check_network_evidence(report)
finally:
    cleanup = stop_test_process(child, job)
    (folder/'cleanup.json').write_text(json.dumps(cleanup), encoding='utf-8')
    host.shutdown(); host.server_close()
`;
  const result = spawnSync(python, ['-c', source], { cwd: root, encoding: 'utf8', timeout: 75000, windowsHide: true,
    env: { ...process.env, PYTHONIOENCODING: 'utf-8', TEST5_CONFIRM_OUTPUT: output,
      TEST5_CONFIRM_SOURCE: `(${runBrowserCases.toString()})().catch(error=>{console.error(error);process.exitCode=1;});` } });
  assert.equal(result.status, 0, `${result.error || ''}\n${result.stdout}\n${result.stderr}\n证据：${output}`);
  console.log(result.stdout.trim());
});
