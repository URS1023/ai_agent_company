/**
 * topLevelNavigation —— 顶层窗口导航工具。
 *
 * 位置：WorkflowWorkbench 等需要"跳出平台模块 iframe、在全屏窗口打开业务编辑器"的地方
 * （打开/离开工作流设计器、报表设计器）。
 * 关键约束：
 * - 只允许同源且以 / 开头的路径：跨域或非绝对路径直接忽略，避免被外部参数带着跳走（防开放重定向）；
 * - 必须操作 window.top 而不是 window，否则页面只会在模块 iframe 内部跳转，用户看到的是"半屏"编辑器。
 */

/** Open full-screen business editors outside the platform module iframe. */
export function openTopLevel(path: string, replace = false) {
  const target = new URL(path, location.origin)
  if (target.origin !== location.origin || !target.pathname.startsWith('/')) return
  const destination = target.pathname + target.search + target.hash
  const host = window.top || window
  if (replace) host.location.replace(destination)
  else host.location.assign(destination)
}
