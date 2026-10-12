/**
 * 模型与用量模块（路由 /system/models?tab=profiles|marketplace|usage）
 *
 * 位置：三个标签页——模型配置（模型角色列表、接入与测试）、模型市场（接入模板）、用量与成本（每日 Token 趋势、
 *   按模型汇总、工作区预算与超额策略）。模型配置是所有 AI 能力（对话、知识问答、抽取、文档生成）的底座。
 * 依赖接口前缀：/api/models（GET/POST/PUT/DELETE /profiles、POST /probe/{profile} 能力测试、
 *   GET /marketplace、GET /usage 与 /usage/export、PUT /cost-policy 预算）。
 * 与 iframe 内 Vue 应用无关。
 *
 * 关键约束：
 * - 模型角色名（profile_name）是引用契约，创建后固定为只读：其他模块按角色名取模型，改名会让引用失效。
 * - API Key 只提交、不回显：留空表示保持原密钥；密钥存入加密凭据库
 *   （表单说明必须保留，避免管理员误以为需要重复粘贴）。
 * - “停用”（DELETE /profiles/{id}）只在数据库来源的配置上出现（item.id 存在），
 *   部署环境提供的配置不能从此处停用；停用会撤销专用密钥，属破坏性操作，走统一确认弹窗。
 * - 计价（pricing）缺失时列表必须显示“未配置价格”，用量页要把“未计价调用”单独统计——不能当成 0 成本。
 * - 费用按调用时保存的价格快照计算（后端口径），前端不做换算或重新计算。
 * - 预算策略：hard_limit 表示“超额后阻止调用”，是影响线上可用性的开关，文案需明确；
 *   预算保存带 version；无 models.edit 时表单整体禁用。
 * - 权限码：models.create/edit/delete/execute/export 必须与后端目录一致。
 */

import { api } from "./api.js";
import {
  $, $$, can, esc, date, pageHeader, button, dataTable, emptyState,
  statusBadge, field, input, select, openDialog, confirmAction, toast,
} from "./ui.js";

const BASE = "/api/models";
// 中文说明：标签页定义（键名即 tab 参数，需与 selectedTab 校验、renderModels 分支一致）。
const TABS = [["profiles", "模型配置"], ["marketplace", "模型市场"], ["usage", "用量与成本"]];
// 中文说明：多值输入解析（换行或逗号分隔），用于能力标签。
const splitLines = value => String(value || "").split(/[\r\n,]+/).map(item => item.trim()).filter(Boolean);

// 中文说明：读取当前标签页；非法值回落 profiles，避免手改 URL 出现空白页。
function selectedTab() {
  const value = new URLSearchParams(location.search).get("tab") || "profiles";
  return TABS.some(([key]) => key === value) ? value : "profiles";
}

function tabs(active) {
  return `<nav class="system-tool-tabs" aria-label="模型与用量功能">${TABS.map(([key, label]) =>
    `<a href="/system/models?tab=${key}" ${active === key ? 'aria-current="page"' : ""}>${esc(label)}</a>`).join("")}</nav>`;
}

// 中文说明：紧凑状态条（各标签页顶部共用，tone 控制颜色语义）。
function compactStatus(items) {
  return `<div class="system-status-strip governance-status-strip">${items.map(item =>
    `<span class="${esc(item.tone || "")}"><i></i><b>${esc(item.label)}</b>${esc(item.value)}</span>`).join("")}</div>`;
}

// 中文说明：计价展示：未配置价格必须显式提示（未计价调用不能算作 0 成本），已配置则显示“输入/输出单价 · 每百万 Token”。
function pricingLabel(pricing) {
  if (!pricing?.configured) return '<span class="unpriced-label">未配置价格</span>';
  const currency = esc(pricing.currency || "CNY");
  return `<span>${currency} ${esc(pricing.input_price_per_million ?? 0)} / ${esc(pricing.output_price_per_million ?? 0)}</span><span class="cell-secondary">输入 / 输出 · 每百万 Token</span>`;
}

/** 仅覆盖表单暴露的运行参数，其余兼容选项与模态声明完整保留。 */
function runtimeOptions(form, previous = {}) {
  const value = { ...previous };
  for (const [key, choices] of Object.entries({ protocol: ["deepseek-messages", "openai-completions"],
    thinking: ["enabled", "disabled"], reasoning_effort: ["off", "minimal", "low", "medium", "high", "xhigh", "max"] })) {
    const option = String(form.get(key) || "");
    if (!option) delete value[key];
    else if (choices.includes(option)) value[key] = option;
    else throw new Error("模型运行参数选项无效");
  }
  for (const [key, minimum, maximum] of [["context_window", 4096, 4194304], ["max_output_tokens", 128, 1048576]]) {
    const raw = String(form.get(key) || "").trim();
    if (!raw) { delete value[key]; continue; }
    const number = Number(raw);
    if (!Number.isSafeInteger(number) || number < minimum || number > maximum) throw new Error("模型 Token 预算超出允许范围");
    value[key] = number;
  }
  if (value.context_window && value.max_output_tokens && value.context_window <= value.max_output_tokens + 1024) throw new Error("上下文窗口必须大于输出上限加 1024 Token");
  if (value.thinking === "disabled" && value.reasoning_effort && value.reasoning_effort !== "off") throw new Error("关闭思考时，推理等级应选关闭或部署默认");
  const protocol = value.protocol || (new URL(String(form.get("endpoint"))).hostname === "api.deepseek.com" ? "deepseek-messages" : "openai-completions");
  if (protocol === "deepseek-messages" && value.reasoning_effort && !["off", "low", "high", "max"].includes(value.reasoning_effort)) throw new Error("DeepSeek Messages 的推理等级仅支持关闭、低、高、最高");
  return value;
}

/**
 * 中文说明：模型配置表单，三种用途：新增（record/template 均为空）、修改（record）、按市场模板接入（template）。
 * 参数：root 页面容器；record=已有配置；template=模型市场模板（预填角色名与建议值）。
 * 副作用：POST /profiles 或 PUT /profiles/{profile_name}；保存后重绘页面。
 * 约束：模型角色名在修改/模板接入时只读（角色名是跨模块引用契约）；API Key 留空表示保持原密钥；无价格时提交 null。
 */
function profileDialog(root, record = null, template = null) {
  const item = record || template || {};
  const pricing = item.pricing || {};
  const runtime = item.runtime_options || {};
  const fixedName = Boolean(record || template);
  const title = record ? "修改模型配置" : template ? "接入模型模板" : "新增模型配置";
  openDialog(title, `<div class="form-grid">
    ${field("模型角色", input("profile_name", item.profile_name || "", `required maxlength="100" pattern="[A-Za-z][A-Za-z0-9_\\-]+" ${fixedName ? "readonly" : ""}`), "例如 reasoning、embedding、vision、rerank")}
    ${field("供应商", input("provider", item.provider || "openai-compatible", 'required maxlength="64"'))}
    ${field("模型名称", input("model", item.model || "", 'required maxlength="200"'))}
    ${field("API 端点", input("endpoint", item.endpoint || "", 'type="url" required maxlength="500"'), "填写兼容接口的基础地址。", true)}
    ${field("API Key", input("api_key", "", 'type="password" maxlength="500" autocomplete="new-password"'), item.api_key_configured ? "已安全保存；留空保持原密钥。" : "密钥只写入加密凭据库，不在页面回显。", true)}
    ${field("能力标签", `<textarea name="capabilities" required>${esc((item.capabilities || []).join("\n"))}</textarea>`, "每行一个，例如 chat、tools、vision、embedding。", true)}
    <h3 class="form-section wide">对话运行参数</h3>
    ${field("协议", select("protocol", [{ value: "", label: "部署默认" }, { value: "deepseek-messages", label: "DeepSeek Messages" }, { value: "openai-completions", label: "OpenAI 兼容" }], runtime.protocol || "", null))}
    ${field("思考模式", select("thinking", [{ value: "", label: "部署默认" }, { value: "enabled", label: "启用" }, { value: "disabled", label: "关闭" }], runtime.thinking || "", null))}
    ${field("推理等级", select("reasoning_effort", [["", "部署默认"], ["off", "关闭"], ["minimal", "最低"], ["low", "低"], ["medium", "中"], ["high", "高"], ["xhigh", "很高"], ["max", "最高"]].map(([value, label]) => ({ value, label })), runtime.reasoning_effort || "", null), "DeepSeek Messages 支持关闭、低、高、最高。")}
    ${field("上下文窗口 / Token", input("context_window", runtime.context_window ?? "", 'type="number" min="4096" max="4194304" step="1" placeholder="部署默认"'))}
    ${field("最大输出 / Token", input("max_output_tokens", runtime.max_output_tokens ?? "", 'type="number" min="128" max="1048576" step="1" placeholder="部署默认"'), "输出上限应至少为输入预留 1024 Token。")}
    <h3 class="form-section wide">计价快照</h3>
    ${field("输入单价 / 百万 Token", input("input_price", pricing.input_price_per_million ?? "", 'type="number" min="0" max="1000000" step="0.000001"'))}
    ${field("输出单价 / 百万 Token", input("output_price", pricing.output_price_per_million ?? "", 'type="number" min="0" max="1000000" step="0.000001"'))}
    ${field("币种", select("currency", [{ value: "CNY", label: "CNY" }, { value: "USD", label: "USD" }, { value: "EUR", label: "EUR" }, { value: "JPY", label: "JPY" }], pricing.currency || "CNY", null))}
    ${field("状态", `<label class="governance-check"><input type="checkbox" name="enabled" ${item.enabled === false ? "" : "checked"}><span>启用该模型角色</span></label>`)}
  </div>`, {
    wide: true,
    onSave: async form => {
      const inputPrice = form.get("input_price");
      const outputPrice = form.get("output_price");
      const payload = {
        provider: form.get("provider"), model: form.get("model"), endpoint: form.get("endpoint"),
        api_key: form.get("api_key") || "", capabilities: splitLines(form.get("capabilities")),
        runtime_options: runtimeOptions(form, runtime),
        enabled: form.has("enabled"), pricing: inputPrice === "" && outputPrice === "" ? null : {
          input_price_per_million: inputPrice === "" ? null : Number(inputPrice),
          output_price_per_million: outputPrice === "" ? null : Number(outputPrice),
          currency: form.get("currency"),
        },
      };
      const profileName = form.get("profile_name");
      if (record) await api.put(`${BASE}/profiles/${encodeURIComponent(profileName)}`, payload);
      else await api.post(`${BASE}/profiles`, { profile_name: profileName, ...payload });
      toast("模型配置已保存"); await renderModels(root);
    },
  });
}

// 中文说明：模型配置列表（profiles 标签）。行内动作：测试（models.execute，真实调用一次模型）、配置（models.edit）、
// 停用（models.delete，仅对数据库来源配置可用，走统一确认弹窗且会撤销专用密钥）。
async function renderProfiles(root) {
  const result = await api.get(`${BASE}/profiles`);
  const items = result.items;
  root.insertAdjacentHTML("beforeend", compactStatus([
    { label: "角色", value: items.length },
    { label: "数据库配置", value: items.filter(item => item.source === "database").length, tone: "healthy" },
    { label: "密钥就绪", value: items.filter(item => item.api_key_configured).length },
    { label: "价格已配", value: items.filter(item => item.pricing?.configured).length },
  ]) + `<section class="panel governance-panel"><div class="system-panel-actions">${can("models.create") ? button("新增模型", "data-new-model", true) : ""}</div>
    ${items.length ? dataTable(["模型角色", "供应商 / 模型", "能力", "计价", "密钥", "最近测试", "操作"], items, item => `<tr>
      <td><strong>${esc(item.profile_name)}</strong><span class="cell-secondary">${item.source === "database" ? "工作区配置" : "部署环境"}</span></td>
      <td>${esc(item.provider)}<span class="cell-secondary">${esc(item.model || "未填写模型")}</span></td>
      <td class="governance-wrap-cell">${(item.capabilities || []).map(value => `<span class="capability-rule">${esc(value)}</span>`).join("") || "—"}</td>
      <td>${pricingLabel(item.pricing)}</td><td>${statusBadge(item.api_key_configured)}</td>
      <td>${item.last_probe ? `${statusBadge(item.last_probe.status)}<span class="cell-secondary">${date(item.last_probe.created_at)}</span>` : "未测试"}</td>
      <td><div class="row-actions">
        ${can("models.execute") ? `<button class="text-button" data-probe-model="${esc(item.profile_name)}">测试</button>` : ""}
        ${can("models.edit") ? `<button class="text-button" data-edit-model="${esc(item.profile_name)}">配置</button>` : ""}
        ${can("models.delete") && item.id ? `<button class="text-button danger" data-delete-model="${item.id}">停用</button>` : ""}
      </div></td></tr>`) : emptyState("尚无模型角色", "新增推理、向量、视觉或重排模型配置。")}</section>`);
  $("[data-new-model]", root)?.addEventListener("click", () => profileDialog(root));
  $$('[data-edit-model]', root).forEach(element => element.addEventListener("click", () =>
    profileDialog(root, items.find(item => item.profile_name === element.dataset.editModel))));
  $$('[data-probe-model]', root).forEach(element => element.addEventListener("click", async () => {
    element.disabled = true;
    try {
      const result = await api.post(`${BASE}/probe/${encodeURIComponent(element.dataset.probeModel)}`, {});
      // 能力测试会产生一次真实模型调用（计入用量），因此按钮期间禁用并禁止并发点击
      toast(result.status === "passed" ? "模型能力测试通过" : "模型能力测试未通过", result.status !== "passed");
      await renderModels(root);
    } catch (error) { toast(error.message, true); }
    finally { element.disabled = false; }
  }));
  $$('[data-delete-model]', root).forEach(element => element.addEventListener("click", () => {
    const record = items.find(item => item.id === element.dataset.deleteModel);
    confirmAction("停用模型配置", `停用“${record.profile_name}”并撤销它的专用密钥？`, async () => {
      await api.delete(`${BASE}/profiles/${record.id}`); toast("模型配置已停用"); await renderModels(root);
    }, "停用");
  }));
}

// 中文说明：模型市场（marketplace 标签）：内置接入模板与建议供应商/模型、计价。
// 模板只用于预填表单，实际接入仍需管理员确认端点与密钥（配置接入会打开 profileDialog）。
async function renderMarketplace(root) {
  const result = await api.get(`${BASE}/marketplace`);
  const items = result.items;
  root.insertAdjacentHTML("beforeend", compactStatus([
    { label: "接入模板", value: items.length },
    { label: "推理", value: items.filter(item => item.capabilities.includes("reasoning")).length },
    { label: "多模态", value: items.filter(item => item.capabilities.includes("vision")).length },
    { label: "检索", value: items.filter(item => item.capabilities.some(value => ["embedding", "rerank"].includes(value))).length },
  ]) + `<section class="panel governance-panel">${dataTable(["用途", "角色", "建议供应商 / 模型", "能力", "价格", "操作"], items, item => `<tr>
    <td><strong>${esc(item.name)}</strong><span class="cell-secondary governance-description">${esc(item.description)}</span></td>
    <td><code>${esc(item.profile_name)}</code></td><td>${esc(item.provider)}<span class="cell-secondary">${esc(item.model || "管理员选择")}</span></td>
    <td class="governance-wrap-cell">${item.capabilities.map(value => `<span class="capability-rule">${esc(value)}</span>`).join("")}</td>
    <td>${pricingLabel(item.pricing)}</td><td>${can("models.create") ? `<button class="text-button" data-use-template="${esc(item.id)}">配置接入</button>` : "只读"}</td>
  </tr>`)}</section>`);
  $$('[data-use-template]', root).forEach(element => element.addEventListener("click", () =>
    profileDialog(root, null, items.find(item => item.id === element.dataset.useTemplate))));
}

// 中文说明：多币种费用展示：不同币种不能相加，用“ / ”并列显示，前端不做汇率换算。
function currencyCosts(costs = {}) {
  const entries = Object.entries(costs);
  return entries.length ? entries.map(([currency, value]) => `${currency} ${Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 6 })}`).join(" / ") : "0";
}

// 中文说明：每日 Token 柱状图（纯 CSS 高度百分比）。aria-label 列出全部数值以支持读屏；
// 零值仍保留 1% 高度用于表达“该日有记录但为 0”。
function usageChart(daily) {
  const max = Math.max(1, ...daily.map(item => item.total_tokens));
  return `<div class="usage-chart" role="img" aria-label="每日模型 Token 用量：${esc(daily.map(item => `${item.date} ${item.total_tokens}`).join("；"))}">
    ${daily.map(item => `<div class="usage-column" title="${esc(item.date)} · ${item.total_tokens} Token · ${item.calls} 次">
      <span class="usage-bar" style="height:${Math.max(item.total_tokens ? 4 : 1, item.total_tokens / max * 100)}%"></span>
      <small>${esc(item.date.slice(5))}</small>
    </div>`).join("")}
  </div>`;
}

// 中文说明：预算表单。无 models.edit 时全部控件禁用（只读展示）；月度预算留空表示不限制；
// hard_limit 勾选即“超额后阻止模型调用”，会直接影响线上可用性。
function budgetForm(policy) {
  const readonly = !can("models.edit");
  return `<form class="governance-budget" data-cost-policy>
    <label><span>月度预算</span><input name="monthly_budget" type="number" min="0" max="1000000000" step="0.01" value="${esc(policy.monthly_budget ?? "")}" placeholder="不限制" ${readonly ? "disabled" : ""}></label>
    <label><span>币种</span>${select("currency", [{ value: "CNY", label: "CNY" }, { value: "USD", label: "USD" }, { value: "EUR", label: "EUR" }, { value: "JPY", label: "JPY" }], policy.currency, null).replace("<select", `<select ${readonly ? "disabled" : ""}`)}</label>
    <label><span>提醒阈值</span><input name="warn_percent" type="number" min="1" max="100" value="${policy.warn_percent}" required ${readonly ? "disabled" : ""}></label>
    <label class="governance-check governance-budget-check"><input type="checkbox" name="hard_limit" ${policy.hard_limit ? "checked" : ""} ${readonly ? "disabled" : ""}><span>超额后阻止调用</span></label>
    ${readonly ? "" : '<button class="button primary" type="submit">保存预算</button>'}
  </form>`;
}

// 中文说明：用量与成本视图：日期区间/角色筛选、每日 Token 趋势、按模型汇总与工作区预算。
// 口径说明：未计价调用（unpriced_calls/tokens）单独统计——没有价格快照的调用不能算作零成本。
// 副作用：GET /usage（只读）；导出 CSV 走 /usage/export（需 models.export）；保存预算走 PUT /cost-policy（带 version）。
async function renderUsage(root) {
  const query = new URLSearchParams(location.search);
  const params = new URLSearchParams();
  if (query.get("start")) params.set("start", query.get("start"));
  if (query.get("end")) params.set("end", query.get("end"));
  if (query.get("profile_name")) params.set("profile_name", query.get("profile_name"));
  const summary = await api.get(`${BASE}/usage${params.size ? `?${params}` : ""}`);
  const totals = summary.totals;
  const policy = summary.budget;
  root.insertAdjacentHTML("beforeend", compactStatus([
    { label: "调用", value: totals.calls },
    { label: "Token", value: totals.total_tokens.toLocaleString("zh-CN") },
    { label: "已计费用", value: currencyCosts(totals.costs), tone: "healthy" },
    { label: "未计价", value: `${totals.unpriced_calls} 次 / ${totals.unpriced_tokens.toLocaleString("zh-CN")} Token`, tone: totals.unpriced_calls ? "warning" : "" },
  ]) + `<section class="panel governance-panel">
    <form class="list-toolbar governance-usage-filter" data-usage-filter>
      <label class="form-field governance-inline-field"><span>开始日期</span><input type="date" name="start" value="${esc(summary.range.start)}"></label>
      <label class="form-field governance-inline-field"><span>结束日期</span><input type="date" name="end" value="${esc(summary.range.end)}"></label>
      <label class="form-field governance-inline-field"><span>模型角色</span><input name="profile_name" value="${esc(query.get("profile_name") || "")}" placeholder="全部"></label>
      <button class="button" type="submit">查询</button>${can("models.export") ? '<button class="button" type="button" data-export-usage>导出 CSV</button>' : ""}
    </form>
    <div class="governance-usage-layout">
      <section class="usage-trend"><div class="governance-section-heading"><strong>每日 Token</strong><span>${esc(summary.range.start)} 至 ${esc(summary.range.end)}</span></div>${usageChart(summary.daily)}</section>
      <section class="budget-panel"><div class="governance-section-heading"><strong>本月预算</strong><span>${policy.monthly_budget == null ? "未设置额度" : `${policy.currency} ${policy.month_spend} / ${policy.monthly_budget}`}</span></div>
        <div class="budget-progress" role="progressbar" aria-label="预算使用比例" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.min(100, policy.usage_percent || 0)}"><span style="width:${Math.min(100, policy.usage_percent || 0)}%"></span></div>${budgetForm(policy)}
      </section>
    </div>
    <div class="governance-subtable"><div class="governance-section-heading"><strong>按模型汇总</strong><span>费用按调用时保存的价格快照计算</span></div>
      ${summary.models.length ? dataTable(["角色 / 模型", "能力", "调用", "Token", "成功计价", "未计价", "费用"], summary.models, item => `<tr>
        <td><strong>${esc(item.profile_name)}</strong><span class="cell-secondary">${esc(item.provider)} / ${esc(item.model)}</span></td><td>${esc(item.capability)}</td>
        <td>${item.calls}</td><td>${item.total_tokens.toLocaleString("zh-CN")}</td><td>${item.priced_calls}</td><td>${item.unpriced_calls}</td><td>${currencyCosts(item.costs)}</td>
      </tr>`) : emptyState("查询范围内没有模型调用", "执行 AI 分析、知识检索或模型测试后，用量会自动进入账本。")}
    </div>
  </section>`);
  $("[data-usage-filter]", root).addEventListener("submit", event => {
    event.preventDefault(); const form = new FormData(event.currentTarget); const next = new URLSearchParams({ tab: "usage" });
    for (const name of ["start", "end", "profile_name"]) if (form.get(name)) next.set(name, form.get(name));
    location.assign(`/system/models?${next}`);
  });
  $("[data-export-usage]", root)?.addEventListener("click", async () => {
    try { await api.download(`${BASE}/usage/export${params.size ? `?${params}` : ""}`, "model-usage.csv"); toast("模型用量已导出"); }
    catch (error) { toast(error.message, true); }
  });
  $("[data-cost-policy]", root)?.addEventListener("submit", async event => {
    event.preventDefault(); const form = new FormData(event.currentTarget); const submit = $('[type="submit"]', event.currentTarget); submit.disabled = true;
    try {
      await api.put(`${BASE}/cost-policy`, {
        version: policy.version, monthly_budget: form.get("monthly_budget") === "" ? null : Number(form.get("monthly_budget")),
        currency: form.get("currency"), warn_percent: Number(form.get("warn_percent")), hard_limit: form.has("hard_limit"),
      });
      toast("模型预算已保存"); await renderModels(root);
    } catch (error) { toast(error.message, true); }
    finally { submit.disabled = false; }
  });
}

/**
 * 中文说明：模型与用量页入口：按 ?tab 渲染模型配置 / 模型市场 / 用量与成本（默认模型配置）。
 * 副作用：先重写页头与标签页，再由子视图追加内容（子视图用 insertAdjacentHTML 追加，不要覆盖 root）；
 *   读取为主，配置/预算保存与模型测试为写操作。异常由 main.js 统一渲染错误页。
 */
export async function renderModels(root) {
  const active = selectedTab();
  root.innerHTML = pageHeader("模型与用量", "配置模型角色、连接能力、真实计价与工作区预算。") + tabs(active);
  if (active === "marketplace") await renderMarketplace(root);
  else if (active === "usage") await renderUsage(root);
  else await renderProfiles(root);
}
