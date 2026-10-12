import { appendFileSync } from 'node:fs';
import { resolve, relative, isAbsolute } from 'node:path';
import { BusinessWorker } from '../../services/dsh_host/worker.mjs';

// 故障只存在测试预加载模块：在明确隔离目录、指定正文的首次结算转发前拒绝一次。
if (process.env.DSH_COMPLETION_FAULT === '1') {
    if (process.env.DSH_TEST_ISOLATION !== '1') throw new Error('结算故障夹具必须启用测试隔离');
    const directory = resolve(process.env.DSH_HOME, '..');
    const evidence = resolve(process.env.DSH_COMPLETION_FAULT_LOG || '');
    const local = relative(directory, evidence);
    if (!local || local.startsWith('..') || isAbsolute(local)) throw new Error('故障证据必须写入本轮临时目录');
    const original = BusinessWorker.prototype.request;
    let injected = false;
    BusinessWorker.prototype.request = function (method, params, ...rest) {
        if (!injected && method === 'chat.settle' && params.status === 'completed' && params.content === 'FIXTURE_A_FINAL') {
            injected = true;
            appendFileSync(evidence, JSON.stringify({ run_id: params.run_id, method, forwarded: false }) + '\n');
            return Promise.reject(Object.assign(new Error('夹具：A结算尚未转发即失败'), { code: 'FIXTURE_SETTLE_NOT_FORWARDED' }));
        }
        return original.call(this, method, params, ...rest);
    };
}
