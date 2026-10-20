/** 将离线报表HTML渲染为真实PDF或PNG；不接受URL，不访问网络或业务数据库。 */
import { createRequire } from 'node:module';
import { existsSync } from 'node:fs';

const require = createRequire(new URL('../apps/web/package.json', import.meta.url));
const { chromium } = require('@playwright/test');

function browserExecutable() {
  if (process.env.AGENT_CHROMIUM_EXECUTABLE) return process.env.AGENT_CHROMIUM_EXECUTABLE;
  if (existsSync(chromium.executablePath())) return chromium.executablePath();
  // 仅使用明确的系统安装位置，独立无痕上下文不读取用户浏览器资料。
  return [
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser',
  ].find((path) => existsSync(path));
}

async function readInput() {
  const chunks = [];
  let size = 0;
  for await (const chunk of process.stdin) {
    size += chunk.length;
    if (size > 8 * 1024 * 1024) throw new Error('报表内容超过渲染预算');
    chunks.push(chunk);
  }
  const input = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  if (typeof input.html !== 'string' || !['pdf', 'png'].includes(input.format)) {
    throw new Error('渲染输入无效');
  }
  return input;
}

let browser;
try {
  const input = await readInput();
  const width = Math.min(Math.max(Number(input.width) || 1440, 320), 4096);
  const height = Math.min(Math.max(Number(input.height) || 900, 240), 4096);
  const executablePath = browserExecutable();
  browser = await chromium.launch({
    headless: true,
    ...(executablePath ? { executablePath } : {}),
  });
  const context = await browser.newContext({ viewport: { width, height }, javaScriptEnabled: false });
  // 连浏览器间接触发的内网/数据库HTTP入口也不能访问，仅保留内嵌素材。
  await context.route('**/*', (route) => route.abort('blockedbyclient'));
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  await page.setContent(input.html, { waitUntil: 'load', timeout: 10000 });
  const dimensions = await page.evaluate(() => ({ width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight }));
  if (dimensions.width > 4096 || dimensions.height > 16000) throw new Error('报表尺寸超过渲染预算');
  const result = input.format === 'pdf'
    ? await page.pdf({ width: `${width}px`, height: `${height}px`, printBackground: true, margin: { top: 0, right: 0, bottom: 0, left: 0 } })
    : await page.screenshot({ type: 'png', fullPage: true });
  process.stdout.write(result);
} catch (error) {
  process.stderr.write(`报表渲染失败：${error.message}\n`);
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
}
