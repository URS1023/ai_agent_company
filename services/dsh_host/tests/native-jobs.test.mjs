import test from 'node:test';
import assert from 'node:assert/strict';
import { SessionHost } from '../sessions.mjs';

const tick = () => new Promise(resolve => setImmediate(resolve));

function fixture(rows) {
    const listeners = new Set(), kills = [];
    const jobs = {
        list(owner) { return rows.filter(job => !job.owner || job.owner === owner).map(job => ({ ...job })); },
        wait() { assert.fail('宿主不能使用会抑制原生完成通知的 jobs.wait'); },
        read() { assert.fail('宿主不能消费模型的后台结果'); },
        events: { subscribe(_filter, listener) { listeners.add(listener); return () => listeners.delete(listener); } },
        finish(id, status = 'completed') {
            const job = rows.find(item => item.id === id);
            job.status = status;
            for (const listener of [...listeners]) listener({ type: 'settled', job, cause: 'producer', awaited: false });
        },
        kill(id, owner) {
            const job = rows.find(item => item.id === id);
            assert.equal(job.owner, owner);
            kills.push(id); this.finish(id, 'killed');
        },
    };
    const agent = { status: 'idle', async whenIdle() {}, cancel() { this.status = 'idle'; } };
    const children = new Map();
    const host = new SessionHost({ on() {}, get(name) { assert.equal(name, 'jobs'); return jobs; }, agents: { get(id) { return children.get(id); } } },
        { worker: { async request() { return { id: 1 }; } } }, { cancel() {}, close() {} });
    const run = { run_id: 'run', session_id: 'root', conversation_id: 'conversation', status: 'running' };
    const current = host.stateFor(run);
    current.active = run; current.agent = agent;
    host.runs.set(run.run_id, run);
    return { host, current, run, agent, jobs, listeners, kills, children };
}

test('父代理空闲仍等待所属Jobs，并保留原生通知唤醒后的后续轮次', async () => {
    const { host, current, run, agent, jobs, listeners } = fixture([{ id: 'workflow-1', owner: 'root', status: 'running' }]);
    let idle = false, releaseFollowup, notices = 0;
    const followup = new Promise(resolve => { releaseFollowup = resolve; });
    const detachNativeNotice = jobs.events.subscribe({ owners: 'scope' }, event => {
        if (event.awaited) return;
        notices++; agent.status = 'running'; agent.whenIdle = () => followup;
    });
    const observed = host.nativeIdle(current, run).then(() => { idle = true; });
    await tick();
    assert.equal(idle, false, '后台工作流没有子代理也不能提前结算');
    jobs.finish('workflow-1');
    await tick();
    assert.equal(notices, 1);
    assert.equal(idle, false, '必须等待原生完成通知触发的最终回答');
    agent.status = 'idle'; releaseFollowup();
    await observed;
    detachNativeNotice();
    assert.equal(listeners.size, 0, '完成后释放观察订阅');
});

test('停止当前运行只取消父子所属Jobs，不触碰别的会话和无主Jobs', async () => {
    const rows = [
        { id: 'parent-job', owner: 'root', status: 'running' },
        { id: 'child-job', owner: 'child', status: 'running' },
        { id: 'foreign-job', owner: 'foreign', status: 'running' },
        { id: 'unowned-job', status: 'running' },
    ];
    const { host, current, run, kills, children } = fixture(rows);
    host.agentRuns.set('child', 'run'); host.agentRuns.set('foreign', 'other-run');
    children.set('child', { cancel() { host.agentRuns.delete('child'); } });
    children.set('foreign', { cancel() { assert.fail('不能取消其他会话'); } });
    run.execution = host.nativeIdle(current, run).then(() => { run.status = 'cancelled'; });
    await host.cancel(run.run_id);
    assert.deepEqual(kills, ['parent-job', 'child-job']);
    assert.equal(rows[2].status, 'running'); assert.equal(rows[3].status, 'running');
    assert.equal(run.status, 'cancelled');
});

test('关闭宿主会停止原生后台命令，等待观察完成并清理订阅', async () => {
    const { host, current, run, kills, listeners } = fixture([{ id: 'pwsh-job', owner: 'root', status: 'running' }]);
    run.execution = host.nativeIdle(current, run);
    await tick();
    await host.close();
    assert.deepEqual(kills, ['pwsh-job']);
    assert.equal(listeners.size, 0);
    assert.equal(run.cancelRequested, true);
});

test('完成事件丢失时通过轮询发现后台任务已结束', async () => {
    const { host, current, run, jobs, listeners } = fixture([{ id: 'silent-job', owner: 'root', status: 'running' }]);
    const observing = host.nativeIdle(current, run);
    await tick();
    // 模拟事件总线丢包：只改变任务快照，不发送 settled 通知。
    jobs.list = owner => [{ id: 'silent-job', owner, status: 'completed' }];
    await observing;
    assert.equal(listeners.size, 0, '轮询完成后必须释放事件订阅');
});

test('宿主关闭信号会解除后台任务观察等待', async () => {
    const { host, current, run } = fixture([{ id: 'never-ending', owner: 'root', status: 'running' }]);
    current.abort = new AbortController();
    const observing = host.nativeIdle(current, run);
    await tick();
    current.abort.abort();
    await assert.rejects(observing, error => error.code === 'DSH_JOB_OBSERVE_ABORTED' || error.code === 'DSH_HOST_ABORTED');
});
