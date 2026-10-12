import test from 'node:test';
import assert from 'node:assert/strict';
import { NativeQuestions, validateAnswers } from '../interaction.mjs';

const questions = [{ id: 'month', question: '选择月份', options: [{ label: '八月' }, { label: '七月' }] }];
function fixture() {
    const events = [], hooks = new Map();
    const run = { run_id: 'run-1', session_id: 'session-1', message_id: 'assistant-1' };
    const current = { active: run }, controller = new AbortController();
    const host = { ctx: { on(name, callback) { hooks.set(name, callback); } }, sessions: new Map([['session-1', current]]),
        async publish(_run, event, data) { events.push({ event, data }); }, failSession() { assert.fail('持久化异常'); } };
    return { bridge: new NativeQuestions(host), current, controller, events, hooks, host };
}

test('原生交互不默认选择；非法回答不会结束等待，跨会话不能回答', async () => {
    const { bridge, current, controller, events } = fixture();
    const pending = bridge.ask(current, { questions, signal: controller.signal });
    const id = bridge.view('session-1')[0].question_id;
    assert.equal(events.length, 1);
    await assert.rejects(bridge.answer('other', id, { answers: [{ id: 'month', selected: ['八月'] }] }), error => error.status === 404);
    await assert.rejects(bridge.answer('session-1', id, { answers: [{ id: 'month', selected: [] }] }), error => error.status === 422);
    assert.equal(bridge.view('session-1').length, 1);
    const answer = { answers: [{ id: 'month', selected: [], custom: '2026年6月' }] };
    await bridge.answer('session-1', id, answer);
    assert.deepEqual(await pending, answer);
    assert.equal((await bridge.answer('session-1', id, answer)).duplicate, true);
    await assert.rejects(bridge.answer('session-1', id, { answers: [{ id: 'month', selected: ['七月'] }] }), error => error.status === 409);
    assert.equal(bridge.view('session-1').length, 0);
});

test('停止原生工具会撤销问题，旧回答不能复活已停止运行', async () => {
    const { bridge, current, controller, events } = fixture();
    const pending = bridge.ask(current, { questions, signal: controller.signal });
    const rejected = assert.rejects(pending, /停止/), id = bridge.view('session-1')[0].question_id;
    controller.abort(new Error('用户停止'));
    await rejected;
    assert.equal(bridge.view('session-1').length, 0);
    assert.equal(events.at(-1).data.status, 'cancelled');
    await assert.rejects(bridge.answer('session-1', id, { answers: [{ id: 'month', selected: ['八月'] }] }), error => error.status === 404);
});

test('单选、多选和自定义输入保持原生回答合同', () => {
    assert.throws(() => validateAnswers(questions, { answers: [{ id: 'month', selected: ['八月', '七月'] }] }));
    assert.throws(() => validateAnswers(questions, { answers: [{ id: 'month', selected: ['不存在'] }] }));
    assert.throws(() => validateAnswers(questions, { answers: [{ id: 'month', selected: ['八月', '八月'] }] }));
    assert.deepEqual(validateAnswers([{ ...questions[0], multiSelect: true }], { answers: [{ id: 'month', selected: ['八月', '七月'] }] }),
        { answers: [{ id: 'month', selected: ['八月', '七月'] }] });
});

test('回答持久化期间取消优先，迟到回执不能继续原生工具', async () => {
    const { bridge, current, controller, events, host } = fixture();
    let release;
    const saved = host.publish;
    host.publish = async (...args) => {
        await saved(...args);
        if (args[2].status === 'answered') await new Promise(resolve => { release = resolve; });
    };
    const pending = bridge.ask(current, { questions, signal: controller.signal });
    const id = bridge.view('session-1')[0].question_id;
    const answering = bridge.answer('session-1', id, { answers: [{ id: 'month', selected: ['八月'] }] });
    const rejectedQuestion = assert.rejects(pending, /停止|取消/);
    const rejectedAnswer = assert.rejects(answering, error => error.status === 409);
    await new Promise(resolve => setImmediate(resolve));
    current.active.cancelRequested = true;
    controller.abort(new Error('用户停止'));
    release();
    await Promise.all([rejectedQuestion, rejectedAnswer]);
    assert.equal(bridge.view('session-1').length, 0);
    assert.equal(bridge.answered.has(id), false);
    assert.equal(events.at(-1).data.status, 'cancelled');
});

test('回答持久化期间不接受并发的第二个回答', async () => {
    const { bridge, current, controller, host } = fixture();
    let release;
    const saved = host.publish;
    host.publish = async (...args) => {
        await saved(...args);
        if (args[2].status === 'answered') await new Promise(resolve => { release = resolve; });
    };
    const pending = bridge.ask(current, { questions, signal: controller.signal });
    const id = bridge.view('session-1')[0].question_id;
    const answer = { answers: [{ id: 'month', selected: ['八月'] }] };
    const answering = bridge.answer('session-1', id, answer);
    await assert.rejects(bridge.answer('session-1', id, answer), error => error.status === 409);
    release();
    await answering;
    assert.deepEqual(await pending, answer);
});
