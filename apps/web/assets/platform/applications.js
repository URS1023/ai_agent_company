/**
 * 应用与解决方案模块（路由 /applications，?id= 进详情）
 *
 * 位置：渲染应用列表（类型/状态/版本筛选）与详情（入口与依赖绑定、版本记录、启动记录），
 *   并提供新建、复制、发布、停用、归档与“启动”入口；启动会按发布版本的输入 Schema 生成参数表单。
 * 依赖接口前缀：/api/v1/applications（列表、详情、/catalog 可绑定资源目录、/launch 启动、
 *   /publish、/disable、/{id}/clone、/{id}/launches 启动记录）。
 * 与 iframe 内 Vue 应用的关系：应用启动后由后端返回 launchPath，若指向 /workflows/* 会以平台外壳（iframe）方式打开，
 *   并附带 application={id} 便于追溯来源；前端只做同源校验后跳转。
 *
 * 关键约束：
 * - 应用本身不复制数据或功能：它固定“唯一启动入口 + 依赖 + 可见范围 + 版本”。
 *   只有已发布的资源（item.ready）可作为入口；未发布资源可保存为依赖但不能作为入口。
 * - 启动必须使用幂等键（idempotencyKey）：同一份参数重复提交复用同一个键，参数变化才生成新键，
 *   防止用户重复点击产生多次执行（该键缓存在 launchAttempts 里，注意不要去重过度）。
 * - 含数据库写入节点的正式版本（publishedSpec.writeNodes）必须让用户勾选确认后才能启动，
 *   这是不可跳过的人审环节；confirmWrites 会随请求提交给后端。
 * - launchPath 必须校验为同源绝对路径（拒绝 http(s) 绝对地址与 // 开头），否则拒绝跳转。
 * - 权限码：applications.create/edit/delete/publish/execute，必须与后端目录一致；
 *   发布、停用、归档走统一确认弹窗，均为可追溯的状态变更而非物理删除。
 * - 参数表单只对简单 Schema 自动渲染控件，复杂 Schema（$ref/anyOf 等）退回 JSON 文本输入，避免生成错误控件。
 */

import { api } from "./api.js";
import {
  $, $$, can, esc, date, pageHeader, dataTable, emptyState, statusBadge,
  field, input, select, checkboxes, openDialog, confirmAction, toast,
} from "./ui.js";

const BASE = "/api/v1/applications";
// 中文说明：按应用记录缓存启动幂等键（WeakMap 不阻止记录被回收）。同一参数复用同一键，
// 参数变化才换新键——保证“重试同一次启动”不会变成“重复执行”。
const launchAttempts = new WeakMap();
const kindLabels = { assistant: "智能助手", automation: "自动化应用", dashboard: "数据应用", solution: "综合解决方案" };
const visibilityLabels = { workspace: "工作区", restricted: "指定成员", private: "仅自己" };
// 中文说明：可作为启动入口的资源类型，与后端 entry 校验的类型集合一致。
const entryTypes = ["bot", "workflow", "dashboard", "plugin"];
const emptySchema = () => ({ type: "object", properties: {}, additionalProperties: false });
const textarea = (name, value = "", attributes = "") => `<textarea name="${esc(name)}" ${attributes}>${esc(value)}</textarea>`;

async function request(method, path, body) {
  const value = await api[method](path, body);
  return value?.success === true && "data" in value ? value.data : value;
}
const get = path => request("get", path);
const post = (path, body) => request("post", path, body);

// 中文说明：整页跳转并保留非空查询参数（id 为空表示回列表）。
function go(values = {}) {
  const query = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== "" && value !== null && value !== undefined) query.set(key, value);
  });
  location.assign("/applications" + (query.size ? "?" + query.toString() : ""));
}

// 中文说明：可绑定资源目录按类型分组（type → group），用于按类别渲染依赖勾选。
function groupMap(catalog) {
  return new Map((catalog.groups || []).map(group => [group.type, group]));
}

// 中文说明：依赖资源勾选区。选择值为 `type:id`（与后端 bindings 的 type/id 对应）；
// 未发布的资源会标注“尚未发布”——它们可以作为依赖保存，但不能作为启动入口。
function dependencyOptions(catalog, selected) {
  return (catalog.groups || []).map(group => {
    const choices = group.items.map(item => ({
      value: `${group.type}:${item.id}`,
      label: `${item.name}${item.ready ? "" : " · 尚未发布"}`,
    }));
    if (!choices.length) return "";
    return `<section class="application-resource-group"><h3>${esc(group.label)}</h3>${checkboxes("dependencies", choices, selected)}</section>`;
  }).join("");
}

/**
 * 中文说明：新建应用 / 创建应用新版本弹窗。
 * 行为：从 /applications/catalog 拉取可绑定资源；入口类型只能从当前有可选资源的类型中选；
 *   编辑已有应用时提交的是“新版本草稿”，已发布版本不受影响。
 * 副作用：POST /applications 或 /applications/{id}/versions；保存后进入该应用详情。
 */
async function applicationDialog(record = null) {
  const catalog = await get(BASE + "/catalog");
  const groups = groupMap(catalog);
  const spec = record?.latestSpec || {};
  const bindings = spec.bindings || [];
  const currentEntry = bindings.find(item => item.role === "entry") || {};
  const selectedDependencies = bindings.filter(item => item.role === "dependency" && item.id)
    .map(item => `${item.type}:${item.id}`);
  const availableEntryTypes = entryTypes.filter(type => groups.get(type)?.items?.length);
  const initialEntryType = currentEntry.type || availableEntryTypes[0] || "dashboard";
  const audience = spec.audience || { mode: record?.visibility || "workspace", roleIds: [], userIds: [] };
  openDialog(record ? "创建应用新版本" : "新建应用", `<div class="form-grid application-form">
    ${field("应用名称", input("name", record?.name || "", 'required maxlength="200"'))}
    ${field("应用编码", input("code", record?.code || "", 'required maxlength="80" pattern="[a-z](?:[a-z0-9_]|-){2,79}" placeholder="例如 equipment-operations"'))}
    ${field("应用类型", select("kind", [
      { value: "assistant", label: "智能助手" }, { value: "automation", label: "自动化应用" },
      { value: "dashboard", label: "数据应用" }, { value: "solution", label: "综合解决方案" },
    ], record?.kind || "solution", null))}
    ${field("可见范围", select("visibility", [
      { value: "workspace", label: "整个工作区" }, { value: "restricted", label: "指定角色或用户" },
      { value: "private", label: "仅创建人" },
    ], record?.visibility || "workspace", null))}
    ${field("用途", textarea("description", record?.description || "", 'rows="2" maxlength="2000"'), "", true)}
    ${field("入口类型", select("entryType", availableEntryTypes.map(type => ({ value: type, label: groups.get(type).label })), initialEntryType, null))}
    ${field("启动入口", '<select name="entryId" required></select>')}
    <details class="form-field wide application-settings" data-audience-settings>
      <summary>指定发布受众</summary>
      <div class="application-settings-body">
        <h3>角色</h3>${checkboxes("roleIds", (catalog.roles || []).map(item => ({ value: item.id, label: item.name })), audience.roleIds || [])}
        <h3>用户</h3>${checkboxes("userIds", (catalog.users || []).map(item => ({ value: item.id, label: `${item.name} · ${item.username}` })), audience.userIds || [])}
      </div>
    </details>
    <details class="form-field wide application-settings">
      <summary>依赖资源</summary>
      <div class="application-settings-body application-dependencies">${dependencyOptions(catalog, selectedDependencies) || '<span class="muted">当前没有可绑定资源</span>'}</div>
    </details>
    <details class="form-field wide application-settings">
      <summary>展示与执行设置</summary>
      <div class="application-settings-body">
        ${field("欢迎语", textarea("welcomeMessage", spec.welcomeMessage || "", 'rows="2" maxlength="1000"'))}
        ${field("执行说明", textarea("instructions", spec.instructions || "", 'rows="6" maxlength="20000" placeholder="说明应用如何使用入口和依赖资源"'))}
      </div>
    </details>
  </div>`, {
    wide: true,
    saveLabel: record ? "保存新版本" : "保存草稿",
    onReady: dialog => {
      const entryType = $("[name=entryType]", dialog);
      const entryId = $("[name=entryId]", dialog);
      const syncEntry = () => {
        const items = groups.get(entryType.value)?.items || [];
        const selected = entryType.value === currentEntry.type ? currentEntry.id : "";
        entryId.innerHTML = `<option value="">请选择已登记资源</option>${items.map(item => `<option value="${esc(item.id)}" ${item.id === selected ? "selected" : ""}>${esc(item.name)}${item.ready ? "" : " · 尚未发布"}</option>`).join("")}`;
      };
      const visibility = $("[name=visibility]", dialog);
      const syncAudience = () => {
        $("[data-audience-settings]", dialog).hidden = visibility.value !== "restricted";
      };
      entryType.addEventListener("change", syncEntry);
      visibility.addEventListener("change", syncAudience);
      syncEntry(); syncAudience();
    },
    onSave: async (form) => {
      const entryType = String(form.get("entryType") || "");
      const entryId = String(form.get("entryId") || "");
      const bindings = [{ type: entryType, id: entryId, role: "entry", purpose: "应用启动入口", required: true }];
      form.getAll("dependencies").forEach(value => {
        const [type, id] = String(value).split(":", 2);
        if (type && id && !(type === entryType && id === entryId)) {
          bindings.push({ type, id, role: "dependency", purpose: "应用依赖", required: true });
        }
      });
      const visibility = String(form.get("visibility") || "workspace");
      const payload = {
        name: String(form.get("name") || "").trim(), code: String(form.get("code") || "").trim(),
        description: String(form.get("description") || "").trim(),
        kind: String(form.get("kind") || "solution"), visibility,
        spec: {
          welcomeMessage: String(form.get("welcomeMessage") || "").trim(),
          instructions: String(form.get("instructions") || "").trim(), bindings,
          audience: {
            mode: visibility,
            roleIds: visibility === "restricted" ? form.getAll("roleIds") : [],
            userIds: visibility === "restricted" ? form.getAll("userIds") : [],
          },
          inputSchema: spec.configuredInputSchema || spec.inputSchema || emptySchema(),
          outputSchema: spec.outputSchema || { type: "object", properties: {} },
        },
      };
      if (record) payload.version = record.recordVersion;
      const saved = record ? await request("put", `${BASE}/${record.id}`, payload) : await post(BASE, payload);
      toast(record ? "应用新版本已保存" : "应用草稿已保存");
      go({ id: saved.id });
    },
  });
}

// 中文说明：复制应用为草稿（编码需另填，符合后端小写字母/数字/下划线/短横线规则）。
function cloneDialog(record) {
  openDialog("复制为新应用", `<div class="form-grid">
    ${field("新名称", input("name", record.name + " 副本", 'required maxlength="200"'))}
    ${field("新编码", input("code", record.code + "-copy", 'required maxlength="80" pattern="[a-z](?:[a-z0-9_]|-){2,79}"'))}
  </div>`, {
    saveLabel: "复制草稿",
    onSave: async form => {
      const saved = await post(`${BASE}/${record.id}/clone`, {
        name: String(form.get("name") || "").trim(), code: String(form.get("code") || "").trim(),
      });
      toast("应用草稿已复制"); go({ id: saved.id });
    },
  });
}

/**
 * 中文说明：调用启动接口并跳转到应用入口。
 * 参数：record=应用记录；args=启动参数；confirmWrites=是否已由用户确认“执行数据库写入”。
 * 副作用：POST /applications/{id}/launch（会真实触发工作流/Bot 执行）→ location.assign 到 launchPath。
 * 失败表现：launchPath 非同源绝对路径时抛“服务端返回了无效的应用入口”（防开放重定向）；
 *   后端拒绝（参数校验、资源不可用、需确认写入）时抛出业务错误，由调用方 toast。
 */
async function openApplication(record, args, confirmWrites = false) {
  const serialized = JSON.stringify(args);
  let attempt = launchAttempts.get(record);
  if (!attempt || attempt.input !== serialized) {
    attempt = { input: serialized, key: Array.from(crypto.getRandomValues(new Uint8Array(16)), value => value.toString(16).padStart(2, "0")).join("") };
    launchAttempts.set(record, attempt);
  }
  const idempotencyKey = attempt.key;
  const launched = await post(`${BASE}/${record.id}/launch`, { channel: "ui", input: args, idempotencyKey, confirmWrites });
  if (!String(launched.launchPath || "").startsWith("/") || String(launched.launchPath).startsWith("//")) {
    throw new Error("服务端返回了无效的应用入口");
  }
  const destination = new URL(launched.launchPath, location.origin);
  if (destination.origin !== location.origin) throw new Error("服务端返回了无效的应用入口");
  if (destination.pathname.startsWith("/workflows/")) destination.searchParams.set("application", record.id);
  location.assign(destination.pathname + destination.search + destination.hash);
}

// 中文说明：启动弹窗。无参数且无写入节点时直接启动（不打扰用户）；
// 简单 Schema 自动生成控件，复杂 Schema（含 $ref/anyOf/patternProperties 等）退回 JSON 输入；
// 含写入节点时必须勾选“我已核对工作流目标表和字段”，该确认会作为 confirmWrites 提交给后端。
async function launchDialog(record) {
  const schema = record.publishedSpec?.inputSchema || emptySchema();
  const noParameters = schema.type === "object" && schema.additionalProperties === false
    && !Object.keys(schema.properties || {}).length && !(schema.required || []).length
    && !Object.keys(schema.patternProperties || {}).length
    && !["$ref", "allOf", "anyOf", "oneOf", "if", "then", "else", "dependentSchemas", "dependencies"].some(key => key in schema);
  const writeNodes = record.publishedSpec?.writeNodes || [];
  if (noParameters && !writeNodes.length) return openApplication(record, {});
  const properties = Object.entries(schema.properties || {});
  const simple = schema.type === "object" && schema.additionalProperties === false
    && properties.every(([, item]) => ['string', 'number', 'integer', 'boolean'].includes(item.type))
    && !["$ref", "allOf", "anyOf", "oneOf", "if", "patternProperties", "dependentSchemas", "dependencies"].some(key => key in schema);
  const simpleFields = simple ? properties.map(([key, item], index) => {
    const required = (schema.required || []).includes(key) ? 'required' : '';
    const name = `argument_${index}`;
    let control;
    if (item.enum || item.type === 'boolean') {
      const values = item.enum || [true, false];
      control = `<select name="${name}" ${required}><option value="">请选择</option>${values.map(value => `<option value="${esc(JSON.stringify(value))}" ${value === item.default ? 'selected' : ''}>${esc(typeof value === 'boolean' ? value ? '是' : '否' : value)}</option>`).join('')}</select>`;
    } else {
      const numeric = item.type !== 'string';
      control = input(name, item.default ?? '', `type="${numeric ? 'number' : 'text'}" ${required} ${numeric ? `step="${item.type === 'integer' ? '1' : 'any'}"` : item.maxLength ? `maxlength="${Number(item.maxLength)}"` : ''}`);
    }
    return field(item.title || key, control, item.description || '', true);
  }).join('') : '';
  const parameterFields = noParameters ? "" : simple ? simpleFields : `${field("启动参数 JSON", textarea("arguments", "{}", 'rows="10" spellcheck="false"'), "按此应用要求填写参数，可在下方查看输入说明。", true)}
      <details class="form-field wide application-settings"><summary>输入说明</summary><pre class="json-preview application-schema-preview">${esc(JSON.stringify(schema, null, 2))}</pre></details>`;
  openDialog("启动正式应用", `<div class="form-grid">
    ${field("发布版本", `<input value="v${esc(record.publishedVersion)}" disabled>`)}
    ${parameterFields}
    ${writeNodes.length ? `<div class="form-field wide"><p>此正式版本将执行数据库写入：${writeNodes.map(esc).join("、")}。</p><label><input type="checkbox" name="confirmWrites" required>我已核对工作流目标表和字段，确认执行写入</label></div>` : ""}
  </div>`, {
    wide: true, saveLabel: record.publishedSpec?.bindings?.some(item => item.role === 'entry' && item.type === 'workflow') ? "运行并打开" : "校验并打开",
    onSave: async form => {
      let args;
      try {
        args = simple ? Object.fromEntries(properties.flatMap(([key, item], index) => {
          const raw = form.get(`argument_${index}`);
          if (raw === '' || raw == null) return [];
          return [[key, item.enum || item.type === 'boolean' ? JSON.parse(raw) : item.type === 'string' ? raw : Number(raw)]];
        })) : JSON.parse(String(form.get("arguments") || "{}"));
      }
      catch { throw new Error("启动参数不是有效 JSON"); }
      await openApplication(record, args, form.get("confirmWrites") === "on");
    },
  });
}

// 中文说明：入口与依赖绑定表（role=entry 为唯一启动入口，其余为依赖）；未就绪的资源标红状态。
function bindingsTable(bindings) {
  if (!bindings?.length) return emptyState("没有资源绑定", "应用版本未配置入口资源。");
  return dataTable(["角色", "资源", "类型", "状态", "用途"], bindings,
    item => `<tr><td>${item.role === "entry" ? '<span class="application-entry-label">启动入口</span>' : "依赖"}</td><td>${item.path ? `<a class="cell-primary" href="${esc(item.path)}">${esc(item.name)}</a>` : esc(item.name)}</td><td>${esc(item.typeLabel)}</td><td>${statusBadge(item.ready ? "active" : item.status)}</td><td class="application-purpose">${esc(item.purpose || "—")}</td></tr>`);
}

// 中文说明：应用使用说明弹窗（四步交付流程）。文案为产品口径，修改时需与后端交付约定保持一致。
function applicationGuide() {
  openDialog("应用与解决方案使用说明", `<section class="application-guide">
    <header><span>APPLICATION DELIVERY</span><h3>把已有能力组合成一个可发布入口</h3><p>应用本身不复制数据或功能，它固定一个启动入口、必要依赖、可见范围和版本，方便业务人员直接使用。</p></header>
    <ol>
      <li><i>01</i><div><strong>准备可运行入口</strong><p>先发布一个 Bot、工作流、大屏或 Plugin；未发布资源可以保存为依赖，但不能作为正式启动入口。</p></div></li>
      <li><i>02</i><div><strong>组合业务方案</strong><p>选择唯一入口，再绑定数据源、知识库、技能或文档等依赖，并明确用途说明。</p></div></li>
      <li><i>03</i><div><strong>限定使用范围</strong><p>按工作区、指定角色/用户或仅自己控制访问；发布时固定应用配置和工作流修订，其他资源仍会校验当前可用状态。</p></div></li>
      <li><i>04</i><div><strong>启动并追溯</strong><p>从应用详情启动，系统校验参数与资源状态；执行结果进入“运行记录”，可查看步骤、调用与产物。</p></div></li>
    </ol>
    <aside><strong>页面中的示例</strong><p>“示例·设备预警与质量协同方案”由正式接口创建并绑定真实工作流与数据源，可直接打开查看资源关系。</p></aside>
  </section>`, { wide: true });
}

// 中文说明：应用详情页：基本信息、入口与依赖、版本记录、启动记录与全部状态操作按钮。
// 约束：发布/停用/归档走统一确认弹窗且都带 recordVersion（乐观锁）；
//   “启动”只在 launchable 时可用；启动过程中禁用按钮防止重复触发。
async function renderDetail(root, id) {
  const record = await get(`${BASE}/${encodeURIComponent(id)}`);
  const launches = record.launchCount ? await get(`${BASE}/${encodeURIComponent(id)}/launches?limit=20`) : [];
  const actions = `<button class="button" type="button" data-app-guide>使用说明</button><a class="button" href="/applications">返回列表</a>
    ${can("applications.edit") ? '<button class="button" type="button" data-app-edit>新建版本</button>' : ""}
    ${can("applications.create") ? '<button class="button" type="button" data-app-clone>复制</button>' : ""}
    ${can("applications.publish") && record.latestVersionStatus === "draft" ? '<button class="button primary" type="button" data-app-publish>发布</button>' : ""}
    ${can("applications.execute") && record.launchable ? '<button class="button primary" type="button" data-app-launch>启动</button>' : ""}
    ${can("applications.publish") && record.status === "published" ? '<button class="button" type="button" data-app-disable>停用</button>' : ""}
    ${can("applications.delete") ? '<button class="button danger" type="button" data-app-archive>归档</button>' : ""}`;
  const history = record.versions.map(item => `<div class="capability-version"><strong>v${item.version}</strong>${statusBadge(item.status)}<span>${date(item.publishTime || item.createTime)}</span></div>`).join("");
  const launchHistory = launches.map(item => `<div class="application-launch-row"><span>${date(item.createTime)}</span><span>${esc(item.channel.toUpperCase())}</span>${statusBadge(item.status)}${item.runId ? `<a href="/operations?id=${esc(item.runId)}">运行记录</a>` : ""}</div>`).join("");
  root.innerHTML = pageHeader(record.name, record.description || record.code, actions) + `
    <section class="panel catalog-detail-panel application-detail">
      <dl class="detail-grid catalog-detail-grid">
        <div><dt>应用编码</dt><dd>${esc(record.code)}</dd></div>
        <div><dt>应用类型</dt><dd>${esc(kindLabels[record.kind] || record.kind)}</dd></div>
        <div><dt>状态</dt><dd>${statusBadge(record.status)}</dd></div>
        <div><dt>可见范围</dt><dd>${esc(visibilityLabels[record.visibility] || record.visibility)}</dd></div>
        <div><dt>最新版本</dt><dd>v${record.latestVersion} · ${statusBadge(record.latestVersionStatus)}</dd></div>
        <div><dt>正式版本</dt><dd>${record.publishedVersion ? "v" + record.publishedVersion : "未发布"}</dd></div>
        ${record.latestSpec.welcomeMessage ? `<div class="wide"><dt>欢迎语</dt><dd>${esc(record.latestSpec.welcomeMessage)}</dd></div>` : ""}
        ${record.latestSpec.instructions ? `<div class="wide"><dt>执行说明</dt><dd class="capability-prose">${esc(record.latestSpec.instructions)}</dd></div>` : ""}
      </dl>
      <h2 class="capability-section-title">入口与依赖</h2>
      ${bindingsTable(record.latestSpec.bindings)}
      <details class="capability-history"><summary>版本记录 · ${record.versions.length}</summary>${history}</details>
      ${launches.length ? `<details class="capability-history"><summary>启动记录 · ${record.launchCount}</summary>${launchHistory}</details>` : ""}
    </section>`;
  $("[data-app-guide]", root)?.addEventListener("click", applicationGuide);
  $("[data-app-edit]", root)?.addEventListener("click", () => applicationDialog(record));
  $("[data-app-clone]", root)?.addEventListener("click", () => cloneDialog(record));
  $("[data-app-launch]", root)?.addEventListener("click", async event => {
    const trigger = event.currentTarget;
    trigger.disabled = true;
    try { await launchDialog(record); }
    catch (error) { toast(error.message, true); }
    finally { trigger.disabled = false; }
  });
  $("[data-app-publish]", root)?.addEventListener("click", () => confirmAction(
    "发布应用", `发布 v${record.latestVersion} 后，受众可通过固定入口启动该应用。`, async () => {
      const saved = await post(`${BASE}/${record.id}/publish`, {
        version: record.recordVersion, releaseVersion: record.latestVersion,
      });
      toast("应用已发布"); go({ id: saved.id });
    }, "确认发布",
  ));
  $("[data-app-disable]", root)?.addEventListener("click", () => confirmAction(
    "停用应用", "停用后不再允许新启动，版本和历史记录仍保留。", async () => {
      const saved = await post(`${BASE}/${record.id}/disable`, { version: record.recordVersion });
      toast("应用已停用"); go({ id: saved.id });
    }, "确认停用",
  ));
  $("[data-app-archive]", root)?.addEventListener("click", () => confirmAction(
    "归档应用", "归档后从正常列表移除，正式版本、审计与启动记录仍保留。", async () => {
      await request("delete", `${BASE}/${record.id}?version=${record.recordVersion}`);
      toast("应用已归档"); go();
    }, "确认归档",
  ));
}

// 中文说明：应用列表页（关键字/状态/类型筛选，limit=200 一次取回，无分页控件）。
async function renderList(root) {
  const query = new URLSearchParams(location.search);
  const keyword = query.get("keyword") || "";
  const status = query.get("status") || "";
  const kind = query.get("kind") || "";
  const data = await get(`${BASE}?keyword=${encodeURIComponent(keyword)}&status=${encodeURIComponent(status)}&kind=${encodeURIComponent(kind)}&limit=200`);
  const toolbar = `<form class="list-toolbar" data-app-filter>
    <label class="search-input"><span aria-hidden="true">⌕</span><input name="keyword" value="${esc(keyword)}" placeholder="名称或编码" aria-label="搜索应用"></label>
    ${select("status", [
      { value: "draft", label: "草稿" }, { value: "published", label: "已发布" }, { value: "disabled", label: "已停用" },
    ], status, "全部状态").replace("<select ", '<select aria-label="状态" ')}
    ${select("kind", Object.entries(kindLabels).map(([value, label]) => ({ value, label })), kind, "全部类型").replace("<select ", '<select aria-label="类型" ')}
    <button class="button" type="submit">查询</button>
    ${keyword || status || kind ? '<button class="button quiet" type="button" data-app-reset>重置</button>' : ""}
  </form>`;
  const table = data.items.length ? dataTable(["应用", "类型", "状态", "最新版本", "正式版本", "最后修改", "操作"], data.items,
    item => `<tr><td><a class="cell-primary" href="/applications?id=${esc(item.id)}">${esc(item.name)}</a><span class="cell-secondary">${esc(item.code)}</span></td><td>${esc(kindLabels[item.kind] || item.kind)}</td><td>${statusBadge(item.status)}</td><td>v${item.latestVersion}</td><td>${item.publishedVersion ? "v" + item.publishedVersion : "—"}</td><td>${date(item.updateTime)}</td><td><a href="/applications?id=${esc(item.id)}">查看</a></td></tr>`) : emptyState("还没有应用", "把已发布能力组合成可管理的业务入口。");
  const actions = `<button class="button" type="button" data-app-guide>使用说明</button>${can("applications.create") ? '<button class="button primary" type="button" data-new-app>新建应用</button>' : ""}`;
  root.innerHTML = pageHeader("应用与解决方案", "组合并发布业务入口", actions) + `<section class="panel">${toolbar}${table}</section>`;
  $("[data-app-filter]", root).addEventListener("submit", event => {
    event.preventDefault(); go(Object.fromEntries(new FormData(event.currentTarget)));
  });
  $("[data-app-reset]", root)?.addEventListener("click", () => go());
  $("[data-app-guide]", root)?.addEventListener("click", applicationGuide);
  $("[data-new-app]", root)?.addEventListener("click", () => applicationDialog());
}

/**
 * 中文说明：应用页入口（?id= 进详情，否则列表）。
 * 副作用：由子视图完成（读为主；启动/发布/停用/归档会写数据）；异常由 main.js 统一渲染错误页。
 */
export async function renderApplications(root) {
  const id = new URLSearchParams(location.search).get("id");
  if (id) return renderDetail(root, id);
  return renderList(root);
}
