/**
 * 权限与资源授权模块（路由 /system/permissions?view=catalog|grants|history）
 *
 * 位置：三个视图——权限目录（只读展示后端权限码与“可委派”标记）、资源授权（单个资源的额外共享，
 *   可新增/撤销）与变更记录（权限相关变更的前后快照审计）；页头与标签页由各视图自行渲染。
 * 依赖接口前缀：/api/v1/system（GET /permissions、GET/POST/DELETE /resource-grants、GET /permission-history），
 *   以及各资源列表接口（/api/files、/api/artifacts、/api/knowledge、/api/datasources、/api/plugins、/api/skills、/api/connectors）。
 * 与 iframe 内 Vue 应用无关。
 *
 * 关键约束：
 * - 资源授权是在角色数据范围之外的“额外共享”，不是替代：页面上必须保持这一语义，不要把两者混为一谈。
 * - 权限码（system.permissions.manage / system.permissions.history / system.roles.view 等）必须与后端目录一致；
 *   变更记录页只在拥有 system.permissions.history 时才出现在标签页里，非授权用户手动改 URL 也会被回落。
 * - RESOURCE_TYPES 的 endpoint 与前端权限码约定为 “<value>.view”（如 files.view），新增资源类型须同时满足两者。
 * - 筛选/翻页状态通过 URL 承载（replaceQuery 用 pushState 重写查询串），刷新与分享链接能还原视图。
 * - 撤销授权、授权失败后的错误提示都要给出“资源本身不会被删除”的信息，避免用户误判影响面。
 */

import { api, allPages } from "./api.js";
import { $, $$, state, can, esc, date, pageHeader, button, dataTable, emptyState, field, select,
  checkboxes, openDialog, confirmAction, toast, pagination, wireList, searchBar } from "./ui.js";

const BASE = "/api/v1/system";
// 中文说明：可授权资源类型。value 同时是权限码前缀（value + ".view"）与后端 resource_type 字段取值，
// endpoint 为对应资源列表接口；files 额外合并 /api/artifacts（生成结果与文件库分开存储）。
const RESOURCE_TYPES = [
  { value: "files", label: "文件与生成结果", endpoint: "/api/files", extraEndpoint: "/api/artifacts" },
  { value: "knowledge", label: "知识库", endpoint: "/api/knowledge" },
  { value: "datasources", label: "数据源", endpoint: "/api/datasources" },
  { value: "plugins", label: "插件", endpoint: "/api/plugins" },
  { value: "skills", label: "技能", endpoint: "/api/skills" },
  { value: "connectors", label: "连接器", endpoint: "/api/connectors" },
];
const ACTIONS = [
  { value: "view", label: "查看" }, { value: "edit", label: "修改" },
  { value: "execute", label: "执行" }, { value: "export", label: "导出" },
];
// 中文说明：动作码必须与后端资源授权校验使用的动作集合一致，缺一项就会出现“授权成功但无法执行”。

// 中文说明：新增资源授权弹窗。先按当前账号可读的资源类型过滤，再动态加载该类型下的资源清单。
// 失败表现：无任何可读资源类型时直接抛错（调用方 toast），避免打开一个无法完成提交的空表单。
async function grantForm(reload) {
  const [users, roles] = await Promise.all([allPages(BASE + "/users"), allPages(BASE + "/roles")]);
  const choices = RESOURCE_TYPES.filter((item) => can(item.value + ".view"));
  if (!choices.length) throw new Error("当前账号没有可授权资源的读取权限");
  openDialog("新增资源授权", `<div class="form-grid">${field("资源类型", select("resource_type", choices, choices[0].value, null))}${field("具体资源", select("resource_id", [], "", "正在读取资源…"))}${field("授权对象类型", select("subject_type", [{ value: "user", label: "用户" }, { value: "role", label: "角色" }], "user", null))}${field("授权对象", select("subject_id", users.map((item) => ({ value: item.id, label: item.display_name + " · " + item.username }))))}${field("允许动作", checkboxes("actions", ACTIONS, ["view"]), "对方仍需具有相应功能权限；这里仅扩展这个资源的可访问范围。", true)}</div>`, {
    async onReady(dialog) {
      // 资源类型可被快速连续切换：用自增 revision 丢弃过期响应，防止后到的旧结果覆盖新选择
      let resourceLoadRevision = 0;
      const loadResources = async () => {
        const type = $('[name="resource_type"]', dialog).value;
        const selectElement = $('[name="resource_id"]', dialog);
        const revision = ++resourceLoadRevision;
        selectElement.disabled = true;
        selectElement.innerHTML = '<option value="">正在读取资源…</option>';
        try {
          const choice = choices.find((item) => item.value === type);
          const results = await Promise.all([choice.value === "files" ? allPages(choice.endpoint).then(items => ({ items })) : api.get(choice.endpoint), choice.extraEndpoint ? api.get(choice.extraEndpoint) : { items: [] }]);
          if (revision !== resourceLoadRevision || $('[name="resource_type"]', dialog).value !== type) return;
          const items = results.flatMap((result) => result.items || []);
          selectElement.innerHTML = `<option value="">${items.length ? "请选择资源" : "暂无可授权资源"}</option>` + items.map((item) => `<option value="${esc(item.id)}">${esc(item.name || item.title || item.id)}</option>`).join("");
        } catch (error) {
          if (revision !== resourceLoadRevision || $('[name="resource_type"]', dialog).value !== type) return;
          selectElement.innerHTML = '<option value="">读取失败，请调整资源类型</option>';
          toast(error.message, true);
        } finally {
          if (revision === resourceLoadRevision && $('[name="resource_type"]', dialog).value === type) selectElement.disabled = false;
        }
      };
      $('[name="resource_type"]', dialog).addEventListener("change", loadResources);
      // 切换授权对象类型只替换选项数据，不重置已选对象（选中项必然失效，因此重建为“请选择”）
      $('[name="subject_type"]', dialog).addEventListener("change", (event) => {
        const options = event.target.value === "user" ? users.map((item) => ({ id: item.id, name: item.display_name })) : roles;
        $('[name="subject_id"]', dialog).innerHTML = '<option value="">请选择授权对象</option>' + options.map((item) => `<option value="${esc(item.id)}">${esc(item.name)}</option>`).join("");
      });
      await loadResources();
    },
    onSave: async (form) => {
      const payload = { resource_type: form.get("resource_type"), resource_id: form.get("resource_id"),
        subject_type: form.get("subject_type"), subject_id: form.get("subject_id"), actions: form.getAll("actions") };
      // 前端先做一次完整性校验：避免把不完整授权写到后端后只得到一条难懂的报错
      if (!payload.resource_id || !payload.subject_id || !payload.actions.length) throw new Error("请选择资源、授权对象和至少一个允许动作");
      await api.post(BASE + "/resource-grants", payload);
      toast("资源授权已保存");
      await reload();
    },
  });
}

// 中文说明：顶部标签页；“变更记录”只在拥有 system.permissions.history 权限时渲染。
function tabs(view) {
  return `<nav class="system-tool-tabs" aria-label="权限管理功能"><a href="/system/permissions" ${view === "catalog" ? 'aria-current="page"' : ""}>权限目录</a><a href="/system/permissions?view=grants" ${view === "grants" ? 'aria-current="page"' : ""}>资源授权</a>${can("system.permissions.history") ? `<a href="/system/permissions?view=history" ${view === "history" ? 'aria-current="page"' : ""}>变更记录</a>` : ""}</nav>`;
}

// 中文说明：把筛选状态写回地址栏（pushState，保留可回退历史）并重绘页面。
// 空值不写入查询串，保证 URL 干净；重绘失败只 toast，不打断当前页面。
function replaceQuery(values, root) {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => { if (value !== "" && value !== undefined && value !== null) params.set(key, value); });
  history.pushState({}, "", "/system/permissions" + (params.size ? "?" + params : ""));
  renderPermissions(root).catch((error) => toast(error.message, true));
}

// 中文说明：权限目录视图（只读）。展示后端权限码分组与“可委派(grantable)”标记：
// grantable=false 表示该权限只能由系统内置角色持有，不可在角色页勾选。
async function renderCatalog(root) {
  const catalog = await api.get(BASE + "/permissions");
  const groups = new Map();
  catalog.items.forEach((item) => { const rows = groups.get(item.group) || []; rows.push(item); groups.set(item.group, rows); });
  root.innerHTML = pageHeader("权限与数据范围", "通过角色、数据范围和单个资源授权控制业务访问。") + tabs("catalog") +
    `<section class="panel"><div class="panel-heading"><div><h2>功能权限目录</h2><p>在角色中组合授权；目录本身不会自动给当前账号授权。</p></div>${can("system.roles.view") ? '<a class="button" href="/system/roles">管理角色</a>' : ""}</div><div class="dialog-body"><div class="scope-description">数据范围：${catalog.data_scopes.map((scope) => esc(scope.name)).join("、")}。范围只作用于同一角色授予的操作。</div><div class="permission-tree">${[...groups].map(([name, items]) => `<details class="permission-group"><summary>${esc(name)} · ${items.length} 项</summary>${dataTable(["业务操作", "权限编码", "可委派"], items, (item) => `<tr><td>${esc(item.name)}</td><td><code>${esc(item.code)}</code></td><td>${item.grantable ? "是" : "否"}</td></tr>`)}</details>`).join("")}</div></div></section>`;
}

// 中文说明：资源授权列表视图（服务端分页）。
// 副作用：GET /resource-grants；撤销走统一确认弹窗并 DELETE /resource-grants/{id}（只删授权关系，不删资源）。
async function renderGrants(root, page) {
  const grants = await api.get(BASE + "/resource-grants?page=" + page + "&page_size=" + state.pageSize);
  const reload = () => renderGrants(root, page);
  root.innerHTML = pageHeader("权限与数据范围", "通过角色、数据范围和单个资源授权控制业务访问。") + tabs("grants") +
    `<section class="panel"><div class="panel-heading"><div><h2>单个资源授权</h2><p>仅在角色数据范围之外共享一个明确资源。</p></div>${can("system.permissions.manage") ? button("新增授权", "data-new-grant", true) : ""}</div>` +
    (grants.items.length ? dataTable(["资源类型", "资源标识", "对象类型", "对象标识", "允许动作", "操作"], grants.items, (item) => `<tr><td>${esc(RESOURCE_TYPES.find((type) => type.value === item.resource_type)?.label || item.resource_type)}</td><td><code>${esc(item.resource_id)}</code></td><td>${item.subject_type === "user" ? "用户" : "角色"}</td><td><code>${esc(item.subject_id)}</code></td><td>${esc(item.actions.map((action) => ACTIONS.find((item) => item.value === action)?.label || action).join("、"))}</td><td>${can("system.permissions.manage") ? `<button class="text-button danger" data-revoke-grant="${esc(item.id)}">撤销</button>` : "只读"}</td></tr>`) : emptyState("没有额外资源授权", "角色定义的数据范围仍然生效。")) + pagination(grants) + "</section>";
  wireList(root, grants, (change) => replaceQuery({ view: "grants", page: change.page }, root));
  $("[data-new-grant]", root)?.addEventListener("click", () => grantForm(reload).catch((error) => toast(error.message, true)));
  $$('[data-revoke-grant]', root).forEach((element) => element.addEventListener("click", () => {
    confirmAction("撤销资源授权", "撤销这条额外授权后，对方仅保留角色范围内的访问权限。资源本身不会删除。", async () => {
      await api.delete(BASE + "/resource-grants/" + element.dataset.revokeGrant);
      toast("资源授权已撤销");
      await reload();
    }, "撤销授权");
  }));
}

// 中文说明：权限变更记录视图（需 system.permissions.history）。
// 数据来源 GET /permission-history；每行可查看 before/after JSON 快照（只读弹窗）。
// 该页的分页/筛选没有走 wireList，而是自己重写 URL 参数，以保留 target_type/target_id/action 等额外筛选条件。
async function renderHistory(root, query) {
  const values = {
    view: "history", page: Number(query.get("page") || 1), q: query.get("q") || "",
    target_type: query.get("target_type") || "", target_id: query.get("target_id") || "",
    action: query.get("action") || "",
  };
  const params = new URLSearchParams({ page: values.page, page_size: state.pageSize, q: values.q });
  if (values.target_type) params.set("target_type", values.target_type);
  if (values.target_id) params.set("target_id", values.target_id);
  if (values.action) params.set("action", values.action);
  const result = await api.get(BASE + "/permission-history?" + params);
  const filterExtras = select("target_type", [{ value: "user", label: "用户" }, { value: "role", label: "角色" }, { value: "resource_grant", label: "资源授权" }], values.target_type, "全部对象").replace("<select", '<select aria-label="筛选变更对象"') +
    select("action", [{ value: "user.create", label: "创建用户" }, { value: "user.update", label: "修改用户" }, { value: "user.remove_from_workspace", label: "移除成员" }, { value: "role.create", label: "创建角色" }, { value: "role.update", label: "修改角色" }, { value: "role.delete", label: "删除角色" }, { value: "resource.grant", label: "新增资源授权" }, { value: "resource.revoke", label: "撤销资源授权" }, { value: "user.bulk_import", label: "批量导入" }], values.action, "全部动作").replace("<select", '<select aria-label="筛选变更动作"');
  const table = result.items.length ? dataTable(["变更时间", "动作", "对象", "变更摘要", "操作人", "来源", "详情"], result.items, (item) => `<tr><td>${date(item.occurred_at)}</td><td>${esc(item.action_label)}<span class="cell-secondary">${esc(item.action)}</span></td><td><span class="cell-primary">${esc(item.target.label)}</span><span class="cell-secondary">${esc(item.target.type)}</span></td><td class="wrap-cell">${esc(item.summary)}</td><td>${esc(item.actor.display_name)}<span class="cell-secondary">${esc(item.actor.username || "系统")}</span></td><td>${item.source === "bulk_import" ? "批量导入" : item.source === "manual" ? "人工操作" : "系统"}</td><td><button class="text-button" data-history-detail="${esc(item.id)}">前后快照</button></td></tr>`) : emptyState("没有匹配的权限变更", "调整对象、动作或关键字后重试。");
  root.innerHTML = pageHeader("权限与数据范围", "通过角色、数据范围和单个资源授权控制业务访问。") + tabs("history") +
    `<section class="panel">${searchBar("搜索账号、姓名或对象", values.q, filterExtras)}${values.target_id ? `<div class="active-filter">已限定对象 ${esc(values.target_id)}<button class="text-button" type="button" data-clear-target>清除</button></div>` : ""}${table}${pagination(result)}</section>`;
  const byId = new Map(result.items.map((item) => [item.id, item]));
  $$('[data-history-detail]', root).forEach((element) => element.addEventListener("click", () => {
    const item = byId.get(element.dataset.historyDetail);
    openDialog(item.action_label + " · " + item.target.label, `<div class="history-snapshots"><section><h3>变更前</h3><pre class="json-preview">${esc(JSON.stringify(item.before, null, 2) || "{}")}</pre></section><section><h3>变更后</h3><pre class="json-preview">${esc(JSON.stringify(item.after, null, 2) || "{}")}</pre></section></div>`, { wide: true });
  }));
  $("[data-search-form]", root).addEventListener("submit", (event) => {
    event.preventDefault();
    replaceQuery({ ...values, page: 1, ...Object.fromEntries(new FormData(event.currentTarget)) }, root);
  });
  $("[data-reset]", root)?.addEventListener("click", () => replaceQuery({ view: "history" }, root));
  $("[data-clear-target]", root)?.addEventListener("click", () => replaceQuery({ ...values, page: 1, target_type: "", target_id: "" }, root));
  $("[data-page-prev]", root)?.addEventListener("click", () => replaceQuery({ ...values, page: values.page - 1 }, root));
  $("[data-page-next]", root)?.addEventListener("click", () => replaceQuery({ ...values, page: values.page + 1 }, root));
}

/**
 * 中文说明：权限页入口，按 ?view 分发（catalog 默认 / grants / history）。
 * 约束：view=history 但缺少 system.permissions.history 权限时回落到权限目录，
 *   避免出现“标签不存在但内容仍渲染”的不一致状态。
 */
export async function renderPermissions(root) {
  const query = new URLSearchParams(location.search);
  const view = query.get("view") || "catalog";
  if (view === "history" && can("system.permissions.history")) return renderHistory(root, query);
  if (view === "grants") return renderGrants(root, Math.max(1, Number(query.get("page") || 1)));
  return renderCatalog(root);
}
