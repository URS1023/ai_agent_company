/* 网络证据写入只使用临时文件、受控 rename 故障和本机代理，不启动浏览器。 */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { createGuard } = require('./isolated_browser_guard.cjs');
const { createNetworkReportWriter } = require('./network_report_writer.cjs');

/** 从真实守卫取得保存入口，基线红例和修复后均经过同一路径。 */
async function fixture() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'network-writer-'));
  const report = path.join(directory, 'network.json');
  const guard = createGuard('http://127.0.0.1:54321', report);
  await guard.ready;
  const previous = fs.readFileSync(report, 'utf8');
  const rename = fs.renameSync;
  return { directory, report, guard, previous, rename, async close(expectedFailure) {
    fs.renameSync = rename;
    try { await guard.close(); }
    catch (error) { if (error !== expectedFailure) throw error; }
    assert.equal(path.dirname(path.resolve(directory)), path.resolve(os.tmpdir()));
    assert.ok(path.basename(directory).startsWith('network-writer-'));
    fs.rmSync(directory, { recursive: true, force: true });
  } };
}

function renameError(code, source, destination) {
  return Object.assign(new Error(`受控 ${code} rename 故障`), { code, syscall: 'rename', path: source, dest: destination });
}

test('瞬态占用只重试rename并完整替换旧JSON', async () => {
  const f = await fixture();
  let attempts = 0;
  const temporaryContents = [], targets = [];
  f.guard.evidence.allowed_requests = 7;
  f.guard.evidence.blocked_requests = 3;
  fs.renameSync = (source, destination) => {
    attempts++;
    assert.equal(fs.readFileSync(destination, 'utf8'), f.previous, '替换成功前不得删除或覆盖旧目标');
    temporaryContents.push(fs.readFileSync(source, 'utf8'));
    targets.push(source);
    if (attempts <= 2) throw renameError('EPERM', source, destination);
    return f.rename(source, destination);
  };
  try {
    f.guard.save();
    assert.equal(attempts, 3);
    assert.equal(new Set(targets).size, 1, '重试必须复用原始完整临时文件');
    assert.equal(new Set(temporaryContents).size, 1, '文件重试不能再次计数或改写快照');
    assert.deepEqual(JSON.parse(fs.readFileSync(f.report, 'utf8')), f.guard.evidence);
    assert.equal(fs.existsSync(targets[0]), false, '真实 rename 应消费临时文件');
    console.log('NETWORK_WRITER transient attempts=3 target_complete=1 counts=7/3');
  } finally { await f.close(); }
});

test('持续占用达到固定上限后保留旧目标及失败临时原件', async () => {
  const f = await fixture();
  let attempts = 0, temporary, failure;
  f.guard.evidence.allowed_requests = 9;
  fs.renameSync = (source, destination) => {
    attempts++;
    temporary = source;
    assert.equal(fs.readFileSync(destination, 'utf8'), f.previous);
    throw renameError('EPERM', source, destination);
  };
  try {
    assert.throws(() => f.guard.save(), error => { failure = error; return error.code === 'EPERM'; });
    console.log(`NETWORK_WRITER persistent attempts=${attempts} old_target_preserved=${fs.readFileSync(f.report, 'utf8') === f.previous} temporary=${temporary}`);
    assert.equal(attempts, 4);
    assert.equal(fs.readFileSync(f.report, 'utf8'), f.previous);
    assert.equal(JSON.parse(fs.readFileSync(temporary, 'utf8')).allowed_requests, 9);
    assert.equal(failure.reportTemporary, temporary);
    fs.renameSync = f.rename;
    assert.throws(() => f.guard.save(), error => error === failure, '永久失败后不能由exit回写掩盖失败原件');
    assert.equal(fs.readFileSync(f.report, 'utf8'), f.previous);
  } finally { await f.close(failure); }
});

test('非占用错误立即抛出而不删除旧目标', async () => {
  const f = await fixture();
  let attempts = 0, failure;
  fs.renameSync = (source, destination) => {
    attempts++;
    throw renameError('ENOSPC', source, destination);
  };
  try {
    assert.throws(() => f.guard.save(), error => { failure = error; return error.code === 'ENOSPC'; });
    assert.equal(attempts, 1);
    assert.equal(fs.readFileSync(f.report, 'utf8'), f.previous);
    console.log('NETWORK_WRITER other_error code=ENOSPC attempts=1 old_target_preserved=1');
  } finally { await f.close(failure); }
});

test('EBUSY瞬态占用同样仅重试本次文件替换', async () => {
  const f = await fixture();
  let attempts = 0;
  f.guard.evidence.blocked_requests = 2;
  fs.renameSync = (source, destination) => {
    if (++attempts === 1) throw renameError('EBUSY', source, destination);
    return f.rename(source, destination);
  };
  try {
    f.guard.save();
    assert.equal(attempts, 2);
    assert.equal(JSON.parse(fs.readFileSync(f.report)).blocked_requests, 2);
  } finally { await f.close(); }
});

for (const kind of ['EACCES', 'wrong-syscall', 'wrong-destination', 'readonly', 'non-windows']) {
  test(`无法明确归入本次Windows替换的错误立即停止：${kind}`, async () => {
    const f = await fixture();
    let attempts = 0, failure;
    const platform = Object.getOwnPropertyDescriptor(process, 'platform');
    if (kind === 'readonly') fs.chmodSync(f.report, 0o444);
    if (kind === 'non-windows') Object.defineProperty(process, 'platform', { ...platform, value: 'linux' });
    fs.renameSync = (source, destination) => {
      attempts++;
      const error = renameError(kind === 'EACCES' ? 'EACCES' : 'EPERM', source, destination);
      if (kind === 'wrong-syscall') error.syscall = 'open';
      if (kind === 'wrong-destination') error.dest = destination + '.other';
      throw error;
    };
    try {
      assert.throws(() => f.guard.save(), error => { failure = error; return true; });
      assert.equal(attempts, 1);
      assert.equal(failure.reportRetryDelayMs, 0);
      assert.equal(fs.readFileSync(f.report, 'utf8'), f.previous);
    } finally {
      Object.defineProperty(process, 'platform', platform);
      if (kind === 'readonly') fs.chmodSync(f.report, 0o666);
      await f.close(failure);
    }
  });
}

test('每次写入使用独立临时路径且首次目标不存在时正常保存', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'network-writer-'));
  const report = path.join(directory, 'network.json');
  const write = createNetworkReportWriter(report), rename = fs.renameSync, sources = [];
  fs.renameSync = (source, destination) => { sources.push(source); return rename(source, destination); };
  try {
    write({ allowed_requests: 0 });
    write({ allowed_requests: 1 });
    assert.equal(new Set(sources).size, 2);
    assert.ok(sources.every(source => path.dirname(source) === directory));
    assert.deepEqual(JSON.parse(fs.readFileSync(report)), { allowed_requests: 1 });
    assert.deepEqual(fs.readdirSync(directory), ['network.json']);
  } finally {
    fs.renameSync = rename;
    assert.equal(path.dirname(path.resolve(directory)), path.resolve(os.tmpdir()));
    assert.ok(path.basename(directory).startsWith('network-writer-'));
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

for (const protocol of ['http', 'connect', 'websocket']) {
  test(`保存持续失败必须退出且不能提前转发：${protocol}`, () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'network-writer-'));
    // 子进程承受真实未捕获异常；只建立独占回环代理和目标，不启动浏览器或业务服务。
    const source = `
      const fs=require('node:fs'), http=require('node:http'), net=require('node:net'), path=require('node:path');
      const {createGuard}=require('./tests/ui/isolated_browser_guard.cjs');
      const directory=process.argv[1], protocol=process.argv[2], report=path.join(directory,'network.json');
      let guard, attempts=0, hits=0, temporary;
      process.on('exit',()=>fs.writeFileSync(path.join(directory,'audit.json'),JSON.stringify({
        attempts,hits,temporary,allowed:guard?.evidence.allowed_requests,report:JSON.parse(fs.readFileSync(report))})));
      (async()=>{
        const target=http.createServer((request,response)=>{hits++;response.end('不应转发');});
        target.on('connection',socket=>{socket.on('error',()=>{});});
        target.on('connect',()=>{hits++;}); target.on('upgrade',()=>{hits++;});
        await new Promise(resolve=>target.listen(0,'127.0.0.1',resolve));
        const targetPort=target.address().port;
        guard=createGuard('http://127.0.0.1:'+targetPort,report);
        const proxyPort=await guard.ready, originalConnect=net.Socket.prototype.connect;
        net.Socket.prototype.connect=function(...args){
          const first=Array.isArray(args[0])?args[0][0]:args[0];
          const address=first&&typeof first==='object'?first:{port:first,host:args[1]};
          if(address.host!=='127.0.0.1'||![targetPort,proxyPort].includes(Number(address.port)))throw Error('禁止其它网络目标');
          if(Number(address.port)===targetPort)hits++;
          return originalConnect.apply(this,args);
        };
        fs.renameSync=(from,to)=>{attempts++;temporary=from;throw Object.assign(Error('受控持续EPERM'),
          {code:'EPERM',syscall:'rename',path:from,dest:to});};
        const request=http.request({hostname:'127.0.0.1',port:proxyPort,agent:false,
          method:protocol==='connect'?'CONNECT':'GET',
          path:protocol==='connect'?'127.0.0.1:'+targetPort:(protocol==='websocket'?'ws':'http')+'://127.0.0.1:'+targetPort+'/',
          headers:protocol==='websocket'?{Connection:'Upgrade',Upgrade:'websocket'}:{}},response=>response.resume());
        request.on('error',()=>{});request.end();
      })().catch(error=>{console.error(error);process.exitCode=1;});
    `;
    const env = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
      ['PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC', 'PATHEXT'].includes(key.toUpperCase())));
    try {
      const result = spawnSync(process.execPath, ['-e', source, directory, protocol], {
        cwd: path.resolve(__dirname, '../..'), env, encoding: 'utf8', timeout: 5000, windowsHide: true,
      });
      assert.equal(result.error, undefined, '子进程必须自行失败退出，不能靠超时结束');
      assert.notEqual(result.status, 0);
      assert.match(result.stderr, /EPERM/);
      const audit = JSON.parse(fs.readFileSync(path.join(directory, 'audit.json')));
      assert.equal(audit.attempts, 4, 'exit不能触发第二轮文件重试');
      assert.equal(audit.hits, 0, '证据未落盘前不可打开上游连接或发送请求');
      assert.equal(audit.allowed, 1);
      assert.equal(audit.report.allowed_requests, 0, '旧目标保持最后一次有效快照');
      assert.equal(JSON.parse(fs.readFileSync(audit.temporary)).allowed_requests, 1, '失败临时原件保存本次计数');
      console.log(`NETWORK_WRITER fail_closed protocol=${protocol} child_exit=${result.status} attempts=4 upstream=0 old=0 temporary=1`);
    } finally {
      assert.equal(path.dirname(path.resolve(directory)), path.resolve(os.tmpdir()));
      assert.ok(path.basename(directory).startsWith('network-writer-'));
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });
}
