const active = job => ['running', 'stopping'].includes(job.status);
const observePollMs = () => {
    const value = Number(process.env.DSH_JOB_OBSERVE_POLL_MS || 250);
    return Number.isFinite(value) && value > 0 ? Math.min(Math.max(Math.floor(value), 50), 5000) : 250;
};

/** 只观察当前业务运行的父子代理；原生 list 同时返回的无主任务不属于该运行。 */
export function ownedActiveJobs(host, current, run) {
    const jobs = host.ctx.get?.('jobs');
    if (!jobs) return [];
    const owners = new Set([current.id, ...[...host.agentRuns].filter(([, id]) => id === run.run_id).map(([id]) => id)]);
    return [...owners].flatMap(owner => jobs.list(owner).filter(job => job.owner === owner && active(job)));
}

/** 纯订阅不会消费输出或设置 awaited，原生完成通知仍可唤醒父代理。 */
export async function observeOwnedJobs(host, current, run) {
    const jobs = host.ctx.get?.('jobs');
    if (!jobs || !ownedActiveJobs(host, current, run).length) return;
    await new Promise((resolve, reject) => {
        let dispose = () => {};
        let poll;
        let finished = false;
        const finish = error => {
            if (finished) return;
            finished = true;
            dispose();
            if (poll) clearInterval(poll);
            current.abort?.signal.removeEventListener('abort', onAbort);
            if (error) reject(error); else resolve();
        };
        const onAbort = () => {
            const error = new Error('宿主正在关闭，已停止等待后台任务');
            error.code = 'DSH_JOB_OBSERVE_ABORTED';
            finish(error);
        };
        const check = () => {
            try {
                if (current.abort?.signal.aborted) { onAbort(); return; }
                if (!ownedActiveJobs(host, current, run).length) finish();
            } catch (error) { finish(error); }
        };
        dispose = jobs.events.subscribe({ owners: 'all' }, event => {
            if (event.type === 'settled' || event.type === 'removed') check();
        });
        // 订阅后复核，并定期轮询，避免完成事件丢失时永久等待。
        current.abort?.signal.addEventListener('abort', onAbort, { once: true });
        poll = setInterval(check, observePollMs());
        poll.unref?.();
        check();
    });
}

/** Agent.cancel 不会销毁跨轮会话，因而还需显式停止该运行拥有的后台任务。 */
export function cancelOwnedJobs(host, current, run, reason) {
    const jobs = host.ctx.get?.('jobs');
    for (const job of ownedActiveJobs(host, current, run)) {
        try {
            const result = jobs.kill(job.id, job.owner, reason);
            if (result?.catch) void result.catch(error => process.stderr.write(`DSH 后台任务停止失败 ${job.id}: ${error.message || error}\n`));
        } catch (error) { process.stderr.write(`DSH 后台任务停止失败 ${job.id}: ${error.message || error}\n`); }
    }
}
