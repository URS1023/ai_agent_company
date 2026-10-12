import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { EventEmitter } from 'node:events';

// 这些 RPC 只负责控制面持久化和生命周期回执，不应因 Worker 异常而无限占住宿主。
// 模型生成、工具执行和插件分发可能是真正的长任务，继续保持无总时限。
const CONTROL_PLANE_METHODS = new Set([
    'chat.authorize', 'chat.history', 'chat.prepare', 'chat.recover', 'chat.event', 'chat.settle',
    'chat.steer', 'chat.queue.update', 'run.cancel', 'model.authorize', 'model.settle',
    'delivery.reference', 'delivery.present', 'system.activate', 'shutdown', 'tool.receipt.get',
]);
const controlPlaneTimeout = () => {
    const value = Number(process.env.DSH_RPC_CONTROL_TIMEOUT_MS || 60000);
    return Number.isFinite(value) && value > 0 ? Math.min(Math.floor(value), 10 * 60 * 1000) : 0;
};

/** 私有长驻 Worker 管道；业务请求与反向模型请求可以交错完成。 */
export class BusinessWorker extends EventEmitter {
    constructor({ python, projectRoot, env = process.env, command, args }) {
        super();
        this.pending = new Map();
        this.handlers = new Map();
        this.sequence = 0;
        this.closed = false;
        this.child = spawn(command || python, args || ['-m', 'app.business_worker'], {
            cwd: `${projectRoot}/backend`, env: { ...env, PYTHONUNBUFFERED: '1' },
            stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true,
        });
        this.reader = createInterface({ input: this.child.stdout, crlfDelay: Infinity });
        this.reader.on('line', line => { void this.receive(line); });
        this.child.stderr.on('data', chunk => process.stderr.write(chunk));
        this.child.on('error', error => this.fail(error));
        this.child.on('exit', (code, signal) => this.fail(new Error(`业务 Worker 已退出 (${code ?? signal})`)));
    }
    handle(method, handler) { this.handlers.set(method, handler); }
    send(message) {
        if (this.closed) throw new Error('业务 Worker 不可用');
        this.child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', ...message })}\n`);
    }
    request(method, params = {}, options = {}) {
        if (this.closed) return Promise.reject(new Error('业务 Worker 不可用'));
        const id = `host-${++this.sequence}`;
        // 未显式指定时仅给控制面 RPC 加有限等待，不能限制模型和业务工具的长任务。
        const timeoutMs = options.timeoutMs === undefined && CONTROL_PLANE_METHODS.has(method)
            ? controlPlaneTimeout() : Number(options.timeoutMs || 0);
        return new Promise((resolve, reject) => {
            const timer = timeoutMs ? setTimeout(() => {
                this.pending.delete(id);
                const error = new Error(`业务 Worker 控制请求超时：${method}`);
                error.code = 'DSH_RPC_TIMEOUT'; error.method = method;
                reject(error);
            }, timeoutMs) : undefined;
            timer?.unref?.();
            this.pending.set(id, { resolve, reject, timer, method, params });
            try { this.send({ id, method, params }); }
            catch (error) { this.pending.delete(id); clearTimeout(timer); reject(error); }
        });
    }
    async receive(line) {
        let message;
        try { message = JSON.parse(line); }
        catch { this.fail(new Error('业务 Worker 返回了无效 JSON 协议')); return; }
        if (message.method === 'tool.receipt.committed') {
            const owner = message.params?.identity;
            for (const [id, pending] of this.pending) {
                if (pending.method === 'tool.execute' && pending.params.durable_receipt === true
                    && owner?.run_id === pending.params.run_id && owner?.call_id === pending.params.call_id
                    && owner?.invocation_seq === pending.params.invocation_seq
                    && owner?.native_agent_session_id === pending.params.native_agent_session_id) {
                    this.recoverReceipt(id, pending);
                }
            }
            return;
        }
        if (message.method) {
            const handler = this.handlers.get(message.method);
            try {
                if (!handler) throw new Error(`未支持的宿主方法：${message.method}`);
                const result = await handler(message.params || {}, { id: message.id });
                if (message.id !== undefined) this.send({ id: message.id, result });
            } catch (error) {
                if (message.id !== undefined && !this.closed) this.send({ id: message.id, error: {
                    code: -32000, message: error.message || String(error), data: { code: error.code },
                } });
            }
            return;
        }
        const pending = this.pending.get(message.id);
        if (!pending) return;
        if (message.error && pending.method === 'tool.execute' && pending.params.durable_receipt === true) {
            this.recoverReceipt(message.id, pending);
            return;
        }
        this.pending.delete(message.id);
        clearTimeout(pending.timer);
        this.clearReceiptLookup(pending);
        if (message.error) {
            const error = new Error(message.error.message || '业务调用失败');
            Object.assign(error, message.error.data || {});
            error.rpcCode = message.error.code;
            pending.reject(error);
        } else pending.resolve(message.result);
    }
    clearReceiptLookup(pending) {
        // 原回复赢得竞速时移除只读查询的等待者；迟到包按普通未知请求忽略。
        if (!pending.lookupId) return;
        const lookup = this.pending.get(pending.lookupId);
        if (lookup) { this.pending.delete(pending.lookupId); clearTimeout(lookup.timer); lookup.resolve(undefined); }
        delete pending.lookupId;
    }
    recoverReceipt(id, pending) {
        if (pending.recovery || this.pending.get(id) !== pending) return;
        pending.recovery = true;
        const { durable_receipt, ...params } = pending.params;
        // 仅给一次只读 lookup 五秒期限，长业务执行本身不增加总时限或自动重试。
        const lookup = this.request('tool.receipt.get', params, { timeoutMs: 5000 });
        pending.lookupId = `host-${this.sequence}`;
        lookup.then(result => {
            if (this.pending.get(id) !== pending) return;
            this.pending.delete(id); clearTimeout(pending.timer);
            if (result?.receipt_protocol === 1 && result.state === 'committed') pending.resolve(result);
            else {
                const error = new Error('工具结果尚未取得可授权的持久回执，已停止自动继续，请核实原操作。');
                error.code = 'DSH_RECEIPT_UNCONFIRMED'; error.receiptState = result?.state || 'unknown'; pending.reject(error);
            }
        }, cause => {
            if (this.pending.get(id) !== pending) return;
            this.pending.delete(id); clearTimeout(pending.timer);
            const error = new Error('工具持久回执查询失败，已停止自动继续，请核实原操作。', { cause });
            error.code = 'DSH_RECEIPT_UNCONFIRMED'; pending.reject(error);
        });
    }
    fail(error) {
        if (this.closed) return;
        this.closed = true;
        for (const pending of this.pending.values()) { clearTimeout(pending.timer); pending.reject(error); }
        this.pending.clear();
        this.emit('unavailable', error);
    }
    async close() {
        if (!this.closed) {
            try { await this.request('shutdown', {}, { timeoutMs: 10000 }); } catch {}
        }
        this.reader.close();
        this.child.stdin.end();
        if (this.child.exitCode === null) {
            await Promise.race([new Promise(resolve => this.child.once('exit', resolve)), new Promise(resolve => setTimeout(resolve, 1500))]);
            if (this.child.exitCode === null) this.child.kill();
        }
    }
}
