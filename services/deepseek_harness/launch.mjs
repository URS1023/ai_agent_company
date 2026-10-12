import { mkdir, readFile, writeFile, access } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { join, resolve, isAbsolute } from 'node:path';
import { JsonLineRpc } from './protocol.mjs';
import { companyProfile } from './profile.mjs';

const serviceRoot = fileURLToPath(new URL('.', import.meta.url));
const upstreamRoot = resolve(process.env.DSH_UPSTREAM_ROOT || join(serviceRoot, '../../.implementation/harness_migration/upstream'));
const rpc = new JsonLineRpc();
// 标准输出只承载 RPC。上游诊断输出与协议分开，避免破坏流式消息。
process.stdout.write = process.stderr.write.bind(process.stderr);
let initializeId = null;
try {
    const first = await rpc.first;
    initializeId = first.id;
    if (first.method !== 'initialize' || initializeId === undefined) throw new Error('第一条消息必须是 initialize 请求');
    const init = first.params;
    if (!init?.sessionId || !/^[A-Za-z0-9._-]{1,160}$/.test(init.sessionId)) throw new Error('无效的会话标识');
    if (!process.env.DSH_HOME || !isAbsolute(process.env.DSH_HOME)) throw new Error('必须为每个会话提供绝对路径 DSH_HOME');
    if (!init.cwd || !isAbsolute(init.cwd)) throw new Error('必须提供绝对会话工作目录');
    const model = init.model;
    if (!model?.model || !model.baseURL || !model.apiKey) throw new Error('模型名称、地址与密钥必填');
    for (const key of ['contextWindow', 'maxTokens']) {
        if (!Number.isSafeInteger(model[key]) || model[key] <= 0) throw new Error(`模型 ${key} 必须是正整数`);
    }
    if (model.maxTokens >= model.contextWindow) throw new Error('模型上下文窗口必须大于输出预算');
    const endpoint = new URL(model.baseURL);
    if (!['http:', 'https:'].includes(endpoint.protocol) || endpoint.username || endpoint.password) throw new Error('模型地址无效');
    const manifest = JSON.parse(await readFile(join(upstreamRoot, 'package.json'), 'utf8'));
    if (manifest.version !== '0.1.7-alpha.2') throw new Error('上游版本与锁定版本不一致');
    const bin = join(upstreamRoot, 'apps/cli/lib/bin.js');
    await access(bin);
    await access(join(serviceRoot, 'lib/company-runtime.js'));
    const home = resolve(process.env.DSH_HOME);
    const profileDir = join(home, 'profiles/company-runtime');
    await mkdir(profileDir, { recursive: true });
    await mkdir(init.cwd, { recursive: true });
    // 会话专属 home 禁止加载额外覆盖层，否则不能保证白名单组合。
    try { await access(join(home, 'cordis.patch.yml')); throw new Error('独立 Harness home 中不允许用户覆盖层'); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    const { provider, patches } = companyProfile({ serviceRoot, home, cwd: init.cwd, model });
    await writeFile(join(profileDir, 'package.json'), JSON.stringify({ private: true, type: 'module', dsh: { profile: { bundles: [] } } }, null, 2));
    await writeFile(join(profileDir, 'cordis.patch.yml'), JSON.stringify(patches, null, 2));
    process.env.DSH_TELEMETRY_DISABLED = '1';
    process.env.COMPANY_MODEL_API_KEY = model.apiKey;
    delete process.env.DSH_TELEMETRY_OTLP_URL;
    delete process.env.DSH_TELEMETRY_MODE;
    globalThis[Symbol.for('company.harness.runtime')] = { init, rpc, initializeId, upstreamRoot, provider };
    process.chdir(init.cwd);
    process.argv = [process.execPath, bin, '--profile', 'company-runtime'];
    const { runCli } = await import(pathToFileURL(bin).href);
    await runCli();
} catch (error) {
    rpc.fail(initializeId, error);
    process.exitCode = 1;
    rpc.reader.close();
    process.stdin.destroy();
}
