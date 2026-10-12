import '../../../tests/dsh/local_network_only.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { lstat, mkdir, mkdtemp, readFile, readdir, rename, rm, symlink, unlink, utimes, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { upstream } from '../models.mjs';

const root = resolve(fileURLToPath(new URL('../../..', import.meta.url)));
const upstreamRoot = resolve(process.env.DSH_UPSTREAM_ROOT || `${root}/.implementation/harness_migration/upstream`);

test('原生工具大结果保留结构化口径，定向读取保持会话隔离且不再次截断', async t => {
    const home = await mkdtemp(join(tmpdir(), 'company-dsh-spill-'));
    globalThis[Symbol.for('company.dsh.host')] = { upstreamRoot, home };
    const [{ Context }, { default: ToolRuntime }, { default: SystemPrompt }, SpillPolicy, { default: BusinessSpillStore }] = await Promise.all([
        upstream(upstreamRoot, 'vendor/cordis'), upstream(upstreamRoot, 'packages/core/tools'),
        upstream(upstreamRoot, 'packages/core/system-prompt'), upstream(upstreamRoot, 'packages/spill/spill-policy'), import('../spill.mjs'),
    ]);
    const ctx = new Context();
    t.after(async () => {
        await ctx.fiber.dispose();
        assert.ok(resolve(home).startsWith(resolve(tmpdir()) + '\\') || resolve(home).startsWith(resolve(tmpdir()) + '/'));
        await rm(home, { recursive: true, force: true });
    });
    await ctx.plugin(SystemPrompt);
    await ctx.plugin(ToolRuntime);
    await ctx.plugin(BusinessSpillStore);
    await ctx.plugin(SpillPolicy, { maxInlineTokens: 2048 });
    const exec = (name, args = {}, owner = 'owner-one') => ({ name, callId: `test-${name}`, arguments: args,
        agent: { id: owner, session: { header: { id: owner } } }, signal: new AbortController().signal });
    const value = { status: 'success', data: { resultType: 'alert_problem_list',
        summary: { returnedReports: 50, returnedProblems: 50, reportsMayHaveMore: true, scopeNote: '只代表当前返回页' },
        reports: Array.from({ length: 50 }, (_, index) => ({ id: `report-${index}`, deviceName: `设备${index}`, issues: index % 3,
            message: '无需重复读取的展示说明'.repeat(10) })),
        problems: [{ severity: 'critical', message: '液压 29.4 MPa', status: 'open' }],
        provenance: { source: '持久化报告，未实时检测', retrievedAt: '2026-09-26T00:00:00' },
        message: '重复展示正文😀'.repeat(5500) },
        artifacts: [{ id: 'artifact-one', name: '设备预警.xlsx', download_url: '/api/artifacts/artifact-one/download' }] };
    const raw = JSON.stringify(value);
    ctx.tools.register({ name: 'view_device_alerts', description: '离线回执', parameters: {},
        output: { schema: {}, render: (_, result) => [{ type: 'text', text: JSON.stringify(result) }] }, execute: async () => value });
    const result = await ctx.tools.execute(exec('view_device_alerts'));
    assert.equal(result.isError, false);
    assert.deepEqual(result.value, value, '概览不能替换业务原始值');
    const overview = JSON.parse(result.content[0].text);
    assert.equal(overview.format, 'structured_tool_result');
    assert.ok(result.content[0].text.length <= 7200);
    assert.deepEqual(overview.values['/data/summary'], value.data.summary);
    assert.deepEqual(overview.values['/data/provenance'], value.data.provenance);
    assert.deepEqual(overview.values['/artifacts'], value.artifacts);
    assert.equal(overview.collections.find(item => item.json_pointer === '/data/reports').returned_items, 50);
    assert.match(overview.notice, /样例不是全部记录/);
    assert.equal(overview.collections.find(item => item.json_pointer === '/data/reports').complete, false);
    assert.deepEqual(overview.collections.find(item => item.json_pointer === '/data/problems').items, value.data.problems);
    const { structuredOverview } = await import('../spill.mjs');
    const inventoryRows = Array.from({ length: 4 }, (_, index) => ({ 物料: `备件${index}`, 数量: index + 1, 安全库存: null, 单位: index % 2 ? '根' : '桶' }));
    const small = structuredOverview({ status: 'success', data: { queryId: 'query-one', totalCount: 4, truncated: false,
        statistics: { quantityTotal: null }, data: inventoryRows } }, 'test-reference', sourceForTest(), 9000);
    assert.equal(small.values['/data/queryId'], 'query-one');
    const inline = small.collections.find(item => item.json_pointer === '/data/data');
    assert.equal(inline.complete, true);
    assert.deepEqual(inline.items, inventoryRows);
    const long = structuredOverview({ data: { data: [{ text: '长'.repeat(9000) }] } }, 'test-reference', sourceForTest(), 10000);
    assert.ok(JSON.stringify(long).length <= 7200);
    assert.notEqual(long.collections.find(item => item.json_pointer === '/data/data').complete, true);
    const wideTables = Array.from({ length: 12 }, (_, index) => ({ tableName: `finance_${index}`, tableComment: `财务表${index}`,
        columns: Array.from({ length: 85 }, (_, column) => ({ columnName: `field_${column}`, comment: '真实业务字段说明'.repeat(3) })) }));
    const schema = structuredOverview({ data: { tables: wideTables,
        coverage: { catalogTableCount: 353, returnedTableCount: 12, complete: false } } }, 'schema-reference', sourceForTest(), 60000);
    const tableIndex = schema.collections.find(item => item.json_pointer === '/data/tables');
    assert.equal(tableIndex.index_complete, true, '宽表不能挤掉候选表名，避免反复读取整个schema');
    assert.equal(tableIndex.complete, false, '表索引完整不等于列信息或全库目录完整');
    assert.equal(tableIndex.index.length, 12);
    assert.equal(tableIndex.index[11].columns_pointer, '/data/tables/11/columns');
    assert.equal(tableIndex.index[11].columnCount, 85);
    assert.equal(schema.values['/data/coverage'].catalogTableCount, 353);
    assert.ok(JSON.stringify(schema).length <= 7200);
    const manyTables = Array.from({ length: 30 }, (_, index) => ({ tableName: `table_${index}`, tableComment: '业务说明'.repeat(30), columns: [] }));
    const multiple = structuredOverview({ tables: manyTables, data: { tables: manyTables,
        coverage: { catalogTableCount: 353, returnedTableCount: 30, complete: false } } }, 'multi-reference', sourceForTest(), 60000);
    assert.ok(JSON.stringify(multiple).length <= 7200, '多个表集合的索引也必须遵守摘要预算');
    assert.equal(multiple.values['/data/coverage'].catalogTableCount, 353, '索引不能挤掉覆盖范围证据');

    const store = ctx.get('spillStore');
    await store.cleanupTask;
    const schemaSource = { ...sourceForTest(), toolName: 'query_business_data' };
    const schemaRaw = { data: { tables: [
        { columns: [{ columnName: 'c_pamented', dataType: 'DECIMAL', comment: '已付金额（元）' }] },
        { columns: [{ columnName: 'c_bumen', dataType: 'VARCHAR', comment: '部门' }] },
    ] } };
    const savedSchema = await store.saveText({ owner: { sessionId: 'owner-one' }, source: schemaSource, content: JSON.stringify(schemaRaw) });
    const pointers = ['/data/tables/0/columns', '/data/tables/1/columns'];
    const batchResult = await ctx.tools.execute(exec('read_tool_result', { reference: savedSchema.locator,
        json_pointers: pointers, fields: ['columnName', 'columnComment'], limit: 24000 }));
    assert.equal(batchResult.isError, false);
    const batch = JSON.parse(batchResult.content[0].text);
    assert.equal(batch.next_read, null);
    assert.deepEqual(JSON.parse(batch.content), {
        [pointers[0]]: [{ columnName: 'c_pamented', columnComment: '已付金额（元）' }],
        [pointers[1]]: [{ columnName: 'c_bumen', columnComment: '部门' }],
    }, '一次读取多个列集合，columnComment 别名不能丢掉真实业务注释');
    const batchPage = await store.readPage('owner-one', savedSchema.locator, 0, 30, undefined, ['columnName'], undefined, pointers);
    assert.deepEqual(batchPage.next_read.json_pointers, pointers);
    await assert.rejects(store.readPage('owner-one', savedSchema.locator, 0, 30, undefined, ['columnName'], batchPage.next_cursor, pointers.slice(0, 1)), /修改.*fields/);
    await assert.rejects(store.readPage('owner-one', savedSchema.locator, 0, 30, '/data', undefined, undefined, pointers), /不能同时/);
    await assert.rejects(store.readPage('owner-one', savedSchema.locator, 0, 30, undefined, undefined, undefined, ['/data/constructor']), /不存在/);
    const before = await readdir(store.directory('owner-one'));
    const page = await ctx.tools.execute(exec('read_tool_result', { reference: overview.reference, limit: 24000 }));
    assert.equal(page.isError, false);
    const read = JSON.parse(page.content[0].text);
    assert.equal(Array.from(read.content).length, 24000);
    assert.equal(read.next_offset, 24000);
    assert.equal(read.source.toolName, 'view_device_alerts');
    assert.deepEqual(await readdir(store.directory('owner-one')), before, '一页读取结果不能产生第二层 spill');
    const defaultPage = await store.readPage('owner-one', overview.reference);
    assert.equal(Array.from(defaultPage.content).length, 12000);
    assert(defaultPage.next_read.cursor);
    const cursorPage = await store.readPage('owner-one', overview.reference, undefined, defaultPage.next_read.limit,
        undefined, undefined, defaultPage.next_read.cursor);
    assert.equal(cursorPage.offset, 12000);
    assert.equal(cursorPage.content, Array.from(raw).slice(12000, 24000).join(''));
    await assert.rejects(store.readPage('owner-one', overview.reference, undefined, 12000,
        '/data/reports', ['id'], defaultPage.next_read.cursor), /修改.*fields.*offset=0/);
    await assert.rejects(store.readPage('owner-one', overview.reference, 20000, 12000,
        '/data/reports', ['id']), /恢复参数.*offset.*0/);
    const restarted = await store.readPage('owner-one', overview.reference, 0, 12000, '/data/reports', ['id']);
    assert.equal(restarted.next_cursor, null);
    assert.equal(restarted.next_read, null);
    assert.equal(JSON.parse(restarted.content).length, 50);
    let complete = '', offset = 0;
    do {
        const part = await store.readPage('owner-one', overview.reference, offset, 24000);
        complete += part.content; offset = part.next_offset;
    } while (offset !== null);
    assert.equal(complete, raw, 'Unicode 分页必须无损重建原回执');

    const selection = await ctx.tools.execute(exec('read_tool_result', { reference: overview.reference,
        json_pointer: '/data/reports', fields: ['id', 'deviceName', 'issues'] }));
    assert.equal(selection.isError, false);
    const selected = JSON.parse(selection.content[0].text);
    assert.equal(selected.next_offset, null, '50 条结构化记录选列后应一次返回');
    assert.deepEqual(JSON.parse(selected.content), value.data.reports.map(({ id, deviceName, issues }) => ({ id, deviceName, issues })));
    assert.equal(selected.json_pointer, '/data/reports');
    await assert.rejects(store.readPage('other-session', overview.reference), /ENOENT/);
    await assert.rejects(store.readPage('owner-one', overview.reference, 0, 24001), /分页范围无效/);
    await assert.rejects(store.readPage('owner-one', overview.reference, raw.length + 1), /超过/);
    await assert.rejects(store.readPage('owner-one', overview.reference, 0, 100, '/data/constructor'), /不存在/);
    await assert.rejects(store.readPage('owner-one', overview.reference, 0, 100, '/data/~2'), /JSON Pointer/);
    await assert.rejects(store.readPage('owner-one', overview.reference, 0, 100, undefined, ['id']), /同时指定/);

    const blocked = ctx.on('tools/post-execute', async (execution, toolResult, next) => {
        if (execution.name === 'read_tool_result') return { kind: 'block', feedback: [{ type: 'text', text: '拒绝读取' }] };
        return next();
    });
    assert.equal((await ctx.tools.execute(exec('read_tool_result', { reference: overview.reference }))).isError, true,
        '分页保留钩子不能覆盖其他策略拒绝');
    blocked();

    await t.test('配额包含元数据和实际文件数，并发写入不能绕过上限', async () => {
        const content = '1234', source = sourceForTest();
        const receiptBytes = Buffer.byteLength(content) + Buffer.byteLength(JSON.stringify({ source, created_at: new Date().toISOString(), bytes: 4 }));
        await withEnv('DSH_SPILL_MAX_SESSION_BYTES', String(receiptBytes), async () => {
            const results = await Promise.allSettled([1, 2].map(() => store.saveText({ owner: { sessionId: 'owner-limit' }, source, content })));
            assert.equal(results.filter(item => item.status === 'fulfilled').length, 1);
            assert.match(String(results.find(item => item.status === 'rejected').reason), /存储已达到上限/);
            assert.equal((await store.sessionUsage(store.directory('owner-limit'))).bytes, receiptBytes);
        });
        await withEnv('DSH_SPILL_MAX_SESSION_FILES', '2', async () => {
            await store.saveText({ owner: { sessionId: 'owner-file-limit' }, source, content });
            await assert.rejects(store.saveText({ owner: { sessionId: 'owner-file-limit' }, source, content }), /存储已达到上限/);
            assert.equal((await readdir(store.directory('owner-file-limit'))).length, 2);
        });
        await withEnv('DSH_SPILL_MAX_ENTRY_BYTES', '100', async () => {
            await assert.rejects(store.saveText({ owner: { sessionId: 'owner-large-metadata' }, source: { label: '大'.repeat(100) }, content: '1' }), /单项存储上限/);
        });
    });
    await t.test('正文或元数据写入中断后回收本次创建的半份文件', async () => {
        const original = store.writeEntryFile;
        for (const extension of ['.txt', '.json']) {
            store.writeEntryFile = async function(path, content, created) {
                await original.call(this, path, content, created);
                if (path.endsWith(extension)) throw new Error('离线模拟磁盘写入中断');
            };
            try {
                await assert.rejects(store.saveText({ owner: { sessionId: 'owner-interrupted' }, source: sourceForTest(), content: '回执正文' }), /写入中断/);
                assert.deepEqual(await readdir(store.directory('owner-interrupted')), []);
            } finally { store.writeEntryFile = original; }
        }
        const recovered = await store.saveText({ owner: { sessionId: 'owner-interrupted' }, source: sourceForTest(), content: '恢复后的正文' });
        assert.equal((await store.readPage('owner-interrupted', recovered.locator)).content, '恢复后的正文');
    });
    await t.test('排队读取刷新使用时间，过期清理不能删除正在读取的会话', async () => {
        const session = 'owner-read-retention';
        const saved = await store.saveText({ owner: { sessionId: session }, source: sourceForTest(), content: '仍在使用' });
        const directory = store.directory(session), old = new Date(Date.now() - 8 * 24 * 60 * 60 * 1000);
        await utimes(directory, old, old);
        let release, entered;
        const ready = new Promise(resolve => { entered = resolve; });
        const gate = new Promise(resolve => { release = resolve; });
        const blocker = store.withSession(directory, async () => { entered(); await gate; });
        await ready;
        const reading = store.readPage(session, saved.locator);
        const original = store.withSession;
        let queued;
        const cleanupQueued = new Promise(resolve => { queued = resolve; });
        store.withSession = function(path, action) {
            const operation = original.call(this, path, action);
            if (path === directory) queued();
            return operation;
        };
        try {
            const cleanup = store.pruneExpired();
            await cleanupQueued;
            release();
            await blocker;
            assert.equal((await reading).content, '仍在使用');
            await cleanup;
        } finally { release(); store.withSession = original; }
        assert.ok((await lstat(directory)).isDirectory());
        assert.equal((await store.readPage(session, saved.locator)).content, '仍在使用');
    });
    await t.test('正在写入的过期会话与清理串行，回执不会写到一半被删除', async () => {
        const session = 'owner-write-retention';
        await store.saveText({ owner: { sessionId: session }, source: sourceForTest(), content: '旧正文' });
        const directory = store.directory(session), old = new Date(Date.now() - 8 * 24 * 60 * 60 * 1000);
        await utimes(directory, old, old);
        let release, entered;
        const ready = new Promise(resolve => { entered = resolve; });
        const gate = new Promise(resolve => { release = resolve; });
        const original = store.sessionUsage;
        store.sessionUsage = async function(path) {
            if (path === directory) { entered(); await gate; }
            return original.call(this, path);
        };
        const originalQueue = store.withSession;
        try {
            const writing = store.saveText({ owner: { sessionId: session }, source: sourceForTest(), content: '新正文' });
            await ready;
            let queued;
            const cleanupQueued = new Promise(resolve => { queued = resolve; });
            store.withSession = function(path, action) {
                const operation = originalQueue.call(this, path, action);
                if (path === directory) queued();
                return operation;
            };
            const cleanup = store.pruneExpired();
            await cleanupQueued;
            release();
            const saved = await writing;
            await cleanup;
            assert.equal((await store.readPage(session, saved.locator)).content, '新正文');
        } finally { release(); store.sessionUsage = original; store.withSession = originalQueue; }
    });
    await t.test('仅清理过期的正规会话目录，未知目录和子目录保留', async () => {
        const session = 'owner-expired';
        await store.saveText({ owner: { sessionId: session }, source: sourceForTest(), content: '过期正文' });
        const old = new Date(Date.now() - 8 * 24 * 60 * 60 * 1000);
        await utimes(store.directory(session), old, old);
        const unknown = join(store.root, 'operator-notes');
        await mkdir(unknown);
        await writeFile(join(unknown, 'important.txt'), '不能删除');
        await utimes(unknown, old, old);
        const nested = store.directory('owner-unknown-child');
        await mkdir(join(nested, 'keep'), { recursive: true });
        await utimes(nested, old, old);
        await store.pruneExpired();
        await assert.rejects(lstat(store.directory(session)), { code: 'ENOENT' });
        assert.equal(await readFile(join(unknown, 'important.txt'), 'utf8'), '不能删除');
        assert.ok((await lstat(join(nested, 'keep'))).isDirectory());
        await assert.rejects(store.withSession(home, async () => {}), /超出允许/);
    });
    await t.test('会话 junction 不被读取、写入或过期清理跟随', async () => {
        const outside = join(home, 'outside-session');
        await mkdir(outside);
        const id = '00000000-0000-0000-0000-000000000001';
        await writeFile(join(outside, `${id}.txt`), '外部正文');
        await writeFile(join(outside, `${id}.json`), JSON.stringify({ source: sourceForTest() }));
        const session = 'owner-junction', directory = store.directory(session);
        await symlink(outside, directory, process.platform === 'win32' ? 'junction' : 'dir');
        try {
            await assert.rejects(store.readPage(session, `company-tool-result:${id}`), /符号链接或 junction/);
            await assert.rejects(store.saveText({ owner: { sessionId: session }, source: sourceForTest(), content: '不能写到外部' }), /符号链接或 junction/);
            await store.pruneExpired(Date.now() + 8 * 24 * 60 * 60 * 1000);
            assert.equal(await readFile(join(outside, `${id}.txt`), 'utf8'), '外部正文');
            assert.equal((await readdir(outside)).length, 2);
        } finally { await unlink(directory); }
    });
    await t.test('tool-results 根目录 junction 不会导向其他目录', async () => {
        const backup = join(home, 'tool-results-original'), outside = join(home, 'outside-root');
        await mkdir(outside);
        await writeFile(join(outside, 'important.txt'), '外部保留');
        await rename(store.root, backup);
        try {
            await symlink(outside, store.root, process.platform === 'win32' ? 'junction' : 'dir');
            try {
                await assert.rejects(store.saveText({ owner: { sessionId: 'owner-root-junction' }, source: sourceForTest(), content: '不能写到外部' }), /符号链接或 junction/);
                await assert.rejects(store.pruneExpired(), /符号链接或 junction/);
                assert.deepEqual(await readdir(outside), ['important.txt']);
            } finally { await unlink(store.root); }
        } finally { await rename(backup, store.root); }
    });
});

function sourceForTest() { return { kind: 'tool', toolName: 'query_inventory_data', callId: 'inventory-test' }; }

async function withEnv(name, value, action) {
    const old = process.env[name];
    process.env[name] = value;
    try { return await action(); }
    finally { if (old === undefined) delete process.env[name]; else process.env[name] = old; }
}
