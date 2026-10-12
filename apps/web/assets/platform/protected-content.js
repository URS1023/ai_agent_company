/**
 * 受保护附件读取模块（平台外壳下载/预览的统一入口）
 *
 * 位置：被知识库、文件、能力等模块用于读取后端受保护内容，不渲染页面区域。
 * 依赖接口前缀：仅允许 /api/files/{id}/(download|content) 与 /api/artifacts/{id}/(download|content)，
 * 其余地址（含外部域名）一律拒绝，防止把鉴权令牌带去第三方。
 * 与 iframe 内 Vue 应用无关：iframe 走自身会话，不使用本模块。
 *
 * 关键约束：
 * - 附件地址里的 token 查询参数一律丢弃，鉴权改用 Authorization 头 + X-Workspace-Id，避免令牌出现在日志/历史。
 * - 单次下载时限 5 分钟（附件可能很大），仍受统一请求时限与取消信号约束。
 * - 失败时抛出中文错误（含 HTTP 状态码），调用方负责提示，勿静默吞掉。
 */

import { withRequestDeadline } from "./request-deadline.js";

// 中文说明：把任意字符串规范成受保护的附件 URL；不合法（跨域/非白名单路径/非法 URL）返回 null，由调用方决定提示文案。
export function contentUrl(value, origin = location.origin) {
  let url;
  try { url = new URL(value, origin); } catch { return null; }
  // 白名单正则必须与后端受保护内容路由保持一致；放宽会导致令牌被发往非附件接口
  if (url.origin !== origin || !/^\/api\/(?:files|artifacts)\/[A-Za-z0-9_-]+\/(?:download|content)$/.test(url.pathname)) return null;
  url.searchParams.delete("token");
  return url;
}

// 中文说明：以 blob 形式拉取附件内容。
// 参数：value=附件地址（经 contentUrl 校验）；options 可覆盖 timeoutMs / signal。
// 返回：{ blob, name }，name 优先取服务端 Content-Disposition 的 filename*（RFC 5987 编码）再降级到 filename。
// 副作用：只读网络请求，不写库；失败抛出 Error（消息取自后端 message/detail 或“附件加载失败（状态码）”）。
export async function fetchContent(value, options = {}) {
  const url = contentUrl(value);
  if (!url) throw new Error("不支持的附件地址");
  const headers = { Accept: "*/*" };
  const token = localStorage.getItem("agent_token");
  // workspace_id 以 URL 参数优先，其次回落本地会话，保证切换工作区后仍能取到正确租户的附件
  const workspace = url.searchParams.get("workspace_id") || localStorage.getItem("agent_workspace_id");
  if (token) headers.Authorization = "Bearer " + token;
  if (workspace) headers["X-Workspace-Id"] = workspace;
  return withRequestDeadline(async signal => {
    const response = await fetch(url.pathname + url.search, { headers, signal, credentials: "same-origin" });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.message || error.detail || `附件加载失败（${response.status}）`);
    }
    const disposition = response.headers.get("Content-Disposition") || "";
    const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
    let name = disposition.match(/filename="?([^";]+)"?/i)?.[1] || "附件";
    try { if (encoded) name = decodeURIComponent(encoded); } catch { /* keep fallback */ }
    return { blob: await response.blob(), name };
  }, { timeoutMs: 300000, ...options, method: "GET" });
}

export function saveContent({ blob, name }) {
  // 中文说明：把已取回的 blob 触发浏览器下载。
  // 注意：本函数不发起请求，内容已由 fetchContent 读取；1 秒后回收 objectURL 只是延迟释放，不影响已开始的下载。
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
