import { createInterface } from 'node:readline';
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';

const path = join(process.env.DSH_HOME, 'fake-worker.json');
const database = existsSync(path) ? JSON.parse(readFileSync(path, 'utf8')) : { runs: [], messages: [], events: [], next: 0 };
const save = () => writeFileSync(path, JSON.stringify(database));
const body = request => JSON.parse(Buffer.from(request.body_base64 || '', 'base64').toString() || '{}');
const identity = (conversation = 'one') => ({ conversation_id: conversation, session_id: `company-test-${conversation}`, workspace_id: conversation, user_id: 'test' });
const reader = createInterface({ input: process.stdin });
const pending = new Map();
const askHost = (method, params) => new Promise((resolve, reject) => {
    const id = `worker-${++database.next}`; pending.set(id, { resolve, reject });
    process.stdout.write(`${JSON.stringify({ jsonrpc: '2.0', id, method, params })}\n`);
});
async function handle(method, params) {
    const run = database.runs.find(item => item.run_id === params.run_id);
    switch (method) {
        case 'system.describe': return { protocol_version: 2, plugins: [], skills: [], home_root: process.env.DSH_HOME };
        case 'system.initialize': return { ready: true };
        case 'system.activate': return { ready: true };
        case 'chat.recover': return { runs: database.runs.filter(item => ['running', 'queued'].includes(item.status)) };
        case 'chat.prepare': {
            const input = body(params.request);
            const conversation = params.request.path.split('/')[4];
            const duplicate = database.runs.find(item => item.request_id === input.request_id);
            if (duplicate) return { ...duplicate, duplicate: true };
            const item = { ...identity(conversation), run_id: `run-${++database.next}`, request_id: input.request_id || `request-${database.next}`,
                message_id: `assistant-${database.next}`, user_message_id: `user-${database.next}`, status: 'queued', duplicate: false,
                model: { protocol: input.protocol || 'openai-completions', baseURL: process.env.FAKE_MODEL_URL, model: 'fake-model', apiKey: `key-${conversation}`,
                    contextWindow: 32768, maxTokens: 4096, ...(input.thinking === 'disabled' ? { thinking: 'disabled', reasoningEfforts: false,
                        ...(input.protocol === 'deepseek-messages' ? { reasoningEffort: 'off' } : {}) } : {}) },
                system_prompt: `你服务于工作区 ${conversation}，当前目标：${input.content}`, tools: [{ name: 'company_echo', description: '返回真实业务回执',
                    parameters: { type: 'object', properties: { value: { type: 'string' } }, required: ['value'] } },
                    { name: 'search_knowledge', description: '测试知识技能可用性', parameters: { type: 'object', properties: {} } }],
                content: [{ type: 'text', text: input.content || input.message || 'hello' }], cwd: join(process.env.DSH_HOME, `workspace-${conversation}`),
                home: process.env.DSH_HOME, task: { id: `task-${database.next}`, status: 'queued' }, resources: {}, mode: 'standard', delivery_mode: 'queue' };
            if (conversation === 'legacy') item.history_seed = { source: 'legacy-platform', messages: [
                { role: 'user', content: 'LEGACY_UNIQUE_USER', status: 'completed' }, { role: 'assistant', content: 'LEGACY_UNIQUE_ANSWER', status: 'completed' }] };
            if (conversation === 'legacy-large') item.history_seed = { source: 'legacy-platform', messages: [
                { role: 'user', content: '完整历史'.repeat(15000), status: 'completed' }] };
            if (input.image) {
                item.model.inputModalities = ['text', 'image'];
                item.content.push({ type: 'image', mediaType: 'image/png', data: process.env.FAKE_IMAGE_BASE64, name: 'fixture.png' });
            }
            database.runs.push(item);
            database.messages.push({ id: `user-${database.next}`, role: 'user', content: input.content, run_id: item.run_id, conversation_id: conversation });
            database.messages.push({ id: item.message_id, role: 'assistant', content: '', run_id: item.run_id, conversation_id: conversation });
            save(); return item;
        }
        case 'chat.authorize': return { ...identity(run?.conversation_id || params.conversation_id || 'one'),
            run_id: run?.run_id, run_status: run?.status };
        case 'chat.steer': {
            const input = body(params.request);
            const previous = database.messages.find(item => item.request_id === input.request_id);
            const message = previous || { id: `user-${++database.next}`, role: 'user', content: input.content,
                request_id: input.request_id, run_id: run.run_id, conversation_id: run.conversation_id };
            if (!previous) database.messages.push(message);
            save(); return { run_id: run.run_id, request_id: input.request_id, user_message: message,
                content: [{ type: 'text', text: input.content }], duplicate: !!previous };
        }
        case 'chat.event': {
            const event = { id: ++database.next, event: params.event, data: params.data };
            database.events.push(event);
            if (params.event === 'run.started') run.status = 'running';
            const message = database.messages.find(item => item.id === run.message_id);
            if (params.event === 'message.replace') message.content = params.data.content;
            if (params.event === 'message.delta') message.content += params.data.delta;
            save(); return { id: event.id, sequence: event.id, session_id: run.session_id, run_id: run.run_id };
        }
        case 'chat.settle': {
            run.status = params.status;
            database.messages.find(item => item.id === run.message_id).content = params.content;
            save(); return { status: run.status, message_id: run.message_id, task: { id: run.task.id, status: run.status }, deliveries: run.deliveries || [] };
        }
        case 'chat.history': {
            // 回放用独立小窗口复现旧运行被截断；普通详情保留全部夹具供集成断言使用。
            const replayLimit = params.after ? Number(process.env.FAKE_STREAM_HISTORY_MAX_RUNS || Infinity) : Infinity;
            const own = database.runs.filter(item => item.conversation_id === params.conversation_id).slice(-replayLimit);
            const ids = new Set(own.map(item => item.run_id));
            const events = database.events.filter(item => ids.has(item.data.run_id));
            return { id: params.conversation_id, ...identity(params.conversation_id), title: '测试会话',
                messages: database.messages.filter(item => ids.has(item.run_id)), last_run: own.at(-1),
                runs: own.map(item => ({ id: item.run_id, run_id: item.run_id, status: item.status })),
                events: events.filter(item => item.id > (params.after || 0)), cursor: events.at(-1)?.id || 0,
                projections: { queue: [], tasks: own.map(item => ({ id: item.task.id, status: item.status })), deliveries: own.flatMap(item => item.deliveries || []), pending_questions: [] } };
        }
        case 'chat.queue.update': {
            run.content = [{ type: 'text', text: params.content }, ...run.content.filter(block => block.type !== 'text')];
            run.system_prompt = `你服务于工作区 ${run.conversation_id}，当前目标：${params.content}`;
            run.task.title = params.content;
            database.messages.find(message => message.id === run.user_message_id).content = params.content;
            save(); return run;
        }
        case 'tool.execute': {
            await askHost('business.event', { run_id: run.run_id, event: 'task.progress', data: { message: '正在执行本机业务操作' } });
            run.deliveries = [{ id: `delivery-${run.run_id}`, task_id: run.task.id, source_run_id: run.run_id, name: '测试回执.txt' }];
            const result = { status: 'success', content: params.arguments.value === 'LARGE_RESULT'
                ? 'BEGIN_SOURCE_' + 'row-value '.repeat(15000) + '_END_SOURCE' : params.arguments.value, artifacts: run.deliveries };
            run.raw_tool_result = result;
            save(); return result;
        }
        case 'model.authorize': return { allowed: true, model_call_id: `model-${++database.next}`, run_id: run?.run_id };
        case 'model.settle': return { settled: true };
        case 'run.cancel': return { cancelled: true };
        case 'api.dispatch': {
            let result;
            if (params.request.path === '/api/test/plugin') {
                const prepared = await handle('chat.prepare', { request: { path: '/api/chat/conversations/plugin/send', body_base64: Buffer.from('{"content":"plugin native execution","request_id":"plugin-request"}').toString('base64') } });
                result = await askHost('chat.execute', { prepared });
            } else if (params.request.path === '/api/test/vision') {
                result = await askHost('model.generate', { workspace_id: 'vision', profile: 'vision', capability: 'vision',
                    model: { protocol: 'openai-completions', baseURL: process.env.FAKE_MODEL_URL, model: 'fake-model', apiKey: 'key-vision', contextWindow: 32768, maxTokens: 1024,
                        thinking: 'disabled', reasoningEffort: 'off', reasoningEfforts: false },
                    messages: [{ role: 'user', content: [{ type: 'text', text: 'vision image' }, { type: 'image_url', image_url: {
                        url: 'data:image/png;base64,' + process.env.FAKE_IMAGE_BASE64, detail: 'high' } }] }] });
            } else result = params.request.path === '/api/test/aux' ? await askHost('model.generate', { workspace_id: 'auxiliary', profile: 'reasoning',
                model: { protocol: 'openai-completions', baseURL: process.env.FAKE_MODEL_URL, model: 'fake-model', apiKey: 'key-auxiliary', contextWindow: 32768, maxTokens: 2048 },
                messages: [{ role: 'user', content: 'json auxiliary' }], response_format: { type: 'json_object' }, request_timeout: 5 }) : { ok: true };
            return { status: 200, headers: [['content-type', 'application/json']], body_base64: Buffer.from(JSON.stringify(result)).toString('base64') };
        }
        case 'shutdown': setTimeout(() => process.exit(0), 50); return { stopped: true };
        default: throw new Error(`未知测试RPC ${method}`);
    }
}
reader.on('line', async line => {
    const message = JSON.parse(line);
    if (!message.method) {
        const completion = pending.get(message.id); pending.delete(message.id);
        if (message.error) completion?.reject(new Error(message.error.message)); else completion?.resolve(message.result);
        return;
    }
    try { process.stdout.write(`${JSON.stringify({ jsonrpc: '2.0', id: message.id, result: await handle(message.method, message.params || {}) })}\n`); }
    catch (error) { process.stdout.write(`${JSON.stringify({ jsonrpc: '2.0', id: message.id, error: { code: -32000, message: error.message } })}\n`); }
});
