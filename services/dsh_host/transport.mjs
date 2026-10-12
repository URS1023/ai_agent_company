import { createServer, request as httpRequest } from 'node:http';
import { createServer as createTlsServer } from 'node:https';
import { readFile } from 'node:fs/promises';

export function json(res, status, body) {
    res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' });
    res.end(JSON.stringify(body));
}
export async function httpEnvelope(req, limit = 128 * 1024 * 1024) {
    const chunks = []; let size = 0;
    for await (const chunk of req) {
        size += chunk.length;
        if (size > limit) { const error = new Error('请求体超过宿主限制'); error.status = 413; throw error; }
        chunks.push(chunk);
    }
    const url = new URL(req.url, 'http://localhost');
    const headers = [];
    for (let index = 0; index < req.rawHeaders.length; index += 2) headers.push([req.rawHeaders[index], req.rawHeaders[index + 1]]);
    return { method: req.method, path: url.pathname, query_string: url.search.slice(1), headers, body_base64: Buffer.concat(chunks).toString('base64') };
}
export function sendWorkerResponse(res, response) {
    const headers = (response.headers || []).filter(([name]) => !['transfer-encoding', 'connection'].includes(name.toLowerCase())).flat();
    res.writeHead(response.status || 200, headers);
    res.end(Buffer.from(response.body_base64 || '', 'base64'));
}
export function openSse(req, res) {
    res.writeHead(200, { 'content-type': 'text/event-stream; charset=utf-8', 'cache-control': 'no-cache, no-transform', connection: 'keep-alive', 'x-accel-buffering': 'no' });
    res.flushHeaders();
    const timer = setInterval(() => { if (!res.destroyed) res.write(': heartbeat\n\n'); }, 15000);
    const end = () => clearInterval(timer);
    res.once('close', end);
    return { send(event, data, id) {
        if (res.destroyed || res.writableEnded) return;
        if (res.writableLength > 4 * 1024 * 1024) { res.destroy(); return; }
        res.write(`${id === undefined ? '' : `id: ${id}\n`}event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
    }, close() { end(); res.end(); } };
}

/** 对外 HTTP/TLS 与原生路由同属一个 Node 进程，按流转发而不聚合 SSE。 */
export async function publicCarrier({ nativePort, host, port, certificate, key }) {
    const handler = (req, res) => {
        const target = httpRequest({ host: '127.0.0.1', port: nativePort, path: req.url, method: req.method,
            headers: { ...req.headers, 'x-forwarded-proto': certificate ? 'https' : 'http' } }, response => {
            res.writeHead(response.statusCode, response.headers);
            response.pipe(res);
            response.on('error', () => res.destroy());
        });
        target.on('error', error => { if (!res.headersSent) json(res, 502, { detail: error.message }); else res.destroy(); });
        req.on('aborted', () => target.destroy());
        res.on('close', () => target.destroy());
        req.pipe(target);
    };
    if (Boolean(certificate) !== Boolean(key)) throw new Error('TLS 证书与私钥必须同时配置');
    const server = certificate ? createTlsServer({ cert: await readFile(certificate), key: await readFile(key) }, handler) : createServer(handler);
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(port, host, resolve); });
    return server;
}
