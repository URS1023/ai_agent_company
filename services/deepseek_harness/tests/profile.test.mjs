import test from 'node:test';
import assert from 'node:assert/strict';
import { companyProfile } from '../profile.mjs';

const input = {
    serviceRoot: '/company/service', home: '/company/session', cwd: '/company/work',
    model: { protocol: 'openai-completions', baseURL: 'http://127.0.0.1:12345/v1', model: 'test',
        apiKey: 'secret-must-not-persist', contextWindow: 32768, maxTokens: 4096 },
};

test('企业 profile 不含上传与任意工具能力，凭据不落配置', () => {
    const profile = companyProfile(input);
    const rows = profile.patches[0].insert;
    const serialized = JSON.stringify(profile);
    assert.equal(serialized.includes(input.model.apiKey), false);
    for (const forbidden of ['session-log-deepseek', 'session-telemetry', 'plugin-package-inventory',
        'tool-bash', 'tool-pwsh', 'tool-fs', 'tool-web', 'agent-instructions', 'skill-filesystem', 'plugin-manager']) {
        assert.equal(rows.some(row => row.name.includes(forbidden)), false, forbidden);
    }
    assert.equal(rows.find(row => row.id === 'agent-loop').config.maxParallelToolCalls, 1);
    assert.equal(rows.find(row => row.id === 'agent-presets').config.default, 'company');
    const adapter = rows.find(row => row.id === 'llm-pi-ai').config.providers['company-gateway'];
    assert.equal(adapter.retryPolicy.maxRetries, 2);
    assert.equal(adapter.models[0].contextWindow, 32768);
    assert.equal(adapter.apiKeyEnv, 'COMPANY_MODEL_API_KEY');
    const compact = rows.find(row => row.id === 'company-compaction').config;
    assert.ok(compact.headroomTokens + input.model.maxTokens < input.model.contextWindow);
});

test('原生 DeepSeek 使用 Messages 适配器和显式思考策略', () => {
    const profile = companyProfile({ ...input, model: { ...input.model, protocol: 'deepseek-messages',
        baseURL: 'http://127.0.0.1:12345/anthropic', thinking: 'enabled' } });
    const rows = profile.patches[0].insert;
    assert.equal(rows.some(row => row.id === 'llm-pi-ai'), false);
    const adapter = rows.find(row => row.id === 'llm-deepseek');
    assert.equal(adapter.config.thinking, 'enabled');
    assert.equal(adapter.config.retryPolicy.maxRetries, 2);
    assert.equal(profile.provider, 'deepseek-official');
});

test('未知模型协议拒绝启动', () => {
    assert.throws(() => companyProfile({ ...input, model: { ...input.model, protocol: 'unknown' } }));
});
