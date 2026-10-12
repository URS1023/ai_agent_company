/* 只验证夹具与离线文件判定，不启动浏览器、HTTP 服务或模型。 */
const test = require('node:test');
const assert = require('node:assert/strict');
const suite = require('./full_browser_ai_documents_acceptance');

function zip(files) {
  // 构造普通存储 ZIP；CRC 与目录都真实有效，使反例只改变 Office 引用关系。
  let offset = 0;
  const bodies = [], entries = [];
  for (const [name, value] of Object.entries(files)) {
    const filename = Buffer.from(name), data = Buffer.from(value);
    let crc = 0xffffffff;
    for (const byte of data) {
      crc ^= byte;
      for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
    }
    crc = (crc ^ 0xffffffff) >>> 0;
    const local = Buffer.alloc(30), central = Buffer.alloc(46);
    local.writeUInt32LE(0x04034b50); local.writeUInt16LE(20, 4);
    local.writeUInt32LE(crc, 14); local.writeUInt32LE(data.length, 18); local.writeUInt32LE(data.length, 22); local.writeUInt16LE(filename.length, 26);
    central.writeUInt32LE(0x02014b50); central.writeUInt16LE(20, 4); central.writeUInt16LE(20, 6);
    central.writeUInt32LE(crc, 16); central.writeUInt32LE(data.length, 20); central.writeUInt32LE(data.length, 24);
    central.writeUInt16LE(filename.length, 28); central.writeUInt32LE(offset, 42);
    bodies.push(local, filename, data); entries.push(central, filename);
    offset += local.length + filename.length + data.length;
  }
  const directory = Buffer.concat(entries), end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50); end.writeUInt16LE(Object.keys(files).length, 8); end.writeUInt16LE(Object.keys(files).length, 10);
  end.writeUInt32LE(directory.length, 12); end.writeUInt32LE(offset, 16);
  return Buffer.concat([...bodies, directory, end]);
}

test('五个幻灯片文件不能掩盖演示顺序重复引用同一页', () => {
  const rel = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships';
  const files = {
    '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
    '_rels/.rels': `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="root" Type="${rel}/officeDocument" Target="ppt/presentation.xml"/></Relationships>`,
    'ppt/presentation.xml': `<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="${rel}"><p:sldIdLst>${Array.from({ length: 5 }, (_, i) => `<p:sldId id="${i + 256}" r:id="r1"/>`).join('')}</p:sldIdLst></p:presentation>`,
    'ppt/_rels/presentation.xml.rels': `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Type="${rel}/slide" Target="slides/slide1.xml"/></Relationships>`,
  };
  for (let i = 1; i <= 5; i++) files[`ppt/slides/slide${i}.xml`] = '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:t>真实正文主题</a:t></p:sld>';
  assert.throws(() => suite.assertPresentationFile(zip(files), ['真实正文主题']), /重复引用/);
});

function compoundPayload() {
  const names = ['query_business_data', 'render_chart_image', 'export_query_spreadsheet',
    'create_dashboard_from_queries', 'render_dashboard_image', 'render_document',
    'create_presentation_plan', 'write_presentation_slides', 'finalize_presentation'];
  return { messages: [{ role: 'user', content: suite.COMPOUND_PROMPT }],
    tools: names.map(name => ({ type: 'function', function: { name } })) };
}

function receipt(payload, decision, result) {
  const id = 'actual-call-' + payload.messages.length;
  payload.messages.push({ role: 'assistant', tool_calls: [{ id, type: 'function',
    function: { name: decision.name, arguments: JSON.stringify(decision.args) } }] });
  payload.messages.push({ role: 'tool', tool_call_id: id, content: JSON.stringify(result) });
}

function partialPayload() {
  const payload = compoundPayload();
  const artifact = (id, name) => ({ id, name, download_url: '/api/v1/artifacts/' + id + '/download' });
  const results = [
    { data: { datasourceId: 'inspection-source', datasourceVersion: 1, queryId: 'persisted-query',
      data: suite.INSPECTION_ROWS, columns: Object.keys(suite.INSPECTION_ROWS[0]).map(columnName => ({ columnName })), totalCount: 2, truncated: false } },
    { artifacts: [artifact('real-pie', '状态饼图.png')] },
    { artifacts: [artifact('real-sheet', '巡检统计.xlsx')] },
    { data: { reportId: 'saved-dashboard', version: 1, href: '/reports/view/saved-dashboard', queryIds: ['persisted-query'] } },
    { artifacts: [artifact('real-dashboard-image', '大屏.png')] },
    { artifacts: [artifact('real-word', '巡检分析.docx')] },
    { data: { draft_id: 'persisted-draft', total_page_count: 15, body_page_count: 13 } },
    ...[0, 4, 8].map(offset => ({ data: { draft_id: 'persisted-draft',
      written_now: [1, 2, 3, 4].map(number => number + offset), rejected_slides: [],
      missing_slide_numbers: Array.from({ length: 9 - offset }, (_, index) => index + offset + 5) } })),
    { status: 'failed', error_code: 'tool_execution_error', error_summary: '以下正文页尚未写入：13',
      metadata: { presentation_progress: { draft_id: 'persisted-draft', missing_slide_numbers: [13], missing_page_numbers: [14] } }, artifacts: [] },
  ];
  const decisions = [];
  for (const value of results) {
    const decision = suite.compoundDecision(payload, 'inspection-source');
    assert.equal(decision.kind, 'tool');
    decisions.push(decision);
    receipt(payload, decision, { status: 'success', ...value });
  }
  return { payload, decisions };
}

test('原话复合场景只从成功查询和真实草稿回执形成两轮交付', () => {
  const { payload, decisions } = partialPayload();
  assert.equal(decisions[0].args.query, suite.COMPOUND_PROMPT);
  assert.equal(decisions[6].args.page_count, 15);
  assert.deepEqual(decisions[5].args.document_spec.sections.map(section => section.blocks.find(block => block.type === 'image').artifact_id),
    ['real-pie', 'real-dashboard-image']);
  assert.equal(suite.compoundDecision(payload, 'inspection-source').kind, 'compound-partial');
  payload.messages.push({ role: 'user', content: suite.COMPOUND_CONTINUE }, { role: 'user', content: '<技能指引>保留原任务</技能指引>' });
  const write = suite.compoundDecision(payload, 'inspection-source');
  assert.equal(write.name, 'write_presentation_slides');
  assert.equal(write.args.draft_id, 'persisted-draft');
  assert.deepEqual(write.args.slides.map(slide => slide.slide_number), [13]);
  receipt(payload, write, { status: 'success', data: { draft_id: 'persisted-draft', written_now: [13], missing_slide_numbers: [] } });
  const finalize = suite.compoundDecision(payload, 'inspection-source');
  assert.equal(finalize.name, 'finalize_presentation');
  assert.equal(finalize.args.draft_id, 'persisted-draft');
  receipt(payload, finalize, { status: 'success', artifacts: [{ id: 'final-ppt', name: '巡检十五页.pptx', download_url: '/download' }] });
  assert.equal(suite.compoundDecision(payload, 'inspection-source').kind, 'compound-complete');
  assert.equal(decisions.filter(decision => decision.name === 'query_business_data').length, 1);
});

test('续办丢失原话或查询回执时拒绝从夹具缓存补回', () => {
  const payload = compoundPayload();
  payload.messages = [{ role: 'user', content: suite.COMPOUND_CONTINUE }];
  assert.throws(() => suite.compoundDecision(payload, 'inspection-source'), /丢失用户原话/);
  payload.messages.unshift({ role: 'user', content: suite.COMPOUND_PROMPT });
  assert.throws(() => suite.compoundDecision(payload, 'inspection-source'), /缺少首轮成功查询回执/);
});

test('只有预定缺第13页可作为部分完成，未知外部副作用和其它失败都不能放行', () => {
  const { payload } = partialPayload();
  const index = payload.messages.length - 1;
  const expected = JSON.parse(payload.messages[index].content);
  for (const changed of [
    { ...expected, error_code: 'tool_result_unconfirmed' },
    { ...expected, error_summary: '临时失败' },
    { ...expected, metadata: { presentation_progress: { draft_id: 'persisted-draft', missing_slide_numbers: [12], missing_page_numbers: [13] } } },
    { ...expected, artifacts: [{ id: 'fake-completion' }] },
  ]) {
    payload.messages[index].content = JSON.stringify(changed);
    assert.throws(() => suite.compoundDecision(payload, 'inspection-source'));
  }
});

test('仅模型输入接受原生Error前缀且继续核对真实缺页结构', () => {
  const { payload } = partialPayload();
  const index = payload.messages.length - 1;
  const body = payload.messages[index].content;
  payload.messages[index].content = 'Error: ' + body;
  assert.equal(suite.compoundDecision(payload, 'inspection-source').kind, 'compound-partial');
  for (const invalid of [
    'Error:' + body, 'Error: ' + JSON.stringify({ ...JSON.parse(body), status: 'success' }),
    'Error: ' + JSON.stringify(body), 'Error: ' + JSON.stringify({ ...JSON.parse(body), error_code: 'tool_result_unconfirmed' }),
    'Error: ' + JSON.stringify({ ...JSON.parse(body), metadata: {} }),
  ]) {
    payload.messages[index].content = invalid;
    assert.throws(() => suite.compoundDecision(payload, 'inspection-source'));
  }
});

test('查询值、权限来源和调用身份不得被模型桩无声替换', () => {
  const { payload } = partialPayload();
  const index = payload.messages.findIndex(message => message.role === 'tool');
  const original = JSON.parse(payload.messages[index].content);
  for (const changed of [
    { ...original, data: { ...original.data, datasourceId: 'different-source' } },
    { ...original, data: { ...original.data, data: [{ ...suite.INSPECTION_ROWS[0], 设备数量: 99 }, suite.INSPECTION_ROWS[1]] } },
    { ...original, status: 'failed', error_code: 'permission_denied' },
  ]) {
    payload.messages[index].content = JSON.stringify(changed);
    assert.throws(() => suite.compoundDecision(payload, 'inspection-source'));
  }
  payload.messages[index].content = JSON.stringify(original);
  payload.messages[index].tool_call_id = 'missing-call';
  assert.throws(() => suite.compoundDecision(payload, 'inspection-source'), /丢失实际工具调用身份/);
});

test('受保护接口经真实Blob下载，以GUID完成事件及响应字节绑定文件', () => {
  const expected = new URL('http://127.0.0.1:50000/api/artifacts/actual/download');
  const start = { kind: 'begin', guid: 'actual-download', suggestedFilename: '巡检报告.pptx', url: 'blob:' + expected.origin + '/local-object' };
  const progress = { kind: 'progress', guid: start.guid, state: 'completed', receivedBytes: 200 };
  assert.equal(suite.completedDownload([start, progress], expected, '.pptx').started.guid, start.guid);
  assert.equal(suite.completedDownload([start, { ...progress, state: 'inProgress' }], expected, '.pptx'), null);
  assert.equal(suite.completedDownload([start, { ...progress, guid: 'other-file' }], expected, '.pptx'), null);
  const bytes = Buffer.alloc(200, 65);
  const response = { complete: true, bytes: 200, sha256: require('node:crypto').createHash('sha256').update(bytes).digest('hex') };
  assert.match(suite.verifyDownloadedBytes(bytes, response, progress), /^[0-9a-f]{64}$/);
  assert.throws(() => suite.verifyDownloadedBytes(bytes, { ...response, sha256: 'a'.repeat(64) }, progress), /响应字节不符/);
  assert.throws(() => suite.verifyDownloadedBytes(bytes, { ...response, complete: false }, progress));
  assert.throws(() => suite.verifyDownloadedBytes(bytes, { ...response, bytes: 201 }, progress));
  assert.throws(() => suite.verifyDownloadedBytes(bytes, { ...response, error: '响应传输中断' }, progress));
  assert.throws(() => suite.verifyDownloadedBytes(bytes, response, { ...progress, receivedBytes: 199 }));
});

test('重复下载、异源Blob、错误直接链接、取消和伪装扩展名均拒绝', () => {
  const expected = new URL('http://127.0.0.1:50000/api/artifacts/actual/download');
  const start = { kind: 'begin', guid: 'actual-download', suggestedFilename: '巡检报告.pptx', url: 'blob:' + expected.origin + '/local-object' };
  for (const events of [
    [start, start], [{ ...start, url: 'blob:http://127.0.0.1:50001/local-object' }],
    [{ ...start, url: expected.origin + '/api/artifacts/other/download' }],
    [{ ...start, suggestedFilename: '错误.html' }], [{ ...start, guid: '../outside' }],
    [start, { kind: 'progress', guid: start.guid, state: 'canceled' }],
  ]) assert.throws(() => suite.completedDownload(events, expected, '.pptx'));
});

test('缺页终态从实际Run读取结构码并核对唯一展示错误', () => {
  const run = { status: 'partial', error: { code: 'delivery_incomplete', message: '交付核验仍有缺项' } };
  const part = { type: 'error', message: run.error.message };
  suite.assertIncompleteRun(run, [{ type: 'text', text: '已经保留原有文件' }, part]);
  for (const [invalidRun, parts] of [
    [{ ...run, status: 'completed' }, [part]],
    [{ ...run, error: { ...run.error, code: 'tool_result_unconfirmed' } }, [part]],
    [{ ...run, error: null }, [part]],
    [run, [{ ...part, message: '另一运行的错误' }]], [run, [part, part]], [run, []],
    [run, [{ ...part, code: 'permission_denied' }]],
  ]) assert.throws(() => suite.assertIncompleteRun(invalidRun, parts));
});
