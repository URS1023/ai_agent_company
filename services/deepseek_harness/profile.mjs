import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

/** 独立白名单组合，不继承个人 profile、shell、技能扫描或上传插件。 */
export function companyProfile({ serviceRoot, home, cwd, model }) {
    const native = model.protocol === 'deepseek-messages';
    if (!native && model.protocol !== 'openai-completions') throw new Error('不支持的模型协议');
    const provider = native ? 'deepseek-official' : 'company-gateway';
    const plugin = name => pathToFileURL(join(serviceRoot, 'lib', `${name}.js`)).href;
    const row = (id, name, config) => ({ id, name: `@deepseek-ai/${name}`, ...(config ? { config } : {}) });
    const retryPolicy = { mode: 'normal', maxRetries: 2, backoff: { initialDelayMs: 250, maxDelayMs: 2000 } };
    const rows = [
        row('timer', 'cordis-plugin-timer'),
        row('llm', 'dsh-llm'),
        row('session', 'dsh-session'),
        row('typert', 'dsh-typert-registry'),
        row('session-projection', 'dsh-session-projection'),
        row('agent', 'dsh-agent'),
        row('agent-default-model', 'dsh-agent-default-model', { provider, model: model.model,
            ...(model.reasoningEffort ? { reasoningEffort: model.reasoningEffort } : {}) }),
        row('system-prompt', 'dsh-system-prompt', { includeHarnessIdentity: false, includeRuntimeContext: false }),
        row('tools', 'dsh-tools', { mode: 'native' }),
        row('agent-loop', 'dsh-agent-loop', { agents: [], maxParallelToolCalls: 1 }),
        row('credentials', 'dsh-credentials-local', { dshHome: home, watch: false }),
        row('attachment-local', 'dsh-attachment-local', { dshHome: home }),
        row('session-persistence-jsonl', 'dsh-session-persistence-jsonl', { root: join(home, 'sessions'), compression: 'none' }),
        row('session-query', 'dsh-session-query-sqlite', { path: ':memory:', openAt: 'never' }),
        row('storage', 'dsh-storage'),
        row('storage-json', 'dsh-storage-json', { root: join(home, 'storages') }),
        row('storage-domain', 'dsh-storage-domain', { backend: 'json' }),
        row('workspace', 'dsh-workspace'),
        row('fs', 'dsh-fs-local', { cwd }),
        row('commands', 'dsh-commands'),
        row('connection', 'dsh-client-connection'),
        row('file-uploads', 'dsh-client-file-upload'),
        row('session-controller', 'dsh-api-session-controller', { nativeOpen: false }),
        row('agent-presets', 'dsh-agent-preset-registry', { default: 'company', modeSelectionEnabled: false }),
        row('company-preset', 'dsh-agent-preset', { id: 'company', name: '企业业务助手', plugins: [
            { id: 'company-tools', name: plugin('company-preset') },
        ] }),
        row('llm-retry', 'dsh-llm-retry'),
        row('token-meter', 'dsh-token-meter'),
        row('checkpoint', 'dsh-session-checkpoint-policy'),
        { id: 'company-compaction', name: plugin('company-compaction'), config: {
            maxTokens: Math.min(model.maxTokens, 4096), maxOverflowRetries: 1,
            headroomTokens: Math.max(1, Math.min(4096, Math.floor((model.contextWindow - model.maxTokens) * 0.1))),
        } },
        { id: 'company-runtime', name: plugin('company-runtime') },
    ];
    if (native) {
        rows.push(row('llm-deepseek', 'dsh-llm-deepseek', {
            apiKeyEnv: 'COMPANY_MODEL_API_KEY', baseURL: model.baseURL,
            thinking: model.thinking || 'enabled', maxTokens: model.maxTokens,
            defaultContextWindow: model.contextWindow, retryPolicy,
            ...(model.reasoningEffort ? { reasoningEffort: model.reasoningEffort } : {}),
            models: [{ id: model.model, contextWindow: model.contextWindow, maxTokens: model.maxTokens,
                inputModalities: model.inputModalities || ['text'] }],
        }));
    } else {
        rows.push(row('llm-pi-ai', 'dsh-llm-pi-ai', { providers: { [provider]: {
            apiKeyEnv: 'COMPANY_MODEL_API_KEY', api: 'openai-completions', baseURL: model.baseURL, retryPolicy,
            compat: { maxTokensField: 'max_tokens', ...(model.compat || {}) },
            models: [{ id: model.model, name: model.model, contextWindow: model.contextWindow,
                maxTokens: model.maxTokens, input: model.inputModalities || ['text'],
                ...(model.reasoningEfforts !== undefined ? { reasoningEfforts: model.reasoningEfforts } : {}) }],
        } } }));
    }
    return { provider, patches: [{ insert: rows }] };
}
