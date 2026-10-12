/*
 * 中文说明（局域网/Https 环境下的实时对话验收）：
 * 验证行为：在真实 LAN 部署形态下从界面发起对话，校验流式回答与页面状态。
 * 运行方式：需已有可访问的 LAN 服务；通用登录/填写/等待工具来自 tests/ui/browser_only_helpers.js。
 * 边界：会真实调用模型并写入会话记录，请在允许产生模型调用的环境执行。
 */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const puppeteer = require("puppeteer-core");
const { login, fillElement, waitUiResponse } = require("./browser_only_helpers");
const BASE = process.env.TEST5_E2E_BASE_URL;
const OUTPUT = path.resolve('.implementation/ui-results/lan-live-chat-' + Date.now());
let browser, page;
const report = { checks: [], errors: [] };
(async () => {
  assert.equal(BASE, 'https://192.168.6.16:8443');
  fs.mkdirSync(OUTPUT, { recursive: true });
  browser = await puppeteer.launch({ executablePath: process.env.TEST5_EDGE_PATH, headless: true, defaultViewport: { width: 1440, height: 960 } });
  page = await browser.newPage();
  await login(page, BASE, process.env.TEST5_E2E_PASSWORD);
  await page.goto(BASE + '/ai', { waitUntil: 'networkidle0' });
  await page.waitForSelector('#prompt-textarea', { visible: true });
  const prompt = '局域网连通性验收：不需要查询任何业务数据，也不调用工具。请只回复“局域网对话正常”。';
  // A previous interrupted run may have left this exact synthetic conversation.
  const previous = await page.evaluateHandle(() => [...document.querySelectorAll('#conversation-list .history-item')]
    .find(node => node.title === '局域网连通性验收：不需要查询任何业务数据，也不调用工具') || null);
  if (previous.asElement()) {
    await previous.asElement().click();
    await page.waitForFunction(text => document.querySelector('#messages .msg-row')?.textContent.includes(text), { timeout: 30000 }, prompt);
    page.once('dialog', dialog => dialog.accept());
    const response = await waitUiResponse(page, (r,u) => u.pathname.startsWith('/api/chat/conversations/') && r.request().method() === 'DELETE', async () => {
      const active = await page.$('#conversation-list .history-item.active');
      await active.hover();
      await (await active.$('.history-options')).click();
    });
    assert.equal(response.status(), 200);
    console.log('PASS removed the exact synthetic conversation left by the interrupted check');
  }
  await fillElement(page, await page.$('#prompt-textarea'), prompt);
  const response = await waitUiResponse(page, (r,u) => /\/api\/chat\/conversations\/[^/]+\/send$/.test(u.pathname) && r.request().method() === 'POST', () => page.click('#composer-submit-button'), 120000);
  assert.equal(response.status(), 200);
  report.conversationId = new URL(response.url()).pathname.split('/').at(-2);
  fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify(report,null,2));
  await page.waitForFunction(() => !document.querySelector('#composer-submit-button')?.classList.contains('streaming') && document.querySelectorAll('#messages .msg-row').length >= 2, { timeout: 180000 });
  const answer = await page.$eval('#messages .msg-row:last-child', row => row.innerText);
  assert(answer.includes('局域网对话正常'), 'Expected a real model reply in the chat UI');
  report.checks.push('真实模型通过 HTTPS 流式返回回答，页面显示完整');
  console.log('PASS actual HTTPS streaming chat');
  await page.screenshot({ path: path.join(OUTPUT, 'chat.png'), fullPage: true });
  const currentTitle = await page.$eval('#header-title', node => node.textContent.trim());
  const handle = await page.evaluateHandle(title => [...document.querySelectorAll('#conversation-list .history-item')]
    .find(node => node.textContent.includes(title))?.querySelector('.history-options') || null, currentTitle);
  const remove = handle.asElement();
  assert(remove, 'Test conversation cleanup control is missing');
  const parent = await remove.evaluateHandle(node => node.closest('.history-item'));
  await parent.asElement().hover();
  page.once('dialog', dialog => dialog.accept());
  const deleted = await waitUiResponse(page, (r,u) => u.pathname === '/api/chat/conversations/' + report.conversationId && r.request().method() === 'DELETE', () => remove.click());
  assert.equal(deleted.status(), 200);
  report.checks.push('本次验收会话已通过界面删除');
  console.log('PASS removed only the test conversation');
})().catch(error => { report.errors.push(error.message); console.error(error.stack); process.exitCode = 1; })
  .finally(async () => { await browser?.close(); fs.mkdirSync(OUTPUT,{recursive:true}); fs.writeFileSync(path.join(OUTPUT,'result.json'), JSON.stringify(report,null,2)); console.log('REPORT '+OUTPUT); });
