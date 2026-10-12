/**
 * 文件与生成结果模块（路由 /files）
 *
 * 位置：渲染文件目录页：搜索/分类筛选工具栏、文件列表（大小、版本、解析状态）、分页，
 *   以及上传/批量上传、编辑信息、预览、版本记录（下载/恢复/上传新版本）、分类管理、复制、删除等弹窗。
 * 依赖接口前缀：/api/files（列表、/categories、/{id}/metadata、/{id}/download、/{id}/text 预览文本、
 *   /{id}/parse-events 解析记录、/{id}/versions 版本与恢复、/batch 批量上传、/export 导出目录）。
 * 与 iframe 内 Vue 应用无关。
 *
 * 关键约束：
 * - 筛选与分页状态保存在 URL（?q=&category_id=&page=），操作后整页导航重新加载，不使用前端状态缓存。
 * - 权限码必须与后端目录一致：files.create / files.edit / files.delete / files.export；
 *   工具栏按钮与行内“更多”菜单都按权限渲染，缺权限时不显示对应入口。
 * - 删除会同时删除全部分历史版本，属不可逆操作，必须走统一确认弹窗；分类只有空分类可删。
 * - 版本历史不可覆盖：恢复历史版本是“以旧内容创建一个新版本”，不是回滚，文案与实际行为必须一致。
 * - 预览文本超过 50000 字符会被截断并提示下载查看，避免一次性渲染大文本卡死页面。
 * - 分类的上级选择需排除自身（categoryChoices 的 excluded 参数），否则会形成分类环。
 */

import { api } from "./api.js";
import {
  $, $$, can, esc, date, pageHeader, dataTable, emptyState, field, input, select,
  openDialog, confirmAction, toast, statusBadge, pagination, icon,
} from "./ui.js";

const params = () => new URLSearchParams(location.search);
// 中文说明：标签输入同时接受中文逗号、英文逗号与换行，提交前统一规整为英文逗号分隔
const splitTags = value => String(value || "").split(/[，,\n]/).map(item => item.trim()).filter(Boolean);
// 中文说明：字节数展示（B/KB/MB）。后端返回的是字节数，前端不换算成 GB 以免出现 0.0 GB
const bytes = value => {
  const size = Number(value || 0);
  if (size < 1024) return `${size} B`;
  if (size < 1024 ** 2) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 ** 2).toFixed(1)} MB`;
};

// 中文说明：把扁平分类列表整理成带缩进的 <option> 选项（供所有分类下拉复用）。
// excluded 用于编辑场景排除自身；递归 walk 假设后端返回的是无环分类树。
function categoryChoices(categories, excluded = "") {
  const byParent = new Map();
  categories.forEach(item => {
    const key = item.parent_id || "";
    byParent.set(key, [...(byParent.get(key) || []), item]);
  });
  const result = [];
  const walk = (parent, depth) => (byParent.get(parent) || []).forEach(item => {
    if (item.id !== excluded) result.push({ value: item.id, label: `${"— ".repeat(depth)}${item.name}` });
    walk(item.id, depth + 1);
  });
  walk("", 0);
  return result;
}

// 中文说明：新建/编辑分类弹窗。PATCH 时必须回传 version（后端并发校验），POST 不需要。
function categoryDialog(root, categories, record = null) {
  openDialog(record ? "编辑分类" : "新建分类", `<div class="form-grid">
    ${field("分类名称", input("name", record?.name || "", 'required maxlength="120"'))}
    ${field("上级分类", select("parent_id", categoryChoices(categories, record?.id), record?.parent_id || "", "顶级分类"))}
    ${field("显示顺序", input("sort_order", record?.sort_order ?? 0, 'type="number" min="-100000" max="100000"'))}
    ${field("说明", `<textarea name="description" maxlength="1000">${esc(record?.description || "")}</textarea>`, "", true)}
  </div>`, {
    onSave: async form => {
      const payload = {
        name: form.get("name"), parent_id: form.get("parent_id") || null,
        description: form.get("description"), sort_order: Number(form.get("sort_order") || 0),
      };
      if (record) await api.patch(`/api/files/categories/${record.id}`, { ...payload, version: record.version });
      else await api.post("/api/files/categories", payload);
      toast(record ? "分类已更新" : "分类已创建");
      await renderFiles(root);
    },
  });
}

// 中文说明：分类管理弹窗（列表 + 新建/编辑/删除）。
// 注意：删除前必须先 dialog.close()，否则确认弹窗会顶掉当前弹窗的单例状态（openDialog 会复用同一个 dialog 元素）。
function manageCategories(root, categories) {
  const rows = categories.length ? dataTable(["分类", "上级", "文件", "顺序", "操作"], categories, item => `<tr>
    <td><strong>${esc(item.name)}</strong></td><td>${esc(categories.find(parent => parent.id === item.parent_id)?.name || "—")}</td>
    <td>${item.file_count}</td><td>${item.sort_order}</td><td><div class="row-actions">
      <button class="text-button" type="button" data-category-edit="${item.id}">编辑</button>
      <button class="text-button danger" type="button" data-category-delete="${item.id}">删除</button>
    </div></td></tr>`) : emptyState("尚无分类", "可直接新建第一个分类。");
  openDialog("文件分类", `<div class="content-dialog-toolbar"><button class="button primary" type="button" data-category-new>新建分类</button></div>${rows}`, {
    wide: true,
    onReady: dialog => {
      $("[data-category-new]", dialog)?.addEventListener("click", () => categoryDialog(root, categories));
      $$('[data-category-edit]', dialog).forEach(element => element.addEventListener("click", () =>
        categoryDialog(root, categories, categories.find(item => item.id === element.dataset.categoryEdit))));
      $$('[data-category-delete]', dialog).forEach(element => element.addEventListener("click", () => {
        const record = categories.find(item => item.id === element.dataset.categoryDelete);
        dialog.close();
        confirmAction("删除分类", `删除“${record.name}”？只有空分类可以删除。`, async () => {
          await api.delete(`/api/files/categories/${record.id}?version=${record.version}`);
          toast("分类已删除");
          await renderFiles(root);
        }, "删除");
      }));
    },
  });
}

// 中文说明：上传弹窗（单个/批量共用）。提交的是 FormData（含文件），故走 api.upload 而非 api.post。
// 约束：批量最多 20 个文件（与后端上限一致，由 input multiple 与后端共同保证）。
function uploadDialog(root, categories, batch = false) {
  openDialog(batch ? "批量上传" : "上传文件", `<div class="form-grid">
    ${field(batch ? "选择文件（最多 20 个）" : "选择文件", `<input name="${batch ? "files" : "file"}" type="file" ${batch ? "multiple" : ""} required>` , "", true)}
    ${field("分类", select("category_id", categoryChoices(categories), "", "未分类"))}
    ${field("标签", input("tags", "", 'maxlength="1000" placeholder="设备，维护"'), "逗号分隔")}
    ${field("说明", `<textarea name="description" maxlength="2000"></textarea>`, "", true)}
  </div>`, {
    saveLabel: batch ? "上传所选文件" : "上传",
    onSave: async form => {
      form.set("tags", splitTags(form.get("tags")).join(","));
      const result = await api.upload(batch ? "/api/files/batch" : "/api/files", form);
      toast(batch ? `已上传 ${result.count} 个文件` : "文件已上传");
      await renderFiles(root);
    },
  });
}

// 中文说明：编辑文件元信息（名称/分类/标签/说明）。
// 注意提交的是 metadata_version 而不是 version：文件内容版本与元信息版本是两套计数，混用会导致并发校验失败。
function editFileDialog(root, record, categories) {
  openDialog("编辑文件信息", `<div class="form-grid">
    ${field("文件名", input("name", record.name, 'required maxlength="500"'), "保留原扩展名；改名不会转换文件格式")}
    ${field("分类", select("category_id", categoryChoices(categories), record.category_id || "", "未分类"))}
    ${field("标签", input("tags", (record.tags || []).join("，"), 'maxlength="1000"'), "逗号分隔", true)}
    ${field("说明", `<textarea name="description" maxlength="2000">${esc(record.description || "")}</textarea>`, "", true)}
  </div>`, {
    onSave: async form => {
      await api.patch(`/api/files/${record.id}/metadata`, {
        name: form.get("name"),
        version: record.metadata_version, category_id: form.get("category_id") || null,
        description: form.get("description"), tags: splitTags(form.get("tags")),
      });
      toast("文件信息已保存");
      await renderFiles(root);
    },
  });
}

// 中文说明：文本预览弹窗：并行取预览文本与解析事件统计。
// 超过 50000 字符时截断并明确提示“请下载查看完整内容”，避免大文本阻塞渲染。
async function previewFile(record) {
  const [preview, events] = await Promise.all([
    api.get(`/api/files/${record.id}/text`), api.get(`/api/files/${record.id}/parse-events`),
  ]);
  const shown = preview.text.length > 50000 ? preview.text.slice(0, 50000) + "\n\n……预览已截断，请下载文件查看完整内容。" : preview.text;
  openDialog(`预览 · ${record.name}`, `<div class="content-preview-meta"><span>v${preview.revision_number}</span><span>${preview.char_count} 字符</span><span>${events.count} 次解析</span></div><pre class="json-preview content-text-preview">${esc(shown || "文件没有可预览文本")}</pre>`, { wide: true });
}

// 中文说明：上传新版本：创建后旧版本内容仍保留（历史不可覆盖），变更说明必填用于追溯。
function versionUploadDialog(root, record) {
  openDialog("上传新版本", `<div class="form-grid">
    ${field("新文件", '<input name="file" type="file" required>', "", true)}
    ${field("变更说明", input("change_note", "更新文件内容", 'required maxlength="500"'), "", true)}
  </div>`, {
    saveLabel: "创建版本",
    onSave: async form => {
      await api.upload(`/api/files/${record.id}/versions`, form);
      toast("新版本已创建");
      await renderFiles(root);
    },
  });
}

// 中文说明：版本记录弹窗：列出各历史版本，支持下载、恢复（生成新的当前版本）与上传新版本。
// 当前版本不显示“恢复”按钮；恢复属写操作，需 files.edit 权限并走统一确认弹窗。
async function versionsDialog(root, record) {
  const result = await api.get(`/api/files/${record.id}/versions`);
  const table = dataTable(["版本", "文件名", "大小", "说明", "创建时间", "操作"], result.items, item => `<tr>
    <td><strong>v${item.revision_number}</strong>${item.current ? '<span class="cell-secondary">当前</span>' : ""}</td>
    <td>${esc(item.name)}</td><td>${bytes(item.size)}</td><td class="content-wrap">${esc(item.change_note || "—")}</td>
    <td>${date(item.created_at)}</td><td><div class="row-actions">
      <button class="text-button" type="button" data-version-download="${item.file_id}">下载</button>
      ${!item.current && can("files.edit") ? `<button class="text-button" type="button" data-version-restore="${item.revision_number}">恢复</button>` : ""}
    </div></td></tr>`);
  openDialog(`版本记录 · ${record.name}`, `<div class="content-dialog-toolbar">${can("files.edit") ? '<button class="button primary" type="button" data-version-new>上传新版本</button>' : ""}<span>历史版本不会被覆盖</span></div>${table}`, {
    wide: true,
    onReady: dialog => {
      $("[data-version-new]", dialog)?.addEventListener("click", () => versionUploadDialog(root, record));
      $$('[data-version-download]', dialog).forEach(element => element.addEventListener("click", async () => {
        await api.download(`/api/files/${element.dataset.versionDownload}/download`, record.name);
      }));
      $$('[data-version-restore]', dialog).forEach(element => element.addEventListener("click", () => {
        dialog.close();
        const revision = Number(element.dataset.versionRestore);
        confirmAction("恢复历史版本", `将 v${revision} 的内容恢复为一个新的当前版本？`, async () => {
          await api.post(`/api/files/${record.id}/versions/${revision}/restore`, {});
          toast("历史内容已恢复为新版本");
          await renderFiles(root);
        }, "恢复");
      }));
    },
  });
}

// 中文说明：行内“更多”菜单。每个动作都单独按权限渲染（files.export/edit/create/delete），
// 权限码必须与后端目录一致，前端隐藏只是减少误操作，真正的拒绝仍由后端完成。
function actionMenu(record) {
  return `<details class="content-action-menu"><summary>更多</summary><div>
    ${can("files.export") ? `<button type="button" data-file-download="${record.id}">下载</button>` : ""}
    ${can("files.edit") ? `<button type="button" data-file-edit="${record.id}">编辑信息</button>` : ""}
    ${can("files.create") ? `<button type="button" data-file-copy="${record.id}">复制</button>` : ""}
    ${can("files.delete") ? `<button class="danger" type="button" data-file-delete="${record.id}">删除</button>` : ""}
  </div></details>`;
}

/**
 * 中文说明：文件目录页渲染入口。
 * 行为：并行拉取分类与文件列表（筛选/分页取自 URL 查询串，page_size 上限 100，默认 50）；
 *   重写 root 并绑定工具栏、分页（整页导航）、行内预览/版本/下载/编辑/复制/删除事件。
 * 副作用：GET /api/files 与 /api/files/categories；上传、删除、恢复版本等写操作会真实修改文件库。
 * 失败表现：列表加载失败由 main.js 展示错误页；单条操作（预览、版本、下载、复制）失败只 toast。
 */
export async function renderFiles(root) {
  const query = params();
  const q = query.get("q") || "";
  const categoryId = query.get("category_id") || "";
  const page = Math.max(1, Number(query.get("page") || 1));
  const [categoriesResult, result] = await Promise.all([
    api.get("/api/files/categories"),
    api.get(`/api/files?q=${encodeURIComponent(q)}&category_id=${encodeURIComponent(categoryId)}&page=${page}&page_size=${Math.min(100, Number(query.get("page_size") || 50))}`),
  ]);
  const categories = categoriesResult.items || [];
  const actions = [
    can("files.export") ? '<button class="button" type="button" data-files-export>导出目录</button>' : "",
    can("files.edit") ? '<button class="button" type="button" data-categories>分类管理</button>' : "",
    can("files.create") ? '<button class="button" type="button" data-batch-upload>批量上传</button>' : "",
    can("files.create") ? '<button class="button primary" type="button" data-upload>上传文件</button>' : "",
  ].join("");
  const toolbar = `<form class="list-toolbar content-toolbar" data-file-filter>
    <label class="search-input">${icon("search")}<input name="q" aria-label="搜索文件" placeholder="搜索文件名或说明" value="${esc(q)}"></label>
    ${select("category_id", categoryChoices(categories), categoryId, "全部分类").replace("<select", '<select aria-label="文件分类"')}
    <button class="button" type="submit">查询</button>${q || categoryId ? '<button class="button quiet" type="button" data-file-reset>重置</button>' : ""}
    <span class="content-result-count">${result.total} 个文件</span>
  </form>`;
  const table = result.items.length ? dataTable(["文件", "分类", "大小", "版本", "状态", "更新时间", "操作"], result.items, item => `<tr>
    <td class="file-name-cell"><strong class="cell-primary" title="${esc(item.name)}">${esc(item.name)}</strong><span class="cell-secondary" title="${esc(item.mime_type)}">${esc(item.name.includes(".") ? item.name.split(".").at(-1).toUpperCase() + " 文件" : "文件")}</span></td>
    <td>${esc(item.category_name || "未分类")}${item.tags?.length ? `<span class="cell-secondary">${esc(item.tags.join(" · "))}</span>` : ""}</td>
    <td>${bytes(item.size)}</td><td>v${item.revision_number}</td><td>${statusBadge(item.status)}</td><td>${date(item.updated_at || item.created_at)}</td>
    <td><div class="row-actions"><button class="text-button" type="button" data-file-preview="${item.id}">预览</button><button class="text-button" type="button" data-file-versions="${item.id}">版本</button>${actionMenu(item)}</div></td>
  </tr>`) : emptyState("没有匹配文件", q || categoryId ? "调整筛选条件后重试。" : "上传文件后可管理分类、版本与解析记录。", can("files.create") ? '<button class="button primary" type="button" data-upload>上传文件</button>' : "");
  root.innerHTML = pageHeader("文件与生成结果", "分类、版本与解析记录。", actions) + `<section class="panel content-panel">${toolbar}${table}${pagination(result)}</section>`;

  $$('[data-upload]', root).forEach(element => element.addEventListener("click", () => uploadDialog(root, categories)));
  $("[data-batch-upload]", root)?.addEventListener("click", () => uploadDialog(root, categories, true));
  $("[data-categories]", root)?.addEventListener("click", () => manageCategories(root, categories));
  $("[data-files-export]", root)?.addEventListener("click", () => api.download("/api/files/export", "文件目录.csv"));
  $("[data-file-filter]", root)?.addEventListener("submit", event => {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    const next = new URLSearchParams();
    if (form.get("q")) next.set("q", form.get("q"));
    if (form.get("category_id")) next.set("category_id", form.get("category_id"));
    location.assign("/files" + (next.size ? "?" + next : ""));
  });
  $("[data-file-reset]", root)?.addEventListener("click", () => location.assign("/files"));
  $("[data-page-prev]", root)?.addEventListener("click", () => { query.set("page", String(page - 1)); location.assign("/files?" + query); });
  $("[data-page-next]", root)?.addEventListener("click", () => { query.set("page", String(page + 1)); location.assign("/files?" + query); });
  $$('[data-file-preview]', root).forEach(element => element.addEventListener("click", async () => {
    try { await previewFile(result.items.find(item => item.id === element.dataset.filePreview)); }
    catch (error) { toast(error.message, true); }
  }));
  $$('[data-file-versions]', root).forEach(element => element.addEventListener("click", async () => {
    try { await versionsDialog(root, result.items.find(item => item.id === element.dataset.fileVersions)); }
    catch (error) { toast(error.message, true); }
  }));
  $$('[data-file-download]', root).forEach(element => element.addEventListener("click", () => {
    const record = result.items.find(item => item.id === element.dataset.fileDownload);
    api.download(`/api/files/${record.id}/download`, record.name).catch(error => toast(error.message, true));
  }));
  $$('[data-file-edit]', root).forEach(element => element.addEventListener("click", () =>
    editFileDialog(root, result.items.find(item => item.id === element.dataset.fileEdit), categories)));
  $$('[data-file-copy]', root).forEach(element => element.addEventListener("click", async () => {
    const record = result.items.find(item => item.id === element.dataset.fileCopy);
    try { await api.post(`/api/files/${record.id}/copy`, {}); toast("文件已复制"); await renderFiles(root); }
    catch (error) { toast(error.message, true); }
  }));
  $$('[data-file-delete]', root).forEach(element => element.addEventListener("click", () => {
    const record = result.items.find(item => item.id === element.dataset.fileDelete);
    confirmAction("删除文件", `删除“${record.name}”及其全部内容版本？`, async () => {
      await api.delete(`/api/files/${record.id}`); toast("文件及全部版本已删除"); await renderFiles(root);
    }, "删除");
  }));
}
