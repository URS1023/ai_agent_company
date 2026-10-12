import test from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { BusinessWorker } from '../worker.mjs';

const root = fileURLToPath(new URL('../../..', import.meta.url));
const timeoutWorker = fileURLToPath(new URL('./worker-timeout.mjs', import.meta.url));

test('控制面 RPC 使用有限等待，长任务 RPC 仍不设置宿主总时限', async () => {
    const previousTimeout = process.env.DSH_RPC_CONTROL_TIMEOUT_MS;
    process.env.DSH_RPC_CONTROL_TIMEOUT_MS = '30';
    const worker = new BusinessWorker({ python: process.execPath, command: process.execPath,
        args: [timeoutWorker], projectRoot: root, env: { ...process.env, DSH_RPC_CONTROL_TIMEOUT_MS: '30' } });
    try {
        await assert.rejects(worker.request('chat.event', {}), error => error.code === 'DSH_RPC_TIMEOUT');
        const longTask = await worker.request('tool.execute', {}, { timeoutMs: 0 });
        assert.equal(longTask.status, 'success');
        // 关闭请求显式使用较长时间，不能被控制面默认值影响。
    } finally {
        await worker.close();
        if (previousTimeout === undefined) delete process.env.DSH_RPC_CONTROL_TIMEOUT_MS;
        else process.env.DSH_RPC_CONTROL_TIMEOUT_MS = previousTimeout;
    }
});
