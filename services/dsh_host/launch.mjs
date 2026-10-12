import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { join, resolve } from 'node:path';
import { BusinessWorker } from './worker.mjs';
import { hostProfile } from './profile.mjs';

const serviceRoot = fileURLToPath(new URL('.', import.meta.url));
const projectRoot = resolve(process.env.DSH_PROJECT_ROOT || join(serviceRoot, '../..'));
const upstreamRoot = resolve(process.env.DSH_UPSTREAM_ROOT || join(projectRoot, '.implementation/harness_migration/upstream'));
const home = resolve(process.env.DSH_HOME || join(projectRoot, 'storage/dsh-host'));
/**
 * 使用短生命周期 Python 子进程完成 Worker 崩溃后的数据库收尾。
 * 子进程只接收本宿主曾登记的 run id，且 Python 侧以状态条件更新保证幂等。
 */
function recoverDshRuns(python, runIds) {
    const ids = [...new Set((runIds || []).filter(value => /^[A-Za-z0-9_-]{1,100}$/.test(String(value))).map(String))];
    if (!ids.length) return Promise.resolve({ recovered: [] });
    const script = [
        'import json, sys',
        'from app.db import new_session',
        'from app.models import Message, Run',
        'from app.core.parts import error_part',
        'from app.modules.runtime.service import touch_conversation',
        'from app.modules.runtime.chat_liveness import mark_interrupted',
        'run_ids = json.loads(sys.argv[1])',
        'db = new_session()',
        'recovered = []',
        'try:',
        '    for run_id in run_ids:',
        '        run = db.get(Run, run_id)',
        "        if run is None or run.status not in {'created', 'running'}:",
        '            continue',
        "        if (run.context_snapshot or {}).get('execution_owner') != 'dsh-host':",
        '            continue',
        "        if mark_interrupted(db, run, commit=False, error_code='dsh_worker_unavailable'):",
        "            assistant_id = (run.context_snapshot or {}).get('assistant_message_id')",
        '            assistant = db.get(Message, assistant_id) if assistant_id else None',
        "            if (assistant is not None and assistant.workspace_id == run.workspace_id and assistant.conversation_id == run.conversation_id and assistant.role == 'assistant' and assistant.status in {'created', 'queued', 'running', 'pending'}):",
        "                message = '本次处理已中断，已完成的结果仍然保留。'",
        "                assistant.status = 'failed'",
        '                assistant.content = assistant.content or message',
        "                assistant.parts_json = list(assistant.parts_json or []) + [error_part(message, 'dsh_worker_unavailable')]",
        '            touch_conversation(db, run.conversation_id)',
        '            recovered.append(run_id)',
        '    db.commit()',
        '    print(json.dumps({"recovered": recovered}, ensure_ascii=False))',
        'except Exception:',
        '    db.rollback()',
        '    raise',
        'finally:',
        '    db.close()',
    ].join('\n');
    const configuredTimeout = Number(process.env.DSH_RECOVERY_TIMEOUT_MS || 15000);
    const timeoutMs = Number.isFinite(configuredTimeout) && configuredTimeout > 0
        ? Math.max(1000, Math.min(Math.floor(configuredTimeout), 60000)) : 15000;
    return new Promise((resolve, reject) => {
        const child = spawn(python, ['-X', 'utf8', '-c', script, JSON.stringify(ids)], {
            cwd: join(projectRoot, 'backend'), env: { ...process.env, PYTHONUNBUFFERED: '1' },
            stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true,
        });
        let stdout = '', stderr = '';
        child.stdout.on('data', chunk => { stdout += chunk.toString('utf8').slice(0, 65536); });
        child.stderr.on('data', chunk => { stderr += chunk.toString('utf8').slice(0, 65536); });
        const timer = setTimeout(() => { child.kill(); reject(new Error('Worker 失效收尾超过允许时间')); }, timeoutMs);
        child.once('error', error => { clearTimeout(timer); reject(error); });
        child.once('exit', (code, signal) => {
            clearTimeout(timer);
            if (code !== 0) { reject(new Error(`Worker 失效收尾进程退出 (${code ?? signal})${stderr ? `：${stderr.trim()}` : ''}`)); return; }
            try {
                // 依赖导入可能写入诊断行，只解析子进程最后一行机器可读回执。
                const output = stdout.trim().split(/\r?\n/).filter(Boolean).at(-1) || '{"recovered":[]}';
                resolve(JSON.parse(output));
            }
            catch { reject(new Error('Worker 失效收尾返回了无效结果')); }
        });
    });
}
let worker;
try {
    const manifest = JSON.parse(await readFile(join(upstreamRoot, 'package.json'), 'utf8'));
    if (manifest.version !== '0.1.7-alpha.2') throw new Error('DSH 上游版本与锁定版本不一致');
    worker = new BusinessWorker({ python: process.env.DSH_PYTHON || join(projectRoot, '.venv_py312/Scripts/python.exe'), projectRoot,
        ...(process.env.DSH_TEST_WORKER ? { command: process.execPath, args: [resolve(process.env.DSH_TEST_WORKER)] } : {}) });
    process.stderr.write('DSH：读取业务目录\n');
    const description = await worker.request('system.describe', {}, { timeoutMs: 60000 });
    process.stderr.write('DSH：初始化业务存储\n');
    await worker.request('system.initialize', {}, { timeoutMs: 60000 });
    const catalog = JSON.parse(await readFile(resolve(process.env.BUSINESS_APPS_MANIFEST || join(projectRoot, 'configurations/business_apps.json')), 'utf8'));
    description.plugins = catalog.plugins.filter(plugin => plugin.enabled !== false);
    description.skills = description.plugins.flatMap(plugin => plugin.skills || []);
    const profile = join(home, 'profiles/company-host');
    await mkdir(profile, { recursive: true });
    await writeFile(join(profile, 'package.json'), JSON.stringify({ private: true, type: 'module', dsh: { profile: { bundles: ['@deepseek-ai/dsh-base'] } } }));
    await writeFile(join(profile, 'cordis.patch.yml'), await hostProfile({ serviceRoot, upstreamRoot, home }));
    process.env.DSH_HOME = home;
    process.env.DSH_TELEMETRY_DISABLED = '1';
    delete process.env.DSH_TELEMETRY_OTLP_URL;
    globalThis[Symbol.for('company.dsh.host')] = { worker, projectRoot, upstreamRoot, home, description, keys: new Map(),
        recoverRuns: runIds => recoverDshRuns(process.env.DSH_PYTHON || join(projectRoot, '.venv_py312/Scripts/python.exe'), runIds) };
    process.chdir(projectRoot);
    const bin = join(upstreamRoot, 'apps/cli/lib/bin.js');
    process.argv = [process.execPath, bin, '--profile', 'company-host'];
    const { runCli } = await import(pathToFileURL(bin).href);
    process.stderr.write('DSH：装配原生宿主\n');
    await runCli();
} catch (error) {
    process.stderr.write(`DSH 宿主启动失败：${error.stack || error}\n`);
    await worker?.close();
    process.exitCode = 1;
}
