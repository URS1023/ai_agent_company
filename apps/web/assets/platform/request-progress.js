/**
 * 只读请求进度提示模块（平台外壳的“取消加载”浮层）
 *
 * 位置：由 request-deadline.js 在读请求超过 8 秒未返回时调用，渲染在页面右下角的固定浮层
 * （容器标记 data-request-progress ），不是页面区域内容，也不参与路由。
 * 依赖接口：无；纯 DOM 实现，取消动作通过回调交回调用方（由调用方 abort 对应请求）。
 * 与 iframe 内 Vue 应用无关：只作用于平台外壳文档，iframe 内部请求由其自身处理。
 *
 * 关键约束：
 * - 仅用于只读请求。写请求不得挂该提示，避免用户在写操作执行中点击取消造成“已提交但未得到结果”的误解。
 * - 按钮只绑定一次点击（once），点完即由调用方 abort，不允许重复触发。
 */

/** Non-blocking cancellation for slow read requests; never retries a mutation. */
// 中文补充：展示一条可取消的读请求提示，返回“移除提示”的清理函数；不阻塞页面交互，也不重试请求。
// cancel：用户点击“取消加载”时的回调（通常是 abort 对应请求）；label：提示文案，默认“页面数据正在加载”。
export function showReadProgress(cancel, label = "页面数据正在加载") {
  // 服务端渲染/测试环境可能没有 document，此时降级为空实现，保证调用方无需判空
  if (typeof document === "undefined") return () => {};
  // 同类提示共用一个容器，多个请求同时超时时纵向堆叠，避免浮层互相覆盖
  let stack = document.querySelector("[data-request-progress]");
  if (!stack) {
    stack = document.createElement("div");
    stack.dataset.requestProgress = "";
    stack.style.cssText = "position:fixed;right:20px;bottom:24px;z-index:4000;display:grid;gap:8px;max-width:calc(100vw - 40px)";
    document.body.appendChild(stack);
  }
  const notice = document.createElement("div");
  notice.style.cssText = "display:flex;align-items:center;gap:16px;padding:12px 16px;border:1px solid #d8e2ef;border-radius:10px;background:#fff;color:#26364b;box-shadow:0 5px 20px #14243c18;font:14px system-ui";
  const text = document.createElement("span");
  text.setAttribute("role", "status");
  text.textContent = label;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "button small";
  button.textContent = "取消加载";
  button.addEventListener("click", cancel, { once: true });
  notice.append(text, button);
  stack.appendChild(notice);
  return () => {
    notice.remove();
    if (!stack.childElementCount) stack.remove();
  };
}
