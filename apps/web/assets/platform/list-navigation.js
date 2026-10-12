/**
 * 列表分页导航模块（服务端分页页面的公共分页逻辑）
 *
 * 位置：用于以 query 参数 ?page=N 进行服务端分页的页面（用户、角色、权限、资源目录等），
 * 只负责分页参数解析、生成分页控件与越界纠正，具体列表渲染由 ui.js 的 wireList 完成。
 * 依赖接口：约定所有分页接口返回 { items, total } 结构，total 为总条数。
 * 与 iframe 内 Vue 应用无关。
 *
 * 关键约束：
 * - 分页必须走整页导航（location.assign/replace），因为这些页面是服务端渲染式模板拼接，
 *   不存在前端状态容器，局部刷新会丢失事件绑定。
 * - 删除最后一条记录或使用过期书签时页码会越界，必须回落到最后一页而不是显示空列表。
 */

import { wireList } from "./ui.js";

// 中文说明：读取当前页码；缺失、非正整数或超出安全整数范围时统一按第 1 页处理。
export function currentPage(search = location.search) {
  const value = Number(new URLSearchParams(search).get("page"));
  return Number.isSafeInteger(value) && value > 0 ? value : 1;
}

// 中文说明：构造目标页码的 URL（只改 page 参数，保留其余查询串与 hash）。
// 参数 page 会被夹到 >=1，避免出现 ?page=0 这类非法地址。
export function pageUrl(page, href = location.href) {
  const url = new URL(href);
  url.searchParams.set("page", String(Math.max(1, page)));
  return url.pathname + url.search + url.hash;
}

// 中文说明：把分页控件的翻页事件接到整页导航上；result 为 { items, total } 结构。
// 副作用：点击分页按钮会触发 location.assign 重新加载页面（不使用前端路由）。
export function wirePageNavigation(root, result) {
  wireList(root, result, ({ page }) => location.assign(pageUrl(page)));
}

// A last-page deletion or a stale bookmark must still lead to usable results.
// 中文补充：列表操作（如删除末页最后一条）后调用；若当前页已超出实际总页数，
// 用 location.replace 跳到最后一页并返回 true（调用方据此终止本次渲染），未越界返回 false。
export function redirectEmptyPage(result, pageSize) {
  const lastPage = Math.max(1, Math.ceil(result.total / pageSize));
  if (currentPage() <= lastPage) return false;
  location.replace(pageUrl(lastPage));
  return true;
}
