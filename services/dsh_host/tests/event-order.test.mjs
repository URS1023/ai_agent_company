import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { SessionHost } from '../sessions.mjs';

test('同一会话并发工具和正文按持久事件顺序发布', async () => {
    const owner = new EventEmitter();
    owner.sessions = new Map();
    owner.stateFor = SessionHost.prototype.stateFor;
    const entered = [], emitted = [];
    owner.worker = { async request(_method, params) {
        entered.push(params.event);
        if (params.event === 'tool.started') await new Promise(resolve => setTimeout(resolve, 20));
        return { id: entered.length };
    } };
    owner.on('session:session-1', entry => emitted.push(entry));
    const run = { session_id: 'session-1', conversation_id: 'conversation-1', run_id: 'native-1', request_id: 'native-1', client_request_id: 'user-request-1' };
    await Promise.all([SessionHost.prototype.publish.call(owner, run, 'tool.started'), SessionHost.prototype.publish.call(owner, run, 'message.delta', { delta: '完成' })]);
    assert.deepEqual(emitted.map(entry => [entry.id, entry.event]), [[1, 'tool.started'], [2, 'message.delta']]);
    assert.equal(emitted[0].data.request_id, 'user-request-1');
});

test('全局槽位按入队顺序分配，旧会话连续消息不会饿死新会话', async () => {
    let sequence = 0;
    const host = new SessionHost({ on() {} }, { worker: { async request() { return { id: ++sequence }; } } }, {});
    host.limit = 1;
    const started = [], releases = [];
    host.execute = async (_session, run) => { started.push(run.run_id); await new Promise(resolve => releases.push(resolve)); run.status = 'completed'; };
    const run = (id, conversation) => ({ run_id: id, request_id: id, session_id: conversation, conversation_id: conversation, content: [] });
    await host.admit(run('a1', 'a'));
    await host.admit(run('b1', 'b'));
    await host.admit(run('a2', 'a'));
    releases.shift()();
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(started, ['a1', 'b1']);
    releases.shift()();
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(started, ['a1', 'b1', 'a2']);
    releases.shift()();
    await new Promise(resolve => setImmediate(resolve));
});

test('增量后取消以Worker规范正文发布终态，刷新前后内容一致', async () => {
    const host = new EventEmitter(), published = [];
    let storedContent = '';
    host.worker = { async request(method, params) {
        assert.equal(method, 'chat.settle');
        storedContent = params.content || storedContent;
        return { status: 'cancelled', content: storedContent, deliveries: [] };
    } };
    host.publish = async (_run, event, data) => { published.push({ event, data }); };
    const run = { run_id: 'cancel-partial', session_id: 'session-partial' };
    storedContent += '停止前已经流出的片段';
    await SessionHost.prototype.settle.call(host, run, 'cancelled', '', undefined);
    assert.equal(run.result.content, storedContent);
    assert.equal(published.find(entry => entry.event === 'run.cancelled').data.content, storedContent);
});

test('编辑回执晚于取消时不能复活已移除的排队任务', { timeout: 2000 }, async () => {
    let releaseEdit;
    const host = new SessionHost({ on() {} }, { worker: { async request(method, params) {
        if (method === 'chat.queue.update') return new Promise(resolve => { releaseEdit = resolve; });
        if (method === 'chat.settle') return { status: params.status, content: '', deliveries: [] };
        return { id: 1 };
    } } }, { cancel() {} });
    host.accepting = false;
    const run = { run_id: 'queued-edit', session_id: 'session', conversation_id: 'conversation', status: 'queued',
        content: [{ type: 'text', text: '原始任务' }], model: { model: 'fixture' } };
    const current = host.stateFor(run);
    current.queue.push(run); host.runs.set(run.run_id, run);
    const editing = host.updateQueue(run.run_id, 'edit', '修改任务');
    const rejected = assert.rejects(editing, error => error.status === 409);
    const staleReply = { ...run, status: 'queued', content: [{ type: 'text', text: '修改任务' }] };
    await host.cancel(run.run_id);
    releaseEdit(staleReply);
    await rejected;
    assert.equal(run.status, 'cancelled');
    assert.deepEqual(current.queue, []);
});

test('同一排队任务的并发编辑被明确拒绝，不能提前解除执行锁', { timeout: 2000 }, async () => {
    let releaseEdit, writes = 0;
    const host = new SessionHost({ on() {} }, { worker: { async request(method) {
        if (method === 'chat.queue.update') { writes++; return new Promise(resolve => { releaseEdit = resolve; }); }
        return { id: 1 };
    } } }, {});
    host.accepting = false;
    const run = { run_id: 'queued-edit', session_id: 'session', conversation_id: 'conversation', status: 'queued',
        content: [{ type: 'text', text: '原始任务' }], model: { model: 'fixture' } };
    host.stateFor(run).queue.push(run); host.runs.set(run.run_id, run);
    const editing = host.updateQueue(run.run_id, 'edit', '第一次修改');
    await assert.rejects(host.updateQueue(run.run_id, 'edit', '第二次修改'), error => error.status === 409);
    assert.equal(writes, 1); assert.equal(run.updating, true);
    releaseEdit({ ...run, content: [{ type: 'text', text: '第一次修改' }] });
    await editing;
    assert.equal(run.updating, false);
});

test('结算 RPC 失败时仍发布失败和 done 终态，避免前端永久运行中', async () => {
    const host = new EventEmitter(), published = [], settled = [];
    host.worker = { async request(method) {
        assert.equal(method, 'chat.settle');
        throw Object.assign(new Error('模拟 Worker 断开'), { code: 'EPIPE' });
    } };
    host.publish = async (_run, event, data) => { published.push({ event, data }); };
    host.on('settled:rpc-failed', result => settled.push(result));
    const run = { run_id: 'rpc-failed', session_id: 'session-rpc-failed' };
    const result = await SessionHost.prototype.settle.call(host, run, 'completed', '已生成正文', undefined);
    assert.equal(result.status, 'failed');
    assert.deepEqual(published.map(item => item.event), ['run.failed', 'done']);
    assert.equal(published[0].data.error.code, 'DSH_SETTLE_FAILED');
    assert.equal(settled.length, 1);
});

test('结算后的单条辅助事件失败不阻断最终 done', async () => {
    const host = new EventEmitter(), published = [];
    host.worker = { async request() {
        return { status: 'completed', content: '已完成', task: { id: 'task-1', status: 'completed' }, deliveries: [] };
    } };
    host.publish = async (_run, event, data) => {
        if (event === 'task.updated') throw new Error('模拟事件写入失败');
        published.push({ event, data });
    };
    const result = await SessionHost.prototype.settle.call(host, { run_id: 'event-failed' }, 'completed', '已完成');
    assert.equal(result.status, 'completed');
    assert.deepEqual(published.map(item => item.event), ['run.completed', 'done']);
});
