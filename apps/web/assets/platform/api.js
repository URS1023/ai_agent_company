/**
 * 平台外壳统一 HTTP 客户端（鉴权、工作区上下文、超时、错误归一化的唯一入口）
 *
 * 位置：被 knowledge.js、files.js、capabilities.js、operations.js 等所有功能模块复用；
 * 本身不渲染任何页面区域，只负责把 fetch 包装成 { method, path, body } 形式的调用。
 * 依赖接口前缀：调用方传入的 path 即后端真实路径（/api/... 与 /api/v1/... 两套前缀并存），
 * 本模块不加前缀、不做重写，避免与后端路由错位。
 * 与 iframe 内 Vue 应用无关：iframe（apps/analytics）使用自己的 axios 实例和会话，不共用此处 token。
 *
 * 关键约束：
 * - 鉴权头由本模块统一注入：Authorization: Bearer <agent_token> + X-Workspace-Id（工作区上下文）。
 *   手工拼接 fetch 会丢失工作区头并命中错误租户数据，功能模块必须走 api.*。
 * - 读请求默认 60s、写请求/上传/下载默认 300s，且都经过 withRequestDeadline；
 *   写请求超时不得自动重试（服务端可能已执行），由调用方提示用户先核对执行记录。
 * - 报告分享链接可能带 workspace_id，必须优先于本地记忆的工作区，否则老的工作区选择会把有效报告变成 404。
 * - 错误统一抛 ApiError，并把后端校验问题（issues[0].location/message）拼进 message，调用方直接展示即可。
 */

import { withRequestDeadline } from "./request-deadline.js";
"use strict";

// 中文说明：平台统一业务异常。status=HTTP 状态码（用于区分 401/403/404/409/422 分支），
// detail=后端原始响应体（表单回填、字段级错误提示依赖它，勿丢弃）。
export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

const WORKSPACE_ID_PATTERN = /^[A-Za-z0-9_-]{1,128}$/;

/**
 * A saved report belongs to one workspace.  Its share/open link may therefore
 * carry an explicit workspace context.  Prefer that context while the report
 * is open so an old localStorage selection cannot turn a valid report into a
 * misleading 404.  Other pages continue to use the user's active workspace.
 */
// 中文补充：从报告链接中解析工作区上下文，仅对 /report/(view|designer|basic)/{id} 生效。
// 返回 "" 表示沿用当前激活工作区；返回值已按 WORKSPACE_ID_PATTERN 校验，可安全拼入请求头。
export function reportRouteWorkspace(href = location.href) {
  const url = new URL(href, location.origin);
  if (!/^\/report\/(?:view|designer|basic)\/[A-Za-z0-9_-]+$/.test(url.pathname)) return "";
  const workspaceId = url.searchParams.get("workspace_id") || "";
  return WORKSPACE_ID_PATTERN.test(workspaceId) ? workspaceId : "";
}

/**
 * 中文说明：会话与请求门面对象。所有业务模块只通过它访问后端。
 * 会话状态保存在 localStorage（agent_token / agent_workspace_id / agent_device_id），
 * 因此同一浏览器标签共享登录态；切换工作区必须调用 setWorkspace，不能直接改 localStorage。
 * 各方法失败均抛 ApiError；upload/download 会走 300s 时限，download 还带文件落盘副作用。
 */
export const api = {
  get token() { return localStorage.getItem("agent_token") || ""; },
  // 报告分享链接里的 workspace_id 优先于本地记忆，避免跨工作区打开报告时误命中 404
  get workspace() { return reportRouteWorkspace() || localStorage.getItem("agent_workspace_id") || ""; },
  get device() {
    let value = localStorage.getItem("agent_device_id") || "";
    // 设备标识只在首次访问时生成并持久化，用于审计/风控侧识别同一浏览器，勿在请求时临时生成
    if (!value) {
      value = globalThis.crypto?.randomUUID?.() || ("device-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2));
      localStorage.setItem("agent_device_id", value);
    }
    return value;
  },
  setWorkspace(workspaceId) {
    // 传空表示“清空选择”，此时请求不再带 X-Workspace-Id，由后端按默认工作区处理
    if (workspaceId) localStorage.setItem("agent_workspace_id", workspaceId);
    else localStorage.removeItem("agent_workspace_id");
  },
  // 中文说明：登录/换令牌后写入会话，并优先沿用用户当前工作区；
  // 该工作区若不在新账号可见列表内则回落到第一个可用工作区，防止越权访问。
  setSession(result) {
    localStorage.setItem("agent_token", result.token);
    const available = result.workspaces || [];
    const preferred = available.find(item => item.id === this.workspace)?.id;
    this.setWorkspace(preferred || available[0]?.id || "");
  },
  clearSession() {
    localStorage.removeItem("agent_token");
    localStorage.removeItem("agent_workspace_id");
  },
  // 中文说明：统一请求方法（内部使用，业务代码用下面的 get/post/... 快捷方法）。
  // 读请求 60s、写请求 300s ；非 2xx 抛 ApiError，并把后端首个校验问题拼进消息。
  // 副作用：GET/POST 等均为真实后端调用，写接口可能产生审计记录与业务数据变更。
  async request(method, path, body, options = {}) {
    const headers = { Accept: "application/json" };
    if (this.token) headers.Authorization = "Bearer " + this.token;
    if (this.workspace) headers["X-Workspace-Id"] = this.workspace;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    return withRequestDeadline(async signal => {
      const response = await fetch(path, {
        method, headers, body: body === undefined ? undefined : JSON.stringify(body),
        signal, credentials: "same-origin",
      });
      const contentType = response.headers.get("Content-Type") || "";
      const data = contentType.includes("json") ? await response.json() : await response.text();
      if (!response.ok) {
        const issue = data?.issues?.[0];
        const detail = issue ? "：" + issue.location.slice(1).join(".") + " " + issue.message : "";
        throw new ApiError((data?.message || data?.detail || "请求失败（" + response.status + "）") + detail, response.status, data);
      }
      return data;
    }, { timeoutMs: method === "GET" ? 60000 : 300000, ...options, method });
  },
  get(path, options) { return this.request("GET", path, undefined, options); },
  post(path, body, options) { return this.request("POST", path, body, options); },
  patch(path, body, options) { return this.request("PATCH", path, body, options); },
  put(path, body, options) { return this.request("PUT", path, body, options); },
  delete(path, options) { return this.request("DELETE", path, undefined, options); },
  // 中文说明：上传文件（multipart）。form 必须是 FormData，浏览器自动带 boundary，故不设置 Content-Type。
  // 返回后端响应体；失败抛 ApiError（含状态码），不自动重试，避免重复入库。
  async upload(path, form, method = "POST", options = {}) {
    const headers = { Accept: "application/json" };
    if (this.token) headers.Authorization = "Bearer " + this.token;
    if (this.workspace) headers["X-Workspace-Id"] = this.workspace;
    return withRequestDeadline(async signal => {
      const response = await fetch(path, { method, headers, body: form, signal, credentials: "same-origin" });
      const contentType = response.headers.get("Content-Type") || "";
      const data = contentType.includes("json") ? await response.json() : await response.text();
      if (!response.ok) {
        const issue = data?.issues?.[0];
        const detail = issue ? "：" + issue.location.slice(1).join(".") + " " + issue.message : "";
        const error = new ApiError((data?.message || data?.detail || "上传失败（" + response.status + "）") + detail, response.status, data);
        throw error;
      }
      return data;
    }, { timeoutMs: 300000, ...options, method });
  },
  // 中文说明：下载文件并直接触发浏览器保存。
  // 文件名优先取 Content-Disposition 的 filename*（UTF-8 编码），其次 filename，最后用 fallbackName（中文名必须走 filename*）。
  // 返回 { name, size }；失败抛 ApiError。副作用：写盘下载，不写业务库。
  async download(path, fallbackName = "download", options = {}) {
    const headers = { Accept: "*/*" };
    if (this.token) headers.Authorization = "Bearer " + this.token;
    if (this.workspace) headers["X-Workspace-Id"] = this.workspace;
    return withRequestDeadline(async signal => {
      const response = await fetch(path, { headers, signal, credentials: "same-origin" });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new ApiError(data?.message || data?.detail || "下载失败（" + response.status + "）", response.status, data);
      }
      const disposition = response.headers.get("Content-Disposition") || "";
      const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
      const plain = disposition.match(/filename="?([^";]+)"?/i)?.[1];
      let name = fallbackName;
      try { name = encoded ? decodeURIComponent(encoded) : (plain || fallbackName); } catch { name = plain || fallbackName; }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = name;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      return { name, size: blob.size };
    }, { timeoutMs: 300000, ...options, method: "GET" });
  },
};

// 中文说明：按 page/page_size 逐页拉取列表接口的全部数据（每次 100 条，最多 100 页）。
// 约束：上限 10000 条，超过则抛错要求调用方加筛选；不要用于可能无界增长的列表，避免请求风暴。
// 参数 path 可自带查询串（自动改用 & 拼接）；返回扁平的 items 数组。
export async function allPages(path) {
  const items = [];
  for (let page = 1; page <= 100; page++) {
    const result = await api.get(path + (path.includes("?") ? "&" : "?") + "page_size=100&page=" + page);
    items.push(...result.items);
    if (items.length >= result.total) return items;
  }
  throw new Error("选项超过 10000 条，请通过筛选缩小范围");
}

// 中文说明：把对象序列化成 JSON 文件并在浏览器下载（用于导出配置/审计结果）。
// 副作用：本地落盘，不访问后端；1 秒后回收 objectURL。
export function downloadJson(value, name) {
  const blob = new Blob([JSON.stringify(value, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
