"use strict";
// 仅提供合成 API 和本地静态文件；不加载后端配置、数据库驱动或模型。
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const ROOT = path.resolve(__dirname, '../..');
const WEB = path.join(ROOT, 'apps/web');
const copy = value => JSON.parse(JSON.stringify(value));
const permissions = ['dashboards.view', 'dashboards.edit', 'dashboards.create', 'dashboards.export', 'dashboards.publish',
  'templates.view', 'templates.create', 'templates.edit', 'templates.delete', 'datasources.view', 'analytics.view'];

function createFixture() {
  const templates = JSON.parse(fs.readFileSync(path.join(ROOT, 'backend/app/modules/reports/resources/hifi_templates.json'), 'utf8')).templates;
  const reports = new Map(templates.map(template => [template.id, {
    id: template.id, reportName: template.name, content: copy(template.content), version: 1, templateId: template.id,
    templateMode: 'manual', status: 'draft', width: template.width, height: template.height, refreshSeconds: 0,
    lineage: { preserveSourceWidgets: true, templateVisualHash: 'fixture' },
  }]));
  const calls = [], unexpected = [];
  function send(response, payload, status = 200) {
    response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
    response.end(JSON.stringify(payload));
  }
  const server = http.createServer(async (request, response) => {
    const url = new URL(request.url, 'http://127.0.0.1');
    if (url.pathname.startsWith('/api/')) {
      calls.push({ method: request.method, path: url.pathname });
      if (url.pathname === '/api/auth/me') return send(response, { user: { id: 'fixture', must_change_password: false }, workspace: { id: 'fixture' } });
      if (url.pathname === '/api/v1/platform/navigation') return send(response, { permissions });
      if (url.pathname === '/api/v1/report-templates') return send(response, { data: templates });
      if (url.pathname === '/api/v1/reports/templates/list') return send(response, { data: templates });
      if (url.pathname === '/api/v1/reports/list') return send(response, { data: [...reports.values()] });
      if (url.pathname === '/api/v1/analytics/datasources') return send(response, { data: [] });
      const match = url.pathname.match(/^\/api\/v1\/reports\/([^/]+)$/);
      if (match && reports.has(match[1])) {
        if (request.method === 'GET') return send(response, { data: copy(reports.get(match[1])) });
        if (request.method === 'PUT') {
          let body = ''; for await (const chunk of request) body += chunk;
          const report = reports.get(match[1]), update = JSON.parse(body);
          if (typeof update.content === 'string') update.content = JSON.parse(update.content);
          Object.assign(report, update, { version: report.version + 1 });
          return send(response, { data: copy(report) });
        }
      }
      if (/\/revisions$/.test(url.pathname)) return send(response, { data: [] });
      unexpected.push({ method: request.method, path: url.pathname });
      return send(response, { message: '隔离夹具未定义此接口' }, 404);
    }
    let relative = decodeURIComponent(url.pathname).replace(/^\/+/, '');
    if (!relative || !path.extname(relative) || relative === 'analytics-app') relative = 'assets/analytics/index.html';
    const target = path.resolve(WEB, relative);
    if (!target.startsWith(WEB + path.sep) || !fs.existsSync(target) || !fs.statSync(target).isFile()) {
      response.writeHead(404); return response.end('Not found');
    }
    const mime = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.jpg': 'image/jpeg',
      '.png': 'image/png', '.json': 'application/json', '.woff2': 'font/woff2', '.svg': 'image/svg+xml' };
    response.writeHead(200, { 'Content-Type': mime[path.extname(target)] || 'application/octet-stream', 'Cache-Control': 'no-store' });
    fs.createReadStream(target).pipe(response);
  });
  return { server, templates, reports, calls, unexpected };
}

if (require.main === module) {
  const fixture = createFixture();
  const port = Number(process.argv[2] || 9158);
  fixture.server.listen(port, '127.0.0.1', () => process.stdout.write(`参考模板隔离验收：http://127.0.0.1:${port}/templates\n`));
}
module.exports = { createFixture };
