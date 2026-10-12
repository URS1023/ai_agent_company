/**
 * 资源层级模块（资产“类型 / 业务域”两级归组与资源选择器）
 *
 * 位置：被 capability-governance.js、capabilities.js、asset-catalog.js 等使用，用于把
 * 扁平的资产目录数据整理成「资源类型 → 业务域 → 资源」三级结构，并渲染 <optgroup> 选择器。
 * 依赖接口：loadResourceCatalog 固定调用 GET /api/v1/assets/catalog（limit/offset 分页）。
 * 与 iframe 内 Vue 应用无关。
 *
 * 关键约束：
 * - 分组展示顺序必须与后端下发的类型定义（typeDefinitions）顺序一致，未定义的类型排在最后，
 *   因为该顺序就是权限/治理页面的既定视觉顺序，不能按名称重排。
 * - “未分配”业务域固定排在最后，避免空分类夹在真实业务域之间。
 * - 中文按拼音/数字感知排序（Intl.Collator zh-CN, numeric），不要改成默认字典序。
 */

import { esc } from "./ui.js";

// 中文说明：后端未返回 category/businessDomain 时使用的兜底分组名；与页面文案保持一致
export const UNASSIGNED_RESOURCE_GROUP = "未分配";

const collator = new Intl.Collator("zh-CN", { numeric: true, sensitivity: "base" });

// 内部辅助：统一把可能为 null/undefined/数字的值转成去掉首尾空白的字符串
function text(value) {
  return String(value || "").trim();
}

// 中文说明：取资源的业务域标签：优先 category，其次 businessDomain，都没有则归入“未分配”。
export function resourceGroupLabel(item) {
  return text(item?.category) || text(item?.businessDomain) || UNASSIGNED_RESOURCE_GROUP;
}

// 中文说明：按 `${type}:${id}` 去重合并两组资源（主列表 + 引用列表）；后者字段覆盖前者，
// 但不会丢字段（用于把“被引用资源”的补充字段并入目录项）。缺少 type 或 id 的脏数据直接丢弃。
export function mergeResourceItems(items = [], references = []) {
  const merged = new Map();
  [...items, ...references].forEach(item => {
    if (!item?.type || !item?.id) return;
    const key = `${item.type}:${item.id}`;
    merged.set(key, { ...(merged.get(key) || {}), ...item });
  });
  return [...merged.values()];
}

// 中文说明：把扁平资源列表整理成 [{ type, label, count, categories:[{ label, items }] }]。
// typeDefinitions 决定类型顺序与类型显示名（label 缺失时回落 item.typeLabel，再回落 type 本身）。
// 返回纯数据结构，不做 DOM 操作；无副作用。
export function groupResourceItems(items = [], typeDefinitions = []) {
  const typeOrder = new Map(typeDefinitions.map((item, index) => [item.type, index]));
  const definitions = new Map(typeDefinitions.map(item => [item.type, item]));
  const grouped = new Map();

  items.forEach(item => {
    if (!item?.type || !item?.id) return;
    if (!grouped.has(item.type)) {
      grouped.set(item.type, {
        type: item.type,
        label: text(definitions.get(item.type)?.label) || text(item.typeLabel) || item.type,
        categories: new Map(),
      });
    }
    const typeGroup = grouped.get(item.type);
    const category = resourceGroupLabel(item);
    if (!typeGroup.categories.has(category)) typeGroup.categories.set(category, []);
    typeGroup.categories.get(category).push(item);
  });

  return [...grouped.values()].map(group => {
    const categories = [...group.categories.entries()].map(([label, values]) => ({
      label,
      items: values.sort((left, right) => collator.compare(text(left.name), text(right.name))),
    })).sort((left, right) => {
      if (left.label === UNASSIGNED_RESOURCE_GROUP) return 1;
      if (right.label === UNASSIGNED_RESOURCE_GROUP) return -1;
      return collator.compare(left.label, right.label);
    });
    return { type: group.type, label: group.label, count: categories.reduce((sum, item) => sum + item.items.length, 0), categories };
  }).sort((left, right) => {
    const leftOrder = typeOrder.has(left.type) ? typeOrder.get(left.type) : Number.MAX_SAFE_INTEGER;
    const rightOrder = typeOrder.has(right.type) ? typeOrder.get(right.type) : Number.MAX_SAFE_INTEGER;
    return leftOrder === rightOrder ? collator.compare(left.label, right.label) : leftOrder - rightOrder;
  });
}

// 中文说明：生成资源选择器的 HTML 字符串（<optgroup> 分组 + <option>，值为 `type:id`）。
// selected 需传入同样的 `type:id` 形式才会命中 selected；emptyLabel 传 null 表示不渲染空选项。
// 所有标签都经 esc() 转义，调用方直接 innerHTML 即可，不要再二次拼接用户数据。
export function resourceSelectOptions(items = [], selected = "", emptyLabel = "请选择资源") {
  const empty = emptyLabel === null ? "" : `<option value="">${esc(emptyLabel)}</option>`;
  const groups = groupResourceItems(items);
  return empty + groups.map(typeGroup => typeGroup.categories.map(category => {
    const label = `${typeGroup.label} / ${category.label}`;
    return `<optgroup label="${esc(label)}">${category.items.map(item => {
      const value = `${item.type}:${item.id}`;
      return `<option value="${esc(value)}" ${value === selected ? "selected" : ""}>${esc(item.name || item.id)}</option>`;
    }).join("")}</optgroup>`;
  }).join("")).join("");
}

// 中文说明：分页拉取完整资产目录（GET /api/v1/assets/catalog），返回 { items, total }。
// get：调用方注入的请求函数（便于复用统一鉴权/时限），filters 中空值会被忽略。
// 约束：每页 200 条，最多翻 500 页，防止异常 total 导致无限循环；中途 rows 为空也会终止。
// 副作用：只读批量请求，失败异常向上抛给调用方（调用方负责展示与重试）。
export async function loadResourceCatalog(get, filters = {}) {
  const pageSize = 200;
  const items = [];
  let offset = 0;
  let total = 0;
  let pageCount = 0;

  do {
    const query = new URLSearchParams({ limit: String(pageSize), offset: String(offset) });
    Object.entries(filters).forEach(([key, value]) => {
      const normalized = text(value);
      if (normalized) query.set(key, normalized);
    });
    const page = await get(`/api/v1/assets/catalog?${query.toString()}`);
    const rows = Array.isArray(page?.items) ? page.items : [];
    items.push(...rows);
    total = Number(page?.total || rows.length);
    offset += rows.length;
    pageCount += 1;
    if (!rows.length || pageCount >= 500) break;
  } while (offset < total);

  return { items: mergeResourceItems(items), total };
}
