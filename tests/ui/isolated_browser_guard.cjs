/* 完整业务验收的浏览器代理：只转发本轮宿主及测试进程自己创建的回环桩。 */
'use strict';

const fs = require('node:fs');
const http = require('node:http');
const net = require('node:net');
const { createNetworkReportWriter } = require('./network_report_writer.cjs');

function loopback(host) { return host === '127.0.0.1' || host === '::1' || host === '[::1]'; }
function addressKey(host, port) { return `${host.replace(/^\[|\]$/g, '')}|${Number(port)}`; }

const BACKGROUND_HOSTS = new Set(['edge.microsoft.com', 'cn.bing.com', 'www.bing.com', 'arc.msn.com', 'edge-consumer-static.azureedge.net', 'go.microsoft.com']);
// 20261004-084542-6b78129f 第3轮仅观察到此HTTPS origin；它仍被代理拒绝，不能泛化到其它协议、端口或子域。
const BACKGROUND_ORIGINS = new Set(['https://telem-edge.smartscreen.microsoft.com']);

function createGuard(base, reportPath) {
  const baseUrl = new URL(base);
  const basePort = Number(baseUrl.port || 80);
  if (baseUrl.protocol !== 'http:' || !loopback(baseUrl.hostname) || !basePort || [8011, 8012].includes(basePort)) {
    throw new Error('浏览器验收必须使用本轮隔离宿主的回环随机端口');
  }
  const endpoints = new Map([[addressKey(baseUrl.hostname, basePort), 1]]);
  const evidence = { allowed_requests: 0, blocked_requests: 0, blocked_origins: [],
    unexpected_requests: 0, application_blocked_requests: 0, application_blocked_origins: [],
    application_blocked_by_target_type: {},
    client_disconnects: 0, upstream_errors: 0, transport_error_codes: {},
    background_candidate_requests: 0, background_candidate_origins: [],
    background_classification: '仅列已观测浏览器背景域；页面或Worker发起同域请求仍记为应用阻断，所有类别均禁止转发',
    boundary: '本轮隔离宿主及测试进程创建的回环桩；禁止在用8011/8012与外网' };
  const sockets = new Set();
  let proxy;
  let proxyPort;
  const originalListen = net.Server.prototype.listen;
  // 自动识别当前套件自己的 HTTP/嵌入/检测桩，不能因为地址是回环就放行所有本机服务。
  net.Server.prototype.listen = function (...args) {
    let registered;
    this.once('listening', () => {
      const address = this.address();
      if (this !== proxy && typeof address === 'object' && loopback(address?.address)) {
        registered = addressKey(address.address, address.port);
        endpoints.set(registered, (endpoints.get(registered) || 0) + 1);
      }
    });
    this.once('close', () => {
      if (registered) {
        const remaining = (endpoints.get(registered) || 1) - 1;
        if (remaining) endpoints.set(registered, remaining); else endpoints.delete(registered);
      }
    });
    return originalListen.apply(this, args);
  };
  const writeReport = createNetworkReportWriter(reportPath);
  const save = () => writeReport(evidence);
  const transportError = (kind, error) => {
    evidence[kind]++;
    const code = `${kind}:${error?.code || 'UNKNOWN'}`;
    evidence.transport_error_codes[code] = (evidence.transport_error_codes[code] || 0) + 1;
    save();
  };
  const inspect = raw => {
    let target;
    try { target = new URL(raw); } catch {}
    const port = Number(target?.port || (['https:', 'wss:'].includes(target?.protocol) ? 443 : 80));
    const allowed = target && ['http:', 'https:', 'ws:', 'wss:'].includes(target.protocol)
      && !target.username && !target.password && loopback(target.hostname)
      && ![8011, 8012, proxyPort].includes(port) && endpoints.has(addressKey(target.hostname, port));
    return { target, port, allowed };
  };
  const addOrigin = (list, origin) => { if (list.length < 30 && !list.includes(origin)) list.push(origin); };
  const observeApplicationRequest = (raw, targetType = 'target') => {
    const { target, allowed } = inspect(raw);
    if (!target || !['http:', 'https:', 'ws:', 'wss:'].includes(target.protocol) || allowed) return;
    evidence.application_blocked_requests++;
    evidence.application_blocked_by_target_type[targetType] = (evidence.application_blocked_by_target_type[targetType] || 0) + 1;
    addOrigin(evidence.application_blocked_origins, target.origin);
    save();
  };
  const authorize = raw => {
    const { target, port, allowed } = inspect(raw);
    if (!allowed) {
      evidence.blocked_requests++;
      // 只存 origin，不落查询参数、请求头、业务内容或访问令牌。
      const origin = target?.origin || 'invalid-target';
      addOrigin(evidence.blocked_origins, origin);
      if (BACKGROUND_HOSTS.has(target?.hostname) || BACKGROUND_ORIGINS.has(target?.origin)) {
        evidence.background_candidate_requests++;
        addOrigin(evidence.background_candidate_origins, origin);
      } else evidence.unexpected_requests++;
      save();
      return null;
    }
    evidence.allowed_requests++;
    save();
    return { target, port, host: target.hostname.replace(/^\[|\]$/g, '') };
  };
  proxy = http.createServer((request, response) => {
    // 下游主动断开只回收连接；业务上游的发送/接收失败另记失败证据，不能混同吞掉。
    request.on('error', () => response.destroy());
    response.on('error', () => response.destroy());
    const allowed = authorize(request.url);
    if (!allowed || allowed.target.protocol !== 'http:') { response.writeHead(403); response.end('隔离验收禁止此目标'); return; }
    const headers = { ...request.headers };
    delete headers['proxy-authorization']; delete headers['proxy-connection'];
    let clientClosed = false, recordedFailure = false;
    const upstreamFailure = error => {
      if (!clientClosed && !recordedFailure) { recordedFailure = true; transportError('upstream_errors', error); }
    };
    const upstream = http.request({ hostname: allowed.host, port: allowed.port, method: request.method,
      path: allowed.target.pathname + allowed.target.search, headers, agent: false }, received => {
      received.on('error', error => { upstreamFailure(error); response.destroy(); });
      response.writeHead(received.statusCode, received.headers); received.pipe(response);
    });
    upstream.on('error', error => {
      upstreamFailure(error);
      if (!response.destroyed) { if (!response.headersSent) response.writeHead(502); response.end(); }
    });
    request.on('aborted', () => { clientClosed = true; upstream.destroy(); });
    response.on('close', () => { if (!response.writableFinished) clientClosed = true; upstream.destroy(); });
    request.pipe(upstream);
  });
  // HTTPS 与 WebSocket 均经过相同目标校验，弹窗和 Worker 也不能绕过页面级拦截。
  function tunnel(request, client, head, connect) {
    const allowed = authorize(connect ? 'https://' + request.url : request.url);
    if (!allowed) { client.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n'); return; }
    let clientClosed = false;
    const upstream = net.connect({ host: allowed.host, port: allowed.port }, () => {
      if (connect) client.write('HTTP/1.1 200 Connection Established\r\n\r\n');
      else {
        const headers = Object.entries(request.headers).filter(([key]) => !key.startsWith('proxy-'))
          .map(([key, value]) => `${key}: ${value}`).join('\r\n');
        upstream.write(`${request.method} ${allowed.target.pathname + allowed.target.search} HTTP/1.1\r\n${headers}\r\n\r\n`);
      }
      if (head.length) upstream.write(head);
      upstream.pipe(client); client.pipe(upstream);
    });
    upstream.on('error', error => { if (!clientClosed) transportError('upstream_errors', error); client.destroy(); });
    client.on('close', () => { clientClosed = true; upstream.destroy(); });
  }
  proxy.on('connect', (request, socket, head) => tunnel(request, socket, head, true));
  proxy.on('upgrade', (request, socket, head) => tunnel(request, socket, head, false));
  proxy.on('connection', socket => {
    sockets.add(socket);
    // 拒绝 CONNECT 后 Edge 常主动 RST；必须监听被升级/接管的原始 Socket，不能让它终止 Node。
    socket.on('error', error => { transportError('client_disconnects', error); socket.destroy(); });
    socket.on('close', () => sockets.delete(socket));
  });
  const ready = new Promise((resolve, reject) => {
    proxy.once('error', reject);
    proxy.listen(0, '127.0.0.1', () => { proxyPort = proxy.address().port; proxy.unref(); resolve(proxyPort); });
  });
  process.on('exit', save);
  save();
  return { ready, evidence, save, observeApplicationRequest, async close() {
    try { save(); }
    finally {
      // 落盘失败仍向调用方抛出，同时完成代理清理，不能遗留监听器或回环连接。
      process.removeListener('exit', save); net.Server.prototype.listen = originalListen;
      for (const socket of sockets) socket.destroy();
      await new Promise(resolve => proxy.close(resolve));
    }
  } };
}

if (process.env.TEST5_E2E_NETWORK_REPORT) {
  if (process.env.TEST5_E2E_ALLOW_WRITE !== 'isolated') throw new Error('浏览器隔离代理只允许专用验收入口使用');
  if (process.env.TEST5_E2E_PROCESS_GATE) {
    // 父进程将本轮 Node 纳入专属进程容器后才放行；之前不能创建 Edge 子进程。
    const deadline = Date.now() + 30000;
    const pause = new Int32Array(new SharedArrayBuffer(4));
    while (!fs.existsSync(process.env.TEST5_E2E_PROCESS_GATE)) {
      if (Date.now() >= deadline) throw new Error('未取得本轮进程隔离启动许可');
      Atomics.wait(pause, 0, 0, 20);
    }
  }
  const guard = createGuard(process.env.TEST5_E2E_BASE_URL, process.env.TEST5_E2E_NETWORK_REPORT);
  const puppeteer = require('puppeteer-core');
  const launch = puppeteer.launch.bind(puppeteer);
  const guardedLaunch = async options => {
    const port = await guard.ready;
    const args = (options?.args || []).filter(value => !/^--proxy-|^--no-proxy-server/.test(value));
    args.push(`--proxy-server=http://127.0.0.1:${port}`, '--proxy-bypass-list=<-loopback>',
      '--disable-quic', '--disable-background-networking', '--dns-prefetch-disable',
      '--force-webrtc-ip-handling-policy=disable_non_proxied_udp');
    const browser = await launch({ ...options, args });
    const root = await browser.target().createCDPSession();
    const connection = root.connection();
    const sessions = new WeakMap();
    const observerError = error => {
      // 浏览器/目标已关闭时不把正常销毁计作网络失败，其它安装失败必须进入门禁。
      if (browser.connected && !String(error).match(/closed|detached|No target/i)) {
        guard.evidence.observer_errors = (guard.evidence.observer_errors || 0) + 1;
        guard.save();
      }
    };
    const instrument = session => {
      if (sessions.has(session)) return sessions.get(session);
      const send = session.send.bind(session);
      const state = { type: null, ready: null, enable(type) {
        if (type) this.type = type;
        if (!this.ready) this.ready = (async () => {
          if (['browser', 'tab'].includes(this.type)) return;
          if (!this.type) throw new Error('缺少新目标类型，网络观察器不能确认已安装');
          await send('Network.enable');
        })().catch(observerError);
        return this.ready;
      } };
      sessions.set(session, state);
      session.on('Network.requestWillBeSent', event => guard.observeApplicationRequest(event.request.url, state.type || 'unknown'));
      session.on('Network.webSocketCreated', event => guard.observeApplicationRequest(event.url, state.type || 'unknown'));
      session.on('Target.attachedToTarget', attached);
      // Puppeteer 已用 waitForDebuggerOnStart 暂停新页、弹窗与 Worker；必须先装好网络监听再恢复。
      // 下一微任务读取同一 attachedToTarget 消息中的类型，避免元信息监听顺序造成竞态。
      session.send = (method, ...parameters) => method === 'Runtime.runIfWaitingForDebugger'
        ? Promise.resolve().then(() => state.enable()).then(() => send(method, ...parameters))
        : send(method, ...parameters);
      return state;
    };
    function attached(event) {
      const session = connection.session(event.sessionId);
      if (!session) { observerError(new Error('新目标没有可用网络观察会话')); return; }
      instrument(session).type = event.targetInfo.type;
      guard.observeApplicationRequest(event.targetInfo.url);
    }
    connection.on('sessionattached', instrument);
    connection.on('Target.attachedToTarget', attached);
    instrument(root).type = 'browser';
    browser.on('targetchanged', target => guard.observeApplicationRequest(target.url()));
    // 启动前已存在的空白页也先完成安装；测试脚本拿到 browser 时所有现有页面均已就绪。
    await Promise.all(browser.targets().filter(target => target.type() !== 'browser').map(async target => {
      guard.observeApplicationRequest(target.url());
      try { await instrument(await target.createCDPSession()).enable(target.type()); }
      catch (error) { observerError(error); }
    }));
    return browser;
  };
  // Node 24 可将该依赖作为只读 ESM 命名空间返回；为测试脚本提供包装，不写它的导出对象。
  const Module = require('node:module');
  const load = Module._load;
  const defaultExport = Object.create(puppeteer.default || puppeteer);
  Object.defineProperty(defaultExport, 'launch', { value: guardedLaunch });
  const wrapped = { ...puppeteer, default: defaultExport, launch: guardedLaunch };
  Module._load = function (request, ...args) {
    return request === 'puppeteer-core' ? wrapped : load.call(this, request, ...args);
  };
}

module.exports = { createGuard };
