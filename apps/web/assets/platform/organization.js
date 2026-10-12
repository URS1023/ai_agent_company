/**
 * 组织与岗位管理模块（路由 /system/departments 与 /system/positions）
 *
 * 位置：同一份实现同时服务“部门”和“岗位”两种 kind，渲染组织树状列表、当前组织/工作区提示条与
 *   部门/岗位的新增编辑弹窗；部门以缩进和层级符号体现组织结构。
 * 依赖接口前缀：/api/v1/system（GET/POST /departments、/positions、PATCH/DELETE /{kind}/{id}、
 *   GET /organization 用于页头提示、GET /users 用于负责人候选、GET /permissions 由角色页使用）。
 * 与 iframe 内 Vue 应用无关。
 *
 * 关键约束：
 * - 上级部门选项必须排除自身及其所有后代（invalidParents 计算），否则会形成环导致组织树无法展开。
 * - 编码（code）创建后不可修改；部门与岗位的删除必须走统一确认弹窗，且后端会拒绝仍被引用的记录。
 * - 岗位不隐含任何角色权限：岗位只表达职责，授权一律通过角色完成，页面上不要暗示相反语义。
 * - 负责人候选只列 status === "active" 的用户；读取用户列表需要 system.users.view，无权限时退化为空列表而不是报错。
 */

import { api, allPages } from "./api.js";
import { $, $$, state, can, esc, pageHeader, button, dataTable, searchBar, pagination, wireList,
  emptyState, statusBadge, field, input, select, openDialog, confirmAction, toast } from "./ui.js";

const BASE = "/api/v1/system";

// 中文说明：把扁平部门列表整理成带 depth（层级）的行，并按「排序值 + 编码」的完整父链路径排序。
// 用 visited 集合与 100 层上限保护：历史脏数据（父子互指）不能让渲染陷入死循环。
function treeRows(departments) {
  const byId = new Map(departments.map((item) => [item.id, item]));
  return departments.map((item) => {
    let depth = 0, parent = item.parent_id;
    const visited = new Set([item.id]);
    while (parent && byId.has(parent) && !visited.has(parent)) {
      visited.add(parent);
      depth++;
      parent = byId.get(parent).parent_id;
    }
    return { ...item, depth };
  }).sort((a, b) => {
    const path = (item) => {
      let current = item;
      const chain = [];
      while (current && chain.length < 100) { chain.unshift(String(current.sort_order).padStart(6, "0") + current.code); current = byId.get(current.parent_id); }
      return chain.join("/");
    };
    return path(a).localeCompare(path(b));
  });
}

/**
 * 中文说明：打开部门/岗位表单弹窗。
 * 参数：kind="departments" | "positions"；record=被编辑记录（null 为新增）；reload=保存后的刷新回调。
 * 副作用：POST/PATCH /api/v1/system/{kind}，成功后 toast + reload；版本号随更新一起提交用于并发校验。
 * 失败表现：保存失败在弹窗内显示后端消息；加载部门/用户目录失败则整体抛出，由调用方 toast。
 */
async function organizationForm(kind, record, reload) {
  const isDepartment = kind === "departments";
  const label = isDepartment ? "部门" : "岗位";
  // 部门表单才需要候选数据：岗位没有上级、也不设负责人
  const [departments, members] = isDepartment ? await Promise.all([
    allPages(BASE + "/departments"), can("system.users.view") ? allPages(BASE + "/users") : [],
  ]) : [[], []];
  const item = record || { enabled: true, sort_order: 0 };
  // 反复向下扩散标记后代：把所有以自身为祖先的节点都排除在“上级部门”候选之外，防止出现环
  const invalidParents = new Set(record ? [record.id] : []);
  for (let count = 0; count < departments.length; count++) {
    departments.forEach((department) => { if (invalidParents.has(department.parent_id)) invalidParents.add(department.id); });
  }
  const content = `<div class="form-grid">` +
    field(label + "名称", input("name", item.name, 'required maxlength="160"')) +
    field(label + "编码", input("code", item.code, `required maxlength="80" pattern="[A-Za-z0-9_.\\x2D]+" ${record ? "disabled" : ""}`), "创建后不可修改。") +
    (isDepartment ?
      field("上级部门", select("parent_id", treeRows(departments).filter((row) => row.enabled && !invalidParents.has(row.id)).map((row) => ({ value: row.id, label: "　".repeat(row.depth) + row.name })), item.parent_id, "根部门")) +
      field("负责人", select("leader_user_id", members.filter((row) => row.status === "active").map((row) => ({ value: row.id, label: row.display_name + " · " + row.username })), item.leader_user_id, "未设置")) +
      field("排序值", input("sort_order", item.sort_order, 'type="number" min="0" max="100000" required')) :
      field("职责说明", `<textarea name="description" maxlength="1000">${esc(item.description)}</textarea>`, "岗位不会自动赋予角色权限。", true)) +
    field("状态", select("enabled", [{ value: "true", label: "启用" }, { value: "false", label: "停用" }], String(item.enabled), null)) + "</div>";
  openDialog((record ? "编辑" : "新增") + label, content, {
    onSave: async (form) => {
      const values = Object.fromEntries(form);
      const payload = { name: values.name, enabled: values.enabled === "true" };
      if (isDepartment) Object.assign(payload, { parent_id: values.parent_id || null, leader_user_id: values.leader_user_id || null, sort_order: Number(values.sort_order) });
      else payload.description = values.description;
      if (record) await api.patch(BASE + "/" + kind + "/" + record.id, { version: record.version, ...payload });
      else await api.post(BASE + "/" + kind, { ...payload, code: values.code });
      toast(label + "已保存");
      await reload();
    },
  });
}

/**
 * 中文说明：部门/岗位列表页渲染入口（kind 决定数据源与列定义）。
 * 参数：filters={page,q} 由翻页/搜索补丁合并后递归调用。
 * 副作用：GET /api/v1/system/{kind}（列表）与 /organization（页头当前组织提示）；
 *   重写 root 并绑定行内操作；权限码 system.{kind}.manage 决定是否显示新增/编辑/删除。
 * 失败表现：列表加载失败抛出由 main.js 展示错误页；行内操作失败只 toast。
 */
export async function renderOrganization(root, kind, filters = { page: 1, q: "" }) {
  const isDepartment = kind === "departments";
  const label = isDepartment ? "部门" : "岗位";
  const permission = "system." + kind + ".manage";
  const result = await api.get(BASE + "/" + kind + "?" + new URLSearchParams({ page: filters.page, page_size: state.pageSize, q: filters.q }));
  const organization = isDepartment ? await api.get(BASE + "/organization") : null;
  const rows = isDepartment ? treeRows(result.items) : result.items;
  const reload = () => renderOrganization(root, kind, filters);
  root.innerHTML = pageHeader(isDepartment ? "组织与部门" : "岗位管理", isDepartment ? "维护组织结构、部门负责人和成员归属。上级关系不能形成循环。" : "维护岗位职责，在用户管理中关联成员；岗位与角色权限相互独立。",
    can(permission) ? button("新增" + label, 'data-new-record data-testid="create-organization-record"', true) : "") +
    (organization ? `<div class="notice-inline">当前组织：${esc(organization.organization.name)}<span>／</span>工作区：${esc(organization.workspace.name)}</div>` : "") +
    `<section class="panel">${searchBar("搜索" + label + "名称", filters.q)}` +
    (rows.length ? dataTable(isDepartment ? ["部门名称", "编码", "上级部门", "排序", "状态", "操作"] : ["岗位名称", "编码", "职责说明", "状态", "操作"], rows, (item) => `<tr><td><span class="cell-primary" ${isDepartment ? `style="padding-left:${Math.min(item.depth, 8) * 15}px"` : ""}>${isDepartment && item.depth ? "└ " : ""}${esc(item.name)}</span></td><td>${esc(item.code)}</td>${isDepartment ? `<td>${esc(result.items.find((row) => row.id === item.parent_id)?.name || (item.parent_id ? "其他分页中的上级" : "根部门"))}</td><td>${item.sort_order}</td>` : `<td>${esc(item.description || "—")}</td>`}<td>${statusBadge(item.enabled)}</td><td><div class="row-actions">${can(permission) ? `<button class="text-button" data-edit-record="${esc(item.id)}">编辑</button><button class="text-button danger" data-delete-record="${esc(item.id)}">删除</button>` : "<span class='muted'>只读</span>"}</div></td></tr>`) : emptyState("尚无符合条件的" + label, "调整搜索条件，或创建" + label + "后为成员设置归属。")) + pagination(result) + "</section>";
  wireList(root, result, (change) => renderOrganization(root, kind, { ...filters, ...change }).catch((error) => toast(error.message, true)));
  $("[data-new-record]", root)?.addEventListener("click", () => organizationForm(kind, null, reload).catch((error) => toast(error.message, true)));
  $$('[data-edit-record]', root).forEach((element) => element.addEventListener("click", () => organizationForm(kind, result.items.find((row) => row.id === element.dataset.editRecord), reload).catch((error) => toast(error.message, true))));
  $$('[data-delete-record]', root).forEach((element) => element.addEventListener("click", () => {
    const record = result.items.find((row) => row.id === element.dataset.deleteRecord);
    // 删除走统一确认弹窗；仍关联成员/子部门/授权范围时后端会拒绝，错误由弹窗内展示
    confirmAction("删除" + label, `确认删除“${record.name}”？仍关联成员、子部门或授权范围的记录不会被删除。`, async () => {
      await api.delete(BASE + "/" + kind + "/" + record.id);
      toast(label + "已删除");
      await reload();
    }, "删除" + label);
  }));
}
