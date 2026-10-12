/**
 * 个人资料与安全设置模块（路由 /account/profile）
 *
 * 位置：渲染“基本资料”与“安全设置”两个标签页，供用户维护本人资料与登录密码；
 *   角色、部门、账号状态由管理员在系统管理页面维护，此页只读展示，不提供改角色的入口。
 * 依赖接口：GET /api/auth/profile、PATCH /api/auth/profile（带 version 乐观锁）、
 *   POST /api/auth/password（改密）、GET /api/auth/me（刷新当前用户）。
 * 与 iframe 内 Vue 应用无关。
 *
 * 关键约束：
 * - 保存资料必须回传后端下发的 version，防止两人同时编辑时互相覆盖（后端会做版本校验）。
 * - 改密成功后必须清本地会话并回到登录页：后端会立即撤销该账号在其他设备上的旧会话，
 *   继续用旧令牌操作只会得到 401。
 * - 资料保存成功后要派发 platform-profile-changed，让 main.js 刷新页头与内存中的当前用户。
 */

import { api } from "./api.js";
import { $, state, esc, pageHeader, field, input, statusBadge, toast } from "./ui.js";


// 中文说明：标签页导航。用真实链接（非前端路由）以便刷新后保持在同一标签，选中态用 aria-current。
function tabs(selected) {
  return `<nav class="system-tool-tabs" aria-label="账号设置"><a href="/account/profile" ${selected === "profile" ? 'aria-current="page"' : ""}>基本资料</a><a href="/account/profile?tab=security" ${selected === "security" ? 'aria-current="page"' : ""}>安全设置</a></nav>`;
}


// 中文说明：只读的账号事实列表（组织/工作区/部门/岗位/角色）。后端未返回的字段显示“未分配/未授权”，
// 不要把这些字段做成可编辑控件——它们归管理员维护。
function accountFacts(profile) {
  const rows = [
    ["登录账号", profile.username],
    ["组织 / 工作区", `${profile.organization_name} / ${profile.workspace_name}`],
    ["部门", profile.department_name || "未分配"],
    ["岗位", profile.position_names.join("、") || "未分配"],
    ["角色", profile.role_names.join("、") || "未授权"],
  ];
  return `<dl class="account-facts">${rows.map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join("")}</dl>`;
}


// 中文说明：基本资料表单。
// 副作用：PATCH /api/auth/profile 成功后重新拉取 /api/auth/me、派发 platform-profile-changed、
// 弹出成功提示并用返回数据重绘本页（页面内错误只内联展示，不清空用户输入）。
function renderBasic(root, profile) {
  root.innerHTML = pageHeader("个人资料", "维护本人在当前工作区的资料。角色、部门和账号状态由管理员管理。") + tabs("profile") +
    `<section class="panel account-profile-panel"><div class="account-profile-summary"><span class="enterprise-avatar account-avatar">${esc((profile.display_name || "用").slice(0, 1))}</span><div><strong>${esc(profile.display_name)}</strong><span>${esc(profile.username)}</span></div>${statusBadge(profile.status)}</div>${accountFacts(profile)}<form class="account-form" data-profile-form><div class="form-grid">${field("显示姓名", input("display_name", profile.display_name, 'required maxlength="160" autocomplete="name"'))}${field("联系邮箱", input("email", profile.email, 'type="email" maxlength="254" autocomplete="email"'))}${field("联系电话", input("phone", profile.phone, 'type="tel" maxlength="40" autocomplete="tel"'))}</div><p class="form-error" role="alert" hidden></p><div class="form-actions"><button class="button primary" type="submit">保存资料</button></div></form></section>`;
  $("[data-profile-form]", root).addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = form.querySelector('[type="submit"]');
    const error = form.querySelector(".form-error");
    const values = Object.fromEntries(new FormData(form));
    error.hidden = true;
    submit.disabled = true;
    try {
      const updated = await api.patch("/api/auth/profile", {
        version: profile.version,
        display_name: values.display_name,
        email: values.email,
        phone: values.phone,
      });
      state.me = await api.get("/api/auth/me");
      window.dispatchEvent(new CustomEvent("platform-profile-changed", { detail: state.me }));
      toast("个人资料已保存");
      renderBasic(root, updated);
    } catch (reason) {
      error.textContent = reason.message;
      error.hidden = false;
    } finally { submit.disabled = false; }
  });
}


// 中文说明：安全设置（改密）表单。
// 副作用：POST /api/auth/password 成功后清会话并 location.assign("/") 到登录页；失败时重新启用提交按钮。
// 约束：两次新密码必须前端先比对，避免无谓的写请求；密码长度/复杂度规则与后端保持一致（至少 10 位含字母数字）。
function renderSecurity(root, profile) {
  root.innerHTML = pageHeader("个人资料", "修改登录密码会立即撤销当前及其他设备上的旧会话。") + tabs("security") +
    `<section class="panel account-profile-panel"><div class="account-security-head"><div><h2>登录安全</h2><p>${esc(profile.username)} · ${profile.must_change_password ? "当前仍是临时密码" : "密码已由本人设置"}</p></div>${statusBadge(profile.status)}</div><form class="account-form" data-password-form><div class="form-grid">${field("当前密码", input("current_password", "", 'type="password" required maxlength="256" autocomplete="current-password"'))}${field("新密码", input("new_password", "", 'type="password" required minlength="10" maxlength="256" autocomplete="new-password"'), "至少 10 位，同时包含字母和数字。")}${field("确认新密码", input("confirm_password", "", 'type="password" required minlength="10" maxlength="256" autocomplete="new-password"'))}</div><p class="form-error" role="alert" hidden></p><div class="form-actions"><button class="button primary" type="submit">修改密码并退出</button></div></form></section>`;
  $("[data-password-form]", root).addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = Object.fromEntries(new FormData(form));
    const error = form.querySelector(".form-error");
    const submit = form.querySelector('[type="submit"]');
    error.hidden = true;
    submit.disabled = true;
    try {
      if (values.new_password !== values.confirm_password) throw new Error("两次输入的新密码不一致");
      await api.post("/api/auth/password", { current_password: values.current_password, new_password: values.new_password });
      api.clearSession();
      location.assign("/");
    } catch (reason) {
      error.textContent = reason.message;
      error.hidden = false;
      submit.disabled = false;
    }
  });
}


/**
 * 中文说明：个人资料页入口，按 ?tab=security 决定渲染安全设置还是基本资料。
 * 副作用：GET /api/auth/profile 拉取资料并写 root.innerHTML；异常由 main.js 统一渲染错误页。
 */
export async function renderProfile(root) {
  const profile = await api.get("/api/auth/profile");
  if (new URLSearchParams(location.search).get("tab") === "security") renderSecurity(root, profile);
  else renderBasic(root, profile);
}

