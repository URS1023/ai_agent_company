/**
 * 系统管理模块（路由 /system/menus、/system/sessions、/system/settings、/system/development 的接口中心）
 *
 * 位置：渲染四个管理视图——菜单显示设置、在线会话、系统参数与业务字典、接口中心（OpenAPI 目录）。
 * 依赖接口前缀：/api/v1/system（/menus、/sessions、/settings、/dictionary）
 *   与 /api/v1/platform（/openapi.json 动态生成的接口契约、/api-collection Postman 合集）。
 * 与 iframe 内 Vue 应用无关。
 *
 * 关键约束：
 * - 菜单只影响导航显示（label/sort_order/visible），不构成权限：访问控制仍由后端按角色判定，
 *   页面必须保留这句提示，避免管理员误以为“隐藏菜单=禁用功能”。
 * - 所有写操作后要派发 platform-navigation-changed，让 main.js 重新拉取导航/设置；
 *   参数 ui.page_size 变更还需同步内存中的 state.pageSize，否则列表分页仍用旧值。
 * - 字典的 value 支持 string/number/boolean/json，交互式提交前必须按类型解析校验，
 *   JSON 解析失败或类型不匹配要抛出中文错误，不能把字符串存进期望数字/布尔的字段。
 * - 权限码：system.menus.manage / system.sessions.manage / system.settings.manage 必须与后端目录一致。
 * - 撤销会话属破坏性动作，必须走统一确认弹窗；凭据（密码、API Key）不在本模块管理，应引导到密钥模块。
 */

import { api, allPages, downloadJson } from "./api.js";
import { $, $$, state, can, esc, date, pageHeader, button, dataTable, pagination, wireList, searchBar,
  emptyState, statusBadge, field, input, select, checkboxes, openDialog, confirmAction, toast } from "./ui.js";

const BASE = "/api/v1/system";

// 中文说明：菜单管理。GET /api/v1/system/menus 列出所有已登记页面及其访问权限码；
// 保存用 PUT /menus/{id}（无 version，覆盖式），成功后重绘并派发 platform-navigation-changed 刷新侧边栏。
export async function renderMenus(root) {
  const result = await api.get(BASE + "/menus");
  root.innerHTML = pageHeader("菜单管理", "调整已登记页面的显示名称、顺序和显隐。功能与数据权限由角色管理控制。") +
    `<div class="notice-inline">菜单只决定导航显示，不能替代后端权限；已整合的独立入口默认隐藏，可按需显示。</div><section class="panel">` +
    dataTable(["菜单名称", "所属分组", "页面路径", "访问权限", "显示", "接入状态", "操作"], result.items, (item) => `<tr><td>${esc(item.label)}</td><td>${esc(item.group)}</td><td><code>${esc(item.path)}</code></td><td><code>${esc(item.permission)}</code></td><td>${item.visible ? "显示" : "隐藏"}</td><td>${item.available ? "已接入" : "暂未接入"}</td><td>${can("system.menus.manage") ? `<button class="text-button" data-menu="${esc(item.id)}">编辑</button>` : "只读"}</td></tr>`) + "</section>";
  $$('[data-menu]', root).forEach((element) => element.addEventListener("click", () => {
    const item = result.items.find((item) => item.id === element.dataset.menu);
    openDialog("菜单设置 · " + item.name, `<div class="form-grid">${field("显示名称", input("label", item.label, 'maxlength="160"'), "留空使用原始模块名称。", true)}${field("组内排序", input("sort_order", item.sort_order, 'type="number" min="0" max="100000" required'))}${field("是否显示", select("visible", [{ value: "true", label: "显示" }, { value: "false", label: "隐藏" }], String(item.visible), null))}</div>`, {
      onSave: async (form) => {
        await api.put(BASE + "/menus/" + item.id, { label: form.get("label"), visible: form.get("visible") === "true", sort_order: Number(form.get("sort_order")) });
        toast("菜单设置已保存");
        await renderMenus(root);
        window.dispatchEvent(new CustomEvent("platform-navigation-changed"));
      },
    });
  }));
}

// 中文说明：在线会话列表（服务端分页）。风险等级与认证方式由后端给出，前端只做文案映射；
// 撤销会话走统一确认弹窗并提示“对应浏览器需要重新登录”（后端会立即失效该会话）。
export async function renderSessions(root, page = 1) {
  const result = await api.get(BASE + "/sessions?page=" + page + "&page_size=" + state.pageSize);
  root.innerHTML = pageHeader("在线会话", "查看有效登录、来源与风险；撤销后对应浏览器需要重新登录。", button("刷新", "data-refresh")) +
    `<section class="panel">${result.items.length ? dataTable(["用户", "认证", "来源 IP", "设备", "风险", "最近活动", "空闲到期", "操作"], result.items, (item) => `<tr><td>${esc(item.display_name)}</td><td>${esc(item.auth_method === "oidc" ? "企业 SSO" : item.auth_method === "local" ? "本地账号" : "历史会话")}</td><td><code>${esc(item.ip_address || "—")}</code></td><td><code>${esc(item.device_hash || "—")}</code></td><td><span class="risk-level risk-${esc(item.risk_level)}">${esc({ low: "低", medium: "中", high: "高", unknown: "未知" }[item.risk_level] || item.risk_level)}</span></td><td>${date(item.last_seen_at)}</td><td>${date(item.idle_expires_at)}</td><td>${can("system.sessions.manage") ? `<button class="text-button danger" data-revoke="${esc(item.id)}">撤销会话</button>` : "只读"}</td></tr>`) : emptyState("没有有效会话", "只显示当前权限范围内的用户会话。")}${pagination(result)}</section>`;
  wireList(root, result, (change) => renderSessions(root, change.page).catch((error) => toast(error.message, true)));
  $("[data-refresh]", root).addEventListener("click", () => renderSessions(root, page).catch((error) => toast(error.message, true)));
  $$('[data-revoke]', root).forEach((element) => element.addEventListener("click", () => {
    const item = result.items.find((item) => item.id === element.dataset.revoke);
    confirmAction("撤销会话", `确认撤销“${item.display_name}”的这次会话？对应浏览器需要重新登录。`, async () => {
      await api.delete(BASE + "/sessions/" + item.id);
      toast("会话已撤销");
      await renderSessions(root, page);
    }, "撤销会话");
  }));
}

// 中文说明：系统参数 + 业务字典同页展示（两个不同接口，并行加载）。
// 约束：site.name 为固定品牌参数不可改（只显示标记）；ui.page_size 需数字校验（10–100）；
//   参数保存后同步 state.settings/state.pageSize 并派发 platform-navigation-changed。
// 副作用：PUT /settings/{key}、POST/PATCH/DELETE /dictionary；字典删除走统一确认弹窗。
export async function renderSettings(root, page = 1) {
  const [settings, dictionary] = await Promise.all([api.get(BASE + "/settings"), api.get(BASE + "/dictionary?page=" + page + "&page_size=" + state.pageSize)]);
  root.innerHTML = pageHeader("字典与参数", "管理业务字典和系统显示参数。密码、API Key 等凭据在密钥模块中管理。") +
    `<section class="panel"><div class="panel-heading"><h2>系统参数</h2></div><div class="configuration-list">${settings.items.map((item) => `<div class="configuration-row"><div><strong>${esc(item.description)}</strong><p><code>${esc(item.key)}</code></p></div><div><span>${esc(item.value)}</span>${item.key === "site.name" ? '<span class="status-badge">固定品牌</span>' : can("system.settings.manage") ? `<button class="text-button" data-setting="${esc(item.key)}">修改</button>` : ""}</div></div>`).join("")}</div></section>` +
    `<section class="panel section-gap"><div class="panel-heading"><div><h2>业务字典</h2><p>按类型维护编码、显示名称和业务值。</p></div>${can("system.settings.manage") ? button("新增字典项", "data-new-dictionary", true) : ""}</div>` +
    (dictionary.items.length ? dataTable(["字典类型", "编码", "显示名称", "值", "顺序", "状态", "操作"], dictionary.items, (item) => `<tr><td>${esc(item.namespace)}</td><td>${esc(item.key)}</td><td>${esc(item.label)}</td><td>${esc(typeof item.value === "object" ? JSON.stringify(item.value) : item.value)}</td><td>${item.sort_order}</td><td>${statusBadge(item.enabled)}</td><td><div class="row-actions">${can("system.settings.manage") ? `<button class="text-button" data-edit-dictionary="${esc(item.id)}">编辑</button><button class="text-button danger" data-delete-dictionary="${esc(item.id)}">删除</button>` : "只读"}</div></td></tr>`) : emptyState("尚无业务字典", "例如建立设备类型、问题等级等业务字典。")) + pagination(dictionary) + "</section>";
  wireList(root, dictionary, (change) => renderSettings(root, change.page).catch((error) => toast(error.message, true)));
  $$('[data-setting]', root).forEach((element) => element.addEventListener("click", () => {
    const item = settings.items.find((item) => item.key === element.dataset.setting);
    openDialog("修改参数", field(item.description, input("value", item.value, item.key === "ui.page_size" ? 'type="number" min="10" max="100" required' : 'required maxlength="300"'), item.key), {
      onSave: async (form) => {
        const value = item.key === "ui.page_size" ? Number(form.get("value")) : form.get("value");
        await api.put(BASE + "/settings/" + encodeURIComponent(item.key), { value });
        state.settings[item.key] = value;
        if (item.key === "ui.page_size") state.pageSize = value;
        toast("参数已保存");
        await renderSettings(root, page);
        window.dispatchEvent(new CustomEvent("platform-navigation-changed"));
      },
    });
  }));
  const editDictionary = (record) => {
    const item = record || { enabled: true, sort_order: 0, value: "" };
    // 后端 value 可能是对象/数字/布尔：按原类型推断下拉初始值，JSON 用格式化文本回填便于编辑
    const jsonValue = typeof item.value === "object";
    openDialog(record ? "编辑字典项" : "新增字典项", `<div class="form-grid">${field("字典类型", input("namespace", item.namespace, `required maxlength="80" pattern="[A-Za-z0-9_.\\x2D]+" ${record ? "disabled" : ""}`))}${field("项编码", input("key", item.key, `required maxlength="120" ${record ? "disabled" : ""}`))}${field("显示名称", input("label", item.label, 'required maxlength="200"'))}${field("值类型", select("value_type", [{ value: "string", label: "文本" }, { value: "number", label: "数字" }, { value: "boolean", label: "布尔值" }, { value: "json", label: "JSON" }], jsonValue ? "json" : typeof item.value, null))}${field("业务值", `<textarea name="value" required>${esc(jsonValue ? JSON.stringify(item.value, null, 2) : item.value)}</textarea>`, "布尔值使用 true/false；JSON 必须符合语法。", true)}${field("排序值", input("sort_order", item.sort_order, 'type="number" min="0" max="100000" required'))}${field("状态", select("enabled", [{ value: "true", label: "启用" }, { value: "false", label: "停用" }], String(item.enabled), null))}</div>`, {
      onSave: async (form) => {
        const kind = form.get("value_type");
        let value = form.get("value");
        // 先按所选类型解析成真实类型再提交：后端按类型校验，字符串 "true"/"[1]" 会被拒绝或存错
        if (kind === "json" || kind === "boolean") {
          try { value = JSON.parse(value); } catch { throw new Error("业务值不是有效的 JSON / 布尔值"); }
          if (kind === "boolean" && typeof value !== "boolean") throw new Error("布尔值只能填写 true 或 false");
        } else if (kind === "number") {
          value = Number(value);
          if (!Number.isFinite(value)) throw new Error("请输入有效数字");
        }
        const payload = { label: form.get("label"), value, sort_order: Number(form.get("sort_order")), enabled: form.get("enabled") === "true" };
        if (record) await api.patch(BASE + "/dictionary/" + item.id, { version: item.version, ...payload });
        else await api.post(BASE + "/dictionary", { namespace: form.get("namespace"), key: form.get("key"), ...payload });
        toast("字典项已保存");
        await renderSettings(root, page);
      },
    });
  };
  $("[data-new-dictionary]", root)?.addEventListener("click", () => editDictionary(null));
  $$('[data-edit-dictionary]', root).forEach((element) => element.addEventListener("click", () => editDictionary(dictionary.items.find((item) => item.id === element.dataset.editDictionary))));
  $$('[data-delete-dictionary]', root).forEach((element) => element.addEventListener("click", () => {
    const item = dictionary.items.find((item) => item.id === element.dataset.deleteDictionary);
    confirmAction("删除字典项", `确认删除“${item.label}”？请确认相关业务配置已不再引用此编码。`, async () => {
      await api.delete(BASE + "/dictionary/" + item.id);
      toast("字典项已删除");
      await renderSettings(root, page);
    }, "删除字典项");
  }));
}

// 中文说明：接口中心。契约来自运行时生成的 /api/v1/platform/openapi.json（只含已注册接口），
// 因此条目数会随后端模块变化，页面文案中的“当前登记 N 个真实操作”即指此。
// 副作用：下载 OpenAPI/Postman 合集到本地（不写库）；“查看契约”弹窗展示该操作的完整定义及相关 schema 引用。
export async function renderApiCenter(root, search = "") {
  const spec = await api.get("/api/v1/platform/openapi.json");
  const operations = [];
  Object.entries(spec.paths).forEach(([path, methods]) => Object.entries(methods).forEach(([method, operation]) => {
    if (["get", "post", "put", "patch", "delete"].includes(method)) operations.push({ path, method, ...operation });
  }));
  const filtered = operations.filter((item) => [item.path, item.summary, item.operationId, item.tags?.join(" ")].join(" ").toLowerCase().includes(search.toLowerCase()));
  root.innerHTML = pageHeader("接口中心", "接口目录由当前实际注册的服务生成。合集使用变量占位符，不包含账号令牌或真实密钥。",
    button("下载 OpenAPI", "data-download-openapi") + button("下载 Postman 合集", "data-download-collection", true)) +
    `<section class="panel">${searchBar("搜索接口名称、路径或模块", search)}<div class="notice-inline">当前登记 ${operations.length} 个真实操作，仅展示已注册接口。</div>` +
    dataTable(["方法", "接口路径", "业务说明", "模块", "详情"], filtered, (item) => `<tr><td><span class="api-method">${esc(item.method.toUpperCase())}</span></td><td><code class="api-path">${esc(item.path)}</code></td><td>${esc(item.summary || item.operationId)}</td><td>${esc(item.tags?.join("、") || "未分类")}</td><td><button class="text-button" data-operation="${esc(item.operationId)}">查看契约</button></td></tr>`) + "</section>";
  $("[data-search-form]", root).addEventListener("submit", (event) => { event.preventDefault(); renderApiCenter(root, new FormData(event.currentTarget).get("q")).catch((error) => toast(error.message, true)); });
  $("[data-reset]", root).addEventListener("click", () => renderApiCenter(root).catch((error) => toast(error.message, true)));
  $("[data-download-openapi]", root).addEventListener("click", () => downloadJson(spec, "cloud-brain-openapi.json"));
  $("[data-download-collection]", root).addEventListener("click", async () => {
    try { downloadJson(await api.get("/api/v1/platform/api-collection"), "cloud-brain-postman-collection.json"); }
    catch (error) { toast(error.message, true); }
  });
  $$('[data-operation]', root).forEach((element) => element.addEventListener("click", () => {
    const operation = operations.find((item) => item.operationId === element.dataset.operation);
    const schemaRefs = JSON.stringify(operation).match(/#\/components\/schemas\/[^"\s]+/g) || [];
    const related = Object.fromEntries([...new Set(schemaRefs)].map((ref) => [ref.split("/").at(-1), spec.components?.schemas?.[ref.split("/").at(-1)]]));
    openDialog(operation.summary || operation.operationId, `<p>${esc(operation.description || "请结合请求字段、响应结构和权限要求调用。")}</p><pre class="json-preview">${esc(JSON.stringify({ operation, schemas: related }, null, 2))}</pre>`, { wide: true });
  }));
}
