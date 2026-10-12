/**
 * 数据与资料目录模块（路由 /assets-catalog?tab=catalog|terms|metrics|relations）
 *
 * 位置：四个视图——资源目录（按“资源类型 → 业务域 → 资源”懒加载的目录树、资源详情与字段快照）、
 *   业务术语、指标定义、资源关系。是平台内所有资源的统一索引入口，供权限授权、文档/PPT 业务来源等复用。
 * 依赖接口前缀：/api/v1/assets（/types、/catalog 列表与详情、/catalog/{type}/{id}/annotation 目录信息、
 *   /terms、/metrics、/relations 及各自的归档接口）；
 *   需要用户候选时读 /api/v1/system/users（需 system.users.view，无权限时只显示本人）。
 * 与 iframe 内 Vue 应用无关。
 *
 * 关键约束：
 * - 目录树必须 lazy-load：按类型展开时才拉取该类型资源（loadResourceCatalog 分页 200/页，最多 500 页），
 *   避免进入页面即拉全量资源目录。展开状态按工作区隔离存在 sessionStorage。
 * - 资源可见性由后端按权限计算：列表只返回当前账号有权使用的资源；item.authorizedActions 决定是否显示“编辑目录信息”。
 * - 目录信息（别名、分类、敏感级别、责任人、时效、字段说明）是本体之外的业务元数据，
 *   写操作走 PUT /catalog/{type}/{id}/annotation，带 version 做并发校验。
 * - 敏感级别（public/internal/confidential/restricted）与时效状态是治理标签，必须原样展示，前端不得降级显示。
 * - 术语/指标/关系都是“归档”而非物理删除，归档走统一确认弹窗；指标公式与来源字段是报表口径，不要在本地计算。
 * - 字段快照上限 5000 个（truncated 标记），页面需提示“完整结构请在数据源模块查看”。
 * - 权限码：assets.create/edit/delete/view 必须与后端目录一致。
 */

import { api } from "./api.js";
import {
  $, $$, state, can, esc, date, pageHeader, button, dataTable, emptyState,
  statusBadge, field, input, select, openDialog, confirmAction, toast,
} from "./ui.js";
import {
  UNASSIGNED_RESOURCE_GROUP, groupResourceItems, loadResourceCatalog,
  mergeResourceItems, resourceSelectOptions,
} from "./resource-hierarchy.js";

const BASE = "/api/v1/assets";
const textarea = (name, value = "", attributes = "") => `<textarea name="${esc(name)}" ${attributes}>${esc(value)}</textarea>`;

// 中文说明：本模块接口返回 { success, data } 包装，统一解包。
async function request(method, path, body) {
  const result = await api[method](path, body);
  return result && result.success === true && "data" in result ? result.data : result;
}
const get = path => request("get", path);
const post = (path, body) => request("post", path, body);
const put = (path, body) => request("put", path, body);
const remove = path => request("delete", path);
const params = () => new URLSearchParams(location.search);
// 中文说明：生成“返回资源列表”链接所需的查询串。只保留 keyword/type/offset 三个字段，
// 且固定 tab=catalog；用于从资源详情返回时恢复原筛选位置。
function catalogListQuery(source = params()) {
  const query = new URLSearchParams({ tab: "catalog" });
  for (const key of ["keyword", "type", "offset"]) {
    if (source.get(key)) query.set(key, source.get(key));
  }
  return query.toString();
}
// 中文说明：多值输入解析（逗号/中文逗号分隔并去重），用于别名、标签、维度等字段。
const split = value => [...new Set(String(value || "").split(/[，,]/).map(item => item.trim()).filter(Boolean))];
const pretty = value => JSON.stringify(value || {}, null, 2);
// 中文说明：解析表单 JSON 文本框：空值回落 fallback，非法 JSON 抛中文错误由弹窗展示。
const parseJson = (value, label, fallback = {}) => {
  const text = String(value || "").trim();
  if (!text) return fallback;
  try { return JSON.parse(text); } catch { throw new Error(label + "不是有效 JSON"); }
};

// 中文说明：四个视图的标签页定义（键名即 URL 的 tab 值，需与 renderAssetCatalog 的分支一致）。
const tabDefinitions = [
  ["catalog", "资源"], ["terms", "业务术语"], ["metrics", "指标"], ["relations", "资源关系"],
];

function tabBar(active) {
  return `<nav class="catalog-tabs" aria-label="数据与资料目录视图">${tabDefinitions.map(([key, label]) =>
    `<a href="/assets-catalog?tab=${key}" ${key === active ? 'aria-current="page"' : ""}>${label}</a>`).join("")}</nav>`;
}

function page(root, active, title, description, actions, content) {
  root.innerHTML = pageHeader(title, description, actions) + tabBar(active) + content;
}

function navigate(values) {
  const query = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== "" && value !== null && value !== undefined) query.set(key, value);
  });
  location.assign("/assets-catalog?" + query.toString());
}

// 中文说明：该模块分页用 limit/offset（不是 page/page_size），与 /api/v1/assets 各列表接口约定一致。
function pagination(result, tab, filters = {}) {
  if (!result.total) return "";
  const start = result.offset + 1;
  const end = Math.min(result.total, result.offset + result.items.length);
  return `<div class="pagination"><span>${start}–${end} / ${result.total}</span><div>
    <button class="button small" type="button" data-catalog-page="${Math.max(0, result.offset - result.limit)}" ${result.offset === 0 ? "disabled" : ""}>上一页</button>
    <button class="button small" type="button" data-catalog-page="${result.offset + result.limit}" ${end >= result.total ? "disabled" : ""}>下一页</button>
  </div></div>`;
}

function wirePagination(root, tab, filters) {
  $$('[data-catalog-page]', root).forEach(element => element.addEventListener("click", () =>
    navigate({ tab, ...filters, offset: element.dataset.catalogPage })));
}

// 中文说明：业务元数据枚举的中文映射（与后端枚举一一对应，不要翻译键名）。
const freshnessLabels = {
  current: "当前", stale: "已过期", unknown: "未知", not_configured: "未设时效",
};
const sensitivityLabels = {
  public: "公开", internal: "内部", confidential: "机密", restricted: "严格受限",
};
const relationLabels = {
  depends_on: "依赖", derived_from: "派生自", contains: "包含", related_to: "相关",
};
const aggregationLabels = {
  sum: "求和", avg: "平均", min: "最小", max: "最大", count: "计数",
  count_distinct: "去重计数", ratio: "比率", custom: "自定义",
};
const nativeStatusLabels = {
  active: "启用", inactive: "停用", disabled: "停用", maintenance: "维护中", ready: "就绪",
  draft: "草稿", published: "已发布", archived: "已归档", failed: "失败", completed: "已完成",
};

// 中文说明：目录树展开状态的存储键按工作区隔离，避免切换工作区后看到别的租户的展开状态。
function directoryStateKey() {
  return `cloud-brain.asset-directory:${api.workspace || "default"}`;
}

// 中文说明：读取已展开节点；sessionStorage 不可用或数据损坏时返回空集合（目录仍可正常浏览）。
function directoryState() {
  try { return new Set(JSON.parse(sessionStorage.getItem(directoryStateKey()) || "[]")); }
  catch { return new Set(); }
}

// 中文说明：记录节点展开状态。只保留最近 200 个键，防止长期使用后 sessionStorage 无限增长。
function rememberDirectoryNode(key, open) {
  const stored = directoryState();
  if (open) stored.add(key); else stored.delete(key);
  try { sessionStorage.setItem(directoryStateKey(), JSON.stringify([...stored].slice(-200))); }
  catch { /* Browsing remains usable when session storage is unavailable. */ }
}

// 中文说明：目录节点唯一键（层级 + 类型 + 分类）。分段用 encodeURIComponent 转义，避免分类名里的冒号造成键冲突。
function directoryNodeKey(level, type, category = "") {
  return [level, type, category].map(value => encodeURIComponent(String(value))).join(":");
}

// 中文说明：由类型名散列出 0–5 的配色序号（纯视觉区分，与业务语义无关）。
function directoryTone(type) {
  let hash = 0;
  for (const character of String(type)) hash = ((hash << 5) - hash + character.codePointAt(0)) | 0;
  return Math.abs(hash) % 6;
}

// 中文说明：目录节点/条目的首字标记（去掉标点后取第一个字符），仅用于视觉识别。
function directoryMark(label) {
  return [...String(label || "资源").replace(/[^\p{L}\p{N}]/gu, "")][0] || "资";
}

// 中文说明：业务域层节点（资源类型下的第二级）。每条资源链接都带 returnQuery，用于返回时恢复筛选位置；
// “未分配”业务域使用独立样式类区分。仅当资源描述与子类不同才展示描述，避免重复文案。
function resourceCategoryMarkup(group, expanded, forceOpen = false) {
  return `<div class="resource-category-list">${group.categories.map(category => {
    const key = directoryNodeKey("category", group.type, category.label);
    const opened = forceOpen || expanded.has(key);
    return `<details class="resource-category-group${category.label === UNASSIGNED_RESOURCE_GROUP ? " is-unassigned" : ""}" data-directory-key="${esc(key)}" ${opened ? "open" : ""}>
      <summary><span class="resource-category-name">${esc(category.label)}</span><span class="resource-node-count">${category.items.length}</span><span class="resource-node-chevron" aria-hidden="true"></span></summary>
      <div class="resource-item-list">${category.items.map(item => {
        const description = item.description && item.description !== item.kind ? item.description : "";
        const context = [item.businessDomain && item.businessDomain !== category.label ? item.businessDomain : "", item.kind].filter(Boolean).join(" · ");
        const freshness = item.freshness?.state && !["unknown", "not_configured"].includes(item.freshness.state)
          ? (freshnessLabels[item.freshness.state] || item.freshness.state) : "";
        return `<a class="resource-directory-item" href="/assets-catalog?tab=catalog&type=${encodeURIComponent(item.type)}&id=${encodeURIComponent(item.id)}&returnQuery=${encodeURIComponent(catalogListQuery())}">
          <span class="resource-item-copy"><strong>${esc(item.name)}</strong>${description ? `<small title="${esc(description)}">${esc(description)}</small>` : ""}</span>
          ${context ? `<span class="resource-item-context">${esc(context)}</span>` : ""}
          <span class="resource-item-state">${statusBadge(item.status)}${freshness ? `<small>${esc(freshness)}</small>` : ""}</span>
          <span class="resource-item-arrow" aria-hidden="true">›</span>
        </a>`;
      }).join("")}</div>
    </details>`;
  }).join("")}</div>`;
}

// 中文说明：资源类型层节点。data-loaded 标记该类型资源是否已拉取：只有未加载过才在展开时请求，
// 因此这里不能预先渲染内容（lazy-load 的关键，见 renderCatalogList 的 ensureTypeLoaded）。
function resourceTypeMarkup(definition, group, expanded, { forceOpen = false, forceCategoriesOpen = false } = {}) {
  const key = directoryNodeKey("type", definition.type);
  const opened = forceOpen || expanded.has(key);
  const count = group?.count ?? Number(definition.count || 0);
  return `<details class="resource-type-group directory-tone-${directoryTone(definition.type)}" data-resource-type="${esc(definition.type)}" data-resource-type-label="${esc(definition.label)}" data-directory-key="${esc(key)}" data-loaded="${group ? "true" : "false"}" ${opened ? "open" : ""}>
    <summary>
      <span class="resource-type-mark" aria-hidden="true">${esc(directoryMark(definition.label))}</span>
      <span class="resource-type-copy"><strong>${esc(definition.label)}</strong>${group ? `<small>${group.categories.length} 个分类</small>` : ""}</span>
      <span class="resource-node-count">${count}</span><span class="resource-node-chevron" aria-hidden="true"></span>
    </summary>
    <div class="resource-type-content" data-resource-type-content>${group ? resourceCategoryMarkup(group, expanded, forceCategoriesOpen) : ""}</div>
  </details>`;
}

/**
 * 中文说明：资源目录视图（默认标签页）。
 * 行为：无关键字时只渲染资源类型骨架（展开时才张加载该类型资源）；有关键字/类型筛选时直接全量搜索。
 * 副作用：GET /assets/types 与按需 GET /assets/catalog（loadResourceCatalog 分页拉取）；展开状态写入 sessionStorage。
 * 失败表现：单个类型加载失败只在节点内展示错误与“重试”按钮，不影响其他类型。
 */
async function renderCatalogList(root) {
  const query = params();
  const keyword = query.get("keyword") || "";
  const type = query.get("type") || "";
  const types = await get(BASE + "/types");
  const searched = keyword ? await loadResourceCatalog(get, { keyword, type }) : null;
  const searchedGroups = searched ? groupResourceItems(searched.items, types) : [];
  const searchedByType = new Map(searchedGroups.map(group => [group.type, group]));
  const visibleTypes = (searched
    ? types.filter(item => searchedByType.has(item.type))
    : types.filter(item => Number(item.count || 0) > 0 && (!type || item.type === type)));
  const expanded = directoryState();
  const total = searched ? searched.total : visibleTypes.reduce((sum, item) => sum + Number(item.count || 0), 0);
  const toolbar = `<form class="list-toolbar" data-asset-search>
    <label class="search-input"><span aria-hidden="true">⌕</span><input name="keyword" aria-label="搜索资源" placeholder="名称、别名或标签" value="${esc(keyword)}"></label>
    ${select("type", types.map(item => ({ value: item.type, label: `${item.label} (${item.count})` })), type, "全部类型").replace("<select ", '<select aria-label="资源类型" ')}
    <button class="button" type="submit">查询</button>${keyword || type ? '<button class="button quiet" type="button" data-clear-assets>重置</button>' : ""}
    <span class="directory-result-count">${total} 项</span>
  </form>`;
  const content = visibleTypes.length ? `<div class="resource-directory" data-resource-directory>${visibleTypes.map(definition =>
    resourceTypeMarkup(definition, searchedByType.get(definition.type), expanded, {
      forceOpen: Boolean(keyword || type), forceCategoriesOpen: Boolean(keyword),
    })).join("")}</div>` : emptyState("没有匹配资源", "调整关键词或类型后重试。");
  page(root, "catalog", "数据与资料目录", "查找当前账号有权使用的业务资源。", "",
    `<section class="panel asset-directory-panel">${toolbar}${content}</section>`);
  $("[data-asset-search]", root).addEventListener("submit", event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    navigate({ tab: "catalog", keyword: form.get("keyword"), type: form.get("type") });
  });
  $("[data-clear-assets]", root)?.addEventListener("click", () => navigate({ tab: "catalog" }));

  const directory = $("[data-resource-directory]", root);
  if (!directory) return;
  const wireNode = node => {
    // 只绑定一次：节点内容可能被重新加载，重复绑定会让同一开关记录多次状态
    if (node.dataset.directoryWired) return;
    node.dataset.directoryWired = "true";
    node.addEventListener("toggle", () => rememberDirectoryNode(node.dataset.directoryKey, node.open));
  };
  const ensureTypeLoaded = async node => {
    // 已在加载或已加载过的类型直接返回，避免展开时重复请求同一份资源目录
    if (node.dataset.loaded === "true" || node.dataset.loading === "true") return;
    node.dataset.loading = "true";
    const contentRoot = $("[data-resource-type-content]", node);
    contentRoot.innerHTML = '<div class="resource-directory-loading" role="status">正在加载资源…</div>';
    try {
      const result = await loadResourceCatalog(get, { type: node.dataset.resourceType });
      const group = groupResourceItems(result.items, [{ type: node.dataset.resourceType, label: node.dataset.resourceTypeLabel }])[0];
      contentRoot.innerHTML = group ? resourceCategoryMarkup(group, directoryState()) : '<div class="resource-directory-loading">该类型暂无可用资源</div>';
      node.dataset.loaded = "true";
      $$("[data-directory-key]", contentRoot).forEach(wireNode);
    } catch (error) {
      contentRoot.innerHTML = `<div class="resource-directory-error" role="alert"><span>${esc(error.message)}</span><button class="button small" type="button" data-retry-resource-type>重试</button></div>`;
      $("[data-retry-resource-type]", contentRoot)?.addEventListener("click", () => ensureTypeLoaded(node));
    } finally {
      node.dataset.loading = "false";
    }
  };
  $$('[data-directory-key]', directory).forEach(wireNode);
  $$('[data-resource-type]', directory).forEach(node => {
    node.addEventListener("toggle", () => { if (node.open) ensureTypeLoaded(node); });
    if (node.open) ensureTypeLoaded(node);
  });
}

// 中文说明：资源详情的基础信息行（类型/来源模块/业务状态/敏感级别/时效/别名/标签等）。
// details 里的键按 detailLabels 映射成中文；未登记的键原样显示，便于发现后端新增字段。
// 该字段来自后端目录下发，前端不得自行推断敏感级别或时效状态。
function detailRows(item) {
  const values = [
    ["资源类型", item.typeLabel], ["来源模块", item.source.module], ["业务状态", nativeStatusLabels[item.status] || item.status],
    ["资源子类", item.kind], ["目录分类", item.category], ["业务域", item.businessDomain],
    ["敏感级别", sensitivityLabels[item.sensitivity] || item.sensitivity],
    ["时效状态", freshnessLabels[item.freshness.state] || item.freshness.state],
    ["别名", (item.aliases || []).join("、")], ["标签", (item.tags || []).join("、")],
    ["最后修改", date(item.updatedAt)],
  ].filter(([, value]) => value && value !== "—");
  const detailLabels = {
    provider: "连接类型", metadataAvailable: "已有字段快照", tableCount: "表数量", fieldCount: "字段数量",
    mimeType: "文件类型", size: "文件大小（字节）", sourceType: "文件来源", artifactType: "结果类型",
    visibility: "可见范围", documentCount: "文档数量", canvas: "画布尺寸", refreshSeconds: "刷新周期（秒）",
    templateMode: "模板模式", componentCount: "组件数量", dataSourceCount: "数据源数量", category: "业务分类",
    enabled: "启用状态", tags: "模板标签", mode: "模式", usageCount: "使用次数", builtin: "系统内置",
    domains: "适用业务", nodeCount: "节点数量", publishedRevision: "正式修订", code: "设备编码",
    model: "设备型号", manufacturer: "生产商", location: "安装位置", protocol: "设备协议",
    hasDataSource: "已绑定数据源", ruleCount: "规则数量", hasAdvice: "已关联处置建议", unit: "单位",
    standardMin: "标准下限", standardMax: "标准上限", deviceType: "适用设备类型", metricCount: "指标数量",
    conceptCount: "概念数量", relationTypeCount: "关系类型数量", ontologyRevision: "本体修订",
    executionCount: "执行次数", hasTargetGraph: "已写入图谱", entityCount: "实体数量", relationCount: "关系数量",
    botType: "Bot 类型", apiEnabled: "API 调用", bindingCount: "绑定资源数量", published: "已发布",
  };
  Object.entries(item.details || {}).forEach(([key, value]) => {
    if (value === "" || value === null || value === undefined) return;
    const shown = Array.isArray(value) ? value.join("、") : typeof value === "boolean" ? (value ? "是" : "否") : value;
    values.push([detailLabels[key] || key, shown]);
  });
  return `<dl class="detail-grid catalog-detail-grid">${values.map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join("")}</dl>`;
}

// 中文说明：目录信息里“责任人”的候选成员：无 system.users.view 权限时只显示本人（不报错、不泄漏成员名单）。
async function memberChoices() {
  const current = [{ value: state.me.user.id, label: state.me.user.display_name || state.me.user.username }];
  if (!can("system.users.view")) return current;
  try {
    const result = await api.get("/api/v1/system/users?page=1&page_size=100");
    return [...new Map((result.items || []).map(item => [item.id, { value: item.id, label: item.display_name + " · " + item.username }])).values()];
  } catch { return current; }
}

// 中文说明：编辑资源目录信息（别名/分类/敏感级别/责任人/时效/标签/字段说明）。
// 副作用：PUT /catalog/{type}/{id}/annotation（带 version）；时效以分钟输入、按秒提交；
//   字段说明 JSON 的键固定为“表名.字段名”（后端按此匹配列，写错不会生效）。
// 约束：保存成功后 reload 以刷新最新版本号，否则连续两次编辑会因 version 过期失败。
async function annotationDialog(item, reload) {
  const annotation = item.annotation || {};
  const members = await memberChoices();
  const minutes = annotation.freshnessSeconds ? Math.round(annotation.freshnessSeconds / 60) : "";
  openDialog("编辑目录信息", `<div class="form-grid">
    ${field("业务别名", input("aliases", (annotation.aliases || []).join("，"), 'maxlength="4000"'), "逗号分隔")}
    ${field("分类", input("category", annotation.category, 'maxlength="120"'))}
    ${field("业务域", input("businessDomain", annotation.businessDomain, 'maxlength="120"'))}
    ${field("敏感级别", select("sensitivity", [
      { value: "public", label: "公开" }, { value: "internal", label: "内部" },
      { value: "confidential", label: "机密" }, { value: "restricted", label: "严格受限" },
    ], annotation.sensitivity || "internal", null))}
    ${field("责任人", select("stewardUserId", members, annotation.stewardUserId || "", "未指定"))}
    ${field("时效（分钟）", input("freshnessMinutes", minutes, 'type="number" min="1" max="525600" placeholder="未配置"'))}
    ${field("标签", input("tags", (annotation.tags || []).join("，"), 'maxlength="4000"'), "逗号分隔", true)}
    ${field("业务说明", textarea("description", annotation.description || item.description, 'rows="4" maxlength="8000"'), "", true)}
    ${item.type === "datasource" ? field("字段说明 JSON", textarea("fieldDescriptions", pretty(annotation.fieldDescriptions), 'rows="9" spellcheck="false"'), '键使用“表名.字段名”。', true) : ""}
  </div>`, {
    wide: true,
    onSave: async form => {
      const freshness = String(form.get("freshnessMinutes") || "").trim();
      const payload = {
        aliases: split(form.get("aliases")), category: form.get("category"), businessDomain: form.get("businessDomain"),
        description: form.get("description"), tags: split(form.get("tags")),
        stewardUserId: form.get("stewardUserId") || null, sensitivity: form.get("sensitivity"),
        freshnessSeconds: freshness ? Number(freshness) * 60 : null,
        fieldDescriptions: item.type === "datasource" ? parseJson(form.get("fieldDescriptions"), "字段说明") : (annotation.fieldDescriptions || {}),
      };
      if (annotation.version) payload.version = annotation.version;
      await put(`${BASE}/catalog/${encodeURIComponent(item.type)}/${encodeURIComponent(item.id)}/annotation`, payload);
      toast("目录信息已保存");
      await reload();
    },
  });
}

// 中文说明：资源详情页：目录信息 + 字段快照（数据源类资源的列结构，最多 5000 个，超限提示去数据源模块查看）。
// 权限：item.authorizedActions 含 "annotate" 才显示编辑入口（后端按资源授权计算）。
// 返回列表链接使用 returnQuery 还原原筛选与滚动位置。
async function renderAssetDetail(root, type, id) {
  const item = await get(`${BASE}/catalog/${encodeURIComponent(type)}/${encodeURIComponent(id)}`);
  const reload = () => renderAssetDetail(root, type, id);
  const returnQuery = catalogListQuery(new URLSearchParams(params().get("returnQuery") || ""));
  const actions = `<a class="button" href="/assets-catalog?${esc(returnQuery)}">返回列表</a>
    <a class="button" href="${esc(item.openPath)}">打开原模块</a>
    ${item.authorizedActions.includes("annotate") ? button("编辑目录信息", "data-edit-annotation", true) : ""}`;
  const metadataNotice = item.metadata?.status === "failed"
    ? `<p class="inline-warning" role="status">字段快照同步失败：${esc(item.metadata.error || "未知错误")}</p>` : "";
  const fields = item.fields?.length ? `<section class="panel"><div class="panel-heading"><h2>字段</h2><span class="muted">${item.details.fieldCount || item.fields.length} 个</span></div>${dataTable(
    ["字段", "类型", "约束", "说明"], item.fields,
    value => `<tr><td><span class="cell-primary">${esc(value.path)}</span></td><td>${esc(value.type)}</td><td>${value.primaryKey ? "主键" : value.nullable ? "可空" : "必填"}</td><td>${esc(value.description || "—")}</td></tr>`,
  )}${item.metadata.truncated ? '<p class="panel-note">字段较多，目录仅显示前 5000 个；完整结构请在数据源模块查看。</p>' : ""}</section>` : "";
  page(root, "catalog", item.name, item.description || item.typeLabel, actions,
    `<section class="panel catalog-detail-panel">${detailRows(item)}${metadataNotice}</section>${fields}`);
  $("[data-edit-annotation]", root)?.addEventListener("click", () => annotationDialog(item, reload));
}

// 中文说明：术语/指标/关系三个列表共用的搜索工具栏（字段名 keyword）。
function searchToolbar(name, value) {
  return `<form class="list-toolbar" data-semantic-search><label class="search-input"><span aria-hidden="true">⌕</span><input name="keyword" aria-label="${esc(name)}" placeholder="${esc(name)}" value="${esc(value)}"></label><button class="button" type="submit">查询</button>${value ? '<button class="button quiet" type="button" data-semantic-reset>重置</button>' : ""}</form>`;
}

// 中文说明：业务术语表单（编码创建后仍可提交，若后端限制修改会在弹窗内报错）。
function termDialog(record, reload) {
  openDialog(record ? "编辑业务术语" : "新增业务术语", `<div class="form-grid">
    ${field("术语编码", input("code", record?.code, 'required maxlength="80" pattern="[A-Za-z][A-Za-z0-9_.\\x2D]*"'))}
    ${field("术语名称", input("name", record?.name, 'required maxlength="160"'))}
    ${field("业务域", input("businessDomain", record?.businessDomain, 'maxlength="120"'))}
    ${field("别名", input("aliases", (record?.aliases || []).join("，"), 'maxlength="4000"'))}
    ${field("定义", textarea("definition", record?.definition, 'required rows="5" maxlength="8000"'), "", true)}
    ${field("标签", input("tags", (record?.tags || []).join("，"), 'maxlength="4000"'), "", true)}
  </div>`, {
    onSave: async form => {
      const payload = { code: form.get("code"), name: form.get("name"), definition: form.get("definition"),
        businessDomain: form.get("businessDomain"), aliases: split(form.get("aliases")), tags: split(form.get("tags")) };
      if (record) { payload.version = record.version; await put(`${BASE}/terms/${record.id}`, payload); }
      else await post(BASE + "/terms", payload);
      toast("业务术语已保存"); await reload();
    },
  });
}

// 中文说明：业务术语列表页：新增/编辑/归档（归档保留历史口径，走统一确认弹窗）。
async function renderTerms(root) {
  const keyword = params().get("keyword") || "";
  const offset = Number(params().get("offset") || 0);
  const limit = Math.min(100, Math.max(10, Number(state.pageSize) || 20));
  const result = await get(`${BASE}/terms?keyword=${encodeURIComponent(keyword)}&limit=${limit}&offset=${offset}`);
  page(root, "terms", "业务术语", "维护名称、别名和统一业务口径。",
    can("assets.create") ? button("新增术语", "data-new-term", true) : "",
    `<section class="panel">${searchToolbar("搜索术语", keyword)}${result.items.length ? dataTable(
      ["编码 / 名称", "业务域", "别名", "状态", "最后修改", "操作"], result.items,
      item => `<tr><td><span class="cell-primary">${esc(item.name)}</span><span class="cell-secondary">${esc(item.code)}</span></td><td>${esc(item.businessDomain || "—")}</td><td>${esc(item.aliases.join("、") || "—")}</td><td>${statusBadge(item.status)}</td><td>${date(item.updatedAt)}</td><td><div class="row-actions">${can("assets.edit") ? `<button class="text-button" data-edit-term="${esc(item.id)}">编辑</button>` : ""}${can("assets.delete") && item.status === "active" ? `<button class="text-button danger" data-archive-term="${esc(item.id)}">归档</button>` : ""}</div></td></tr>`,
    ) : emptyState("暂无业务术语", "新增后可统一数据分析和报表中的业务叫法。")}${pagination(result, "terms")}</section>`);
  const byId = new Map(result.items.map(item => [item.id, item]));
  $("[data-new-term]", root)?.addEventListener("click", () => termDialog(null, () => renderTerms(root)));
  $$('[data-edit-term]', root).forEach(element => element.addEventListener("click", () => termDialog(byId.get(element.dataset.editTerm), () => renderTerms(root))));
  $$('[data-archive-term]', root).forEach(element => element.addEventListener("click", () => {
    const item = byId.get(element.dataset.archiveTerm);
    confirmAction("归档业务术语", `确认归档“${item.name}”？历史口径会保留。`, async () => {
      await remove(`${BASE}/terms/${item.id}?version=${item.version}`); toast("业务术语已归档"); await renderTerms(root);
    }, "归档");
  }));
  $("[data-semantic-search]", root).addEventListener("submit", event => { event.preventDefault(); navigate({ tab: "terms", keyword: new FormData(event.currentTarget).get("keyword") }); });
  $("[data-semantic-reset]", root)?.addEventListener("click", () => navigate({ tab: "terms" }));
  wirePagination(root, "terms", { keyword });
}

// 中文说明：指标来源行（资源 `type:id` + 可选字段路径）。按行 DOM 顺序提交，增删行不会错位。
function sourceRow(items, value = {}, index = 0) {
  return `<div class="source-ref-row" data-source-ref-row><select name="source_${index}">${resourceSelectOptions(items, value.type && value.id ? value.type + ":" + value.id : "")}</select><input name="source_field_${index}" value="${esc(value.field || "")}" placeholder="可选字段路径"><button class="icon-button" type="button" data-remove-source aria-label="移除来源">×</button></div>`;
}

// 中文说明：指标表单（编码/名称/聚合方式/单位/粒度/维度/公式/来源/标签）。
// 约束：公式为必填（后端按公式计算，前端不做语法校验）；来源引用必须是资源目录中真实存在的 type:id。
async function metricDialog(record, reload) {
  const catalog = await loadResourceCatalog(get);
  const resources = mergeResourceItems(catalog.items, record?.sourceRefs || []);
  let counter = Math.max(1, (record?.sourceRefs || []).length);
  const rows = (record?.sourceRefs?.length ? record.sourceRefs : [{}]).map((value, index) => sourceRow(resources, value, index)).join("");
  openDialog(record ? "编辑指标" : "新增指标", `<div class="form-grid">
    ${field("指标编码", input("code", record?.code, 'required maxlength="80" pattern="[A-Za-z][A-Za-z0-9_.\\x2D]*"'))}
    ${field("指标名称", input("name", record?.name, 'required maxlength="160"'))}
    ${field("聚合方式", select("aggregation", Object.entries(aggregationLabels).map(([value, label]) => ({ value, label })), record?.aggregation || "custom", null))}
    ${field("单位", input("unit", record?.unit, 'maxlength="40"'))}
    ${field("统计粒度", input("granularity", record?.granularity, 'maxlength="80"'))}
    ${field("分析维度", input("dimensions", (record?.dimensions || []).join("，"), 'maxlength="4000"'), "逗号分隔")}
    ${field("指标公式", textarea("formula", record?.formula, 'required rows="4" maxlength="12000" spellcheck="false"'), "", true)}
    ${field("说明", textarea("description", record?.description, 'rows="3" maxlength="8000"'), "", true)}
    <fieldset class="form-field wide"><legend>数据来源</legend><div class="source-ref-list" data-source-ref-list>${rows}</div><button class="button small" type="button" data-add-source>添加来源</button></fieldset>
    ${field("标签", input("tags", (record?.tags || []).join("，"), 'maxlength="4000"'), "", true)}
  </div>`, {
    wide: true,
    onReady: dialog => {
      $("[data-add-source]", dialog).addEventListener("click", () => {
        $("[data-source-ref-list]", dialog).insertAdjacentHTML("beforeend", sourceRow(resources, {}, counter++));
      });
      dialog.addEventListener("click", event => {
        if (event.target.closest("[data-remove-source]")) event.target.closest("[data-source-ref-row]").remove();
      });
    },
    onSave: async (form, dialog) => {
      const sourceRefs = $$('[data-source-ref-row]', dialog).map(row => {
        const source = $("select", row).value;
        if (!source) return null;
        const [type, id] = source.split(":", 2);
        return { type, id, field: $("input", row).value.trim() };
      }).filter(Boolean);
      const payload = { code: form.get("code"), name: form.get("name"), aggregation: form.get("aggregation"),
        unit: form.get("unit"), granularity: form.get("granularity"), dimensions: split(form.get("dimensions")),
        formula: form.get("formula"), description: form.get("description"), sourceRefs, tags: split(form.get("tags")) };
      if (record) { payload.version = record.version; await put(`${BASE}/metrics/${record.id}`, payload); }
      else await post(BASE + "/metrics", payload);
      toast("指标已保存"); await reload();
    },
  });
}

// 中文说明：指标列表页（新增/编辑/归档）。
async function renderMetrics(root) {
  const keyword = params().get("keyword") || "";
  const offset = Number(params().get("offset") || 0);
  const limit = Math.min(100, Math.max(10, Number(state.pageSize) || 20));
  const result = await get(`${BASE}/metrics?keyword=${encodeURIComponent(keyword)}&limit=${limit}&offset=${offset}`);
  page(root, "metrics", "指标定义", "维护公式、粒度、维度和真实数据来源。",
    can("assets.create") ? button("新增指标", "data-new-metric", true) : "",
    `<section class="panel">${searchToolbar("搜索指标", keyword)}${result.items.length ? dataTable(
      ["编码 / 名称", "公式", "聚合 / 粒度", "来源", "状态", "操作"], result.items,
      item => `<tr><td><span class="cell-primary">${esc(item.name)}</span><span class="cell-secondary">${esc(item.code)}</span></td><td><code class="formula-cell">${esc(item.formula)}</code></td><td>${esc((aggregationLabels[item.aggregation] || item.aggregation) + (item.granularity ? " · " + item.granularity : ""))}</td><td>${esc(item.sourceRefs.map(value => value.name + (value.field ? "." + value.field : "")).join("、") || "—")}</td><td>${statusBadge(item.status)}</td><td><div class="row-actions">${can("assets.edit") ? `<button class="text-button" data-edit-metric="${esc(item.id)}">编辑</button>` : ""}${can("assets.delete") && item.status === "active" ? `<button class="text-button danger" data-archive-metric="${esc(item.id)}">归档</button>` : ""}</div></td></tr>`,
    ) : emptyState("暂无指标", "新增指标并明确公式与数据来源。")}${pagination(result, "metrics")}</section>`);
  const byId = new Map(result.items.map(item => [item.id, item]));
  $("[data-new-metric]", root)?.addEventListener("click", () => metricDialog(null, () => renderMetrics(root)));
  $$('[data-edit-metric]', root).forEach(element => element.addEventListener("click", () => metricDialog(byId.get(element.dataset.editMetric), () => renderMetrics(root))));
  $$('[data-archive-metric]', root).forEach(element => element.addEventListener("click", () => {
    const item = byId.get(element.dataset.archiveMetric);
    confirmAction("归档指标", `确认归档“${item.name}”？`, async () => {
      await remove(`${BASE}/metrics/${item.id}?version=${item.version}`); toast("指标已归档"); await renderMetrics(root);
    }, "归档");
  }));
  $("[data-semantic-search]", root).addEventListener("submit", event => { event.preventDefault(); navigate({ tab: "metrics", keyword: new FormData(event.currentTarget).get("keyword") }); });
  $("[data-semantic-reset]", root)?.addEventListener("click", () => navigate({ tab: "metrics" }));
  wirePagination(root, "metrics", { keyword });
}

// 中文说明：资源关系表单（来源/目标资源 + 关系类型 + 说明）。关系类型取值为后端固定枚举。
async function relationDialog(record, reload) {
  const catalog = await loadResourceCatalog(get);
  const resources = mergeResourceItems(catalog.items, record ? [record.source, record.target] : []);
  const sourceValue = record ? record.source.type + ":" + record.source.id : "";
  const targetValue = record ? record.target.type + ":" + record.target.id : "";
  openDialog(record ? "编辑资源关系" : "新增资源关系", `<div class="form-grid">
    ${field("来源资源", `<select name="source" required>${resourceSelectOptions(resources, sourceValue)}</select>`)}
    ${field("目标资源", `<select name="target" required>${resourceSelectOptions(resources, targetValue)}</select>`)}
    ${field("关系", select("relationKind", Object.entries(relationLabels).map(([value, label]) => ({ value, label })), record?.relationKind || "related_to", null))}
    ${field("说明", textarea("description", record?.description, 'rows="4" maxlength="4000"'), "", true)}
  </div>`, {
    onSave: async form => {
      const ref = value => { const [type, id] = String(value).split(":", 2); return { type, id }; };
      const payload = { source: ref(form.get("source")), target: ref(form.get("target")),
        relationKind: form.get("relationKind"), description: form.get("description") };
      if (record) { payload.version = record.version; await put(`${BASE}/relations/${record.id}`, payload); }
      else await post(BASE + "/relations", payload);
      toast("资源关系已保存"); await reload();
    },
  });
}

// 中文说明：资源关系列表页（只列 status=active，归档走统一确认弹窗）。
async function renderRelations(root) {
  const offset = Number(params().get("offset") || 0);
  const limit = Math.min(100, Math.max(10, Number(state.pageSize) || 20));
  const result = await get(`${BASE}/relations?status=active&limit=${limit}&offset=${offset}`);
  page(root, "relations", "资源关系", "维护资源依赖、派生和关联关系。",
    can("assets.create") ? button("新增关系", "data-new-relation", true) : "",
    `<section class="panel">${result.items.length ? dataTable(
      ["来源", "关系", "目标", "说明", "最后修改", "操作"], result.items,
      item => `<tr><td><a class="cell-primary" href="/assets-catalog?tab=catalog&type=${esc(item.source.type)}&id=${esc(item.source.id)}">${esc(item.source.name)}</a><span class="cell-secondary">${esc(item.source.typeLabel)}</span></td><td>${esc(relationLabels[item.relationKind] || item.relationKind)}</td><td><a class="cell-primary" href="/assets-catalog?tab=catalog&type=${esc(item.target.type)}&id=${esc(item.target.id)}">${esc(item.target.name)}</a><span class="cell-secondary">${esc(item.target.typeLabel)}</span></td><td>${esc(item.description || "—")}</td><td>${date(item.updatedAt)}</td><td><div class="row-actions">${can("assets.edit") ? `<button class="text-button" data-edit-relation="${esc(item.id)}">编辑</button>` : ""}${can("assets.delete") ? `<button class="text-button danger" data-archive-relation="${esc(item.id)}">归档</button>` : ""}</div></td></tr>`,
    ) : emptyState("暂无资源关系", "新增后可追踪资源依赖和派生路径。")}${pagination(result, "relations")}</section>`);
  const byId = new Map(result.items.map(item => [item.id, item]));
  $("[data-new-relation]", root)?.addEventListener("click", () => relationDialog(null, () => renderRelations(root)));
  $$('[data-edit-relation]', root).forEach(element => element.addEventListener("click", () => relationDialog(byId.get(element.dataset.editRelation), () => renderRelations(root))));
  $$('[data-archive-relation]', root).forEach(element => element.addEventListener("click", () => {
    const item = byId.get(element.dataset.archiveRelation);
    confirmAction("归档资源关系", `确认归档“${item.source.name} → ${item.target.name}”？`, async () => {
      await remove(`${BASE}/relations/${item.id}?version=${item.version}`); toast("资源关系已归档"); await renderRelations(root);
    }, "归档");
  }));
  wirePagination(root, "relations", {});
}

/**
 * 中文说明：数据与资料目录页入口，按 ?tab 与 ?type/?id 分发到资源详情或四个列表视图。
 * 副作用：由各子视图完成（读取为主，编辑目录信息/术语/指标/关系为写操作）；异常由 main.js 统一渲染错误页。
 */
export async function renderAssetCatalog(root) {
  const query = params();
  const tab = query.get("tab") || "catalog";
  if (tab === "catalog" && query.get("type") && query.get("id")) return renderAssetDetail(root, query.get("type"), query.get("id"));
  if (tab === "terms") return renderTerms(root);
  if (tab === "metrics") return renderMetrics(root);
  if (tab === "relations") return renderRelations(root);
  return renderCatalogList(root);
}
