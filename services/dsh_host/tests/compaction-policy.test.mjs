import '../../../tests/dsh/local_network_only.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { BUSINESS_COMPACTION_POLICY } from '../profile.mjs';
import { upstream } from '../models.mjs';

const root = resolve(fileURLToPath(new URL('../../..', import.meta.url)));
const upstreamRoot = resolve(process.env.DSH_UPSTREAM_ROOT || `${root}/.implementation/harness_migration/upstream`);
const target = { provider: 'fixture', model: 'fixture' };

/** 使用原生会话与计量器复现 53K 多步上下文；只替换摘要模型，不连接网络或数据库。 */
async function fixture(t, contextWindow = 131072, output = 16384, policy = BUSINESS_COMPACTION_POLICY) {
    const [{ Context }, llm, { default: Registry }, { default: Meter }, { Session }, { BasicCompactionEngine }] = await Promise.all([
        upstream(upstreamRoot, 'vendor/cordis'), upstream(upstreamRoot, 'packages/llm/llm'),
        upstream(upstreamRoot, 'packages/session/session-projection'), upstream(upstreamRoot, 'packages/llm/token-meter'),
        upstream(upstreamRoot, 'packages/core/session'), upstream(upstreamRoot, 'packages/compaction/compaction-basic'),
    ]);
    const ctx = new Context();
    t.after(() => ctx.fiber.dispose());
    new llm.LlmRuntime(ctx);
    new Registry(ctx);
    new Meter(ctx);
    class Adapter extends llm.LlmAdapter {
        async resolveModel(provider, model) { return { provider, id: model, name: model, context: { contextWindow } }; }
        async *stream() { throw new Error('此隔离测试禁止真实模型请求'); }
    }
    ctx.llm.registerAdapter(['fixture'], new Adapter());
    let summaries = 0;
    class SummaryStub extends BasicCompactionEngine {
        async summarize() {
            summaries += 1;
            return { summary: [{ type: 'text', text: '保留用户需求、成功查询回执与未完成交付。' }], ...target, maxTokens: this.config.maxTokens };
        }
    }
    const compact = new SummaryStub(ctx, { ...policy, auto: false });
    const session = Session.create('compaction-fixture');
    session.append('turn/start', { turn: 1 });
    session.append('request/header', { header: { config: { ...target, maxTokens: output } }, reason: 'initial' });
    const owner = { session, options: target };
    let index = 0;
    const append = () => {
        index += 1;
        session.append('user/message', llm.createUserMessage({ content: [{ type: 'text', text: `业务步骤 ${index} ` + 'record amount date source '.repeat(160) }],
            source: { kind: 'user' } }), { surfaceOp: 'append' });
    };
    return { compact, session, owner, append, measure: () => ctx.tokenMeter.measure(session).totalTokens, summaries: () => summaries };
}

test('旧默认预留在 128K 模型的约 53K 请求上触发压缩，复现实际会话根因', async t => {
    const f = await fixture(t, 131072, 16384, {});
    while (f.measure() < 53591) f.append();
    assert.equal(f.compact.config.headroomTokens, 65536);
    assert.ok(await f.compact.compactIfNeeded(f.owner, 'pressure'));
    assert.equal(f.summaries(), 1);
});

test('原生引擎在约 53K 上下文的连续步骤中不发起无效压缩，达到真正阈值后仍能压缩', async t => {
    const f = await fixture(t);
    while (f.measure() < 53591) f.append();
    assert.ok(f.measure() > 49152 && f.measure() < 104857);
    for (let step = 0; step < 27; step += 1) {
        f.append();
        assert.equal(await f.compact.compactIfNeeded(f.owner, 'pressure'), null);
    }
    assert.equal(f.summaries(), 0, '相同真实窗口下不再逐步触发摘要模型');
    while (f.measure() < 105000) f.append();
    const before = f.measure();
    const result = await f.compact.compactIfNeeded(f.owner, 'pressure');
    assert.ok(result && f.summaries() === 1, '保留原生长任务压缩能力');
    assert.ok(f.measure() < 104857 && f.measure() < before);
});

test('较小的 32K 模型在真实容量边界前仍进行原生压缩', async t => {
    const f = await fixture(t, 32768, 4096);
    while (f.measure() < 27000) f.append();
    assert.ok(await f.compact.compactIfNeeded(f.owner, 'pressure'));
    assert.equal(f.summaries(), 1);
    assert.ok(f.measure() < 26214);
});
