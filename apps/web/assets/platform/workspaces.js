/**
 * 组织与工作区管理模块（路由 /system/workspaces，?tab=workspaces|organizations|members）
 *
 * 位置：渲染三个标签页——工作区列表（进入/编辑/归档恢复）、组织列表（编辑/归档恢复）、成员调动；
 *   顶部状态条展示当前工作区/组织数量与当前成员数。属于租户边界管理，影响所有其他模块的数据可见范围。
 * 依赖接口前缀：/api/v1/system（GET/POST/PATCH /organizations、/workspaces、POST /workspaces/member-transfer、GET /users）。
 * 与 iframe 内 Vue 应用无关；但切换工作区后 iframe 内的 analytics 也会随 X-Workspace-Id 变化。
 *
 * 关键约束：
 * - 管理动作统一要求 system.workspaces.manage 权限码（与后端目录一致）；无权限时列表只读且不显示按钮。
 * - 归档工作区会让成员无法登录，必须走统一确认弹窗，并提示“归档组织前必须先归档全部工作区”的后端前置条件。
 * - 所有 PATCH 都要回传 version 做并发控制；切换工作区必须调用 api.setWorkspace 再整页跳转，
 *   直接改 localStorage 不会刷新已加载的 state（权限/导航仍是旧工作区的）。
 * - 成员调动有 copy/move 两种语义：copy 保留当前工作区成员身份，move 会移出，文案与后端行为必须一致。
 */

import { api } from "./api.js";
import {
  $, $$, can, esc, date, pageHeader, button, dataTable, emptyState,
  statusBadge, field, input, select, openDialog, confirmAction, toast,
} from "./ui.js";

const BASE = "/api/v1/system";
// 中文说明：标签页定义（键名即 URL 的 tab 参数值，需与 selectedTab 的校验保持一致）
const TABS = [["workspaces", "工作区"], ["organizations", "组织"], ["members", "成员调动"]];

// 中文说明：读取当前标签页；非法值一律回落到“工作区”，避免手改 URL 出现空白页。
function selectedTab() {
  const value = new URLSearchParams(location.search).get("tab") || "workspaces";
  return TABS.some(([key]) => key === value) ? value : "workspaces";
}

function tabs(active) {
  return `<nav class="system-tool-tabs" aria-label="组织与工作区功能">${TABS.map(([key, label]) =>
    `<a href="/system/workspaces?tab=${key}" ${active === key ? 'aria-current="page"' : ""}>${esc(label)}</a>`).join("")}</nav>`;
}

// 中文说明：状态条。current 由后端标记（当前请求上下文所在工作区），前端不自行推断。
function statusStrip(organizations, workspaces) {
  const active = workspaces.filter(item => item.status === "active");
  const current = workspaces.find(item => item.current);
  return `<div class="system-status-strip governance-status-strip">
    <span class="healthy"><i></i><b>当前</b>${esc(current?.name || "—")}</span>
    <span><i></i><b>组织</b>${organizations.length}</span>
    <span><i></i><b>启用工作区</b>${active.length}</span>
    <span><i></i><b>当前成员</b>${current?.member_count ?? 0}</span>
  </div>`;
}

// 中文说明：新建/修改组织弹窗。新建时必须同时给出首个工作区编码与名称（后端要求组织至少有一个工作区）；
// 编辑时编码不可改，故表单里不渲染编码字段。成功后整页重绘以刷新状态条与列表。
function organizationDialog(root, record = null) {
  openDialog(record ? "修改组织" : "新建组织", `<div class="form-grid">
    ${record ? "" : field("组织编码", input("code", "", 'required maxlength="80" pattern="[A-Za-z][A-Za-z0-9_.\\-]+"'))}
    ${field("组织名称", input("name", record?.name || "", 'required maxlength="200"'))}
    ${field("说明", `<textarea name="description" maxlength="1000">${esc(record?.description || "")}</textarea>`, "", true)}
    ${record ? "" : field("首个工作区编码", input("initial_workspace_code", "main", 'required maxlength="80" pattern="[A-Za-z][A-Za-z0-9_.\\-]+"'))}
    ${record ? "" : field("首个工作区名称", input("initial_workspace_name", "主工作区", 'required maxlength="200"'))}
  </div>`, {
    onSave: async form => {
      if (record) {
        await api.patch(`${BASE}/organizations/${record.id}`, {
          version: record.version, name: form.get("name"), description: form.get("description"),
        });
      } else {
        await api.post(`${BASE}/organizations`, Object.fromEntries(form));
      }
      toast(record ? "组织信息已保存" : "组织和首个工作区已创建");
      await renderWorkspaces(root);
    },
  });
}

// 中文说明：新建/修改工作区弹窗（编码建后不可改，编辑只提交名称与说明 + version）。
function workspaceDialog(root, record = null) {
  openDialog(record ? "修改工作区" : "新建工作区", `<div class="form-grid">
    ${record ? "" : field("工作区编码", input("code", "", 'required maxlength="80" pattern="[A-Za-z][A-Za-z0-9_.\\-]+"'))}
    ${field("工作区名称", input("name", record?.name || "", 'required maxlength="200"'))}
    ${field("说明", `<textarea name="description" maxlength="1000">${esc(record?.description || "")}</textarea>`, "", true)}
  </div>`, {
    onSave: async form => {
      if (record) {
        await api.patch(`${BASE}/workspaces/${record.id}`, {
          version: record.version, name: form.get("name"), description: form.get("description"),
        });
      } else {
        await api.post(`${BASE}/workspaces`, Object.fromEntries(form));
      }
      toast(record ? "工作区信息已保存" : "工作区已创建");
      await renderWorkspaces(root);
    },
  });
}

// 中文说明：工作区列表页（追加在页头/状态条之后，故用 insertAdjacentHTML 而不是覆盖 root）。
// 行内动作：进入（切换工作区并整页跳转）、编辑、归档/恢复（确认弹窗 + PATCH version）。
// 当前工作区不显示归档按钮，避免把自己所在的工作区归档掉。
async function renderWorkspaceList(root, organizations, workspaces) {
  const rows = workspaces;
  root.insertAdjacentHTML("beforeend", `<section class="panel governance-panel">
    <div class="system-panel-actions">${can("system.workspaces.manage") ? button("新建工作区", "data-new-workspace", true) : ""}</div>
    ${rows.length ? dataTable(["工作区", "编码", "成员", "我的角色", "状态", "更新时间", "操作"], rows, item => `<tr>
      <td><strong>${esc(item.name)}</strong>${item.current ? '<span class="cell-secondary">当前工作区</span>' : ""}</td>
      <td><code>${esc(item.code)}</code></td><td>${item.member_count}</td><td>${esc(item.actor_role || "—")}</td>
      <td>${statusBadge(item.status)}</td><td>${date(item.updated_at)}</td>
      <td><div class="row-actions">
        ${item.status === "active" && !item.current ? `<button class="text-button" data-switch-workspace="${item.id}">进入</button>` : ""}
        ${can("system.workspaces.manage") ? `<button class="text-button" data-edit-workspace="${item.id}">编辑</button>${item.current ? "" : `<button class="text-button ${item.status === "active" ? "danger" : ""}" data-workspace-status="${item.status === "active" ? "archived" : "active"}" data-workspace="${item.id}">${item.status === "active" ? "归档" : "恢复"}</button>`}` : ""}
      </div></td></tr>`) : emptyState("没有可管理的工作区", "可在当前组织中新建工作区。")}
  </section>`);
  $("[data-new-workspace]", root)?.addEventListener("click", () => workspaceDialog(root));
  $$('[data-edit-workspace]', root).forEach(element => element.addEventListener("click", () =>
    workspaceDialog(root, rows.find(item => item.id === element.dataset.editWorkspace))));
  $$('[data-switch-workspace]', root).forEach(element => element.addEventListener("click", () => {
    // 先写工作区上下文再跳首页：整页重载后导航/权限才会按新工作区重新拉取
    api.setWorkspace(element.dataset.switchWorkspace);
    location.assign("/");
  }));
  $$('[data-workspace-status]', root).forEach(element => element.addEventListener("click", () => {
    const record = rows.find(item => item.id === element.dataset.workspace);
    const next = element.dataset.workspaceStatus;
    confirmAction(next === "archived" ? "归档工作区" : "恢复工作区",
      `${next === "archived" ? "归档" : "恢复"}“${record.name}”？归档后成员不能在该工作区登录。`, async () => {
        await api.patch(`${BASE}/workspaces/${record.id}`, { version: record.version, status: next });
        toast(next === "archived" ? "工作区已归档" : "工作区已恢复");
        await renderWorkspaces(root);
      }, next === "archived" ? "归档" : "恢复");
  }));
}

// 中文说明：组织列表页（tab=organizations）。归档组织有前置条件（须先归档其下全部工作区），
// 该约束由后端强制，前端只在确认文案中提示，不要在前端假装校验通过。
async function renderOrganizations(root, organizations) {
  root.insertAdjacentHTML("beforeend", `<section class="panel governance-panel">
    <div class="system-panel-actions">${can("system.workspaces.manage") ? button("新建组织", "data-new-organization", true) : ""}</div>
    ${organizations.length ? dataTable(["组织", "编码", "工作区", "状态", "更新时间", "操作"], organizations, item => `<tr>
      <td><strong>${esc(item.name)}</strong>${item.current ? '<span class="cell-secondary">当前组织</span>' : ""}</td>
      <td><code>${esc(item.code)}</code></td><td>${item.active_workspace_count} / ${item.workspace_count}</td>
      <td>${statusBadge(item.status)}</td><td>${date(item.updated_at)}</td><td><div class="row-actions">
        ${can("system.workspaces.manage") ? `<button class="text-button" data-edit-organization="${item.id}">编辑</button><button class="text-button ${item.status === "active" ? "danger" : ""}" data-organization-status="${item.status === "active" ? "archived" : "active"}" data-organization="${item.id}">${item.status === "active" ? "归档" : "恢复"}</button>` : "只读"}
      </div></td></tr>`) : emptyState("没有可管理的组织", "新建组织时会同时建立首个工作区。")}
  </section>`);
  $("[data-new-organization]", root)?.addEventListener("click", () => organizationDialog(root));
  $$('[data-edit-organization]', root).forEach(element => element.addEventListener("click", () =>
    organizationDialog(root, organizations.find(item => item.id === element.dataset.editOrganization))));
  $$('[data-organization-status]', root).forEach(element => element.addEventListener("click", () => {
    const record = organizations.find(item => item.id === element.dataset.organization);
    const next = element.dataset.organizationStatus;
    confirmAction(next === "archived" ? "归档组织" : "恢复组织",
      `${next === "archived" ? "归档前必须先归档全部工作区。" : "恢复组织后仍需按需恢复工作区。"}`, async () => {
        await api.patch(`${BASE}/organizations/${record.id}`, { version: record.version, status: next });
        toast(next === "archived" ? "组织已归档" : "组织已恢复");
        await renderWorkspaces(root);
      }, next === "archived" ? "归档" : "恢复");
  }));
}

// 中文说明：成员调动页（tab=members）。只列当前工作区成员（前 100 条），
// 目标工作区仅包含“启用且非当前”的工作区——不能把自己调动到当前所在地。
// 副作用：POST /workspaces/member-transfer 会真实改变成员归属，成功后整页重绘。
async function renderMemberTransfer(root, workspaces) {
  const result = await api.get(`${BASE}/users?page=1&page_size=100`);
  const targets = workspaces.filter(item => item.status === "active" && !item.current);
  root.insertAdjacentHTML("beforeend", `<section class="panel governance-panel">
    ${result.items.length ? dataTable(["成员", "账号", "部门", "状态", "操作"], result.items, item => `<tr>
      <td><strong>${esc(item.display_name)}</strong></td><td>${esc(item.username)}</td><td>${esc(item.department_name || "—")}</td>
      <td>${statusBadge(item.status)}</td><td>${targets.length && can("system.workspaces.manage") ? `<button class="text-button" data-transfer-user="${item.id}">复制 / 调动</button>` : "—"}</td>
    </tr>`) : emptyState("当前工作区没有成员", "请先在用户管理中新增成员。")}
  </section>`);
  $$('[data-transfer-user]', root).forEach(element => element.addEventListener("click", () => {
    const member = result.items.find(item => item.id === element.dataset.transferUser);
    openDialog("复制或调动成员", `<div class="form-grid">
      ${field("成员", `<input value="${esc(member.display_name)} · ${esc(member.username)}" disabled>`)}
      ${field("目标工作区", select("target_workspace_id", targets.map(item => ({ value: item.id, label: item.name })), targets[0]?.id, null))}
      ${field("处理方式", select("mode", [{ value: "copy", label: "复制：保留当前工作区" }, { value: "move", label: "调动：移出当前工作区" }], "copy", null))}
      ${field("目标角色", select("target_role_code", [{ value: "member", label: "普通成员" }, { value: "workspace_admin", label: "工作区管理员" }], "member", null))}
    </div>`, { onSave: async form => {
      const response = await api.post(`${BASE}/workspaces/member-transfer`, {
        user_id: member.id, target_workspace_id: form.get("target_workspace_id"),
        mode: form.get("mode"), target_role_code: form.get("target_role_code"),
      });
      toast(response.message);
      await renderWorkspaces(root);
    }});
  }));
}

/**
 * 中文说明：组织与工作区页入口。并行拉取组织与工作区列表，渲染页头、标签页与状态条后分发到具体标签内容。
 * 副作用：只读请求 + 重写 root；子页面会用 insertAdjacentHTML 继续追加，因此不要在本函数后再覆盖 root。
 * 失败表现：任一列表接口异常直接抛出，由 main.js 渲染错误页（并保留重试按钮）。
 */
export async function renderWorkspaces(root) {
  const active = selectedTab();
  const [organizations, workspaces] = await Promise.all([
    api.get(`${BASE}/organizations`), api.get(`${BASE}/workspaces`),
  ]);
  root.innerHTML = pageHeader("组织与工作区", "维护租户边界、工作区状态与成员流转。") +
    tabs(active) + statusStrip(organizations, workspaces);
  if (active === "organizations") await renderOrganizations(root, organizations);
  else if (active === "members") await renderMemberTransfer(root, workspaces);
  else await renderWorkspaceList(root, organizations, workspaces);
}
