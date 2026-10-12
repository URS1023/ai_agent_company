import '../../../tests/dsh/local_network_only.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { ModelRoutes, upstream } from '../models.mjs';

const root = resolve(fileURLToPath(new URL('../../..', import.meta.url)));
const upstreamRoot = resolve(process.env.DSH_UPSTREAM_ROOT || `${root}/.implementation/harness_migration/upstream`);
const textOf = message => typeof message?.content === 'string' ? message.content
    : (message?.content || []).filter(block => block.type === 'text').map(block => block.text).join('');

/** 真实原生运行时和适配器只访问本机模型桩；业务授权结算用内存替身，无数据库。 */
async function fixture(t, protocol) {
    const requests = [], nativeRequests = [], settlements = [];
    const server = createServer(async (request, response) => {
        let raw = ''; for await (const chunk of request) raw += chunk;
        const body = JSON.parse(raw); requests.push(body);
        const corrected = body.messages.some(message => message.role === 'assistant');
        const content = JSON.stringify({ query: corrected ? 'SELECT name FROM devices WHERE isDeleted=0'
            : 'SELECT name FROM devices WHERE isDeleted=0 OR isDeleted IS NULL' });
        response.writeHead(200, { 'content-type': 'text/event-stream' });
        if (request.url.endsWith('/messages')) {
            for (const event of [
                { type: 'message_start', message: { id: `stub-${requests.length}`, model: 'fixture-model', usage: { input_tokens: 8, output_tokens: 1 } } },
                { type: 'content_block_start', index: 0, content_block: { type: 'text', text: '' } },
                { type: 'content_block_delta', index: 0, delta: { type: 'text_delta', text: content } },
                { type: 'content_block_stop', index: 0 },
                { type: 'message_delta', delta: { stop_reason: 'end_turn' }, usage: { output_tokens: 9 } },
                { type: 'message_stop' },
            ]) response.write(`event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`);
            response.end();
            return;
        }
        const chunk = { id: `stub-${requests.length}`, object: 'chat.completion.chunk', created: 1,
            model: 'fixture-model', choices: [{ index: 0, delta: { content }, finish_reason: null }] };
        response.write(`data: ${JSON.stringify(chunk)}\n\n`);
        response.write(`data: ${JSON.stringify({ ...chunk, choices: [{ index: 0, delta: {}, finish_reason: 'stop' }],
            usage: { prompt_tokens: 8, completion_tokens: 9, total_tokens: 17 } })}\n\n`);
        response.end('data: [DONE]\n\n');
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); });
    const { Context } = await upstream(upstreamRoot, 'vendor/cordis');
    const native = await upstream(upstreamRoot, 'packages/llm/llm');
    const ctx = new Context();
    t.after(() => ctx.fiber.dispose());
    await ctx.plugin(native.LlmRuntime);
    const state = { upstreamRoot, keys: new Map(), worker: { request: async (method, params) => {
        if (method === 'model.authorize') return { allowed: true, run_id: params.run_id,
            model_call_id: `authorization-${settlements.length + 1}` };
        assert.equal(method, 'model.settle'); settlements.push(params); return {};
    } } };
    ctx.provide('launchEnvironment', { get: () => undefined, getFrom: () => undefined });
    ctx.provide('credentials', {
        resolve: async name => state.keys.has(String(name)) ? { value: state.keys.get(String(name)), source: 'fixture' } : undefined,
        readRecord: async () => undefined, listRecords: async () => [],
    });
    ctx.on('llm/stream', async function* (options, next) { nativeRequests.push(options); yield* next(); });
    const routes = new ModelRoutes(ctx, state);
    t.after(() => routes.close());
    await routes.initialize();
    const params = { run_id: 'fixture-run', workspace_id: 'fixture-workspace', profile: 'chat',
        request_timeout: 5, response_format: { type: 'json_object' },
        model: { protocol, model: 'fixture-model', baseURL: `http://127.0.0.1:${server.address().port}/v1`,
            apiKey: 'fixture-only-key', contextWindow: 16384, maxTokens: 512, thinking: 'disabled' } };
    return { routes, params, requests, nativeRequests, settlements, state };
}

for (const protocol of ['openai-completions', 'deepseek-messages']) {
    test(`辅助模型二次修正保留 assistant 历史并完成原生请求：${protocol}`, { timeout: 20000 }, async t => {
        const { routes, params, requests, nativeRequests, settlements } = await fixture(t, protocol);
        const messages = [{ role: 'system', content: '只输出 SQL JSON。' }, { role: 'user', content: '查询未删除设备名称。' }];
        const first = await routes.generate({ ...params, messages }, 'first-request');
        assert.match(JSON.parse(first.content).query, /OR isDeleted IS NULL/);
        const correctedMessages = [...messages, { role: 'assistant', content: first.content },
            { role: 'user', content: '语义校验失败，未删除只允许 isDeleted=0，请修正。' }];
        const before = JSON.stringify(correctedMessages);
        const second = await routes.generate({ ...params, messages: correctedMessages }, 'second-request');
        assert.equal(JSON.parse(second.content).query, 'SELECT name FROM devices WHERE isDeleted=0');
        assert.equal(requests.length, 2, '第二次请求必须实际到达原生适配器后的本机 HTTP 桩');
        assert.equal(textOf(requests[1].messages.find(message => message.role === 'assistant')), first.content);
        assert.match(textOf(requests[1].messages.at(-1)), /语义校验失败/);
        const history = nativeRequests[1].messages.find(message => message.role === 'assistant');
        assert.equal(history.source.kind, 'model');
        assert.equal(history.source.model, 'fixture-model');
        assert.equal(history.source.provider, nativeRequests[1].provider);
        assert.equal(history.source.replayState, undefined);
        assert.ok(nativeRequests[1].messages.every(message => message.id && message.source));
        assert.equal(JSON.stringify(correctedMessages), before, '转换不能改写 Worker 传入的历史');
        assert.deepEqual(settlements.map(item => item.status), ['completed', 'completed']);
        assert.equal(routes.pending.size, 0);
        assert.equal(routes.requests.size, 0);
    });
}

test('模型配置切换达到路由上限时淘汰空闲路由并释放凭据', { timeout: 20000 }, async t => {
    const { routes, params, state } = await fixture(t, 'openai-completions');
    routes.maxRoutes = 1;
    await routes.generate({ ...params, messages: [{ role: 'user', content: '第一条' }] }, 'route-first');
    const secondModel = { ...params.model, model: 'fixture-model-second' };
    await routes.generate({ ...params, model: secondModel, messages: [{ role: 'user', content: '第二条' }] }, 'route-second');
    assert.equal(routes.routes.size, 1);
    assert.equal(routes.pending.size, 0);
    assert.equal(routes.requests.size, 0);
    assert.equal(state.keys.size, 1, '被淘汰的路由 API key 不应继续留在宿主缓存');
});

test('辅助工具历史保留调用参数与原生结果关联ID', { timeout: 20000 }, async t => {
    const { routes, params, requests, nativeRequests } = await fixture(t, 'openai-completions');
    await routes.generate({ ...params, messages: [
        { role: 'user', content: '使用已查出的设备名称回答。' },
        { role: 'assistant', content: null, tool_calls: [{ id: 'call-fixture-1', type: 'function',
            function: { name: 'lookup_device', arguments: { device_id: 'fixture-device' } } }] },
        { role: 'tool', tool_call_id: 'call-fixture-1', content: '{"name":"fixture-device-name"}' },
        { role: 'user', content: '确认该工具结果。' },
    ] }, 'tool-history');
    assert.equal(requests.length, 1);
    const tool = nativeRequests[0].messages.find(message => message.role === 'tool');
    assert.equal(tool.toolCallId, 'call-fixture-1');
    assert.equal(tool.source.callId, 'call-fixture-1');
    const wireAssistant = requests[0].messages.find(message => message.role === 'assistant');
    const wireTool = requests[0].messages.find(message => message.role === 'tool');
    assert.equal(wireAssistant.tool_calls[0].id, wireTool.tool_call_id);
    assert.equal(wireAssistant.tool_calls[0].function.name, 'lookup_device');
    assert.deepEqual(JSON.parse(wireAssistant.tool_calls[0].function.arguments), { device_id: 'fixture-device' });
    assert.equal(wireTool.content, '{"name":"fixture-device-name"}');
});


test('辅助调用等待业务授权时持有路由，结束后才允许其他模型淘汰', { timeout: 20000 }, async t => {
    const { routes, params, requests, state } = await fixture(t, 'openai-completions');
    routes.maxRoutes = 1;
    let entered, resume;
    const awaiting = new Promise(resolve => { entered = resolve; });
    const authorized = new Promise(resolve => { resume = resolve; });
    const original = state.worker.request;
    state.worker.request = async (method, value) => {
        if (method === 'model.authorize') { entered(); await authorized; }
        return original(method, value);
    };
    const running = routes.generate({ ...params, messages: [{ role: 'user', content: '查询设备' }] }, 'authorize-wait');
    await awaiting;
    await assert.rejects(routes.ensure({ ...params.model, model: 'other-model' }), { code: 'MODEL_ROUTE_LIMIT' });
    resume();
    await running;
    assert.equal(requests.length, 1);
    await routes.ensure({ ...params.model, model: 'other-model' });
    assert.equal(routes.routes.size, 1);
    assert.equal(routes.pending.size, 0);
    assert.equal(routes.requests.size, 0);
});

for (const stage of ['content', 'authorize', 'settle']) {
    test(`辅助调用在 ${stage} 失败也释放租约和请求记录`, { timeout: 20000 }, async t => {
        const { routes, params, state } = await fixture(t, 'openai-completions');
        routes.maxRoutes = 1;
        const original = state.worker.request;
        state.worker.request = async (method, value) => {
            if (method === `model.${stage}`) throw new Error('离线失败样例');
            return original(method, value);
        };
        const content = stage === 'content' ? [{ type: 'unsupported' }] : '查询设备';
        await assert.rejects(routes.generate({ ...params, messages: [{ role: 'user', content }] }, `failure-${stage}`));
        assert.equal(routes.pending.size, 0);
        assert.equal(routes.requests.size, 0);
        assert.equal([...routes.routes.values()][0].active, 0);
        await routes.ensure({ ...params.model, model: 'other-model' });
    });
}

test('等待授权时取消辅助调用不再发送模型请求且释放路由', { timeout: 20000 }, async t => {
    const { routes, params, requests, settlements, state } = await fixture(t, 'openai-completions');
    let entered, resume;
    const awaiting = new Promise(resolve => { entered = resolve; });
    const authorized = new Promise(resolve => { resume = resolve; });
    const original = state.worker.request;
    state.worker.request = async (method, value) => {
        if (method === 'model.authorize') { entered(); await authorized; }
        return original(method, value);
    };
    const running = routes.generate({ ...params, messages: [{ role: 'user', content: '查询设备' }] }, 'cancel-authorize');
    const rejected = assert.rejects(running, /用户停止/);
    await awaiting;
    routes.cancel(params.run_id);
    resume(); await rejected;
    assert.equal(requests.length, 0);
    assert.equal(settlements[0].status, 'failed');
    assert.equal([...routes.routes.values()][0].active, 0);
    assert.equal(routes.pending.size, 0);
    assert.equal(routes.requests.size, 0);
});
