/*
 * 中文说明（AI 工作台 + 文档中心的全量端到端验收）：
 * 验证行为：所有 Cloud Brain 侧变更都从可见的浏览器控件发起，随后校验落库与页面结果一致。
 * 运行方式：需隔离服务与本机 Edge；文件内的本地 HTTP 服务只是模拟的客户数据接口与
 *   OpenAI 兼容模型提供方，它不会调用 Cloud Brain 应用接口（保证被测对象只有本平台）。
 * 边界：本文件会对隔离环境产生真实的会话/产物数据，禁止对生产运行。
 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const zlib = require("node:zlib");
const { createHash } = require("node:crypto");
const { spawnSync } = require("node:child_process");
const puppeteer = require("puppeteer-core");
const {
  clickHandle,
  clickText,
  createHttpSourceThroughUi,
  envelopeData,
  fillElement,
  login,
  removeHttpSourceThroughUi,
  sleep,
  visibleByText,
  visitBusiness,
  waitUiResponse,
} = require("./browser_only_helpers");

const BASE = process.env.TEST5_E2E_BASE_URL || "http://127.0.0.1:8030";
const PASSWORD = process.env.TEST5_E2E_PASSWORD;
const EDGE = process.env.TEST5_EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const RUN = Date.now().toString(36);
const OUTPUT = path.resolve(".implementation/ui-results/full-browser-ai-documents-" + RUN);
const DOWNLOADS = path.join(OUTPUT, "downloads");
const SOURCE_NAME = "终验 AI 财务设备数据-" + RUN;
const CHAT_PROMPT = "请分析财务与设备运行情况。第二句只用于验证会话摘要。";
const CHAT_TITLE = "请分析财务与设备运行情况";
const PPT_PROMPT = "帮我生成一份 ERP 系统的介绍，严格按所选模板生成 5 页并提供下载。";
const COMPOUND_PROMPT = "帮我分析一下设备巡检，近期有多少设备需要巡检，但是未巡检的需要图饼并导出为word和excel，再帮我做一份15页的ppt然后做一份数字大屏放到word里面";
const COMPOUND_CONTINUE = "继续，使用已有结果制作大屏和Excel";
const INSPECTION_SOURCE = "隔离验收：设备巡检合成快照-" + RUN;
const INSPECTION_WINDOW = "2026-09-28至2026-10-04（北京时间）";
const INSPECTION_DESCRIPTION = "固定窗口的合成巡检汇总，非生产数据；按应检设备去重，未检不等于告警或问题数。";
const INSPECTION_COLUMNS = ["巡检状态", "设备数量", "单位", "统计期间", "数据性质"];
const INSPECTION_LEDGER = {
  plans: Array.from({ length: 8 }, (_, i) => ({ device: "synthetic-device-" + (i + 1), inWindow: true }))
    .concat([{ device: "outside-window", inWindow: false }]),
  completions: ["synthetic-device-1", "synthetic-device-1", "synthetic-device-2", "synthetic-device-3", "outside-window"],
};
// 数据接口由独立合成账本统计；真实 SQL 的正确性不在此 HTTP 场景范围。
const INSPECTION_ROWS = (() => {
  const planned = new Set(INSPECTION_LEDGER.plans.filter(row => row.inWindow).map(row => row.device));
  const finished = new Set(INSPECTION_LEDGER.completions.filter(id => planned.has(id)));
  return [["已巡检", finished.size], ["未巡检", planned.size - finished.size]].map(([state, count]) =>
    Object.fromEntries(INSPECTION_COLUMNS.map((key, i) => [key, [state, count, "台", INSPECTION_WINDOW, "合成验收非生产"][i]])));
})();
const COMPOUND_TITLE = "设备巡检分析";
const COMPOUND_DASHBOARD = "巡检合成快照大屏-" + RUN;

const checks = [];
const failures = [];
const accessibilityFindings = [];
const applicationRequests = [];
const modelCalls = [];
const fixtureCalls = [];
const responseFailures = [];
const generationIds = [];
const conversationIds = [];
const chatRuns = [];
const downloadedFiles = [];
const downloadTransport = [];
let restoreTransportObserver;
let browser;
let page;
let fixture;
let source;
let inspectionSource;
let downloadClient;
const downloadEvents = [];
const compoundEvidence = { fixture: { ledger: INSPECTION_LEDGER, rows: INSPECTION_ROWS }, checkpoints: [], downloads: {}, toolCalls: [] };

const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAIAAAAmkwkpAAAAG0lEQVR4nGP4z8DAwMDAxMDAwMDAwMDAwAAANwABfH9q3QAAAABJRU5ErkJggg==",
  "base64",
);

function pass(name, detail = {}) {
  checks.push({ name, ...detail });
  console.log("PASS " + name);
}

function phase(name) {
  console.log("PHASE " + name);
}

async function assertStatus(response, expected) {
  const actual = response.status();
  let detail = "";
  if (actual !== expected) {
    try { detail = await response.text(); }
    catch (error) { detail = "response body unavailable: " + error.message; }
    detail = JSON.stringify({ path: new URL(response.url()).pathname, method: response.request().method(),
      resourceType: response.request().resourceType(), fromCache: response.fromCache(), fromServiceWorker: response.fromServiceWorker(),
      contentType: response.headers()["content-type"], contentLength: response.headers()["content-length"], body: detail });
  }
  assert.equal(actual, expected, detail);
}

async function requestBody(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf8");
}

function normalCompletion(payload, content) {
  return {
    id: "browser-ai-" + RUN,
    object: "chat.completion",
    created: Math.floor(Date.now() / 1000),
    model: payload.model || "browser-ai-model",
    choices: [{ index: 0, message: { role: "assistant", content }, finish_reason: "stop" }],
    usage: { prompt_tokens: 31, completion_tokens: 23, total_tokens: 54 },
  };
}

function streamChunk(response, payload) {
  response.write("data: " + JSON.stringify(payload) + "\n\n");
}

function streamContent(response, payload, content) {
  response.writeHead(200, {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
  });
  const midpoint = Math.max(1, Math.floor(content.length / 2));
  [content.slice(0, midpoint), content.slice(midpoint)].filter(Boolean).forEach((part, index) => {
    streamChunk(response, {
      id: "browser-ai-" + RUN,
      object: "chat.completion.chunk",
      created: Math.floor(Date.now() / 1000),
      model: payload.model || "browser-ai-model",
      choices: [{ index: 0, delta: index === 0 ? { role: "assistant", content: part } : { content: part }, finish_reason: null }],
    });
  });
  streamChunk(response, {
    id: "browser-ai-" + RUN,
    object: "chat.completion.chunk",
    created: Math.floor(Date.now() / 1000),
    model: payload.model || "browser-ai-model",
    choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
  });
  response.end("data: [DONE]\n\n");
}

function streamToolCall(response, payload, name, args) {
  response.writeHead(200, {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
  });
  streamChunk(response, {
    id: "browser-ai-" + RUN,
    object: "chat.completion.chunk",
    created: Math.floor(Date.now() / 1000),
    model: payload.model || "browser-ai-model",
    choices: [{
      index: 0,
      delta: {
        role: "assistant",
        tool_calls: [{
          index: 0,
          id: "call-" + name + "-" + RUN + "-" + modelCalls.length,
          type: "function",
          function: { name, arguments: JSON.stringify(args) },
        }],
      },
      finish_reason: null,
    }],
  });
  streamChunk(response, {
    id: "browser-ai-" + RUN,
    object: "chat.completion.chunk",
    created: Math.floor(Date.now() / 1000),
    model: payload.model || "browser-ai-model",
    choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }],
  });
  response.end("data: [DONE]\n\n");
}

function presentationPlan() {
  return {
    title: "ERP 系统介绍",
    subtitle: "从业务协同到经营决策的一体化平台",
    author: "智慧云脑 Cloud Brain",
    footer: "ERP 系统介绍",
    closing_title: "总结与下一步",
    closing_subtitle: "以统一数据和流程推动持续经营改善",
    page_count: 5,
    slides: [
      { title: "ERP 的核心价值", layout: "cards", section: "价值", key_message: "ERP 通过统一数据、流程与责任体系，让经营动作可协同、可追踪、可分析。" },
      { title: "核心业务闭环", layout: "cards", section: "能力", key_message: "财务、销售、采购、库存与生产在同一主数据和业务单据链路中协同。" },
      { title: "实施路线图", layout: "timeline", section: "落地", key_message: "成功实施依赖分阶段治理、数据迁移、用户验证和持续运营，而不只是软件安装。" },
    ],
  };
}

function presentationSlides() {
  return {
    slides: [
      {
        slide_number: 1,
        title: "ERP 的核心价值",
        layout: "cards",
        key_message: "统一数据口径减少重复录入，端到端流程缩短协作等待，实时经营视图帮助管理者更快识别偏差并采取行动。",
        items: [
          { type: "card", title: "统一数据", description: "客户、物料、组织和会计科目采用统一主数据，减少跨部门对账与口径争议。" },
          { type: "card", title: "贯通流程", description: "订单、采购、生产、库存与结算形成可追踪单据链，异常能够定位到责任环节。" },
          { type: "card", title: "辅助决策", description: "实时汇总收入、成本、库存和交付指标，让经营复盘建立在一致事实之上。" },
        ],
      },
      {
        slide_number: 2,
        title: "核心业务闭环",
        layout: "cards",
        key_message: "ERP 将销售预测转化为供应与生产计划，再由库存、交付和财务结算闭环验证经营结果。",
        items: [
          { type: "card", title: "销售到收款", description: "从报价和订单到发货、开票与回款，完整追踪收入实现和客户信用风险。" },
          { type: "card", title: "采购到付款", description: "从需求、寻源和采购到收货、对账与付款，控制成本、质量和供应履约。" },
          { type: "card", title: "计划到生产", description: "结合需求、产能和物料可用量形成计划，持续反馈完工、损耗和设备状态。" },
        ],
      },
      {
        slide_number: 3,
        title: "实施路线图",
        layout: "timeline",
        key_message: "采用小步验证的实施路径，先统一范围和数据，再完成配置迁移、业务验收与持续优化，可以显著降低切换风险。",
        items: [
          { title: "规划蓝图", description: "明确业务范围、关键流程、责任角色、成功指标以及不能被系统改变的合规边界。" },
          { title: "数据治理", description: "清理主数据和历史数据，定义编码、质量规则、迁移范围与可审计的核对方法。" },
          { title: "配置验证", description: "按真实业务场景完成系统配置、接口联调、权限校验和关键用户端到端测试。" },
          { title: "上线运营", description: "分批切换并监控问题、性能和采用率，通过培训、复盘和版本治理持续改进。" },
        ],
      },
    ],
  };
}

function aiDocumentSpec() {
  return {
    title: "AI 生成设备经营简报",
    metadata: { source: "浏览器终验模拟模型" },
    sections: [
      {
        title: "经营结论",
        blocks: [
          { type: "p", text: "本期销售与设备运行整体稳定，关键指标已经形成统一的复核口径。" },
          { type: "bullet", text: "销售数据按业务日期和组织范围汇总，避免将测试记录计入经营结果。" },
          { type: "bullet", text: "设备异常需要同时保留原始判定、人工复核和最终处置记录。" },
        ],
      },
    ],
  };
}

function messageText(message) {
  return typeof message.content === "string" ? message.content
    : (message.content || []).filter(item => item.type === "text").map(item => item.text || "").join("\n");
}

function inspectionSummary(rows) {
  const completed = rows.find(row => row["巡检状态"] === "已巡检")["设备数量"];
  const pending = rows.find(row => row["巡检状态"] === "未巡检")["设备数量"];
  return `应检${completed + pending}台，已巡检${completed}台，未巡检${pending}台，未检占比${(pending / (completed + pending) * 100).toFixed(1)}%。`;
}

function inspectionSlides(rows) {
  const topics = ["统计口径", "数据来源", "应检概况", "巡检状态占比", "未检风险解释", "缺失信息边界", "建议排程", "现场核对清单", "责任安排建议", "Excel使用说明", "Word证据说明", "大屏快照范围", "下一步核对清单"];
  return topics.map((title, index) => index === 3 ? {
    slide_number: index + 1, title, layout: "chart", key_message: inspectionSummary(rows),
    chart: { chart_type: "pie", categories: rows.map(row => row["巡检状态"]), series: [{ name: "设备数（台）", values: rows.map(row => row["设备数量"]) }],
      source: "已授权合成巡检查询；" + INSPECTION_WINDOW, insight: inspectionSummary(rows) },
  } : {
    slide_number: index + 1, title, layout: "cards", key_message: `${title}：${inspectionSummary(rows)}`,
    items: [
      { type: "card", title: title + "依据", description: `${inspectionSummary(rows)}以上只代表${INSPECTION_WINDOW}的合成查询快照。` },
      { type: "card", title: title + "边界", description: "统计单位是去重后的设备，不是巡检项或告警；未提供设备明细、故障原因和历史趋势，不能推测生产事实。" },
      { type: "card", title: title + "建议", description: `建议围绕${title}核对计划、记录与责任人。保留本次来源和统计窗口，现场复核后再决定处置；此项为建议。` },
    ],
  });
}

function payloadReceipts(messages, start, end = messages.length) {
  // 只读模型实际收到的调用与回执，不从测试全局缓存补丢失的上下文。
  const calls = new Map(), receipts = [];
  for (const message of messages.slice(start, end)) {
    for (const call of message.tool_calls || []) calls.set(call.id, call.function);
    if (message.role !== "tool") continue;
    const call = calls.get(message.tool_call_id);
    assert(call?.name, "复合模型请求丢失实际工具调用身份");
    const raw = messageText(message), nativeError = raw.startsWith("Error: ");
    // 原生 ToolRuntime 给模型的失败文本带此固定前缀；只适配模型输入，UI 回执仍须原生结构。
    const result = JSON.parse(nativeError ? raw.slice(7) : raw);
    if (nativeError) assert.equal(result?.status, "failed", "原生错误文本不能冒充成功工具回执");
    receipts.push({ name: call.name, arguments: JSON.parse(call.arguments || "{}"), result, callId: message.tool_call_id });
  }
  return receipts;
}

function assertMissingThirteen(result, draftId) {
  assert.equal(result.status, "failed");
  assert.equal(result.error_code, "tool_execution_error");
  assert.match(result.error_summary, /以下正文页尚未写入：13/);
  assert.equal(result.metadata?.presentation_progress?.draft_id, draftId);
  assert.deepEqual(result.metadata.presentation_progress.missing_slide_numbers, [13]);
  assert.deepEqual(result.metadata.presentation_progress.missing_page_numbers, [14]);
  assert.equal(result.artifacts?.length || 0, 0);
}

function compoundDecision(payload, sourceId) {
  const messages = payload.messages || [];
  const original = messages.findLastIndex(message => message.role === "user" && messageText(message).includes(COMPOUND_PROMPT));
  const continued = messages.findLastIndex(message => message.role === "user" && messageText(message).includes(COMPOUND_CONTINUE));
  if (original < 0 && continued < 0) return null;
  assert(original >= 0, "续办模型请求丢失用户原话，不得借夹具缓存补回");
  const followup = continued > original;
  const prior = payloadReceipts(messages, original + 1, followup ? continued : messages.length);
  const stages = ["query_business_data", "render_chart_image", "export_query_spreadsheet", "create_dashboard_from_queries", "render_dashboard_image", "render_document", "create_presentation_plan", "write_presentation_slides", "write_presentation_slides", "write_presentation_slides", "finalize_presentation"];
  assert(prior.length <= stages.length);
  assert.deepEqual(prior.map(row => row.name), stages.slice(0, prior.length), "复合任务阶段重复或错序");
  for (const [index, row] of prior.entries()) {
    if (index === 10) assertMissingThirteen(row.result, prior[6].result.data.draft_id);
    else assert.equal(row.result.status, "success", `${row.name} 实际失败：${JSON.stringify(row.result)}`);
  }
  const tool = (name, args) => {
    assert((payload.tools || []).some(row => row.function?.name === name), "未提供复合交付工具：" + name);
    return { kind: "tool", name, args, scenario: "compound", continuation: followup };
  };
  if (!prior.length) {
    assert(!followup && sourceId, "续办缺少首轮成功查询回执");
    return tool(stages[0], { datasource_id: sourceId, mode: "natural_language", query: COMPOUND_PROMPT, max_rows: 10 });
  }
  const query = prior[0].result.data;
  assert.equal(query.datasourceId, sourceId);
  assert(query.queryId && query.datasourceVersion);
  assert.equal(query.totalCount, 2);
  assert.equal(query.truncated, false);
  assert.deepEqual(query.columns?.map(column => column.columnName), INSPECTION_COLUMNS);
  assert.deepEqual(query.data, INSPECTION_ROWS);
  const artifactId = index => {
    const row = prior[index].result.artifacts?.[0];
    assert(row?.id && row.download_url, "工具未返回真实可下载工件");
    return row.id;
  };
  if (followup) {
    assert.equal(prior.length, 11, "续办缺少完整首轮回执，不能旁路补入");
    const current = payloadReceipts(messages, continued + 1);
    assert(current.length <= 2);
    assert.deepEqual(current.map(row => row.name), ["write_presentation_slides", "finalize_presentation"].slice(0, current.length));
    for (const receipt of current) assert.equal(receipt.result.status, "success", JSON.stringify(receipt));
    const draftId = prior[6].result.data.draft_id;
    if (!current.length) return tool("write_presentation_slides", { draft_id: draftId, slides: [inspectionSlides(query.data)[12]] });
    assert.equal(current[0].result.data.draft_id, draftId);
    assert.deepEqual(current[0].result.data.written_now, [13]);
    assert.deepEqual(current[0].result.data.missing_slide_numbers, []);
    if (current.length === 1) return tool("finalize_presentation", { draft_id: draftId, name: "巡检十五页-" + RUN });
    assert(current[1].result.artifacts?.some(row => row.name.endsWith('.pptx')));
    return { kind: "compound-complete", content: `${inspectionSummary(query.data)}\n\n已沿用原查询与草稿补完十五页PPT。原Word、Excel、饼图和数字大屏均保留，未重新查询。数据仅为${INSPECTION_WINDOW}的合成验收快照，不代表生产。` };
  }
  if (prior.length === 1) return tool(stages[1], { query_id: query.queryId, chart_type: "pie", label_column: "巡检状态", value_column: "设备数量", title: "合成巡检状态占比" });
  if (prior.length === 2) return tool(stages[2], { query_id: query.queryId, name: "巡检统计-" + RUN + ".xlsx" });
  if (prior.length === 3) return tool(stages[3], { query_ids: [query.queryId], title: COMPOUND_DASHBOARD });
  const report = prior[3].result.data;
  assert(report.reportId && report.version && report.href);
  assert.deepEqual(report.queryIds, [query.queryId]);
  if (prior.length === 4) return tool(stages[4], { report_id: report.reportId });
  if (prior.length === 5) return tool(stages[5], { format: "docx", name: "巡检分析-" + RUN,
    document_spec: { title: "设备巡检合成分析", sections: [
      { title: "巡检统计", blocks: [{ type: "p", text: inspectionSummary(query.data) },
        { type: "p", text: `${INSPECTION_WINDOW}，合成验收非生产；按应检设备去重，未检不等于告警。查询标识：${query.queryId}` },
        { type: "image", artifact_id: artifactId(1), caption: "同一成功查询生成的巡检状态饼图" }] },
      { title: "数字大屏", blocks: [{ type: "p", text: "保存查询数据快照，非实时页面截图。" },
        { type: "image", artifact_id: artifactId(4), caption: `对应大屏：${report.reportId}，保存版本${report.version}` }] },
    ] } });
  const slides = inspectionSlides(query.data);
  if (prior.length === 6) return tool(stages[6], { title: COMPOUND_TITLE, subtitle: INSPECTION_WINDOW + " · 合成验收非生产", page_count: 15,
    include_cover: true, include_closing: true, closing_title: "核对后再执行", closing_subtitle: inspectionSummary(query.data),
    slides: slides.map(({ title, layout, key_message }) => ({ title, layout, key_message })) });
  const draft = prior[6].result.data;
  assert(draft.draft_id && draft.total_page_count === 15 && draft.body_page_count === 13);
  for (let index = 7; index < Math.min(prior.length, 10); index++) {
    const progress = prior[index].result.data;
    assert.equal(progress.draft_id, draft.draft_id);
    assert.deepEqual(progress.written_now, [1, 2, 3, 4].map(value => value + (index - 7) * 4));
    assert.deepEqual(progress.rejected_slides, []);
  }
  if (prior.length < 10) return tool("write_presentation_slides", { draft_id: draft.draft_id, slides: slides.slice((prior.length - 7) * 4, (prior.length - 6) * 4) });
  if (prior.length === 10) {
    assert.deepEqual(prior[9].result.data.missing_slide_numbers, [13]);
    return tool(stages[10], { draft_id: draft.draft_id, name: "巡检十五页-" + RUN });
  }
  return { kind: "compound-partial", content: `${inspectionSummary(query.data)}\n\n饼图、Excel、数字大屏和内嵌双图Word已生成。PPT还缺正文第13页，尚未完成；已保存现有草稿和原查询。所有数据为合成验收非生产快照。` };
}

/** 根据本轮真实工具回执推进；技能注入消息不能被当作新的业务轮次。 */
function modelDecision(payload, { datasourceId, chartUrl, inspectionSourceId }) {
  const messages = payload.messages || [];
  if (messages.some(message => messageText(message).includes("企业文档编写引擎"))) {
    return { kind: "document", content: JSON.stringify(aiDocumentSpec()) };
  }
  const compound = compoundDecision(payload, inspectionSourceId);
  if (compound) return compound;
  const index = messages.findLastIndex(message => message.role === "user"
    && [CHAT_PROMPT, PPT_PROMPT].some(prompt => messageText(message).includes(prompt)));
  if (index < 0) return { kind: "probe", content: "ok" };
  const turn = messages.slice(index + 1), calls = new Map(), receipts = [];
  for (const message of turn) {
    for (const call of message.tool_calls || []) calls.set(call.id, call.function?.name);
    if (message.role !== "tool") continue;
    const name = calls.get(message.tool_call_id) || message.name;
    assert(name, "模型桩无法找到本轮工具回执对应的调用名称");
    const result = JSON.parse(messageText(message));
    assert.equal(result.status, "success", `${name} 实际工具失败：${JSON.stringify(result)}`);
    receipts.push({ name, result });
  }
  const tool = (name, args) => {
    assert((payload.tools || []).some(item => item.function?.name === name), `DSH 没有提供所需业务工具：${name}`);
    return { kind: "tool", name, args };
  };
  if (messageText(messages[index]).includes(PPT_PROMPT)) {
    const stages = ["create_presentation_plan", "write_presentation_slides", "finalize_presentation"];
    assert.deepEqual(receipts.map(item => item.name), stages.slice(0, receipts.length), "PPT 实际回执发生重复或阶段错序");
    if (!receipts.length) return tool(stages[0], presentationPlan());
    const draftId = receipts[0].result.data?.draft_id;
    assert(draftId, "创建大纲未返回真实 draft_id");
    if (receipts.length === 1) return tool(stages[1], { ...presentationSlides(), draft_id: draftId });
    assert.deepEqual(receipts[1].result.data?.missing_slide_numbers, [], "写页回执仍有缺失正文页");
    if (receipts.length === 2) return tool(stages[2], { name: "ERP系统介绍-" + RUN, draft_id: draftId });
    const artifact = receipts[2].result.artifacts?.find(item => item.name?.endsWith(".pptx"));
    assert(artifact?.id && artifact.download_url, "终稿工具没有返回真实可下载 PPTX 产物");
    return { kind: "presentation", content: "演示文稿已生成，请通过下方文件卡片下载。" };
  }
  if (!receipts.length) {
    assert(datasourceId, "模型桩未获取浏览器创建的数据源标识");
    return tool("query_business_data", { query: CHAT_PROMPT, datasource_id: datasourceId, mode: "natural_language", max_rows: 10 });
  }
  assert.deepEqual(receipts.map(item => item.name), ["query_business_data"]);
  const row = receipts[0].result.data?.data?.[0];
  assert(row && Number.isFinite(Number(row.revenue)) && Number.isFinite(Number(row.deviceRate)), "HTTP 查询没有返回实际财务设备字段");
  return { kind: "analysis", content: "## 财务与设备摘要\n\n已读取本次明确选择的本地验收数据源，仅分析返回样本。\n\n"
    + `| 指标 | 当前值 | 结论 |\n|---|---:|---|\n| 销售收入 | ${(Number(row.revenue) / 10000).toFixed(2)} 万元 | 样本值 |\n| 设备运行率 | ${Number(row.deviceRate).toFixed(2)}% | 样本值 |\n\n`
    + `![财务与设备趋势](${chartUrl})\n\n建议继续核对异常设备与订单回款的时间口径。` };
}

function startFixture() {
  fixture = http.createServer(async (request, response) => {
    const raw = await requestBody(request);
    fixtureCalls.push({ method: request.method, url: request.url, raw });
    if (request.url === "/inspection-business") {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ records: INSPECTION_ROWS }));
      return;
    }
    if (request.url === "/business") {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ records: [{ month: "2026-08", revenue: "1280000.50", deviceRate: "98.70" }] }));
      return;
    }
    if (request.url === "/chart.png") {
      response.writeHead(200, { "Content-Type": "image/png", "Content-Length": PNG.length });
      response.end(PNG);
      return;
    }
    if (request.url === "/v1/chat/completions") {
      try {
      const payload = JSON.parse(raw || "{}");
      const toolNames = (payload.tools || []).map(tool => tool?.function?.name).filter(Boolean);
      assert(modelCalls.length < 40, "本地模型桩超过 40 次请求预算，可能出现重复工具循环");
      const call = { stream: Boolean(payload.stream), toolNames, messages: payload.messages || [] };
      modelCalls.push(call);
      const decision = modelDecision(payload, { datasourceId: source?.id, inspectionSourceId: inspectionSource?.id, chartUrl: `http://127.0.0.1:${fixture.address().port}/chart.png` });
      call.decision = decision.kind;
      call.scenario = decision.scenario || (decision.kind.startsWith("compound") ? "compound" : null);
      call.continuation = decision.continuation || false;
      if (decision.kind === "tool") {
        assert(payload.stream, "DSH 工具阶段应使用 SSE");
        call.selectedTool = decision.name;
        call.arguments = decision.args;
        streamToolCall(response, payload, decision.name, decision.args);
        return;
      }
      if (!payload.stream) {
        response.writeHead(200, { "Content-Type": "application/json" });
        response.end(JSON.stringify(normalCompletion(payload, decision.content)));
        return;
      }
      streamContent(response, payload, decision.content);
      } catch (error) {
        failures.push("模型桩合同失败：" + error.message);
        response.writeHead(422, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: { message: error.message } }));
      }
      return;
    }
    response.writeHead(404, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ error: { message: "fixture route not found" } }));
  });
  return new Promise(resolve => fixture.listen(0, "127.0.0.1", resolve));
}

async function visit(route, selector) {
  const response = await page.goto(BASE + route, { waitUntil: "networkidle0", timeout: 90000 });
  assert.equal(response.status(), 200, route);
  if (selector) await page.waitForSelector(selector, { visible: true, timeout: 90000 });
}

async function configureReasoningModel(endpoint) {
  await visit("/system/models", ".governance-panel");
  const existing = await page.$('[data-edit-model="reasoning"]');
  if (existing) {
    await clickHandle(existing);
  } else {
    await clickHandle(await page.$("[data-new-model]"));
    await fillElement(page, await page.waitForSelector('#platform-dialog [name="profile_name"]', { visible: true }), "reasoning");
  }
  await fillElement(page, await page.waitForSelector('#platform-dialog [name="provider"]', { visible: true }), "openai-compatible");
  await fillElement(page, await page.$('#platform-dialog [name="model"]'), "browser-ai-model");
  await fillElement(page, await page.$('#platform-dialog [name="endpoint"]'), endpoint);
  await fillElement(page, await page.$('#platform-dialog [name="api_key"]'), "browser-only-secret");
  await fillElement(page, await page.$('#platform-dialog [name="capabilities"]'), "chat\nreasoning");
  const saved = await waitUiResponse(page,
    (response, url) => url.pathname.startsWith("/api/models/profiles") && ["POST", "PUT"].includes(response.request().method()),
    async () => clickHandle(await page.$('#platform-dialog button[type="submit"]')));
  assert([200, 201].includes(saved.status()), await saved.text());
  await page.waitForFunction(() => !document.querySelector("#platform-dialog")?.open, { timeout: 90000 });
  const probe = await waitUiResponse(page,
    (response, url) => url.pathname === "/api/models/probe/reasoning" && response.request().method() === "POST",
    async () => clickHandle(await page.$('[data-probe-model="reasoning"]')));
  await assertStatus(probe, 200);
  await page.waitForFunction(() => document.querySelector("#platform-toast")?.textContent.includes("通过"), { timeout: 90000 });
  pass("模型管理页面可保存并实测 OpenAI 兼容 reasoning 模型", { endpoint });
}

async function runAxe(label, context = null) {
  await page.addScriptTag({ path: require.resolve("axe-core/axe.min.js") });
  const findings = await page.evaluate(async selector => {
    const target = selector ? document.querySelector(selector) : document;
    const report = await axe.run(target || document, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] },
    });
    return report.violations.map(item => ({
      id: item.id,
      impact: item.impact,
      nodes: item.nodes.map(node => ({ target: node.target, summary: node.failureSummary })),
    }));
  }, context);
  accessibilityFindings.push(...findings.map(item => ({ route: label, ...item })));
}

async function selectSourceInChat(name = SOURCE_NAME, description = "财务与设备经营分析的浏览器终验数据") {
  await clickHandle(await page.$("#business-source-btn"));
  await page.waitForSelector("#menu-panel.datasource-picker:not(.hidden)", { visible: true, timeout: 90000 });
  await page.waitForFunction(name => [...document.querySelectorAll(".datasource-option")]
    .some(node => node.textContent.includes(name)), { timeout: 90000 }, name);
  const rowRaw = await page.evaluateHandle(name => [...document.querySelectorAll(".datasource-option")]
    .find(node => node.textContent.includes(name)) || null, name);
  const row = rowRaw.asElement();
  assert(row, "Browser-created source is missing from the AI picker");
  const sourceDetails = await row.evaluate(node => ({
    text: node.textContent.replace(/\s+/g, " ").trim(),
    selected: node.querySelector("input")?.checked,
  }));
  assert(sourceDetails.text.includes(description));
  assert.equal(sourceDetails.selected, false, "Data source picker must not silently preselect a source");
  await clickHandle(await row.$('input[type="checkbox"]'));
  await clickText(page, "#menu-panel button", "完成");
  await page.waitForFunction(name => document.querySelector("#business-source-label")?.textContent.includes(name), {}, name);
  pass("AI 数据源弹层展示用途说明且必须由用户明确选择", { source: name, shown: sourceDetails.text });
}

async function selectDashboardTemplateInChat() {
  await clickHandle(await page.$("#dashboard-template-btn"));
  await page.waitForSelector("#dashboard-template-modal:not(.hidden)", { visible: true, timeout: 90000 });
  await page.waitForFunction(() => document.querySelectorAll("[data-dashboard-template-id]").length === 33, { timeout: 90000 });
  assert.equal(await page.$$eval(".dashboard-template-card.selected", nodes => nodes.length), 0,
    "Opening the dashboard picker must not silently select a template");
  await clickHandle(await page.$('[data-dashboard-mode="manual"]'));
  const card = await page.$("[data-dashboard-template-id]:not([disabled])");
  assert(card, "No selectable imported Cloud Brain dashboard template exists");
  const template = await card.evaluate(node => ({ id: node.dataset.dashboardTemplateId, name: node.querySelector("strong")?.textContent.trim() }));
  await clickHandle(card);
  await clickHandle(await page.$("#dashboard-template-use"));
  await page.waitForFunction(name => document.querySelector("#dashboard-template-label")?.textContent.includes(name), {}, template.name);
  pass("AI 输入区可手动选择智慧云脑大屏模板且无默认模板", template);
  return template;
}

async function sendChat(prompt, { expectedStatus = "completed" } = {}) {
  await fillElement(page, await page.$("#prompt-textarea"), prompt);
  const response = await waitUiResponse(page,
    (candidate, url) => url.pathname.includes("/api/chat/conversations/") && url.pathname.endsWith("/send") && candidate.request().method() === "POST",
    async () => clickHandle(await page.$("#composer-submit-button")), 180000);
  await assertStatus(response, 202);
  const accepted = envelopeData(await response.json());
  assert.equal(accepted.accepted, true, "DSH 未确认任务受理");
  assert(accepted.run_id && accepted.session_id && accepted.request_id, "受理回执缺少运行、会话或请求标识");
  const payload = JSON.parse(response.request().postData() || "{}");
  applicationRequests.push({ path: new URL(response.url()).pathname, payload, accepted });
  // 检查本次 run 的实际终态，避免按钮尚未进入忙碌状态时误判生成结束。
  await page.waitForFunction(runId => {
    const run = dshChat.controller.store.value.runs[runId];
    return run && ["completed", "failed", "partial", "interrupted", "cancelled"].includes(run.status);
  }, { timeout: 240000 }, accepted.run_id);
  const outcome = await page.evaluate(runId => {
    const value = dshChat.controller.store.value;
    const task = value.projections.tasks.find(item => item.run_ids?.includes(runId));
    return { run: value.runs[runId], messages: value.messages.filter(item => item.run_id === runId),
      deliveries: value.projections.deliveries.filter(item => item.source_run_id === runId), task,
      taskDeliveries: value.projections.deliveries.filter(item => task && item.task_id === (task.task_id || task.id)) };
  }, accepted.run_id);
  chatRuns.push({ accepted, ...outcome });
  assert.equal(outcome.run.status, expectedStatus, JSON.stringify(outcome));
  const assistant = outcome.messages.find(item => item.role === "assistant");
  assert(assistant, "运行已结束但没有对应助手消息");
  if (expectedStatus === "partial") {
    assert.equal(prompt, COMPOUND_PROMPT, "只有显式巡检缺页场景允许预期 partial");
    const parts = assistant.parts || [], failed = parts.filter(item => item.type === "tool_result" && item.result?.status === "failed");
    assert.equal(failed.length, 1);
    assert.equal(failed[0].tool, "finalize_presentation");
    const draft = parts.find(item => item.type === "tool_result" && item.tool === "create_presentation_plan")?.result?.data;
    assert(draft?.draft_id);
    assertMissingThirteen(failed[0].result, draft.draft_id);
    assertIncompleteRun(outcome.run, parts);
    assert.equal(outcome.task?.status, "partial");
    assert.deepEqual(outcome.task.missing_deliveries.map(item => item.type), ["presentation"]);
  } else {
    assert.equal(expectedStatus, "completed");
    assert(!assistant.parts?.some(item => item.type === "error" || item.type === "tool_result" && item.result?.status === "failed"), JSON.stringify(assistant));
  }
  await page.waitForFunction(() => document.querySelector("#composer-submit-button")?.getAttribute("aria-label") === "发送", { timeout: 30000 });
  return { payload, accepted, outcome };
}

/** 下载必须从页面控件发起；在磁盘核对浏览器真正保存的产物。 */
function completedDownload(events, expectedUrl, extension) {
  const starts = events.filter(item => item.kind === "begin");
  assert(starts.length <= 1, "一次点击产生了重复下载");
  const started = starts[0];
  if (!started) return null;
  const downloadedUrl = new URL(started.url);
  assert.equal(downloadedUrl.origin, expectedUrl.origin, "下载事件不能来自其它站点");
  assert(downloadedUrl.protocol === "blob:" || downloadedUrl.pathname === expectedUrl.pathname,
    "直接下载地址与请求的工件不匹配");
  assert(started.suggestedFilename.toLowerCase().endsWith(extension), "下载扩展名与实际预期不符");
  assert(/^[a-zA-Z0-9-]+$/.test(started.guid), "下载 GUID 无效");
  const progress = events.findLast(item => item.kind === "progress" && item.guid === started.guid);
  assert(progress?.state !== "canceled", "浏览器取消了本次下载");
  return progress?.state === "completed" ? { started, progress } : null;
}

function assertIncompleteRun(run, parts) {
  // 运行投影保存结构错误码，展示片段只携带文案；分别核对，不虚构片段字段。
  assert.equal(run.status, "partial");
  assert.equal(run.error?.code, "delivery_incomplete");
  assert(typeof run.error.message === "string" && run.error.message.length > 0);
  const errors = parts.filter(item => item.type === "error");
  assert.equal(errors.length, 1, "预设缺页场景只能出现一个对应运行的错误说明");
  assert.equal(errors[0].message, run.error.message);
  if (errors[0].code != null) assert.equal(errors[0].code, run.error.code);
}

function verifyDownloadedBytes(data, response, progress) {
  assert(data.length > 100, "浏览器下载了空文件或错误占位内容");
  assert.equal(progress.receivedBytes, data.length);
  assert(response.complete && !response.error, "API 文件响应尚未完整接收");
  assert.equal(response.bytes, data.length, "API 响应和下载文件大小不一致");
  assert(/^[0-9a-f]{64}$/.test(response.sha256), "API 响应缺少实际流摘要");
  const digest = createHash("sha256").update(data).digest("hex");
  assert.equal(digest, response.sha256, "浏览器保存的文件与本次下载响应字节不符");
  return digest;
}

async function downloadThroughUi(control, pathname, extension) {
  const eventStart = downloadEvents.length;
  const transportStart = downloadTransport.length;
  const href = await control.evaluate(node => node.getAttribute("href"));
  // 文档中心使用按钮，聊天附件使用链接；两者都由真实控件 fetch 后保存 Blob。
  const expectedUrl = new URL(href || pathname, BASE);
  assert.equal(expectedUrl.origin, new URL(BASE).origin, "下载链接必须属于本轮隔离应用");
  assert.equal(expectedUrl.pathname, pathname);
  const response = await waitUiResponse(page,
    (candidate, url) => url.origin === expectedUrl.origin && url.pathname === pathname && candidate.request().method() === "GET",
    () => clickHandle(control), 90000);
  await assertStatus(response, 200);
  const length = Number(response.headers()["content-length"] || 0);
  assert(Number.isFinite(length) && length <= 64 * 1024 * 1024, "下载响应超出验收预算");
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    const events = downloadEvents.slice(eventStart);
    const completed = completedDownload(events, expectedUrl, extension);
    // 浏览器会把附件响应体从 CDP 清空；旁听本轮隔离代理转发流，不发第二次请求。
    const transfers = downloadTransport.slice(transportStart).filter(item => item.path === pathname
      && item.host === expectedUrl.host && item.method === "GET");
    assert(transfers.length <= 1, "一次下载出现多个同路径 API 响应，无法证明文件身份");
    const transfer = transfers[0];
    assert(!transfer?.error, "下载传输异常：" + transfer?.error);
    if (completed && transfer?.complete) {
      assert.equal(transfer.status, 200);
      assert(!transfer.contentEncoding || transfer.contentEncoding === "identity", "下载传输编码不能与文件字节混比");
      const { started, progress } = completed;
      const absolute = path.join(DOWNLOADS, started.guid), data = fs.readFileSync(absolute);
      const digest = verifyDownloadedBytes(data, transfer, progress);
      downloadedFiles.push({ path: absolute, name: started.suggestedFilename, bytes: data.length,
        guid: started.guid, pathname, artifactId: pathname.match(/^\/api\/artifacts\/([^/]+)\/download$/)?.[1] || null,
        downloadUrl: started.url, state: progress.state, sha256: digest, responseSha256: digest });
      return data;
    }
    await sleep(100);
  }
  assert.fail("页面下载未完成：" + pathname);
}

/** 只读取本轮下载的有界 OOXML 包，不将 ZIP 内容解压到磁盘。 */
function zipEntries(data) {
  assert(data.length <= 64 * 1024 * 1024, "验收产物异常超大");
  let end = -1;
  for (let index = data.length - 22; index >= Math.max(0, data.length - 65557); index--) {
    if (data.readUInt32LE(index) === 0x06054b50 && index + 22 + data.readUInt16LE(index + 20) === data.length) { end = index; break; }
  }
  assert(end >= 0, "下载内容不是完整 ZIP/OOXML 文件");
  const count = data.readUInt16LE(end + 10), entries = new Map();
  let offset = data.readUInt32LE(end + 16);
  assert(count > 0 && count < 10000);
  for (let index = 0; index < count; index++) {
    assert.equal(data.readUInt32LE(offset), 0x02014b50);
    const method = data.readUInt16LE(offset + 10), bytes = data.readUInt32LE(offset + 20);
    const expanded = data.readUInt32LE(offset + 24), nameLength = data.readUInt16LE(offset + 28);
    const name = data.subarray(offset + 46, offset + 46 + nameLength).toString("utf8");
    const local = data.readUInt32LE(offset + 42);
    assert.equal(data.readUInt32LE(local), 0x04034b50);
    const start = local + 30 + data.readUInt16LE(local + 26) + data.readUInt16LE(local + 28);
    assert(expanded <= 16 * 1024 * 1024 && start + bytes <= data.length, "ZIP 成员大小超出验收范围");
    assert([0, 8].includes(method), "不支持的 OOXML 压缩方式");
    const content = method === 0 ? data.subarray(start, start + bytes)
      : zlib.inflateRawSync(data.subarray(start, start + bytes), { maxOutputLength: 16 * 1024 * 1024 });
    assert.equal(content.length, expanded);
    assert(!entries.has(name), "OOXML 存在重复成员名");
    entries.set(name, content.toString("utf8"));
    offset += 46 + nameLength + data.readUInt16LE(offset + 30) + data.readUInt16LE(offset + 32);
  }
  return entries;
}

function inspectFiles(request) {
  // 解释器必须由隔离入口显式提供，不能偷偷改用部署环境或安装依赖。
  const python = process.env.TEST5_E2E_PYTHON;
  assert(python && path.isAbsolute(python) && fs.existsSync(python), "请设置 TEST5_E2E_PYTHON 为现有隔离 Python 的绝对路径");
  const result = spawnSync(python, [path.join(__dirname, "compound_file_inspector.py")], {
    input: JSON.stringify(request), encoding: "utf8", timeout: 30000, maxBuffer: 8 * 1024 * 1024,
    windowsHide: true, env: { ...process.env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" },
  });
  assert(!result.error && result.status === 0, result.error?.message || result.stderr || "离线文件校验失败");
  return JSON.parse(result.stdout);
}

function assertPresentationFile(data, expectedTitles, pageCount = 5) {
  return inspectFiles({ kind: "pptx", base64: data.toString("base64"), expected: { pages: pageCount, titles: expectedTitles } });
}

async function deleteCurrentConversation() {
  const currentTitle = await page.$eval("#header-title", node => node.textContent.trim());
  const raw = await page.evaluateHandle(title => [...document.querySelectorAll("#conversation-list .history-item")]
    .find(node => node.textContent.includes(title))?.querySelector(".history-options") || null, currentTitle);
  const button = raw.asElement();
  if (!button) return false;
  page.once("dialog", dialog => dialog.accept());
  const deleted = await waitUiResponse(page,
    (response, url) => url.pathname.startsWith("/api/chat/conversations/") && response.request().method() === "DELETE",
    () => clickHandle(button));
  await assertStatus(deleted, 200);
  return true;
}

async function openGenerationDialog() {
  await visit("/documents?tab=generations", "[data-new-generation]");
  await clickHandle(await page.$("[data-new-generation]"));
  await page.waitForSelector("#platform-dialog[open]", { visible: true, timeout: 90000 });
}

async function createGeneration({ name, outputType, mode, bodyText = "", tableText = "", prompt = "", templateId = "", pageCount = 5 }) {
  await openGenerationDialog();
  const dialog = "#platform-dialog";
  await fillElement(page, await page.$(`${dialog} [name="name"]`), name);
  await page.select(`${dialog} [name="outputType"]`, outputType);
  await page.select(`${dialog} [name="mode"]`, mode);
  if (mode === "ai") await fillElement(page, await page.$(`${dialog} [name="prompt"]`), prompt);
  if (mode === "manual" && ["document", "pdf"].includes(outputType)) {
    await fillElement(page, await page.$(`${dialog} [name="bodyText"]`), bodyText);
  }
  if (mode === "manual" && outputType === "spreadsheet") {
    await fillElement(page, await page.$(`${dialog} [name="tableText"]`), tableText);
  }
  if (outputType === "presentation") {
    assert.equal(await page.$eval(`${dialog} [name="templateId"]`, node => node.value), "",
      "PPT creation must not silently select a template");
    await page.select(`${dialog} [name="templateId"]`, templateId);
    await fillElement(page, await page.$(`${dialog} [name="pageCount"]`), String(pageCount));
  }
  const response = await waitUiResponse(page,
    (candidate, url) => url.pathname === "/api/v1/documents/generations" && candidate.request().method() === "POST",
    async () => clickHandle(await page.$(`${dialog} button[type="submit"]`)));
  await assertStatus(response, 201);
  let item = null;
  try { item = envelopeData(await response.json()); }
  catch (error) {
    // 页面消费响应后可能立即切换历史，CDP 随之释放响应体；从已完成的可见路由
    // 读取同一记录标识，不另行调用隐藏的应用接口。
    await page.waitForFunction(() => Boolean(new URL(location.href).searchParams.get("id")), { timeout: 90000 });
    item = { id: await page.evaluate(() => new URL(location.href).searchParams.get("id")) };
  }
  assert(item?.id, "Generation creation did not expose an id in either the response or resulting UI route");
  generationIds.push(item.id);
  await page.waitForFunction(id => new URL(location.href).searchParams.get("id") === id, { timeout: 90000 }, item.id);
  await page.waitForSelector(".catalog-detail-panel", { visible: true, timeout: 90000 });
  return item;
}

async function executeGeneration(id) {
  const response = await waitUiResponse(page,
    (candidate, url) => url.pathname === `/api/v1/documents/generations/${id}/execute` && candidate.request().method() === "POST",
    async () => clickHandle(await page.$("[data-execute-generation]")), 240000);
  await assertStatus(response, 200);
  const item = envelopeData(await response.json());
  await page.waitForFunction(() => document.querySelector(".catalog-detail-panel")?.textContent.includes("完成"), { timeout: 240000 });
  await page.waitForFunction(() => {
    const image = document.querySelector("[data-generation-preview]");
    return image && image.complete && image.naturalWidth > 0;
  }, { timeout: 180000 });
  assert(item?.artifact?.id, "Completed generation has no artifact");
  const extension = { document: ".docx", pdf: ".pdf", spreadsheet: ".xlsx", presentation: ".pptx" }[item.outputType];
  const downloaded = await downloadThroughUi(await page.waitForSelector("[data-download-generation]", { visible: true }),
    `/api/v1/documents/generations/${id}/download`, extension);
  if (item.outputType === "pdf") assert.equal(downloaded.subarray(0, 5).toString("ascii"), "%PDF-");
  else {
    const entries = zipEntries(downloaded);
    if (item.outputType === "document") assert(entries.get("word/document.xml")?.includes("经营结论"), "Word 正文没有实际生成所要求的内容");
    if (item.outputType === "spreadsheet") {
      assert(entries.has("xl/workbook.xml"), "下载内容不是 Excel 工作簿");
      const content = [...entries].filter(([name]) => name.startsWith("xl/") && name.endsWith(".xml")).map(([, value]) => value).join("\n");
      assert(content.includes("1280000.50") || content.includes("1280000.5"), "Excel 中没有实际输入的销售数值");
    }
    if (item.outputType === "presentation") assertPresentationFile(downloaded, ["ERP 核心价值", "业务协同闭环", "实施路线图"]);
  }
  return item;
}

async function archiveCurrentGeneration(id) {
  const archive = await page.$("[data-archive-generation]");
  assert(archive, `Archive button is missing for ${id}`);
  await clickHandle(archive);
  await page.waitForSelector("#platform-dialog[open]", { visible: true, timeout: 30000 });
  const response = await waitUiResponse(page,
    (candidate, url) => url.pathname === `/api/v1/documents/generations/${id}` && candidate.request().method() === "DELETE",
    async () => clickHandle(await page.$('#platform-dialog button[type="submit"]')));
  await assertStatus(response, 200);
  await page.waitForFunction(() => new URL(location.href).searchParams.get("id") === null, { timeout: 90000 });
}

async function createManualPresentation() {
  const name = "终验人工 ERP 汇报-" + RUN;
  const created = await createGeneration({
    name,
    outputType: "presentation",
    mode: "manual",
    templateId: "executive-ivory",
    pageCount: 5,
  });
  await clickHandle(await page.$("[data-create-plan]"));
  await page.waitForSelector("#platform-dialog[open]", { visible: true, timeout: 30000 });
  await fillElement(page, await page.$('#platform-dialog [name="title"]'), "ERP 系统实施汇报");
  await fillElement(page, await page.$('#platform-dialog [name="subtitle"]'), "浏览器端分阶段编写验收");
  await fillElement(page, await page.$('#platform-dialog [name="outline"]'), "ERP 核心价值\n业务协同闭环\n实施路线图");
  const plan = await waitUiResponse(page,
    (candidate, url) => url.pathname === `/api/v1/documents/generations/${created.id}/presentation-plan` && candidate.request().method() === "POST",
    async () => clickHandle(await page.$('#platform-dialog button[type="submit"]')));
  await assertStatus(plan, 200);
  await page.waitForSelector("[data-write-slides]", { visible: true, timeout: 90000 });
  await clickHandle(await page.$("[data-write-slides]"));
  await page.waitForSelector("#platform-dialog[open] .slide-editor", { visible: true, timeout: 30000 });
  const editors = await page.$$("#platform-dialog .slide-editor");
  assert.equal(editors.length, 3, "Five-page PPT must expose exactly three body-page editors");
  for (let index = 0; index < editors.length; index += 1) {
    const number = index + 1;
    await fillElement(page, await page.$(`#platform-dialog [name="message_${number}"]`),
      `第 ${number} 页的核心判断包含原因、业务影响和可复核证据，确保内容不是只有标题的空页面。`);
    await fillElement(page, await page.$(`#platform-dialog [name="items_${number}"]`),
      `统一第 ${number} 项业务数据口径并明确责任人\n贯通第 ${number} 项端到端流程并保留审计记录\n通过第 ${number} 项真实场景验收持续优化`);
  }
  const slides = await waitUiResponse(page,
    (candidate, url) => url.pathname === `/api/v1/documents/generations/${created.id}/presentation-slides` && candidate.request().method() === "POST",
    async () => clickHandle(await page.$('#platform-dialog button[type="submit"]')));
  await assertStatus(slides, 200);
  await page.waitForSelector("[data-execute-generation]", { visible: true, timeout: 90000 });
  const completed = await executeGeneration(created.id);
  assert.equal(completed.actualPageCount, 5);
  assert.equal(completed.templateId, "executive-ivory");
  await page.screenshot({ path: path.join(OUTPUT, "manual-ppt-completed.png"), fullPage: true });
  pass("人工 PPT 可按大纲和最多四页批次写作并精确生成 5 页", { id: created.id, actualPageCount: completed.actualPageCount });
  await archiveCurrentGeneration(created.id);
}

function compoundCheckpoint(label, expectedReads) {
  const business = fixtureCalls.filter(item => item.url === "/inspection-business");
  const value = { label, reads: business.length, modelCalls: modelCalls.length };
  compoundEvidence.checkpoints.push(value);
  assert.equal(business.length, expectedReads, "巡检接口出现重复取数：" + label);
  assert(business.every(item => item.method === "GET"));
}

function actualToolResults(outcome) {
  const parts = outcome.messages.filter(item => item.role === "assistant").flatMap(item => item.parts || []);
  return parts.filter(item => item.type === "tool_result").map(item => ({ name: item.tool, result: item.result, id: item.id,
    arguments: parts.find(call => call.type === "tool_call" && (call.id === item.id || "result-" + call.id === item.id))?.arguments }));
}

function assertCompoundTask(task) {
  assert(task?.task_id && task.id === task.task_id);
  assert.deepEqual(Object.fromEntries(task.required_deliveries.map(item => [item.type, item.count])),
    { presentation: 1, document: 1, spreadsheet: 1, dashboard: 1 });
  const pages = task.delivery_constraints.find(item => item.kind === "page_count");
  assert(pages && pages.minimum === 15 && pages.maximum === 15);
  assert(task.delivery_constraints.some(item => item.kind === "embedded_dashboard"));
}

async function downloadCompoundArtifact(kind, artifact) {
  assert(artifact?.id && artifact.download_url);
  const card = await page.waitForSelector(`[data-artifact-id="${artifact.id}"], [data-delivery-id="${artifact.id}"]`, { visible: true, timeout: 30000 });
  const control = await card.$('a[aria-label^="下载"], a[title="下载"]') || await visibleByText(card, "a", "下载");
  const extension = { chart: ".png", dashboard: ".png", word: ".docx", spreadsheet: ".xlsx", presentation: ".pptx" }[kind];
  await downloadThroughUi(control, new URL(artifact.download_url, BASE).pathname, extension);
  const receipt = { ...downloadedFiles.at(-1), artifactId: artifact.id, kind };
  compoundEvidence.downloads[kind] = receipt;
  return receipt;
}

async function inspectCompoundDashboard(report, query) {
  // 业务卡片与任务投影会去重，只沿当前可见、精确指向该大屏的真实链接进入。
  const rawControl = await page.waitForFunction(reportId => [...document.querySelectorAll('#messages a[href]')].find(link =>
    link.getClientRects().length && new URL(link.href).pathname === '/report/view/' + reportId), { timeout: 30000 }, report.reportId);
  const control = rawControl.asElement();
  assert(control, "任务中没有真实大屏打开入口");
  let responseBody, responseError;
  const popupReady = new Promise((resolve, reject) => {
    const deadline = setTimeout(() => reject(new Error("大屏入口没有打开浏览器页面")), 30000);
    page.once("popup", popup => {
    clearTimeout(deadline);
    popup.on("response", response => {
      const url = new URL(response.url());
      if (url.origin === new URL(BASE).origin && url.pathname === `/api/v1/reports/${report.reportId}`) {
        response.json().then(body => { responseBody = envelopeData(body); }, error => { responseError = error; });
      }
    });
    resolve(popup);
    });
  });
  const [popup] = await Promise.all([popupReady, clickHandle(control)]);
  try {
    await popup.setViewport({ width: 1440, height: 960, deviceScaleFactor: 1 });
    const deadline = Date.now() + 30000;
    let reportFrame;
    while (Date.now() < deadline) {
      // 业务外壳通过编码后的 path 参数加载子页，沿真实 iframe 取上下文，不匹配明文路由。
      const frameHandle = await popup.$('#business-module-frame');
      const candidate = frameHandle ? await frameHandle.contentFrame() : popup.mainFrame();
      if (candidate && await candidate.$('.report-viewer')) reportFrame = candidate;
      if (reportFrame && responseBody) break;
      assert(!responseError, responseError?.message);
      await sleep(50);
    }
    assert(reportFrame, "大屏页面的真实业务框架未就绪");
    assert(responseBody, "大屏页面未加载本次持久报告响应");
    assert.equal(responseBody.id, report.reportId);
    assert.equal(responseBody.version, report.version);
    assert.equal(responseBody.refreshSeconds, 0);
    const content = typeof responseBody.content === "string" ? JSON.parse(responseBody.content) : responseBody.content;
    const widgets = content.widgets.filter(widget => widget.config?.queryId === query.queryId);
    assert(widgets.length > 0);
    for (const widget of widgets) {
      assert.deepEqual(widget.config.chartDataParsed, INSPECTION_ROWS);
      assert.equal(widget.config.dataType, 1);
      assert.equal(widget.config.snapshotOnly, true);
    }
    await reportFrame.waitForFunction(() => document.body.innerText.includes("已巡检") && document.body.innerText.includes("未巡检")
      && document.body.innerText.includes("合成验收非生产"), { timeout: 30000 });
    // 只等有限入场动画自然结束，保留真实动效；无限背景动画不作为截图就绪条件。
    await reportFrame.waitForFunction(() => {
      const viewer = document.querySelector('.report-viewer');
      return viewer && viewer.getAnimations({ subtree: true }).every(animation => {
        const timing = animation.effect?.getComputedTiming();
        return !timing || !Number.isFinite(timing.endTime) || ['finished', 'idle'].includes(animation.playState);
      });
    }, { timeout: 30000 });
    await popup.screenshot({ path: path.join(OUTPUT, "compound-dashboard.png"), fullPage: true });
    compoundEvidence.dashboard = { id: report.reportId, version: report.version, queryId: query.queryId, response: responseBody };
  } catch (error) {
    // 先保存弹窗现场，再关闭页面；区分框架、请求与内容失败，避免只剩聊天页截图。
    const details = { url: popup.url(), responseBody, responseError: responseError?.message, frames: [] };
    for (const frame of popup.frames()) {
      const item = { url: frame.url() };
      try { item.text = await frame.evaluate(() => document.body?.innerText.slice(0, 12000) || ''); }
      catch (cause) { item.error = cause.message; }
      details.frames.push(item);
    }
    await popup.screenshot({ path: path.join(OUTPUT, 'compound-dashboard-failure.png'), fullPage: true })
      .catch(cause => { details.screenshotError = cause.message; });
    compoundEvidence.dashboardFailure = details;
    fs.writeFileSync(path.join(OUTPUT, 'compound-dashboard-failure.json'), JSON.stringify(details, null, 2));
    throw error;
  } finally { await popup.close(); }
}

async function deleteCompoundReport(report) {
  const frame = await visitBusiness(page, BASE, "/dashboards", "[data-report-id]");
  const card = await frame.waitForSelector(`[data-report-id="${report.reportId}"]`, { visible: true, timeout: 30000 });
  assert((await card.$eval('.name', node => node.textContent.trim())) === COMPOUND_DASHBOARD);
  const response = await waitUiResponse(page,
    (candidate, url) => url.pathname === `/api/v1/reports/${report.reportId}` && candidate.request().method() === "DELETE",
    async () => {
      await clickHandle(await card.$('button[aria-label="删除报表"]'));
      await frame.waitForFunction(name => [...document.querySelectorAll('.el-message-box')].some(box =>
        box.getClientRects().length && box.textContent.includes(`确定删除报表「${name}」吗？`)), {}, COMPOUND_DASHBOARD);
      const raw = await frame.evaluateHandle(name => [...document.querySelectorAll('.el-message-box')].find(box =>
        box.getClientRects().length && box.textContent.includes(`确定删除报表「${name}」吗？`)), COMPOUND_DASHBOARD);
      await clickText(raw.asElement(), 'button', '确定');
    });
  await assertStatus(response, 200);
  await frame.waitForSelector(`[data-report-id="${report.reportId}"]`, { hidden: true });
  compoundEvidence.cleanup = { reportId: report.reportId, status: response.status() };
}

async function runCompoundInspection() {
  phase("原话巡检复合任务：真实部分交付与跨轮补页");
  fs.writeFileSync(path.join(OUTPUT, 'compound-fixture.json'), JSON.stringify(compoundEvidence.fixture, null, 2));
  ({ source: inspectionSource } = await createHttpSourceThroughUi(page, BASE, {
    name: INSPECTION_SOURCE, method: "GET", url: `http://127.0.0.1:${fixture.address().port}/inspection-business`,
    description: INSPECTION_DESCRIPTION, allowPrivate: true, allowAutomatic: true, recordPath: "$.records[*]",
    fields: INSPECTION_COLUMNS.map(name => ({ name, path: `$[${JSON.stringify(name)}]`, type: name === "设备数量" ? "integer" : "string", comment: name, required: true })),
  }));
  compoundEvidence.source = { id: inspectionSource.id, name: INSPECTION_SOURCE };
  compoundCheckpoint("创建合成来源后", 0);
  await visit("/ai", "#composer-form");
  await clickHandle(await page.$("#new-chat-btn"));
  await selectSourceInChat(INSPECTION_SOURCE, INSPECTION_DESCRIPTION);
  const modelStart = modelCalls.length;
  const first = await sendChat(COMPOUND_PROMPT, { expectedStatus: "partial" });
  compoundEvidence.first = first;
  assert.equal(first.payload.content, COMPOUND_PROMPT);
  assert.deepEqual(first.payload.datasource_ids, [inspectionSource.id]);
  assert(!first.payload.template_id && !first.payload.plugin_inputs?.page_count, "原话页数不得由旧PPT插件参数替代");
  assert.notEqual(first.payload.dashboard_template_mode, "manual");
  assertCompoundTask(first.outcome.task);
  const taskId = first.outcome.task.task_id;
  const cid = new URL(page.url()).searchParams.get('cid');
  assert(cid);
  conversationIds.push(cid);
  const firstTools = actualToolResults(first.outcome);
  compoundEvidence.toolCalls.push({ runId: first.accepted.run_id, calls: firstTools });
  assert.equal(firstTools.length, 11);
  assert.deepEqual(firstTools.map(item => item.name), ["query_business_data", "render_chart_image", "export_query_spreadsheet", "create_dashboard_from_queries", "render_dashboard_image", "render_document", "create_presentation_plan", "write_presentation_slides", "write_presentation_slides", "write_presentation_slides", "finalize_presentation"]);
  const query = firstTools[0].result.data, report = firstTools[3].result.data;
  const draftId = firstTools[6].result.data.draft_id;
  assertMissingThirteen(firstTools[10].result, draftId);
  compoundCheckpoint("第一轮真实缺页终态", 1);
  const artifacts = { chart: firstTools[1].result.artifacts[0], spreadsheet: firstTools[2].result.artifacts[0],
    dashboard: firstTools[4].result.artifacts[0], word: firstTools[5].result.artifacts[0] };
  const before = {};
  for (const [kind, artifact] of Object.entries(artifacts)) before[kind] = await downloadCompoundArtifact(kind, artifact);
  await page.screenshot({ path: path.join(OUTPUT, 'compound-partial.png'), fullPage: true });
  const historyResponse = await waitUiResponse(page,
    (candidate, url) => url.pathname === `/api/chat/conversations/${cid}` && candidate.request().method() === 'GET',
    // 会话订阅持续使用 SSE；以真实历史响应和下方任务状态证明恢复，不等待网络空闲。
    () => page.reload({ waitUntil: 'domcontentloaded', timeout: 90000 }));
  await assertStatus(historyResponse, 200);
  const history = envelopeData(await historyResponse.json());
  const restoredTask = history.projections?.tasks?.find(item => item.task_id === taskId);
  assertCompoundTask(restoredTask);
  assert.equal(restoredTask.status, 'partial');
  const restoredRuns = Array.isArray(history.runs) ? history.runs : Object.values(history.runs || {});
  compoundEvidence.restored = { task: restoredTask, historyRunIds: restoredRuns.map(item => item.id || item.run_id) };
  await page.waitForFunction(id => dshChat.controller.store.value.projections.tasks.some(item => item.task_id === id && item.status === 'partial'), {}, taskId);
  compoundCheckpoint("刷新历史与下载原文件后", 1);
  const second = await sendChat(COMPOUND_CONTINUE);
  compoundEvidence.second = second;
  assert.equal(second.payload.content, "继续，使用已有结果制作大屏和Excel", "第二轮必须发送原始自然续办句");
  assert.equal(Object.hasOwn(second.payload, "task_id"), false, "自然续办不能借显式 task_id 绕过任务识别");
  assert.notEqual(second.accepted.run_id, first.accepted.run_id);
  assert.equal(second.outcome.task?.task_id, taskId);
  assertCompoundTask(second.outcome.task);
  assert.deepEqual(second.outcome.task.missing_deliveries, []);
  assert.deepEqual(second.outcome.task.constraint_failures, []);
  const secondTools = actualToolResults(second.outcome);
  compoundEvidence.toolCalls.push({ runId: second.accepted.run_id, calls: secondTools });
  assert.deepEqual(secondTools.map(item => item.name), ["write_presentation_slides", "finalize_presentation"]);
  assert.equal(secondTools[0].arguments.draft_id, draftId);
  assert.deepEqual(secondTools[0].arguments.slides.map(row => row.slide_number), [13]);
  assert.equal(secondTools[1].arguments.draft_id, draftId);
  for (const artifact of Object.values(artifacts)) {
    const inherited = second.outcome.taskDeliveries.find(item => item.artifact_id === artifact.id);
    assert(inherited?.status === 'ready' && inherited.source_run_id === first.accepted.run_id, '原交付未继承或身份被伪装成本轮新建');
  }
  compoundCheckpoint("第二轮只补缺页后", 1);
  artifacts.presentation = secondTools[1].result.artifacts.find(item => item.name.endsWith('.pptx'));
  await downloadCompoundArtifact('presentation', artifacts.presentation);
  for (const kind of ['word', 'spreadsheet']) {
    const after = await downloadCompoundArtifact(kind, artifacts[kind]);
    assert.equal(after.sha256, before[kind].sha256, '继续意外重写了既有文件');
  }
  const proof = firstTools[1].result.metadata?.delivery_evidence;
  assert(proof?.source_query_id === query.queryId && proof.source_datasource_id === inspectionSource.id);
  assert.deepEqual(proof.chart_types, ['pie']);
  const request = { download_root: DOWNLOADS, files: Object.fromEntries(Object.entries(compoundEvidence.downloads).map(([kind, item]) => [kind, item.path])), expected: {
    pie: { labels: ['已巡检', '未巡检'], values: [3, 5] }, rendered_values_sha256: proof.rendered_values_sha256,
    word: { text: ['应检8台，已巡检3台，未巡检5台，未检占比62.5%', INSPECTION_WINDOW, '合成验收非生产', '保存查询数据快照，非实时页面截图', query.queryId], song_font: true },
    spreadsheet: { columns: INSPECTION_COLUMNS, rows: INSPECTION_ROWS.map(row => INSPECTION_COLUMNS.map(key => row[key])), song_font: true,
      origin: { '查询标识': query.queryId, '数据源标识': inspectionSource.id, '数据源版本': query.datasourceVersion, '样本行数': 2, '已截断': 'False' } },
    presentation: { pages: 15, page_titles: [COMPOUND_TITLE, ...inspectionSlides(query.data).map(row => row.title), '核对后再执行'],
      min_body_characters: 25, pie: { labels: ['已巡检', '未巡检'], values: [3, 5] } },
  } };
  compoundEvidence.fileInspection = inspectFiles(request);
  fs.writeFileSync(path.join(OUTPUT, 'compound-file-inspection.json'), JSON.stringify(compoundEvidence.fileInspection, null, 2));
  const wordReceipt = second.outcome.task.constraint_receipts.find(item => item.kind === 'docx');
  assert(wordReceipt?.embedded_image?.report_id === report.reportId);
  assert.equal(wordReceipt.embedded_image.report_version, report.version);
  assert.equal(wordReceipt.embedded_image.sha256, compoundEvidence.fileInspection.dashboard.sha256);
  const pptReceipt = second.outcome.task.constraint_receipts.find(item => item.kind === 'pptx');
  assert.equal(pptReceipt?.pages, 15);
  assert.equal(pptReceipt.sha256, compoundEvidence.fileInspection.presentation.sha256);
  await inspectCompoundDashboard(report, query);
  compoundCheckpoint("打开大屏与核验全部下载后", 1);
  assert.equal(modelCalls.slice(modelStart).filter(item => item.selectedTool === 'query_business_data').length, 1);
  await runAxe('/ai-compound-delivery');
  await page.screenshot({ path: path.join(OUTPUT, 'compound-completed.png'), fullPage: true });
  pass("用户原话的巡检双轮任务保留查询与草稿，交付15页PPT、真实饼图、Excel和双图Word及可打开大屏", {
    taskId, draftId, firstRun: first.accepted.run_id, secondRun: second.accepted.run_id, queryId: query.queryId, reportId: report.reportId,
    businessReads: 1, pages: compoundEvidence.fileInspection.presentation.pages,
  });
  assert(await deleteCurrentConversation());
  await deleteCompoundReport(report);
  assert(await removeHttpSourceThroughUi(page, BASE, INSPECTION_SOURCE));
}

async function main() {
  assert(PASSWORD, "Supply TEST5_E2E_PASSWORD");
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, "isolated", "Browser writes are allowed only against the isolated acceptance server");
  const target = new URL(BASE);
  assert(["127.0.0.1", "localhost", "[::1]"].includes(target.hostname) && target.port && target.port !== "8011",
    "只允许本机隔离验收端口，禁止业务服务 8011 与非本机地址");
  fs.mkdirSync(DOWNLOADS, { recursive: true });
  await startFixture();
  // 旁听隔离代理已在转发的下载响应，不增加请求，用于区分服务器与浏览器合成状态。
  const originalHttpRequest = http.request;
  http.request = function (...args) {
    const request = originalHttpRequest.apply(this, args);
    if (/^\/api\/(?:v1\/documents\/generations|artifacts)\/[^/]+\/download(?:\?|$)/.test(request.path || "")) {
      request.once("response", response => {
        const record = { path: request.path.split("?")[0], host: String(request.getHeader("host")), method: request.method,
          status: response.statusCode, contentType: response.headers["content-type"], contentEncoding: response.headers["content-encoding"],
          contentLength: response.headers["content-length"], bytes: 0 };
        const digest = createHash("sha256");
        downloadTransport.push(record);
        response.on("data", chunk => { record.bytes += chunk.length; digest.update(chunk); });
        response.once("end", () => { record.sha256 = digest.digest("hex"); record.complete = true; });
        response.once("aborted", () => { record.error = "响应传输中断"; });
        response.once("error", error => { record.error = error.message; });
      });
    }
    return request;
  };
  restoreTransportObserver = () => { http.request = originalHttpRequest; };
  browser = await puppeteer.launch({ executablePath: EDGE, headless: true });
  page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 960, deviceScaleFactor: 1 });
  downloadClient = await browser.target().createCDPSession();
  downloadClient.on("Browser.downloadWillBegin", event => downloadEvents.push({ kind: "begin", ...event }));
  downloadClient.on("Browser.downloadProgress", event => downloadEvents.push({ kind: "progress", ...event }));
  await downloadClient.send("Browser.setDownloadBehavior", { behavior: "allowAndName", downloadPath: DOWNLOADS, eventsEnabled: true });
  page.on("pageerror", error => failures.push("pageerror " + error.message));
  page.on("response", response => {
    if (response.url().startsWith(BASE) && response.status() >= 500) {
      const record = { status: response.status(), path: new URL(response.url()).pathname };
      responseFailures.push(record);
      failures.push(record.status + " " + record.path);
      // 保留错误原文辅助定位静态资源等偶发 500，不额外重发请求或忽略失败。
      response.text().then(body => { record.body = body.slice(0, 3000); }, error => { record.bodyError = error.message; });
    }
  });

  phase("登录、模型配置与授权数据源");
  await login(page, BASE, PASSWORD);
  await configureReasoningModel(`http://127.0.0.1:${fixture.address().port}/v1`);
  ({ source } = await createHttpSourceThroughUi(page, BASE, {
    name: SOURCE_NAME,
    method: "GET",
    url: `http://127.0.0.1:${fixture.address().port}/business`,
    description: "财务与设备经营分析的浏览器终验数据",
    allowPrivate: true,
    allowAutomatic: true,
    recordPath: "$.records[*]",
    fields: [
      { name: "month", path: "$.month", type: "string", comment: "经营月份", required: true },
      { name: "revenue", path: "$.revenue", type: "decimal", comment: "销售收入", required: true },
      { name: "deviceRate", path: "$.deviceRate", type: "decimal", comment: "设备运行率", required: true },
    ],
  }));
  assert(source?.id);
  pass("通过数据源界面创建可供 AI 使用的真实 HTTP 数据源", { id: source.id });

  phase("AI 工作台普通对话、数据与模板上下文");
  await visit("/ai", "#composer-form");
  await page.waitForSelector("#business-source-btn:not(.hidden)", { visible: true, timeout: 90000 });
  await page.waitForSelector("#dashboard-template-btn:not(.hidden)", { visible: true, timeout: 90000 });
  const links = await page.$$eval("#ai-resource-links a", nodes => nodes.map(node => ({ label: node.textContent.trim(), href: node.getAttribute("href") })));
  assert.deepEqual(links, [
    { label: "技能", href: "/skills" },
    { label: "插件", href: "/plugins" },
    { label: "智能体", href: "/bots" },
  ]);
  await selectSourceInChat();
  const dashboardTemplate = await selectDashboardTemplateInChat();
  const { payload: normalPayload, accepted: normalAccepted } = await sendChat(CHAT_PROMPT);
  conversationIds.push(new URL(applicationRequests.at(-1).path, "http://local").pathname.split("/").at(-2));
  assert.deepEqual(normalPayload.datasource_ids, [source.id]);
  assert.equal(normalPayload.dashboard_template_mode, "manual");
  assert.equal(normalPayload.dashboard_template_id, dashboardTemplate.id);
  await page.waitForFunction(() => {
    const body = [...document.querySelectorAll("#messages .msg-row .markdown-body")].at(-1);
    const image = body?.querySelector("img");
    return body?.querySelector("table") && image?.complete && image.naturalWidth > 0;
  }, { timeout: 120000 });
  await page.waitForFunction(title => [...document.querySelectorAll('#conversation-list .history-title')].some(node => node.textContent.trim() === title), { timeout: 30000 }, CHAT_TITLE);
  const rendered = await page.evaluate(() => ({
    heading: [...document.querySelectorAll("#messages .msg-row h2")].at(-1)?.textContent.trim(),
    tableRows: [...document.querySelectorAll("#messages .msg-row .markdown-body")].at(-1)?.querySelectorAll("table tr").length,
    imageAlt: [...document.querySelectorAll("#messages .msg-row .markdown-body img")].at(-1)?.alt,
    title: document.querySelector("#header-title")?.textContent.trim(),
    history: [...document.querySelectorAll("#conversation-list .history-title")].map(node => node.textContent.trim()),
  }));
  assert.equal(rendered.heading, "财务与设备摘要");
  assert.equal(rendered.tableRows, 3);
  assert.equal(rendered.imageAlt, "财务与设备趋势");
  assert.equal(rendered.title, CHAT_TITLE);
  assert(rendered.history.includes(CHAT_TITLE));
  assert(!rendered.history.some(value => value.includes("第二句")), "Conversation rail used more than the first sentence");
  assert(modelCalls.some(call => call.selectedTool === "query_business_data"), "普通分析没有实际查询已选 HTTP 数据源");
  await runAxe("/ai-conversation");
  await page.screenshot({ path: path.join(OUTPUT, "ai-markdown-result.png"), fullPage: true });
  pass("DSH 对话实际读取已选 HTTP 数据、渲染 Markdown 并以本轮 completed 终态结束", { ...rendered, runId: normalAccepted.run_id });
  assert(await deleteCurrentConversation());

  phase("AI 对话分阶段生成 PPT");
  await clickHandle(await page.$("#new-chat-btn"));
  await clickHandle(await page.$("#add-menu-btn"));
  await clickHandle(await page.waitForSelector('#menu-panel [data-act="presentation"]', { visible: true }));
  await page.waitForSelector("#template-modal:not(.hidden)", { visible: true, timeout: 90000 });
  await page.waitForFunction(() => document.querySelectorAll('.template-card:not([data-template^="custom:"])').length === 20, { timeout: 90000 });
  assert.equal(await page.$$eval(".template-card.selected", nodes => nodes.length), 0,
    "PPT template library must not have a default selection");
  let pptCard = await page.$('[data-template="executive-ivory"]');
  if (!pptCard) pptCard = await page.$(".template-card");
  assert(pptCard, "No PPT template is available");
  const pptTemplateId = await pptCard.evaluate(node => node.dataset.template);
  await clickHandle(pptCard);
  await page.select("#template-page-count", "5");
  await clickHandle(await page.$("#template-use"));
  const { payload: pptPayload, accepted: pptAccepted, outcome: pptOutcome } = await sendChat(PPT_PROMPT);
  conversationIds.push(new URL(applicationRequests.at(-1).path, "http://local").pathname.split("/").at(-2));
  assert.equal(pptPayload.template_id, pptTemplateId);
  assert.equal(pptPayload.plugin_inputs.page_count, "5");
  const pptReceipts = pptOutcome.messages.filter(item => item.role === "assistant")
    .flatMap(item => item.parts || []).filter(item => item.type === "tool_result");
  const stageCalls = modelCalls.filter(call => call.stream && [
    "create_presentation_plan", "write_presentation_slides", "finalize_presentation",
  ].includes(call.selectedTool)).map(call => call.selectedTool);
  assert.deepEqual(stageCalls, ["create_presentation_plan", "write_presentation_slides", "finalize_presentation"]);
  assert.equal(pptReceipts.length, 3, "PPT 必须存在三个实际工具回执，不能只看模型选中了工具");
  for (const receipt of pptReceipts) assert.equal(receipt.result?.status, "success", JSON.stringify(receipt));
  const pptArtifact = pptReceipts.flatMap(item => item.result?.artifacts || []).find(item => item.name?.endsWith(".pptx"));
  assert(pptArtifact?.id && pptArtifact.download_url, "实际工具回执没有生成 PPTX");
  const artifactSelector = `[data-artifact-id="${pptArtifact.id}"], [data-delivery-id="${pptArtifact.id}"]`;
  const artifactCard = await page.waitForSelector(artifactSelector, { visible: true, timeout: 90000 });
  assert((await artifactCard.evaluate(node => node.textContent)).includes(pptArtifact.name));
  const downloadControl = await artifactCard.$('a[aria-label^="下载"], a[title="下载"]')
    || await visibleByText(artifactCard, "a", "下载");
  const pptFile = await downloadThroughUi(downloadControl, new URL(pptArtifact.download_url, BASE).pathname, ".pptx");
  assertPresentationFile(pptFile, ["ERP 的核心价值", "核心业务闭环", "实施路线图"]);
  await clickHandle(await page.$(".dsh-process > summary"));
  assert.equal(await page.$$eval("[data-dsh-tool-result]", nodes => nodes.length), 3);
  await page.screenshot({ path: path.join(OUTPUT, "ai-ppt-result.png"), fullPage: true });
  pass("AI 对话实际完成大纲、正文、终稿并经浏览器下载核验五页 PPTX", { runId: pptAccepted.run_id, artifact: pptArtifact.name, stages: stageCalls });
  assert(await deleteCurrentConversation());

  phase("文档中心四种真实产物");
  const aiDoc = await createGeneration({
    name: "终验 AI 设备经营简报-" + RUN,
    outputType: "document",
    mode: "ai",
    prompt: "根据财务和设备口径生成一份可供经营复盘的 Word 简报，必须明确结论与复核建议。",
  });
  const aiDocCompleted = await executeGeneration(aiDoc.id);
  assert.equal(aiDocCompleted.outputType, "document");
  assert(modelCalls.some(call => call.stream && call.decision === "document"), "文档引擎没有通过 DSH SSE 返回结构化正文");
  pass("文档中心 AI 模式生成真实 Word 文件", { id: aiDoc.id, artifact: aiDocCompleted.artifact.name });
  await archiveCurrentGeneration(aiDoc.id);

  const pdf = await createGeneration({
    name: "终验设备复核报告-" + RUN,
    outputType: "pdf",
    mode: "manual",
    bodyText: "设备运行率为 98.70%，当前状态正常。\n异常记录应保留原始判断、人工复核和最终处置。",
  });
  const pdfCompleted = await executeGeneration(pdf.id);
  assert.equal(pdfCompleted.outputType, "pdf");
  assert(pdfCompleted.actualPageCount >= 1);
  pass("文档中心人工模式生成可预览 PDF", { id: pdf.id, pages: pdfCompleted.actualPageCount });
  await archiveCurrentGeneration(pdf.id);

  const spreadsheet = await createGeneration({
    name: "终验财务设备数据-" + RUN,
    outputType: "spreadsheet",
    mode: "manual",
    tableText: "月份\t销售收入\t设备运行率\n2026-07\t1180000.25\t97.80\n2026-08\t1280000.50\t98.70",
  });
  const spreadsheetCompleted = await executeGeneration(spreadsheet.id);
  assert.equal(spreadsheetCompleted.outputType, "spreadsheet");
  assert.equal(spreadsheetCompleted.actualPageCount, 1);
  pass("文档中心人工模式生成可预览 Excel", { id: spreadsheet.id, sheets: spreadsheetCompleted.actualPageCount });
  await archiveCurrentGeneration(spreadsheet.id);

  await createManualPresentation();

  await runCompoundInspection();

  phase("文档模板与移动端");
  await visit("/documents?tab=templates", ".document-template-grid");
  assert.equal(await page.$$eval(".document-template-item", nodes => nodes.length), 20);
  assert.equal(await page.$$eval(".document-template-item.selected,[aria-current].document-template-item", nodes => nodes.length), 0);
  await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 1 });
  await visit("/documents?tab=generations", "[data-new-generation]");
  await clickHandle(await page.$("[data-new-generation]"));
  await page.waitForSelector("#platform-dialog[open]", { visible: true, timeout: 30000 });
  const mobile = await page.$eval("#platform-dialog", dialog => {
    const rect = dialog.getBoundingClientRect();
    return {
      left: rect.left,
      right: rect.right,
      viewport: innerWidth,
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    };
  });
  assert(mobile.left >= 0 && mobile.right <= 390 && mobile.overflow <= 1);
  await runAxe("/documents-mobile-dialog", "#platform-dialog");
  await page.screenshot({ path: path.join(OUTPUT, "documents-mobile.png"), fullPage: true });
  await clickHandle(await page.$("#platform-dialog [data-dialog-close]"));
  pass("文档中心 20 套模板无默认选择且移动端新建弹窗可用", mobile);

  phase("浏览器清理");
  await page.setViewport({ width: 1440, height: 960, deviceScaleFactor: 1 });
  assert(await removeHttpSourceThroughUi(page, BASE, SOURCE_NAME));
  pass("浏览器界面已清理终验 HTTP 数据源");

  assert.deepEqual(failures, []);
  assert.deepEqual(accessibilityFindings, []);
  const result = {
    checks,
    failures,
    accessibilityFindings,
    source: { id: source.id, name: SOURCE_NAME },
    conversations: conversationIds,
    generations: generationIds,
    chatRuns,
    compoundEvidence,
    downloadedFiles,
    downloadTransport,
    downloadEvents,
    modelCalls: modelCalls.map(call => ({ stream: call.stream, toolNames: call.toolNames, decision: call.decision, selectedTool: call.selectedTool,
      scenario: call.scenario, continuation: call.continuation, arguments: call.arguments })),
    fixtureCalls: fixtureCalls.map(call => ({ method: call.method, url: call.url })),
    responseFailures,
    applicationRequests,
  };
  fs.writeFileSync(path.join(OUTPUT, "result.json"), JSON.stringify(result, null, 2));
  console.log("RESULT_DIR " + OUTPUT);
  console.log("ACCESSIBILITY_FINDINGS " + accessibilityFindings.length);
}

/** 纯桩校验不启动应用或浏览器，只访问本文件临时创建的本机模型 HTTP 服务。 */
async function selfTest() {
  const names = ["query_business_data", "create_presentation_plan", "write_presentation_slides", "finalize_presentation"];
  const payload = { stream: true, tools: names.map(name => ({ type: "function", function: { name } })),
    messages: [{ role: "user", content: PPT_PROMPT }, { role: "user", content: "本轮可用技能目录" }] };
  const options = { datasourceId: "fixture-source", chartUrl: "http://127.0.0.1/chart.png" };
  const addReceipt = (name, result, blocks = false) => {
    const id = "self-test-" + payload.messages.length;
    payload.messages.push({ role: "assistant", content: null, tool_calls: [{ id, type: "function", function: { name, arguments: "{}" } }] },
      { role: "tool", tool_call_id: id, content: blocks ? [{ type: "text", text: JSON.stringify(result) }] : JSON.stringify(result) });
  };
  assert.equal(modelDecision(payload, options).name, names[1]);
  addReceipt(names[1], { status: "success", data: { draft_id: "real-draft" } }, true);
  assert.equal(modelDecision(payload, options).args.draft_id, "real-draft");
  assert.equal(modelDecision(payload, options).name, names[2]);
  addReceipt(names[2], { status: "success", data: { missing_slide_numbers: [] } });
  assert.equal(modelDecision(payload, options).name, names[3]);
  addReceipt(names[3], { status: "success", artifacts: [{ id: "real-artifact", name: "验收.pptx", download_url: "/api/artifacts/real-artifact/download" }] });
  assert.equal(modelDecision(payload, options).kind, "presentation");
  payload.messages.push({ role: "user", content: [{ type: "text", text: PPT_PROMPT }] }, { role: "user", content: "新轮技能目录" });
  assert.equal(modelDecision(payload, options).name, names[1], "旧轮回执不能令新轮跳过大纲");
  assert.throws(() => modelDecision({ ...payload, tools: [] }, options), /没有提供所需业务工具/);
  addReceipt(names[1], { status: "failed", error_summary: "真实工具失败" });
  assert.throws(() => modelDecision(payload, options), /实际工具失败/);
  const analysis = { ...payload, messages: [{ role: "user", content: CHAT_PROMPT }] };
  assert.equal(modelDecision(analysis, options).name, "query_business_data");
  analysis.messages.push({ role: "assistant", tool_calls: [{ id: "read", function: { name: "query_business_data" } }] },
    { role: "tool", tool_call_id: "read", content: JSON.stringify({ status: "success", data: { data: [{ revenue: "200000", deviceRate: "91.25" }] } }) });
  assert.match(modelDecision(analysis, options).content, /20\.00 万元/);
  assert.match(modelDecision(analysis, options).content, /91\.25%/);
  assert.throws(() => zipEntries(Buffer.from("这不是一个真实 PPTX 文件")), /不是完整 ZIP/);
  await startFixture();
  try {
    const response = await fetch(`http://127.0.0.1:${fixture.address().port}/v1/chat/completions`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stream: true, messages: [{ role: "system", content: "你是企业文档编写引擎。" }] }),
    });
    assert.equal(response.status, 200);
    assert.match(response.headers.get("content-type"), /text\/event-stream/);
    const stream = await response.text();
    const chunks = stream.split("\n").filter(line => line.startsWith("data: ") && line !== "data: [DONE]")
      .map(line => JSON.parse(line.slice(6)));
    const content = chunks.map(chunk => chunk.choices[0].delta.content || "").join("");
    assert.deepEqual(JSON.parse(content), aiDocumentSpec());
    assert(stream.endsWith("data: [DONE]\n\n"));
    assert.deepEqual(failures, []);
  } finally { await new Promise(resolve => fixture.close(resolve)); fixture = null; }
  console.log("PASS 纯桩合同：多工具阶段推进、真实回执、跨轮隔离、失败拒绝、查询值引用、SSE 文档 JSON");
}

if (require.main === module) (process.argv.includes("--self-test") ? selfTest() : main()).catch(async error => {
  failures.push(error.stack || error.message);
  console.error(error.stack || error.message);
  if (page && !page.isClosed()) await page.screenshot({ path: path.join(OUTPUT, "failure.png"), fullPage: true }).catch(() => {});
  if (fs.existsSync(OUTPUT)) fs.writeFileSync(path.join(OUTPUT, "result.json"), JSON.stringify({ checks, failures, accessibilityFindings,
    applicationRequests, chatRuns, compoundEvidence, downloadedFiles, downloadTransport, downloadEvents, responseFailures, modelCalls, fixtureCalls }, null, 2));
  process.exitCode = 1;
}).finally(async () => {
  if (browser) await browser.close();
  restoreTransportObserver?.();
  if (fixture) await new Promise(resolve => fixture.close(resolve));
});

module.exports = { modelDecision, compoundDecision, assertMissingThirteen, inspectionSlides, zipEntries, assertPresentationFile,
  assertIncompleteRun, completedDownload, verifyDownloadedBytes, COMPOUND_PROMPT, COMPOUND_CONTINUE, INSPECTION_ROWS };
