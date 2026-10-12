import '../../../tests/dsh/local_network_only.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { getEventListeners } from 'node:events';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { SessionHost } from '../sessions.mjs';
import { upstream } from '../models.mjs';

const root = resolve(fileURLToPath(new URL('../../..', import.meta.url)));
const location = resolve(process.env.DSH_UPSTREAM_ROOT || `${root}/.implementation/harness_migration/upstream`);

/** 使用真实原生工具运行时和宿主注册入口，只把私有Worker回执设为内存桩。 */
async function fixture(t, response, options = {}) {
    const [{ Context }, { default: ToolRuntime }, { default: SystemPrompt }] = await Promise.all([
        upstream(location, 'vendor/cordis'), upstream(location, 'packages/core/tools'),
        upstream(location, 'packages/core/system-prompt'),
    ]);
    const ctx = new Context();
    t.after(() => ctx.fiber.dispose());
    await ctx.plugin(SystemPrompt);
    await ctx.plugin(ToolRuntime);
    const events = [], publishAttempts = [], requests = [], controller = new AbortController();
    let executionSignal;
    const register = ctx.tools.register;
    // 仅观察真实工具体收到的融合信号，避免原生运行时移除外层转发后掩盖宿主监听泄漏。
    t.mock.method(ctx.tools, 'register', definition => register.call(ctx.tools, definition.name === 'fixture_business'
        ? { ...definition, execute(args, exec) { executionSignal = exec.signal; return definition.execute(args, exec); } }
        : definition));
    const run = { run_id: 'fixture-run', status: 'running', tools: [{ function: { name: 'fixture_business',
        description: '受控业务回执', parameters: { type: 'object', properties: {} } } }] };
    const current = { id: 'fixture-session', active: run,
        invocations: new Map([['fixture-call', { name: 'fixture_business', sessionId: 'fixture-session', seq: 42 }]]) };
    const host = new SessionHost({ on() {} }, { projectRoot: root, description: { skills: [] },
        worker: { async request(method, body) {
            requests.push({ method, body });
            if (method === 'run.cancel') return {};
            if (response instanceof Error) throw response;
            return structuredClone(response);
        } } }, { cancel() {} });
    host.publish = async (_run, event, data) => {
        publishAttempts.push(event);
        if (event === 'tool.started' && options.startedFailure) throw options.startedFailure;
        if (event === 'tool.failed' && options.failedPublishFailure) throw options.failedPublishFailure;
        if (event === 'tool.completed' && options.publishBeforeFailure) throw options.publishBeforeFailure;
        events.push({ event, data: structuredClone(data) });
        if (event === 'tool.completed' && options.publishFailure) throw options.publishFailure;
        if (event === 'tool.completed' && options.abortAfterReceipt) controller.abort(new Error('用户在回执后取消'));
        if (event === 'tool.started' && options.abortAfterStarted) controller.abort(options.abortAfterStarted);
    };
    const disposers = [];
    await host.installBusiness(current, run, { ctx }, disposers);
    t.after(() => { for (const dispose of disposers) dispose(); });
    const execute = () => ctx.tools.execute({ name: 'fixture_business', callId: 'fixture-call', arguments: {}, signal: controller.signal });
    return { execute, events, publishAttempts, requests, current, controller, signal: () => executionSignal };
}

const missingPage = { status: 'failed', error_code: 'tool_execution_error', error_summary: '以下正文页尚未写入：13',
    metadata: { presentation_progress: { draft_id: 'real-draft', missing_slide_numbers: [13], missing_page_numbers: [14] } }, artifacts: [] };

for (const [name, receipt] of [
    ['PPT缺页保留结构化进度', missingPage],
    ['未知外部操作保留不可重试标记', { status: 'failed', error_code: 'tool_result_unconfirmed', error_summary: '结果尚未核实',
        metadata: { operationOutcome: 'uncertain', retryable: false, requiresVerification: true, idempotencyKey: 'fixture-key' } }],
    ['取消回执保留原始身份', { status: 'cancelled', error_code: 'chat_cancelled', error_summary: '用户取消' }],
    ['旧isError回执保留原始字段', { isError: true, error_code: 'validation_error', error_summary: '参数错误' }],
]) {
    test(name, async t => {
        const f = await fixture(t, receipt);
        const native = await f.execute();
        assert.equal(native.isError, true, '业务失败仍应进入原生工具失败分支');
        assert.deepEqual(f.events.map(row => row.event), ['tool.started', 'tool.completed'], '不能二次发布无结构失败覆盖原业务回执');
        assert.deepEqual(f.events.at(-1).data.result, receipt);
        assert.equal(f.events.at(-1).data.status, receipt.status || 'failed');
        assert.equal(f.requests.filter(row => row.method === 'tool.execute').length, 1);
        assert.equal(f.current.invocations.size, 0);
    });
}

test('成功业务结果原样返回且只发布一次最终回执', async t => {
    const receipt = { status: 'success', data: { amount: '9007199254740993' }, artifacts: [] };
    const f = await fixture(t, receipt);
    const native = await f.execute();
    assert.equal(native.isError, false);
    assert.deepEqual(native.value, receipt);
    assert.deepEqual(f.events.map(row => row.event), ['tool.started', 'tool.completed']);
});

test('未取得Worker回执的传输错误仍发布工具失败并清理调用身份', async t => {
    const f = await fixture(t, new Error('受控Worker传输中断'));
    assert.equal((await f.execute()).isError, true);
    assert.deepEqual(f.events.map(row => row.event), ['tool.started', 'tool.failed']);
    assert.equal(f.events.at(-1).data.error, '受控Worker传输中断');
    assert.equal(f.current.invocations.size, 0);
});

test('started发布失败清理调用身份和监听，且不派发业务或伪造失败回执', async t => {
    const primary = new Error('受控开始回执发布失败');
    const f = await fixture(t, { status: 'success' }, { startedFailure: primary });
    const native = await f.execute();
    assert.equal(native.isError, true);
    assert.equal(native.error.message, primary.message);
    assert.deepEqual(f.publishAttempts, ['tool.started']);
    assert.deepEqual(f.events, []);
    assert.equal(f.requests.filter(row => row.method === 'tool.execute').length, 0);
    assert.equal(f.current.invocations.size, 0);
    assert.equal(getEventListeners(f.signal(), 'abort').length, 0);
    f.controller.abort(new Error('调用收尾后取消'));
    assert.equal(f.requests.filter(row => row.method === 'run.cancel').length, 0);
});

test('Worker主异常后失败发布再次失败仍保留原生主错误并诊断副异常', async t => {
    // 原生仅将 HarnessError 的 name/code 投影到 error.info；使用真实错误类型验证该合同。
    const { HarnessError } = await upstream(location, 'packages/llm/llm');
    class PrimaryError extends HarnessError {}
    class SecondaryError extends HarnessError {}
    const primary = new PrimaryError('受控Worker主传输失败', 'FIXTURE_WORKER_PRIMARY');
    const secondary = new SecondaryError('受控失败回执副发布失败', 'FIXTURE_PUBLISH_SECONDARY');
    const diagnostics = [];
    t.mock.method(process.stderr, 'write', chunk => { diagnostics.push(String(chunk)); return true; });
    const f = await fixture(t, primary, { failedPublishFailure: secondary });
    const native = await f.execute();
    assert.equal(native.isError, true);
    assert.equal(native.error.message, primary.message);
    assert.deepEqual(native.error.info, { name: 'PrimaryError', code: primary.code });
    assert.deepEqual(f.publishAttempts, ['tool.started', 'tool.failed']);
    assert.deepEqual(f.events.map(row => row.event), ['tool.started']);
    assert.equal(f.requests.filter(row => row.method === 'tool.execute').length, 1);
    assert.equal(f.current.invocations.size, 0);
    assert.equal(getEventListeners(f.signal(), 'abort').length, 0);
    f.controller.abort(new Error('调用收尾后取消'));
    assert.equal(f.requests.filter(row => row.method === 'run.cancel').length, 0);
    assert.equal(diagnostics.length, 1);
    assert.match(diagnostics[0], /fixture-run\/fixture-call/);
    assert.match(diagnostics[0], /SecondaryError: 受控失败回执副发布失败/);
});

test('started发布等待期间取消后不派发业务，保留原生取消错误并清理', async t => {
    const { HarnessError } = await upstream(location, 'packages/llm/llm');
    class AbortError extends HarnessError {}
    const reason = new AbortError('用户在开始回执等待期间取消', 'ABORTED');
    const f = await fixture(t, { status: 'success' }, { abortAfterStarted: reason });
    const native = await f.execute();
    assert.equal(native.isError, true);
    assert.equal(native.error.message, reason.message);
    assert.deepEqual(native.error.info, { name: 'AbortError', code: 'ABORTED' });
    assert.equal(f.requests.filter(row => row.method === 'tool.execute').length, 0);
    assert.deepEqual(f.publishAttempts, ['tool.started', 'tool.failed']);
    assert.equal(f.events.at(-1).data.error, reason.message);
    assert.equal(f.current.invocations.size, 0);
    assert.equal(getEventListeners(f.signal(), 'abort').length, 0);
    assert.equal(f.requests.filter(row => row.method === 'run.cancel').length, 0);
});

test('取得真实回执后事件发布失败不以传输错误覆盖业务事实', async t => {
    const f = await fixture(t, missingPage, { publishFailure: new Error('受控事件持久化失败') });
    assert.equal((await f.execute()).isError, true);
    assert.deepEqual(f.events.map(row => row.event), ['tool.started', 'tool.completed']);
    assert.deepEqual(f.events.at(-1).data.result, missingPage);
    assert.equal(f.current.invocations.size, 0);
});

test('回执已成功后取消等待不把已确认业务结果改成失败', async t => {
    const receipt = { status: 'success', data: { completed: true } };
    const f = await fixture(t, receipt, { abortAfterReceipt: true });
    assert.equal((await f.execute()).isError, true);
    assert.deepEqual(f.events.map(row => row.event), ['tool.started', 'tool.completed']);
    assert.deepEqual(f.events.at(-1).data.result, receipt);
    assert.equal(f.requests.filter(row => row.method === 'run.cancel').length, 1);
    assert.equal(f.current.invocations.size, 0);
});

test('提前返回回执首次发布前失败仍向原生传播，不能冒充已持久化', async t => {
    const receipt = { status: 'failed', error_code: 'tool_not_available', error_summary: '当前业务插件未提供此工具。' };
    const f = await fixture(t, receipt, { publishBeforeFailure: new Error('受控发布前失败') });
    const native = await f.execute();
    assert.equal(native.isError, true);
    assert.deepEqual(f.events.map(row => row.event), ['tool.started']);
    assert.equal(f.requests.filter(row => row.method === 'tool.execute').length, 1);
    assert.equal(f.current.invocations.size, 0);
});

for (const malformed of [null, [], '不是结构化回执']) {
    test(`异常Worker回执明确失败：${JSON.stringify(malformed)}`, async t => {
        const f = await fixture(t, malformed);
        assert.equal((await f.execute()).isError, true);
        assert.deepEqual(f.events.map(row => row.event), ['tool.started', 'tool.failed']);
        assert.equal(f.current.invocations.size, 0);
    });
}
