/**
 * 剪贴板复制模块（平台外壳唯一复制入口）
 *
 * 位置：被权限页、接口目录、密钥/令牌等需要“复制标识或密钥”的模块调用，不渲染任何页面区域。
 * 依赖接口：无网络请求；纯浏览器 API。
 * 与 iframe 内 Vue 应用无关：iframe 使用自身剪贴板逻辑。
 *
 * 关键约束：
 * - 必须同时支持 HTTPS/localhost 与普通局域网 HTTP：后者没有 navigator.clipboard，
 *   只能降级到隐藏 textarea + execCommand("copy")，因此本模块不能用单一方案替换。
 * - 降级路径要求真实用户手势（点击）才有效；不要在没有用户交互的流程（如自动轮询回调）里调用。
 * - 失败时抛出中文错误提示用户手动选中复制，不做静默失败。
 */

/** Copy text on HTTPS, localhost and ordinary LAN HTTP pages. */
// 中文补充：优先使用异步剪贴板 API；被拒绝或不可用时降级为临时 textarea 方案。
// 参数 text：待复制文本。返回 Promise<void>；失败抛出 Error("复制失败，请选中文字后手动复制")。
export async function copyText(text) {
  try {
    if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(text); return; }
  } catch { /* A denied permission can still allow user-initiated selection copying. */ }
  const active = document.activeElement;
  const input = document.createElement("textarea");
  input.value = text;
  input.setAttribute("readonly", "");
  input.style.cssText = "position:fixed;left:-9999px;top:0;opacity:0";
  // Native modal dialogs make the rest of the document inert.
  (document.querySelector("dialog[open]") || document.body).append(input);
  input.select();
  try {
    if (!document.execCommand("copy")) throw new Error("复制失败，请选中文字后手动复制");
  } finally { input.remove(); active?.focus({ preventScroll: true }); }
}
