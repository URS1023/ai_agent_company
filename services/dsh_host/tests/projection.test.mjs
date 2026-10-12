import test from 'node:test';
import assert from 'node:assert/strict';
import { NativeProjection, usageView } from '../projection.mjs';

test('重试撤销增量并以已提交原生消息替换', () => {
    const view = new NativeProjection();
    view.accept({ type: 'snapshot', cursor: 2 });
    view.accept({ type: 'assistant-stream', frame: { type: 'start', attemptId: 'a' } });
    assert.equal(view.accept({ type: 'assistant-stream', frame: { type: 'chunk', attemptId: 'a', index: 0, chunk: { type: 'text-delta', text: '错误片段' } } })[0][1].delta, '错误片段');
    assert.equal(view.accept({ type: 'assistant-stream', frame: { type: 'end', attemptId: 'a', outcome: { kind: 'abandoned' } } })[0][1].content, '');
    assert.equal(view.accept({ type: 'event', event: { seq: 3, type: 'assistant/message', data: { message: { content: [{ type: 'text', text: '真实答案' }] } } } })[0][1].content, '真实答案');
    assert.deepEqual(view.accept({ type: 'event', event: { seq: 3 } }), []);
});
test('流序号缺口不能被静默拼接', () => {
    const view = new NativeProjection();
    view.accept({ type: 'assistant-stream', frame: { type: 'start', attemptId: 'a' } });
    assert.throws(() => view.accept({ type: 'assistant-stream', frame: { type: 'chunk', attemptId: 'a', index: 1, chunk: { type: 'text-delta', text: '缺前段' } } }), /序号缺口/);
});
test('缓存输入参与总输入量', () => {
    assert.deepEqual(usageView({ inputTokens: 10, cacheReadTokens: 20, cacheWriteTokens: 3, outputTokens: 5 }), {
        prompt_tokens: 33, completion_tokens: 5, total_tokens: 38, prompt_tokens_details: { cached_tokens: 20 } });
});
test('压缩失败不能投影成压缩成功', () => {
    const view = new NativeProjection();
    const result = view.accept({ type: 'event', event: { seq: 1, type: 'compaction/end', data: { error: { message: '摘要超限' } } } });
    assert.equal(result[0][0], 'task.progress');
    assert.equal(result[0][1].status, 'failed');
});
