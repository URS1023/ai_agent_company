/**
 * 统一请求时限模块（平台外壳所有 fetch 的超时/取消基础设施）
 *
 * 位置：被 api.js 及各功能模块（files.js、knowledge.js、operations.js 等）复用，
 * 不直接渲染任何页面区域，只在读请求长时间无响应时向页面右下角挂出“取消加载”提示。
 * 依赖接口：不绑定具体接口前缀，包裹任意 operation(signal) 即可；进度提示由 request-progress.js 提供。
 * 与 iframe 内 Vue 应用无关：iframe（apps/analytics）自带 axios 拦截器，不经过这里。
 *
 * 关键约束：
 * - 读请求（GET/HEAD/OPTIONS）超时后提示“可重新加载”，写请求超时文案明确要求先核对执行记录，
 *   因为写请求超时只代表客户端放弃等待，服务端可能仍在执行，自动重试会造成重复提交。
 * - 取消信号同时作用于 fetch 与响应体读取（body 流式读取也可能长期挂起）。
 * - 不要在本模块内做重试；重试策略由调用方决定。
 */

import { showReadProgress } from "./request-progress.js";

/** Bound both fetching and reading the body; never automatically retry writes. */
// 中文补充：为一次请求同时施加「总时限」与「外部取消信号」，并把两者合并成同一条 reject。
// operation 收到的是内部的 AbortSignal，必须透传给 fetch（否则超时后底层连接不会释放）。
export async function withRequestDeadline(operation, { signal, timeoutMs = 120000, method = "GET", requestLabel } = {}) {
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new TypeError("请求超时必须为正数");
  const controller = new AbortController();
  let rejectAbort;
  const cancelled = new Promise((_, reject) => { rejectAbort = reject; });
  const abort = reason => {
    controller.abort(reason);
    rejectAbort(reason);
  };
  const onAbort = () => abort(signal.reason || new DOMException("请求已取消", "AbortError"));
  let removeProgress;
  const readOnly = ["GET", "HEAD", "OPTIONS"].includes(method.toUpperCase());
  // 只有读请求才给「取消加载」入口：写请求中途放弃会让用户误以为没提交，故不提供该按钮
  const progressTimer = readOnly ? setTimeout(() => {
    removeProgress = showReadProgress(() => abort(new DOMException("已取消加载", "AbortError")), requestLabel);
  }, 8000) : null;
  const timer = setTimeout(() => {
    // 文案区分读写：写请求超时属于「结果未知」，必须引导用户先看执行记录/刷新结果
    const error = new Error(readOnly ? "请求超时，请检查网络后重试" : "等待响应超时，服务器可能仍在处理。请先检查执行记录或刷新结果，避免重复提交");
    error.name = "TimeoutError";
    abort(error);
  }, timeoutMs);
  signal?.addEventListener("abort", onAbort, { once: true });
  try {
    if (signal?.aborted) onAbort();
    return await Promise.race([
      cancelled,
      Promise.resolve().then(() => {
        controller.signal.throwIfAborted();
        return operation(controller.signal);
      }),
    ]);
  } finally {
    clearTimeout(timer);
    if (progressTimer !== null) clearTimeout(progressTimer);
    removeProgress?.();
    signal?.removeEventListener("abort", onAbort);
  }
}
