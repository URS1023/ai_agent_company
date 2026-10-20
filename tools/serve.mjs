/** 提供仅供本机查看的静态预览，不暴露仓库、配置或任何业务接口。 */
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
const page = new URL('../index.html', import.meta.url);
const port = Number(process.env.PREVIEW_PORT || 4173);
const server = createServer(async (req, res) => {
    const path = new URL(req.url || '/', 'http://127.0.0.1').pathname;
    if (path === '/favicon.ico') { res.writeHead(204); res.end(); return; }
    if (path !== '/' && path !== '/index.html') { res.writeHead(404); res.end('Not found'); return; }
    try {
        res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
        res.end(await readFile(page));
    } catch {
        res.writeHead(500); res.end('Preview file unavailable');
    }
});
server.listen(port, '127.0.0.1', () => process.stdout.write(`Preview: http://127.0.0.1:${port}\n`));
