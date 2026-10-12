/*
 * 中文说明（知识库批量上传验收）：
 * 验证行为：真实 Chrome 对隔离服务/数据库执行批量上传，仅向量化提供方使用桩实现。
 * 运行方式：需隔离服务（TEST5_E2E_BASE_URL）+ 本机 Edge + TEST5_E2E_PASSWORD。
 * 边界：会写入真实文件与索引（隔离环境）；嵌入/向量化由本地夹具替代，避免外部依赖。
 */
"use strict";
// Real Chrome UI against an isolated server/database; only the embedding provider is a fixture.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const { spawn } = require("node:child_process");
const puppeteer = require("puppeteer-core");
const ROOT = path.resolve(__dirname, "../..");
const OUTPUT = path.join(ROOT, ".implementation/ui-results/knowledge-batch-" + Date.now());
const PASSWORD = "IsolatedBatchUpload12345";
const errors = [], responses = [], steps = [];
let server, browser, page, fixture;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function click(selector) { await page.waitForSelector(selector, { visible: true }); await page.click(selector); }
async function submit(closes = true) {
  await click('#platform-dialog [type="submit"]');
  if (closes) await page.waitForFunction(() => !document.querySelector('#platform-dialog').open, { timeout: 90000 });
  else await page.waitForFunction(() => !document.querySelector('#platform-dialog .form-error').hidden && !document.querySelector('#platform-dialog [type="submit"]').disabled, { timeout: 90000 });
}
async function record(label) {
  const name = `${String(steps.length + 1).padStart(2, "0")}.png`;
  await page.screenshot({ path: path.join(OUTPUT, name), fullPage: true });
  steps.push({ label, screenshot: name }); console.log("PASS " + label);
}
async function selectFiles(...names) {
  const [chooser] = await Promise.all([page.waitForFileChooser(), click('[name="new_file"]')]);
  assert(chooser.isMultiple());
  await chooser.accept(names.map(name => path.join(OUTPUT, name)));
}
async function newKb(name) {
  await page.goto(base + '/knowledge', { waitUntil: 'networkidle0' });
  await click('[data-new-kb]');
  await page.type('#platform-dialog [name=name]', name);
  await submit();
  const links = await page.$$('a.cell-primary');
  for (const link of links) if ((await link.evaluate(el => el.textContent)) === name) {
    await Promise.all([page.waitForNavigation({ waitUntil: 'networkidle0' }), link.click()]); return;
  }
  throw new Error('Created knowledge base link not found');
}
let base;
(async () => {
  fs.mkdirSync(OUTPUT, { recursive: true });
  for (const name of ['first.txt', 'last.txt', 'extra.txt', 'replacement.txt']) fs.writeFileSync(path.join(OUTPUT, name), `批量知识文档 ${name}：设备温度超过标准时，需要检查冷却系统并记录处理结果。`, 'utf8');
  fs.writeFileSync(path.join(OUTPUT, 'invalid.pdf'), 'This is not a PDF.');
  fixture = http.createServer((req, res) => {
    const chunks = [];
    req.on('data', chunk => chunks.push(chunk));
    req.on('end', () => {
      if (!req.url.endsWith('/embeddings')) { res.writeHead(404, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ error: 'isolated search index unavailable', status: 404 })); return; }
      const payload = JSON.parse(Buffer.concat(chunks).toString() || '{}');
      const inputs = Array.isArray(payload.input) ? payload.input : [payload.input];
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ data: inputs.map((_, index) => ({ index, embedding: Array.from({ length: 1024 }, (_, j) => j === 0 ? 1 : 0) })) }));
    });
  });
  await new Promise(resolve => fixture.listen(0, '127.0.0.1', resolve));
  const portProbe = http.createServer();
  await new Promise(resolve => portProbe.listen(0, '127.0.0.1', resolve));
  const port = portProbe.address().port;
  await new Promise(resolve => portProbe.close(resolve));
  base = `http://127.0.0.1:${port}`;
  const log = fs.openSync(path.join(OUTPUT, 'server.log'), 'w');
  server = spawn(path.join(ROOT, '.venv_py312/Scripts/python.exe'), ['run.py'], {
    cwd: path.join(ROOT, 'backend'), windowsHide: true, stdio: ['ignore', log, log],
    env: { ...process.env, HOST: '127.0.0.1', PORT: String(port), APP_ENV: 'test', DATABASE_URL: 'sqlite:///' + path.join(OUTPUT, 'test.sqlite').replaceAll('\\', '/'), LOCAL_STORAGE_ROOT: path.join(OUTPUT, 'storage'), BOOTSTRAP_ADMIN_PASSWORD: PASSWORD, BOOTSTRAP_LYNX_REPORTS: '0', SCHEDULER_ENABLED: '0', AUTO_MIGRATE: '1', ES_URL: `http://127.0.0.1:${fixture.address().port}`, REDIS_URL: 'redis://127.0.0.1:1/0' },
  });
  fs.closeSync(log);
  let ready = false;
  for (let i = 0; i < 90; i++) {
    try { ready = (await (await fetch(base + '/api/health')).json()).app === 'test'; } catch {}
    if (ready) break;
    if (server.exitCode !== null) throw new Error('Isolated server stopped; inspect server.log');
    await sleep(500);
  }
  assert(ready, 'Isolated server must start');
  // Test fixture configuration, not browser session inspection.
  const login = await (await fetch(base + '/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: 'workspace-admin', password: PASSWORD }) })).json();
  const configured = await fetch(base + '/api/models/profiles/embedding', { method: 'PUT', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + login.token, 'X-Workspace-Id': login.workspaces[0].id }, body: JSON.stringify({ provider: 'openai-compatible', model: 'batch-test-embedding', endpoint: `http://127.0.0.1:${fixture.address().port}/v1`, api_key: 'isolated-fixture', capabilities: ['embedding', 'semantic-search'], enabled: true }) });
  assert.equal(configured.status, 200);
  browser = await puppeteer.launch({ executablePath: process.env.TEST5_CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true, args: ['--disable-gpu'], timeout: 60000 });
  page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 960 });
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => {
    const pathname = new URL(response.url()).pathname;
    if (response.request().method() === 'POST' && ['/api/files', '/api/knowledge/documents'].includes(pathname)) responses.push({ path: pathname, status: response.status() });
  });
  await page.goto(base, { waitUntil: 'networkidle0' });
  await page.type('#login-username', 'workspace-admin');
  await page.type('#login-password', PASSWORD);
  await click('#login-btn');
  await page.waitForSelector('.enterprise-area-grid');
  await newKb('批量上传真人测试');
  await click('[data-add-document]');
  assert(await page.$eval('[name=document_source][value=upload]', el => el.checked));
  await submit(false);
  assert.equal(responses.length, 0, 'No upload on empty submission');
  assert.equal(await page.$$eval('.field-error', els => els.filter(el => el.getClientRects().length).length), 1);
  await selectFiles('first.txt', 'invalid.pdf', 'last.txt');
  await selectFiles('first.txt', 'extra.txt');
  assert.equal(await page.$$eval('[data-upload-list] li', els => els.length), 4);
  assert(await page.$eval('#platform-dialog .form-error', el => el.hidden), 'Corrected selection clears the old empty-selection error');
  await click('#platform-dialog footer [data-dialog-close]');
  await page.waitForSelector('#platform-dialog .dialog-discard:not([hidden])');
  await click('[data-keep-editing]');
  await click('[data-remove-upload="3"]');
  assert.equal(await page.$$eval('[data-upload-list] li', els => els.length), 3);
  await record('桌面多选、追加、去重和移除文件');
  await submit(false);
  assert.equal(await page.$$eval('[data-upload-state=ready]', els => els.length), 2);
  assert.equal(await page.$$eval('[data-upload-state=failed]', els => els.length), 1);
  assert.match(await page.$eval('[data-upload-state=failed]', el => el.textContent), /文件已保存在文件库/);
  assert.equal(responses.filter(r => r.path === '/api/files').length, 3);
  await record('一个无效PDF失败，后续文档继续成功加入');
  await page.setViewport({ width: 390, height: 844 });
  await submit(false);
  assert.equal(responses.filter(r => r.path === '/api/files').length, 3, 'Retry must not upload again');
  assert.equal(responses.filter(r => r.path === '/api/knowledge/documents').length, 4, 'Only failed file is retried');
  const geometry = await page.$eval('#platform-dialog', el => ({ left: el.getBoundingClientRect().left, right: el.getBoundingClientRect().right, width: innerWidth, scroll: el.scrollWidth, client: el.clientWidth }));
  assert(geometry.left >= 0 && geometry.right <= geometry.width + 1 && geometry.scroll <= geometry.client + 1, JSON.stringify(geometry));
  await record('390px失败原因可读，重试跳过成功文件且无重复上传');
  await click('[data-remove-upload="1"]');
  await selectFiles('replacement.txt');
  await submit();
  assert.equal(await page.$$eval('tbody tr', rows => rows.filter(row => row.innerText.includes('就绪') || row.innerText.includes('ready')).length) >= 3, true);
  await page.reload({ waitUntil: 'networkidle0' });
  for (const name of ['first.txt', 'last.txt', 'replacement.txt']) assert((await page.$eval('tbody', el => el.innerText)).includes(name));
  await record('替换失败文件后完成上传，刷新仍可见成功文档');
  await page.setViewport({ width: 1440, height: 960 });
  await newKb('批量全部成功测试');
  await click('[data-add-document]');
  await selectFiles('first.txt', 'last.txt');
  await submit();
  assert.equal(await page.$$eval('tbody tr', rows => rows.length), 2);
  await record('两个文件一次提交全部成功');
  await newKb('原文件库入口回归');
  await click('[data-add-document]');
  await click('[name=document_source][value=existing]');
  await page.waitForFunction(() => !document.querySelector('[name=file_id]').disabled && document.querySelector('[name=file_id]').options.length > 1);
  const option = await page.$$eval('[name=file_id] option', options => options.find(o => o.textContent.startsWith('first.txt') && !o.disabled)?.value);
  assert(option);
  await page.select('[name=file_id]', option);
  await submit();
  assert((await page.$eval('tbody', el => el.innerText)).includes('first.txt'));
  await record('原有从文件库选择入口仍可正常添加');
  assert.deepEqual(errors, []);
})().catch(async error => {
  errors.push(error.stack); console.error(error.stack); process.exitCode = 1;
  if (page) { await page.screenshot({ path: path.join(OUTPUT, 'failure.png'), fullPage: true }).catch(() => {}); fs.writeFileSync(path.join(OUTPUT, 'failure.txt'), await page.evaluate(() => document.body.innerText).catch(() => '')); }
}).finally(async () => {
  await browser?.close();
  server?.kill();
  await new Promise(resolve => fixture ? fixture.close(resolve) : resolve());
  fs.writeFileSync(path.join(OUTPUT, 'results.json'), JSON.stringify({ mode: 'Real Chrome native clicks, keyboard and multi-file chooser; isolated SQLite and storage; embedding fixture; ES keyword fallback', steps, responses, errors }, null, 2));
  console.log(OUTPUT);
});
