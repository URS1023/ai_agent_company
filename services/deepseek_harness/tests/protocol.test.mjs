import test from 'node:test';
import assert from 'node:assert/strict';
import { PassThrough } from 'node:stream';
import { JsonLineRpc } from '../protocol.mjs';

test('初始化之前排队命令，工具回调与取消可以独立双向传输', async () => {
    const input = new PassThrough();
    const output = [];
    const rpc = new JsonLineRpc(input, line => output.push(JSON.parse(line)));
    const send = message => input.write(`${JSON.stringify({ jsonrpc: '2.0', ...message })}\n`);
    send({ id: 1, method: 'initialize', params: {} });
    send({ id: 2, method: 'session.cancel', params: { sessionId: 'a' } });
    assert.equal((await rpc.first).method, 'initialize');
    assert.equal(output.length, 0);
    rpc.handle(async (method, params) => ({ method, sessionId: params.sessionId }));
    rpc.activate();
    const tool = rpc.request('tool.invoke', { callId: 'tool1' });
    const request = output.find(value => value.method === 'tool.invoke');
    send({ id: request.id, result: { status: 'success', data: 5 } });
    assert.equal((await tool).data, 5);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(output.find(value => value.id === 2).result.method, 'session.cancel');
    input.end();
});

test('管道关闭后结果不明的工具请求失败，不伪造成功回执', async () => {
    const input = new PassThrough();
    const rpc = new JsonLineRpc(input, () => {});
    input.write(`${JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'initialize' })}\n`);
    await rpc.first;
    const request = rpc.request('tool.invoke', { callId: 'unknown' });
    input.end();
    await assert.rejects(request, /操作结果需核实/);
});
