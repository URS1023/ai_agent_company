import { createInterface } from 'node:readline';

/** 单进程双向 JSON-RPC；业务回调可与取消命令同时传输。 */
export class JsonLineRpc {
    constructor(input = process.stdin, output = process.stdout.write.bind(process.stdout)) {
        this.input = input;
        this.output = output;
        this.pending = new Map();
        this.queue = [];
        this.nextId = 0;
        this.active = false;
        this.closed = false;
        this.first = new Promise((resolve, reject) => { this.firstResolve = resolve; this.firstReject = reject; });
        this.reader = createInterface({ input, crlfDelay: Infinity });
        this.reader.on('line', line => {
            if (line.length > 32 * 1024 * 1024) return this.fail(null, new Error('协议消息超过 32 MiB'));
            let message;
            try { message = JSON.parse(line); }
            catch { this.fail(null, new Error('协议消息必须是 JSON')); return; }
            if (message.jsonrpc !== '2.0') return this.fail(message.id ?? null, new Error('仅支持 JSON-RPC 2.0'));
            if (!message.method) {
                const pending = this.pending.get(message.id);
                if (!pending) return;
                this.pending.delete(message.id);
                if (message.error) pending.reject(new Error(message.error.message || '企业服务拒绝操作'));
                else pending.resolve(message.result);
                return;
            }
            if (this.firstResolve) {
                const resolve = this.firstResolve;
                this.firstResolve = undefined;
                resolve(message);
                return;
            }
            if (this.active) void this.dispatch(message);
            else this.queue.push(message);
        });
        this.reader.on('close', () => {
            this.closed = true;
            this.firstReject(new Error('初始化前标准输入已关闭'));
            for (const item of this.pending.values()) item.reject(new Error('企业服务通信已中断，操作结果需核实'));
            this.pending.clear();
            this.endHandler?.();
        });
    }

    write(value) { this.output(`${JSON.stringify(value)}\n`); }
    notify(method, params) { this.write({ jsonrpc: '2.0', method, params }); }
    respond(id, result) { this.write({ jsonrpc: '2.0', id, result }); }
    fail(id, error) { this.write({ jsonrpc: '2.0', id, error: { code: -32000, message: String(error?.message || error) } }); }
    handle(handler) { this.handler = handler; }
    onEnd(handler) { this.endHandler = handler; if (this.closed) handler(); }
    closeInput() {
        this.endHandler = undefined;
        this.reader.close();
        this.input.destroy();
    }
    activate() { this.active = true; for (const item of this.queue.splice(0)) void this.dispatch(item); }
    async dispatch(message) {
        try {
            const result = await this.handler(message.method, message.params || {});
            if (message.id !== undefined) this.respond(message.id, result);
        } catch (error) {
            if (message.id !== undefined) this.fail(message.id, error);
        }
    }
    request(method, params) {
        if (this.closed) return Promise.reject(new Error('企业服务通信已关闭'));
        const id = `node:${++this.nextId}`;
        return new Promise((resolve, reject) => {
            this.pending.set(id, { resolve, reject });
            this.write({ jsonrpc: '2.0', id, method, params });
        });
    }
}
