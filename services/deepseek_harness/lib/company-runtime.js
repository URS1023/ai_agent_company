import { state, upstream } from './shared.js';

const { LlmError } = await upstream('packages/llm/llm');

export const name = 'company-runtime';
export const inject = ['appReady', 'appExit', 'sessionController', 'agents', 'sessions', 'agentPresets', 'llm', 'agentLoop'];

/** 仅转发上游公开会话 API，运行循环完全由 Harness 执行。 */
export function apply(ctx     )       {
    const { rpc, init } = state;
    const sessionId = init.sessionId;
    const lifetime = new AbortController();
    let follower                             ;
    let followTask                           ;
    let active     ;
    let lastTurnEnd     ;
    let agentError     ;
    let cursor = -1;
    let accepting = true;
    let followFailure         ;
    const completed = new Map             ();
    const cursorWaiters = new Set     ();

    function deliveredThrough(target        )                {
        if (followFailure) return Promise.reject(followFailure);
        if (cursor >= target) return Promise.resolve();
        return new Promise((resolve, reject) => { cursorWaiters.add({ target, resolve, reject }); });
    }

    function terminalOutcome(requestId        , terminal     , terminalCursor        )      {
        const status = terminal?.kind === 'completed' ? 'completed' : terminal?.kind === 'aborted' ? 'cancelled' : 'failed';
        return { sessionId, requestId, status, cursor: terminalCursor,
            ...(status === 'failed' ? { error: terminal?.error || {
                code: terminal?.kind || 'INTERRUPTED', message: '此前请求未成功完成；禁止自动重放已执行的业务操作',
            } } : {}) };
    }

    async function restoreReceipts()                {
        const historical = await ctx.sessionController.inspect(sessionId, lifetime.signal);
        const pending = new Set        ();
        for (const event of historical.events) {
            if (event.type === 'user/message' && event.data.source?.kind === 'user' && event.data.source.rpcId) {
                pending.add(event.data.source.rpcId);
            }
            if (event.type === 'turn/end') {
                for (const requestId of pending) completed.set(requestId, terminalOutcome(requestId, event.data.reason, event.seq));
                pending.clear();
            }
        }
        for (const requestId of pending) completed.set(requestId, terminalOutcome(requestId, undefined, historical.events.at(-1)?.seq ?? -1));
    }

    ctx.on('session/event', (session     , event     ) => {
        if (session.id !== sessionId) return;
        if (event.type === 'turn/end') lastTurnEnd = event;
    }, { global: true });
    ctx.on('agent/error', ({ agent, error }     ) => {
        if (agent.id === sessionId) agentError = error;
    }, { global: true });
    ctx.on('llm/stream', async function* (options     , next     ) {
        options.signal?.throwIfAborted();
        const permission = await rpc.request('model.authorize', {
            sessionId, purpose: options.purpose || 'conversation', provider: options.provider, model: options.model,
        });
        if (permission?.allowed !== true) throw new LlmError('平台拒绝模型调用或预算不足', 'COMPANY_AUTHORIZATION_DENIED');
        options.signal?.throwIfAborted();
        yield* next();
    }, { global: true });

    async function resolveAgent()               {
        const result = await ctx.sessionController.resolveAgent(sessionId);
        if (result.error) throw result.error;
        return result.agent;
    }

    function requireSession(params     )       {
        if (params.sessionId !== undefined && params.sessionId !== sessionId) throw new Error('运行进程不能访问其他会话');
    }

    async function follow()                {
        follower?.abort();
        if (followTask) await followTask;
        follower = new AbortController();
        const signal = follower.signal;
        let ready            ;
        let rejectReady                          ;
        const opened = new Promise      ((resolve, reject) => { ready = resolve; rejectReady = reject; });
        followTask = (async () => {
            try {
                for await (const frame of ctx.sessionController.follow({
                    address: { kind: 'session', sessionId }, assistantStream: true, maxMessages: 100,
                }, signal)) {
                    if (frame.type === 'snapshot') cursor = frame.cursor;
                    if (frame.type === 'event') cursor = frame.event.seq;
                    rpc.notify('session.frame', { sessionId, frame });
                    for (const waiter of cursorWaiters) {
                        if (cursor >= waiter.target) { cursorWaiters.delete(waiter); waiter.resolve(); }
                    }
                    ready ();
                }
            } catch (error) {
                rejectReady (error);
                if (!signal.aborted) {
                    accepting = false;
                    followFailure = error;
                    for (const waiter of cursorWaiters) waiter.reject(error);
                    cursorWaiters.clear();
                    rpc.notify('runtime.error', { sessionId, message: String(error) });
                    ctx.agents.get(sessionId)?.cancel({ kind: 'hook', reason: '事件传输失败' });
                }
            }
        })();
        await opened;
    }

    async function settle(run     , agent     )                {
        let outcome     ;
        try {
            await agent.whenIdle();
            const durable = await ctx.sessions.flush(agent.session);
            if (!durable) throw new Error('未加载会话持久化监听器');
            const terminal = lastTurnEnd?.seq >= run.startSeq ? lastTurnEnd.data.reason : undefined;
            let status = terminal?.kind === 'completed' ? 'completed' : terminal?.kind === 'aborted' ? 'cancelled' : 'failed';
            // 收到取消后仍按真正的持久终态报告，防止取消与完成竞态丢失结果。
            const error = terminal?.error || (status === 'failed' ? { code: terminal?.kind || 'MISSING_TERMINAL', message: String(agentError || '任务没有成功终态') } : undefined);
            await deliveredThrough(agent.session.seq - 1);
            outcome = { sessionId, requestId: run.requestId, status, cursor: agent.session.seq - 1,
                ...(error ? { error } : {}) };
        } catch (error) {
            accepting = false;
            outcome = { sessionId, requestId: run.requestId, status: 'failed', cursor,
                error: { code: 'PERSISTENCE_OR_SETTLEMENT_FAILED', message: String(error) } };
        }
        completed.set(run.requestId, outcome);
        if (active === run) active = undefined;
        rpc.notify('run.settled', outcome);
    }

    async function shutdown()               {
        accepting = false;
        if (active) {
            active.cancelRequested = true;
            await active.admission;
        }
        const agent = ctx.agents.get(sessionId);
        if (agent) {
            agent.cancel({ kind: 'disposed' });
            await agent.whenIdle();
            if (!await ctx.sessions.flush(agent.session)) throw new Error('关闭前无法确认持久化');
            if (followTask) await deliveredThrough(agent.session.seq - 1);
        }
        follower?.abort();
        await followTask;
        lifetime.abort();
        // 官方正常关闭允许事件循环自然退出；必须释放本适配层持有的 stdin。
        setImmediate(() => { rpc.closeInput(); ctx.appExit(0); });
        return { stopped: true };
    }

    rpc.handle(async (method        , params     ) => {
        requireSession(params);
        switch (method) {
            case 'session.create':
                return ctx.sessionController.create({ sessionId, cwd: init.cwd, agentPreset: 'company' });
            case 'session.prompt': {
                if (!accepting) throw new Error('运行时已停止接收任务');
                if (!params.requestId || typeof params.requestId !== 'string') throw new Error('缺少 requestId');
                const previous = completed.get(params.requestId);
                if (previous) {
                    setImmediate(() => rpc.notify('run.settled', previous));
                    return { accepted: true, duplicate: true };
                }
                if (active) {
                    if (active.requestId === params.requestId) return { accepted: true, duplicate: true };
                    throw new Error('同一会话只允许一个活动任务');
                }
                let admitted            ;
                const admission = new Promise      (resolve => { admitted = resolve; });
                const run = { requestId: params.requestId, startSeq: -1, cancelRequested: false, admission };
                active = run;
                try {
                    const agent = await resolveAgent();
                    if (run.cancelRequested) throw new Error('消息提交已取消');
                    agentError = undefined;
                    run.startSeq = agent.session.seq;
                    const receipt = await ctx.sessionController.prompt({
                        sessionId, requestId: params.requestId, content: params.content, mode: 'queue',
                        clientTimeZone: params.clientTimeZone || 'Asia/Shanghai',
                    }, lifetime.signal);
                    if (run.cancelRequested) agent.cancel({ kind: 'user' });
                    void settle(run, agent);
                    return receipt;
                } catch (error) {
                    if (active === run) active = undefined;
                    throw error;
                } finally {
                    admitted ();
                }
            }
            case 'session.cancel': {
                if (active) {
                    active.cancelRequested = true;
                    await active.admission;
                }
                const agent = await resolveAgent();
                agent.cancel({ kind: 'user' });
                await agent.whenIdle();
                if (!await ctx.sessions.flush(agent.session)) throw new Error('停止后无法确认持久化');
                return { stopped: true, cursor: agent.session.seq - 1 };
            }
            case 'session.follow':
                await follow();
                return { following: true, cursor };
            case 'session.page':
                return ctx.sessionController.page({
                    address: { kind: 'session', sessionId }, throughSeq: params.throughSeq ?? cursor,
                    beforeSeq: params.beforeSeq, maxMessages: Math.min(params.maxMessages || 50, 100),
                }, lifetime.signal);
            case 'health':
                return { ready: accepting, sessionId, activeRequestId: active?.requestId || null };
            case 'shutdown':
                return shutdown();
            default:
                throw new Error(`不支持的运行接口：${method}`);
        }
    });
    rpc.onEnd(() => { void shutdown().catch(() => ctx.appExit(1)); });
    ctx.effect(() => () => { lifetime.abort(); follower?.abort(); }, 'company-runtime: 停止事件订阅');
    ctx.appReady.onReady(() => {
        void (async () => {
            await ctx.agentPresets.resolve('company');
            if (init.resumeExpected) await ctx.sessionController.inspect(sessionId, lifetime.signal);
            const created = await ctx.sessionController.create({ sessionId, cwd: init.cwd, agentPreset: 'company' });
            await ctx.sessionController.selectModel({ sessionId, provider: state.provider, model: init.model.model,
                ...(init.model.reasoningEffort ? { reasoningEffort: init.model.reasoningEffort } : {}) });
            await restoreReceipts();
            await follow();
            if (!await ctx.sessions.flush((await resolveAgent()).session)) throw new Error('启动时无法确认会话持久化');
            rpc.respond(state.initializeId, { protocolVersion: 1, ...created, preset: 'company',
                modelProtocol: init.model.protocol, upstreamVersion: '0.1.7-alpha.2',
                disabledUploads: ['session-log', 'telemetry', 'plugin-inventory'], maxParallelToolCalls: 1 });
            rpc.activate();
        })().catch((error         ) => {
            rpc.fail(state.initializeId, error);
            rpc.closeInput();
            ctx.appExit(1);
        });
    });
}


//# sourceURL=company-runtime.ts