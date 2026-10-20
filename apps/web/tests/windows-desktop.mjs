/** 验证本机双服务登录、平台对话入口及一次64-token原生界面对话；凭据只经环境注入。 */
import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';

const proof = JSON.parse(process.env.DESKTOP_PROOF || '{}');
assert.equal(proof.owned, true);
assert.ok(Date.now() - proof.checked_at < 10000);
const credentials = JSON.parse(process.env.DESKTOP_ACCOUNTS || '{}');
const chatOnly = process.env.DESKTOP_CHAT_ONLY === '1';
const output = new URL('../test-results/', import.meta.url);
await mkdir(output, { recursive: true });
const result = { status: 'running', business_http: proof.http, static_requests: 0, model_calls: 0, checks: [], errors: [] };
const browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--disable-gpu', '--renderer-process-limit=2'] });
const context = await browser.newContext({ locale: 'zh-CN', viewport: { width: 1440, height: 960 }, serviceWorkers: 'block' });
const deadline = Date.now() + 120000;
let fatal = null;
const timer = setTimeout(() => { fatal = 'verification_deadline'; void browser.close(); }, 120000);
context.setDefaultTimeout(15000);
await context.addInitScript(() => {
  if (['http://127.0.0.1:8080', 'http://127.0.0.1:13000'].includes(location.origin))
    localStorage.setItem('locale', 'zh-CN');
});
await context.route('**/*', async route => {
  const request = route.request();
  const url = new URL(request.url());
  if (!['http://127.0.0.1:8080', 'http://127.0.0.1:13000'].includes(url.origin)) {
    return route.abort();
  }
  const business = url.pathname.startsWith('/api') || url.pathname.startsWith('/ws');
  if (business) result.business_http++;
  else result.static_requests++;
  if (Date.now() >= deadline || result.business_http > (chatOnly ? 40 : 60) || result.static_requests > 300) {
    fatal = 'request_budget_exceeded';
    await route.abort();
    return;
  }
  if (url.pathname === '/api/chat/completions' && request.method() === 'POST') {
    result.model_calls++;
    if (result.model_calls > 1) { fatal = 'model_budget_exceeded'; return route.abort(); }
    const body = request.postDataJSON();
    body.params = { ...body.params, max_tokens: 64 };
    body.background_tasks = {};
    return route.continue({ postData: JSON.stringify(body) });
  }
  if (/\/api\/v1\/tasks\//.test(url.pathname) && request.method() === 'POST') {
    fatal = 'unexpected_background_model_task';
    return route.abort();
  }
  await route.continue();
});
context.on('response', response => {
  if (response.status() >= 500) fatal = `server_error_${response.status()}`;
});
try {
  let chat;
  if (chatOnly) {
    chat = await context.newPage();
    await chat.goto('http://127.0.0.1:13000/auth?form=signin', { waitUntil: 'domcontentloaded' });
  } else {
  const platform = await context.newPage();
  platform.on('pageerror', error => result.errors.push(error.message.slice(0, 160)));
  await platform.goto('http://127.0.0.1:8080', { waitUntil: 'domcontentloaded' });
  await platform.locator('input[autocomplete="username"]').fill(credentials['制造业平台'].username);
  await platform.locator('input[type="password"]').fill(credentials['制造业平台'].password);
  await platform.getByRole('button', { name: '登录', exact: true }).click();
  await platform.getByText('工作概览', { exact: true }).first().waitFor();
  result.checks.push('platform_login');
  await platform.goto('http://127.0.0.1:8080/chat', { waitUntil: 'domcontentloaded' });
  await platform.getByRole('button', { name: '进入对话', exact: true }).waitFor();
  await platform.screenshot({ path: new URL('windows-platform.png', output).pathname.replace(/^\/([A-Z]:)/i, '$1') });
  [chat] = await Promise.all([
    context.waitForEvent('page'),
    platform.getByRole('button', { name: '进入对话', exact: true }).click(),
  ]);
  assert.equal(new URL(chat.url()).origin, 'http://127.0.0.1:13000');
  result.checks.push('platform_opens_native_chat');
  }
  chat.on('pageerror', error => result.errors.push(error.message.slice(0, 160)));
  await chat.locator('input[type="email"]').fill(credentials['AI对话'].username);
  await chat.locator('input[type="password"]').fill(credentials['AI对话'].password);
  await chat.locator('button[type="submit"]').click();
  const input = chat.locator('#chat-input');
  await input.waitFor({ state: 'visible', timeout: 25000 });
  result.checks.push('native_login_chat_input');
  const releaseNotes = chat.getByRole('button', { name: /^(确认，开始使用！|Okay, Let's Go!)$/ });
  // 首次管理员登录会展示原生更新说明，正常点击关闭后再发送，保留原生用户设置。
  await Promise.race([
    releaseNotes.waitFor({ state: 'visible', timeout: 4000 }).then(() => releaseNotes.click()),
    chat.locator('#chat-input').click({ timeout: 4000 }),
  ]).catch(async () => {
    if (await releaseNotes.isVisible()) await releaseNotes.click();
  });
  await chat.screenshot({ path: new URL('windows-openwebui.png', output).pathname.replace(/^\/([A-Z]:)/i, '$1') });
  await input.fill('请直接回复：连接成功。不要展开说明。');
  await chat.locator('#send-message-button').click();
  // 原生ResponseMessage独有容器，不能把用户输入中的相同文本当回复。
  const assistant = chat.locator('#response-content-container').filter({ hasText: '连接成功' }).last();
  await assistant.waitFor({ timeout: 30000 });
  result.assistant_text = (await assistant.innerText()).slice(0, 300);
  assert.ok(result.assistant_text.includes('连接成功'));
  assert.equal(result.model_calls, 1);
  result.checks.push('native_chat_reply');
  await chat.screenshot({ path: new URL('windows-openwebui-reply.png', output).pathname.replace(/^\/([A-Z]:)/i, '$1') });
  assert.equal(fatal, null);
  assert.equal(result.errors.length, 0);
  result.status = 'passed';
} catch (error) {
  result.status = 'failed';
  result.failure = fatal || error.message.slice(0, 500);
  const pages = context.pages();
  if (pages.length) {
    await pages.at(-1).screenshot({ path: new URL('windows-desktop-failure.png', output).pathname.replace(/^\/([A-Z]:)/i, '$1') }).catch(() => {});
    result.visible_text = (await pages.at(-1).locator('body').innerText().catch(() => '')).slice(0, 1500);
  }
  process.exitCode = 1;
} finally {
  clearTimeout(timer);
  await browser.close();
  await writeFile(new URL(chatOnly ? 'windows-desktop-chat.json' : 'windows-desktop.json', output), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
}
