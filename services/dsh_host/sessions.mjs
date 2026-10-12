import { EventEmitter } from 'node:events';
import { mkdir, readFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { NativeProjection, usageView } from './projection.mjs';
import { NativeQuestions } from './interaction.mjs';
import { installArtifactPresent } from './present-references.mjs';
import { cancelOwnedJobs, observeOwnedJobs, ownedActiveJobs } from './native-jobs.mjs';

const terminal = new Set(['completed', 'failed', 'cancelled', 'partial', 'interrupted']);
const textOf = content => typeof content === 'string' ? content : (content || []).filter(block => block.type === 'text').map(block => block.text).join('');

/** 按业务存储证明的请求归属截取恢复窗口；steer 属于原运行，下一根请求另起运行。 */
function recoveryWindow(events, run) {
    const identity = run.recovery_identity || {};
    const own = new Set([run.request_id, ...(identity.steering_request_ids || [])]);
    const other = new Set(identity.other_request_ids || []);
    const ambiguous = new Set(identity.ambiguous_request_ids || []);
    const userRequest = event => event.type === 'user/message'
        && (!event.data.source?.kind || event.data.source.kind === 'user');
    const index = events.findIndex(event => userRequest(event) && event.data.source?.rpcId === run.request_id);
    if (index < 0) return { index, events: [], uncertain: false };
    if (ambiguous.has(run.request_id) || other.has(run.request_id)
        || identity.root_request_id && identity.root_request_id !== run.request_id) {
        return { index, events: [], uncertain: true };
    }
    const beforeRequest = (next, request) => {
        const prefix = events.slice(index, next);
        const opened = prefix.findLastIndex(event => event.type === 'turn/start');
        // 原生引擎先消费 next-turn 收件箱再写用户消息；只有明确对应这条请求、
        // 且开启后尚无正文/工具工作的前导事件，才能随下一请求一起截走。
        const prelude = new Set(['agent/inbox/spliced', 'step/start', 'system/message']);
        const previousEnd = prefix.slice(0, opened).findLastIndex(event => event.type === 'turn/end');
        const queued = prefix.slice(previousEnd + 1, opened).some(event => event.type === 'agent/inbox/spliced'
            && event.data.target === 'next-turn' && event.data.inserted?.some(item => item.source?.kind === 'user'
                && item.source.rpcId === request));
        const boundary = opened > 0 && queued && prefix.slice(opened + 1).every(event => prelude.has(event.type))
            ? index + opened : next;
        return events.slice(index, boundary);
    };
    for (let next = index + 1; next < events.length; next++) {
        const event = events[next];
        if (!userRequest(event)) continue;
        const request = event.data.source?.rpcId;
        // 子代理通知拥有独立 source.kind；缺少身份的用户消息不能当成本运行的确定性内容。
        if (ambiguous.has(request) || own.has(request) && other.has(request))
            return { index, events: beforeRequest(next, request), uncertain: true };
        if (own.has(request)) continue;
        return { index, events: beforeRequest(next, request), uncertain: !other.has(request) };
    }
    return { index, events: events.slice(index), uncertain: false };
}

/** 在宿主关闭时让无法自行结束的原生等待及时退出，不限制正常业务长任务。 */
function abortable(promise, signal) {
    if (!signal) return promise;
    if (signal.aborted) return Promise.reject(Object.assign(new Error('宿主正在关闭'), { code: 'DSH_HOST_ABORTED' }));
    let onAbort;
    const aborted = new Promise((_, reject) => {
        onAbort = () => reject(Object.assign(new Error('宿主正在关闭'), { code: 'DSH_HOST_ABORTED' }));
        signal.addEventListener('abort', onAbort, { once: true });
    });
    return Promise.race([promise, aborted]).finally(() => signal.removeEventListener('abort', onAbort));
}

/** 会话队列与原生 Agent 一对一；不同会话共享进程但不共享工具或运行上下文。 */
export class SessionHost extends EventEmitter {
    constructor(ctx, state, models) {
        super();
        this.ctx = ctx; this.state = state; this.worker = state.worker; this.models = models;
        this.sessions = new Map(); this.runs = new Map(); this.agentRuns = new Map(); this.childCompletions = new Map(); this.activeCount = 0;
        this.limit = Math.max(1, Number(process.env.DSH_MAX_ACTIVE_SESSIONS || 4));
        this.admissionSequence = 0;
        this.accepting = true;
        this.installNativeHooks();
        this.questions = new NativeQuestions(this);
    }
    installNativeHooks() {
        this.ctx.on('agent/created', async ({ agent }) => {
            const current = this.ownerOf(agent.id);
            if (!current?.active || agent.id === current.id) return;
            if (current.active.receiptFailure) { agent.cancel({ kind: 'parent' }); return; }
            this.agentRuns.set(agent.id, current.active.run_id);
            let resolve;
            const done = new Promise(yes => { resolve = yes; });
            this.childCompletions.set(agent.id, { done, resolve });
            await this.installBusiness(current, current.active, agent, []);
        }, { global: true });
        this.ctx.on('agent/disposed', ({ agent }) => {
            this.agentRuns.delete(agent.id);
            this.childCompletions.get(agent.id)?.resolve();
            this.childCompletions.delete(agent.id);
        }, { global: true });
        // 补充消息持久化期间守住原生轮次边界，避免补充落成没有业务归属的新轮次。
        this.ctx.on('agent/turn-stopping', async ({ agent }) => {
            const current = this.sessions.get(agent.id);
            if (current?.steering) await current.steering;
        }, { global: true });
        this.ctx.on('session/event', (session, event) => {
            const current = this.ownerOf(session.id);
            if (!current) return;
            if (event.type === 'turn/end' && session.id === current.id) current.lastTurn = event;
            if (event.type === 'tool/call') current.invocations.set(event.data.callId, { seq: event.seq, name: event.data.name, sessionId: session.id });
            if (event.type === 'deliverables/presented' && session.id !== current.id && current.active) {
                const run = current.active;
                current.childDeliveries = (current.childDeliveries || Promise.resolve()).then(async () => {
                    const delivery = await this.worker.request('delivery.present', { run_id: run.run_id, files: event.data.files });
                    for (const artifact of delivery.artifacts || []) await this.publish(run, 'delivery.created', { delivery: artifact });
                });
                void current.childDeliveries.catch(error => this.failSession(current, error));
            }
        }, { global: true });
        this.ctx.on('agent/error', ({ agent, error }) => {
            const current = this.sessions.get(agent.id);
            if (current) current.agentError = error;
        }, { global: true });
        const owner = this;
        this.ctx.on('llm/stream', async function* (options, next) {
            const current = owner.ownerOf(String(options.sessionId)), run = current?.active;
            if (!current) { yield* next(); return; }
            if (!run || run.cancelRequested || run.receiptFailure || (current.id !== String(options.sessionId) && owner.agentRuns.get(String(options.sessionId)) !== run.run_id))
                throw new Error('原生代理所属业务运行已结束');
            options.signal?.throwIfAborted();
            const permission = await owner.worker.request('model.authorize', { run_id: run.run_id,
                provider: options.provider, model: options.model, purpose: options.purpose || 'conversation' });
            if (!permission.allowed) throw new Error('模型调用预算不足或配置不可用');
            let usage; let failure; let finish;
            try {
                if (current.active !== run || run.cancelRequested || run.receiptFailure) throw new Error('业务运行已停止，不能继续模型请求');
                options.signal?.throwIfAborted();
                for await (const chunk of next()) {
                    if (chunk.type === 'usage') usage = usageView(chunk.usage);
                    if (chunk.type === 'finish') finish = chunk.reason;
                    yield chunk;
                }
                if (finish?.kind === 'error' || finish?.kind === 'aborted') failure = finish.failure;
            } catch (error) { failure = { message: error.message, code: error.code }; throw error; }
            finally {
                if (usage) {
                    run.usage ||= { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 };
                    for (const key of ['prompt_tokens', 'completion_tokens', 'total_tokens']) run.usage[key] += usage[key] || 0;
                }
                await owner.worker.request('model.settle', { run_id: run.run_id, model_call_id: permission.model_call_id,
                    status: failure ? 'failed' : 'completed', usage, error: failure });
            }
        }, { global: true });
    }
    ownerOf(agentId) {
        if (this.sessions.has(agentId)) return this.sessions.get(agentId);
        const registry = this.ctx.agents;
        if (!registry?.get(agentId)) return undefined;
        const visited = new Set();
        let childId = agentId;
        while (!visited.has(childId)) {
            visited.add(childId);
            const parent = registry.list().find(candidate => registry.isOwnedBy(childId, candidate));
            if (!parent) return undefined;
            if (this.sessions.has(parent.id)) return this.sessions.get(parent.id);
            childId = parent.id;
        }
    }
    stateFor(prepared) {
        let current = this.sessions.get(prepared.session_id);
        if (!current) {
            current = { id: prepared.session_id, conversationId: prepared.conversation_id, queue: [], active: null,
                invocations: new Map(), disposers: [], cursor: -1, waiters: new Set(), projection: new NativeProjection() };
            this.sessions.set(current.id, current);
        }
        if (current.conversationId !== prepared.conversation_id) throw new Error('会话标识与业务会话不一致');
        return current;
    }
    /** 返回此宿主内存中尚未进入终态的运行，供 Worker 崩溃后的幂等收尾使用。 */
    unavailableRunIds() {
        return [...this.runs.values()]
            .filter(run => run?.run_id && !terminal.has(run.status))
            .map(run => run.run_id);
    }
    queueView(current) {
        return [...(current.active ? [current.active] : []), ...current.queue].map(run => ({ id: run.run_id, run_id: run.run_id,
            request_id: run.client_request_id || run.request_id, content: textOf(run.content), status: run.status, message_id: run.message_id }));
    }
    async publish(run, event, data = {}, nativeSequence) {
        const payload = { ...data, run_id: run.run_id, session_id: run.session_id, message_id: run.message_id,
            request_id: run.client_request_id || run.request_id };
        const current = this.stateFor(run);
        // 同会话的工具、正文和队列回执统一排序，不能因并发 RPC 回复倒序丢事件。
        const operation = (current.publication || Promise.resolve()).then(async () => {
            const receipt = await this.worker.request('chat.event', { run_id: run.run_id, event, data: payload,
                ...(nativeSequence === undefined ? {} : { native_sequence: nativeSequence }) });
            const entry = { id: receipt.id, event: receipt.event || event, data: receipt.data || payload };
            this.emit(`session:${run.session_id}`, entry);
            this.emit(`run:${run.run_id}`, entry);
            return entry;
        });
        current.publication = operation.catch(() => {});
        return operation;
    }
    async publishReceipt(run, receipt) {
        const current = this.stateFor(run);
        const operation = (current.publication || Promise.resolve()).then(() => {
            // 规范事件已经由 Worker 原子提交，只发出同一游标，不再调用 chat.event 二次写入。
            current.receiptIds ||= new Set();
            if (current.receiptIds.has(receipt.id)) return;
            current.receiptIds.add(receipt.id);
            const entry = { id: receipt.id, event: receipt.event, data: receipt.data };
            for (const channel of [`session:${run.session_id}`, `run:${run.run_id}`]) {
                try { this.emit(channel, entry); }
                catch (error) {
                    // 订阅异常不能把已提交成功变成原生工具失败；另一通道仍有独立发送机会。
                    try { process.stderr.write(`DSH 已存回执订阅推送失败 ${channel}/${receipt.id}: ${error.stack || error}\n`); }
                    catch { /* 诊断输出异常同样不能改变业务结果。 */ }
                }
            }
            return entry;
        });
        current.publication = operation.catch(() => {});
        return operation;
    }
    async queueChanged(current, run, extra = {}) { await this.publish(run, 'queue.updated', { queue: this.queueView(current), ...extra }); }
    async steer(sessionId, request) {
        const current = this.sessions.get(sessionId), run = current?.active;
        if (!run || run.status !== 'running' || run.cancelRequested || current.agent?.status !== 'running')
            throw Object.assign(new Error('当前任务已结束或尚未启动，请作为新消息发送'), { status: 409 });
        if (current.steering) throw Object.assign(new Error('正在接收上一条补充，请稍后发送'), { status: 409 });
        let release;
        current.steering = new Promise(resolve => { release = resolve; });
        try {
            const receipt = await this.worker.request('chat.steer', { request, conversation_id: current.conversationId, run_id: run.run_id });
            if (run.cancelRequested) throw Object.assign(new Error('任务已停止，补充内容已保存到历史'), { status: 409 });
            await this.ctx.sessionController.prompt({ sessionId, requestId: receipt.request_id, content: receipt.content,
                mode: 'steer', clientTimeZone: 'Asia/Shanghai' }, current.abort.signal);
            await this.publish(run, 'message.steered', { user_message: receipt.user_message, steering_request_id: receipt.request_id });
            return { accepted: true, run_id: run.run_id, session_id: sessionId, request_id: receipt.request_id,
                delivery_mode: 'steer', status: 'running', user_message: receipt.user_message, duplicate: receipt.duplicate };
        } finally { current.steering = null; release(); }
    }
    async admit(prepared) {
        if (!this.accepting) throw new Error('宿主正在关闭，暂不接受新任务');
        if (prepared.duplicate || this.runs.has(prepared.run_id)) return prepared;
        const current = this.stateFor(prepared);
        prepared.status = 'queued';
        prepared.admission_order = ++this.admissionSequence;
        this.runs.set(prepared.run_id, prepared);
        current.queue.push(prepared);
        await this.queueChanged(current, prepared);
        this.pump();
        return prepared;
    }
    pump() {
        if (!this.accepting) return;
        const waiting = [...this.sessions.values()].sort((left, right) =>
            (left.queue[0]?.admission_order ?? Infinity) - (right.queue[0]?.admission_order ?? Infinity));
        for (const current of waiting) {
            if (this.activeCount >= this.limit) break;
            if (current.active || current.failure || current.archiving || !current.queue.length || current.queue[0].updating) continue;
            const run = current.queue.shift();
            current.active = run; run.status = 'running'; this.activeCount++;
            run.execution = this.execute(current, run).catch(error => {
                process.stderr.write(`DSH 运行结算异常 ${run.run_id}: ${error.stack || error}\n`);
                this.emit('execution-error', { run_id: run.run_id, error });
            }).finally(() => {
                current.active = null; this.activeCount--;
                void this.queueChanged(current, run).catch(error => this.failSession(current, error));
                this.pump();
            });
        }
    }
    async configure(current, run) {
        await mkdir(run.cwd, { recursive: true });
        run.model = this.models.normalize(run.model);
        // 主代理及其子代理可能跨多轮使用同一路由；直到整项业务收尾才允许 LRU 回收。
        run.modelLease = await this.models.acquire(run.model, run.workspace_id, run.session_id);
        const { provider } = run.modelLease;
        await this.ctx.sessionController.create({ sessionId: current.id, cwd: run.cwd, agentPreset: 'company' });
        const resolved = await this.ctx.sessionController.resolveAgent(current.id);
        if (resolved.error) throw resolved.error;
        const agent = resolved.agent;
        current.agent = agent;
        if (run.history_seed?.messages?.length) {
            const hasHistory = agent.session.snapshotEvents().some(event => ['user/message', 'assistant/message'].includes(event.type));
            const pendingSeed = [...agent.inbox.nextStep, ...agent.inbox.nextTurn].some(message => message.source?.kind === 'company-legacy-history');
            if (!hasHistory && !pendingSeed) {
                const source = { kind: 'company-legacy-history', form: 'notice', summary: '已导入旧聊天历史，后续使用原生会话继续' };
                const raw = JSON.stringify(run.history_seed.messages);
                let text = `以下为迁移前已保存的聊天历史，仅作业务上下文；本轮请求仍以后续用户消息为准。\n${raw}`;
                const message = this.models.native.createUserMessage({ source, content: [{ type: 'text', text }] });
                const budget = Math.max(256, Math.floor((run.model.contextWindow - run.model.maxTokens) * 0.25));
                if (this.ctx.get('tokenMeter').estimateMessage(message) > budget) {
                    const stored = await this.ctx.get('spillStore').saveText({ owner: { sessionId: current.id },
                        source: { kind: 'session-reference', sessionId: current.id, label: 'legacy-platform-history' },
                        suggestedName: 'legacy-history.json', content: raw });
                    const tail = run.history_seed.messages.slice(-6), perMessage = Math.max(32, Math.floor(Math.min(2000, budget) / tail.length));
                    const preview = tail.map(item => ({ role: item.role, status: item.status,
                        content: Array.from(String(item.content || '')).slice(-perMessage).join('') }));
                    text = `旧聊天记录已迁移，完整内容位于 ${stored.locator}。${stored.retrievalHint}\n以下仅为最近消息的末尾片段；业务数值和早期约束需要读取完整来源核对。\n${JSON.stringify(preview)}`;
                }
                agent.inject(this.models.native.createUserMessage({ source, content: [{ type: 'text', text }] }));
            }
        }
        await this.ctx.sessionController.selectModel({ sessionId: current.id, provider, model: run.model.model,
            ...(run.model.reasoningEffort ? { reasoningEffort: run.model.reasoningEffort } : {}) });
        for (const dispose of current.disposers.splice(0)) dispose();
        await this.installBusiness(current, run, agent, current.disposers);
        if (!current.followTask) await this.follow(current);
        return agent;
    }
    async installBusiness(current, run, agent, disposers) {
        disposers.push(installArtifactPresent(agent.ctx, {
            active: () => current.active === run && !run.cancelRequested && !run.receiptFailure && !terminal.has(run.status),
            reference: files => this.worker.request('delivery.reference', { run_id: run.run_id, files }),
            publish: artifact => this.publish(run, 'delivery.created', { delivery: artifact }),
        }));
        disposers.push(agent.ctx.systemPrompt.section({ name: 'company:business-policy', order: -1000,
            complete: true, interpolate: false, text: `${run.system_prompt || '你是企业业务助手。'}\n依据真实工具回执报告结果；未生成的文件不得称已完成。数据库超时或结果不明时停止，不重复业务动作。` }));
        const allowed = new Set((run.tools || []).map(tool => (tool.function || tool).name));
        current.businessTools = allowed;
        for (const item of this.state.description.skills || []) {
            if ((item.tools || []).some(name => !allowed.has(name))) continue;
            const path = resolve(this.state.projectRoot, item.path);
            const content = await readFile(path, 'utf8');
            disposers.push(agent.ctx.get('skills').register({ name: item.id, description: item.description || item.name,
                content, source: 'runtime', path, resourceBase: { kind: 'directory', path: dirname(path) } }));
        }
        for (const entry of run.tools || []) {
            const tool = entry.function || entry;
            disposers.push(agent.ctx.tools.register({ name: tool.name, description: tool.description || tool.name,
                parameters: tool.parameters || { type: 'object', properties: {} }, isConcurrencySafe: () => false,
                output: { schema: {}, render: (_, value) => [{ type: 'text', text: JSON.stringify(value) }] },
                execute: async (args, exec) => {
                    exec.signal.throwIfAborted();
                    if (current.active !== run || run.cancelRequested || run.receiptFailure || terminal.has(run.status)) throw new Error('该工具所属业务运行已结束');
                    const invocation = current.invocations.get(exec.callId);
                    if (!invocation || invocation.name !== tool.name) throw new Error('工具调用缺少原生事件身份');
                    const cancel = () => { this.models.cancel(run.run_id); void this.worker.request('run.cancel', { run_id: run.run_id }).catch(() => {}); };
                    let started = false, receivedResult = false;
                    const durable = this.state.description.receipt_protocol === 1;
                    try {
                        await this.publish(run, 'tool.started', { call_id: exec.callId, tool_name: tool.name, arguments: args,
                            agent_session_id: invocation.sessionId, invocation_seq: invocation.seq }, invocation.sessionId === current.id ? invocation.seq : undefined);
                        started = true;
                        // 等待开始回执期间可能已取消；已触发的 abort 不会补发给新监听，派发前必须再检查。
                        exec.signal.throwIfAborted();
                        if (current.active !== run || run.cancelRequested || run.receiptFailure || terminal.has(run.status)) throw new Error('业务运行已停止，不能继续派发工具');
                        exec.signal.addEventListener('abort', cancel, { once: true });
                        const response = await this.worker.request('tool.execute', { run_id: run.run_id, call_id: exec.callId,
                            invocation_seq: invocation.seq, name: tool.name, arguments: args,
                            ...(durable ? { durable_receipt: true, native_agent_session_id: invocation.sessionId } : {}) });
                        let result = response;
                        if (durable) {
                            const receipt = response?.receipt, owner = receipt?.identity;
                            if (response?.receipt_protocol !== 1 || response.state !== 'committed'
                                || !Number.isSafeInteger(receipt?.id) || receipt.id <= 0 || receipt.event !== 'tool.completed'
                                || owner?.run_id !== run.run_id || owner?.call_id !== exec.callId
                                || owner?.invocation_seq !== invocation.seq || owner?.native_agent_session_id !== invocation.sessionId
                                || owner?.session_id !== run.session_id || receipt.run_id !== run.run_id || receipt.session_id !== run.session_id
                                || receipt.data?.run_id !== run.run_id || receipt.data?.call_id !== exec.callId
                                || receipt.data?.message_id !== run.message_id || receipt.data?.session_id !== run.session_id
                                || !['execution', 'pre_dispatch_rejection'].includes(receipt.phase)
                                || receipt.data?.tool_name !== tool.name || !response.result
                                || JSON.stringify(receipt.data.result) !== JSON.stringify(response.result)) {
                                const error = new Error('工具结果缺少匹配的持久回执，已停止自动继续，请核实原操作。');
                                error.code = 'DSH_RECEIPT_UNCONFIRMED'; throw error;
                            }
                            result = response.result;
                        }
                        if (!result || typeof result !== 'object' || Array.isArray(result)) throw new Error('工具回执格式无效');
                        // 已取得结构化业务回执；原生失败、事件发布失败或随后取消都不能将它改写成无结构错误。
                        receivedResult = true;
                        if (durable) await this.publishReceipt(run, response.receipt);
                        else await this.publish(run, 'tool.completed', { call_id: exec.callId, tool_name: tool.name, result,
                            status: result.status || (result.isError ? 'failed' : 'success') });
                        exec.signal.throwIfAborted();
                        if (result.isError || (result.status && result.status !== 'success')) throw new Error(JSON.stringify(result));
                        return result;
                    } catch (error) {
                        if (durable && started && !receivedResult) {
                            // 回执未知时取消原生续推，不能让模型换调用标识重发；整轮仍走既有交付核验。
                            run.receiptFailure = { code: 'DSH_RECEIPT_UNCONFIRMED', message: error.message };
                            run.cancelRequested = true;
                            cancelOwnedJobs(this, current, run, '工具回执未核实，停止该运行后台任务');
                            current.agent?.cancel({ kind: 'parent' });
                            for (const [id, ownerRun] of this.agentRuns) if (ownerRun === run.run_id) this.ctx.agents.get(id)?.cancel({ kind: 'parent' });
                            current.abort?.abort(new Error('工具回执未核实'));
                            this.models.cancel(run.run_id);
                            void this.worker.request('run.cancel', { run_id: run.run_id }).catch(() => {});
                            throw error;
                        }
                        // 开始发布失败时尚未派发业务，不补造工具失败；已有结构回执仍遵守不二次覆盖。
                        if (started && !receivedResult) {
                            try { await this.publish(run, 'tool.failed', { call_id: exec.callId, tool_name: tool.name, error: error.message }); }
                            catch (publishError) {
                                // 副发布异常只进入诊断，原异常对象及其原生错误身份保持不变。
                                try { process.stderr.write(`DSH 工具失败回执发布失败 ${run.run_id}/${exec.callId}: ${publishError.stack || publishError}\n`); }
                                catch { /* 诊断通道失败也不能替换工具主异常。 */ }
                            }
                        }
                        throw error;
                    } finally { exec.signal.removeEventListener('abort', cancel); current.invocations.delete(exec.callId); }
                } }));
        }
    }
    async follow(current) {
        current.abort = new AbortController();
        let opened, rejected;
        const ready = new Promise((resolve, reject) => { opened = resolve; rejected = reject; });
        current.followTask = (async () => {
            try {
                for await (const frame of this.ctx.sessionController.follow({ address: { kind: 'session', sessionId: current.id },
                    assistantStream: true, maxMessages: 100 }, current.abort.signal)) {
                    const events = current.projection.accept(frame);
                    const run = current.active;
                    if (run && frame.type === 'event' && frame.event.type === 'deliverables/presented') {
                        const delivery = await this.worker.request('delivery.present', { run_id: run.run_id, files: frame.event.data.files });
                        for (const artifact of delivery.artifacts || []) await this.publish(run, 'delivery.created', { delivery: artifact });
                    }
                    if (run) for (const [event, data] of events) await this.publish(run, event, data, data.native_sequence);
                    if (run && frame.type === 'event' && frame.event.type === 'tool/call' && !current.businessTools?.has(frame.event.data.name)) {
                        const call = frame.event.data;
                        await this.publish(run, 'tool.started', { call_id: call.callId, tool_name: call.name, arguments: call.arguments, native: true }, frame.event.seq);
                    }
                    if (run && frame.type === 'event' && frame.event.type === 'tool/result') {
                        const result = frame.event.data.message, callId = result.source?.callId;
                        const invocation = current.invocations.get(callId);
                        if (invocation && !current.businessTools?.has(invocation.name)) {
                            await this.publish(run, result.isError ? 'tool.failed' : 'tool.completed', { call_id: callId, tool_name: invocation.name,
                                result: { content: result.content, error: frame.event.data.error }, status: result.isError ? 'failed' : 'success', native: true }, frame.event.seq);
                            current.invocations.delete(callId);
                        }
                    }
                    if (frame.type === 'snapshot') current.cursor = frame.cursor;
                    if (frame.type === 'event') current.cursor = frame.event.seq;
                    for (const waiter of current.waiters) if (current.cursor >= waiter.target) { current.waiters.delete(waiter); waiter.resolve(); }
                    opened();
                }
            } catch (error) {
                rejected(error);
                if (!current.abort.signal.aborted) this.failSession(current, error);
            }
        })();
        await ready;
    }
    failSession(current, error) {
        current.failure = error;
        current.agent?.cancel({ kind: 'hook', reason: '业务事件持久化失败' });
        for (const waiter of current.waiters) waiter.reject(error);
        current.waiters.clear();
    }
    delivered(current, target) {
        if (current.failure) return Promise.reject(current.failure);
        if (current.cursor >= target) return Promise.resolve();
        return new Promise((resolve, reject) => current.waiters.add({ target, resolve, reject }));
    }
    async nativeIdle(current, run) {
        // 原生后台子代理会在结束时唤醒父代理；等整个原生生命周期收尾后再结算业务运行。
        while (true) {
            await abortable(current.agent.whenIdle(), current.abort?.signal);
            const children = [...this.agentRuns].filter(([, id]) => id === run.run_id)
                .map(([id]) => this.childCompletions.get(id)?.done).filter(Boolean);
            if (children.length) await abortable(Promise.all(children), current.abort?.signal);
            await observeOwnedJobs(this, current, run);
            if (current.childDeliveries) await abortable(current.childDeliveries, current.abort?.signal);
            await new Promise(resolve => setImmediate(resolve));
            if (current.agent.status === 'idle' && ![...this.agentRuns.values()].includes(run.run_id)
                && !ownedActiveJobs(this, current, run).length) return;
        }
    }
    async execute(current, run) {
        let status = 'failed', failure;
        current.lastTurn = null; current.agentError = null;
        current.projection.text = ''; current.projection.committed = '';
        try {
            await this.queueChanged(current, run);
            const agent = await this.configure(current, run);
            if (run.cancelRequested) { status = 'cancelled'; return; }
            const startSeq = agent.session.seq;
            await this.publish(run, 'run.started', { status: 'running', task: run.task });
            await this.ctx.sessionController.prompt({ sessionId: current.id, requestId: run.request_id, content: run.content,
                mode: 'queue', clientTimeZone: 'Asia/Shanghai' }, current.abort.signal);
            if (run.cancelRequested) agent.cancel({ kind: 'user' });
            await this.nativeIdle(current, run);
            if (!await this.ctx.sessions.flush(agent.session)) throw new Error('原生会话未配置持久化');
            await this.delivered(current, agent.session.seq - 1);
            const reason = current.lastTurn?.seq >= startSeq ? current.lastTurn.data.reason : undefined;
            status = run.cancelRequested ? 'cancelled' : reason?.kind === 'completed' ? 'completed' : reason?.kind === 'aborted' ? 'cancelled' : 'failed';
            if (status === 'failed') failure = reason?.error || { code: reason?.kind || 'MISSING_TERMINAL', message: String(current.agentError || '原生任务没有成功终态') };
            if (run.receiptFailure) { status = 'interrupted'; failure = run.receiptFailure; }
        } catch (error) { failure = { code: error.code || 'DSH_EXECUTION_FAILED', message: error.message || String(error) }; }
        finally {
            if (run.receiptFailure) { status = 'interrupted'; failure = run.receiptFailure; }
            try { await this.settle(run, status, current.projection.committed || '', failure); }
            finally { run.modelLease?.release(); delete run.modelLease; }
        }
    }
    async settle(run, status, content, error) {
        let result;
        try {
            result = await this.worker.request('chat.settle', { run_id: run.run_id, status, content, error, usage: run.usage });
        } catch (settleError) {
            // 结算 RPC 失败时也必须给前端一个明确终态，避免消息永远停留在“运行中”。
            const fallbackStatus = ['completed', 'partial'].includes(status) ? 'failed' : status;
            const fallbackError = { code: 'DSH_SETTLE_FAILED', message: '业务运行结束状态未写入存储，请稍后从会话历史恢复。' };
            run.status = fallbackStatus;
            run.result = { run_id: run.run_id, status: fallbackStatus, content: content || '', error: fallbackError };
            process.stderr.write(`DSH 运行结算失败 ${run.run_id}: ${settleError.stack || settleError}\n`);
            const fallbackEvent = fallbackStatus === 'cancelled' ? 'run.cancelled'
                : ['failed', 'interrupted'].includes(fallbackStatus) ? 'run.failed' : 'run.completed';
            try { await this.publish(run, fallbackEvent, { status: fallbackStatus, content: content || '', error: fallbackError, usage: run.usage }); }
            catch (publishError) { process.stderr.write(`DSH 结算失败回执发布失败 ${run.run_id}: ${publishError.stack || publishError}\n`); }
            try { await this.publish(run, 'done', { status: fallbackStatus }); }
            catch (publishError) { process.stderr.write(`DSH 结算结束回执发布失败 ${run.run_id}: ${publishError.stack || publishError}\n`); }
            this.emit(`settled:${run.run_id}`, run.result);
            return run.result;
        }
        const canonicalContent = result.content ?? content;
        run.result = { ...result, content: canonicalContent };
        run.status = result.status || status;
        // 单条辅助事件失败不能阻止最终 done；Worker 已经写入结算后，前端仍应看到可恢复的终态。
        const publishSafe = async (event, data) => {
            try { return await this.publish(run, event, data); }
            catch (publishError) {
                process.stderr.write(`DSH 结算事件发布失败 ${run.run_id}/${event}: ${publishError.stack || publishError}\n`);
                return null;
            }
        };
        if (result.task) await publishSafe('task.updated', { task: result.task });
        for (const delivery of result.deliveries || []) await publishSafe('delivery.created', { delivery });
        const event = run.status === 'failed' || run.status === 'interrupted' ? 'run.failed' : run.status === 'cancelled' ? 'run.cancelled' : 'run.completed';
        await publishSafe(event, { status: run.status, content: canonicalContent, error: result.error || error, usage: run.usage, task: result.task });
        await publishSafe('done', { status: run.status });
        this.emit(`settled:${run.run_id}`, run.result);
        return run.result;
    }
    async cancel(runId) {
        const run = this.runs.get(runId);
        if (!run || terminal.has(run.status)) return { stopped: true, run_id: runId };
        const current = this.sessions.get(run.session_id);
        run.cancelRequested = true;
        this.models.cancel(runId);
        await this.worker.request('run.cancel', { run_id: runId });
        if (current.active === run) {
            cancelOwnedJobs(this, current, run, '用户停止当前业务运行');
            current.agent?.cancel({ kind: 'user' });
            for (const [id, ownerRun] of this.agentRuns) if (ownerRun === runId) this.ctx.agents.get(id)?.cancel({ kind: 'parent' });
            await run.execution;
        } else {
            current.queue = current.queue.filter(item => item !== run);
            await this.settle(run, 'cancelled', '', undefined);
            await this.queueChanged(current, run);
        }
        return { stopped: true, run_id: runId, status: run.status };
    }
    async updateQueue(runId, action, content) {
        const run = this.runs.get(runId);
        if (!run || run.status !== 'queued') { const error = new Error('消息已开始执行或不在队列中'); error.status = 409; throw error; }
        if (action === 'remove') return this.cancel(runId);
        if (action !== 'edit') { const error = new Error('队列操作仅支持 edit/remove；运行中补充请使用发送接口的 steer'); error.status = 422; throw error; }
        if (typeof content !== 'string' || !content.trim()) { const error = new Error('编辑内容不能为空'); error.status = 422; throw error; }
        if (run.updating || run.cancelRequested) { const error = new Error('消息正在更新或移除，请等待当前操作完成'); error.status = 409; throw error; }
        run.updating = true;
        try {
            const prepared = await this.worker.request('chat.queue.update', { run_id: runId, action, content });
            // 编辑 RPC 与取消可以交错完成，迟到的旧回执不能把终态改回排队中。
            if (run.cancelRequested || run.status !== 'queued') { const error = new Error('消息已开始执行或已被移除'); error.status = 409; throw error; }
            if (prepared.run_id !== run.run_id || prepared.session_id !== run.session_id || !prepared.model || !Array.isArray(prepared.content)) {
                throw new Error('队列编辑未返回完整的新执行上下文');
            }
            Object.assign(run, prepared);
            await this.queueChanged(this.sessions.get(run.session_id), run, { task: run.task,
                user_message: prepared.user_message || { id: run.user_message_id, run_id: run.run_id, role: 'user',
                    content: textOf(run.content), request_id: run.client_request_id || run.request_id } });
        } finally { run.updating = false; this.pump(); }
        return { updated: true, run_id: runId };
    }
    async archive(sessionId) {
        const current = this.sessions.get(sessionId);
        if (!current) return;
        current.archiving = true;
        await Promise.all([...current.queue.map(run => run.run_id), ...(current.active ? [current.active.run_id] : [])].map(id => this.cancel(id)));
    }
    async recover() {
        const recovered = await this.worker.request('chat.recover', {});
        for (const run of recovered.runs || []) {
            const current = this.stateFor(run);
            this.runs.set(run.run_id, run);
            let events = [];
            try { events = (await this.ctx.sessionController.inspect(run.session_id, new AbortController().signal)).events; }
            catch (error) { if (!String(error.code || error).includes('not-found') && !String(error).includes('not found')) throw error; }
            const window = recoveryWindow(events, run);
            if (window.index < 0) {
                // 原生日志丢失不能推翻业务库中的派发事实；仅尚未派发的任务可重新入队。
                if (run.tool_dispatches?.has_execution_trace) {
                    await this.settle(run, 'interrupted', '', { code: 'NATIVE_HISTORY_MISSING',
                        message: '原生记录缺失，但业务存储已有执行痕迹；已阻止自动重做，请核实此前结果。' });
                    await this.queueChanged(current, run);
                } else {
                    run.duplicate = false; this.runs.delete(run.run_id); await this.admit(run);
                }
                continue;
            }
            const remaining = window.events;
            const boundary = remaining.findLastIndex(event => event.type === 'turn/end');
            const end = remaining[boundary];
            // 原生后台委派可能跨多个父轮次；未落业务结算回执的崩溃窗口不能仅凭父轮次完成断言全部完成。
            const backgroundDelegation = remaining.some(event => event.type === 'tool/call' && (
                (['subagent', 'subagent_fork'].includes(event.data.name) && event.data.arguments?.run_in_background !== false)
                || (['workflow', 'subagent_codex', 'subagent_claude_code'].includes(event.data.name) && event.data.arguments?.run_in_background === true)
                // 命令即使未声明后台，也可能在超时后由原生执行器提升为 Job。
                || ['bash', 'pwsh'].includes(event.data.name)
            ));
            const unfinishedTurn = remaining.slice(boundary + 1).some(event => event.type === 'turn/start');
            const content = textOf(remaining.filter(event => event.type === 'assistant/message').at(-1)?.data.message?.content);
            const status = window.uncertain || backgroundDelegation || unfinishedTurn ? 'interrupted'
                : end?.data.reason?.kind === 'completed' ? 'completed' : end?.data.reason?.kind === 'aborted' ? 'cancelled' : 'interrupted';
            await this.settle(run, status, content, status === 'interrupted' ? {
                code: window.uncertain ? 'NATIVE_REQUEST_UNCONFIRMED' : 'HOST_INTERRUPTED',
                message: window.uncertain ? '无法确认原生消息属于本次运行；已保留本轮已确认的正文，未采用其他请求的结果或自动重做操作。'
                    : '宿主中断了此前任务；已保留会话记录，请确认后继续，未自动重复业务操作。' } : undefined);
            await this.queueChanged(current, run);
        }
    }
    async close() {
        this.accepting = false;
        this.models.close();
        for (const current of this.sessions.values()) {
            if (current.active) {
                current.active.cancelRequested = true;
                void this.worker.request('run.cancel', { run_id: current.active.run_id }).catch(() => {});
                cancelOwnedJobs(this, current, current.active, '宿主正在关闭');
                current.agent?.cancel({ kind: 'disposed' });
                for (const [id, ownerRun] of this.agentRuns) if (ownerRun === current.active.run_id) this.ctx.agents.get(id)?.cancel({ kind: 'parent' });
                // 先解除原生等待，再等待 execution，避免 whenIdle 或后台观察永远阻塞宿主关闭。
                current.abort?.abort();
            }
        }
        await Promise.allSettled([...this.sessions.values()].map(current => current.active?.execution));
        await Promise.allSettled([...this.sessions.values()].map(current => current.followTask));
    }
}
