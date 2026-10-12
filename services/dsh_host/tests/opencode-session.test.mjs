import test from 'node:test';
import assert from 'node:assert/strict';
import { ModelRoutes } from '../models.mjs';

/** 仅核对宿主生成的配置，不访问模型端点或数据库。 */
function routes(llm = {}) {
    return new ModelRoutes({ emit() {}, llm: { async resolveModelInfo() {}, ...llm } }, { keys: new Map() });
}
const model = { protocol: 'openai-completions', baseURL: 'https://opencode.ai/zen/go/v1',
    model: 'deepseek-v4.1-flash', apiKey: 'fixture', contextWindow: 16384, maxTokens: 512 };

test('OpenCode 同一会话保持路由标识，其他会话和工作区分别隔离', async () => {
    const host = routes();
    const first = await host.ensure(model, 'workspace-a', 'session-a');
    assert.equal(await host.ensure(model, 'workspace-a', 'session-a'), first);
    const second = await host.ensure(model, 'workspace-a', 'session-b');
    const other = await host.ensure(model, 'workspace-b', 'session-a');
    const ids = [first, second, other].map(provider => host.profiles[provider].headers['x-opencode-session']);
    assert.equal(new Set(ids).size, 3);
    assert.ok(ids.every(id => /^[a-f0-9]{64}$/.test(id)));
    assert.equal((await routes().ensure(model, 'workspace-a', 'session-a')), first);
});

test('本机 OpenCode 代理也收到会话头；其他提供商保持原有配置', async () => {
    const host = routes();
    const proxy = await host.ensure({ ...model, baseURL: `http://127.0.0.1:${process.env.OPCODE_PROXY_PORT || 8901}/v1` }, 'workspace', 'session');
    assert.ok(host.profiles[proxy].headers['x-opencode-session']);
    const otherModel = { ...model, baseURL: 'https://example.invalid/v1' };
    const a = await host.ensure(otherModel, 'workspace', 'one');
    const b = await host.ensure(otherModel, 'workspace', 'two');
    assert.equal(a, b);
    assert.equal(host.profiles[a].headers, undefined);
    await assert.rejects(host.ensure(model, 'workspace'), /缺少会话标识/);
});

function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}

test('并发初始化同一路由只注册一次，初始化占位和租约共同守住容量', async () => {
    const entered = deferred(), ready = deferred();
    let resolved = 0;
    const host = routes({ async resolveModelInfo() { resolved++; entered.resolve(); await ready.promise; } });
    host.maxRoutes = 1;
    const first = host.acquire(model, 'workspace', 'session');
    const second = host.acquire(model, 'workspace', 'session');
    await entered.promise;
    assert.equal(resolved, 1);
    assert.equal(host.routes.size, 1);
    assert.equal(host.state.keys.size, 1);
    await assert.rejects(host.ensure({ ...model, model: 'other-model' }, 'workspace', 'session'), { code: 'MODEL_ROUTE_LIMIT' });
    ready.resolve();
    const [left, right] = await Promise.all([first, second]);
    assert.equal(left.provider, right.provider);
    assert.equal(host.routes.get(left.provider).active, 2);
    left.release(); left.release();
    await assert.rejects(host.ensure({ ...model, model: 'other-model' }, 'workspace', 'session'), { code: 'MODEL_ROUTE_LIMIT' });
    right.release();
    const other = await host.ensure({ ...model, model: 'other-model' }, 'workspace', 'session');
    assert.notEqual(left.provider, other);
    assert.equal(host.state.keys.size, 1);
    host.close();
});

test('初始化失败移除占位和凭据，下一次相同配置可以重新注册', async () => {
    let attempts = 0;
    const host = routes({ async resolveModelInfo() { if (++attempts === 1) throw new Error('注册失败'); } });
    const results = await Promise.allSettled([host.acquire(model, 'workspace', 'session'), host.ensure(model, 'workspace', 'session')]);
    assert.ok(results.every(result => result.status === 'rejected'));
    assert.equal(attempts, 1);
    assert.equal(host.routes.size, 0);
    assert.equal(host.state.keys.size, 0);
    assert.deepEqual(host.profiles, {});
    const lease = await host.acquire(model, 'workspace', 'session');
    assert.equal(attempts, 2);
    lease.release(); host.close();
});

for (const protocol of ['openai-completions', 'deepseek-messages']) {
    test(`关闭发生在初始化期间不允许迟到的路由复活：${protocol}`, async () => {
        const entered = deferred(), ready = deferred();
        let registrations = 0, disposals = 0;
        const host = routes({
            async resolveModelInfo() { entered.resolve(); await ready.promise; },
            registerAdapter() { registrations++; return () => { disposals++; }; },
        });
        host.deepseek = { resolveAdapterOptions: value => value, DeepSeekAdapter: class {} };
        const config = { ...model, protocol };
        const first = host.acquire(config, 'workspace', 'session');
        const second = host.acquire(config, 'workspace', 'session');
        await entered.promise;
        host.close(); host.close(); ready.resolve();
        const results = await Promise.allSettled([first, second]);
        assert.ok(results.every(result => result.status === 'rejected' && result.reason.code === 'MODEL_ROUTES_CLOSED'));
        assert.equal(host.routes.size, 0);
        assert.equal(host.state.keys.size, 0);
        assert.equal(host.registrations.size, 0);
        assert.deepEqual(host.profiles, {});
        assert.equal(registrations, protocol === 'deepseek-messages' ? 1 : 0);
        assert.equal(disposals, registrations);
        await assert.rejects(host.ensure(config, 'workspace', 'session'), { code: 'MODEL_ROUTES_CLOSED' });
    });
}
