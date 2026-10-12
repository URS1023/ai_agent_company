// 本夹具所有网络连接均禁止；仅使用匿名 stdio 管道与临时 SQLite 文件。
import net from 'node:net';
net.Socket.prototype.connect = function () { throw new Error('持久回执专项禁止网络'); };
import assert from 'node:assert/strict';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { BusinessWorker } from '../../services/dsh_host/worker.mjs';
import { SessionHost } from '../../services/dsh_host/sessions.mjs';
import { upstream } from '../../services/dsh_host/models.mjs';

const root = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const [python, database] = process.argv.slice(2);
const environment = Object.fromEntries(['SystemRoot', 'WINDIR', 'TEMP', 'TMP', 'PATH'].filter(k => process.env[k]).map(k => [k, process.env[k]]));
const create = () => new BusinessWorker({ command: python, python, projectRoot: root,
    args: [resolve(root, 'tests/dsh/receipt_worker_fixture.py'), database], env: { ...environment, PYTHONUTF8: '1' } });
async function closeAndObserveExit(instance) {
    const child = instance.child;
    const exited = child.exitCode !== null || child.signalCode !== null ? Promise.resolve()
        : new Promise(resolve => child.once('exit', resolve));
    let timer;
    try {
        await instance.close();
        await Promise.race([exited, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('旧 Worker 未确认退出')), 5000); })]);
    } finally { clearTimeout(timer); }
}
let worker = create();
const timeout = setTimeout(() => {
    worker.fail(new Error('持久回执 Node 专项总预算 90 秒'));
    worker.child.kill();
}, 90000);
let ctx;
try {
    const run = await worker.request('fixture.prepare', { mode: 'reply' }, { timeoutMs: 30000 });
    const description = await worker.request('system.describe');
    assert.equal(description.receipt_protocol, 1);
    const location = resolve(root, '.implementation/harness_migration/upstream');
    const [{ Context }, { default: ToolRuntime }, { default: SystemPrompt }] = await Promise.all([
        upstream(location, 'vendor/cordis'), upstream(location, 'packages/core/tools'), upstream(location, 'packages/core/system-prompt'),
    ]);
    ctx = new Context();
    await ctx.plugin(SystemPrompt); await ctx.plugin(ToolRuntime);
    const calls = [], entries = [];
    const originalRequest = worker.request.bind(worker);
    worker.request = (method, params, options) => { calls.push({ method, params }); return originalRequest(method, params, options); };
    const host = new SessionHost({ on() {} }, { projectRoot: root, description: { receipt_protocol: 1, skills: [] }, worker }, { cancel() {} });
    const current = host.stateFor(run);
    current.active = run; current.invocations.set('ack-clock', { name: 'current_datetime', sessionId: current.id, seq: 37 });
    let sessionFailures = 0, runFailures = 0;
    host.on(`session:${run.session_id}`, entry => {
        if (entry.event === 'tool.completed') { sessionFailures++; throw new Error('受控会话订阅抛错'); }
    });
    host.on(`run:${run.run_id}`, entry => entries.push(entry));
    host.on(`run:${run.run_id}`, entry => {
        if (entry.event === 'tool.completed') { runFailures++; throw new Error('受控运行订阅抛错'); }
    });
    // installBusiness 的运行对象必须就是 current.active，确保真实宿主归属检查实际通过。
    const selected = { ...run, tools: run.tools.filter(row => (row.function || row).name === 'current_datetime') };
    current.active = selected;
    await host.installBusiness(current, selected, { ctx }, []);
    const native = await ctx.tools.execute({ name: 'current_datetime', callId: 'ack-clock', arguments: {}, signal: new AbortController().signal });
    assert.equal(native.isError, false);
    const completed = entries.filter(entry => entry.event === 'tool.completed');
    assert.equal(completed.length, 1);
    assert.equal(sessionFailures, 1); assert.equal(runFailures, 1);
    assert.equal(calls.filter(row => row.method === 'tool.execute').length, 1);
    assert.equal(calls.filter(row => row.method === 'tool.receipt.get').length, 1);
    assert.equal(calls.filter(row => row.method === 'chat.event' && row.params.event === 'tool.completed').length, 0);
    assert.equal(worker.pending.size, 0);
    assert.equal(current.invocations.size, 0);
    const stored = await worker.request('fixture.inspect', { run_id: run.run_id });
    assert.equal(stored.fault.dropped, 1);
    assert.equal(stored.fault.execute, 1);
    assert.equal(stored.calls.length, 1);
    assert.equal(stored.calls[0].status, 'completed');
    assert.deepEqual(stored.calls[0].result, native.value);
    assert.equal(stored.events.filter(row => row.payload.receipt).length, 1);
    const params = calls.find(row => row.method === 'tool.execute').params;
    const { durable_receipt, ...lookup } = params;
    await closeAndObserveExit(worker);
    worker = create();
    const recovered = await worker.request('tool.receipt.get', lookup, { timeoutMs: 30000 });
    assert.equal(recovered.state, 'committed');
    assert.equal(recovered.receipt.id, completed[0].id);
    assert.deepEqual(recovered.result, native.value);
    const reopened = await worker.request('fixture.inspect', { run_id: run.run_id });
    assert.equal(reopened.fault.execute, 0);
    assert.equal(reopened.calls.length, 1);
    console.log(JSON.stringify({ proof: 'real_worker_host_lost_reply_and_cold_lookup', dropped: 1, providerCalls: 1,
        lookupCalls: 2, modelRequests: 0, networkRequests: 0, eventId: completed[0].id, pending: worker.pending.size }));
} finally {
    if (ctx) await ctx.fiber.dispose();
    await closeAndObserveExit(worker);
    clearTimeout(timeout);
}
