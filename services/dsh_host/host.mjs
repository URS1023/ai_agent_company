import { createInterface } from 'node:readline';
import { ModelRoutes } from './models.mjs';
import { SessionHost } from './sessions.mjs';
import { httpEnvelope, json, openSse, publicCarrier, sendWorkerResponse } from './transport.mjs';

export const name = 'company-dsh-platform';
export const inject = ['appReady', 'appExit', 'webServer', 'sessionController', 'agents', 'sessions', 'llm', 'agentLoop', 'agentPresets', 'skills', 'tools', 'systemPrompt', 'credentials'];

/**
 * Worker 进程退出后，先把本宿主持有的运行交给独立后端收尾，再关闭失效宿主。
 * 回调只接收本进程已经登记的 run id，并由后端使用状态条件更新，重复执行不会覆盖终态。
 */
export async function recoverUnavailableRuns(sessions, state, error) {
    const runIds = typeof sessions.unavailableRunIds === 'function' ? sessions.unavailableRunIds() : [];
    for (const current of sessions.sessions.values()) sessions.failSession(current, error);
    if (!runIds.length || typeof state.recoverRuns !== 'function') return { run_ids: runIds, recovered: [] };
    try {
        const result = await state.recoverRuns(runIds);
        return { run_ids: runIds, recovered: result?.recovered || [] };
    } catch (recoveryError) {
        process.stderr.write(`DSH Worker 失效后的运行收尾失败：${recoveryError.stack || recoveryError}\n`);
        return { run_ids: runIds, recovered: [], error: recoveryError };
    }
}

/** 解析 SSE 续传游标，拒绝 NaN、负数和超过安全整数范围的输入。 */
function parseEventCursor(req) {
    const raw = new URL(req.url, 'http://localhost').searchParams.get('after') ?? req.headers['last-event-id'];
    if (raw == null || raw === '') return 0;
    if (!/^\d+$/.test(String(raw))) { const error = new Error('事件游标必须是非负整数'); error.status = 400; throw error; }
    const value = Number(raw);
    if (!Number.isSafeInteger(value)) { const error = new Error('事件游标超出允许范围'); error.status = 400; throw error; }
    return value;
}

/** 企业路由与原生执行共享同一个长驻宿主，Worker 不占用 HTTP 端口。 */
export function apply(ctx) {
    const state = globalThis[Symbol.for('company.dsh.host')];
    const { worker, description } = state;
    const models = new ModelRoutes(ctx, state);
    const sessions = new SessionHost(ctx, state, models);
    const streams = new Set();
    let ready = false, closing = false, carrier, closeTask;
    const capabilities = () => ({ engine: 'dsh', protocol_version: 2,
        modes: [{ id: 'standard', available: true }, { id: 'web', available: false, reason: '尚未配置联网检索业务插件' },
            { id: 'deep', available: false, reason: '尚未接通研究任务编排' }],
        features: { queue: true, steer: true, reasoning: true, questions: true, tasks: true, todos: true, deliveries: true },
        plugins: description.plugins.map(({ id, name, frontend }) => ({ id, name, frontend })),
        skills: description.skills.map(({ id, name, description, tools }) => ({ id, name, description, tools })) });

    async function history(request, conversationId) {
        const value = await worker.request('chat.history', { request, conversation_id: conversationId });
        const current = sessions.sessions.get(value.session_id);
        value.projections ||= { queue: [], tasks: [], deliveries: [], pending_questions: [] };
        if (current) value.projections.queue = sessions.queueView(current);
        if (current) value.projections.pending_questions = sessions.questions.view(current.id);
        if (current?.agent) value.projections.todos = ctx.get('sessionProjections').stateOf(current.agent.session, 'todos') || [];
        value.active_run = current?.active ? { id: current.active.run_id, run_id: current.active.run_id, status: current.active.status } : null;
        return value;
    }
    async function conversationStream(req, res, request, conversationId) {
        const identity = await worker.request('chat.authorize', { request, conversation_id: conversationId });
        const key = `session:${identity.session_id}`;
        let snapshotReady = false, cursor = 0;
        const pending = [];
        const stream = openSse(req, res);
        streams.add(stream);
        const deliver = entry => {
            if (!snapshotReady) { pending.push(entry); return; }
            if (Number(entry.id) <= cursor) return;
            cursor = Number(entry.id); stream.send(entry.event, entry.data, entry.id);
        };
        sessions.on(key, deliver);
        res.once('close', () => { sessions.off(key, deliver); streams.delete(stream); });
        try {
            const value = await history(request, conversationId);
            cursor = Number(value.cursor || 0);
            stream.send('session.snapshot', value, cursor);
            snapshotReady = true;
            for (const entry of pending) deliver(entry);
        } catch (error) { stream.send('session.error', { message: error.message }); stream.close(); }
    }
    async function runStream(req, res, request, runId) {
        const identity = await worker.request('chat.authorize', { request, run_id: runId });
        const key = `run:${runId}`;
        let loaded = false, cursor = parseEventCursor(req);
        const pending = [];
        const stream = openSse(req, res);
        streams.add(stream);
        const deliver = entry => {
            if (!loaded) { pending.push(entry); return; }
            if (Number(entry.id) <= cursor) return;
            cursor = Number(entry.id); stream.send(entry.event, entry.data, entry.id);
            if (entry.event === 'done') stream.close();
        };
        sessions.on(key, deliver);
        res.once('close', () => { sessions.off(key, deliver); streams.delete(stream); });
        try {
            const value = await worker.request('chat.history', { request, conversation_id: identity.conversation_id, after: cursor });
            loaded = true;
            for (const entry of value.events || []) if (entry.data?.run_id === runId || entry.run_id === runId) deliver(entry);
            for (const entry of pending) deliver(entry);
            // 重启后终态运行不在内存表；必须按目标运行取状态，不能借用会话最后一轮。
            const run = sessions.runs.get(runId) || value.runs?.find(item => (item.run_id || item.id) === runId)
                || (value.last_run && (value.last_run.run_id || value.last_run.id) === runId ? value.last_run : null)
                // 窗口外的旧终态运行不会再产生事件，使用鉴权时读取的目标状态关闭回放。
                || (identity.run_id === runId && identity.run_status ? { status: identity.run_status } : null);
            if (!res.writableEnded && run && ['completed', 'partial', 'failed', 'cancelled', 'interrupted'].includes(run.status)) {
                stream.send('done', { run_id: runId, session_id: identity.session_id, status: run.status }); stream.close();
            }
        } catch (error) { stream.send('error', { message: error.message }); stream.close(); }
    }
    async function handle(req, res) {
        try {
            const url = new URL(req.url, 'http://localhost');
            const path = url.pathname;
            if (['/api/health', '/health', '/api/ready', '/api/ai/ready'].includes(path)) {
                json(res, ready && !worker.closed ? 200 : 503, { ok: ready && !worker.closed, app: 'native-dev', engine: 'dsh', status: ready ? 'ok' : 'starting', ready,
                    protocol_version: 2, active_sessions: sessions.activeCount }); return;
            }
            if (!ready || closing || worker.closed) { json(res, 503, { detail: 'DSH 企业宿主尚未就绪' }); return; }
            if (path === '/api/chat/capabilities' && req.method === 'GET') { json(res, 200, capabilities()); return; }
            const request = await httpEnvelope(req);
            let match;
            if ((match = path.match(/^\/api\/chat\/conversations\/([^/]+)\/send$/)) && req.method === 'POST') {
                const input = JSON.parse(Buffer.from(request.body_base64, 'base64').toString('utf8') || '{}');
                if (input.delivery_mode === 'steer') {
                    const identity = await worker.request('chat.authorize', { request, conversation_id: decodeURIComponent(match[1]) });
                    json(res, 202, await sessions.steer(identity.session_id, request)); return;
                }
                if (input.mode && input.mode !== 'standard') { json(res, 422, { detail: '该模式尚未配置，当前可用 standard', code: 'mode_unavailable' }); return; }
                const prepared = await worker.request('chat.prepare', { request });
                await sessions.admit(prepared);
                if (String(req.headers.accept || '').includes('application/json')) {
                    json(res, 202, { accepted: true, request_id: prepared.client_request_id || prepared.request_id, run_id: prepared.run_id, session_id: prepared.session_id,
                        delivery_mode: 'queue', status: prepared.status, duplicate: !!prepared.duplicate });
                } else await runStream(req, res, request, prepared.run_id);
                return;
            }
            if ((match = path.match(/^\/api\/chat\/conversations\/([^/]+)\/events$/)) && req.method === 'GET') {
                await conversationStream(req, res, request, decodeURIComponent(match[1])); return;
            }
            if ((match = path.match(/^\/api\/chat\/conversations\/([^/]+)$/)) && req.method === 'GET') {
                json(res, 200, await history(request, decodeURIComponent(match[1]))); return;
            }
            if ((match = path.match(/^\/api\/chat\/conversations\/([^/]+)$/)) && req.method === 'DELETE') {
                const identity = await worker.request('chat.authorize', { request, conversation_id: decodeURIComponent(match[1]) });
                await sessions.archive(identity.session_id);
                sendWorkerResponse(res, await worker.request('api.dispatch', { request })); return;
            }
            if ((match = path.match(/^\/api\/chat\/runs\/([^/]+)\/events$/)) && req.method === 'GET') {
                await runStream(req, res, request, decodeURIComponent(match[1])); return;
            }
            if ((match = path.match(/^\/api\/chat\/runs\/([^/]+)\/cancel$/)) && req.method === 'POST') {
                const runId = decodeURIComponent(match[1]);
                await worker.request('chat.authorize', { request, run_id: runId });
                json(res, 200, await sessions.cancel(runId)); return;
            }
            if ((match = path.match(/^\/api\/chat\/conversations\/([^/]+)\/queue\/([^/]+)$/)) && req.method === 'POST') {
                const conversationId = decodeURIComponent(match[1]), runId = decodeURIComponent(match[2]);
                const identity = await worker.request('chat.authorize', { request, conversation_id: conversationId, run_id: runId });
                if (identity.conversation_id !== conversationId) { json(res, 409, { detail: '该队列消息不属于当前会话' }); return; }
                const input = JSON.parse(Buffer.from(request.body_base64, 'base64').toString('utf8') || '{}');
                json(res, 200, await sessions.updateQueue(runId, input.action, input.content)); return;
            }
            if ((match = path.match(/^\/api\/chat\/conversations\/([^/]+)\/questions\/([^/]+)$/)) && req.method === 'POST') {
                const identity = await worker.request('chat.authorize', { request, conversation_id: decodeURIComponent(match[1]) });
                const input = JSON.parse(Buffer.from(request.body_base64, 'base64').toString('utf8') || '{}');
                json(res, 200, await sessions.questions.answer(identity.session_id, decodeURIComponent(match[2]), input)); return;
            }
            const plugin = description.plugins.find(item => item.route_prefixes?.some(prefix => path === prefix || path.startsWith(`${prefix}/`)));
            sendWorkerResponse(res, await worker.request('api.dispatch', { request, plugin_id: plugin?.id }));
        } catch (error) {
            if (!res.headersSent) json(res, Number(error.status || (error instanceof SyntaxError ? 400 : 500)), { detail: error.message || String(error), code: error.code || 'host_error' });
            else res.destroy(error);
        }
    }
    ctx.webServer.registerFallback(handle);
    worker.handle('model.generate', (params, message) => models.generate(params, message.id));
    worker.handle('model.cancel', params => { models.cancelRequest(params.request_id); if (params.run_id) models.cancel(params.run_id); return { cancelled: true }; });
    worker.handle('business.event', async params => {
        const run = sessions.runs.get(params.run_id);
        if (!run) return { published: false, reason: 'unattached_run' };
        if (params.event !== 'task.progress') throw new Error('业务进度回调只接收 task.progress');
        const entry = await sessions.publish(run, params.event, params.data || {});
        return { published: true, id: entry.id };
    });
    worker.handle('chat.execute', async ({ prepared }) => {
        let unsubscribe;
        const complete = new Promise((resolve, reject) => {
            const key = `settled:${prepared.run_id}`;
            const listener = result => { unsubscribe(); resolve(result); };
            const failed = ({ run_id, error }) => { if (run_id === prepared.run_id) { unsubscribe(); reject(error); } };
            unsubscribe = () => { sessions.off(key, listener); sessions.off('execution-error', failed); };
            sessions.on(key, listener); sessions.on('execution-error', failed);
        });
        try {
            await sessions.admit(prepared);
            const known = sessions.runs.get(prepared.run_id);
            if (known?.result) { unsubscribe(); return known.result; }
            if (!known && prepared.duplicate) throw new Error('重复业务执行已在历史中结算，请读取此前运行结果');
            return await complete;
        } catch (error) { unsubscribe(); throw error; }
    });
    worker.on('unavailable', error => {
        ready = false;
        for (const stream of streams) { stream.send('session.error', { code: 'worker_unavailable', message: error.message }); stream.close(); }
        // Worker 已不可用时不再等待原生任务；先独立收尾数据库中的活动 Run，再让上层重启宿主。
        void recoverUnavailableRuns(sessions, state, error).finally(() => close(1));
    });
    async function close(exitCode = 0) {
        if (closeTask) return closeTask;
        closing = true; ready = false;
        closeTask = (async () => {
            for (const stream of streams) stream.close();
            carrier?.close(); carrier?.closeIdleConnections();
            await sessions.close();
            await worker.close();
            control.close(); process.stdin.pause();
            ctx.appExit(exitCode);
        })();
        return closeTask;
    }
    const control = createInterface({ input: process.stdin });
    control.on('line', line => { try { if (JSON.parse(line).method === 'shutdown') void close(); } catch {} });
    control.once('close', () => { if (!closing) void close(); });
    process.once('SIGINT', () => { void close(); });
    process.once('SIGTERM', () => { void close(); });
    ctx.effect(() => () => { ready = false; carrier?.close(); for (const stream of streams) stream.close(); }, '企业宿主释放监听与订阅');
    ctx.appReady.onReady(() => {
        void (async () => {
            process.stderr.write('DSH：初始化原生模型路由\n');
            await models.initialize();
            await ctx.agentPresets.resolve('company');
            await sessions.recover();
            if (closing) return;
            process.stderr.write('DSH：激活业务后台服务\n');
            await worker.request('system.activate', {});
            carrier = await publicCarrier({ nativePort: ctx.webServer.port, host: process.env.HOST || '127.0.0.1',
                port: Number(process.env.PORT || 8000), certificate: process.env.SSL_CERTFILE, key: process.env.SSL_KEYFILE });
            ready = true;
            process.stdout.write(`${JSON.stringify({ event: 'dsh.ready', host: process.env.HOST || '127.0.0.1', port: carrier.address().port, engine: 'dsh' })}\n`);
        })().catch(error => {
            process.stderr.write(`DSH 初始化失败：${error.stack || error}\n`);
            void close(1);
        });
    });
}
