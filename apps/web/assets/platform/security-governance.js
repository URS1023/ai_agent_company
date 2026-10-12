/**
 * 登录安全与企业 SSO 模块（路由 /system/security?tab=policy|devices|events|sso）
 *
 * 位置：四个标签页——登录策略（IP/设备/失败锁定/会话上限）、可信设备审批、登录记录、企业 SSO（OIDC）配置。
 *   登录策略直接影响全工作区成员的登录能力，属于高影响配置。
 * 依赖接口前缀：/api/v1/system（/security/policy、/security/devices、/security/login-events、
 *   /sso-providers 与 /sso-providers/{id}/discover 发现校验、/users 用于把设备映射到成员名）。
 *   登录页的 SSO 按钮在 main.js 中通过 /api/auth/sso/* 走同一批配置。
 * 与 iframe 内 Vue 应用无关。
 *
 * 关键约束：
 * - 管理动作统一要求 system.security.manage（与后端目录一致）；无权限时表单整体禁用、按钮不渲染（只读展示）。
 * - 策略保存带 version；启用“阻止新登录”类策略（session_overflow=block、设备必须审批）会改变线上登录行为，
 *   保存前页面需保持只读/可核对的状态，不要在前端做任何自动保存。
 * - IP 允许/禁止列表按行解析；禁止列表优先且不得与允许列表重复（后端校验），文案需保持这一说明。
 * - 设备只会从 pending/trusted/revoked 之间流转；撤销设备属影响登录的动作，走统一确认弹窗。
 * - SSO 的 Client Secret 只提交不回显（留空表示保持不变）；Scope 必须包含 openid；
 *   allow_private（允许本机/内网联调地址）仅供联调，生产必须使用 HTTPS 公网或企业 DNS 地址。
 * - 已启用的 SSO 配置不能删除（需先停用），删除会同时作废客户端密钥引用，走统一确认弹窗。
 * - “发现校验”会真实请求身份平台的 Discovery 文档，失败原因直接来自对方服务，不要在前端改写。
 */

import { currentPage, wirePageNavigation, redirectEmptyPage } from "./list-navigation.js";
import { api, allPages } from "./api.js";
import {
  $, $$, can, esc, date, pageHeader, button, dataTable, emptyState,
  pagination, statusBadge, field, input, select, openDialog, confirmAction, toast,
} from "./ui.js";

const BASE = "/api/v1/system";
// 中文说明：标签页定义（键名即 tab 参数，需与 selectedTab 校验、renderSecurity 分支一致）。
const TABS = [["policy", "登录策略"], ["devices", "可信设备"], ["events", "登录记录"], ["sso", "企业 SSO"]];
// 中文说明：多值输入解析（换行或逗号分隔），用于 IP/CIDR、Scopes、邮箱域名等字段。
const splitLines = value => String(value || "").split(/[\r\n,]+/).map(item => item.trim()).filter(Boolean);

// 中文说明：读取当前标签页；非法值回落 policy。
function selectedTab() {
  const value = new URLSearchParams(location.search).get("tab") || "policy";
  return TABS.some(([key]) => key === value) ? value : "policy";
}

function tabs(active) {
  return `<nav class="system-tool-tabs" aria-label="登录与企业 SSO 功能">${TABS.map(([key, label]) =>
    `<a href="/system/security?tab=${key}" ${active === key ? 'aria-current="page"' : ""}>${esc(label)}</a>`).join("")}</nav>`;
}

// 中文说明：紧凑状态条（四个标签页共用）。
function compactStatus(items) {
  return `<div class="system-status-strip governance-status-strip">${items.map(item =>
    `<span class="${esc(item.tone || "")}"><i></i><b>${esc(item.label)}</b>${esc(item.value)}</span>`).join("")}</div>`;
}

// 中文说明：登录策略视图：IP/设备/失败锁定/会话上限配置。
// 约束：无 system.security.manage 时全部控件禁用（只读）；保存带 version；
//   策略生效范围是整个工作区，修改会影响所有成员的登录，因此不做任何自动提交。
async function renderPolicy(root) {
  const policy = await api.get(`${BASE}/security/policy`);
  const readonly = !can("system.security.manage");
  root.insertAdjacentHTML("beforeend", compactStatus([
    { label: "策略", value: policy.enabled ? "执行中" : "未启用", tone: policy.enabled ? "healthy" : "warning" },
    { label: "设备审批", value: policy.require_trusted_device ? "必须" : "按风险记录" },
    { label: "失败锁定", value: `${policy.max_failed_attempts} 次 / ${policy.lock_minutes} 分钟` },
    { label: "会话上限", value: `${policy.max_active_sessions} 个` },
  ]) + `<section class="panel governance-panel"><form class="governance-form" data-security-policy>
    <div class="governance-form-head"><strong>当前工作区策略</strong>${readonly ? '<span class="muted">只读</span>' : '<button class="button primary" type="submit">保存策略</button>'}</div>
    <div class="form-grid governance-form-grid">
      ${field("策略状态", `<label class="governance-check"><input type="checkbox" name="enabled" ${policy.enabled ? "checked" : ""} ${readonly ? "disabled" : ""}><span>启用 IP、设备和会话限制</span></label>`)}
      ${field("可信设备", `<label class="governance-check"><input type="checkbox" name="require_trusted_device" ${policy.require_trusted_device ? "checked" : ""} ${readonly ? "disabled" : ""}><span>新设备必须由管理员批准</span></label>`)}
      ${field("连续失败次数", input("max_failed_attempts", policy.max_failed_attempts, `type="number" min="2" max="20" required ${readonly ? "disabled" : ""}`))}
      ${field("锁定时长（分钟）", input("lock_minutes", policy.lock_minutes, `type="number" min="1" max="1440" required ${readonly ? "disabled" : ""}`))}
      ${field("每人有效会话上限", input("max_active_sessions", policy.max_active_sessions, `type="number" min="1" max="100" required ${readonly ? "disabled" : ""}`))}
      ${field("超限处理", select("session_overflow", [{ value: "revoke_oldest", label: "撤销最早会话" }, { value: "block", label: "阻止新登录" }], policy.session_overflow, null).replace("<select", `<select ${readonly ? "disabled" : ""}`))}
      ${field("允许登录的 IP / CIDR", `<textarea name="allowed_cidrs" placeholder="留空表示不限制" ${readonly ? "disabled" : ""}>${esc(policy.allowed_cidrs.join("\n"))}</textarea>`, "每行一个 IPv4、IPv6 地址或网段。", true)}
      ${field("禁止登录的 IP / CIDR", `<textarea name="blocked_cidrs" placeholder="例如 203.0.113.0/24" ${readonly ? "disabled" : ""}>${esc(policy.blocked_cidrs.join("\n"))}</textarea>`, "禁止列表优先，且不能与允许列表重复。", true)}
    </div>
  </form></section>`);
  $("[data-security-policy]", root)?.addEventListener("submit", async event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const submit = $('[type="submit"]', event.currentTarget);
    submit.disabled = true;
    try {
      await api.patch(`${BASE}/security/policy`, {
        version: policy.version, enabled: form.has("enabled"),
        require_trusted_device: form.has("require_trusted_device"),
        max_failed_attempts: Number(form.get("max_failed_attempts")),
        lock_minutes: Number(form.get("lock_minutes")),
        max_active_sessions: Number(form.get("max_active_sessions")),
        session_overflow: form.get("session_overflow"),
        allowed_cidrs: splitLines(form.get("allowed_cidrs")),
        blocked_cidrs: splitLines(form.get("blocked_cidrs")),
      });
      toast("登录安全策略已保存");
      await renderSecurity(root);
    } catch (error) { toast(error.message, true); }
    finally { submit.disabled = false; }
  });
}

// 中文说明：可信设备视图。成员名通过 /users 全量映射（失败则回落显示 user_id，不阻塞本页）。
// 行内动作：批准（TAP 填写设备名，PATCH status=trusted）、撤销（统一确认弹窗）；
// 越界页码由 redirectEmptyPage 回落到末页，避免撤销后当前页为空。
async function renderDevices(root) {
  const status = new URLSearchParams(location.search).get("status") || "";
  const [result, users] = await Promise.all([
    api.get(`${BASE}/security/devices?page=${currentPage()}&page_size=100${status ? `&status=${encodeURIComponent(status)}` : ""}`),
    allPages(`${BASE}/users`).catch(() => []),
  ]);
  const names = Object.fromEntries(users.map(item => [item.id, item.display_name]));
  if (redirectEmptyPage(result, 100)) return;
  const counts = result.items.reduce((value, item) => ({ ...value, [item.status]: (value[item.status] || 0) + 1 }), {});
  root.insertAdjacentHTML("beforeend", compactStatus([
    { label: "本页待审批", value: counts.pending || 0, tone: counts.pending ? "warning" : "healthy" },
    { label: "本页可信", value: counts.trusted || 0, tone: "healthy" },
    { label: "本页已撤销", value: counts.revoked || 0 },
    { label: "记录", value: result.total },
  ]) + `<section class="panel governance-panel"><form class="list-toolbar" data-device-filter>
    <label class="form-field governance-inline-field"><span>状态</span>${select("status", [{ value: "pending", label: "待审批" }, { value: "trusted", label: "可信" }, { value: "revoked", label: "已撤销" }], status, "全部")}</label>
    <button class="button" type="submit">筛选</button><a class="button quiet" href="/system/security?tab=devices">重置</a>
    </form>${result.items.length ? dataTable(["设备", "成员", "状态", "首次出现", "最近出现", "操作"], result.items, item => `<tr>
      <td><strong>${esc(item.label)}</strong><span class="cell-secondary"><code>${esc(item.device_hash)}</code></span></td>
      <td>${esc(names[item.user_id] || item.user_id)}</td><td>${statusBadge(item.status)}</td>
      <td>${date(item.first_seen_at)}</td><td>${date(item.last_seen_at)}</td><td><div class="row-actions">
        ${can("system.security.manage") && item.status !== "trusted" ? `<button class="text-button" data-device-trust="${item.id}">批准</button>` : ""}
        ${can("system.security.manage") && item.status !== "revoked" ? `<button class="text-button danger" data-device-revoke="${item.id}">撤销</button>` : ""}
      </div></td></tr>`) : emptyState("没有符合条件的设备", "新浏览器完成一次账号校验后会生成设备记录。")}${pagination(result)}</section>`);
  wirePageNavigation(root, result);
  $("[data-device-filter]", root).addEventListener("submit", event => {
    event.preventDefault(); const value = new FormData(event.currentTarget).get("status");
    location.assign(`/system/security?tab=devices${value ? `&status=${encodeURIComponent(value)}` : ""}`);
  });
  $$('[data-device-trust]', root).forEach(element => element.addEventListener("click", () => {
    const record = result.items.find(item => item.id === element.dataset.deviceTrust);
    openDialog("批准可信设备", field("设备名称", input("label", record.label, 'required maxlength="160"')), {
      onSave: async form => {
        await api.patch(`${BASE}/security/devices/${record.id}`, { version: record.version, status: "trusted", label: form.get("label") });
        toast("设备已批准"); await renderSecurity(root);
      },
    });
  }));
  $$('[data-device-revoke]', root).forEach(element => element.addEventListener("click", () => {
    const record = result.items.find(item => item.id === element.dataset.deviceRevoke);
    confirmAction("撤销设备", `撤销“${record.label}”的可信状态？`, async () => {
      await api.patch(`${BASE}/security/devices/${record.id}`, { version: record.version, status: "revoked" });
      toast("设备已撤销"); await renderSecurity(root);
    }, "撤销");
  }));
}

// 中文说明：登录记录视图（只读审计）：成功/失败/已阻止三种结果与风险等级、IP、设备哈希。
// 记录用于排查登录异常（例如被策略阻止的原因），不可修改或删除。
async function renderEvents(root) {
  const outcome = new URLSearchParams(location.search).get("outcome") || "";
  const result = await api.get(`${BASE}/security/login-events?page=${currentPage()}&page_size=100${outcome ? `&outcome=${encodeURIComponent(outcome)}` : ""}`);
  if (redirectEmptyPage(result, 100)) return;
  const counts = result.items.reduce((value, item) => ({ ...value, [item.outcome]: (value[item.outcome] || 0) + 1 }), {});
  root.insertAdjacentHTML("beforeend", compactStatus([
    { label: "本页成功", value: counts.success || 0, tone: "healthy" },
    { label: "本页失败", value: counts.failed || 0, tone: counts.failed ? "warning" : "" },
    { label: "本页阻止", value: counts.blocked || 0, tone: counts.blocked ? "unhealthy" : "" },
    { label: "当前结果", value: result.total },
  ]) + `<section class="panel governance-panel"><form class="list-toolbar" data-event-filter>
    <label class="form-field governance-inline-field"><span>结果</span>${select("outcome", [{ value: "success", label: "成功" }, { value: "failed", label: "失败" }, { value: "blocked", label: "已阻止" }], outcome, "全部")}</label>
    <button class="button" type="submit">筛选</button><a class="button quiet" href="/system/security?tab=events">重置</a>
    </form>${result.items.length ? dataTable(["账号", "方式", "结果", "原因", "风险", "IP", "设备", "时间"], result.items, item => `<tr>
      <td><strong>${esc(item.username)}</strong></td><td>${esc(item.auth_method.toUpperCase())}</td><td>${statusBadge(item.outcome)}</td>
      <td>${esc(item.reason)}</td><td><span class="risk-level risk-${esc(item.risk_level)}">${esc(item.risk_level)}</span></td>
      <td><code>${esc(item.ip_address || "—")}</code></td><td><code>${esc(item.device_hash || "—")}</code></td><td>${date(item.created_at)}</td>
    </tr>`) : emptyState("没有符合条件的登录记录", "登录成功、凭据失败和策略阻止都会记录在这里。")}${pagination(result)}</section>`);
  wirePageNavigation(root, result);
  $("[data-event-filter]", root).addEventListener("submit", event => {
    event.preventDefault(); const value = new FormData(event.currentTarget).get("outcome");
    location.assign(`/system/security?tab=events${value ? `&outcome=${encodeURIComponent(value)}` : ""}`);
  });
}

// 中文说明：企业 SSO（OIDC）配置表单。
// 约束：Client Secret 只提交不回显（留空表示保持原值）；Scopes 必须含 openid；
//   claim 映射（subject/email/name/username）决定成员匹配方式，默认值即后端约定，改动前需与身份平台对齐；
//   auto_provision 表示首次登录自动建成员，default_role_code 决定其初始角色（授权范围较大，谨慎放开）。
function providerDialog(root, record = null) {
  const claims = record?.claims || {};
  openDialog(record ? "修改企业 SSO" : "新增企业 SSO", `<div class="form-grid">
    ${record ? "" : field("配置编码", input("code", "", 'required maxlength="32" pattern="[A-Za-z][A-Za-z0-9_\\-]+"'))}
    ${field("登录名称", input("display_name", record?.display_name || "", 'required maxlength="120"'))}
    ${field("Issuer", input("issuer_url", record?.issuer_url || "", 'type="url" required maxlength="1000"'), "", true)}
    ${field("授权端点", input("authorization_endpoint", record?.authorization_endpoint || "", 'type="url" required maxlength="1000"'), "", true)}
    ${field("令牌端点", input("token_endpoint", record?.token_endpoint || "", 'type="url" required maxlength="1000"'), "", true)}
    ${field("JWKS 地址", input("jwks_uri", record?.jwks_uri || "", 'type="url" required maxlength="1000"'), "", true)}
    ${field("Client ID", input("client_id", record?.client_id || "", 'required maxlength="300"'))}
    ${field("客户端认证", select("token_auth_method", [{ value: "client_secret_basic", label: "client_secret_basic" }, { value: "client_secret_post", label: "client_secret_post" }, { value: "none", label: "公共客户端（PKCE）" }], record?.token_auth_method || "client_secret_basic", null))}
    ${field("Client Secret", input("client_secret", "", 'type="password" maxlength="1000" autocomplete="new-password"'), record?.client_secret_configured ? "已配置；留空保持不变。" : "机密客户端必须填写。")}
    ${field("Scopes", `<textarea name="scopes" required>${esc((record?.scopes || ["openid", "profile", "email"]).join("\n"))}</textarea>`, "必须包含 openid。")}
    ${field("允许的邮箱域名", `<textarea name="allowed_domains" placeholder="example.com">${esc((record?.allowed_domains || []).join("\n"))}</textarea>`, "留空表示不按邮箱域限制。")}
    ${field("自动开通角色", select("default_role_code", [{ value: "member", label: "普通成员" }, { value: "workspace_admin", label: "工作区管理员" }], record?.default_role_code || "member", null))}
    <h3 class="form-section wide">声明映射</h3>
    ${field("Subject", input("claim_subject", claims.subject || "sub", 'required maxlength="120"'))}
    ${field("邮箱", input("claim_email", claims.email || "email", 'required maxlength="120"'))}
    ${field("显示姓名", input("claim_name", claims.name || "name", 'required maxlength="120"'))}
    ${field("登录名", input("claim_username", claims.username || "preferred_username", 'required maxlength="120"'))}
    ${field("安全与开通", `<div class="governance-check-stack">
      <label class="governance-check"><input type="checkbox" name="auto_provision" ${record?.auto_provision ? "checked" : ""}><span>首次登录自动创建成员</span></label>
      <label class="governance-check"><input type="checkbox" name="enabled" ${record?.enabled ? "checked" : ""}><span>允许在登录页使用</span></label>
      <label class="governance-check"><input type="checkbox" name="allow_private" ${record?.allow_private ? "checked" : ""}><span>允许本机 / 内网联调地址</span></label>
    </div>`, "生产环境应使用 HTTPS 公网或企业 DNS 地址。", true)}
  </div>`, {
    wide: true,
    onSave: async form => {
      const payload = {
        display_name: form.get("display_name"), issuer_url: form.get("issuer_url"),
        authorization_endpoint: form.get("authorization_endpoint"), token_endpoint: form.get("token_endpoint"),
        jwks_uri: form.get("jwks_uri"), client_id: form.get("client_id"),
        token_auth_method: form.get("token_auth_method"), scopes: splitLines(form.get("scopes")),
        allowed_domains: splitLines(form.get("allowed_domains")), auto_provision: form.has("auto_provision"),
        enabled: form.has("enabled"), allow_private: form.has("allow_private"),
        default_role_code: form.get("default_role_code"), claims: {
          subject: form.get("claim_subject"), email: form.get("claim_email"),
          name: form.get("claim_name"), username: form.get("claim_username"),
        },
      };
      if (form.get("client_secret")) payload.client_secret = form.get("client_secret");
      if (record) await api.patch(`${BASE}/sso-providers/${record.id}`, { version: record.version, ...payload });
      else await api.post(`${BASE}/sso-providers`, { code: form.get("code"), client_secret: form.get("client_secret") || "", ...payload });
      toast("企业 SSO 配置已保存"); await renderSecurity(root);
    },
  });
}

// 中文说明：企业 SSO 列表视图。回调地址固定为 /sso/callback（与 main.js 的回调处理路径一致），
// 在身份平台侧必须登记同一地址。已启用的配置不能删除，需先停用；启用开关也会决定登录页是否出现该入口。
async function renderSso(root) {
  const result = await api.get(`${BASE}/sso-providers`);
  const items = result.items;
  root.insertAdjacentHTML("beforeend", compactStatus([
    { label: "配置", value: items.length },
    { label: "已启用", value: items.filter(item => item.enabled).length, tone: "healthy" },
    { label: "发现校验通过", value: items.filter(item => item.last_probe_status === "passed").length },
    { label: "回调", value: "/sso/callback" },
  ]) + `<section class="panel governance-panel"><div class="system-panel-actions">${can("system.security.manage") ? button("新增 SSO", "data-new-sso", true) : ""}</div>
    ${items.length ? dataTable(["登录方式", "编码", "客户端", "开通", "发现校验", "状态", "操作"], items, item => `<tr>
      <td><strong>${esc(item.display_name)}</strong><span class="cell-secondary">${esc(item.workspace_name)}</span></td><td><code>${esc(item.code)}</code></td>
      <td>${esc(item.token_auth_method)}${item.client_secret_configured ? '<span class="cell-secondary">密钥已配置</span>' : ""}</td>
      <td>${item.auto_provision ? esc(item.default_role_code) : "仅映射账号"}</td><td>${item.last_probe_status ? statusBadge(item.last_probe_status) : "未校验"}</td>
      <td>${statusBadge(item.enabled)}</td><td><div class="row-actions">${can("system.security.manage") ? `<button class="text-button" data-edit-sso="${item.id}">编辑</button><button class="text-button" data-discover-sso="${item.id}">发现校验</button>${item.enabled ? "" : `<button class="text-button danger" data-delete-sso="${item.id}">删除</button>`}` : "只读"}</div></td>
    </tr>`) : emptyState("尚未配置企业 SSO", "可接入支持 OIDC Discovery、签名 ID Token 和 PKCE 的身份平台。")}</section>`);
  $("[data-new-sso]", root)?.addEventListener("click", () => providerDialog(root));
  $$('[data-edit-sso]', root).forEach(element => element.addEventListener("click", () =>
    providerDialog(root, items.find(item => item.id === element.dataset.editSso))));
  $$('[data-discover-sso]', root).forEach(element => element.addEventListener("click", async () => {
    const result = await api.post(`${BASE}/sso-providers/${element.dataset.discoverSso}/discover`, {});
    toast(result.status === "passed" ? `发现校验通过，签名密钥 ${result.key_count} 个` : result.error, result.status !== "passed");
    await renderSecurity(root);
  }));
  $$('[data-delete-sso]', root).forEach(element => element.addEventListener("click", () => {
    const record = items.find(item => item.id === element.dataset.deleteSso);
    confirmAction("删除 SSO 配置", `删除“${record.display_name}”及其客户端密钥引用？`, async () => {
      await api.delete(`${BASE}/sso-providers/${record.id}`); toast("SSO 配置已删除"); await renderSecurity(root);
    }, "删除");
  }));
}

/**
 * 中文说明：登录与企业 SSO 页入口：按 ?tab 渲染登录策略 / 可信设备 / 登录记录 / 企业 SSO（默认策略）。
 * 副作用：先重写页头与标签页，子视图用 insertAdjacentHTML 追加内容；策略保存与设备/SSO 变更为写操作。
 */
export async function renderSecurity(root) {
  const active = selectedTab();
  root.innerHTML = pageHeader("登录与企业 SSO", "集中配置登录边界、设备审批、会话风险与企业身份。") + tabs(active);
  if (active === "devices") await renderDevices(root);
  else if (active === "events") await renderEvents(root);
  else if (active === "sso") await renderSso(root);
  else await renderPolicy(root);
}
