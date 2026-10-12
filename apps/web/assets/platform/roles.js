/**
 * 角色管理模块（路由 /system/roles）
 *
 * 位置：渲染角色列表（名称/编码、数据范围、权限数、用户数、状态）与新增/编辑/复制/删除弹窗，
 *   并使用 permissionTree 展示可勾选的功能权限树（该组件也被用户、权限相关页面复用）。
 * 依赖接口前缀：/api/v1/system（GET/POST /roles、PATCH/DELETE /roles/{id}、GET /permissions、GET /departments）。
 * 与 iframe 内 Vue 应用无关。
 *
 * 关键约束：
 * - 权限码（如 system.roles.manage、runs.view）必须与后端权限目录一致，前端不得自造；
 *   列表操作按钮与权限树的可勾选项都要按 can() / item.grantable 过滤。
 * - 内置角色（builtin，如 workspace_admin）不能删除、不能改权限与数据范围：表单里禁用相关字段，
 *   只允许改名/改说明，避免把手管管理员角色改坏导致工作区失联。
 * - 角色编码创建后不可修改（表单禁用 code）；复制角色时编码需截断并追加 -copy 以符合长度与字符集限制。
 * - 删除、停用等破坏性动作必须走 confirmAction。
 */

import { api, allPages } from "./api.js";
import { $, $$, state, can, esc, pageHeader, button, dataTable, searchBar, pagination, wireList,
  emptyState, statusBadge, field, input, select, checkboxes, openDialog, confirmAction, toast } from "./ui.js";

const BASE = "/api/v1/system";
// 中文说明：数据范围码 → 中文文案。键名与后端 data_scope 枚举一一对应，新增枚举需同步这里，否则列表显示空白。
export const SCOPE_LABELS = { self: "本人数据", department: "本部门", department_tree: "本部门及下级", custom: "指定部门", workspace: "整个工作区" };

/**
 * 中文说明：渲染按 group 分组的权限勾选树（供角色、用户等表单复用）。
 * 参数：items=后端权限目录；selected=已勾选权限码数组；disabled=整体只读（内置角色场景）。
 * 约束：item.grantable 为 false 的权限即使勾选也会被禁用——后端不允许授予的权限不要在前端放行。
 */
export function permissionTree(items, selected = [], disabled = false) {
  const groups = new Map();
  items.forEach((item) => {
    const list = groups.get(item.group) || [];
    list.push(item);
    groups.set(item.group, list);
  });
  return `<div class="permission-tree">${[...groups].map(([name, permissions]) => `<details class="permission-group" open><summary>${esc(name)} <span class="muted">${permissions.length} 项</span></summary><div class="checkbox-options">${permissions.map((item) => `<label data-permission-label><input type="checkbox" name="permissions" value="${esc(item.code)}" ${selected.includes(item.code) ? "checked" : ""} ${disabled || !item.grantable ? "disabled" : ""}><span>${esc(item.name)}<small class="cell-secondary">${esc(item.code)}</small></span></label>`).join("")}</div></details>`).join("")}</div>`;
}

/**
 * 中文说明：打开角色新增/编辑/复制弹窗。
 * 参数：record=被编辑或复制的角色（null 表示新增）；reload=保存成功后的列表刷新回调；clone=true 表示复制模式。
 * 副作用：并行加载权限目录与全部部门（allPages 会翻页直到拿全），保存时 POST/PATCH /api/v1/system/roles，
 *   成功后 toast 提示并触发 reload；校验失败的错误会显示在弹窗内且不关闭。
 * 失败表现：目录接口异常直接抛出（调用方 toast 提示），弹窗不打开，避免出现没有权限可选的半成品表单。
 */
export async function openRoleForm(record, reload, clone = false) {
  const [catalog, departments] = await Promise.all([api.get(BASE + "/permissions"), allPages(BASE + "/departments")]);
  const edit = Boolean(record) && !clone;
  const locked = edit && record.code === "workspace_admin";
  const role = record || { name: "", code: "", description: "", data_scope: "self", permissions: [], department_ids: [], enabled: true };
  const html = `<div class="form-grid">` +
    field("角色名称", input("name", clone ? role.name + "（副本）" : role.name, 'required maxlength="160"')) +
    field("角色编码", input("code", clone ? role.code.slice(0, 70) + "-copy" : role.code, `required maxlength="80" pattern="[A-Za-z0-9_.\\x2D]+" ${edit ? "disabled" : ""}`), "创建后不可修改。") +
    field("用途说明", `<textarea name="description" maxlength="1000">${esc(role.description)}</textarea>`, "", true) +
    field("数据访问范围", select("data_scope", catalog.data_scopes.map((scope) => ({ value: scope.code, label: scope.name })), role.data_scope, null).replace("<select", locked ? "<select disabled" : "<select")) +
    field("角色状态", select("enabled", [{ value: "true", label: "启用" }, { value: "false", label: "停用" }], String(role.enabled), null).replace("<select", locked ? "<select disabled" : "<select")) +
    `<div class="form-field wide" data-custom-departments ${role.data_scope === "custom" ? "" : "hidden"}><span>允许访问的部门</span>${checkboxes("department_ids", departments.map((row) => ({ value: row.id, label: row.name })), role.department_ids)}</div></div>` +
    `<h3 class="form-section">功能权限</h3><p class="scope-description">功能权限决定能做什么，数据范围决定能对哪些记录操作。AI 对话与直接接口使用相同规则。${locked ? "内置管理员权限不可修改；可以复制为自定义角色。" : ""}</p>` +
    `<label class="search-input"><input data-permission-filter placeholder="筛选权限名称或编码" aria-label="筛选权限"></label>` + permissionTree(catalog.items, role.permissions, locked);
  openDialog(edit ? "编辑角色 · " + role.name : clone ? "复制角色" : "新增角色", html, {
    wide: true,
    onReady(dialog) {
      $('[name="data_scope"]', dialog)?.addEventListener("change", (event) => { $("[data-custom-departments]", dialog).hidden = event.target.value !== "custom"; });
      // 权限筛选只按名称/编码做前端过滤（不重新请求），因此用 hidden 隐藏而不是重建 DOM
      $("[data-permission-filter]", dialog).addEventListener("input", (event) => {
        const search = event.target.value.toLowerCase().trim();
        $$('[data-permission-label]', dialog).forEach((label) => { label.hidden = !label.textContent.toLowerCase().includes(search); });
      });
    },
    onSave: async (form) => {
      const payload = { name: form.get("name"), description: form.get("description") };
      // 内置角色只提交名称与说明：权限/数据范围/状态由后端固定，提交了也会被拒
      if (!locked) Object.assign(payload, {
        permissions: form.getAll("permissions"), data_scope: form.get("data_scope"),
        // 非 custom 范围必须显式传空数组，否则会残留上一次提交的指定部门
        department_ids: form.get("data_scope") === "custom" ? form.getAll("department_ids") : [],
        enabled: form.get("enabled") === "true",
      });
      // 编辑带 version 做并发校验，避免覆盖他人同时的修改
      if (edit) await api.patch(BASE + "/roles/" + role.id, { version: role.version, ...payload });
      else await api.post(BASE + "/roles", { ...payload, code: form.get("code") });
      toast(edit ? "角色已更新，后续请求将使用新权限" : "角色已创建");
      await reload();
    },
  });
}

/**
 * 中文说明：角色列表页渲染入口（服务端分页 + 关键字搜索）。
 * 参数：filters 为 { page, q }，翻页/查询时以补丁形式合并回本函数递归重绘。
 * 副作用：GET /api/v1/system/roles；重写 root 内容并重新绑定列表与行内操作事件。
 * 失败表现：加载失败抛出由 main.js 展示错误页；行内操作（编辑/复制/删除）失败只 toast，不打断当前列表。
 */
export async function renderRoles(root, filters = { page: 1, q: "" }) {
  const params = new URLSearchParams({ page: filters.page, page_size: state.pageSize, q: filters.q });
  const result = await api.get(BASE + "/roles?" + params);
  const reload = () => renderRoles(root, filters);
  root.innerHTML = pageHeader("角色管理", "按岗位职责组合功能权限与数据范围，再将角色分配给用户。",
    can("system.roles.manage") ? button("新增角色", 'data-new-role data-testid="create-role"', true) : "") +
    `<section class="panel">${searchBar("搜索角色名称", filters.q)}` +
    (result.items.length ? dataTable(["角色名称 / 编码", "数据范围", "权限数", "用户数", "状态", "操作"], result.items, (role) => `<tr><td><span class="cell-primary">${esc(role.name)}</span><span class="cell-secondary">${esc(role.code)}${role.builtin ? " · 内置" : ""}</span></td><td>${esc(SCOPE_LABELS[role.data_scope])}</td><td>${role.permissions.length}</td><td>${role.member_count}</td><td>${statusBadge(role.enabled)}</td><td><div class="row-actions">${can("system.roles.manage") ? `<button class="text-button" data-edit-role="${esc(role.id)}">编辑</button><button class="text-button" data-clone-role="${esc(role.id)}">复制</button>${!role.builtin ? `<button class="text-button danger" data-delete-role="${esc(role.id)}">删除</button>` : ""}` : "<span class='muted'>只读</span>"}</div></td></tr>`) : emptyState("没有符合条件的角色", "调整搜索条件或建立自定义角色。")) + pagination(result) + "</section>";
  wireList(root, result, (change) => renderRoles(root, { ...filters, ...change }).catch((error) => toast(error.message, true)));
  const byId = new Map(result.items.map((role) => [role.id, role]));
  // 行内按钮只带 id，具体数据从本次响应里查：避免把整个对象塞进 DOM 属性，也防止翻页后拿到过期数据
  $("[data-new-role]", root)?.addEventListener("click", () => openRoleForm(null, reload).catch((error) => toast(error.message, true)));
  $$('[data-edit-role]', root).forEach((element) => element.addEventListener("click", () => openRoleForm(byId.get(element.dataset.editRole), reload).catch((error) => toast(error.message, true))));
  $$('[data-clone-role]', root).forEach((element) => element.addEventListener("click", () => openRoleForm(byId.get(element.dataset.cloneRole), reload, true).catch((error) => toast(error.message, true))));
  $$('[data-delete-role]', root).forEach((element) => element.addEventListener("click", () => {
    const role = byId.get(element.dataset.deleteRole);
    // 删除属不可逆操作，必须走统一确认弹窗；仍被用户使用或存在资源授权的角色后端会拒绝
    confirmAction("删除角色", `确认删除“${role.name}”？已分配给用户或存在资源授权的角色不会被删除。`, async () => {
      await api.delete(BASE + "/roles/" + role.id);
      toast("角色已删除");
      await reload();
    }, "删除角色");
  }));
}
