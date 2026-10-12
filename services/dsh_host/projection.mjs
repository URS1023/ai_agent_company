/** 原生尝试与已提交消息分开，重试/压缩不得拼接失败尝试的正文。 */
export class NativeProjection {
    constructor() { this.cursor = -1; this.attempt = null; this.index = 0; this.text = ''; this.committed = ''; }
    accept(frame) {
        if (frame.type === 'snapshot') { this.cursor = frame.cursor; return []; }
        if (frame.type === 'assistant-stream') {
            const stream = frame.frame || frame.event || frame.stream || frame;
            if (stream.type === 'start' || stream.kind === 'start') {
                this.attempt = stream.attemptId; this.index = 0; this.text = '';
                return [['message.replace', { content: '', attempt_id: this.attempt, committed: false }],
                    ['reasoning.replace', { content: '', attempt_id: this.attempt }]];
            }
            if (stream.type === 'chunk' || stream.kind === 'chunk') {
                if (stream.attemptId !== this.attempt || stream.index < this.index) return [];
                if (stream.index !== this.index) throw new Error('原生消息流存在序号缺口');
                this.index++;
                const chunk = stream.chunk;
                if (chunk?.type === 'text-delta') {
                    this.text += chunk.text;
                    return [['message.delta', { delta: chunk.text, attempt_id: this.attempt }]];
                }
                if (chunk?.type === 'reasoning-delta') return [['reasoning.delta', { delta: chunk.text, attempt_id: this.attempt }]];
            }
            if ((stream.type === 'end' || stream.kind === 'end') && (stream.outcome?.kind === 'abandoned' || stream.disposition === 'abandoned')) {
                this.text = this.committed;
                return [['message.replace', { content: this.committed, committed: true }], ['reasoning.replace', { content: '', attempt_id: this.attempt }]];
            }
            return [];
        }
        if (frame.type !== 'event' || frame.event.seq <= this.cursor) return [];
        const event = frame.event;
        this.cursor = event.seq;
        if (event.type === 'todo/write') return [['todo.updated', { todos: event.data.todos, native_sequence: event.seq }]];
        if (event.type === 'deliverables/presented') return [['native.deliveries', { ...event.data, native_sequence: event.seq }]];
        if (event.type === 'assistant/message') {
            this.committed = this.text = (event.data.message?.content || event.data.content || []).filter(block => block.type === 'text').map(block => block.text).join('');
            return [['message.replace', { content: this.text, committed: true, native_sequence: event.seq }]];
        }
        if (event.type === 'assistant/attempt') {
            this.text = this.committed;
            return [['message.replace', { content: this.text, committed: true, native_sequence: event.seq }]];
        }
        if (event.type === 'compaction/end') return [[event.data.error ? 'task.progress' : 'context.compacted', {
            ...event.data, ...(event.data.error ? { phase: 'compaction', status: 'failed', message: '上下文压缩未完成' } : {}), native_sequence: event.seq }]];
        if (event.type === 'llm/retry') return [['task.progress', { phase: 'retry', ...event.data, native_sequence: event.seq }]];
        return [];
    }
}

export function usageView(usage) {
    if (!usage) return undefined;
    const input = (usage.inputTokens || 0) + (usage.cacheReadTokens || 0) + (usage.cacheWriteTokens || 0);
    return { prompt_tokens: input, completion_tokens: usage.outputTokens || 0,
        total_tokens: usage.totalTokens ?? input + (usage.outputTokens || 0),
        prompt_tokens_details: { cached_tokens: usage.cacheReadTokens || 0 } };
}
