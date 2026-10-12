// 传输竞速单测使用内存管道；真实 Python Worker 丢包另由 durable_receipt_probe 覆盖。
import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { BusinessWorker } from '../worker.mjs';
import { SessionHost } from '../sessions.mjs';

function fixture() {
    const worker = Object.create(BusinessWorker.prototype);
    EventEmitter.call(worker);
    Object.assign(worker, { pending: new Map(), handlers: new Map(), sequence: 0, closed: false });
    const sent = [];
    worker.send = packet => sent.push(packet);
    const params = { run_id: 'r', call_id: 'c', invocation_seq: 7, name: 'clock', arguments: {},
        native_agent_session_id: 's', durable_receipt: true };
    const receipt = { receipt_protocol: 1, state: 'committed', receipt: { id: 1 }, result: { status: 'success' } };
    const notice = { method: 'tool.receipt.committed', params: { identity: { run_id: 'r', call_id: 'c',
        invocation_seq: 7, native_agent_session_id: 's' } } };
    const receive = packet => worker.receive(JSON.stringify(packet));
    return { worker, sent, params, receipt, notice, receive };
}

test('原回复先到则提交通知不增加lookup或等待者', async () => {
    const f = fixture();
    const request = f.worker.request('tool.execute', f.params);
    assert.equal(f.worker.pending.get('host-1').timer, undefined);
    await f.receive({ id: 'host-1', result: f.receipt });
    await f.receive(f.notice);
    assert.deepEqual(await request, f.receipt);
    assert.equal(f.sent.length, 1);
    assert.equal(f.worker.pending.size, 0);
});

test('通知启动一次lookup后原回复赢得竞速，清除lookup等待者及迟到包', async () => {
    const f = fixture();
    const request = f.worker.request('tool.execute', f.params);
    await f.receive(f.notice); await f.receive(f.notice);
    assert.equal(f.sent.filter(row => row.method === 'tool.receipt.get').length, 1);
    await f.receive({ id: 'host-1', result: f.receipt });
    await f.receive({ id: 'host-2', result: f.receipt });
    assert.deepEqual(await request, f.receipt);
    assert.equal(f.worker.pending.size, 0);
});

test('丢reply但收到通知时由一次只读lookup恢复', async () => {
    const f = fixture();
    const request = f.worker.request('tool.execute', f.params);
    await f.receive(f.notice);
    await f.receive({ id: 'host-2', result: f.receipt });
    assert.deepEqual(await request, f.receipt);
    assert.equal(f.sent.filter(row => row.method === 'tool.execute').length, 1);
    assert.equal(f.worker.pending.size, 0);
});

for (const state of ['unknown', 'absent', 'access_denied', 'legacy']) {
    test(`lookup ${state}不能恢复成成功或重发工具`, async () => {
        const f = fixture();
        const request = f.worker.request('tool.execute', f.params);
        const rejected = assert.rejects(request, error => error.code === 'DSH_RECEIPT_UNCONFIRMED' && error.receiptState === state);
        await f.receive({ id: 'host-1', error: { message: '受控回复错误' } });
        await f.receive(f.notice);
        await f.receive({ id: 'host-2', result: { receipt_protocol: 1, state } });
        await rejected;
        assert.equal(f.sent.filter(row => row.method === 'tool.execute').length, 1);
        assert.equal(f.sent.filter(row => row.method === 'tool.receipt.get').length, 1);
        assert.equal(f.worker.pending.size, 0);
    });
}

test('不同代理的提交通知不能唤醒本调用', async () => {
    const f = fixture();
    const request = f.worker.request('tool.execute', f.params);
    await f.receive({ ...f.notice, params: { identity: { ...f.notice.params.identity, native_agent_session_id: 'other' } } });
    assert.equal(f.sent.length, 1);
    await f.receive({ id: 'host-1', result: f.receipt });
    await request;
});

test('双信号均丢失不伪造自动恢复，连接关闭清理等待者', async () => {
    const f = fixture();
    const request = f.worker.request('tool.execute', f.params);
    const rejected = assert.rejects(request, /受控关闭/);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(f.sent.length, 1);
    assert.equal(f.worker.pending.get('host-1').timer, undefined);
    f.worker.fail(new Error('受控关闭'));
    await rejected;
    assert.equal(f.worker.pending.size, 0);
});

test('回执未知停止owned后台任务并封住started和模型授权的异步窗口', async () => {
    const hooks = new Map(), agentHooks = new Map(), definitions = new Map(), requests = [], stopped = [];
    const agents = new Map(['root', 'child'].map(id => [id, { id, cancel() { stopped.push(id); } }]));
    const jobs = [{ id: 'root-job', owner: 'root', status: 'running' }, { id: 'child-job', owner: 'child', status: 'running' }];
    const killed = [];
    const hostContext = {
        on(name, hook) { hooks.set(name, hook); return () => {}; },
        get() { return { list: owner => jobs.filter(job => job.owner === owner), kill: id => { killed.push(id); } }; },
        agents: { get: id => agents.get(id), list: () => [...agents.values()], isOwnedBy: (child, parent) => child === 'child' && parent.id === 'root' },
    };
    let authorize, releaseStarted;
    const pendingAuthorization = new Promise(resolve => { authorize = resolve; });
    const pendingStarted = new Promise(resolve => { releaseStarted = resolve; });
    const worker = { async request(method, params) {
        requests.push({ method, params });
        if (method === 'tool.execute') return { receipt_protocol: 1, state: 'unknown' };
        if (method === 'model.authorize') return pendingAuthorization;
        return {};
    } };
    const host = new SessionHost(hostContext, { description: { receipt_protocol: 1, skills: [] }, worker }, { cancel() {} });
    const run = { run_id: 'r', session_id: 'root', conversation_id: 'c', message_id: 'm', status: 'running',
        tools: [{ name: 'fixture', parameters: { type: 'object', properties: {} } }] };
    const current = host.stateFor(run);
    current.active = run; current.agent = agents.get('root'); current.abort = new AbortController();
    current.invocations.set('first', { name: 'fixture', sessionId: 'root', seq: 1 });
    current.invocations.set('later', { name: 'fixture', sessionId: 'child', seq: 2 });
    host.agentRuns.set('child', 'r');
    host.publish = async (_run, event, data) => data.call_id === 'later' && event === 'tool.started' ? pendingStarted : {};
    const ctx = { on(name, hook) { agentHooks.set(name, hook); return () => {}; },
        systemPrompt: { section() { return () => {}; } }, tools: { register(definition) { definitions.set(definition.name, definition); return () => {}; } } };
    await host.installBusiness(current, run, { ctx }, []);
    const definition = definitions.get('fixture');
    const later = definition.execute({}, { callId: 'later', signal: new AbortController().signal });
    const laterRejected = assert.rejects(later, /业务运行已停止/);
    let modelDispatches = 0;
    const modelHook = hooks.get('llm/stream');
    const next = async function* () { modelDispatches++; yield {}; };
    const stream = modelHook({ sessionId: 'child', signal: new AbortController().signal }, next);
    const modelRejected = assert.rejects(stream.next(), /业务运行已停止/);
    await assert.rejects(definition.execute({}, { callId: 'first', signal: new AbortController().signal }), /持久回执/);
    releaseStarted(); authorize({ allowed: true, model_call_id: 'reserved' });
    await Promise.all([laterRejected, modelRejected]);
    assert.equal(requests.filter(row => row.method === 'tool.execute').length, 1);
    assert.equal(modelDispatches, 0);
    assert.equal(requests.find(row => row.method === 'model.settle').params.status, 'failed');
    assert.ok(stopped.includes('root') && stopped.includes('child'));
    assert.ok(killed.includes('root-job') && killed.includes('child-job'));
    await assert.rejects(modelHook({ sessionId: 'child' }, next).next(), /运行已结束/);
    assert.equal(requests.filter(row => row.method === 'model.authorize').length, 1);
    await assert.rejects(agentHooks.get('tools/execute')({ name: 'present', arguments: { files: [{ path: '/api/artifacts/a/download' }] },
        signal: new AbortController().signal }, () => {}), /运行已结束/);
    assert.equal(requests.filter(row => row.method === 'delivery.reference').length, 0);
    let settled;
    host.queueChanged = async () => {};
    host.configure = async () => agents.get('root');
    host.settle = async (_run, status) => { settled = status; };
    await host.execute(current, run);
    assert.equal(settled, 'interrupted');
});
