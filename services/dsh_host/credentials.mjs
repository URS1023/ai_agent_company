export const name = 'company-private-credentials';
export function apply(ctx) {
    const keys = globalThis[Symbol.for('company.dsh.host')].keys;
    const records = new Map();
    let writes = Promise.resolve();
    ctx.provide('credentials', {
        resolve: async ref => keys.has(String(ref)) ? { value: keys.get(String(ref)), source: 'company-memory' } : undefined,
        readRecord: async key => records.get(JSON.stringify(key)), listRecords: async () => [],
        // 原生连接服务需要进程内会话密钥；企业模型凭据仍只由 Worker 提供。
        modifyRecord: (key, mutate) => {
            const operation = writes.then(async () => {
                const address = JSON.stringify(key), next = await mutate(records.get(address));
                if (next !== undefined) records.set(address, next);
                return records.get(address);
            });
            writes = operation.catch(() => {});
            return operation;
        },
        deleteRecord: async key => { records.delete(JSON.stringify(key)); },
    });
}
