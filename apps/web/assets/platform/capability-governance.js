/**
 * 工具治理模块（/capabilities?view=categories 工具分类、?view=methods 方法管理）
 *
 * 位置：由 capabilities.js 的 renderToolCatalog 委托调用（返回 true 表示已渲染）；自身提供标签页组件
 *   capabilityGovernanceTabs 供工具目录/分类/方法三个视图共享。
 * 依赖接口前缀：/api/v1/capabilities（GET/POST/PATCH/DELETE /categories、GET /methods、PATCH /methods/{name}、
 *   POST /methods/batch 批量设置、GET /categories-export 与 /methods-export 导出 CSV）。
 * 与 iframe 内 Vue 应用无关。
 *
 * 关键约束：
 * - 方法启停直接约束 AI 与工作流的调用能力，这是本页最重要的写操作，页头文案需保持这一提示。
 * - 平台内置方法（无 connectorId）只能改分类/显示信息/启停，不能删除其程序定义；
 *   删除操作只对“连接器提供的方法”开放，且实际是归档来源连接器（见 capabilities.js 的 removeManagedMethod）。
 * - 方法的稳定工具名（name）是调用契约，不可修改；展示用 displayName 可改。
 * - 批量设置用 __keep/__none 两个哨兵值区分“保持原值 / 设为未分类”，不能与真实分类 id 混淆。
 * - 方法的唯一标识是 connectorId::name（methodKey），因为不同连接器可以暴露同名方法。
 * - 所有写操作带 version（方法为 methodVersion，分类为 version），删除/归档走统一确认弹窗。
 */

import { currentPage, wirePageNavigation } from "./list-navigation.js";
import { api } from "./api.js";
import {
  $, $$, can, esc, dataTable, emptyState, statusBadge,
  pagination, field, input, select, openDialog, confirmAction, toast, pageHeader,
} from "./ui.js";

const BASE = "/api/v1/capabilities";
const textarea = (name, value, attributes) =>
  '<textarea name="' + esc(name) + '" ' + (attributes || "") + ">" + esc(value || "") + "</textarea>";

// 中文说明：本模块接口返回 { success, data } 包装，统一解包（注意这里用 hasOwnProperty 判断，兼容 data 为 null）。
async function request(method, path, body) {
  const result = await api[method](path, body);
  return result && result.success === true && Object.prototype.hasOwnProperty.call(result, "data")
    ? result.data : result;
}

const get = path => request("get", path);
const post = (path, body) => request("post", path, body);
const patch = (path, body) => request("patch", path, body);
const remove = path => request("delete", path);
const pretty = value => JSON.stringify(value || {}, null, 2);

function go(values) {
  const query = new URLSearchParams();
  Object.entries(values || {}).forEach(([key, value]) => {
    if (value !== "" && value !== null && value !== undefined) query.set(key, value);
  });
  location.assign("/capabilities" + (query.size ? "?" + query.toString() : ""));
}

// 中文说明：工具治理三个视图的标签页（工具目录 / 工具分类 / 方法管理）。
// 由 capabilities.js 复用，路径与 view 参数必须与该文件的 renderToolCatalog 分支保持一致。
export function capabilityGovernanceTabs(active) {
  const tabs = [
    ["tools", "/capabilities", "工具目录"],
    ["categories", "/capabilities?view=categories", "工具分类"],
    ["methods", "/capabilities?view=methods", "方法管理"],
  ];
  return '<nav class="catalog-tabs" aria-label="工具管理视图">' + tabs.map(item =>
    '<a href="' + item[1] + '" ' + (active === item[0] ? 'aria-current="page"' : "") + ">" + item[2] + "</a>"
  ).join("") + "</nav>";
}

// 中文说明：工具分类表单（新增/修改）。上级分类选项排除自身（仅一层排重，后端会做环校验）；
// 分类编码创建后仍允许提交，但正式环境通常不期望变更——若后端限制，错误会在弹窗内提示。
function categoryDialog(categories, record) {
  const parentOptions = categories.filter(item => item.id !== (record && record.id))
    .map(item => ({ value: item.id, label: item.path }));
  const html = '<div class="form-grid">' +
    field("分类名称", input("name", record && record.name, 'required maxlength="160"')) +
    field("分类编码", input("code", record && record.code, 'required maxlength="80" pattern="[A-Za-z][A-Za-z0-9_.\\x2D]*"')) +
    field("上级分类", select("parentId", parentOptions, record && record.parentId || "", "顶级分类")) +
    field("显示顺序", input("sortOrder", record ? record.sortOrder : 0, 'type="number" min="-100000" max="100000"')) +
    field("说明", textarea("description", record && record.description, 'rows="4" maxlength="1000"'), "", true) +
    '<label class="form-field wide capability-checkbox"><input type="checkbox" name="enabled" ' +
      (!record || record.enabled ? "checked" : "") + '><span>启用分类</span></label></div>';
  openDialog(record ? "修改工具分类" : "新增工具分类", html, {
    saveLabel: record ? "保存修改" : "创建分类",
    onSave: async form => {
      const payload = {
        parentId: String(form.get("parentId") || "") || null,
        code: String(form.get("code") || "").trim(),
        name: String(form.get("name") || "").trim(),
        description: String(form.get("description") || "").trim(),
        sortOrder: Number(form.get("sortOrder") || 0),
        enabled: form.get("enabled") === "on",
      };
      if (record) {
        payload.version = record.version;
        await patch(BASE + "/categories/" + record.id, payload);
      } else {
        await post(BASE + "/categories", payload);
      }
      toast(record ? "工具分类已更新" : "工具分类已创建");
      go({ view: "categories" });
    },
  });
}

// 中文说明：工具分类列表页。导出需 capabilities.export；归档仅允许“无下级且未分配方法”的分类（后端校验），
// 归档属不可逆动作，走统一确认弹窗。
async function renderCategories(root) {
  const data = await get(BASE + "/categories");
  let actions = "";
  if (can("capabilities.export")) actions += '<button class="button" type="button" data-category-export>导出 CSV</button>';
  if (can("capabilities.create")) actions += '<button class="button primary" type="button" data-category-new>新增分类</button>';
  const content = data.items.length ? dataTable(
    ["分类", "编码", "状态", "方法数", "操作"],
    data.items,
    item => {
      let rowActions = "";
      if (can("capabilities.edit")) rowActions += '<button class="text-button" type="button" data-category-edit="' + esc(item.id) + '">修改</button>';
      if (can("capabilities.delete")) rowActions += '<button class="text-button danger" type="button" data-category-delete="' + esc(item.id) + '">归档</button>';
      return "<tr><td><span class=\"cell-primary\">" + esc(item.path) + "</span>" +
        (item.description ? '<span class="cell-secondary">' + esc(item.description) + "</span>" : "") +
        "</td><td>" + esc(item.code) + "</td><td>" +
        (item.enabled ? statusBadge("active") : statusBadge("disabled")) +
        "</td><td>" + esc(item.methodCount) + '</td><td><div class="row-actions">' + rowActions + "</div></td></tr>";
    }
  ) : emptyState("还没有工具分类", "分类用于整理真实可执行方法，不会创建占位能力。");
  root.innerHTML = pageHeader("工具与能力", "组织可执行工具", actions) +
    capabilityGovernanceTabs("categories") + '<section class="panel">' + content + "</section>";
  $("[data-category-new]", root)?.addEventListener("click", () => categoryDialog(data.items));
  $("[data-category-export]", root)?.addEventListener("click", async () => {
    await api.download(BASE + "/categories-export", "tool-categories.csv");
    toast("分类目录已导出");
  });
  $$("[data-category-edit]", root).forEach(button => button.addEventListener("click", () => {
    categoryDialog(data.items, data.items.find(item => item.id === button.dataset.categoryEdit));
  }));
  $$("[data-category-delete]", root).forEach(button => button.addEventListener("click", () => {
    const item = data.items.find(value => value.id === button.dataset.categoryDelete);
    if (!item) return;
    confirmAction("归档工具分类", "归档“" + item.name + "”？只有无下级且未分配方法的分类可归档。", async () => {
      await remove(BASE + "/categories/" + item.id + "?version=" + item.version);
      toast("工具分类已归档");
      go({ view: "categories" });
    }, "确认归档");
  }));
}

// 中文说明：方法“设置”弹窗（PATCH /methods/{name}）：
// 稳定工具名只读；分类只列启用分类；勾选“允许 AI、工作流和接口发起新调用”即 enabled，
// 取消勾选会立即影响线上调用方，弹窗文案已说明。
function methodDialog(tool, categories) {
  const html = '<div class="form-grid">' +
    field("稳定工具名", '<input value="' + esc(tool.name) + '" disabled>') +
    field("分类", select("categoryId", categories.filter(item => item.enabled)
      .map(item => ({ value: item.id, label: item.path })), tool.categoryId || "", "未分类")) +
    field("方法编码", input("code", tool.code || tool.name, 'required maxlength="100" pattern="[A-Za-z][A-Za-z0-9_.\\x2D]*"')) +
    field("显示名称", input("displayName", tool.displayName === tool.name ? "" : tool.displayName, 'maxlength="200" placeholder="留空时显示稳定工具名"')) +
    field("标签", input("tags", (tool.tags || []).join("，"), 'maxlength="500" placeholder="用逗号分隔"'), "", true) +
    field("用途说明", textarea("description", tool.description, 'rows="5" maxlength="2000"'), "", true) +
    '<label class="form-field wide capability-checkbox"><input type="checkbox" name="enabled" ' +
      (tool.enabled ? "checked" : "") + '><span>允许 AI、工作流和接口发起新调用</span></label></div>';
  openDialog("维护工具方法", html, {
    wide: true,
    saveLabel: "保存方法设置",
    onSave: async form => {
      await patch(BASE + "/methods/" + encodeURIComponent(tool.name), {
        version: tool.methodVersion,
        categoryId: String(form.get("categoryId") || "") || null,
        code: String(form.get("code") || "").trim(),
        displayName: String(form.get("displayName") || "").trim(),
        description: String(form.get("description") || "").trim(),
        enabled: form.get("enabled") === "on",
        tags: String(form.get("tags") || "").split(/[，,]/).map(item => item.trim()).filter(Boolean),
      });
      toast("工具方法已更新");
      go({ view: "methods" });
    },
  });
}

// 中文说明：方法唯一键 = 来源连接器 + 稳定工具名（内置方法用 builtin 前缀）。
// 不能用 name 单独定位：不同连接器可以暴露同名方法。
function methodKey(tool) {
  return (tool.connectorId || "builtin") + "::" + tool.name;
}

function methodByKey(items, key) {
  return items.find(item => methodKey(item) === key);
}

// 中文说明：方法详情只读弹窗：展示来源、分类、状态、所需权限与输入/输出 Schema。
// 连接器方法给出“查看来源连接器”入口；内置方法说明其程序定义不可删除（只能启停与改展示信息）。
function methodDetailDialog(tool) {
  const source = tool.connectorName
    ? esc(tool.providerLabel + " · " + tool.connectorName)
    : esc(tool.providerLabel);
  const sourceContent = tool.connectorId
    ? '<div class="capability-detail-actions"><a class="button" href="/connectors?id=' + encodeURIComponent(tool.connectorId) + '">查看来源连接器</a></div>'
    : '<p class="muted">平台内置方法由系统版本提供；可调整分类、显示信息和启停状态，但不能删除其程序定义。</p>';
  openDialog("查看工具方法", '<section class="catalog-detail-panel capability-detail">' +
    '<dl class="detail-grid catalog-detail-grid">' +
      '<div><dt>显示名称</dt><dd>' + esc(tool.displayName) + '</dd></div>' +
      '<div><dt>稳定工具名</dt><dd>' + esc(tool.name) + '</dd></div>' +
      '<div><dt>方法编码</dt><dd>' + esc(tool.code || tool.name) + '</dd></div>' +
      '<div><dt>来源</dt><dd>' + source + '</dd></div>' +
      '<div><dt>分类</dt><dd>' + esc(tool.categoryPath || "未分类") + '</dd></div>' +
      '<div><dt>状态</dt><dd>' + (tool.enabled ? "启用" : "停用") + '</dd></div>' +
      '<div class="wide"><dt>用途</dt><dd>' + esc(tool.description || "—") + '</dd></div>' +
      '<div class="wide"><dt>标签</dt><dd>' + esc((tool.tags || []).join("、") || "—") + '</dd></div>' +
      '<div class="wide"><dt>所需权限</dt><dd>' + esc((tool.permissions || []).join("、") || "无额外业务权限") + '</dd></div>' +
    '</dl>' +
    '<details class="capability-schema"><summary>输入 Schema</summary><pre class="json-preview">' + esc(pretty(tool.inputSchema)) + '</pre></details>' +
    (Object.keys(tool.outputSchema || {}).length
      ? '<details class="capability-schema"><summary>输出 Schema</summary><pre class="json-preview">' + esc(pretty(tool.outputSchema)) + '</pre></details>'
      : "") + sourceContent + '</section>', { wide: true });
}

// 中文说明：批量设置弹窗（POST /methods/batch）。
// 约束：__keep/__none 是前端哨兵值，仅用于表达“保持不变/清空分类”，不能作为真实 categoryId 提交；
//   每项都带自己的 methodVersion，批量提交时后端逐项做并发校验（任一项过期会整批失败）。
function batchDialog(selected, categories) {
  const categoryOptions = [
    { value: "__keep", label: "保持原分类" },
    { value: "__none", label: "设为未分类" },
  ].concat(categories.filter(item => item.enabled).map(item => ({ value: item.id, label: item.path })));
  const html = '<div class="form-grid">' +
    field("分类", select("categoryId", categoryOptions, "__keep", null)) +
    field("调用状态", select("enabled", [
      { value: "__keep", label: "保持原状态" },
      { value: "true", label: "启用" },
      { value: "false", label: "停用" },
    ], "__keep", null)) + "</div>";
  openDialog("批量设置 " + selected.length + " 个方法", html, {
    saveLabel: "应用批量设置",
    onSave: async form => {
      const category = String(form.get("categoryId"));
      const enabled = String(form.get("enabled"));
      await post(BASE + "/methods/batch", {
        items: selected.map(tool => ({
          toolName: tool.name,
          version: tool.methodVersion,
          categoryId: category === "__keep" ? tool.categoryId : (category === "__none" ? null : category),
          code: tool.code || tool.name,
          displayName: tool.displayName === tool.name ? "" : tool.displayName,
          description: tool.description || "",
          enabled: enabled === "__keep" ? tool.enabled : enabled === "true",
          tags: tool.tags || [],
        })),
      });
      toast("工具方法已批量更新");
      go({ view: "methods" });
    },
  });
}

/**
 * 中文说明：方法管理视图（列表 + 筛选 + 批量设置）。
 * 参数：operations 由调用方（capabilities.js）注入的回调：
 *   create(新增方法)、edit(编辑来源连接器)、test(测试)、remove(删除)。缺少回调时对应按钮不渲染。
 * 分页用 limit/offset（本接口约定），paging 对象转成 wirePageNavigation 需要的 page/page_size 结构。
 * 副作用：GET /methods 与 /categories；导出 CSV 会触发下载；“设置”走 PATCH，批量走 POST。
 */
async function renderMethods(root, operations = {}) {
  const query = new URLSearchParams(location.search);
  const keyword = query.get("keyword") || "";
  const provider = query.get("provider") || "";
  const categoryId = query.get("category_id") || "";
  const enabled = query.get("enabled") || "";
  const page = currentPage();
  const suffix = "?limit=50&offset=" + ((page - 1) * 50) + "&keyword=" + encodeURIComponent(keyword) +
    "&provider=" + encodeURIComponent(provider) +
    "&category_id=" + encodeURIComponent(categoryId) +
    (enabled ? "&enabled=" + encodeURIComponent(enabled) : "");
  const [data, categories] = await Promise.all([get(BASE + "/methods" + suffix), get(BASE + "/categories")]);
  const paging = { total: data.total, page: data.offset / data.limit + 1, page_size: data.limit };
  let actions = "";
  if (can("capabilities.export")) actions += '<button class="button" type="button" data-method-export>导出 CSV</button>';
  if (can("capabilities.edit")) actions += '<button class="button" type="button" data-method-batch disabled>批量设置</button>';
  if (can("connectors.create") && operations.create) actions += '<button class="button primary" type="button" data-method-new>新增方法</button>';
  const filter = '<form class="list-toolbar" data-method-filter>' +
    '<input type="hidden" name="view" value="methods">' +
    '<label class="search-input"><span aria-hidden="true">⌕</span><input name="keyword" value="' + esc(keyword) + '" placeholder="方法名、编码或标签" aria-label="搜索方法"></label>' +
    '<label><span class="sr-only">工具类型</span>' + select("provider", [
      { value: "builtin", label: "平台内置" }, { value: "http", label: "HTTP" },
      { value: "database", label: "数据库" }, { value: "mcp", label: "MCP" },
    ], provider, "全部类型") + '</label>' +
    '<label><span class="sr-only">工具分类</span>' + select("category_id", categories.items.map(item => ({ value: item.id, label: item.path })), categoryId, "全部分类") + '</label>' +
    '<label><span class="sr-only">调用状态</span>' + select("enabled", [{ value: "true", label: "启用" }, { value: "false", label: "停用" }], enabled, "全部状态") + '</label>' +
    '<button class="button" type="submit">查询</button></form>';
  const content = data.items.length ? dataTable(
    ["选择", "方法", "分类", "来源", "状态", "操作"],
    data.items,
    item => {
      const key = methodKey(item);
      let rowActions = '<button class="text-button" type="button" data-method-view="' + esc(key) + '">查看</button>';
      if (item.connectorId && can("connectors.edit") && operations.edit) {
        rowActions += '<button class="text-button" type="button" data-method-source-edit="' + esc(key) + '">编辑</button>';
      }
      if (can("capabilities.execute") && operations.test) {
        rowActions += '<button class="text-button" type="button" data-method-test="' + esc(key) + '">测试</button>';
      }
      if (can("capabilities.edit")) {
        rowActions += '<button class="text-button" type="button" data-method-edit="' + esc(key) + '">设置</button>';
      }
      if (item.connectorId && can("connectors.delete") && operations.remove) {
        rowActions += '<button class="text-button danger" type="button" data-method-delete="' + esc(key) + '">删除</button>';
      }
      return '<tr><td><input type="checkbox" data-method-select="' + esc(key) + '" aria-label="选择 ' + esc(item.displayName) + '"></td>' +
        '<td><span class="cell-primary">' + esc(item.displayName) + '</span><span class="cell-secondary">' +
        esc(item.code && item.code !== item.name ? item.name + " · " + item.code : item.name) + "</span></td><td>" + esc(item.categoryPath || "未分类") +
        "</td><td><span class=\"cell-primary\">" + esc(item.providerLabel) + "</span>" +
        (item.connectorName ? '<span class="cell-secondary">' + esc(item.connectorName) + "</span>" : "") +
        "</td><td>" + (item.enabled ? statusBadge("active") : statusBadge("disabled")) +
        '</td><td><div class="row-actions method-row-actions">' + rowActions + "</div></td></tr>";
    }
  ) : emptyState("没有符合条件的方法", "调整筛选条件或先发布连接器工具。");
  root.innerHTML = pageHeader("工具与能力", "方法启停会直接约束 AI 与工作流调用", actions) +
    capabilityGovernanceTabs("methods") + '<section class="panel">' + filter + content + pagination(paging) + "</section>";
  wirePageNavigation(root, paging);
  $("[data-method-filter]", root)?.addEventListener("submit", event => {
    event.preventDefault();
    go(Object.fromEntries(new FormData(event.currentTarget)));
  });
  $("[data-method-export]", root)?.addEventListener("click", async () => {
    await api.download(BASE + "/methods-export", "tool-methods.csv");
    toast("方法目录已导出");
  });
  $("[data-method-new]", root)?.addEventListener("click", () => operations.create());
  $$("[data-method-view]", root).forEach(button => button.addEventListener("click", () => {
    const tool = methodByKey(data.items, button.dataset.methodView);
    if (tool) methodDetailDialog(tool);
  }));
  $$("[data-method-source-edit]", root).forEach(button => button.addEventListener("click", () => {
    const tool = methodByKey(data.items, button.dataset.methodSourceEdit);
    if (tool) operations.edit(tool);
  }));
  $$("[data-method-test]", root).forEach(button => button.addEventListener("click", () => {
    const tool = methodByKey(data.items, button.dataset.methodTest);
    if (tool) operations.test(tool);
  }));
  $$("[data-method-edit]", root).forEach(button => button.addEventListener("click", () => {
    const tool = methodByKey(data.items, button.dataset.methodEdit);
    if (tool) methodDialog(tool, categories.items);
  }));
  $$("[data-method-delete]", root).forEach(button => button.addEventListener("click", () => {
    const tool = methodByKey(data.items, button.dataset.methodDelete);
    if (tool) operations.remove(tool);
  }));
  const batch = $("[data-method-batch]", root);
  // 选择集按方法名去重（Map 的键为 name），避免同名方法被批量提交两次
  const selected = () => {
    const unique = new Map();
    $$("[data-method-select]:checked", root).forEach(box => {
      const item = methodByKey(data.items, box.dataset.methodSelect);
      if (item) unique.set(item.name, item);
    });
    return [...unique.values()];
  };
  $$("[data-method-select]", root).forEach(box => box.addEventListener("change", () => {
    if (batch) batch.disabled = selected().length === 0;
  }));
  batch?.addEventListener("click", () => batchDialog(selected(), categories.items));
}

/**
 * 中文说明：治理视图分发入口（由 capabilities.js 的 renderToolCatalog 调用）。
 * 参数：view=URL 的 view 参数（categories / methods）；operations 为注入的操作回调。
 * 返回：true 表示已渲染（调用方应停止后续渲染），false 表示该 view 不归本模块处理。
 */
export async function renderCapabilityGovernance(root, view, operations = {}) {
  if (view === "categories") {
    await renderCategories(root);
    return true;
  }
  if (view === "methods") {
    await renderMethods(root, operations);
    return true;
  }
  return false;
}
