import { createInterface } from 'node:readline';

// 测试 Worker：故意丢弃 chat.event，用于验证宿主控制面不会无限等待。
createInterface({ input: process.stdin }).on('line', line => {
    const request = JSON.parse(line);
    if (request.method === 'shutdown') process.stdout.write(`${JSON.stringify({ jsonrpc: '2.0', id: request.id, result: { stopped: true } })}\n`);
    if (request.method === 'tool.execute') setTimeout(() => {
        process.stdout.write(`${JSON.stringify({ jsonrpc: '2.0', id: request.id, result: { status: 'success' } })}\n`);
    }, 80);
});
