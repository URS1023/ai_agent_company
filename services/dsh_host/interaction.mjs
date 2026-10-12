import { randomUUID } from 'node:crypto';

const failure = (message, status = 422) => Object.assign(new Error(message), { status });

/** 原生问题的回答按问题标识校验；选项位置和页面默认选中不代表用户确认。 */
export function validateAnswers(questions, input) {
    if (!Array.isArray(input?.answers) || input.answers.length !== questions.length) throw failure('请回答本次全部问题');
    const answers = [], seen = new Set();
    for (const question of questions) {
        const answer = input.answers.find(item => item?.id === question.id);
        if (!answer || seen.has(answer.id) || !Array.isArray(answer.selected)) throw failure('问题回答格式无效');
        seen.add(answer.id);
        const selected = answer.selected, labels = new Set((question.options || []).map(option => option.label));
        const custom = typeof answer.custom === 'string' ? answer.custom.trim() : '';
        if (selected.some(value => typeof value !== 'string' || !labels.has(value)) || new Set(selected).size !== selected.length) throw failure('选择项不属于当前问题');
        if (!question.multiSelect && selected.length > 1) throw failure('本问题只能选择一项');
        if ((!selected.length && !custom) || custom.length > 8000) throw failure('回答不能为空或超过长度限制');
        answers.push({ id: question.id, selected: [...selected], ...(custom ? { custom } : {}) });
    }
    return { answers };
}

/** 将 DSH 原生交互请求接到企业会话流，等待真实回答后继续原生工具调用。 */
export class NativeQuestions {
    constructor(host) {
        this.host = host;
        this.pending = new Map();
        this.answered = new Map();
        host.ctx.on('user-questions/request', (request, next) => {
            const current = host.sessions.get(request.agent?.id);
            return current?.active ? this.ask(current, request) : next();
        }, { global: true });
        host.ctx.on('approval/request', async (request, next) => {
            const current = host.sessions.get(request.agent?.id);
            if (!current?.active) return next();
            const answer = await this.ask(current, { ...request, questions: [{ id: 'approval',
                question: request.reason || `是否允许执行 ${request.toolName}？`,
                detail: request.toolName, options: [{ label: '允许本次' }, { label: '拒绝' }] }] });
            return answer.answers[0]?.selected.includes('允许本次') ? 'allowed-once' : 'rejected';
        }, { global: true });
    }
    view(sessionId) {
        return [...this.pending.values()].filter(item => item.run.session_id === sessionId).map(item => item.view);
    }
    async ask(current, request) {
        request.signal?.throwIfAborted();
        const run = current.active, id = randomUUID();
        const questions = JSON.parse(JSON.stringify(request.questions));
        if (!questions.length || questions.length > 8 || new Set(questions.map(item => item.id)).size !== questions.length) throw failure('问题数量或标识无效');
        const view = { question_id: id, questions, status: 'pending', run_id: run.run_id, message_id: run.message_id };
        let resolve, reject;
        const result = new Promise((yes, no) => { resolve = yes; reject = no; });
        // 注册后发布再等待；提前挂拒绝处理，避免发布期间取消产生未处理 Promise。
        void result.catch(() => {});
        const entry = { run, view, questions, resolve, reject, signal: request.signal, settled: false };
        const abort = () => {
            if (entry.settled) return;
            entry.settled = true;
            this.pending.delete(id);
            reject(request.signal?.reason || new Error('问题已取消'));
            void this.host.publish(run, 'question.resolved', { question_id: id, status: 'cancelled' }).catch(error => this.host.failSession(current, error));
        };
        entry.abort = abort;
        this.pending.set(id, entry);
        request.signal?.addEventListener('abort', abort, { once: true });
        try {
            await this.host.publish(run, 'question.pending', view);
            if (request.signal?.aborted) abort();
            return await result;
        } finally {
            this.pending.delete(id);
            request.signal?.removeEventListener('abort', abort);
        }
    }
    async answer(sessionId, id, input) {
        const previous = this.answered.get(id);
        if (previous) {
            if (previous.sessionId !== sessionId) throw failure('问题不存在', 404);
            if (JSON.stringify(previous.answer) !== JSON.stringify(input)) throw failure('问题已经回答，不能修改旧回答', 409);
            return { answered: true, question_id: id, duplicate: true };
        }
        const entry = this.pending.get(id);
        if (!entry || entry.run.session_id !== sessionId) throw failure('问题已结束或不存在', 404);
        if (entry.settled || entry.answering || entry.run.cancelRequested || entry.signal?.aborted) throw failure('问题正在结束', 409);
        const answer = validateAnswers(entry.questions, input);
        // 持久化尚未完成时仍允许取消；回答锁只拦住重复提交，不能屏蔽停止信号。
        entry.answering = true;
        try {
            await this.host.publish(entry.run, 'question.resolved', { question_id: id, status: 'answered', ...answer });
            if (entry.settled || entry.run.cancelRequested || entry.signal?.aborted) {
                entry.abort();
                throw failure('问题已取消，回答未交给执行中的工具', 409);
            }
            entry.settled = true;
            this.pending.delete(id);
            this.answered.set(id, { sessionId, answer });
            if (this.answered.size > 100) this.answered.delete(this.answered.keys().next().value);
            entry.resolve(answer);
            return { answered: true, question_id: id };
        } catch (error) { entry.settled = true; entry.reject(error); throw error; }
        finally { entry.answering = false; }
    }
}
