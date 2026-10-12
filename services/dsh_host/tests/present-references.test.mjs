import '../../../tests/dsh/local_network_only.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { upstream } from '../models.mjs';
import { installArtifactPresent } from '../present-references.mjs';

test('业务工件下载链接复用卡片，本地文件仍经过原生 present 且不绕过拒绝策略', async t => {
    const root = resolve(fileURLToPath(new URL('../../..', import.meta.url)));
    const upstreamRoot = resolve(process.env.DSH_UPSTREAM_ROOT || `${root}/.implementation/harness_migration/upstream`);
    const [{ Context }, { default: ToolRuntime }, { default: SystemPrompt }] = await Promise.all([
        upstream(upstreamRoot, 'vendor/cordis'), upstream(upstreamRoot, 'packages/core/tools'),
        upstream(upstreamRoot, 'packages/core/system-prompt'),
    ]);
    const ctx = new Context();
    t.after(() => ctx.fiber.dispose());
    await ctx.plugin(SystemPrompt);
    await ctx.plugin(ToolRuntime);
    let localCalls = 0, referenceCalls = 0, published = 0;
    let active = true;
    ctx.tools.register({ name: 'present', description: '原生协议夹具', parameters: { type: 'object', properties: {
        files: { type: 'array', items: { type: 'object', properties: { path: { type: 'string' } }, required: ['path'] } },
    }, required: ['files'] }, output: { schema: { type: 'object', additionalProperties: false, properties: {
        turn: { type: 'integer' }, files: { type: 'array' },
    }, required: ['turn', 'files'] }, render: (_, value) => [{ type: 'text', text: JSON.stringify(value) }] },
    execute: async args => { localCalls++; return { turn: 1, files: args.files }; } });
    installArtifactPresent(ctx, { active: () => active, boundary: () => ({ openTurnStartSeq: 1, lastTurn: 1 }),
        reference: async files => { referenceCalls++; return { artifacts: files.map(file => ({ id: 'existing', download_url: file.path })) }; },
        publish: async () => { published++; },
    });
    const exec = files => ctx.tools.execute({ name: 'present', callId: `present-${localCalls}-${referenceCalls}`,
        arguments: { files }, signal: new AbortController().signal });
    const linked = [{ path: '/api/artifacts/existing/download' }];
    const referenced = await exec(linked);
    assert.equal(referenced.isError, false, JSON.stringify(referenced));
    assert.deepEqual(referenced.value, { turn: 1, files: linked }, '适配仍符合原生工具输出协议');
    assert.equal(JSON.parse(referenced.content[0].text).already_presented, true);
    assert.equal(localCalls, 0); assert.equal(referenceCalls, 1); assert.equal(published, 1);
    assert.equal((await exec([{ path: 'report.txt' }])).isError, false);
    assert.equal(localCalls, 1, '本地文件必须保留原生执行能力');
    const mixed = await exec([...linked, { path: 'report.txt' }]);
    assert.equal(mixed.isError, true);
    assert.equal(localCalls, 1); assert.equal(referenceCalls, 1, '混合引用需在任何写入前明确纠正');
    active = false;
    assert.equal((await exec(linked)).isError, true);
    active = true;
    const refuse = ctx.on('tools/pre-execute', async () => ({ kind: 'deny', reason: '测试拒绝' }));
    assert.equal((await exec(linked)).isError, true);
    assert.equal(referenceCalls, 1, '适配不能绕过原生工具前置拒绝');
    refuse();
});
