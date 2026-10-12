/*
 * 中文说明（对话滚动与布局的离线夹具用例）：
 * 验证行为：用真实外壳、样式、输入框高度与滚动控制器（从 index.html 与 app.js 截取）验证
 *   底部跟随与输入区尺寸行为。
 * 运行方式：直接 node 运行离线夹具，无需登录、不产生消息、不调用模型与业务查询。
 * 边界：仅布局/滚动断言；不覆盖后端行为。
 */
"use strict";

// Offline fixture uses the real shell, styles, input sizing and scroll controller.
// No authentication, business queries, messages or model requests are issued.
const fs = require("node:fs");
const path = require("node:path");
const root = path.resolve(__dirname, "../..");

function fixtureHtml() {
  const source = fs.readFileSync(path.join(root, "apps/web/app.js"), "utf8");
  const index = fs.readFileSync(path.join(root, "apps/web/index.html"), "utf8");
  const shell = index.slice(index.indexOf('  <main id="pages"'), index.indexOf('<div id="overlay"'));
  const css = ["app.css", "assets/platform/business-chat.css", "assets/platform/ai-workbench.css"]
    .map(file => fs.readFileSync(path.join(root, "apps/web", file), "utf8")).join("\n");
  const scrolling = source.slice(source.indexOf("function scrollToBottom("), source.indexOf("// ---------------- 附件"));
  const sizing = source.slice(source.indexOf("function updateSendState("), source.indexOf("// ---------------- 发送"));
  return `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>聊天底部布局验收</title>
    <meta name="viewport" content="width=device-width,initial-scale=1"><style>${css}</style>
    <body class="enterprise-ai-workbench sidebar-collapsed"><div id="app">${shell}</div>
    <script>(() => { const $ = s => document.querySelector(s); const state = {streaming: false, fileIds: []};
    // 此夹具只验证几何，模型下拉框的网络状态由独立对话验收覆盖。
    const renderChatModels = () => {};
    ${scrolling}\n${sizing}\n(${installProbe.toString()})(); })();</script></body></html>`;
}

function installProbe() {
  const root = document.querySelector("#scroll-root"), messages = document.querySelector("#messages");
  const composer = document.querySelector(".composer-wrap"), textarea = document.querySelector("#prompt-textarea");
  document.querySelector(".chat-page").classList.remove("is-empty");
  document.querySelector("#composer-form").addEventListener("submit", event => event.preventDefault());
  textarea.addEventListener("input", updateSendState);
  const settle = async () => { for (let i = 0; i < 5; i++) await new Promise(requestAnimationFrame); };
  const checks = [];
  const assert = (value, label) => { if (!value) throw Error(label); checks.push(label); };
  const visible = label => {
    const last = messages.lastElementChild.getBoundingClientRect();
    assert(root.getBoundingClientRect().bottom <= composer.getBoundingClientRect().top + 1, label + ": 消息区与输入区不重叠");
    assert(last.bottom <= root.getBoundingClientRect().bottom + 1 && last.top >= root.getBoundingClientRect().top,
      label + ": 最后一条内容完整可见");
    assert(document.documentElement.scrollWidth <= innerWidth + 1, label + ": 无横向溢出");
  };
  const status = document.createElement("output"); status.id = "layout-check-result";
  document.querySelector(".chat-topbar").replaceChildren(status);
  window.runChatLayoutChecks = async () => {
    checks.length = 0;
    messages.innerHTML = Array.from({length: 35}, (_, i) => '<div class="msg-row"><div class="msg-body">历史消息 ' + i + '：用于检查对话滚动位置。</div></div>').join("");
    const append = html => { const row = document.createElement("div"); row.className = "msg-row"; row.innerHTML = html; messages.appendChild(row); };
    append('<div class="tool-chip">正在生成 PPT</div>');
    scrollToBottom(); await settle(); visible("普通输入框");
    document.querySelector("#preset-plugins").classList.remove("hidden");
    document.querySelector("#preset-chips").innerHTML = '<button class="preset-chip">PPT 生成 · 现代水墨</button>';
    document.querySelector("#composer-attachments").innerHTML = '<span class="attach-chip">数据源 · 本机测试</span><span class="attach-chip">插件 · PPT 生成</span><span class="attach-chip">测试附件 · 报表.csv</span>';
    await settle(); visible("PPT 模板及换行标签");
    textarea.value = Array(6).fill("多行输入布局测试").join("\n"); updateSendState();
    await settle(); visible("输入框增高");
    append('<div class="artifact-card">PPT 已生成 · 下载</div>'); await settle(); visible("异步成品卡片");
    append('<div class="followup-questions"><button class="followup-question">按部门查看付款情况 →</button></div>');
    await settle(); visible("后续问题按钮");
    root.scrollTop = 0; await settle();
    textarea.value = ""; updateSendState(); await settle();
    append('<div class="tool-chip">新增状态</div>'); await settle();
    assert(root.scrollTop < 5, "阅读历史时布局变化不强制跳到底部");
    scrollToBottom(); await settle(); visible("返回底部");
    status.textContent = "PASS · " + checks.length + " 项布局检查";
    return {width: innerWidth, height: innerHeight, checks};
  };
  setupChatScrolling();
}

module.exports = { fixtureHtml };
