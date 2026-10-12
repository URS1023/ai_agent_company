import test from 'node:test';
import assert from 'node:assert/strict';
import { SessionHost } from '../sessions.mjs';
import { recoverUnavailableRuns } from '../host.mjs';
import { ModelRoutes } from '../models.mjs';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const message = text => ({ type: 'assistant/message', data: { message: { content: [{ type: 'text', text }] } } });
const turnEnd = { type: 'turn/end', data: { reason: { kind: 'completed' } } };

async function recover(events) {
    const run = { run_id: 'run', session_id: 'session', conversation_id: 'conversation', request_id: 'request' };
    const results = [], admitted = [];
    const host = new SessionHost({ on() {}, sessionController: { async inspect() { return { events: [
        { type: 'user/message', data: { source: { rpcId: 'request' } } }, ...events,
    ] }; } } }, { worker: { async request() { return { runs: [run] }; } } }, {});
    host.settle = async (_run, status, content, error) => { results.push({ status, content, error }); };
    host.queueChanged = async () => {};
    host.admit = async value => { admitted.push(value); };
    await host.recover();
    assert.equal(admitted.length, 0, '已进入原生会话的业务不能自动重放');
    return results[0];
}

for (const [name, args] of [['subagent', {}], ['subagent_fork', { run_in_background: true }], ['workflow', { run_in_background: true }],
    ['bash', { command: 'fixture', run_in_background: false }], ['pwsh', { command: 'fixture' }]]) {
    test(`崩溃恢复不以父代理首个完成轮次代替后台 ${name} 完整结算`, async () => {
        const result = await recover([{ type: 'tool/call', data: { name, arguments: args } }, message('已交给后台处理'), turnEnd]);
        assert.equal(result.status, 'interrupted');
        assert.equal(result.error.code, 'HOST_INTERRUPTED');
    });
}

test('后台通知后的父代理结果保留，但无业务结算回执时仍标明中断', async () => {
    const result = await recover([
        { type: 'tool/call', data: { name: 'subagent', arguments: { run_in_background: true } } }, message('后台进行中'), turnEnd,
        { type: 'user/message', data: { source: { kind: 'subagent-settled', senderSessionId: 'child' } } },
        { type: 'turn/start', data: { turn: 2 } }, message('后台结果与最终分析'), turnEnd,
    ]);
    assert.equal(result.status, 'interrupted');
    assert.equal(result.content, '后台结果与最终分析');
});

test('无后台委派的完整轮次正常恢复；未结束的新轮次不冒用前一轮终态', async () => {
    assert.equal((await recover([message('已完成'), turnEnd])).status, 'completed');
    assert.equal((await recover([{ type: 'tool/call', data: { name: 'subagent', arguments: { run_in_background: false } } },
        message('前台子任务已完成'), turnEnd])).status, 'completed');
    assert.equal((await recover([message('第一轮'), turnEnd, { type: 'turn/start', data: { turn: 2 } }, message('尚未完成')])).status, 'interrupted');
});

test('关闭宿主先取消本运行后台子代理再等待收尾，不能卡住关闭', { timeout: 1000 }, async () => {
    let complete;
    const stopped = [], run = { run_id: 'run', execution: new Promise(resolve => { complete = resolve; }) };
    const child = { cancel(cause) { stopped.push(['child', cause.kind]); complete(); } };
    const foreignChild = { cancel() { assert.fail('不得停止不属于当前运行的代理'); } };
    const host = new SessionHost({ on() {}, agents: { get(id) { return id === 'child' ? child : foreignChild; } } },
        { worker: { async request() { return {}; } } }, { close() {} });
    host.sessions.set('parent', { active: run, agent: { cancel(cause) { stopped.push(['parent', cause.kind]); } } });
    host.agentRuns.set('child', 'run');
    host.agentRuns.set('foreign-child', 'other-run');
    await host.close();
    assert.deepEqual(stopped, [['parent', 'disposed'], ['child', 'parent']]);
    assert.equal(run.cancelRequested, true);
});

test('Worker 失效只收尾本宿主持有的运行，并允许重复回调', async () => {
    const stopped = [], calls = [];
    const sessions = {
        sessions: new Map([['session', {}]]),
        unavailableRunIds() { return ['run-active', 'run-queued']; },
        failSession(current, error) { stopped.push({ current, error }); },
    };
    const state = { recoverRuns: async runIds => { calls.push(runIds); return { recovered: ['run-active'] }; } };
    const error = new Error('Worker 已退出');
    const first = await recoverUnavailableRuns(sessions, state, error);
    const second = await recoverUnavailableRuns(sessions, state, error);
    assert.deepEqual(first, { run_ids: ['run-active', 'run-queued'], recovered: ['run-active'] });
    assert.deepEqual(second, first, '后端使用状态条件更新，重复收尾应得到相同的安全结果');
    assert.equal(calls.length, 2);
    assert.deepEqual(calls[0], ['run-active', 'run-queued']);
    assert.equal(stopped.length, 2);
    assert.equal(stopped[0].error, error);
});

test('SessionHost 的失效清单不包含已完成运行', () => {
    const host = new SessionHost({ on() {}, agents: { get() {} } }, { worker: { async request() { return {}; } } }, { close() {} });
    host.runs.set('active', { run_id: 'active', status: 'running' });
    host.runs.set('queued', { run_id: 'queued', status: 'created' });
    host.runs.set('done', { run_id: 'done', status: 'completed' });
    assert.deepEqual(host.unavailableRunIds(), ['active', 'queued']);
});

for (const hasExecutionTrace of [true, false]) {
    test(`原生历史缺失时按业务派发证据决定是否恢复入队：${hasExecutionTrace}`, async () => {
        const run = { run_id: 'run', session_id: 'session', conversation_id: 'conversation', request_id: 'request',
            tool_dispatches: { has_execution_trace: hasExecutionTrace, prepared: hasExecutionTrace ? 0 : 1 } };
        const settled = [], admitted = [];
        const host = new SessionHost({ on() {}, sessionController: { async inspect() { return { events: [] }; } } },
            { worker: { async request() { return { runs: [run] }; } } }, {});
        host.settle = async (_run, status, _content, error) => settled.push({ status, error });
        host.queueChanged = async () => {};
        host.admit = async value => admitted.push(value);
        await host.recover();
        assert.equal(admitted.length, hasExecutionTrace ? 0 : 1);
        assert.equal(settled.length, hasExecutionTrace ? 1 : 0);
        if (hasExecutionTrace) assert.equal(settled[0].error.code, 'NATIVE_HISTORY_MISSING');
    });
}


/** 用原生会话内存桩验证整个业务运行持有路由，不调用数据库或外部模型。 */
async function routeLifecycle(t, failConfiguration = false) {
    const cwd = await mkdtemp(join(tmpdir(), 'dsh-route-lifecycle-'));
    t.after(() => rm(cwd, { recursive: true, force: true }));
    const model = { protocol: 'openai-completions', model: 'fixture-model', baseURL: 'https://example.invalid/v1',
        apiKey: 'fixture', contextWindow: 16384, maxTokens: 512 };
    const agent = { session: { seq: 2 }, cancel() {} };
    const ctx = { on() {}, emit() {}, llm: { async resolveModelInfo() {} },
        sessionController: { async create() {}, async resolveAgent() { return { agent }; },
            async selectModel() { if (failConfiguration) throw new Error('选择模型失败'); }, async prompt() {} },
        sessions: { async flush() { return true; } } };
    const state = { keys: new Map(), worker: { async request() { return {}; } } };
    const routes = new ModelRoutes(ctx, state); routes.maxRoutes = 1;
    t.after(() => routes.close());
    const host = new SessionHost(ctx, state, routes);
    host.queueChanged = async () => {};
    host.installBusiness = async () => {};
    host.follow = async current => { current.abort = new AbortController(); };
    host.publish = async () => {};
    host.delivered = async () => {};
    const settlements = [];
    host.settle = async (_run, status) => {
        assert.equal([...routes.routes.values()][0].active, 1, '结算之前仍应持有业务路由');
        settlements.push(status);
    };
    const run = { run_id: 'fixture-run', session_id: 'fixture-session', conversation_id: 'fixture-conversation',
        workspace_id: 'fixture-workspace', model, cwd };
    const current = host.stateFor(run); current.active = run;
    return { host, routes, run, current, model, settlements };
}

test('主代理跨多轮及后台任务等待期间保留模型路由，业务结算后释放', async t => {
    const { host, routes, run, current, model, settlements } = await routeLifecycle(t);
    let entered, resume;
    const awaiting = new Promise(resolve => { entered = resolve; });
    const idle = new Promise(resolve => { resume = resolve; });
    host.nativeIdle = async () => {
        entered(); await idle;
        current.lastTurn = { seq: 2, data: { reason: { kind: 'completed' } } };
    };
    const execution = host.execute(current, run);
    await awaiting;
    await assert.rejects(routes.ensure({ ...model, model: 'other-model' }), { code: 'MODEL_ROUTE_LIMIT' });
    resume(); await execution;
    assert.deepEqual(settlements, ['completed']);
    assert.equal(run.modelLease, undefined);
    await routes.ensure({ ...model, model: 'other-model' });
    assert.equal(routes.routes.size, 1);
});

test('主代理配置失败与业务结算失败均释放已经持有的模型租约', async t => {
    const { host, routes, run, current, model } = await routeLifecycle(t, true);
    host.settle = async () => { throw new Error('结算失败'); };
    await assert.rejects(host.execute(current, run), /结算失败/);
    assert.equal(run.modelLease, undefined);
    assert.equal([...routes.routes.values()][0].active, 0);
    await routes.ensure({ ...model, model: 'other-model' });
});
