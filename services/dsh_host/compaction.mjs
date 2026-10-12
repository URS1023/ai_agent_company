import { upstream } from './models.mjs';
const state = globalThis[Symbol.for('company.dsh.host')];
const { BasicCompactionEngine } = await upstream(state.upstreamRoot, 'packages/compaction/compaction-basic');
const { BlockAssembler, LlmError } = await upstream(state.upstreamRoot, 'packages/llm/llm');

const instruction = `请将以上对话压缩为可继续处理企业业务的中文检查点，依次记录：
1. 用户目标、当前任务、task_id，以及已确认的交付要求。
2. 用户确认条件与禁止事项，特别是先分析、等待确认、暂不执行。
3. 业务口径、日期范围、币种、单位、关键数值与证据来源。
4. 数据源、文件、知识、图表、报告或PPT草稿的稳定资源标识。
5. 已完成操作与真实工具回执；失败、取消或结果未知的操作单独标记。
6. 未完成事项、异常与可执行下一步，不重复结果未知的业务操作。
合并已有检查点。只输出摘要，不调用工具，不编造文件或业务结果。`;

/** 业务摘要模板独立于原生压缩算法，持久化和保留区间仍由上游负责。 */
export default class BusinessCompaction extends BasicCompactionEngine {
    async summarize(input, agent, signal) {
        const selection = agent.session.requestHeader()?.config || agent.options;
        const info = await this.ctx.llm.resolveModelInfo(selection.provider, selection.model, signal);
        const maxTokens = Math.min(this.config.maxTokens, info.defaultMaxTokens || this.config.maxTokens);
        const assembler = new BlockAssembler();
        for await (const chunk of this.ctx.llm.stream({ provider: selection.provider, model: selection.model,
            sessionId: agent.id, purpose: 'compaction', signal, maxTokens,
            messages: [...input.messages, { role: 'user', content: [{ type: 'text', text: instruction }] }],
            ...(input.tools ? { tools: [...input.tools] } : {}) })) assembler.push(chunk);
        const finish = assembler.finish;
        if (finish.kind === 'error' || finish.kind === 'aborted') throw new LlmError(finish.failure.message, finish.failure.code);
        if (finish.kind === 'max-tokens') throw new Error('摘要达到输出上限，不能保存不完整业务检查点');
        const rawOutput = assembler.blocks(), summary = rawOutput.filter(block => block.type === 'text');
        if (rawOutput.some(block => ['tool-call', 'image', 'file'].includes(block.type))) throw new Error('业务摘要必须为完整文本，不能包含工具调用或文件块');
        if (!summary.some(block => block.text.trim())) throw new Error('业务检查点为空');
        return { summary, rawOutput, llmStreamCall: true, provider: selection.provider, model: selection.model, maxTokens,
            ...(assembler.usage ? { usage: assembler.usage } : {}) };
    }
}
