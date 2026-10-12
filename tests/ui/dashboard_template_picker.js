/*
 * 中文说明（大屏模板选择器只读验收）：
 * 验证行为：真实大屏与其对应模板在选取器中视觉一致、可选，且选中后不回改模板本身。
 * 运行方式：需隔离服务（TEST5_E2E_BASE_URL，默认 8011）+ 本机 Edge，口令默认 admin123
 *   （TEST5_E2E_PASSWORD 覆盖）。
 * 边界：只读；依赖已存在的大屏与模板数据，缺失时应报失败而不是自动创建。
 */
/* Read-only acceptance for real Cloud Brain reports and their visually identical templates. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');

const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011';
const PASSWORD = process.env.TEST5_E2E_PASSWORD || 'admin123';
const EDGE = process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const OUTPUT = path.resolve('.implementation/ui-results/direct-report-list-' + Date.now().toString(36));
const EXPECTED_REPORTS = 33;
let browser;

async function api(pathname, options = {}) {
  const response = await fetch(BASE + pathname, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.message || body.detail || `HTTP ${response.status}`);
  return body.data ?? body;
}

(async () => {
  fs.mkdirSync(OUTPUT, { recursive: true });
  const session = await api('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: 'workspace-admin', password: PASSWORD }),
  });
  const workspaceId = session.workspaces?.[0]?.id;
  assert(session.token && workspaceId, 'login response is missing token or workspace');
  browser = await puppeteer.launch({ executablePath: EDGE, headless: true, args: ['--no-sandbox', '--disable-gpu'] });
  const page = await browser.newPage();
  await page.evaluateOnNewDocument((token, workspace) => {
    localStorage.setItem('agent_token', token);
    localStorage.setItem('agent_workspace_id', workspace);
  }, session.token, workspaceId);
  const failures = [];
  page.on('pageerror', (error) => failures.push('pageerror: ' + error.message));
  page.on('response', (response) => {
    const pathname = new URL(response.url()).pathname;
    if (response.status() >= 400 && pathname !== '/favicon.ico') failures.push(response.status() + ' ' + pathname);
  });

  await page.setViewport({ width: 1500, height: 980, deviceScaleFactor: 1 });
  await page.goto(BASE + '/dashboards', { waitUntil: 'networkidle0', timeout: 60000 });
  const frameHandle = await page.waitForSelector('#business-module-frame');
  let frame = await frameHandle.contentFrame();
  await frame.waitForSelector('[data-source-report-id]', { visible: true, timeout: 30000 });
  const importedReports = await frame.evaluate(() => {
    const cards = [...document.querySelectorAll('[data-source-report-id]')];
    return {
      count: cards.length,
      reportIds: cards.map((card) => card.dataset.reportId),
      sourceIds: cards.map((card) => card.dataset.sourceReportId),
      thumbnails: cards.filter((card) => card.querySelector('.report-card-preview > img')).length,
      deleteButtons: cards.filter((card) => card.querySelector('[aria-label="删除报表"]')).length,
    };
  });
  assert.equal(importedReports.count, EXPECTED_REPORTS);
  assert.equal(new Set(importedReports.reportIds).size, EXPECTED_REPORTS);
  assert.equal(new Set(importedReports.sourceIds).size, EXPECTED_REPORTS);
  assert.equal(importedReports.thumbnails, EXPECTED_REPORTS);
  assert.equal(importedReports.deleteButtons, EXPECTED_REPORTS);
  await page.screenshot({ path: path.join(OUTPUT, 'direct-report-list.png'), fullPage: false });

  await frame.click('.create-btn');
  await frame.waitForSelector('.report-create-dialog', { visible: true, timeout: 10000 });
  await frame.waitForFunction(() => [...document.querySelectorAll('.report-create-mode [role="radio"]')]
    .every((node) => !node.textContent.includes('加载中')), { timeout: 10000 });
  const createModes = await frame.$$eval('.report-create-mode [role="radio"]', (nodes) => nodes.map((node) => node.textContent.trim()));
  assert.equal(createModes.length, 2, 'The template creation mode must remain available');
  assert(createModes[0].includes('空白报表'));
  assert(createModes[1].includes('参考模板') && createModes[1].includes(`${EXPECTED_REPORTS} 可用`));
  await frame.evaluate(() => [...document.querySelectorAll('.report-create-mode [role="radio"]')]
    .find((node) => node.textContent.includes('参考模板')).click());
  await frame.waitForSelector('.report-template-card', { visible: true, timeout: 10000 });
  const templatePicker = await frame.evaluate(() => {
    const cards = [...document.querySelectorAll('.report-template-card')];
    const images = cards.map((card) => card.querySelector('.report-template-preview > img'));
    return {
      cards: cards.length,
      selected: cards.filter((card) => card.classList.contains('selected')).length,
      imageCount: images.filter(Boolean).length,
      realScreenshotCount: images.filter((image) => /\/report-thumbnails\/lynx-\d+\.png$/.test(new URL(image.src).pathname)).length,
      sourceLabels: cards.filter((card) => card.textContent.includes('智慧云脑模板')).length,
      lockedLabels: cards.filter((card) => card.textContent.includes('视觉锁定')).length,
    };
  });
  assert.deepEqual(templatePicker, {
    cards: EXPECTED_REPORTS,
    selected: 0,
    imageCount: EXPECTED_REPORTS,
    realScreenshotCount: EXPECTED_REPORTS,
    sourceLabels: EXPECTED_REPORTS,
    lockedLabels: EXPECTED_REPORTS,
  });
  await frame.click('.report-template-card');
  assert.equal(await frame.$$eval('.report-template-card.selected', (cards) => cards.length), 1);
  await new Promise((resolve) => setTimeout(resolve, 350));
  await page.screenshot({ path: path.join(OUTPUT, 'real-template-picker.png'), fullPage: false });

  await page.goto(BASE + '/templates', { waitUntil: 'networkidle0', timeout: 60000 });
  frame = await (await page.waitForSelector('#business-module-frame')).contentFrame();
  await frame.waitForSelector('[data-template-id]', { visible: true, timeout: 30000 });
  const templateCenter = await frame.evaluate(() => ({
    cards: document.querySelectorAll('[data-template-id]').length,
    images: document.querySelectorAll('.template-image > img').length,
    realScreenshots: [...document.querySelectorAll('.template-image > img')]
      .filter((image) => /\/report-thumbnails\/lynx-\d+\.png$/.test(new URL(image.src).pathname)).length,
    locked: [...document.querySelectorAll('[data-template-id]')]
      .filter((card) => card.textContent.includes('视觉锁定')).length,
    sourceImported: [...document.querySelectorAll('[data-template-id]')]
      .filter((card) => card.textContent.includes('智慧云脑模板')).length,
    deleteButtons: [...document.querySelectorAll('[data-template-id]')]
      .filter((card) => [...card.querySelectorAll('button')].some((button) => button.textContent.trim() === '删除')).length,
  }));
  assert.deepEqual(templateCenter, {
    cards: EXPECTED_REPORTS,
    images: EXPECTED_REPORTS,
    realScreenshots: EXPECTED_REPORTS,
    locked: EXPECTED_REPORTS,
    sourceImported: EXPECTED_REPORTS,
    deleteButtons: EXPECTED_REPORTS,
  });
  await page.screenshot({ path: path.join(OUTPUT, 'real-template-center.png'), fullPage: false });

  await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 1 });
  await page.goto(BASE + '/dashboards', { waitUntil: 'networkidle0', timeout: 60000 });
  frame = await (await page.waitForSelector('#business-module-frame')).contentFrame();
  await frame.waitForSelector('[data-source-report-id]', { visible: true, timeout: 30000 });
  const mobile = await frame.evaluate(() => {
    const card = document.querySelector('[data-source-report-id]');
    const remove = card?.querySelector('[aria-label="删除报表"]');
    return {
      noPageOverflow: document.documentElement.scrollWidth <= innerWidth + 1,
      cardFits: Boolean(card && card.getBoundingClientRect().right <= innerWidth + 1),
      deleteVisible: Boolean(remove && remove.getClientRects().length),
    };
  });
  assert.deepEqual(mobile, { noPageOverflow: true, cardFits: true, deleteVisible: true });
  await frame.click('.create-btn');
  await frame.waitForSelector('.report-create-dialog', { visible: true, timeout: 10000 });
  await frame.waitForFunction(() => [...document.querySelectorAll('.report-create-mode [role="radio"]')]
    .every((node) => !node.textContent.includes('加载中')), { timeout: 10000 });
  await frame.evaluate(() => [...document.querySelectorAll('.report-create-mode [role="radio"]')]
    .find((node) => node.textContent.includes('参考模板')).click());
  await frame.waitForSelector('.report-template-card', { visible: true, timeout: 10000 });
  const mobilePicker = await frame.evaluate(() => {
    const dialog = document.querySelector('.report-create-dialog');
    const box = dialog?.getBoundingClientRect();
    const modes = [...document.querySelectorAll('.report-create-mode [role="radio"]')];
    const cards = [...document.querySelectorAll('.report-template-card')];
    const firstCard = cards[0]?.getBoundingClientRect();
    return {
      dialogFits: Boolean(box && box.left >= 0 && box.right <= innerWidth + 1),
      modesFit: modes.length === 2 && modes.every((node) => node.getBoundingClientRect().right <= innerWidth + 1),
      cardCount: cards.length,
      firstCardFits: Boolean(firstCard && firstCard.left >= box.left && firstCard.right <= box.right + 1),
      noHorizontalOverflow: document.documentElement.scrollWidth <= innerWidth + 1,
    };
  });
  assert.deepEqual(mobilePicker, {
    dialogFits: true,
    modesFit: true,
    cardCount: EXPECTED_REPORTS,
    firstCardFits: true,
    noHorizontalOverflow: true,
  });
  await new Promise((resolve) => setTimeout(resolve, 350));
  await page.screenshot({ path: path.join(OUTPUT, 'real-template-picker-mobile.png'), fullPage: false });
  assert.deepEqual(failures, []);
  fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({
    ok: true,
    reportCount: importedReports.count,
    templateCount: templateCenter.cards,
    createModes,
    templatePicker,
    templateCenter,
    mobile,
    mobilePicker,
    failures,
  }, null, 2));
  console.log(JSON.stringify({ ok: true, checks: 24, output: OUTPUT }));
})().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
}).finally(async () => {
  if (browser) await browser.close();
});
