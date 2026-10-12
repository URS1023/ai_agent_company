import test from 'node:test';
import assert from 'node:assert/strict';
import { SessionHost } from '../sessions.mjs';

const user = id => ({ type: 'user/message', data: { source: { kind: 'user', rpcId: id } } });
const message = text => ({ type: 'assistant/message', data: { message: { content: [{ type: 'text', text }] } } });
const start = () => ({ type: 'turn/start', data: {} });
const end = (kind = 'completed') => ({ type: 'turn/end', data: { reason: { kind } } });
const run = (id, steering = []) => ({ run_id: id, request_id: id, client_request_id: `client-${id}`,
    conversation_id: 'conversation', session_id: 'session', task: { task_id: 'same-task' },
    recovery_identity: { root_request_id: id, steering_request_ids: steering,
        other_request_ids: ['run-A', 'run-B'].filter(value => value !== id), ambiguous_request_ids: [] } });

/** 仅注入原生日志和私有 Worker 回执，任何重发 prompt/工具都会直接失败。 */
async function recover(events, runs = [run('run-A')]) {
    const results = new Map();
    const host = new SessionHost({ on() {}, sessionController: {
        async inspect() { return { events: events.map((event, seq) => ({ ...event, seq })) }; },
        async prompt() { assert.fail('恢复不得重新请求模型'); },
    } }, { worker: { async request(method) {
        assert.equal(method, 'chat.recover', '恢复不得派发工具'); return { runs };
    } } }, {});
    host.settle = async (item, status, content, error) => results.set(item.run_id, { status, content, error });
    host.queueChanged = async () => {};
    host.admit = async () => assert.fail('已有原生请求的运行不得重新入队');
    await host.recover();
    return results;
}

test('前轮未落业务结算时不能采用后轮正文，即使两个运行属于同一任务', async () => {
    const result = (await recover([user('run-A'), start(), message('仅属于A'), end(),
        user('run-B'), start(), message('仅属于B'), end()])).get('run-A');
    assert.equal(result.content, '仅属于A');
    assert.equal(result.status, 'completed');
});

test('真实原生日志先开启下一轮再写用户消息，不能把下一轮开头留给前轮', async () => {
    const result = (await recover([start(), user('run-A'), message('A完成'), end(),
        { type: 'agent/inbox/spliced', data: { target: 'next-turn', inserted: [{ source: { kind: 'user', rpcId: 'run-B' } }] } },
        start(), { type: 'step/start', data: { turn: 2, step: 1 } },
        { type: 'system/message', data: { turn: 2, step: 1 } },
        user('run-B'), message('B完成'), end()])).get('run-A');
    assert.equal(result.content, 'A完成');
    assert.equal(result.status, 'completed');
});

test('后一根请求之前已发生本轮工具工作时不能丢掉这段未完成轮次', async () => {
    const result = (await recover([start(), user('run-A'), message('A阶段一'), end(), start(),
        { type: 'tool/call', data: { name: 'current_datetime', arguments: {} } },
        user('run-B'), message('B完成'), end()])).get('run-A');
    assert.equal(result.content, 'A阶段一');
    assert.equal(result.status, 'interrupted');
});

for (const [kind, expected] of [['aborted', 'cancelled'], ['failed', 'interrupted']]) {
    test(`后轮完成不能覆盖前轮 ${kind} 终态`, async () => {
        const result = (await recover([user('run-A'), start(), message('A未完成'), end(kind),
            user('run-B'), start(), message('B完成'), end()])).get('run-A');
        assert.equal(result.status, expected);
        assert.equal(result.content, 'A未完成');
    });
}

test('后轮未完成和后台工具均不能污染前轮已完整完成的判定', async () => {
    const result = (await recover([user('run-A'), start(), message('A完成'), end(), user('run-B'), start(),
        { type: 'tool/call', data: { name: 'bash', arguments: { command: 'fixture' } } }, message('B处理中')])).get('run-A');
    assert.equal(result.status, 'completed');
    assert.equal(result.content, 'A完成');
});

test('同运行的已持久多条 steer 和多次模型轮次保留，遇到下一运行才截断', async () => {
    const a = run('run-A', ['steer-1', 'steer-2']);
    const result = (await recover([user('run-A'), start(), message('旧范围'), end(), user('steer-1'), start(),
        { type: 'compaction/end', data: {} }, user('steer-2'), message('A补充后的完整正文'), end(),
        user('run-B'), start(), message('B正文'), end()], [a])).get('run-A');
    assert.equal(result.status, 'completed');
    assert.equal(result.content, 'A补充后的完整正文');
});

test('真实先开轮后入正文顺序中，本轮 steer 和后续模型轮次不能提前截断', async () => {
    const inbox = id => ({ type: 'agent/inbox/spliced', data: { target: 'next-turn',
        inserted: [{ source: { kind: 'user', rpcId: id } }] } });
    const result = (await recover([inbox('run-A'), start(), user('run-A'), message('A阶段一'), end(),
        inbox('steer-A'), start(), user('steer-A'), message('A收到补充'), end(),
        start(), { type: 'compaction/end', data: {} }, message('A完整交付'), end(),
        inbox('run-B'), start(), user('run-B'), message('B完整交付'), end()],
        [run('run-A', ['steer-A'])])).get('run-A');
    assert.equal(result.content, 'A完整交付');
    assert.equal(result.status, 'completed');
});

test('后台通知仍在原运行窗口内，并保留现有保守中断策略', async () => {
    const result = (await recover([user('run-A'), start(),
        { type: 'tool/call', data: { name: 'subagent', arguments: { run_in_background: true } } },
        message('已交给后台'), end(), { type: 'user/message', data: { source: { kind: 'subagent-settled' } } },
        start(), message('A后台最终结果'), end(), user('run-B'), start(), message('B正文'), end()])).get('run-A');
    assert.equal(result.status, 'interrupted');
    assert.equal(result.content, 'A后台最终结果');
});

test('多个未结算运行按各自窗口恢复，恢复顺序不影响归属', async () => {
    const results = await recover([user('run-A'), start(), message('A'), end(), user('run-B'), start(), message('B'), end()],
        [run('run-B'), run('run-A')]);
    assert.equal(results.get('run-A').content, 'A');
    assert.equal(results.get('run-B').content, 'B');
});

test('未知用户请求边界保守中断，不采用未知请求的正文', async () => {
    const result = (await recover([user('run-A'), start(), message('A已存正文'), end(),
        user('unknown-request'), start(), message('不能归属的正文'), end()])).get('run-A');
    assert.equal(result.status, 'interrupted');
    assert.equal(result.content, 'A已存正文');
    assert.equal(result.error.code, 'NATIVE_REQUEST_UNCONFIRMED');
});

test('冲突的 steer 请求标识不能被当作可信本轮补充', async () => {
    const a = run('run-A', ['run-B']);
    a.recovery_identity.ambiguous_request_ids = ['run-B'];
    const result = (await recover([user('run-A'), start(), message('A'), end(),
        user('run-B'), start(), message('冲突正文'), end()], [a])).get('run-A');
    assert.equal(result.status, 'interrupted');
    assert.equal(result.content, 'A');
    assert.equal(result.error.code, 'NATIVE_REQUEST_UNCONFIRMED');
});
