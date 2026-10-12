/* 代理边界回归：只创建自己的回环服务，不连接真实业务服务或外网。 */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const net = require('node:net');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { createGuard } = require('./isolated_browser_guard.cjs');

const listen = (server, host = '127.0.0.1', port = 0) => new Promise((resolve, reject) => {
  server.once('error', reject); server.listen(port, host, resolve);
});
const close = server => new Promise(resolve => server.close(resolve));
const proxyGet = (port, target) => new Promise((resolve, reject) => {
  http.get({ hostname: '127.0.0.1', port, path: target, agent: false }, response => {
    response.resume(); response.on('end', () => resolve(response.statusCode));
  }).on('error', reject);
});

test('同号IPv6端口属于另一服务时不得跟随IPv4白名单放行', async t => {
  const host = http.createServer((request, response) => response.end('ipv4'));
  let unrelatedRequests = 0;
  const unrelated = http.createServer((request, response) => { unrelatedRequests++; response.end('ipv6'); });
  await listen(host);
  try { await listen(unrelated, '::1', host.address().port); }
  catch (error) { await close(host); t.skip(`本机无法建立独立IPv6回环夹具：${error.code}`); return; }
  const guard = createGuard(`http://127.0.0.1:${host.address().port}`);
  try {
    const port = await guard.ready;
    assert.equal(await proxyGet(port, `http://127.0.0.1:${host.address().port}/`), 200);
    assert.equal(await proxyGet(port, `http://[::1]:${host.address().port}/`), 403);
    assert.equal(unrelatedRequests, 0);
  } finally { await guard.close(); await Promise.all([close(host), close(unrelated)]); }
});

test('CONNECT与WebSocket都拒绝未登记目标且允许自己的桩', async () => {
  const host = http.createServer((request, response) => response.end('ok'));
  host.on('upgrade', (request, socket) => socket.end('HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n'));
  await listen(host);
  const guard = createGuard(`http://127.0.0.1:${host.address().port}`);
  const connect = (port, target) => new Promise((resolve, reject) => {
    const request = http.request({ hostname: '127.0.0.1', port, method: 'CONNECT', path: target, agent: false });
    request.on('connect', (response, socket) => { socket.destroy(); resolve(response.statusCode); });
    request.on('error', reject); request.end();
  });
  const upgrade = (port, target) => new Promise((resolve, reject) => {
    const request = http.request({ hostname: '127.0.0.1', port, path: target, agent: false,
      headers: { Connection: 'Upgrade', Upgrade: 'websocket' } });
    request.on('upgrade', (response, socket) => { socket.destroy(); resolve(response.statusCode); });
    request.on('response', response => { response.resume(); resolve(response.statusCode); });
    request.on('error', reject); request.end();
  });
  try {
    const port = await guard.ready;
    assert.equal(await connect(port, `127.0.0.1:${host.address().port}`), 200);
    for (const target of ['127.0.0.1:8011', '127.0.0.1:8012', '[::1]:8011', 'example.invalid:443']) {
      assert.equal(await connect(port, target), 403);
    }
    assert.equal(await upgrade(port, `ws://127.0.0.1:${host.address().port}/`), 101);
    assert.equal(await upgrade(port, 'ws://127.0.0.1:8011/'), 403);
    assert.equal(await upgrade(port, 'ws://example.invalid/'), 403);
  } finally { await guard.close(); await close(host); }
});

test('客户端CONNECT后RST只记录断开，业务上游RST仍返回失败并保存证据', async () => {
  const host = http.createServer((request, response) => {
    if (request.url === '/reset-upstream') request.socket.resetAndDestroy();
    else response.end('alive');
  });
  await listen(host);
  const guard = createGuard(`http://127.0.0.1:${host.address().port}`);
  const resetConnect = (port, target) => new Promise((resolve, reject) => {
    const socket = net.connect({ host: '127.0.0.1', port }, () => {
      socket.write(`CONNECT ${target} HTTP/1.1\r\nHost: ${target}\r\n\r\n`);
    });
    socket.once('data', () => socket.resetAndDestroy());
    socket.once('close', resolve); socket.once('error', reject);
  });
  try {
    const port = await guard.ready;
    await resetConnect(port, `127.0.0.1:${host.address().port}`);
    await resetConnect(port, 'edge.microsoft.com:443');
    await new Promise(resolve => setTimeout(resolve, 30));
    assert(guard.evidence.client_disconnects >= 1, '真实TCP RST应被服务端原始Socket捕获');
    assert.equal(guard.evidence.upstream_errors, 0, '浏览器主动断开不得误记为业务上游失败');
    assert.equal(await proxyGet(port, `http://127.0.0.1:${host.address().port}/`), 200);
    assert.equal(await proxyGet(port, `http://127.0.0.1:${host.address().port}/reset-upstream`), 502);
    assert.equal(guard.evidence.upstream_errors, 1, '真实业务上游失败不得被断开处理吞掉');
  } finally { await guard.close(); await close(host); }
});

test('证据创建即保存、允许计数实时保存，背景候选用于业务时仍失败', async () => {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'browser-guard-'));
  const report = path.join(temporary, 'network.json');
  const host = http.createServer((request, response) => response.end('ok'));
  await listen(host);
  const guard = createGuard(`http://127.0.0.1:${host.address().port}`, report);
  const read = () => JSON.parse(fs.readFileSync(report));
  try {
    assert.equal(read().allowed_requests, 0);
    const port = await guard.ready;
    await proxyGet(port, `http://127.0.0.1:${host.address().port}/?token=secret`);
    assert.equal(read().allowed_requests, 1);
    assert.equal(await proxyGet(port, 'http://edge.microsoft.com/background?token=secret'), 403);
    assert.equal(read().background_candidate_requests, 1);
    assert.equal(read().unexpected_requests, 0);
    guard.observeApplicationRequest('https://edge.microsoft.com/business?token=secret');
    assert.equal(read().application_blocked_requests, 1);
    assert(!fs.readFileSync(report, 'utf8').includes('secret'));
  } finally {
    await guard.close(); await close(host);
    assert.equal(path.dirname(path.resolve(temporary)), path.resolve(os.tmpdir()));
    assert(path.basename(temporary).startsWith('browser-guard-'));
    fs.rmSync(temporary, { recursive: true, force: true });
  }
});

test('SmartScreen精确HTTPS目标只作背景候选，CONNECT仍拒绝且页面Worker请求仍失败', async () => {
  const host = http.createServer((request, response) => response.end('ok'));
  await listen(host);
  const guard = createGuard(`http://127.0.0.1:${host.address().port}`);
  const originalConnect = net.connect;
  const upstreamTargets = [];
  // 这个回归只连本轮代理；即使分类逻辑退化，也禁止建立外部上游连接。
  net.connect = (options, ...args) => {
    upstreamTargets.push(options.host);
    assert(['127.0.0.1', '::1'].includes(options.host), '不得连接外部目标');
    return originalConnect(options, ...args);
  };
  const connect = (port, target) => new Promise((resolve, reject) => {
    const request = http.request({ hostname: '127.0.0.1', port, method: 'CONNECT', path: target, agent: false });
    request.on('connect', (response, socket) => { socket.destroy(); resolve(response.statusCode); });
    request.on('error', reject); request.end();
  });
  try {
    const port = await guard.ready;
    assert.equal(await connect(port, 'telem-edge.smartscreen.microsoft.com:443'), 403);
    assert.equal(guard.evidence.background_candidate_requests, 1);
    assert.equal(guard.evidence.unexpected_requests, 0);
    assert.deepEqual(guard.evidence.background_candidate_origins, ['https://telem-edge.smartscreen.microsoft.com']);
    // 不把未观测的协议、端口、相似主机或子域一起归入背景候选。
    assert.equal(await proxyGet(port, 'http://telem-edge.smartscreen.microsoft.com/'), 403);
    for (const target of ['telem-edge.smartscreen.microsoft.com:8443',
      'child.telem-edge.smartscreen.microsoft.com:443', 'telem-edge.smartscreen.microsoft.com.example.invalid:443']) {
      assert.equal(await connect(port, target), 403);
    }
    assert.equal(guard.evidence.unexpected_requests, 4);
    assert.equal(guard.evidence.background_candidate_requests, 1);
    for (const type of ['page', 'worker']) {
      guard.observeApplicationRequest('https://telem-edge.smartscreen.microsoft.com/fixture?token=synthetic-secret', type);
    }
    assert.equal(guard.evidence.application_blocked_requests, 2);
    assert.deepEqual(guard.evidence.application_blocked_by_target_type, { page: 1, worker: 1 });
    assert(!JSON.stringify(guard.evidence).includes('synthetic-secret'));
    assert.deepEqual(upstreamTargets, [], '背景候选分类不能创建上游连接');
  } finally {
    net.connect = originalConnect;
    await guard.close(); await close(host);
  }
});

function pythonCheck(source) {
  const root = path.resolve(__dirname, '../..');
  const python = process.env.TEST5_E2E_PYTHON || path.join(root, '.venv_py312', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  assert(fs.existsSync(python), '需要仓库现有隔离Python，不自动安装');
  const result = spawnSync(python, ['-c', source], { cwd: root, encoding: 'utf8', timeout: 30000, windowsHide: true });
  assert.equal(result.status, 0, `${result.error || ''}\n${result.stdout}\n${result.stderr}`);
}

test('Windows专属容器在父进程退出后仍回收后代且保留其它进程', { skip: process.platform !== 'win32' }, () => {
  pythonCheck(String.raw`
import json, os, subprocess, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path('tests/dsh').resolve()))
from business_browser_acceptance import WindowsProcessJob, stop_test_process
with tempfile.TemporaryDirectory(prefix='browser-job-') as directory:
    permit = Path(directory) / 'ready'
    marker = Path(directory) / 'child'
    child_source = '''import pathlib, subprocess, sys, time
permit, marker = map(pathlib.Path, sys.argv[1:])
while not permit.exists(): time.sleep(.01)
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'], creationflags=0x08000000)
marker.write_text(str(child.pid))
'''
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'], creationflags=0x08000000)
    job = WindowsProcessJob()
    parent = subprocess.Popen([sys.executable, '-c', child_source, str(permit), str(marker)], creationflags=0x08000000)
    try:
        job.assign(parent)
        permit.write_text('ready')
        parent.wait(timeout=5)
        assert marker.exists() and job.active() >= 1
        result = stop_test_process(parent, job)
        assert result['active_before'] >= 1 and result['active_after'] == 0, result
        assert unrelated.poll() is None
    finally:
        stop_test_process(parent, job)
        unrelated.kill(); unrelated.wait(timeout=5)
`);
});

test('清理失败不阻止其它清理和诊断，网络证据决定验收终态', () => {
  pythonCheck(String.raw`
import json, sys, tempfile, time
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path('tests/dsh').resolve()))
import business_browser_acceptance as runner
def fail(): raise RuntimeError('fixture cleanup failed')
runner.stop_test_process = lambda *args: fail()
with tempfile.TemporaryDirectory(prefix='browser-cleanup-') as directory:
    folder = Path(directory)
    host = SimpleNamespace(close=fail, diagnostics=['fixture diagnostics'])
    check = {'status': 'passed'}
    runner.finish_case(check, child=object(), job=None, host=host, case_dir=folder, started=time.monotonic())
    assert check['status'] == 'failed' and len(check['cleanup_errors']) == 3, check
    assert check['finished_at'] and (folder / 'host-diagnostics.log').read_text() == 'fixture diagnostics'
    evidence = dict(allowed_requests=1, blocked_requests=1, unexpected_requests=0,
        application_blocked_requests=0, background_candidate_requests=1)
    report = folder / 'network.json'
    report.write_text(json.dumps(evidence))
    assert runner.check_network_evidence(report)['background_candidate_requests'] == 1
    for field in ('unexpected_requests', 'application_blocked_requests', 'observer_errors', 'upstream_errors'):
        report.write_text(json.dumps({**evidence, field: 1}))
        try: runner.check_network_evidence(report)
        except RuntimeError: pass
        else: raise AssertionError(field)
`);
});

test('真实Edge的页面弹窗Worker使用同一代理且由本轮容器回收', {
  skip: process.platform !== 'win32' || !fs.existsSync(process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'),
}, () => {
  // 只启动两个本轮HTTP夹具；未登记的夹具也不应收到请求，不连接平台数据库。
  pythonCheck(String.raw`
import json, os, subprocess, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0, str(Path('tests/dsh').resolve()))
from business_browser_acceptance import WindowsProcessJob, stop_test_process
denied_calls = []
class Denied(BaseHTTPRequestHandler):
    def do_GET(self):
        denied_calls.append(self.path); self.send_response(200); self.end_headers()
    def log_message(self, *_): pass
denied = ThreadingHTTPServer(('127.0.0.1', 0), Denied)
denied_url = 'http://127.0.0.1:%d/private' % denied.server_port
class Allowed(BaseHTTPRequestHandler):
    def do_GET(self):
        worker = "fetch(%s).catch(()=>{}); fetch('https://telem-edge.smartscreen.microsoft.com/fixture').catch(()=>{}); setTimeout(()=>postMessage('done'), 500)" % json.dumps(denied_url)
        body = worker if self.path == '/worker.js' else ('<script>'+worker.replace("postMessage('done')", 'void 0')+'</script>' if self.path == '/popup' else '<html>fixture</html>')
        self.send_response(200); self.send_header('Content-Type', 'application/javascript' if self.path == '/worker.js' else 'text/html'); self.end_headers(); self.wfile.write(body.encode())
    def log_message(self, *_): pass
allowed = ThreadingHTTPServer(('127.0.0.1', 0), Allowed)
for server in (denied, allowed): threading.Thread(target=server.serve_forever, daemon=True).start()
with tempfile.TemporaryDirectory(prefix='browser-proxy-smoke-') as directory:
    folder = Path(directory); permit = folder/'ready'; report = folder/'network.json'
    base = 'http://127.0.0.1:%d' % allowed.server_port
    source = '''const fs=require('node:fs'); const p=require('puppeteer-core');
(async()=>{const b=await p.launch({executablePath:process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
try { const page=await b.newPage(); await page.goto(process.env.TEST5_E2E_BASE_URL);
await page.evaluate(async denied=>{await fetch(denied).catch(()=>{}); await fetch('https://telem-edge.smartscreen.microsoft.com/fixture').catch(()=>{}); await new Promise(resolve=>{const w=new Worker('/worker.js');w.onmessage=()=>{w.terminate();resolve()}});window.open('/popup')},process.env.DENIED_URL);
const complete=r=>r.unexpected_requests>=3 && r.application_blocked_by_target_type.page>=4 && r.application_blocked_by_target_type.worker>=2 && r.application_blocked_origins.includes('https://telem-edge.smartscreen.microsoft.com');
const deadline=Date.now()+5000; while(Date.now()<deadline){const r=JSON.parse(fs.readFileSync(process.env.TEST5_E2E_NETWORK_REPORT));if(complete(r))break;await new Promise(r=>setTimeout(r,50))}
const r=JSON.parse(fs.readFileSync(process.env.TEST5_E2E_NETWORK_REPORT));if(!complete(r) || r.observer_errors)throw Error('missing blocked page/worker/popup evidence '+JSON.stringify(r));
} finally {await b.close()}})().catch(e=>{console.error(e);process.exitCode=1});'''
    env={**os.environ,'NODE_OPTIONS':'','TEST5_E2E_ALLOW_WRITE':'isolated','TEST5_E2E_BASE_URL':base,
         'TEST5_E2E_NETWORK_REPORT':str(report),'TEST5_E2E_PROCESS_GATE':str(permit),'DENIED_URL':denied_url}
    job=WindowsProcessJob()
    child=subprocess.Popen(['node','--require',str(Path('tests/ui/isolated_browser_guard.cjs').resolve()),'-e',source],env=env,
        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',creationflags=0x08000000)
    try:
        job.assign(child)
        time.sleep(.1)
        assert not report.exists(), '未取得进程容器许可前执行了guard'
        permit.write_text('ready')
        output,_=child.communicate(timeout=20)
        assert child.returncode==0, output
        assert denied_calls==[], denied_calls
    finally:
        stop_test_process(child,job)
        for server in (denied,allowed): server.shutdown();server.server_close()
`);
});

test('允许登记宿主及本进程桩，外网和在用应用端口在连接前拒绝', async () => {
  const host = http.createServer((request, response) => response.end('本轮宿主'));
  await new Promise(resolve => host.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${host.address().port}`;
  const guard = createGuard(base);
  const proxyPort = await guard.ready;
  const fixture = http.createServer((request, response) => response.end('本轮桩'));
  await new Promise(resolve => fixture.listen(0, '127.0.0.1', resolve));
  const fetchThroughProxy = target => new Promise((resolve, reject) => {
    http.get({ hostname: '127.0.0.1', port: proxyPort, path: target, agent: false }, response => {
      let text = ''; response.on('data', chunk => { text += chunk; });
      response.on('end', () => resolve({ status: response.statusCode, text }));
    }).on('error', reject);
  });
  try {
    assert.deepEqual(await fetchThroughProxy(base + '/ok'), { status: 200, text: '本轮宿主' });
    assert.equal((await fetchThroughProxy(`http://127.0.0.1:${fixture.address().port}/stub`)).text, '本轮桩');
    for (const target of ['http://example.invalid/blocked?token=secret', 'http://192.0.2.1/blocked',
      'http://127.0.0.1:8011/', 'http://127.0.0.1:8012/', `http://127.0.0.1:${proxyPort}/`]) {
      assert.equal((await fetchThroughProxy(target)).status, 403);
    }
    assert.equal(guard.evidence.allowed_requests, 2);
    assert.equal(guard.evidence.blocked_requests, 5);
    assert(!JSON.stringify(guard.evidence).includes('secret'));
  } finally {
    await guard.close();
    await Promise.all([new Promise(resolve => host.close(resolve)), new Promise(resolve => fixture.close(resolve))]);
  }
});
