/** 只调用当前 profile 组装函数，核验关闭思考开关是否进入模型配置。 */
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { companyProfile } from '../../services/deepseek_harness/profile.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url));
const input = JSON.parse(readFileSync(new URL('./adapter-boundary-evidence.json', import.meta.url), 'utf8'));
const common = { serviceRoot: resolve(root, 'services/deepseek_harness'), home: resolve(root, '.offline-fixture-home'), cwd: root };
const outputs = input.model_configuration.map(({ input: configured, python_to_node: model }) => {
    const profile = companyProfile({ ...common, model });
    const rows = profile.patches[0].insert;
    return { configured, defaultModel: rows.find(row => row.id === 'agent-default-model').config,
        provider: rows.find(row => row.id === 'llm-deepseek' || row.id === 'llm-pi-ai').config };
});
const openaiModel = input.model_configuration[2].python_to_node;
const withDisabled = companyProfile({ ...common, model: openaiModel });
const withoutSetting = companyProfile({ ...common, model: { ...openaiModel, thinking: undefined } });
assert.deepEqual(withDisabled, withoutSetting);
assert.equal(outputs[0].provider.thinking, 'enabled');
assert.equal(outputs[1].provider.thinking, 'disabled');
assert.equal(outputs[3].defaultModel.reasoningEffort, 'high');
const evidence = { execution: '纯 Node 配置组装；0 HTTP 请求、0 子进程、0 数据库',
    openaiDisabledEqualsUnspecified: true, cases: outputs };
writeFileSync(new URL('./profile-thinking-evidence.json', import.meta.url), JSON.stringify(evidence, null, 2));
console.log(JSON.stringify(evidence, null, 2));
