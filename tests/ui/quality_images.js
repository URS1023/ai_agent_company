/*
 * 中文说明（质检图片链路的端到端浏览器验收）：
 * 验证行为：普通设备表单 -> 专用流程 -> 报告 -> 处置，且图片上传/预览链路可用。
 * 运行方式：需隔离服务（TEST5_E2E_BASE_URL，默认 8011）+ 本机 Edge + TEST5_E2E_PASSWORD；
 *   本地 http 桩提供被检测数据。
 * 边界：会上传图片并写入质检记录（隔离环境）。
 */
/* Real Edge: ordinary equipment forms -> dedicated workflow -> report -> handling. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const puppeteer = require('puppeteer-core');
const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011';
const RUN = Date.now().toString(36), OUTPUT = path.resolve('.implementation/ui-results/quality-images-' + RUN);
const checks = [], failures = [], violations = [], calls = [], created = [];
let browser, page, frame, fixture, sourceId, workflowId, deviceId;
const imageIds = [];
const pass = text => { checks.push(text); console.log('PASS ' + text); };
async function button(label, selector = 'button') {
  await frame.waitForFunction((label, selector) => [...document.querySelectorAll(selector)].some(el => el.getClientRects().length && el.textContent.trim() === label && !el.disabled), {}, label, selector);
  for (const handle of await frame.$$(selector)) if (await handle.evaluate((el, text) => el.getClientRects().length && el.textContent.trim() === text, label)) {
    await handle.scrollIntoView(); await frame.waitForFunction(el => { const rect = el.getBoundingClientRect(); return el.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2)); }, {}, handle); await handle.click(); return;
  }
  throw new Error('Missing button ' + label);
}
async function field(label, value, root = '.workflow-ui') {
  await frame.waitForFunction((label, root) => [...document.querySelectorAll(root + ' label')].some(el => el.getClientRects().length && el.firstChild?.textContent?.trim() === label && el.querySelector('input,textarea,select')), {}, label, root);
  const handle = await frame.evaluateHandle((label, root) => [...document.querySelectorAll(root + ' label')].find(el => el.getClientRects().length && el.firstChild?.textContent?.trim() === label)?.querySelector('input,textarea,select'), label, root);
  const element = handle.asElement(); assert(element, 'Missing field ' + label); await element.scrollIntoView();
  if (await element.evaluate(el => el.tagName === 'SELECT')) { await frame.waitForFunction((el, value) => !el.disabled && [...el.options].some(option => option.value === value), {}, element, value); return element.select(value); }
  await element.click({ clickCount: 3 }); await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control'); await element.type(value); await element.press('Tab');
}
async function api(url, method = 'GET', body) { return page.evaluate(async (url, method, body) => { const response = await fetch(url, { method, headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + localStorage.getItem('agent_token'), 'X-Workspace-Id': localStorage.getItem('agent_workspace_id') }, body: body === undefined ? undefined : JSON.stringify(body) }); return { status: response.status, body: await response.json() }; }, url, method, body); }
async function visit(route) { assert.equal((await page.goto(BASE + route, { waitUntil: 'networkidle0' })).status(), 200); frame = await (await page.waitForSelector('#business-module-frame')).contentFrame(); await frame.waitForSelector('.quality-ui', { visible: true }); }
async function audit(label) { await frame.waitForFunction(() => !document.querySelector('[class*="enter-active"],[class*="leave-active"]')); await frame.addScriptTag({ path: require.resolve('axe-core/axe.min.js') }); const found = await frame.evaluate(async () => (await axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] } })).violations.map(item => ({ id: item.id, impact: item.impact, nodes: item.nodes.map(node => ({ target: node.target, summary: node.failureSummary })) }))); violations.push(...found.map(item => ({ label, ...item }))); }

async function save(kind) {
  const waiting = page.waitForResponse(response => response.url().endsWith('/api/v1/quality/' + kind) && response.request().method() === 'POST');
  await button(kind === 'inspections' ? '保存质检记录' : '保存配置');
  const response = await waiting; assert.equal(response.status(), 201, await response.text());
  const row = (await response.json()).data; created.push([kind, row.id]);
  await frame.waitForSelector(kind === 'inspections' ? '#qc-inspection-form' : '#qc-config-form', { hidden: true }); return row;
}
async function recorded(name) {
  const checkbox = await frame.evaluateHandle(name => [...document.querySelectorAll('.qc-observation')].find(el => el.querySelector('legend')?.textContent.includes(name))?.querySelector('input[type="checkbox"]'), name);
  assert(checkbox.asElement()); await checkbox.asElement().click();
}
(async () => {
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, 'isolated'); assert(process.env.TEST5_E2E_PASSWORD);
  assert(['127.0.0.1', 'localhost'].includes(new URL(BASE).hostname)); assert.equal((await (await fetch(BASE + '/api/health')).json()).app, 'test');
  fs.mkdirSync(OUTPUT, { recursive: true });
  fixture = http.createServer((request, response) => {
    let data = ''; request.on('data', chunk => { data += chunk; }); request.on('end', () => {
      const body = JSON.parse(data); calls.push(body);
      response.writeHead(200, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({ items: [{ sampleId: body.sampleId, defects: [{ defectType: '测试服务划痕', confidence: '0.97', severity: 'moderate', bbox: [0.2, 0.3, 0.35, 0.2], suggest: '接口测试结果，需接入真实检测服务' }] }] }));
    });
  });
  await new Promise(resolve => fixture.listen(0, '127.0.0.1', resolve));
  browser = await puppeteer.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true }); page = await browser.newPage(); await page.setViewport({ width: 1600, height: 1100 });
  page.on('pageerror', cause => failures.push(cause.message)); page.on('response', response => { if (response.status() >= 500) failures.push(response.status() + ' ' + response.url()); }); page.on('dialog', dialog => dialog.accept());
  await page.goto(BASE, { waitUntil: 'networkidle0' }); await page.type('#login-username', 'workspace-admin'); await page.type('#login-password', process.env.TEST5_E2E_PASSWORD); await page.click('#login-btn'); await page.waitForSelector('.overview-intro');
  const encodedImage = await page.evaluate(() => {
    const canvas = document.createElement('canvas'); canvas.width = 640; canvas.height = 400; const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#e9edf2'; ctx.fillRect(0, 0, 640, 400); ctx.fillStyle = '#7e8c9c'; ctx.fillRect(80, 60, 480, 280); ctx.fillStyle = '#b4c0cb'; ctx.fillRect(90, 70, 460, 260);
    ctx.strokeStyle = '#536476'; ctx.lineWidth = 8; ctx.beginPath(); ctx.moveTo(160, 170); ctx.lineTo(340, 190); ctx.stroke();
    ctx.fillStyle = '#17263c'; ctx.font = '16px sans-serif'; ctx.fillText('SYNTHETIC UI TEST SAMPLE - NOT INDUSTRIAL EVIDENCE', 45, 375);
    return canvas.toDataURL('image/png').split(',')[1];
  });
  const original = Buffer.from(encodedImage, 'base64'), imagePath = path.join(OUTPUT, 'synthetic-sample.png'); fs.writeFileSync(imagePath, original);
  const source = await api('/api/v1/collection/sources', 'POST', { name: 'qc-image-source-' + RUN, url: 'http://127.0.0.1:' + fixture.address().port, method: 'POST', bodies: [{ sampleId: '${input.sampleId}', image: '${input.imageBase64}', mime: '${input.imageMimeType}', sha: '${input.imageSha256}' }], recordPath: '$.items', allowPrivate: true, allowAutomatic: true }); assert.equal(source.status, 201, JSON.stringify(source.body)); sourceId = source.body.data.id;
  const equipment = await api('/api/v1/alerts/devices', 'POST', { code: 'QCI-' + RUN, name: '图片接口测试设备 ' + RUN, datasourceId: sourceId, category: 'camera', domains: ['quality'] }); assert.equal(equipment.status, 201); deviceId = equipment.body.data.id;
  const metric = await api('/api/v1/quality/metrics', 'POST', { name: '原图外观 ' + RUN, category: 'appearance' }); assert.equal(metric.status, 201); created.push(['metrics', metric.body.data.id]);
  const template = await api('/api/v1/quality/templates', 'POST', { name: '原图标准 ' + RUN, metricIds: [metric.body.data.id] }); assert.equal(template.status, 201); created.push(['templates', template.body.data.id]);
  await visit('/quality/inspections'); await button('使用说明'); await frame.waitForSelector('.business-process-guide', { visible: true });
  assert.equal(await frame.$$eval('.business-guide-flow > li', items => items.length), 5);
  assert((await frame.$eval('.business-process-guide', item => item.innerText)).includes('从检验标准到可追溯结论'));
  await page.screenshot({ path: path.join(OUTPUT, 'quality-usage-guide.png'), fullPage: true }); await page.keyboard.press('Escape'); await frame.waitForSelector('.business-process-guide', { hidden: true });
  pass('Quality guidance explains metrics, immutable templates, detection, review and evidence only when requested');
  await button('新增质检'); await field('质检模板', template.body.data.id); await field('固定模板修订', '1'); await field('受检设备', deviceId); await field('样本 / 批次标识', 'MANUAL-IMAGE-' + RUN); await recorded('原图外观');
  const uploading = page.waitForResponse(response => response.url().endsWith('/api/v1/quality/images') && response.request().method() === 'POST');
  await (await frame.$('input[type="file"]')).uploadFile(imagePath); const uploadResponse = await uploading; assert.equal(uploadResponse.status(), 201, await uploadResponse.text()); const uploaded = (await uploadResponse.json()).data; imageIds.push(uploaded.id);
  assert.equal(uploaded.sha256, require('node:crypto').createHash('sha256').update(original).digest('hex'));
  await frame.waitForFunction(() => document.querySelector('.qc-original-image')?.naturalWidth === 640); assert.equal(calls.length, 0);
  pass('Ordinary quality form uploads original bytes, verifies the hash and displays an authenticated image without inference');
  await field('外观检查结果', 'defects'); await field('缺陷类型', '人工观察划痕'); await field('缺陷级别', 'moderate'); await field('置信度', '0.95'); await button('在图上框选');
  const canvas = await frame.$('.qc-image-canvas'); await canvas.scrollIntoView(); const rect = await canvas.boundingBox(); assert(rect);
  await page.mouse.move(rect.x + rect.width * 0.15, rect.y + rect.height * 0.30); await page.mouse.down(); await page.mouse.move(rect.x + rect.width * 0.55, rect.y + rect.height * 0.55, { steps: 8 }); await page.mouse.up();
  await frame.waitForFunction(() => document.querySelector('.qc-defect-box') && [...document.querySelectorAll('label')].some(el => el.firstChild?.textContent === '左侧 X' && Number(el.querySelector('input')?.value) > 0));
  await field('左侧 X', '0.15'); await field('顶部 Y', '0.30'); await field('框宽 W', '0.40'); await field('框高 H', '0.25'); await button('结束框选');
  await audit('Image upload and keyboard/drag annotation'); await page.screenshot({ path: path.join(OUTPUT, 'image-annotation-form.png'), fullPage: true });
  const manual = await save('inspections'); assert.equal(manual.outcome, 'failed'); assert.deepEqual(manual.results[0].observation.defects[0].bbox, [0.15,0.30,0.40,0.25]); assert.equal(manual.results[0].provenance.image.id, uploaded.id);
  pass('Pointer drawing and keyboard coordinates save the same normalized defect box as manual provenance');
  await frame.waitForFunction(() => document.querySelector('.qc-picture-evidence .qc-original-image')?.naturalWidth === 640);
  assert.equal(await frame.$('.qc-picture-evidence .qc-image-drawing'), null); await field('图片缩放', '2'); await frame.waitForFunction(() => document.querySelector('.qc-image-scroll').scrollWidth > document.querySelector('.qc-image-scroll').clientWidth);
  const visibility = await frame.evaluateHandle(() => [...document.querySelectorAll('label')].find(el => el.textContent.includes('显示缺陷标注'))?.querySelector('input')); await visibility.asElement().click(); assert.equal(await frame.$('.qc-picture-evidence .qc-defect-box'), null); await visibility.asElement().click(); await field('图片缩放', '1');
  const exported = page.waitForResponse(response => response.url().endsWith('/inspections/' + manual.id + '/export')); await button('下载质检报告'); const exportResponse = await exported; assert.equal(exportResponse.status(), 200); const html = await exportResponse.text(); assert(html.includes('data:image/png;base64,' + encodedImage)); assert(html.includes(uploaded.sha256)); assert.equal(calls.length, 0);
  await (await frame.$('.qc-picture-evidence')).scrollIntoView(); await audit('Persisted image report'); await page.screenshot({ path: path.join(OUTPUT, 'image-report.png'), fullPage: true });
  pass('Historical report has read-only images, annotation visibility, zoom and self-contained export without detector requests');
  const node = (id, kind, config = {}) => ({ id, kind, title: id, config });
  const nodes = [node('start','start'), node('appearance','qc-appearance',{ deviceId, datasourceId: sourceId, datasourceVersion: 1, metricId: metric.body.data.id, metricVersion: 1, requestMode: 'input' }), node('evaluate','qc-evaluate',{ deviceId, templateId: template.body.data.id, templateRevision: 1, sampleId: 'AUTO-' + RUN }), node('end','end')];
  const graph = { nodes, edges: nodes.slice(1).map((item,index) => ({ id:'e'+index, source:nodes[index].id, target:item.id, port:'out' })) };
  const flow = await api('/api/v1/workflows', 'POST', { name: '动态图片流程 ' + RUN, domains: ['quality'], graph }); assert.equal(flow.status, 201, JSON.stringify(flow.body)); workflowId = flow.body.data.id;
  await page.goto(BASE + '/workflows/' + workflowId, { waitUntil: 'networkidle0' }); frame = await (await page.waitForSelector('#business-module-frame')).contentFrame(); await frame.waitForSelector('.wf-editor'); await button('自动排布'); await frame.click('.wf-node[data-node-id="appearance"]'); await field('请求参数来源', 'input', '.wf-properties'); await field('样本编号字段', 'sampleId', '.wf-properties'); await field('原图文件标识字段', 'imageFileId', '.wf-properties');
  await audit('Dynamic detector node configuration'); await page.screenshot({ path: path.join(OUTPUT, 'dynamic-image-node.png'), fullPage: true }); await button('保存草稿'); await frame.waitForFunction(() => !document.querySelector('.wf-toolbar')?.textContent.includes('未保存'));
  await button('运行'); await field('开始节点输入（JSON 对象）', JSON.stringify({data:[{sampleId:'AUTO-' + RUN, imageFileId:uploaded.id}]}), '.el-dialog .workflow-ui');
  const executing = page.waitForResponse(response => response.url().endsWith('/runs') && response.request().method() === 'POST'); await button('确认执行'); const executionResponse = await executing; assert.equal(executionResponse.status(), 200, await executionResponse.text()); const run = (await executionResponse.json()).data; assert.equal(calls.length, 1); assert.equal(calls[0].sampleId, 'AUTO-' + RUN); assert(Buffer.from(calls[0].image, 'base64').equals(original)); assert.equal(calls[0].sha, uploaded.sha256);
  const reportId = run.result.outputs.end.qualityInspectionId; created.push(['inspections', reportId]); await frame.waitForSelector('.wf-result'); await button('打开质检业务报告','a'); await frame.waitForFunction(() => document.querySelector('.qc-picture-evidence .qc-original-image')?.naturalWidth === 640);
  const auto = (await api('/api/v1/quality/inspections/' + reportId)).body.data; assert.equal(auto.results[0].provenance.mode, 'detector'); assert.equal(auto.results[0].provenance.image.sha256, uploaded.sha256); assert.deepEqual(auto.results[0].observation.defects[0].bbox, [0.2,0.3,0.35,0.2]);
  await (await frame.$('.qc-picture-evidence')).scrollIntoView(); await audit('Real dynamic image detector report'); await page.screenshot({ path: path.join(OUTPUT, 'dynamic-image-report.png'), fullPage: true });
  pass('Dedicated node binds the current sample and exact authorized image to a real POST, saving detector provenance and boxes');
  await page.setViewport({ width:390, height:844 }); await visit('/quality/inspections?inspection=' + reportId); await frame.waitForFunction(() => document.querySelector('.qc-picture-evidence .qc-original-image')?.naturalWidth === 640); await (await frame.$('.qc-picture-evidence')).scrollIntoView(); await audit('390px image report'); assert(await frame.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)); assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)); await page.screenshot({ path:path.join(OUTPUT,'image-report-mobile.png'), fullPage:true }); assert.equal(calls.length, 1);
  pass('390px image report stays in the enterprise layout and reloads evidence without a second detector call');
  await page.setViewport({width:1600,height:1100}); await api('/api/files/' + uploaded.id, 'DELETE'); await visit('/quality/inspections?inspection=' + reportId); await frame.waitForFunction(() => document.querySelector('.qc-picture-evidence .qc-original-image')?.naturalWidth === 640);
  const deniedNew = await api('/api/v1/quality/images/' + uploaded.id); assert.equal(deniedNew.status, 404); assert.equal(calls.length, 1);
  pass('Soft-deleted images cannot be newly selected; authorized historical reports still retain the original');
  assert.deepEqual(failures, []); assert.deepEqual(violations, []);
})().catch(cause => { failures.push(cause.stack || String(cause)); console.error(cause); process.exitCode = 1; }).finally(async () => {
  if (page) try {
    for (const [kind, id] of [...created].reverse()) { const row = await api('/api/v1/quality/' + kind + '/' + id); if (row.status === 200) assert.equal((await api('/api/v1/quality/' + kind + '/' + id + '?version=' + row.body.data.version, 'DELETE')).status, 200); }
    if (workflowId) { const row = await api('/api/v1/workflows/' + workflowId); if (row.status === 200) assert.equal((await api('/api/v1/workflows/' + workflowId + '?version=' + row.body.data.version, 'DELETE')).status, 200); }
    if (deviceId) { const row = await api('/api/v1/alerts/devices/' + deviceId); if (row.status === 200) assert.equal((await api('/api/v1/alerts/devices/' + deviceId + '?version=' + row.body.data.version, 'DELETE')).status, 200); }
    if (sourceId) { const row = await api('/api/v1/collection/sources/' + sourceId); if (row.status === 200) assert.equal((await api('/api/v1/collection/sources/' + sourceId + '?version=' + row.body.data.version, 'DELETE')).status, 200); }
    for (const id of imageIds) await api('/api/files/' + id, 'DELETE');
    if (failures.length) await page.screenshot({ path: path.join(OUTPUT, 'failure.png'), fullPage: true });
  } catch(cause) { failures.push('Cleanup: ' + cause.message); process.exitCode = 1; }
  fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({checks,failures,violations,actualHttpRequests:calls.length}, null, 2)); if (browser) await browser.close(); if (fixture) await new Promise(resolve => fixture.close(resolve)); console.log('Evidence ' + OUTPUT);
});
