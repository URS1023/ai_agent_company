/*
 * 中文说明（人工操作式工作流旅程）：
 * 验证行为：只用真实 Chrome 输入完成工作流相关操作，检验普通用户路径。
 * 约束：不调用应用 API、不使用 mock、不注入页面状态；配套本地 http 桩仅代表外部被调用方。
 * 运行方式：需隔离服务与本机浏览器；会产生工作流运行记录（隔离环境）。
 *
 * Human-style acceptance: real Chrome input only; no application API calls, mocks or state injection.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const puppeteer = require('puppeteer-core');

const BASE = process.env.TEST5_HUMAN_BASE_URL || 'http://127.0.0.1:8037';
const PASSWORD = process.env.TEST5_HUMAN_PASSWORD;
if (!PASSWORD)
  throw new Error('TEST5_HUMAN_PASSWORD is required');
const RUN = Date.now().toString(36);
const OUTPUT = path.resolve('.implementation/ui-results/human-workflow-' + RUN);
const WORKFLOW = '人工旅程 · 温度检查 ' + RUN;
const SOURCE = '人工旅程 · 本机温度接口 ' + RUN;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

(async () => {
  assert.equal(new URL(BASE).hostname, '127.0.0.1');
  assert.equal(new URL(BASE).port, '8037', 'Only the designated fresh human acceptance instance is allowed');
  fs.mkdirSync(OUTPUT, { recursive: true });
  const requests = [];
  const fixture = http.createServer((request, response) => {
    requests.push({ path: request.url, at: new Date().toISOString() });
    response.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
    response.end(JSON.stringify({ items: [{ device: '一号泵', temperature: '84.50' }, { device: '二号泵', temperature: '72.40' }] }));
  });
  await new Promise(resolve => fixture.listen(0, '127.0.0.1', resolve));
  const browser = await puppeteer.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 960 });
  let frame, stage = 'login';
  const checks = [], errors = [], actions = [], observations = [];
  page.on('pageerror', error => errors.push(error.message));
  const log = label => { checks.push(label); console.log('PASS ' + label); };
  async function screenshot(name) { await page.screenshot({ path: path.join(OUTPUT, name + '.png') }); }
  async function visibleText(target, selector, text, exact = true) {
    for (const handle of await target.$$(selector)) {
      const match = await handle.evaluate((element, expected, exact) => {
        const content = (element.textContent || '').trim();
        return element.getClientRects().length > 0 && !element.disabled && (exact ? content === expected : content.includes(expected));
      }, text, exact);
      if (match) return handle;
    }
    return null;
  }
  async function click(text, selector = 'button', target = frame, exact = true) {
    const deadline = Date.now() + 20000;
    while (Date.now() < deadline) {
      const handle = await visibleText(target, selector, text, exact);
      if (handle) {
        // Wait for any closing modal to stop covering the intended pointer target.
        await target.waitForFunction(element => {
          const rect = element.getBoundingClientRect();
          if (rect.bottom <= 0 || rect.top >= innerHeight || rect.right <= 0 || rect.left >= innerWidth) return true;
          const x = (Math.max(0, rect.left) + Math.min(innerWidth, rect.right)) / 2;
          const y = (Math.max(0, rect.top) + Math.min(innerHeight, rect.bottom)) / 2;
          return element.contains(document.elementFromPoint(x, y));
        }, { timeout: 10000 }, handle);
        // Puppeteer synthesizes normal pointer input; JS evaluation is read-only.
        await handle.click();
        actions.push({ stage, action: 'click', text });
        await sleep(100);
        return;
      }
      await sleep(100);
    }
    throw new Error('Visible control missing: ' + text);
  }
  async function control(label, root = 'body', target = frame) {
    const handle = await target.evaluateHandle((label, root) => {
      for (const container of [...document.querySelectorAll(root)].reverse()) {
        for (const candidate of container.querySelectorAll('label')) {
          if (!candidate.getClientRects().length) continue;
          const direct = [...candidate.childNodes].filter(node => node.nodeType === Node.TEXT_NODE).map(node => node.textContent).join('').trim();
          if (direct === label || direct.startsWith(label)) return candidate.querySelector('input,textarea,select');
        }
      }
      return null;
    }, label, root);
    assert(handle.asElement(), 'Field missing: ' + label);
    return handle.asElement();
  }
  async function type(label, value, root = 'body', target = frame) {
    const handle = await control(label, root, target);
    await handle.click();
    await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
    await page.keyboard.press('Backspace');
    await page.keyboard.type(value);
    await page.keyboard.press('Tab');
    actions.push({ stage, action: 'type', label, value });
    await sleep(120);
  }
  async function select(label, text, root = 'body', target = frame) {
    const handle = await control(label, root, target);
    const options = await handle.evaluate(element => [...element.options].map(option => ({ text: option.textContent.trim(), disabled: option.disabled })));
    const index = options.findIndex(option => option.text === text);
    assert(index >= 0 && !options[index].disabled, 'Selectable option missing: ' + text);
    await handle.click(); await handle.press('Home');
    for (let i = 0; i < index; i++) await handle.press('ArrowDown');
    await handle.press('Enter'); await handle.press('Tab');
    actions.push({ stage, action: 'keyboard-select', label, text });
    await sleep(150);
  }
  async function check(label, root) {
    const handle = await control(label, root);
    if (!await handle.evaluate(element => element.checked)) await handle.click();
    assert(await handle.evaluate(element => element.checked), label);
    actions.push({ stage, action: 'checkbox', label });
  }
  async function more(text) {
    const opened = await frame.$eval('.wf-more-actions', element => element.open);
    if (!opened) await click('更多操作', '.wf-more-actions summary');
    await click(text, '.wf-more-menu button');
  }
  async function addNode(title) {
    await click('添加节点');
    if (!observations.length) {
      observations.push(await frame.evaluate(() => ({ paletteHeight: document.querySelector('.wf-palette').getBoundingClientRect().height, canvasTop: document.querySelector('.wf-canvas-scroll').getBoundingClientRect().top, viewportHeight: innerHeight, nodeChoices: document.querySelectorAll('.wf-palette button').length })));
      await screenshot('02a-node-library-before-selection');
    }
    if (await frame.$('.wf-palette-search input')) {
      assert(await frame.$eval('.wf-palette-host', element => element.getBoundingClientRect().height === 0), 'The node library must not push the canvas down');
      await type('查找节点', title, '.wf-palette');
      assert.equal(await frame.$$eval('.wf-palette-list button', elements => elements.length), 1, 'Search should make the requested node easy to find');
      if (title === 'HTTP 采集') await screenshot('02b-search-http-node');
    }
    await click(title, '.wf-palette button', frame, false);
    await frame.waitForFunction(title => document.querySelector('.wf-node.selected span')?.textContent === title, {}, title);
  }
  async function closeResult() {
    await click('关闭此对话框', '.el-dialog__headerbtn');
  }
  async function closeVisibleDialog() {
    const handle = await frame.evaluateHandle(() => [...document.querySelectorAll('.el-dialog__headerbtn')].find(element => {
      const rect = element.getBoundingClientRect();
      return element.getClientRects().length && element.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2));
    }));
    assert(handle.asElement(), 'No visible dialog close control');
    await handle.asElement().click();
    await sleep(400);
  }
  try {
    await page.goto(BASE, { waitUntil: 'networkidle0' });
    await (await page.waitForSelector('#login-username')).click(); await page.keyboard.type('workspace-admin');
    await page.click('#login-password'); await page.keyboard.type(PASSWORD);
    await page.click('#login-btn');
    await page.waitForSelector('.enterprise-area-grid');
    await click('业务应用', '.enterprise-primary a', page, false);
    frame = await (await page.waitForSelector('#business-module-frame')).contentFrame();
    await frame.waitForSelector('.wf-list-filter');
    await screenshot('01-workflow-list');
    log('从首页可见导航进入工作流列表');

    stage = 'create-workflow-and-source';
    await click('新建工作流'); await frame.waitForSelector('.wf-editor');
    await type('流程名称', WORKFLOW, '.wf-meta');
    await click('用途与适用业务', '.wf-meta-details summary');
    await type('用途说明', '读取两台水泵温度，折算后超过40时提示检查冷却系统。', '.wf-meta');
    await addNode('HTTP 采集');
    await click('新建 HTTP 数据源', '.wf-source-actions button');
    await frame.waitForSelector('.http-config-dialog .http-form');
    await type('数据源名称', SOURCE, '.http-config-dialog .http-form');
    await type('接口地址', `http://127.0.0.1:${fixture.address().port}/temperatures`, '.http-config-dialog .http-form');
    await type('用途说明', '浏览器人工旅程：只读取本机提供的两条温度数据', '.http-config-dialog .http-form');
    await type('记录 JSONPath', '$.items', '.http-config-dialog .http-form');
    await check('允许访问此本机 / 内网设备', '.http-config-dialog .http-form');
    await check('确认仅查询数据，允许报表自动调用', '.http-config-dialog .http-form');
    await click('创建数据源', '.http-form-footer button');
    await frame.waitForSelector('.http-config-dialog .http-form', { hidden: true });
    await frame.waitForSelector('.wf-source-summary');
    assert((await frame.$eval('.wf-source-summary', element => element.textContent)).includes(SOURCE));
    assert.equal(requests.length, 0, 'Saving configuration must not call the source');
    await screenshot('02-http-source-created-in-node');
    log('通过节点表单创建HTTP数据源并自动绑定，保存尚未发出设备请求');

    stage = 'configure-nodes-and-edges';
    await addNode('计算字段');
    await type('输出字段', '温度折算', '.wf-properties');
    await type('计算公式', 'temperature / 2', '.wf-properties');
    await addNode('逐项规则判定');
    await type('规则名称', '折算温度超限', '.wf-properties');
    await type('判断字段', '温度折算', '.wf-properties');
    await type('比较值', '40', '.wf-properties');
    await type('问题说明', '检查冷却系统', '.wf-properties');
    await click('连线配置 · 1 条', '.wf-connections summary');
    await click('移除', '.wf-edge-list button');
    for (const [from, to] of [['开始', 'HTTP 采集'], ['HTTP 采集', '计算字段'], ['计算字段', '逐项规则判定'], ['逐项规则判定', '结束']]) {
      await select('来源节点', from, '.wf-connect-form');
      await select('目标节点', to, '.wf-connect-form');
      await click('添加连线', '.wf-connect-form button');
    }
    await click('自动排布');
    await click('适应画布');
    await more('检查配置');
    await frame.waitForFunction(() => document.querySelector('.wf-validation')?.textContent.includes('结构检查通过'));
    await click('保存草稿');
    await frame.waitForFunction(() => document.querySelector('.wf-version-state')?.textContent.includes('修改已保存'));
    assert.equal(requests.length, 0);
    await screenshot('03-configured-and-saved');
    log('通过可见节点和连线表单建成五节点流程，检查通过且草稿保存成功');

    stage = 'reopen-and-run-draft';
    await click('返回流程列表'); await frame.waitForSelector('.wf-list-filter');
    await click(WORKFLOW, '.wf-table .wf-link'); await frame.waitForSelector('.wf-editor');
    assert.equal(await (await control('流程名称', '.wf-meta')).evaluate(element => element.value), WORKFLOW);
    assert.equal(await frame.$$eval('.wf-node', elements => elements.length), 5);
    log('从列表重新打开后，名称、五个节点和已保存状态保持');
    await click('运行');
    await type('开始节点输入（JSON 对象）', '{"工单":', '.el-dialog .workflow-ui');
    await click('确认执行', '.el-dialog__footer button');
    await frame.waitForFunction(() => [...document.querySelectorAll('.el-dialog [role=alert]')].some(element => element.getClientRects().length && element.textContent.includes('有效 JSON')));
    assert.equal(requests.length, 0);
    await screenshot('04-run-input-error');
    await type('开始节点输入（JSON 对象）', '{}', '.el-dialog .workflow-ui');
    await click('确认执行', '.el-dialog__footer button');
    await frame.waitForSelector('.wf-result', { visible: true });
    await frame.waitForFunction(() => document.querySelector('.wf-result h2')?.textContent.includes('执行完成'));
    const result = await frame.$eval('.wf-result', element => element.textContent);
    assert(result.includes('42.25') && result.includes('检查冷却系统') && result.includes('一号泵'), result);
    assert.equal(requests.length, 1);
    await screenshot('05-real-http-result');
    log('错误JSON就地提示且不执行；修正后真实HTTP运行得到42.25及检查冷却系统问题');
    await closeVisibleDialog();

    stage = 'publish-and-preserve-edits';
    await click('发布');
    await click('确认', '.el-message-box button');
    await frame.waitForFunction(() => document.querySelector('.wf-version-state')?.textContent.includes('已发布'));
    await click('用途与适用业务', '.wf-meta-details summary');
    await type('用途说明', '未保存说明：每班核查温度，异常交给值班人员处理', '.wf-meta');
    const stay = new Promise(resolve => page.once('dialog', async dialog => { assert.equal(dialog.type(), 'confirm'); await dialog.dismiss(); resolve(); }));
    await click('返回流程列表'); await stay;
    assert((await (await control('用途说明', '.wf-meta')).evaluate(element => element.value)).startsWith('未保存说明'));
    await screenshot('06-cancel-leave-preserves-edit');
    await click('保存草稿');
    await frame.waitForFunction(() => document.querySelector('.wf-version-state')?.textContent.includes('修改已保存'));
    log('正式版本发布成功；拒绝离开保留编辑，再保存形成独立新草稿');

    stage = 'history-and-real-version-selection';
    await more('版本与运行');
    await click('查看结果', '.el-dialog .wf-table button');
    await frame.waitForSelector('.wf-result', { visible: true });
    assert.equal(requests.length, 1, 'Reopening history must not run HTTP again');
    await closeVisibleDialog(); await closeVisibleDialog();
    await click('运行');
    await select('执行版本', '已发布正式版本', '.el-dialog .workflow-ui');
    await click('确认执行', '.el-dialog__footer button');
    await frame.waitForSelector('.wf-result', { visible: true });
    await frame.waitForFunction(() => document.querySelector('.wf-result h2')?.textContent.includes('执行完成'));
    assert.equal(requests.length, 2);
    await screenshot('07-published-version-result');
    await closeVisibleDialog();
    log('历史结果查看不重复请求；选择正式版本后可再次实际运行');

    stage = 'discard-confirmed-edit';
    await type('流程名称', WORKFLOW + ' 放弃此修改', '.wf-meta');
    const leave = new Promise(resolve => page.once('dialog', async dialog => { assert.equal(dialog.type(), 'confirm'); await dialog.accept(); resolve(); }));
    await click('返回流程列表'); await leave;
    await frame.waitForSelector('.wf-list-filter');
    await click(WORKFLOW, '.wf-table .wf-link'); await frame.waitForSelector('.wf-editor');
    assert.equal(await (await control('流程名称', '.wf-meta')).evaluate(element => element.value), WORKFLOW);
    log('确认放弃修改后再次打开，仍为最后保存的流程名称');
    await screenshot('08-final-saved-workflow');

    stage = 'real-wait-cancel-and-resume';
    await addNode('提问');
    await type('等待问题', '请核查温度问题并填写值班人', '.wf-properties');
    await type('回答字段', 'reviewer', '.wf-properties');
    await click('连线配置 · 4 条', '.wf-connections summary');
    await click('移除', '.wf-edge-list button[aria-label="移除连线 逐项规则判定 到 结束"]');
    for (const [from, to] of [['逐项规则判定', '提问'], ['提问', '结束']]) {
      await select('来源节点', from, '.wf-connect-form');
      await select('目标节点', to, '.wf-connect-form');
      await click('添加连线', '.wf-connect-form button');
    }
    await click('自动排布'); await click('保存草稿');
    await frame.waitForFunction(() => document.querySelector('.wf-version-state')?.textContent.includes('修改已保存'));
    await click('运行'); await click('确认执行', '.el-dialog__footer button');
    await frame.waitForSelector('.wf-result', { visible: true });
    await frame.waitForFunction(() => document.querySelector('.wf-result h2')?.textContent.includes('等待输入'));
    assert.equal(requests.length, 3);
    assert.equal(await frame.$$eval('.wf-run-controls button', elements => elements.find(element => element.textContent.trim() === '提交并继续').disabled), true);
    await screenshot('09-real-waiting-for-person');
    await click('取消运行', '.wf-run-controls button'); await click('确认取消', '.el-message-box button');
    await frame.waitForFunction(() => document.querySelector('.wf-result h2')?.textContent.includes('已取消'));
    assert.equal(requests.length, 3);
    await screenshot('10-real-cancelled-run');
    await closeVisibleDialog();
    await click('运行'); await click('确认执行', '.el-dialog__footer button');
    await frame.waitForSelector('.wf-result', { visible: true });
    await frame.waitForFunction(() => document.querySelector('.wf-result h2')?.textContent.includes('等待输入'));
    assert.equal(requests.length, 4);
    await type('请核查温度问题并填写值班人', '值班员李工', '.wf-run-controls');
    await click('提交并继续', '.wf-run-controls button');
    await frame.waitForFunction(() => document.querySelector('.wf-result h2')?.textContent.includes('执行完成'));
    assert.equal(requests.length, 4, 'Resuming a real waiting run must not repeat completed HTTP collection');
    assert.equal(await (await control('查看节点', '.wf-result')).evaluate(element => element.selectedOptions[0].textContent.trim()), '结束 · 执行完成');
    assert((await frame.$eval('.wf-value-output', element => element.textContent)).includes('值班员李工'), 'The completed answer should be visible without opening raw output');
    await screenshot('11-real-resumed-run');
    log('真实提问节点等待输入、取消、重新运行及填写回答继续全部可用，不重复已完成HTTP请求');

    stage = 'preserve-manually-selected-result-node';
    await closeVisibleDialog();
    await click('运行'); await click('确认执行', '.el-dialog__footer button');
    await frame.waitForSelector('.wf-result', { visible: true });
    await frame.waitForFunction(() => document.querySelector('.wf-result h2')?.textContent.includes('等待输入'));
    assert.equal(requests.length, 5);
    assert.equal(await (await control('请核查温度问题并填写值班人', '.wf-run-controls')).evaluate(element => element.value), '', 'A new run should ask for its own answer');
    await sleep(400);
    await screenshot('12a-new-run-requires-new-answer');
    await select('查看节点', 'HTTP 采集 · 执行完成', '.wf-result');
    await type('请核查温度问题并填写值班人', '值班员王工', '.wf-run-controls');
    await sleep(3500);
    assert.equal(await (await control('请核查温度问题并填写值班人', '.wf-run-controls')).evaluate(element => element.value), '值班员王工', 'Refreshing the same waiting run must preserve the current answer');
    await click('提交并继续', '.wf-run-controls button');
    await frame.waitForFunction(() => document.querySelector('.wf-result h2')?.textContent.includes('执行完成'));
    assert.equal(requests.length, 5);
    assert.equal(await (await control('查看节点', '.wf-result')).evaluate(element => element.selectedOptions[0].textContent.trim()), 'HTTP 采集 · 执行完成');
    await screenshot('12-manually-selected-node-preserved');
    log('新运行不带旧回答、同一次等待刷新保留输入；默认展示终点和回答，用户主动选择HTTP节点时保留该选择');
    assert.deepEqual(errors, []);
  } catch (error) {
    await sleep(400);
    await screenshot('failure-' + stage).catch(() => {});
    errors.push({ stage, error: error.message });
    console.error('FAILED stage=' + stage, error);
    process.exitCode = 1;
  } finally {
    fs.writeFileSync(path.join(OUTPUT, 'report.json'), JSON.stringify({ workflowName: WORKFLOW, sourceName: SOURCE, checks, errors, sourceRequests: requests, observations, actions }, null, 2));
    await browser.close();
    await new Promise(resolve => fixture.close(resolve));
    console.log('Evidence: ' + OUTPUT);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
