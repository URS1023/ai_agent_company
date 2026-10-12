// 验证当前 DSH 的真实声明与工具调用链；模型和 Worker 均为明确的合成桩。
import './local_network_only.mjs';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import net from 'node:net';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { SessionHost } from '../../services/dsh_host/sessions.mjs';
import { upstream } from '../../services/dsh_host/models.mjs';

const root = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const location = resolve(root, '.implementation/harness_migration/upstream');
let input = '';
for await (const chunk of process.stdin) input += chunk;
const fixture = JSON.parse(input);
assert.ok(['openai-completions', 'deepseek-messages'].includes(fixture.protocol));
assert.equal(fixture.tool.function.name, 'write_presentation_slides');
const tool = fixture.tool.function;
const requests = [], workerRequests = [], events = [], disposers = [];
const controller = new AbortController();
let ctx, timer, connections = 0, handlerError;

/** 每个协议仅响应一次合成工具调用，使用真实适配器支持的流式消息结构。 */
const server = createServer(async (request, response) => {
    try {
        let raw = '';
        for await (const chunk of request) raw += chunk;
        requests.push(JSON.parse(raw));
        assert.equal(requests.length, 1, '每个协议仅允许一个本机模型请求');
        const expectedPath = fixture.protocol === 'deepseek-messages' ? '/v1/messages' : '/v1/chat/completions';
        assert.equal(request.url, expectedPath);
        response.writeHead(200, { 'content-type': 'text/event-stream' });
        const argumentsJson = JSON.stringify(fixture.arguments);
        if (fixture.protocol === 'deepseek-messages') {
            for (const event of [
                { type: 'message_start', message: { id: 'synthetic-message', model: 'synthetic-model', usage: { input_tokens: 1, output_tokens: 0 } } },
                { type: 'content_block_start', index: 0, content_block: { type: 'tool_use', id: 'synthetic-call', name: tool.name, input: {} } },
                { type: 'content_block_delta', index: 0, delta: { type: 'input_json_delta', partial_json: argumentsJson } },
                { type: 'content_block_stop', index: 0 },
                { type: 'message_delta', delta: { stop_reason: 'tool_use' }, usage: { output_tokens: 1 } },
                { type: 'message_stop' },
            ]) response.write(`event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`);
            response.end();
        } else {
            const chunk = { id: 'synthetic-message', object: 'chat.completion.chunk', created: 1, model: 'synthetic-model' };
            response.write(`data: ${JSON.stringify({ ...chunk, choices: [{ index: 0, delta: { role: 'assistant', tool_calls: [{ index: 0,
                id: 'synthetic-call', type: 'function', function: { name: tool.name, arguments: argumentsJson } }] }, finish_reason: null }] })}\n\n`);
            response.write(`data: ${JSON.stringify({ ...chunk, choices: [{ index: 0, delta: {}, finish_reason: 'tool_calls' }],
                usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } })}\n\n`);
            response.end('data: [DONE]\n\n');
        }
    } catch (error) {
        handlerError = error;
        controller.abort(error);
        response.destroy(error);
    }
});

try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const port = server.address().port;
    const guardedConnect = net.Socket.prototype.connect;
    // 除公共禁外网守卫外，再限制到本轮服务器的唯一地址和连接预算。
    net.Socket.prototype.connect = function (...args) {
        const first = Array.isArray(args[0]) ? args[0][0] : args[0];
        const target = first && typeof first === 'object' ? first : { port: first, host: args[1] };
        assert.equal(target.host, '127.0.0.1', '不得连接本轮服务器以外的主机');
        assert.equal(Number(target.port), port, '不得连接本轮服务器以外的端口');
        assert.equal(++connections, 1, '模型适配器不得重试或新增连接');
        return guardedConnect.apply(this, args);
    };
    const [{ Context }, { default: ToolRuntime }, prompt, native] = await Promise.all([
        upstream(location, 'vendor/cordis'), upstream(location, 'packages/core/tools'),
        upstream(location, 'packages/core/system-prompt'), upstream(location, 'packages/llm/llm'),
    ]);
    ctx = new Context();
    await ctx.plugin(prompt.default);
    await ctx.plugin(ToolRuntime);
    await ctx.plugin(native.LlmRuntime);
    const run = { run_id: 'synthetic-run', status: 'running', tools: [fixture.tool] };
    const current = { id: 'synthetic-session', active: run, invocations: new Map() };
    const host = new SessionHost({ on() {} }, { projectRoot: root, description: { skills: [] }, worker: {
        async request(method, body) {
            assert.equal(method, 'tool.execute', '本探针只允许合成工具回执');
            workerRequests.push(structuredClone(body));
            return { status: 'success', data: { schema_probe: true }, artifacts: [] };
        },
    } }, { cancel() { controller.abort(); } });
    host.publish = async (_run, event) => events.push(event);
    await host.installBusiness(current, run, { ctx }, disposers);
    const registered = ctx.tools.schemas().find(entry => entry.name === tool.name);
    const assembly = await ctx.systemPrompt.assemble();
    const assembled = assembly.tools.find(entry => entry.name === tool.name);
    assert.ok(registered && assembled, '真实 Host 注册结果必须进入原生声明组装');
    const provider = 'synthetic-presentation-provider';
    const baseURL = `http://127.0.0.1:${port}/v1`;
    const retryPolicy = { mode: 'normal', maxRetries: 0 };
    // 明确提供路由与密钥，不读取用户模型设置或实际环境凭据。
    ctx.provide('launchEnvironment', { get: () => undefined, getFrom: () => undefined });
    ctx.provide('credentials', {
        resolve: async name => name === 'SYNTHETIC_KEY' ? { value: 'synthetic-only-key', source: 'fixture' } : undefined,
        readRecord: async () => undefined, listRecords: async () => [],
    });
    if (fixture.protocol === 'openai-completions') {
        const pi = await upstream(location, 'packages/llm/llm-pi-ai');
        pi.apply(ctx, { providers: { get: () => ({ [provider]: { apiKeyEnv: 'SYNTHETIC_KEY', api: fixture.protocol,
            baseURL, retryPolicy, compat: { maxTokensField: 'max_tokens' }, models: [{ id: 'synthetic-model',
                name: 'synthetic-model', contextWindow: 16384, maxTokens: 4096, input: ['text'], reasoningEfforts: false }] } }) } });
    } else {
        const deepseek = await upstream(location, 'packages/llm/llm-deepseek');
        const options = deepseek.resolveAdapterOptions({ baseURL, apiKeyEnv: 'SYNTHETIC_KEY', thinking: 'disabled',
            maxTokens: 4096, defaultContextWindow: 16384, retryPolicy,
            models: [{ id: 'synthetic-model', contextWindow: 16384, maxTokens: 4096, inputModalities: ['text'] }] });
        const adapter = new deepseek.DeepSeekAdapter({ options: () => options, resolveApiKey: async () => 'synthetic-only-key',
            resolveAttachments: () => undefined, resolveUserId: () => 'synthetic-workspace',
            resolveImageAccess: () => { throw new Error('合成文本合同不应读取图片'); },
            prepareExtensions: () => Promise.resolve({ fields: {}, accept: async () => {} }) });
        disposers.push(ctx.llm.registerAdapter([provider], adapter));
    }
    await ctx.llm.resolveModelInfo(provider, 'synthetic-model');
    const assembler = new native.BlockAssembler();
    // agent-loop 也由 systemPrompt.assemble 获取 tools 后交给此公开流式运行时。
    timer = setTimeout(() => controller.abort(new Error('本机模型单请求超过 5 秒')), 5000);
    for await (const chunk of ctx.llm.stream({ provider, model: 'synthetic-model',
        messages: [native.createUserMessage({ content: [{ type: 'text', text: '返回合成正文工具调用。' }] })],
        system: prompt.renderPrompt(assembly), tools: assembly.tools, signal: controller.signal, maxTokens: 4096,
    })) assembler.push(chunk);
    clearTimeout(timer);
    if (handlerError) throw handlerError;
    assert.ok(!['error', 'aborted'].includes(assembler.finish.kind), JSON.stringify(assembler.finish));
    const calls = assembler.blocks().filter(block => block.type === 'tool-call');
    assert.equal(calls.length, 1);
    const call = calls[0];
    assert.equal(call.name, tool.name);
    current.invocations.set(call.id, { name: call.name, sessionId: current.id, seq: 1 });
    const result = await ctx.tools.execute({ name: call.name, callId: call.id, arguments: JSON.parse(call.arguments), signal: controller.signal });
    assert.equal(result.isError, false, JSON.stringify(result));
    assert.equal(result.value.data.schema_probe, true);
    assert.equal(requests.length, 1);
    assert.equal(workerRequests.length, 1);
    assert.equal(workerRequests[0].name, tool.name);
    const declared = requests[0].tools.find(entry => (entry.function || entry).name === tool.name);
    const wire = fixture.protocol === 'openai-completions' ? declared.function
        : { name: declared.name, description: declared.description, parameters: declared.input_schema };
    process.stdout.write(JSON.stringify({ registered, assembled, wire, worker_arguments: workerRequests[0].arguments,
        model_requests: requests.length, connections, worker_calls: workerRequests.length, events,
        invocations_remaining: current.invocations.size }));
} finally {
    clearTimeout(timer);
    for (const dispose of disposers.reverse()) dispose();
    if (ctx) await ctx.fiber.dispose();
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
}
