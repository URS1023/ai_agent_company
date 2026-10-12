/**
 * 系统运维与开发集成模块（/system/operations 与 /system/development 两套标签页共用本文件）
 *
 * 位置：/system/operations：服务状态、公告、消息、调度任务、备份；
 *   /system/development：接口目录（OpenAPI 契约与代码生成）、认证客户端、证书、数据迁移（只读扫描 + 脱敏归档）。
 * 依赖接口前缀：/api/v1/system-tools（/health、/cache、/notices、/inbox、/jobs、/backups、/api-directory、
 *   /client-code、/integration-clients、/certificates、/migration/*），
 *   并复用 /api/v1/platform/openapi.json 与 /api/v1/platform/api-collection 生成接口文档产物；
 *   公告受众选项来自 /api/v1/system/roles 与 /users。
 * 与 iframe 内 Vue 应用无关。
 *
 * 关键约束：
 * - 权限码：system.operations.manage（运维类变更）与 system.development.manage（开发集成类变更），
 *   必须与后端目录一致；无权限时按钮不渲染，列表只读。
 * - 一次性密钥（认证客户端 clientSecret）只在创建/轮换响应中出现一次，必须用弹窗提示“关闭后无法再次查看”；
 *   轮换会让旧密钥立即失效，属影响调用方的动作，走统一确认弹窗。
 * - 证书只接受公开 X.509 PEM，禁止包含私钥（页面文案与后端校验一致，不要放宽提示）。
 * - 备份与迁移都是只读/脱敏取向：备份是“创建并校验”，迁移是“只读扫描来源库 + 脱敏归档”，
 *   不得把它描述为会修改源库；恢复只提供离线方案说明（restore-plan），不在页面内执行恢复。
 * - 手工执行调度任务必须带幂等键（manual-{id}-{时间戳}），避免重复点击造成重复执行。
 * - 调度表达式语义：interval 填秒数，cron 为“分 时 日 月 星期”；时区默认 Asia/Shanghai。
 */

import { currentPage, wirePageNavigation, redirectEmptyPage } from "./list-navigation.js";
import { api, allPages, downloadJson } from "./api.js";
import {
  $, $$, can, esc, date, pageHeader, button, dataTable, emptyState, statusBadge,
  pagination, field, input, select, openDialog, confirmAction, toast,
} from "./ui.js";

const BASE = "/api/v1/system-tools";
// 中文说明：可调度的任务类型（与后端调度器支持的 task_type 一一对应）。
const jobTypes = [
  { value: "workflow", label: "工作流" }, { value: "alert", label: "设备预警工作流" },
  { value: "extraction", label: "知识抽取" }, { value: "notification", label: "公告发布" },
  { value: "health", label: "服务健康检查" }, { value: "datasource_health", label: "数据源健康检查" },
];

// 中文说明：系统运维页标签（键名即 tab 参数，需与 renderSystemOperations 分支一致）。
const operationTabs = [
  ["health", "服务状态"], ["notices", "公告"], ["messages", "消息"],
  ["jobs", "调度任务"], ["backups", "备份"],
];
// 中文说明：开发与集成页标签（键名即 tab 参数，需与 renderSystemDevelopment 分支一致）。
const developmentTabs = [
  ["api", "接口目录"], ["clients", "认证客户端"],
  ["certificates", "证书"], ["migration", "数据迁移"],
];

// 中文说明：读取当前标签页；非法值回落 fallback（两个页面各自的默认页）。
function tabName(values, fallback) {
  const value = new URLSearchParams(location.search).get("tab") || fallback;
  return values.some(([key]) => key === value) ? value : fallback;
}

// 中文说明：标签栏组件（运维页与开发页共用，base 传各自的路由）。
function tabs(base, values, selected) {
  return `<nav class="system-tool-tabs" aria-label="页面功能">${values.map(([key, label]) =>
    `<a href="${base}?tab=${key}" ${selected === key ? 'aria-current="page"' : ""}>${esc(label)}</a>`).join("")}</nav>`;
}

// 中文说明：解析 JSON 文本域；空值返回 fallback，非法 JSON 抛中文错误由弹窗展示。
function safeJson(value, fallback = {}) {
  try { return value?.trim() ? JSON.parse(value) : fallback; }
  catch { throw new Error("JSON 参数格式不正确"); }
}

// 中文说明：多行/逗号分隔文本转数组（用于回调地址、Scope、指定表等字段）。
function lines(value) {
  return String(value || "").split(/[\r\n,]+/).map(item => item.trim()).filter(Boolean);
}

// 中文说明：健康状态 → 颜色语义（degraded 用警告色而不是错误色，避免把“需检查”当故障）。
function tone(status) {
  return status === "healthy" ? "healthy" : status === "degraded" ? "warning" : "unhealthy";
}

// 中文说明：紧凑状态条（各标签页顶部共用）。
function compactStatus(items) {
  return `<div class="system-status-strip">${items.map(item => `<span class="${esc(item.tone || "")}"><i></i><b>${esc(item.label)}</b>${esc(item.value)}</span>`).join("")}</div>`;
}

// 中文说明：服务状态页：并行探测健康/调度/备份（用 allSettled，任一失败仍展示其余信息），
// 仅在 Redis 健康时才额外查询缓存统计（缓存不可用时不算异常）。依赖名与状态做中文映射后展示。
async function renderHealth(root) {
  const [healthResult, jobsResult, backupsResult] = await Promise.allSettled([
    api.get(BASE + "/health"), api.get(BASE + "/jobs"), api.get(BASE + "/backups"),
  ]);
  const health = healthResult.status === "fulfilled" ? healthResult.value : null;
  let cache = null;
  if (health?.dependencies?.redis?.status === "healthy") {
    cache = await api.get(BASE + "/cache").catch(() => null);
  }
  const jobs = jobsResult.status === "fulfilled" ? jobsResult.value.items : [];
  const backups = backupsResult.status === "fulfilled" ? backupsResult.value.items : [];
  const dependencies = health ? Object.entries(health.dependencies).map(([name, value]) => ({ name, ...value })) : [];
  root.insertAdjacentHTML("beforeend",
    compactStatus([
      { label: "平台", value: health?.status === "healthy" ? "正常" : "需检查", tone: tone(health?.status) },
      { label: "调度", value: `${jobs.filter(item => item.status === "active").length} 启用` },
      { label: "缓存", value: cache ? `${cache.keys} 键` : "不可用", tone: cache ? "healthy" : "unhealthy" },
      { label: "备份", value: backups[0] ? date(backups[0].createTime) : "尚无" },
    ]) +
    `<section class="panel system-tools-panel">${dependencies.length ? dataTable(
      ["依赖", "状态", "响应", "版本 / 位置"], dependencies,
      item => `<tr><td><strong>${esc({ database: "数据库", redis: "Redis", search: "搜索服务", storage: "文件存储" }[item.name] || item.name)}</strong></td><td>${statusBadge(({ paused: "已暂停", unread: "未读", read: "已读" })[item.status] || item.status)}</td><td>${esc(item.latencyMs)} ms</td><td class="system-detail-cell">${esc(item.version || item.provider || item.cluster || item.error || "—")}</td></tr>`,
    ) : emptyState("服务状态不可用", healthResult.reason?.message || "稍后刷新重试。")}</section>`);
}

// 中文说明：把 ISO 时间转成 datetime-local 输入所需的本地时间字符串（去时区后缀）。
function localDateTime(value) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "";
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

// 中文说明：公告受众候选（角色 + 用户）。用 allSettled：缺少其中一类权限时另一类仍可用，不阻塞发布公告。
async function audienceOptions() {
  const [roles, users] = await Promise.allSettled([
    allPages("/api/v1/system/roles"),
    allPages("/api/v1/system/users"),
  ]);
  return {
    roles: roles.status === "fulfilled" ? roles.value : [],
    users: users.status === "fulfilled" ? users.value : [],
  };
}

// 中文说明：公告表单（草稿/修改）。受众类型决定实际提交的 ids：all 不带 id，roles/users 分别取对应多选项。
// 注意：新建时必须回传 version:0（后端以 0 表示新建），不要省略该字段。
async function noticeDialog(root, record = null) {
  const options = await audienceOptions();
  const audience = record?.audience || { type: "all", ids: [] };
  const optionMarkup = (items, name) => items.map(item => `<option value="${esc(item.id)}" ${audience.ids?.includes(item.id) ? "selected" : ""}>${esc(item.name || item.display_name || item.username)}</option>`).join("");
  openDialog(record ? "修改公告" : "新建公告", `<div class="form-grid">
    ${field("标题", input("title", record?.title || "", 'required maxlength="300"'), "", true)}
    ${field("类型", select("notice_type", [{ value: "announcement", label: "公告" }, { value: "maintenance", label: "维护" }, { value: "security", label: "安全" }], record?.noticeType || "announcement", null))}
    ${field("级别", select("severity", [{ value: "info", label: "一般" }, { value: "warning", label: "提醒" }, { value: "critical", label: "严重" }], record?.severity || "info", null))}
    ${field("受众", select("audience_type", [{ value: "all", label: "全部启用成员" }, ...(options.roles.length ? [{ value: "roles", label: "指定角色" }] : []), ...(options.users.length ? [{ value: "users", label: "指定用户" }] : [])], audience.type, null))}
    ${field("指定角色", `<select name="role_ids" multiple size="6">${optionMarkup(options.roles, "roles")}</select>`, "按 Ctrl / Cmd 可多选", true)}
    ${field("指定用户", `<select name="user_ids" multiple size="6">${optionMarkup(options.users, "users")}</select>`, "按 Ctrl / Cmd 可多选", true)}
    ${field("正文", `<textarea name="body" required maxlength="30000">${esc(record?.body || "")}</textarea>`, "", true)}
    ${field("到期时间", input("expires_at", record?.expiresAt ? localDateTime(record.expiresAt) : "", 'type="datetime-local"'))}
  </div>`, {
    wide: true,
    onReady: dialog => {
      const sync = () => {
        const value = $('[name="audience_type"]', dialog).value;
        $('[name="role_ids"]', dialog).closest("label").hidden = value !== "roles";
        $('[name="user_ids"]', dialog).closest("label").hidden = value !== "users";
      };
      $('[name="audience_type"]', dialog).addEventListener("change", sync); sync();
    },
    onSave: async form => {
      const type = form.get("audience_type");
      const ids = type === "roles" ? form.getAll("role_ids") : type === "users" ? form.getAll("user_ids") : [];
      const payload = {
        title: form.get("title"), body: form.get("body"), noticeType: form.get("notice_type"),
        severity: form.get("severity"), audienceType: type, audienceIds: ids,
        expiresAt: form.get("expires_at") ? new Date(form.get("expires_at")).toISOString() : null,
        version: record?.version || 0,
      };
      if (record) await api.patch(BASE + "/notices/" + record.id, payload);
      else await api.post(BASE + "/notices", payload);
      toast("公告草稿已保存"); await renderSystemOperations(root);
    },
  });
}

// 中文说明：公告列表页。只有草稿可编辑/发布；发布是向受众真实投递站内消息，走统一确认弹窗；
// 归档保留已投递消息，不撤回。
async function renderNotices(root) {
  const result = await api.get(BASE + "/notices");
  root.insertAdjacentHTML("beforeend", `<section class="panel system-tools-panel"><div class="system-panel-actions">${can("system.operations.manage") ? button("新建公告", "data-new-notice", true) : ""}</div>${result.items.length ? dataTable(
    ["标题", "类型", "级别", "受众", "状态", "已投递", "更新时间", "操作"], result.items,
    item => `<tr><td class="cell-primary">${esc(item.title)}</td><td>${esc(item.noticeType)}</td><td>${esc(item.severity)}</td><td>${esc(item.audience.type)}</td><td>${statusBadge(({ paused: "已暂停", unread: "未读", read: "已读" })[item.status] || item.status)}</td><td>${item.deliveredCount}</td><td>${date(item.updateTime)}</td><td><div class="row-actions">${item.status === "draft" && can("system.operations.manage") ? `<button class="text-button" data-edit-notice="${item.id}">编辑</button><button class="text-button" data-publish-notice="${item.id}">发布</button>` : ""}${item.status !== "archived" && can("system.operations.manage") ? `<button class="text-button danger" data-archive-notice="${item.id}">归档</button>` : ""}</div></td></tr>`,
  ) : emptyState("暂无公告", "新建草稿后再选择发布。")}</section>`);
  $("[data-new-notice]", root)?.addEventListener("click", () => noticeDialog(root));
  $$('[data-edit-notice]', root).forEach(el => el.addEventListener("click", () => noticeDialog(root, result.items.find(item => item.id === el.dataset.editNotice))));
  $$('[data-publish-notice]', root).forEach(el => el.addEventListener("click", () => {
    const item = result.items.find(value => value.id === el.dataset.publishNotice);
    confirmAction("发布公告", `向“${item.title}”的当前有效受众投递站内消息？`, async () => {
      await api.post(BASE + `/notices/${item.id}/publish`, { version: item.version });
      toast("公告已发布"); await renderSystemOperations(root);
    }, "发布");
  }));
  $$('[data-archive-notice]', root).forEach(el => el.addEventListener("click", () => {
    const item = result.items.find(value => value.id === el.dataset.archiveNotice);
    confirmAction("归档公告", `归档“${item.title}”？已投递消息会保留。`, async () => {
      await api.post(BASE + `/notices/${item.id}/archive`, { version: item.version });
      toast("公告已归档"); await renderSystemOperations(root);
    }, "归档");
  }));
}

// 中文说明：站内消息收件箱（当前用户），标记已读/归档都会带 version 并整页重绘。
async function renderMessages(root) {
  const page = currentPage();
  const result = await api.get(BASE + `/inbox?limit=50&offset=${(page - 1) * 50}`);
  if (redirectEmptyPage(result, 50)) return;
  const paging = { total: result.total, page, page_size: 50 };
  root.insertAdjacentHTML("beforeend", compactStatus([{ label: "未读", value: result.unread }, { label: "全部", value: result.total }]) +
    `<section class="panel system-tools-panel">${result.items.length ? dataTable(
      ["标题", "类型", "级别", "状态", "时间", "操作"], result.items,
      item => `<tr><td><strong>${esc(item.subject)}</strong><span class="cell-secondary system-message-preview">${esc(item.body)}</span></td><td>${esc(item.messageType)}</td><td>${esc(item.severity)}</td><td>${statusBadge(({ paused: "已暂停", unread: "未读", read: "已读" })[item.status] || item.status)}</td><td>${date(item.createTime)}</td><td><div class="row-actions">${item.status === "unread" ? `<button class="text-button" data-read-message="${item.id}">已读</button>` : ""}<button class="text-button danger" data-archive-message="${item.id}">归档</button></div></td></tr>`,
    ) : emptyState("暂无消息", "公告发布后会出现在对应成员的收件箱。")}${pagination(paging)}</section>`);
  wirePageNavigation(root, paging);
  $$('[data-read-message]', root).forEach(el => el.addEventListener("click", async () => {
    const item = result.items.find(value => value.id === el.dataset.readMessage);
    await api.post(BASE + `/inbox/${item.id}/read`, { version: item.version }); await renderSystemOperations(root);
  }));
  $$('[data-archive-message]', root).forEach(el => el.addEventListener("click", async () => {
    const item = result.items.find(value => value.id === el.dataset.archiveMessage);
    await api.post(BASE + `/inbox/${item.id}/archive`, { version: item.version }); await renderSystemOperations(root);
  }));
}

// 中文说明：调度任务表单。任务编码创建后只读（作为调度标识）；间隔填秒数、Cron 为“分 时 日 月 星期”；
// 执行参数为 JSON，最终校验由对应业务接口完成。新建时同样带 version:0。
function jobDialog(root, record = null) {
  openDialog(record ? "修改调度任务" : "新建调度任务", `<div class="form-grid">
    ${field("任务编码", input("code", record?.code || "", `required maxlength="80" pattern="[a-z][a-z0-9_.\\-]+" ${record ? "readonly" : ""}`))}
    ${field("任务名称", input("name", record?.name || "", 'required maxlength="200"'))}
    ${field("任务类型", select("task_type", jobTypes, record?.taskType || "health", null))}
    ${field("业务目标 ID", input("target_id", record?.targetId || "", 'maxlength="80"'), "服务健康检查无需填写；数据源健康检查填写数据源 ID")}
    ${field("调度方式", select("schedule_kind", [{ value: "interval", label: "固定间隔" }, { value: "cron", label: "Cron" }], record?.scheduleKind || "interval", null))}
    ${field("表达式", input("schedule_expression", record?.scheduleExpression || "3600", 'required maxlength="160"'), "间隔填写秒数；Cron 为“分 时 日 月 星期”")}
    ${field("时区", input("timezone", record?.timezone || "Asia/Shanghai", 'required maxlength="64"'))}
    ${field("状态", select("status", [{ value: "paused", label: "暂停" }, { value: "active", label: "启用" }], record?.status || "paused", null))}
    ${field("执行参数 JSON", `<textarea name="payload">${esc(JSON.stringify(record?.payload || {}, null, 2))}</textarea>`, "参数按对应业务接口校验", true)}
    ${field("说明", `<textarea name="description" maxlength="1000">${esc(record?.description || "")}</textarea>`, "", true)}
  </div>`, {
    wide: true,
    onSave: async form => {
      const payload = {
        code: form.get("code"), name: form.get("name"), description: form.get("description"),
        taskType: form.get("task_type"), targetId: form.get("target_id"),
        scheduleKind: form.get("schedule_kind"), scheduleExpression: form.get("schedule_expression"),
        timezone: form.get("timezone"), payload: safeJson(form.get("payload")), status: form.get("status"),
        version: record?.version || 0,
      };
      if (record) await api.patch(BASE + "/jobs/" + record.id, payload); else await api.post(BASE + "/jobs", payload);
      toast("调度任务已保存"); await renderSystemOperations(root);
    },
  });
}

// 中文说明：调度任务列表页：编辑、立即执行（确认弹窗 + 幂等键）、暂停/启用、归档。
// 立即执行会产生真实业务副作用（例如发公告、跑工作流），必须由用户显式确认。
async function renderJobs(root) {
  const result = await api.get(BASE + "/jobs");
  root.insertAdjacentHTML("beforeend", `<section class="panel system-tools-panel"><div class="system-panel-actions">${can("system.operations.manage") ? button("新建调度", "data-new-job", true) : ""}</div>${result.items.length ? dataTable(
    ["任务", "类型", "计划", "状态", "下次执行", "最近执行", "操作"], result.items,
    item => `<tr><td><strong>${esc(item.name)}</strong><span class="cell-secondary">${esc(item.code)}</span></td><td>${esc(jobTypes.find(type => type.value === item.taskType)?.label || item.taskType)}</td><td><code>${esc(item.scheduleKind === "cron" ? item.scheduleExpression : item.scheduleExpression + " 秒")}</code></td><td>${statusBadge(({ paused: "已暂停", unread: "未读", read: "已读" })[item.status] || item.status)}</td><td>${date(item.nextRunAt)}</td><td>${date(item.lastRunAt)}</td><td><div class="row-actions">${can("system.operations.manage") ? `<button class="text-button" data-edit-job="${item.id}">编辑</button><button class="text-button" data-run-job="${item.id}">执行</button>${item.status === "active" ? `<button class="text-button" data-job-action="pause" data-job="${item.id}">暂停</button>` : `<button class="text-button" data-job-action="activate" data-job="${item.id}">启用</button>`}<button class="text-button danger" data-job-action="archive" data-job="${item.id}">归档</button>` : "只读"}</div></td></tr>`,
  ) : emptyState("暂无调度任务", "创建健康检查、工作流、预警、抽取或公告调度。")}</section>`);
  $("[data-new-job]", root)?.addEventListener("click", () => jobDialog(root));
  $$('[data-edit-job]', root).forEach(el => el.addEventListener("click", () => jobDialog(root, result.items.find(item => item.id === el.dataset.editJob))));
  $$('[data-run-job]', root).forEach(el => el.addEventListener("click", () => {
    const item = result.items.find(value => value.id === el.dataset.runJob);
    confirmAction("执行调度任务", `立即执行“${item.name}”？`, async () => {
      const key = `manual-${item.id}-${Date.now()}`;
      const run = await api.post(BASE + `/jobs/${item.id}/run`, { version: item.version, idempotencyKey: key });
      toast(run.status === "completed" ? "任务执行完成" : "任务执行失败", run.status !== "completed");
      await renderSystemOperations(root);
    }, "立即执行");
  }));
  $$('[data-job-action]', root).forEach(el => el.addEventListener("click", async () => {
    const item = result.items.find(value => value.id === el.dataset.job);
    await api.post(BASE + `/jobs/${item.id}/status/${el.dataset.jobAction}`, { version: item.version });
    toast("任务状态已更新"); await renderSystemOperations(root);
  }));
}

// 中文说明：下载二进制/文件响应（备份包等）。直接 fetch 以获取原始 Response；
// 必须带 Authorization 与 X-Workspace-Id，文件名优先取 Content-Disposition，其次 fallback。
async function fetchDownload(path, fallback) {
  const response = await fetch(path, { headers: { Authorization: "Bearer " + api.token, "X-Workspace-Id": api.workspace } });
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).message || "下载失败");
  const blob = await response.blob();
  const disposition = response.headers.get("Content-Disposition") || "";
  const name = disposition.match(/filename="?([^";]+)"?/)?.[1] || fallback;
  const url = URL.createObjectURL(blob); const link = document.createElement("a");
  link.href = url; link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// 中文说明：备份页：创建备份（需后端确认字串 CREATE_BACKUP）、下载、查看离线恢复方案。
// 约束：恢复方案只做说明展示（restore-plan 返回步骤与原因），本页不执行任何恢复操作；
//   备份名建议写明用途（如“上线前备份”）。
async function renderBackups(root) {
  const result = await api.get(BASE + "/backups");
  root.insertAdjacentHTML("beforeend", `<section class="panel system-tools-panel"><div class="system-panel-actions">${can("system.operations.manage") ? button("创建备份", "data-new-backup", true) : ""}</div>${result.items.length ? dataTable(
    ["名称", "数据库", "大小", "校验", "时间", "操作"], result.items,
    item => `<tr><td><strong>${esc(item.name)}</strong></td><td>${esc(item.databaseProvider)} / ${esc(item.databaseName)}</td><td>${(item.size / 1024 / 1024).toFixed(2)} MB</td><td><code class="checksum">${esc(item.sha256.slice(0, 16))}…</code></td><td>${date(item.createTime)}</td><td><div class="row-actions"><button class="text-button" data-download-backup="${item.id}">下载</button>${can("system.operations.manage") ? `<button class="text-button" data-restore-plan="${item.id}">恢复方案</button>` : ""}</div></td></tr>`,
  ) : emptyState("暂无备份", "需要时创建当前数据库的一致性备份。")}</section>`);
  $("[data-new-backup]", root)?.addEventListener("click", () => openDialog("创建数据库备份", field("备份名称", input("name", "", 'required maxlength="200" placeholder="例如：上线前备份"')), {
    saveLabel: "创建并校验", onSave: async form => { await api.post(BASE + "/backups", { name: form.get("name"), confirmation: "CREATE_BACKUP" }); toast("备份已创建并通过校验"); await renderSystemOperations(root); },
  }));
  $$('[data-download-backup]', root).forEach(el => el.addEventListener("click", () => fetchDownload(BASE + `/backups/${el.dataset.downloadBackup}/download`, "cloud-brain-backup").catch(error => toast(error.message, true))));
  $$('[data-restore-plan]', root).forEach(el => el.addEventListener("click", async () => {
    try {
      const plan = await api.get(BASE + `/backups/${el.dataset.restorePlan}/restore-plan`);
      openDialog("离线恢复方案", `<ol class="restore-steps">${plan.steps.map(step => `<li>${esc(step)}</li>`).join("")}</ol><p class="inline-warning">${esc(plan.reason)}</p>`, { wide: true });
    } catch (error) { toast(error.message, true); }
  }));
}

/**
 * 中文说明：系统运维页入口（/system/operations）：按 ?tab 渲染服务状态 / 公告 / 消息 / 调度任务 / 备份。
 * 副作用：先重写页头与标签页，子视图用 insertAdjacentHTML 追加；
 *   公告发布、调度任务变更、创建备份等为写操作。异常由 main.js 统一渲染错误页。
 */
export async function renderSystemOperations(root) {
  const selected = tabName(operationTabs, "health");
  root.innerHTML = pageHeader("系统运维", "服务、公告、调度与备份") + tabs("/system/operations", operationTabs, selected);
  if (selected === "health") await renderHealth(root);
  else if (selected === "notices") await renderNotices(root);
  else if (selected === "messages") await renderMessages(root);
  else if (selected === "jobs") await renderJobs(root);
  else await renderBackups(root);
}

// 中文说明：接口目录页：来自运行时 OpenAPI 的接口清单（含说明完整度与请求示例覆盖率统计），
// 支持关键词搜索、分页（前端切片，pageSize=50）、查看契约、生成客户端代码，以及下载 OpenAPI/Postman。
// 契约弹窗展示 request/response 示例，属于对外承诺，不要在弹窗内改写字段名。
async function renderApiDirectory(root) {
  const query = new URLSearchParams(location.search).get("q") || "";
  const result = await api.get(BASE + "/api-directory?keyword=" + encodeURIComponent(query));
  const pageSize = 50;
  const pageCount = Math.max(1, Math.ceil(result.items.length / pageSize));
  const requestedPage = Number(new URLSearchParams(location.search).get("page")) || 1;
  const page = Math.min(pageCount, Math.max(1, Math.floor(requestedPage)));
  const pageItems = result.items.slice((page - 1) * pageSize, page * pageSize);
  root.insertAdjacentHTML("beforeend", compactStatus([
    { label: "接口", value: result.quality.operations },
    { label: "完整说明", value: result.quality.describedOperations },
    { label: "请求示例", value: `${result.quality.requestBodiesWithExamples}/${result.quality.requestBodies}` },
  ]) + `<section class="panel system-tools-panel"><form class="list-toolbar" data-api-filter><label class="search-input"><span>⌕</span><input name="q" value="${esc(query)}" placeholder="名称、路径或 operationId" aria-label="搜索接口"></label><button class="button" type="submit">查询</button><button class="button" type="button" data-download-openapi>OpenAPI</button><button class="button" type="button" data-download-postman data-download-collection>Postman</button></form>${result.items.length ? dataTable(
    ["方法", "路径", "说明", "模块", "操作"], pageItems,
    item => `<tr><td><span class="api-method">${esc(item.method)}</span></td><td><code class="api-path">${esc(item.path)}</code></td><td>${esc(item.summary)}</td><td>${esc(item.tag)}</td><td><div class="row-actions"><button class="text-button" data-api-detail="${esc(item.operationId)}" data-operation="${esc(item.operationId)}">契约</button><button class="text-button" data-api-code="${esc(item.operationId)}">代码</button></div></td></tr>`,
  ) : emptyState("没有匹配接口", "调整关键词后重试。")}</section>`);
  root.insertAdjacentHTML("beforeend", `<nav class="list-toolbar" aria-label="接口目录分页"><span>共 ${result.items.length} 项 · 第 ${page} / ${pageCount} 页</span><button class="button" data-api-page="${page - 1}" ${page === 1 ? "disabled" : ""}>上一页</button><button class="button" data-api-page="${page + 1}" ${page === pageCount ? "disabled" : ""}>下一页</button></nav>`);
  $$('[data-api-page]', root).forEach(element => element.addEventListener("click", () => {
    location.assign(`/system/development?${new URLSearchParams({ tab: 'api', q: query, page: element.dataset.apiPage })}`);
  }));
  $("[data-api-filter]", root).addEventListener("submit", event => { event.preventDefault(); const value = new FormData(event.currentTarget).get("q"); location.assign("/system/development?tab=api&q=" + encodeURIComponent(value)); });
  $("[data-download-openapi]", root).addEventListener("click", async () => downloadJson(await api.get("/api/v1/platform/openapi.json"), "cloud-brain-openapi.json"));
  $("[data-download-postman]", root).addEventListener("click", async () => downloadJson(await api.get("/api/v1/platform/api-collection"), "cloud-brain-postman.json"));
  $$('[data-api-detail]', root).forEach(el => el.addEventListener("click", () => {
    const item = result.items.find(value => value.operationId === el.dataset.apiDetail);
    openDialog(item.summary, `<dl class="detail-grid"><div><dt>operationId</dt><dd><code>${esc(item.operationId)}</code></dd></div><div><dt>调用</dt><dd>${esc(item.method)} <code>${esc(item.path)}</code></dd></div></dl><p class="contract-description">${esc(item.description)}</p><pre class="json-preview">${esc(JSON.stringify({ request: item.requestExample, response: item.responseExample }, null, 2))}</pre>`, { wide: true });
  }));
  $$('[data-api-code]', root).forEach(el => el.addEventListener("click", () => openDialog("生成接口客户端", field("语言", select("language", [{ value: "python", label: "Python" }, { value: "typescript", label: "TypeScript" }], "python", null)), {
    saveLabel: "生成并下载", onSave: async form => fetchDownloadFromPost({ operationIds: [el.dataset.apiCode], language: form.get("language"), includeExamples: true }),
  })));
}

// 中文说明：生成客户端代码并下载（POST /client-code）。语言决定文件扩展名（python→py、typescript→ts）；
// 同样需要手动带鉴权与工作区头。
async function fetchDownloadFromPost(payload) {
  const response = await fetch(BASE + "/client-code", { method: "POST", headers: { Authorization: "Bearer " + api.token, "X-Workspace-Id": api.workspace, "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).message || "代码生成失败");
  const text = await response.text();
  const extension = payload.language === "python" ? "py" : "ts";
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  const link = document.createElement("a"); link.href = url; link.download = `cloud-brain-client.${extension}`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// 中文说明：认证客户端表单：回调地址需 HTTPS、Scope 不能超过当前账号权限（后端校验）。
// 副作用：创建/修改后若返回 clientSecret，必须用一次性弹窗展示并提示“关闭后无法再次查看”；
//   这里用 setTimeout 延后打开，避免被上一个弹窗的关闭流程覆盖。
function clientDialog(root, record = null) {
  openDialog(record ? "修改认证客户端" : "新建认证客户端", `<div class="form-grid">
    ${field("名称", input("name", record?.name || "", 'required maxlength="160"'))}
    ${field("启用", select("enabled", [{ value: "true", label: "启用" }, { value: "false", label: "停用" }], String(record?.enabled ?? true), null))}
    ${field("回调地址", `<textarea name="redirect_uris" placeholder="每行一个 HTTPS 地址">${esc((record?.redirectUris || []).join("\n"))}</textarea>`, "", true)}
    ${field("权限范围", `<textarea name="scopes" placeholder="每行一个权限编码">${esc((record?.scopes || []).join("\n"))}</textarea>`, "不能超过当前账号权限", true)}
  </div>`, { wide: true, onSave: async form => {
    const payload = { name: form.get("name"), redirectUris: lines(form.get("redirect_uris")), scopes: lines(form.get("scopes")), enabled: form.get("enabled") === "true", version: record?.version || 0 };
    const saved = record ? await api.patch(BASE + "/integration-clients/" + record.id, payload) : await api.post(BASE + "/integration-clients", payload);
    await renderSystemDevelopment(root);
    if (saved.clientSecret) setTimeout(() => openDialog("请立即保存客户端密钥", `<div class="one-time-secret"><p>关闭后无法再次查看。</p><code>${esc(saved.clientSecret)}</code></div>`), 0);
    else toast("认证客户端已保存");
  }});
}

// 中文说明：认证客户端列表页：编辑、轮换密钥（旧密钥立即失效，走确认弹窗并展示新密钥一次）、停用。
async function renderClients(root) {
  const result = await api.get(BASE + "/integration-clients");
  root.insertAdjacentHTML("beforeend", `<section class="panel system-tools-panel"><div class="system-panel-actions">${can("system.development.manage") ? button("新建客户端", "data-new-client", true) : ""}</div>${result.items.length ? dataTable(
    ["名称", "Client ID", "密钥前缀", "权限", "状态", "最近使用", "操作"], result.items,
    item => `<tr><td><strong>${esc(item.name)}</strong></td><td><code>${esc(item.clientId)}</code></td><td><code>${esc(item.secretPrefix)}…</code></td><td>${item.scopes.length}</td><td>${statusBadge(item.enabled)}</td><td>${date(item.lastUsedAt)}</td><td><div class="row-actions">${can("system.development.manage") ? `<button class="text-button" data-edit-client="${item.id}">编辑</button><button class="text-button" data-rotate-client="${item.id}">轮换密钥</button>${item.enabled ? `<button class="text-button danger" data-disable-client="${item.id}">停用</button>` : ""}` : "只读"}</div></td></tr>`,
  ) : emptyState("暂无认证客户端", "按系统权限创建受限的接口客户端。")}</section>`);
  $("[data-new-client]", root)?.addEventListener("click", () => clientDialog(root));
  $$('[data-edit-client]', root).forEach(el => el.addEventListener("click", () => clientDialog(root, result.items.find(item => item.id === el.dataset.editClient))));
  $$('[data-rotate-client]', root).forEach(el => el.addEventListener("click", () => {
    const item = result.items.find(value => value.id === el.dataset.rotateClient);
    confirmAction("轮换客户端密钥", `旧密钥将立即失效，是否轮换“${item.name}”？`, async () => {
      const saved = await api.post(BASE + `/integration-clients/${item.id}/rotate`, { version: item.version });
      await renderSystemDevelopment(root);
      setTimeout(() => openDialog("请立即保存新密钥", `<div class="one-time-secret"><p>关闭后无法再次查看。</p><code>${esc(saved.clientSecret)}</code></div>`), 0);
    }, "轮换密钥");
  }));
  $$('[data-disable-client]', root).forEach(el => el.addEventListener("click", async () => {
    const item = result.items.find(value => value.id === el.dataset.disableClient);
    await api.post(BASE + `/integration-clients/${item.id}/disable`, { version: item.version }); toast("客户端已停用"); await renderSystemDevelopment(root);
  }));
}

// 中文说明：证书登记页。只接受公开 X.509 PEM（不得含私钥，后端会拒绝），指纹只展示前 16 位。
// 停用证书会影响依赖该证书的签名/校验（后端控制），此处不做额外前置判断。
async function renderCertificates(root) {
  const result = await api.get(BASE + "/certificates");
  root.insertAdjacentHTML("beforeend", `<section class="panel system-tools-panel"><div class="system-panel-actions">${can("system.development.manage") ? button("登记证书", "data-new-certificate", true) : ""}</div>${result.items.length ? dataTable(
    ["名称", "用途", "主体", "有效期", "指纹", "状态", "操作"], result.items,
    item => `<tr><td><strong>${esc(item.name)}</strong></td><td>${esc(item.kind)}</td><td class="system-detail-cell">${esc(item.subject)}</td><td>${date(item.notAfter)}</td><td><code class="checksum">${esc(item.fingerprintSha256.slice(0, 16))}…</code></td><td>${statusBadge(item.enabled)}</td><td>${item.enabled && can("system.development.manage") ? `<button class="text-button danger" data-disable-cert="${item.id}">停用</button>` : ""}</td></tr>`,
  ) : emptyState("暂无可信证书", "登记公开 X.509 证书；私钥不会被接受。")}</section>`);
  $("[data-new-certificate]", root)?.addEventListener("click", () => openDialog("登记公开证书", `<div class="form-grid">${field("名称", input("name", "", 'required maxlength="200"'))}${field("用途", select("kind", [{ value: "trusted_ca", label: "可信 CA" }, { value: "client_signing", label: "客户端签名" }], "trusted_ca", null))}${field("X.509 PEM", '<textarea name="pem" required class="pem-input"></textarea>', "仅公开证书，不得包含私钥", true)}</div>`, {
    wide: true, onSave: async form => { await api.post(BASE + "/certificates", { name: form.get("name"), kind: form.get("kind"), pem: form.get("pem"), enabled: true }); toast("证书已登记"); await renderSystemDevelopment(root); },
  }));
  $$('[data-disable-cert]', root).forEach(el => el.addEventListener("click", async () => {
    const item = result.items.find(value => value.id === el.dataset.disableCert);
    await api.post(BASE + `/certificates/${item.id}/disable`, { version: item.version }); toast("证书已停用"); await renderSystemDevelopment(root);
  }));
}

// 中文说明：数据迁移页（白名单来源库的只读扫描 + 脱敏归档）。
// 约束：扫描是只读的（不修改来源库），归档记录已脱敏；批次校验和（checksum）用于核对批次完整性。
// 失败的批次若无归档记录，弹窗只展示失败原因与“来源数据库未被修改”的说明，不尝试重试。
async function renderMigration(root) {
  const [sources, batches] = await Promise.all([api.get(BASE + "/migration/sources"), api.get(BASE + "/migration/batches")]);
  root.insertAdjacentHTML("beforeend", compactStatus([
    { label: "来源", value: sources.schemas.length }, { label: "批次", value: batches.items.length },
    { label: "策略", value: "只读 / 脱敏", tone: "healthy" },
  ]) + `<section class="panel system-tools-panel"><div class="system-panel-actions">${can("system.development.manage") && sources.schemas.length ? button("扫描来源库", "data-new-migration", true) : ""}</div>${batches.items.length ? dataTable(
    ["来源", "状态", "表 / 行", "脱敏归档", "校验", "完成时间", "操作"], batches.items,
    item => `<tr><td><strong>${esc(item.sourceName)}</strong><span class="cell-secondary">${esc(item.sourceSchema)}</span></td><td>${statusBadge(({ paused: "已暂停", unread: "未读", read: "已读" })[item.status] || item.status)}</td><td>${Object.keys(item.tableCounts).length} / ${item.rowCount}</td><td>${item.archivedCount}</td><td><code class="checksum">${esc(item.checksum.slice(0, 16) || "—")}…</code></td><td>${date(item.completeTime)}</td><td><button class="text-button" data-migration-records="${item.id}">记录</button></td></tr>`,
  ) : emptyState("暂无迁移批次", "选择白名单来源库执行只读扫描。")}</section>`);
  $("[data-new-migration]", root)?.addEventListener("click", () => openDialog("扫描来源数据库", `<div class="form-grid">${field("来源库", select("source_schema", sources.schemas.map(value => ({ value, label: value })), sources.schemas[0], null))}${field("批次名称", input("source_name", "", 'required maxlength="200"'))}${field("指定表", `<textarea name="include_tables" placeholder="留空扫描全部；也可每行填写一个表名"></textarea>`, "", true)}</div>`, {
    wide: true, saveLabel: "开始只读扫描", onSave: async form => { await api.post(BASE + "/migration/batches", { sourceSchema: form.get("source_schema"), sourceName: form.get("source_name"), includeTables: lines(form.get("include_tables")), archiveRows: true }); toast("来源扫描完成"); await renderSystemDevelopment(root); },
  }));
  $$('[data-migration-records]', root).forEach(el => el.addEventListener("click", async () => {
    try {
      const result = await api.get(BASE + `/migration/batches/${el.dataset.migrationRecords}/records?limit=100`);
      const batch = batches.items.find(item => item.id === el.dataset.migrationRecords);
      if (batch?.status === "failed" && !result.items.length) {
        openDialog("来源扫描失败", `<p>本次扫描未完成，没有可查看的归档记录。来源数据库未被修改。</p><p><strong>失败原因</strong></p><pre class="json-preview">${esc(batch.summary?.message || "未保存详细错误，请查看该批次时间对应的服务日志。")}</pre>`, { wide: true });
        return;
      }
      openDialog("脱敏来源记录", result.items.length ? dataTable(["来源表", "来源键", "状态", "目标", "摘要"], result.items, item => `<tr><td>${esc(item.sourceTable)}</td><td><code>${esc(item.sourceKey)}</code></td><td>${statusBadge(({ paused: "已暂停", unread: "未读", read: "已读" })[item.status] || item.status)}</td><td>${esc(item.targetType || "—")}</td><td><button type="button" class="text-button" data-view-payload="${item.id}">查看</button></td></tr>`) : emptyState("没有归档记录", "该批次只保存了表级统计。"), { wide: true, onReady: dialog => $$('[data-view-payload]', dialog).forEach(button => button.addEventListener("click", () => { const row = result.items.find(item => item.id === button.dataset.viewPayload); openDialog("来源记录摘要", `<pre class="json-preview">${esc(JSON.stringify(row.payload, null, 2))}</pre>`, { wide: true }); })) });
    } catch (error) { toast(error.message, true); }
  }));
}

/**
 * 中文说明：开发与集成页入口（/system/development）：按 ?tab 渲染接口目录 / 认证客户端 / 证书 / 数据迁移。
 * 副作用：先重写页头与标签页，子视图追加内容；客户端创建/轮换与证书登记、迁移扫描为写操作。
 */
export async function renderSystemDevelopment(root) {
  const selected = tabName(developmentTabs, "api");
  root.innerHTML = pageHeader("开发与集成", "接口、客户端、证书与迁移") + tabs("/system/development", developmentTabs, selected);
  if (selected === "api") await renderApiDirectory(root);
  else if (selected === "clients") await renderClients(root);
  else if (selected === "certificates") await renderCertificates(root);
  else await renderMigration(root);
}
