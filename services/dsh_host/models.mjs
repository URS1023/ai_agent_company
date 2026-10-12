import { createHash } from 'node:crypto';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { usageView } from './projection.mjs';

export function upstream(root, path) { return import(pathToFileURL(join(root, path, 'lib/index.js')).href); }

/** OpenCode 按会话路由与缓存；直连和本机兼容代理都必须携带稳定标识。 */
function sessionHeaders(model, workspaceId, sessionId) {
    const url = new URL(model.baseURL);
    const localProxy = ['127.0.0.1', 'localhost'].includes(url.hostname)
        && url.port === String(process.env.OPCODE_PROXY_PORT || 8901);
    if (url.hostname !== 'opencode.ai' && !localProxy) return undefined;
    if (!sessionId) throw new Error('OpenCode 模型调用缺少会话标识');
    const id = createHash('sha256').update(JSON.stringify([workspaceId, sessionId])).digest('hex');
    return { 'x-opencode-session': id };
}

/** 配置修订生成独立路由，活动会话不受其他工作区改模型影响。 */
export class ModelRoutes {
    constructor(ctx, state) {
        this.ctx = ctx; this.state = state; this.profiles = {}; this.routes = new Map(); this.pending = new Map(); this.requests = new Map();
        this.registrations = new Map(); this.routeClock = 0; this.closed = false;
        const configured = Number.parseInt(process.env.DSH_MODEL_ROUTE_MAX || '', 10);
        this.maxRoutes = Number.isSafeInteger(configured) && configured > 0 ? Math.min(configured, 1024) : 128;
    }
    cancel(runId) { for (const controller of this.pending.get(runId) || []) controller.abort(new Error('用户停止了业务模型调用')); }
    cancelRequest(requestId) { this.requests.get(requestId)?.abort(new Error('业务请求停止等待模型')); }
    close() {
        this.closed = true;
        for (const controller of this.requests.values()) controller.abort();
        for (const runId of this.pending.keys()) this.cancel(runId);
        for (const [provider, route] of this.routes) this.removeRoute(provider, route);
    }
    /** 仅释放本路由持有的注册和凭据；初始化失败与宿主关闭共用相同清理路径。 */
    removeRoute(provider, route) {
        if (this.routes.get(provider) !== route) return;
        try { this.registrations.get(provider)?.(); } catch {}
        this.registrations.delete(provider);
        this.routes.delete(provider);
        if (route.keyRef) this.state.keys?.delete?.(route.keyRef);
        if (this.profiles[provider]) {
            const { [provider]: _removed, ...profiles } = this.profiles;
            this.profiles = profiles;
            this.ctx.emit('loader/volatile-update');
        }
    }
    /** 只淘汰初始化完成且没有业务租约的路由，活动主代理和授权中的辅助调用均受保护。 */
    evictIdleRoute() {
        if (this.routes.size < this.maxRoutes) return true;
        const idle = [...this.routes.entries()]
            .filter(([, route]) => !route.initializing && !route.active)
            .sort(([, left], [, right]) => left.lastUsed - right.lastUsed)[0];
        if (!idle) return false;
        this.removeRoute(...idle);
        return true;
    }
    normalize(model) {
        if (model?.protocol === 'openai-completions' && (model.thinking === 'disabled' || model.reasoningEfforts === false)) {
            const normalized = { ...model, reasoningEfforts: false };
            delete normalized.reasoningEffort;
            return normalized;
        }
        return model;
    }
    async initialize() {
        this.native = await upstream(this.state.upstreamRoot, 'packages/llm/llm');
        this.deepseek = await upstream(this.state.upstreamRoot, 'packages/llm/llm-deepseek');
        const pi = await upstream(this.state.upstreamRoot, 'packages/llm/llm-pi-ai');
        pi.apply(this.ctx, { providers: { get: () => this.profiles } });
    }
    reserve(model, workspaceId, sessionId) {
        if (this.closed) throw Object.assign(new Error('模型宿主正在关闭'), { code: 'MODEL_ROUTES_CLOSED' });
        model = this.normalize(model);
        if (!model?.model || !model?.baseURL || !model?.apiKey) throw new Error('模型配置缺少名称、地址或凭据');
        if (!['openai-completions', 'deepseek-messages'].includes(model.protocol)) throw new Error(`尚未接入的模型协议：${model.protocol}`);
        if (!Number.isInteger(model.contextWindow) || !Number.isInteger(model.maxTokens) || model.maxTokens <= 0 || model.contextWindow <= model.maxTokens) throw new Error('模型上下文与输出预算配置无效');
        const headers = model.protocol === 'openai-completions' ? sessionHeaders(model, workspaceId, sessionId) : undefined;
        const digest = createHash('sha256').update(JSON.stringify([workspaceId, model, headers])).digest('hex').slice(0, 24);
        const provider = `company-${digest}`;
        const existing = this.routes.get(provider);
        if (existing) { existing.lastUsed = ++this.routeClock; return { provider, route: existing }; }
        if (!this.evictIdleRoute()) throw Object.assign(new Error('模型路由数量已达到宿主上限，请复用已有模型配置后再试'), { code: 'MODEL_ROUTE_LIMIT' });
        // 在第一次异步等待前占用容量；同配置共享初始化，不同配置不能同时越过上限。
        const route = { ...model, lastUsed: ++this.routeClock, active: 0, initializing: true };
        this.routes.set(provider, route);
        route.ready = Promise.resolve().then(async () => {
            try {
                if (this.closed) throw Object.assign(new Error('模型宿主正在关闭'), { code: 'MODEL_ROUTES_CLOSED' });
                await this.registerRoute(provider, route, model, workspaceId, headers, digest);
                if (this.closed) throw Object.assign(new Error('模型宿主正在关闭'), { code: 'MODEL_ROUTES_CLOSED' });
                route.initializing = false;
            } catch (error) { this.removeRoute(provider, route); throw error; }
        });
        return { provider, route };
    }
    async ensure(model, workspaceId = '', sessionId = '') {
        const { provider, route } = this.reserve(model, workspaceId, sessionId);
        await route.ready;
        if (this.closed) throw Object.assign(new Error('模型宿主正在关闭'), { code: 'MODEL_ROUTES_CLOSED' });
        return provider;
    }
    /** 先持有再等待路由就绪，消除 ensure 返回到调用者恢复执行之间的淘汰窗口。 */
    async acquire(model, workspaceId = '', sessionId = '') {
        const { provider, route } = this.reserve(model, workspaceId, sessionId);
        route.active++;
        let released = false;
        const release = () => {
            if (released) return;
            released = true;
            route.active--;
            route.lastUsed = ++this.routeClock;
        };
        try {
            await route.ready;
            if (this.closed) throw Object.assign(new Error('模型宿主正在关闭'), { code: 'MODEL_ROUTES_CLOSED' });
            return { provider, release };
        }
        catch (error) { release(); throw error; }
    }
    async registerRoute(provider, route, model, workspaceId, headers, digest) {
        const retryPolicy = { mode: 'normal', maxRetries: 2, backoff: { initialDelayMs: 250, maxDelayMs: 2000 } };
        if (model.protocol === 'deepseek-messages') {
            const options = this.deepseek.resolveAdapterOptions({ baseURL: model.baseURL, apiKeyEnv: 'COMPANY_PRIVATE',
                thinking: model.thinking || 'enabled', maxTokens: model.maxTokens, defaultContextWindow: model.contextWindow,
                retryPolicy, ...(model.reasoningEffort ? { reasoningEffort: model.reasoningEffort } : {}),
                models: [{ id: model.model, contextWindow: model.contextWindow, maxTokens: model.maxTokens,
                    inputModalities: model.inputModalities || ['text'] }] });
            const adapter = new this.deepseek.DeepSeekAdapter({ options: () => options, resolveApiKey: async () => model.apiKey,
                resolveAttachments: () => this.ctx.get('attachments'), resolveUserId: () => String(workspaceId),
                resolveImageAccess: (attachments, ref) => this.native.resolveImageAttachmentAccess(attachments,
                    path => this.ctx.get('fs')?.processPathFromHostPath(path), ref),
                prepareExtensions: request => this.ctx.get('deepseekLlmApiExtensions')?.prepare(request)
                    || Promise.resolve({ fields: {}, accept: async () => {} }) });
            const registration = this.ctx.llm.registerAdapter([provider], adapter);
            this.registrations.set(provider, registration);
        } else if (model.protocol === 'openai-completions') {
            const keyRef = `COMPANY_${digest.toUpperCase()}`;
            route.keyRef = keyRef;
            this.state.keys.set(keyRef, model.apiKey);
            this.profiles = { ...this.profiles, [provider]: { apiKeyEnv: keyRef, api: model.protocol, baseURL: model.baseURL, retryPolicy,
                ...(headers ? { headers } : {}),
                compat: { maxTokensField: 'max_tokens', ...(model.compat || {}) },
                models: [{ id: model.model, name: model.model, contextWindow: model.contextWindow, maxTokens: model.maxTokens,
                    input: model.inputModalities || ['text'], ...(model.reasoningEfforts !== undefined ? { reasoningEfforts: model.reasoningEfforts } : {}) }] } };
            this.ctx.emit('loader/volatile-update');
        } else throw new Error(`尚未接入的模型协议：${model.protocol}`);
        await this.ctx.llm.resolveModelInfo(provider, model.model);
    }
    async generate(params, requestId) {
        params = { ...params, model: this.normalize(params.model) };
        if (params.profile === 'vision' && params.model?.inputModalities === undefined) {
            params = { ...params, model: { ...params.model, inputModalities: ['text', 'image'] } };
        }
        const controller = new AbortController();
        const signal = params.request_timeout ? AbortSignal.any([controller.signal, AbortSignal.timeout(Math.ceil(params.request_timeout * 1000))]) : controller.signal;
        this.requests.set(requestId, controller);
        let lease, assembler, permission, failure, runId = params.run_id;
        const track = id => {
            if (!id) return;
            let controllers = this.pending.get(id);
            if (!controllers) { controllers = new Set(); this.pending.set(id, controllers); }
            controllers.add(controller);
        };
        const untrack = id => {
            const controllers = this.pending.get(id);
            controllers?.delete(controller);
            if (!controllers?.size) this.pending.delete(id);
        };
        track(runId);
        try {
            lease = await this.acquire(params.model, params.workspace_id, params.conversation_id || params.run_id || requestId);
            const { provider } = lease;
            signal.throwIfAborted();
            assembler = new this.native.BlockAssembler();
            const messages = await Promise.all((params.messages || []).map(async message => {
                const content = await this.normalizeContent(message.content);
                if (content.some(block => block.type === 'image') && !params.model.inputModalities?.includes('image')) throw new Error('当前模型未声明图片输入能力，请选择视觉模型');
                if (message.role === 'tool') return this.native.createToolResultMessage({
                    callId: message.tool_call_id, content, isError: message.is_error === true,
                });
                // 辅助调用的旧格式历史没有原生身份与来源；通过构造器补齐，不能伪造适配器私有 replayState。
                if (message.role === 'assistant') return this.native.createAssistantMessage({ content: [...content, ...(message.tool_calls || []).map(call => ({
                    type: 'tool-call', id: call.id, name: call.function?.name || call.name,
                    arguments: typeof call.function?.arguments === 'string' ? call.function.arguments : JSON.stringify(call.function?.arguments || call.arguments || {}),
                }))], source: { provider, model: params.model.model } });
                return this.native.createMessage({ role: message.role, content,
                    source: { kind: message.role === 'system' ? 'system-prompt' : 'user' } });
            }));
            // 原生接口不声明结构化输出协议；业务 JSON 请求通过明确提示与实际解析校验闭环。
            if (params.response_format?.type === 'json_object') messages.unshift(this.native.createSystemMessage('本次调用必须只输出一个有效 JSON 对象，不使用 Markdown 代码围栏。'));
            else if (params.response_format) throw new Error(`原生适配器尚未支持的结构化输出格式：${params.response_format.type}`);
            const tools = params.tools?.map(tool => tool.function || tool);
            signal.throwIfAborted();
            permission = await this.state.worker.request('model.authorize', { run_id: params.run_id,
                workspace_id: params.workspace_id, profile: params.profile, provider, model: params.model.model, purpose: params.purpose || 'business',
                capability: params.capability || 'chat', usage_context: params.usage_context });
            if (!permission.allowed) throw new Error('业务辅助模型调用被预算规则拒绝');
            untrack(runId); runId = permission.run_id || params.run_id; track(runId);
            signal.throwIfAborted();
            for await (const chunk of this.ctx.llm.stream({ provider, model: params.model.model, messages, tools, signal,
                maxTokens: params.model.maxTokens, temperature: params.temperature,
                ...(params.model.reasoningEffort ? { reasoningEffort: params.model.reasoningEffort } : {}) })) assembler.push(chunk);
            const finish = assembler.finish;
            if (finish.kind === 'error' || finish.kind === 'aborted') throw new Error(finish.failure?.message || finish.kind);
            const blocks = assembler.blocks();
            const content = blocks.filter(block => block.type === 'text').map(block => block.text).join('');
            if (params.response_format?.type === 'json_object' && !blocks.some(block => block.type === 'tool-call')) {
                const parsed = JSON.parse(content);
                if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error('模型未返回有效 JSON 对象');
            }
            return { content, tool_calls: blocks.filter(block => block.type === 'tool-call').map(block => ({ id: block.id, type: 'function',
                function: { name: block.name, arguments: block.arguments } })),
                usage: usageView(assembler.usage), finish_reason: finish.kind === 'max-tokens' ? 'length' : 'stop' };
        } catch (error) { failure = { code: error.code || 'MODEL_GENERATE_FAILED', message: error.message }; throw error; }
        finally {
            this.requests.delete(requestId);
            untrack(runId);
            try {
                if (permission?.allowed) await this.state.worker.request('model.settle', { run_id: permission.run_id || params.run_id,
                    model_call_id: permission.model_call_id, status: failure ? 'failed' : 'completed',
                    usage: usageView(assembler?.usage), error: failure });
            } finally { lease?.release(); }
        }
    }
    async normalizeContent(content) {
        if (typeof content === 'string') return [{ type: 'text', text: content }];
        const parts = content || [], encoded = [];
        for (const part of parts) {
            if (part.type !== 'image_url') continue;
            const url = typeof part.image_url === 'string' ? part.image_url : part.image_url?.url;
            const match = /^data:(image\/(?:png|jpeg|webp|gif));base64,([A-Za-z0-9+/=\r\n]+)$/.exec(url || '');
            if (!match) throw new Error('辅助视觉调用需要受支持的 base64 图片；尚未接入远程图片地址');
            encoded.push({ mediaType: match[1], data: Buffer.from(match[2], 'base64') });
        }
        const refs = encoded.length ? await this.ctx.get('attachments').saveImages(encoded) : [];
        let index = 0;
        return parts.map(part => {
            if (part.type === 'image_url') return { type: 'image', attachment: refs[index++] };
            if (['text', 'image', 'file', 'reasoning', 'tool-call'].includes(part.type)) return part;
            throw new Error(`辅助模型暂不支持此内容类型：${part.type}`);
        });
    }
}
