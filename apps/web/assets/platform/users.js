/**
 * 用户管理模块（路由 /system/users 与 /system/users/{id}）
 *
 * 位置：渲染工作区成员列表（部门/角色/状态/创建时间）、用户详情页，以及新增、编辑、重置密码、
 *   有效权限查看与 Excel 批量导入（先校验预览、再整批写入）等弹窗。
 * 依赖接口前缀：/api/v1/system（GET/POST/PATCH/DELETE /users、POST /users/{id}/password-reset、
 *   GET /users/{id}/permissions、POST /users/import-preview 与 /users/import、
 *   GET /users/import-template 与 /users/export）；
 *   表单选项还依赖 /roles、/departments、/positions（各自按对应 view 权限决定是否加载）。
 * 与 iframe 内 Vue 应用无关。
 *
 * 关键约束：
 * - 权限码必须与后端目录一致：system.users.manage / reset_password / import / export /
 *   assign_roles、system.roles.view、system.departments.view、system.positions.view、system.permissions.view。
 *   「新增用户」要求同时具备 manage 与 assign_roles；缺 assign_roles 时表单不渲染角色勾选区，提交也不带 role_ids。
 * - 登录账号（username）创建后不可修改；临时密码规则与后端一致（≥10 位含字母数字，72 小时内有效，首登须改）。
 * - 批量导入必须走“预览校验 + checksum 预期值”两段式：确认提交时回传 expected_checksum，
 *   避免用户在校验后文件被替换导致写入内容与预览不一致；任一数据库冲突整批回滚。
 * - 移除成员只是解除本工作区成员关系与角色，保留账号与历史记录，必须走统一确认弹窗。
 * - 停用账号会撤销其旧会话，仅对有权限管理的账号生效（后端判定）。
 */

import { api, allPages } from "./api.js";
import { $, $$, state, can, esc, date, pageHeader, button, dataTable, searchBar, pagination,
  wireList, emptyState, statusBadge, field, input, select, checkboxes, openDialog, confirmAction, toast } from "./ui.js";

const BASE = "/api/v1/system";

// 中文说明：加载用户表单所需的角色/部门/岗位选项。
// 每类数据都先判断对应 view 权限：无权限时返回空数组而不是请求失败，表单退化为没有候选项。
async function userOptions() {
  const [roles, departments, positions] = await Promise.all([
    can("system.roles.view") ? allPages(BASE + "/roles") : [],
    can("system.departments.view") ? allPages(BASE + "/departments") : [],
    can("system.positions.view") ? allPages(BASE + "/positions") : [],
  ]);
  return { roles, departments, positions };
}

/**
 * 中文说明：用户新增/编辑弹窗（也被用户详情页复用）。
 * 参数：record=被编辑用户（null 为新增）；reload=保存成功后的刷新回调。
 * 副作用：POST /users 或 PATCH /users/{id}（带 version 并发校验），成功后 toast 并 reload。
 * 注意：编辑时只提交发生变化的字段（changed 过滤），避免把未展示的后端字段用空值覆盖掉。
 */
export async function openUserForm(record, reload) {
  const { roles, departments, positions } = await userOptions();
  const edit = Boolean(record);
  const user = record || { role_ids: [], position_ids: [] };
  const roleChoices = roles.map((role) => ({ value: role.id, label: role.name, disabled: !role.enabled }));
  const html = `<div class="form-grid">` +
    field("登录账号", input("username", user.username, `required minlength="3" maxlength="80" pattern="[A-Za-z0-9_.\\x2D]+" autocomplete="off" ${edit ? "disabled" : ""}`), "字母、数字、下划线、点或短横线；不区分大小写。") +
    field("显示姓名", input("display_name", user.display_name, 'required maxlength="160"')) +
    (!edit ? field("初始临时密码", input("password", "", 'type="password" required minlength="10" maxlength="256" autocomplete="new-password"'), "至少 10 位，包含字母和数字。72 小时内有效，首次登录须修改。", true) : "") +
    field("所属部门", select("department_id", departments.map((row) => ({ value: row.id, label: row.name, disabled: !row.enabled })), user.department_id, "未分配部门")) +
    field("员工编号", input("employee_code", user.employee_code, 'maxlength="80"')) +
    field("联系邮箱", input("email", user.email, 'type="email" maxlength="254"')) +
    field("联系电话", input("phone", user.phone, 'type="tel" maxlength="40"')) +
    (edit ? field("账号状态", select("status", [{ value: "active", label: "启用" }, { value: "disabled", label: "停用" }], user.status, null), "停用账号会撤销旧会话；仅影响有权管理的账号。", true) : "") +
    field("岗位", checkboxes("position_ids", positions.map((row) => ({ value: row.id, label: row.name, disabled: !row.enabled })), user.position_ids), "岗位用于组织管理，不自动赋予业务权限。", true) +
    (can("system.users.assign_roles") ? field("角色分配", checkboxes("role_ids", roleChoices, user.role_ids), "未选择角色时，用户没有业务访问权限。", true) : "") + `</div>`;
  openDialog(edit ? "编辑用户 · " + user.display_name : "新增用户", html, {
    onSave: async (form) => {
      const values = Object.fromEntries(form);
      const payload = {
        display_name: values.display_name, department_id: values.department_id || null,
        employee_code: values.employee_code, email: values.email, phone: values.phone,
        position_ids: form.getAll("position_ids"),
      };
      if (can("system.users.assign_roles")) payload.role_ids = form.getAll("role_ids");
      if (edit) {
        payload.status = values.status;
        const changed = Object.fromEntries(Object.entries(payload).filter(([key, value]) => JSON.stringify(value) !== JSON.stringify(user[key])));
        if (Object.keys(changed).length) await api.patch(BASE + "/users/" + user.id, { version: user.version, ...changed });
      } else {
        await api.post(BASE + "/users", { ...payload, username: values.username, password: values.password });
      }
      toast(edit ? "用户资料已保存" : "用户已创建，首次登录须修改临时密码");
      await reload();
    },
  });
}

// 中文说明：重置密码弹窗（POST /users/{id}/password-reset）。
// 副作用：撤销该用户全部旧会话，必须提示“重置并撤销旧会话”这一后果；有效小时数限制 1–168（与后端一致）。
function openPasswordReset(user) {
  openDialog("重置密码 · " + user.display_name,
    `<div class="form-grid">${field("新的临时密码", input("new_password", "", 'type="password" required minlength="10" maxlength="256" autocomplete="new-password"'), "至少 10 位，包含字母和数字；旧会话会被撤销。", true)}${field("有效小时数", input("expires_in_hours", 72, 'type="number" min="1" max="168" required'), "1–168 小时，用户需在有效期内登录并修改密码。", true)}</div>`, {
      saveLabel: "重置并撤销旧会话",
      onSave: async (form) => {
        await api.post(BASE + "/users/" + user.id + "/password-reset", {
          new_password: form.get("new_password"), expires_in_hours: Number(form.get("expires_in_hours")),
        });
        toast("临时密码已重置，旧会话已撤销");
      },
    });
}

// 中文说明：查看某用户的“有效权限”（后端按角色合并后的最终结果，只读）。
// 这是排查“为什么他能访问”的权威视图：数据范围文案与后端枚举一一对应，不要在前端自行推断。
async function showEffectivePermissions(user) {
  const result = await api.get(BASE + "/users/" + user.id + "/permissions");
  openDialog("有效权限 · " + user.display_name,
    `<div class="notice-inline">${result.active ? "账号启用" : "账号已停用，不具备有效权限"} · ${result.permissions.length} 项有效操作权限</div>` +
    `<div class="permission-tree">${result.sources.map((source) => `<details class="permission-group" open><summary>${esc(source.name)} · ${esc(({ self: "本人", department: "本部门", department_tree: "部门及下级", custom: "指定部门", workspace: "工作区" })[source.data_scope])}</summary><div class="json-preview">${source.permissions.map(esc).join("\n")}</div></details>`).join("") || emptyState("没有角色授权", "为用户分配角色后，才可使用相应业务功能。")}</div>`, { wide: true });
}

// 中文说明：统一绑定列表/详情页的行内动作按钮（编辑、重置密码、查看权限、移除成员）。
// 触发条件：data-edit-user / data-password-user / data-permissions-user / data-remove-user 对应的 id。
// 失败处理：打开弹窗失败只 toast；移除成员走统一确认弹窗（保留账号与历史记录，仅解除本工作区成员关系）。
function wireUserActions(root, items, reload) {
  const byId = new Map(items.map((user) => [user.id, user]));
  $$('[data-edit-user]', root).forEach((element) => element.addEventListener("click", () => openUserForm(byId.get(element.dataset.editUser), reload).catch((error) => toast(error.message, true))));
  $$('[data-password-user]', root).forEach((element) => element.addEventListener("click", () => openPasswordReset(byId.get(element.dataset.passwordUser))));
  $$('[data-permissions-user]', root).forEach((element) => element.addEventListener("click", () => showEffectivePermissions(byId.get(element.dataset.permissionsUser)).catch((error) => toast(error.message, true))));
  $$('[data-remove-user]', root).forEach((element) => element.addEventListener("click", () => {
    const user = byId.get(element.dataset.removeUser);
    confirmAction("移除工作区成员", `确认移除“${user.display_name}”？将撤销本工作区的成员关系和角色，保留账号与历史记录。`, async () => {
      await api.delete(BASE + "/users/" + user.id);
      toast("成员已从本工作区移除");
      await reload();
    }, "移除成员");
  }));
}

// 中文说明：导入校验结果内容。valid=false 时列出逐行错误且明确“未写入任何数据”；
// valid=true 时给出前 12 行预览与整批回滚说明，让用户在确认前能核对。
function importResultContent(result) {
  const actionLabels = { create: "新增", update: "更新", unchanged: "不变", invalid: "错误" };
  const summary = `<div class="import-summary" role="status"><span><strong>${result.total_rows}</strong> 数据行</span><span><strong>${result.create_count}</strong> 新增</span><span><strong>${result.update_count}</strong> 更新</span><span><strong>${result.unchanged_count}</strong> 不变</span></div>`;
  if (!result.valid) {
    const issues = result.issues.length ? dataTable(["Excel 行", "字段", "修正说明"], result.issues, (item) => `<tr><td>${item.row}</td><td>${esc(item.field)}</td><td class="wrap-cell">${esc(item.message)}</td></tr>`) : "";
    return summary + `<div class="notice-inline import-invalid">文件未通过校验，未写入任何数据。请修正后重新选择文件。</div>${issues}`;
  }
  const rows = result.preview.length ? dataTable(["行", "登录账号", "显示姓名", "部门", "角色", "动作"], result.preview.slice(0, 12), (item) => `<tr><td>${item.row}</td><td><code>${esc(item.username)}</code></td><td>${esc(item.display_name)}</td><td>${esc(item.department_code || "—")}</td><td class="wrap-cell">${esc(item.role_codes.join("、") || "未分配")}</td><td>${esc(actionLabels[item.action] || item.action)}</td></tr>`) : "";
  return summary + `<div class="notice-inline">已校验全部数据。确认后整批写入；任一数据库冲突都会回滚全部行。</div>${rows}`;
}

// 中文说明：用校验结果再开一次弹窗（确认导入 / 展示错误）。
// 必须 setTimeout 延后：openDialog 是单例，直接在 onSave 回调内同步打开会被前一个弹窗的关闭流程清掉。
// 副作用：确认时 POST /users/import 并回传 expected_checksum，后端据此校验文件未被替换。
function showImportResult(result, file, updateExisting, reload) {
  const valid = result.valid;
  setTimeout(() => openDialog(valid ? "确认用户导入" : "用户导入校验结果", importResultContent(result), valid ? {
    wide: true,
    saveLabel: "确认导入",
    onSave: async () => {
      const payload = new FormData();
      payload.append("file", file);
      payload.append("update_existing", String(updateExisting));
      payload.append("expected_checksum", result.checksum);
      const imported = await api.upload(BASE + "/users/import", payload);
      toast(imported.message);
      await reload();
    },
  } : { wide: true }), 0);
}

// 中文说明：批量导入弹窗。第一步只做校验（POST /users/import-preview），确认写入在 showImportResult 里完成。
// 约束：仅接受系统模板的 .xlsx（≤5MB、≤1000 行）；勾选“更新已有用户”时密码列必须留空（后端强制）。
function openUserImport(reload) {
  const content = `<div class="form-grid">${field("Excel 文件", '<input type="file" name="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" required>', "仅支持系统模板格式，最大 5 MB、1000 行。", true)}${field("已有用户", '<label class="check-line"><input type="checkbox" name="update_existing" value="true"><span>按登录账号更新当前工作区已有用户</span></label>', "更新时密码列必须留空；账号状态、角色、部门、岗位和联系方式按文件替换。", true)}</div><div class="import-template-line"><button class="text-button" type="button" data-download-user-template>下载当前工作区模板</button><span>模板内含字段说明及可用部门编码。</span></div>`;
  openDialog("批量导入用户", content, {
    wide: true,
    saveLabel: "校验文件",
    onReady(dialog) {
      $("[data-download-user-template]", dialog).addEventListener("click", async () => {
        try { await api.download(BASE + "/users/import-template", "用户导入模板.xlsx"); }
        catch (error) { toast(error.message, true); }
      });
    },
    onSave: async (form) => {
      const file = form.get("file");
      if (!(file instanceof File) || !file.size) throw new Error("请选择 .xlsx 用户文件");
      const updateExisting = form.get("update_existing") === "true";
      const payload = new FormData();
      payload.append("file", file);
      payload.append("update_existing", String(updateExisting));
      const result = await api.upload(BASE + "/users/import-preview", payload);
      showImportResult(result, file, updateExisting, reload);
    },
  });
}

/**
 * 中文说明：用户列表页渲染入口（服务端分页，支持关键字与状态筛选）。
 * 参数：filters={page,q,status}；翻页/查询以补丁形式合并后递归重绘。
 * 副作用：GET /api/v1/system/users；重写 root 并绑定工具栏/行内动作；
 *   导出按钮会触发下载（GET /users/export，导出文件不含临时密码列）。
 * 失败表现：列表加载失败抛出由 main.js 渲染错误页；导出/导入等操作失败只 toast。
 */
export async function renderUsers(root, filters = { page: 1, q: "", status: "" }) {
  const params = new URLSearchParams({ page: filters.page, page_size: state.pageSize, q: filters.q || "" });
  if (filters.status) params.set("status", filters.status);
  const result = await api.get(BASE + "/users?" + params);
  const reload = () => renderUsers(root, filters);
  const actions = `${can("system.users.import") ? button("导入", 'data-import-users data-testid="import-users"') : ""}${can("system.users.export") ? button("导出", 'data-export-users data-testid="export-users"') : ""}${can("system.users.manage") && can("system.users.assign_roles") ? button("新增用户", 'data-new-user data-testid="create-user"', true) : ""}`;
  root.innerHTML = pageHeader("用户管理", "管理工作区成员、组织归属和角色。账号权限在服务端统一生效。", actions) +
    `<section class="panel">${searchBar("搜索姓名或登录账号", filters.q, select("status", [{ value: "active", label: "启用" }, { value: "disabled", label: "停用" }], filters.status, "全部状态").replace("<select", '<select aria-label="筛选用户状态"'))}` +
    (result.items.length ? dataTable(["姓名 / 账号", "部门", "角色", "状态", "创建时间", "操作"], result.items, (user) => `<tr><td><a class="cell-primary" href="/system/users/${esc(user.id)}">${esc(user.display_name)}</a><span class="cell-secondary">${esc(user.username)}</span></td><td>${esc(user.department_name || "未分配")}</td><td>${esc(user.role_names.join("、") || "未授权")}</td><td>${statusBadge(user.status)}</td><td>${date(user.created_at)}</td><td><div class="row-actions">${can("system.users.manage") ? `<button class="text-button" data-edit-user="${esc(user.id)}">编辑</button>` : ""}${can("system.users.reset_password") ? `<button class="text-button" data-password-user="${esc(user.id)}">重置密码</button>` : ""}${can("system.permissions.view") ? `<button class="text-button" data-permissions-user="${esc(user.id)}">权限</button>` : ""}${can("system.users.manage") && can("system.users.assign_roles") ? `<button class="text-button danger" data-remove-user="${esc(user.id)}">移除</button>` : ""}</div></td></tr>`) : emptyState("没有符合条件的用户", "调整查询条件，或新增工作区成员。")) + pagination(result) + "</section>";
  wireList(root, result, (change) => renderUsers(root, { ...filters, ...change }).catch((error) => toast(error.message, true)));
  $("[data-new-user]", root)?.addEventListener("click", () => openUserForm(null, reload).catch((error) => toast(error.message, true)));
  $("[data-import-users]", root)?.addEventListener("click", () => openUserImport(reload));
  $("[data-export-users]", root)?.addEventListener("click", async () => {
    try {
      const exportParams = new URLSearchParams({ q: filters.q || "" });
      if (filters.status) exportParams.set("status", filters.status);
      await api.download(BASE + "/users/export?" + exportParams, "工作区用户.xlsx");
      toast("用户文件已导出；临时密码列保持为空");
    } catch (error) { toast(error.message, true); }
  });
  wireUserActions(root, result.items, reload);
}

/**
 * 中文说明：用户详情页（路由 /system/users/{id}）。
 * 副作用：GET /users/{id}；重写 root，并复用 wireUserActions 绑定编辑/重置密码/移除等动作。
 * 权限码：system.permissions.history 决定是否给出“权限记录”入口，
 *   system.users.manage 决定是否显示“编辑用户”。
 */
export async function renderUserDetail(root, id) {
  const user = await api.get(BASE + "/users/" + encodeURIComponent(id));
  const entries = [
    ["登录账号", user.username], ["显示姓名", user.display_name], ["所属部门", user.department_name || "未分配"],
    ["员工编号", user.employee_code || "—"], ["联系邮箱", user.email || "—"], ["联系电话", user.phone || "—"],
    ["角色", user.role_names.join("、") || "未授权"], ["临时密码", user.must_change_password ? "登录后须修改" : "不要求修改"],
    ["创建时间", date(user.created_at)], ["最后修改", date(user.updated_at)],
  ];
  root.innerHTML = pageHeader(user.display_name, "用户资料与当前工作区身份", `<a class="button" href="/system/users">返回列表</a>${can("system.permissions.history") ? `<a class="button" href="/system/permissions?view=history&target_type=user&target_id=${esc(user.id)}">权限记录</a>` : ""}${can("system.users.manage") ? button("编辑用户", `data-edit-user="${esc(user.id)}"`, true) : ""}`) +
    `<section class="panel"><div class="panel-heading"><h2>成员资料</h2>${statusBadge(user.status)}</div><div class="dialog-body"><dl class="detail-grid">${entries.map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join("")}</dl></div></section>`;
  wireUserActions(root, [user], () => renderUserDetail(root, id));
}
