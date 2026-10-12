/*
 * 中文说明（流式 markdown 渲染的离线用例）：
 * 验证行为：从 app.js 截取 mdRender 与 sendMessage/handleFrame 片段，在离线页面上用假 SSE 帧
 *   验证增量渲染、代码块与表格等渲染结果。
 * 运行方式：module.exports 导出，由调用方执行；只需本机 Edge。
 * 边界：不连后端、不调用模型；断言前端渲染契约，覆盖流式分片与收尾两种情形。
 */
"use strict";

// Exercises the production renderer, frame handler and send lifecycle offline.
module.exports = async function verifyStreamMarkdown() {
  const assert = require("node:assert/strict");
  const fs = require("node:fs");
  const path = require("node:path");
  const puppeteer = require("puppeteer-core");
  const root = path.resolve(__dirname, "../..");
  const source = fs.readFileSync(path.join(root, "apps/web/app.js"), "utf8");
  const renderer = source.slice(source.indexOf("function mdRender("), source.indexOf("// ---------------- Auth"));
  const streaming = source.slice(source.indexOf("async function sendMessage("), source.indexOf('// ---------------- "+" 菜单'));
  const browser = await puppeteer.launch({ executablePath: process.env.TEST5_EDGE_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: true, args: ["--no-sandbox", "--disable-gpu"] });
  try {
    const page = await browser.newPage();
    await page.setRequestInterception(true);
    page.on("request", request => request.abort());
    await page.setContent('<div id="scroll-root" style="height:600px;overflow:auto"><div id="messages"></div></div><textarea id="prompt-textarea"></textarea>');
    await page.addStyleTag({ path: path.join(root, "apps/web/app.css") });
    await page.addScriptTag({ content: `
      const $ = s => document.querySelector(s);
      const NL = '\\n';
      const esc = value => String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
      const safeHref = value => /^https:\\/\\//.test(value) ? value : '#';
      const sandboxArtifactName = () => '';
      const artifactForMarkdownUrl = () => null;
      const artifactImageSource = value => 'src="' + value + '"';
      const state = { current: { id: 'offline', title: 'Offline', messages: [] }, fileIds: [], kbIds: [], datasourceIds: [], modelsReady: true, modelProfile: 'fixture', chatModels: [{ profile_name: 'fixture' }] };
      const PLACEHOLDER_CONVERSATION_TITLES = new Set();
      const API = { token: '', get: async () => ({ messages: [] }), post: async () => ({ status: 'cancelled' }) };
      const localStorage = { getItem: () => '' };
      function updateSendState() {}
      function toast() {}
      async function loadConversations() {}
      function scrollToBottom() {}
      function appendAssistantRow() { const body = document.createElement('div'); $('#messages').appendChild(body); return body; }
      function renderMessages() {
        $('#messages').innerHTML = state.current.messages.filter(m => m.role === 'assistant').map(m =>
          m.parts.map(p => p.type === 'text' ? mdRender(p.text) : '<div class="error">' + esc(p.message) + '</div>').join('')
        ).join('');
      }
      ${renderer}
      ${streaming}
      function frame(type, data) { return 'event: ' + type + '\\ndata: ' + JSON.stringify(data) + '\\n\\n'; }
      function startProbe() {
        state.current.messages = [];
        $('#prompt-textarea').value = '离线测试';
        window.fetch = async (url, options) => {
          if (url.includes('/events')) return new Response(frame('done', {}));
          if (!url.endsWith('/send')) return Response.json(await API.get(url));
          return { ok: true, body: new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode(frame('run.created', { run_id: 'offline-run' })));
            window.feed = value => controller.enqueue(new TextEncoder().encode(value));
            window.end = () => controller.close();
            options.signal.addEventListener('abort', () => controller.error(new DOMException('stopped', 'AbortError')));
          }
        }) }; };
        window.pending = sendMessage();
      }
    ` });
    await page.evaluate(() => startProbe());
    await page.evaluate(() => feed(frame('message.delta', { delta: '## 实时标题\n\n**重点' })));
    await page.waitForSelector('#messages h2');
    assert.equal(await page.evaluate(() => state.streaming), true);
    const footerRendering = await page.evaluate(() => {
      const footer = '<!-- followups: ["按部门汇总资金申请"] -->';
      const baseline = mdRender('正文\n');
      return Array.from({ length: footer.length }, (_, i) => mdRender('正文\n' + footer.slice(0, i + 1)) === baseline);
    });
    assert(footerRendering.every(Boolean), 'Suggestion metadata must stay hidden at every stream boundary');
    assert.equal(await page.$$eval('#messages strong', nodes => nodes.length), 0);
    await page.evaluate(() => feed(frame('message.delta', { delta: '**\n\n1. 第一项\n2. 第二项\n\n`**literal**`\n\n```js\nconst a = 1;' })));
    await page.waitForSelector('#messages code');
    assert.equal(await page.$eval('#messages code', node => node.textContent), '**literal**');
    assert.equal(await page.$$eval('#messages ol', nodes => nodes.length), 1);
    assert.equal(await page.$$eval('#messages ol li', nodes => nodes.length), 2);
    assert.equal(await page.$eval('.code-block', node => node.textContent), 'const a = 1;');
    await page.evaluate(() => {
      feed(frame('message.delta', { delta: '\n```\n\n| 指标 | 数值 |\n| --- | --- |\n| 销售 | 10 |\n\n<script>window.injected=true</script>' }));
      feed(frame('tool.started', { tool: 'offline_tool', tool_call_id: 'one' }));
      feed(frame('tool.completed', { tool: 'offline_tool', tool_call_id: 'one', status: 'completed' }));
      feed(frame('message.delta', { delta: '### 后续结论' }));
    });
    await page.waitForSelector('#messages h3');
    assert.equal(await page.$$eval('#messages table tr', nodes => nodes.length), 2);
    assert.equal(await page.evaluate(() => Boolean(window.injected)), false);
    assert.equal(await page.$$eval('.tool-chip', nodes => nodes.length), 1);
    assert(await page.evaluate(() => document.querySelector('.tool-chip').nextElementSibling.querySelector('h3') !== null));
    await page.evaluate(async () => { feed(frame('done', {}).trimEnd()); end(); await pending; });
    assert.equal(await page.evaluate(() => state.streaming), false);
    assert.equal(await page.$$eval('#messages h2', nodes => nodes.length), 1);
    assert.equal(await page.$$eval('#messages .error', nodes => nodes.length), 0);
    // Cancel before the 60 ms timer fires: final flush must retain the tail.
    await page.evaluate(() => startProbe());
    await page.evaluate(() => feed(frame('message.delta', { delta: '**停止前的内容**' })));
    await page.evaluate(async () => { stopGeneration(); await pending; });
    assert.equal(await page.$eval('#messages strong', node => node.textContent), '停止前的内容');
    assert((await page.$eval('#messages .error', node => node.textContent)).includes('已停止生成'));
    await page.evaluate(() => startProbe());
    await page.evaluate(() => feed(frame('message.delta', { delta: '断流前的内容' })));
    await page.evaluate(async () => { end(); await pending; });
    assert((await page.$eval('#messages', node => node.textContent)).includes('断流前的内容'));
    assert.equal(await page.$$eval('#messages .error', nodes => nodes.length), 0);
    // A successful refresh remains authoritative for saved attachments/results.
    await page.evaluate(() => {
      API.get = async () => ({ messages: [
        { role: 'user', content: '离线测试' },
        { role: 'assistant', parts: [{ type: 'text', text: '**服务端完整结果**' }] },
      ] });
      startProbe();
    });
    await page.evaluate(() => feed(frame('message.delta', { delta: '临时内容' })));
    await page.evaluate(async () => { feed(frame('done', {})); end(); await pending; });
    assert.equal(await page.$eval('#messages strong', node => node.textContent), '服务端完整结果');
    // Completion from an old conversation must never overwrite the new one.
    await page.evaluate(() => startProbe());
    await page.evaluate(() => feed(frame('message.delta', { delta: '旧会话内容' })));
    await page.evaluate(async () => {
      state.current = { id: 'other', messages: [] };
      feed(frame('done', {})); end(); await pending;
    });
    assert.deepEqual(await page.evaluate(() => state.current.messages), []);
    console.log('PASS live Markdown before completion, split syntax, lists, inline code, open fences, tables, escaped HTML, interleaved tools, trailing SSE frame, saved-result synchronization, confirmed cancellation and automatic same-run recovery (offline)');
  } finally { await browser.close(); }
};
