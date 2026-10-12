import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

// 原生默认会额外预留 64K，适用于超大窗口；企业模型按真实容量预留少量摘要空间。
export const BUSINESS_COMPACTION_POLICY = Object.freeze({
    thresholdRatio: 0.8, headroomTokens: 2048, retainRatio: 0.12, maxTokens: 2048,
    compactionRetries: 1, maxOverflowRetries: 1,
});

/** 直接继承固定版本的原生基础组合和标准预设，只覆盖企业部署的适配点。 */
export async function hostProfile({ serviceRoot, upstreamRoot, home }) {
    const local = name => pathToFileURL(join(serviceRoot, `${name}.mjs`)).href;
    const presetPath = join(upstreamRoot, 'packages/bundle/web-app/presets/standard.patch.yml');
    const standard = (await readFile(presetPath, 'utf8'))
        .replace('id: preset-standard', 'id: preset-company')
        .replace('id: standard', 'id: company')
        .replace('prefix: You are a coding agent powered by the {{model}} model.',
            'prefix: 你是使用 {{model}} 模型的企业业务助手。由你自主拆解和完成用户任务，业务工具只执行明确参数并返回真实结果。')
        // 业务技能由统一注册目录挂载；不自动混入部署者电脑上的开发、视频等全局技能。
        .replace("name: '@deepseek-ai/dsh-skill-filesystem'", "name: '@deepseek-ai/dsh-skill-filesystem'\n            disabled: true")
        .replace("name: '@deepseek-ai/dsh-compaction-basic'",
            `name: '@deepseek-ai/dsh-compaction-basic'\n                config: ${JSON.stringify(BUSINESS_COMPACTION_POLICY)}`);
    // 与上游 Web 的预设分层一致：工具由每个 Agent 的标准预设挂载，宿主保留共享服务。
    const scoped = ['tool-bash', 'tool-pwsh', 'tool-jobs', 'tool-fs', 'tool-fs-search', 'skill-filesystem',
        'tool-skill', 'command-goal', 'tool-goal', 'plan-mode', 'compaction-basic', 'command-compact',
        'tool-result-pruner', 'tool-subagent-control', 'tool-subagent-list-agents', 'tool-subagent',
        'tool-subagent-fork', 'workflow-ptc', 'tool-workflow', 'tool-ralph', 'agent-instructions', 'tool-todo', 'tool-web'];
    const patches = [
        ...scoped.map(id => ({ id, disabled: true })),
        { id: 'hmr', disabled: true },
        { id: 'session-title-llm', disabled: true },
        // 企业模型目录通过同一原生提供方插件注册租户配置，避免基础空配置重复注册提供方。
        { id: 'llm-pi-ai', disabled: true },
        { id: 'llm-deepseek', disabled: true },
        { id: 'agent-default-model', config: { provider: 'company-unconfigured', model: 'unconfigured' } },
        { id: 'credentials', disabled: true },
        { id: 'attachment-local', config: { dshHome: home } },
        { id: 'session-persistence-jsonl', config: { root: join(home, 'sessions'), compression: 'none' } },
        { id: 'storage-json', config: { root: join(home, 'storages') } },
        { id: 'spill-local', disabled: true },
        { id: 'spill-policy', config: { maxInlineTokens: 2048 } },
        { insert: [
            { id: 'business-credentials', name: local('credentials') },
            { id: 'business-spill', name: local('spill') },
            { id: 'workspace', name: '@deepseek-ai/dsh-workspace' },
            { id: 'connection', name: local('connection') },
            { id: 'uploads', name: '@deepseek-ai/dsh-client-file-upload' },
            { id: 'controller', name: '@deepseek-ai/dsh-api-session-controller', config: { nativeOpen: false } },
            { id: 'presets', name: '@deepseek-ai/dsh-agent-preset-registry', config: { default: 'company', modeSelectionEnabled: false } },
            { id: 'subagent-model-selection-settings', name: '@deepseek-ai/dsh-tool-subagent/model-selection-settings' },
            { id: 'http', name: '@deepseek-ai/dsh-host-webserver', config: { host: '127.0.0.1', port: 0, compression: 'none' } },
            { id: 'business-host', name: local('host') },
        ] },
    ];
    // 保留上游 YAML 条件表达式，由原生加载器处理，避免手抄插件配置逐渐偏离上游。
    return `${patches.map(row => `- ${JSON.stringify(row)}`).join('\n')}\n${standard}`;
}
