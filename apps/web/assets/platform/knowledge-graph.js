/**
 * 知识图谱模块（本体 / 抽取 / 图谱 / Bot 四个页面：/ontology、/extraction、/graphs、/bots）
 *
 * 位置：四页共享同一条语义链路，故合并在一个文件里：
 *   本体（概念 + 关系类型）定义业务语义 → 抽取任务按“已发布本体”生成待复核候选 → 复核后写入图谱 → Bot 绑定图谱/知识库对外问答。
 * 依赖接口前缀：/api/v1/ontologies（concepts、relation-types、publish、revisions）、
 *   /api/v1/extractions（execute、runs/{id}/candidates、review、materialize）、
 *   /api/v1/graphs（entities、relations、explore、publish）、/api/v1/bots（binding-options、runs、api-keys、debug、publish）；
 *   知识库选项来自 /api/knowledge（需 knowledge.view）。
 * 与 iframe 内 Vue 应用无关。
 *
 * 关键约束：
 * - 只有带 publishedRevision 的“已发布本体”才能被抽取任务与图谱引用；Bot 的 API Key 也只在
 *   apiEnabled 且已发布修订后才可创建。这些限制同时体现在下拉过滤、按钮显隐与提示文案上，不要放宽。
 * - 权限码必须与后端目录一致：ontology.*、extraction.*、graphs.*、bots.* 的 create/edit/delete/publish/execute。
 *   删除统一走 confirmAction；被其他资源引用时后端会拒绝并返回原因，前端不要提前静默拦截。
 * - 抽取是“人审后入库”的两阶段流程：执行生成候选 → 逐项/批量复核（接受、驳回）→ materialize 写入目标图谱；
 *   存在待复核候选时“写入图谱”按钮必须保持禁用（后端同样要求候选处理完毕）。
 * - 所有更新都带 version；发布修订必须填写发布说明（统一走 openPublish）。
 * - API Key 明文只在创建响应中出现一次，弹窗须提示“关闭后无法再次查看”，复制走统一 clipboard 模块。
 */

import { api } from "./api.js";
import { copyText } from "./clipboard.js";
import {
  $, $$, can, esc, date, pageHeader, button, dataTable, emptyState, statusBadge,
  field, input, select, checkboxes, openDialog, confirmAction, toast,
} from "./ui.js";

const ONTOLOGY = "/api/v1/ontologies";
const EXTRACTION = "/api/v1/extractions";
const GRAPHS = "/api/v1/graphs";
const BOTS = "/api/v1/bots";

// 中文说明：四个接口前缀都返回 { success, data } 包装，这里统一解包；非包装结构原样返回。
async function request(method, path, body) {
  const result = await api[method](path, body);
  return result && result.success === true && "data" in result ? result.data : result;
}
const get = (path) => request("get", path);
const post = (path, body) => request("post", path, body);
const put = (path, body) => request("put", path, body);
const remove = (path) => request("delete", path);
const textarea = (name, value = "", attributes = "") => `<textarea name="${esc(name)}" ${attributes}>${esc(value)}</textarea>`;
const queryId = () => new URLSearchParams(location.search).get("id") || "";
const go = (path, id = "") => location.assign(path + (id ? "?id=" + encodeURIComponent(id) : ""));
const pretty = (value) => JSON.stringify(value ?? {}, null, 2);
// 中文说明：解析表单里的 JSON 文本：空值返回 fallback（不报错），非空解析失败抛中文错误由弹窗展示。
const json = (value, label, fallback) => {
  const clean = String(value || "").trim();
  if (!clean) return fallback;
  try { return JSON.parse(clean); }
  catch { throw new Error(label + "不是有效 JSON"); }
};
const unique = (values) => [...new Set(values.filter(Boolean))];
// 中文说明：状态/类型枚举的中文映射。键名与后端枚举一致；未登记的取值原样显示（便于发现后端新增枚举）。
const labels = {
  draft: "草稿", published: "已发布", review: "待复核", completed: "已完成", failed: "失败", running: "执行中",
  structured: "结构化", unstructured: "知识文档", agent: "Agent", knowledge: "知识问答", chatflow: "Chatflow",
  one_to_one: "一对一", one_to_many: "一对多", many_to_one: "多对一", many_to_many: "多对多",
};
const label = value => labels[value] || value || "—";

// 中文说明：为详情页追加统一“删除”按钮（四个资源共用）。
// 参数：permission=所需权限码；apiPath/listPath=删除接口与返回的列表路由；title/note=确认弹窗文案。
// 约束：删除带 version（后端并发校验），被引用时后端拒绝；成功后返回列表页。
function wireResourceRemoval(root, record, { permission, apiPath, listPath, title, note }) {
  if (!can(permission)) return;
  $(".page-heading .heading-actions", root)?.insertAdjacentHTML("beforeend",
    button(title, 'data-remove-resource'));
  $("[data-remove-resource]", root)?.addEventListener("click", () => {
    confirmAction(title, `确认删除“${record.name}”？${note}`, async () => {
      await remove(`${apiPath}/${encodeURIComponent(record.id)}?version=${record.version}`);
      toast("已删除");
      go(listPath);
    }, "确认删除");
  });
}

// 中文说明：列表页搜索工具栏（字段名固定 keyword，与四个列表接口的查询参数一致）。
function listToolbar(placeholder, value = "") {
  return `<form class="list-toolbar" data-resource-search><label class="search-input"><span aria-hidden="true">⌕</span><input name="keyword" aria-label="${esc(placeholder)}" placeholder="${esc(placeholder)}" value="${esc(value)}"></label><button class="button" type="submit">查询</button>${value ? '<button class="button quiet" type="button" data-clear-search>重置</button>' : ""}</form>`;
}

// 中文说明：绑定列表搜索/重置。render 由调用方传入（各列表的渲染函数），失败统一 toast。
function wireSearch(root, render) {
  $("[data-resource-search]", root)?.addEventListener("submit", (event) => {
    event.preventDefault();
    render(String(new FormData(event.currentTarget).get("keyword") || "").trim()).catch(showError);
  });
  $("[data-clear-search]", root)?.addEventListener("click", () => render("").catch(showError));
}

function showError(error) { toast(error.message || "操作失败", true); }

// 中文说明：详情页统计条（键值对横向展示）。
function summary(items) {
  return `<div class="resource-summary">${items.map(([label, value]) => `<span><small>${esc(label)}</small><strong>${esc(value)}</strong></span>`).join("")}</div>`;
}

// 中文说明：统一的“发布修订”弹窗。发布说明必填，回调收到 { version, reason }；
// 发布是产生正式快照的动作，不可撤销（只能再发新修订），因此文案用“确认发布”。
function openPublish(title, version, callback) {
  openDialog(title, `<div class="form-grid">${field("发布说明", input("reason", "发布正式版本", 'required maxlength="300"'), "", true)}</div>`, {
    saveLabel: "确认发布",
    onSave: async (form) => callback({ version, reason: form.get("reason") }),
  });
}

// 中文说明：本体表单（新建/编辑）。仅提交名称、说明等基本信息，概念与关系在详情页单独维护。
function ontologyForm(record, reload) {
  openDialog(record ? "编辑本体模型" : "新建本体模型", `<div class="form-grid">${field("名称", input("name", record?.name, 'required maxlength="200"'))}${field("说明", textarea("description", record?.description, 'maxlength="4000"'), "", true)}</div>`, {
    onSave: async (form) => {
      const payload = { name: form.get("name"), description: form.get("description") };
      if (record) { payload.version = record.version; await put(`${ONTOLOGY}/${record.id}`, payload); }
      else await post(ONTOLOGY, payload);
      toast("本体模型已保存");
      await reload();
    },
  });
}

// 中文说明：概念表单。属性 JSON 需符合后端属性定义（含 required=true 的必填项），
// 识别词用于抽取时命中该概念，保存后立即影响后续抽取召回。
function conceptForm(ontology, record, reload) {
  const defaults = record || { color: "#3b73d1", icon: "entity", attributes: [], recognitionTerms: [], constraints: {} };
  openDialog(record ? "编辑概念" : "新增概念", `<div class="form-grid">
    ${field("概念编码", input("code", defaults.code, 'required maxlength="80" pattern="[A-Za-z][A-Za-z0-9_\\x2D]*"'))}
    ${field("显示名称", input("name", defaults.name, 'required maxlength="160"'))}
    ${field("识别词", input("terms", (defaults.recognitionTerms || []).join("，"), 'maxlength="2000"'), "使用逗号分隔，用于文档抽取。", true)}
    ${field("属性定义 JSON", textarea("attributes", pretty(defaults.attributes), 'required rows="8" spellcheck="false"'), "字段包含 name、label、type、required、unique、enumValues。", true)}
    ${field("约束 JSON", textarea("constraints", pretty(defaults.constraints), 'rows="4" spellcheck="false"'), "可设置 businessKey；值必须是 name 或上方已定义属性的 name。", true)}
    ${field("说明", textarea("description", defaults.description, 'maxlength="4000"'), "", true)}
  </div>`, {
    wide: true,
    onSave: async (form) => {
      const payload = {
        code: form.get("code"), name: form.get("name"), description: form.get("description"),
        color: defaults.color || "#3b73d1", icon: defaults.icon || "entity",
        attributes: json(form.get("attributes"), "属性定义", []),
        recognitionTerms: unique(String(form.get("terms") || "").split(/[，,]/).map(item => item.trim())),
        constraints: json(form.get("constraints"), "约束", {}),
      };
      if (record) { payload.version = record.version; await put(`${ONTOLOGY}/${ontology.id}/concepts/${record.id}`, payload); }
      else await post(`${ONTOLOGY}/${ontology.id}/concepts`, payload);
      toast("概念已保存");
      await reload();
    },
  });
}

// 中文说明：关系类型表单：起点/终点必须是同一本体内已存在的概念，基数为 one_to_one 等固定枚举。
function relationTypeForm(ontology, record, reload) {
  const choices = ontology.concepts.map(item => ({ value: item.id, label: item.name + " · " + item.code }));
  const defaults = record || { cardinality: "many_to_many", directed: true, attributes: [], constraints: {} };
  openDialog(record ? "编辑关系类型" : "新增关系类型", `<div class="form-grid">
    ${field("关系编码", input("code", defaults.code, 'required maxlength="80" pattern="[A-Za-z][A-Za-z0-9_\\x2D]*"'))}
    ${field("显示名称", input("name", defaults.name, 'required maxlength="160"'))}
    ${field("起点概念", select("sourceConceptId", choices, defaults.sourceConceptId, "请选择"))}
    ${field("终点概念", select("targetConceptId", choices, defaults.targetConceptId, "请选择"))}
    ${field("基数", select("cardinality", [
      { value: "one_to_one", label: "一对一" }, { value: "one_to_many", label: "一对多" },
      { value: "many_to_one", label: "多对一" }, { value: "many_to_many", label: "多对多" },
    ], defaults.cardinality, null))}
    ${field("方向", select("directed", [{ value: "true", label: "有向" }, { value: "false", label: "无向" }], String(defaults.directed), null))}
    ${field("关系属性 JSON", textarea("attributes", pretty(defaults.attributes), 'rows="6" spellcheck="false"'), "", true)}
    ${field("约束 JSON", textarea("constraints", pretty(defaults.constraints), 'rows="3" spellcheck="false"'), "", true)}
    ${field("说明", textarea("description", defaults.description, 'maxlength="4000"'), "", true)}
  </div>`, {
    wide: true,
    onSave: async (form) => {
      const payload = {
        code: form.get("code"), name: form.get("name"), description: form.get("description"),
        sourceConceptId: form.get("sourceConceptId"), targetConceptId: form.get("targetConceptId"),
        cardinality: form.get("cardinality"), directed: form.get("directed") === "true",
        attributes: json(form.get("attributes"), "关系属性", []), constraints: json(form.get("constraints"), "约束", {}),
      };
      if (record) { payload.version = record.version; await put(`${ONTOLOGY}/${ontology.id}/relation-types/${record.id}`, payload); }
      else await post(`${ONTOLOGY}/${ontology.id}/relation-types`, payload);
      toast("关系类型已保存");
      await reload();
    },
  });
}

// 中文说明：本体列表页（关键字搜索）。
async function renderOntologyList(root, keyword = "") {
  const items = await get(ONTOLOGY + (keyword ? "?keyword=" + encodeURIComponent(keyword) : ""));
  root.innerHTML = pageHeader("概念与关系模型", "定义知识抽取和图谱共用的业务语义。",
    can("ontology.create") ? button("新建本体", "data-new-ontology", true) : "") +
    `<section class="panel">${listToolbar("搜索本体名称", keyword)}${items.length ? dataTable(
      ["名称", "概念 / 关系", "状态", "正式修订", "最后修改", "操作"], items,
      item => `<tr><td><a class="cell-primary" href="/ontology?id=${esc(item.id)}">${esc(item.name)}</a><span class="cell-secondary">${esc(item.description || "未填写说明")}</span></td><td>${item.conceptCount} / ${item.relationCount}</td><td>${statusBadge(item.status)}</td><td>${esc(item.publishedRevision || "—")}</td><td>${date(item.updatedAt)}</td><td><a class="text-button" href="/ontology?id=${esc(item.id)}">打开</a></td></tr>`,
    ) : emptyState("暂无本体模型", "新建后添加概念和关系，再发布正式修订。",
      can("ontology.create") ? '<button class="button primary" type="button" data-new-ontology>新建本体</button>' : "")}</section>`;
  wireSearch(root, (value) => renderOntologyList(root, value));
  $$('[data-new-ontology]', root).forEach(element => element.addEventListener("click", () => ontologyForm(null, () => renderOntologyList(root, keyword))));
}

// 中文说明：本体详情页：概念与关系类型两张表 + 发布修订 + 修订记录懒加载。
// 约束：概念/关系删除走统一确认弹窗，被关系或图谱引用时后端拒绝；
//   修订记录首次展开时才请求（dataset.loaded 去重），避免进入页面就发多余请求。
async function renderOntologyDetail(root, id) {
  const record = await get(`${ONTOLOGY}/${encodeURIComponent(id)}`);
  const reload = () => renderOntologyDetail(root, id);
  const conceptById = new Map(record.concepts.map(item => [item.id, item]));
  root.innerHTML = pageHeader(record.name, record.description || "本体模型",
    `<a class="button" href="/ontology">返回列表</a>${can("ontology.edit") ? button("编辑", "data-edit-ontology") : ""}${can("ontology.publish") ? button("发布修订", "data-publish-ontology", true) : ""}`) +
    summary([["状态", record.status === "published" ? "已发布" : "草稿"], ["概念", record.conceptCount], ["关系", record.relationCount], ["正式修订", record.publishedRevision || "—"]]) +
    `<div class="resource-columns"><section class="panel"><div class="panel-heading"><h2>概念</h2>${can("ontology.create") ? button("新增", "data-add-concept", false) : ""}</div>${record.concepts.length ? dataTable(
      ["名称 / 编码", "属性", "识别词", "操作"], record.concepts,
      item => `<tr><td><span class="cell-primary">${esc(item.name)}</span><span class="cell-secondary">${esc(item.code)}</span></td><td>${item.attributes.length}</td><td>${esc(item.recognitionTerms.join("、") || "—")}</td><td><div class="row-actions">${can("ontology.edit") ? `<button class="text-button" data-edit-concept="${esc(item.id)}">编辑</button>` : ""}${can("ontology.delete") ? `<button class="text-button danger" data-delete-concept="${esc(item.id)}">删除</button>` : ""}</div></td></tr>`,
    ) : emptyState("暂无概念", "先定义业务对象及其属性。")}</section>
    <section class="panel"><div class="panel-heading"><h2>关系类型</h2>${can("ontology.create") && record.concepts.length ? button("新增", "data-add-relation-type", false) : ""}</div>${record.relationTypes.length ? dataTable(
      ["名称 / 编码", "起点 → 终点", "基数", "操作"], record.relationTypes,
      item => `<tr><td><span class="cell-primary">${esc(item.name)}</span><span class="cell-secondary">${esc(item.code)}</span></td><td>${esc(conceptById.get(item.sourceConceptId)?.name || "—")} → ${esc(conceptById.get(item.targetConceptId)?.name || "—")}</td><td>${esc(label(item.cardinality))}</td><td><div class="row-actions">${can("ontology.edit") ? `<button class="text-button" data-edit-relation-type="${esc(item.id)}">编辑</button>` : ""}${can("ontology.delete") ? `<button class="text-button danger" data-delete-relation-type="${esc(item.id)}">删除</button>` : ""}</div></td></tr>`,
    ) : emptyState("暂无关系类型", record.concepts.length ? "定义概念之间允许建立的关系。" : "先新增概念。")}</section></div>
    <details class="panel resource-history"><summary>正式修订记录</summary><div data-ontology-revisions class="compact-history">展开后读取…</div></details>`;
  $("[data-edit-ontology]", root)?.addEventListener("click", () => ontologyForm(record, reload));
  wireResourceRemoval(root, record, { permission: "ontology.delete", apiPath: ONTOLOGY, listPath: "/ontology", title: "删除本体", note: "仍被抽取任务或图谱引用时系统会拒绝。" });
  $("[data-add-concept]", root)?.addEventListener("click", () => conceptForm(record, null, reload));
  $("[data-add-relation-type]", root)?.addEventListener("click", () => relationTypeForm(record, null, reload));
  $$('[data-edit-concept]', root).forEach(element => element.addEventListener("click", () => conceptForm(record, conceptById.get(element.dataset.editConcept), reload)));
  const relationById = new Map(record.relationTypes.map(item => [item.id, item]));
  $$('[data-edit-relation-type]', root).forEach(element => element.addEventListener("click", () => relationTypeForm(record, relationById.get(element.dataset.editRelationType), reload)));
  $$('[data-delete-concept]', root).forEach(element => element.addEventListener("click", () => {
    const item = conceptById.get(element.dataset.deleteConcept);
    confirmAction("删除概念", `确认删除“${item.name}”？被关系或图谱使用时系统会拒绝。`, async () => {
      await remove(`${ONTOLOGY}/${record.id}/concepts/${item.id}?version=${item.version}`); toast("概念已删除"); await reload();
    }, "删除");
  }));
  $$('[data-delete-relation-type]', root).forEach(element => element.addEventListener("click", () => {
    const item = relationById.get(element.dataset.deleteRelationType);
    confirmAction("删除关系类型", `确认删除“${item.name}”？`, async () => {
      await remove(`${ONTOLOGY}/${record.id}/relation-types/${item.id}?version=${item.version}`); toast("关系类型已删除"); await reload();
    }, "删除");
  }));
  $("[data-publish-ontology]", root)?.addEventListener("click", () => openPublish("发布本体修订", record.version, async payload => {
    await post(`${ONTOLOGY}/${record.id}/publish`, payload); toast("本体正式修订已发布"); await reload();
  }));
  $(".resource-history", root)?.addEventListener("toggle", async event => {
    if (!event.currentTarget.open || event.currentTarget.dataset.loaded) return;
    event.currentTarget.dataset.loaded = "true";
    const revisions = await get(`${ONTOLOGY}/${record.id}/revisions`);
    $("[data-ontology-revisions]", root).innerHTML = revisions.length ? revisions.map(item => `<div><strong>修订 ${item.revision}</strong><span>${esc(item.reason)}</span><time>${date(item.createdAt)}</time></div>`).join("") : "暂无正式修订";
  });
}

/**
 * 中文说明：本体页面入口（无 ?id 显示列表，有 id 显示详情）。
 */
export async function renderOntology(root) {
  return queryId() ? renderOntologyDetail(root, queryId()) : renderOntologyList(root);
}

// 中文说明：抽取任务可绑定的知识库选项；无 knowledge.view 权限时返回空数组（不报错）。
async function knowledgeBases() {
  if (!can("knowledge.view")) return [];
  const value = await api.get("/api/knowledge");
  return value.items || [];
}

// 中文说明：抽取任务表单。本体下拉只列已发布修订的本体（publishedRevision 存在）；
// 结构化模式提交记录数组与字段映射 JSON，知识文档模式选择文档/知识库，两者互斥。
async function extractionForm(record, reload) {
  const [ontologies, bases] = await Promise.all([get(ONTOLOGY), knowledgeBases()]);
  const published = ontologies.filter(item => item.publishedRevision);
  const defaults = record || { mode: "structured", mappings: { entityMappings: [], relationMappings: [] }, records: [], documentIds: [] };
  const content = `<div class="form-grid">
    ${field("任务名称", input("name", defaults.name, 'required maxlength="200"'))}
    ${field("抽取模式", select("mode", [{ value: "structured", label: "结构化记录" }, { value: "unstructured", label: "知识文档" }], defaults.mode, null))}
    ${field("本体模型", select("ontologyId", published.map(item => ({ value: item.id, label: `${item.name} · 修订 ${item.publishedRevision}` })), defaults.ontologyId, "请选择已发布本体"), "", true)}
    <div class="form-field wide" data-structured-fields>
      <span>输入记录 JSON 数组</span>${textarea("records", pretty(defaults.records), 'rows="8" spellcheck="false" aria-label="输入记录 JSON 数组"')}
      <small>最多 1000 条；执行时保存输入快照。</small>
      <span>字段映射 JSON</span>${textarea("mappings", pretty(defaults.mappings), 'rows="10" spellcheck="false" aria-label="字段映射 JSON"')}
    </div>
    <div class="form-field wide" data-unstructured-fields>
      <label>知识库${select("knowledgeBaseId", bases.map(item => ({ value: item.id, label: item.name })), defaults.knowledgeBaseId, "请选择知识库")}</label>
      <small>只读取已完成解析的文档分块。</small><div data-document-options class="checkbox-options"><span class="muted">选择知识库后读取文档</span></div>
    </div>
    ${field("说明", textarea("description", defaults.description, 'maxlength="4000"'), "", true)}
  </div>`;
  openDialog(record ? "编辑抽取任务" : "新建抽取任务", content, {
    wide: true,
    onReady: (dialog) => {
      const mode = $('[name="mode"]', dialog);
      const kb = $('[name="knowledgeBaseId"]', dialog);
      const sync = () => {
        const structured = mode.value === "structured";
        $("[data-structured-fields]", dialog).hidden = !structured;
        $("[data-unstructured-fields]", dialog).hidden = structured;
      };
      const loadDocuments = async () => {
        const box = $("[data-document-options]", dialog);
        if (!kb.value) { box.innerHTML = '<span class="muted">选择知识库后读取文档</span>'; return; }
        try {
          const value = await api.get(`/api/knowledge/${encodeURIComponent(kb.value)}/documents`);
          box.innerHTML = value.items?.length ? value.items.map(item => `<label><input type="checkbox" name="documentIds" value="${esc(item.id)}" ${(defaults.documentIds || []).includes(item.id) ? "checked" : ""} ${item.status !== "ready" ? "disabled" : ""}><span>${esc(item.file_name || item.name || item.id)}${item.status !== "ready" ? " · 未解析" : ""}</span></label>`).join("") : '<span class="muted">该知识库暂无文档</span>';
        } catch (error) { box.innerHTML = `<span class="muted">${esc(error.message)}</span>`; }
      };
      mode.addEventListener("change", sync); kb.addEventListener("change", loadDocuments); sync(); if (kb.value) loadDocuments();
    },
    onSave: async (form) => {
      const mode = form.get("mode");
      const payload = {
        name: form.get("name"), description: form.get("description"), mode,
        ontologyId: form.get("ontologyId"), knowledgeBaseId: mode === "unstructured" ? form.get("knowledgeBaseId") : null,
        documentIds: mode === "unstructured" ? form.getAll("documentIds") : [],
        records: mode === "structured" ? json(form.get("records"), "输入记录", []) : [],
        mappings: mode === "structured" ? json(form.get("mappings"), "字段映射", {}) : {},
      };
      if (record) { payload.version = record.version; await put(`${EXTRACTION}/${record.id}`, payload); }
      else await post(EXTRACTION, payload);
      toast("抽取任务已保存"); await reload();
    },
  });
}

// 中文说明：抽取任务列表页。
async function renderExtractionList(root, keyword = "") {
  const items = await get(EXTRACTION + (keyword ? "?keyword=" + encodeURIComponent(keyword) : ""));
  root.innerHTML = pageHeader("知识抽取", "从结构化记录或已解析文档生成可复核候选。",
    can("extraction.create") ? button("新建任务", "data-new-extraction", true) : "") +
    `<section class="panel">${listToolbar("搜索任务名称", keyword)}${items.length ? dataTable(
      ["任务", "模式", "本体修订", "状态", "候选 / 结果", "最后修改", "操作"], items,
      item => `<tr><td><a class="cell-primary" href="/extraction?id=${esc(item.id)}">${esc(item.name)}</a><span class="cell-secondary">${esc(item.description || "—")}</span></td><td>${item.mode === "structured" ? "结构化" : "知识文档"}</td><td>${item.ontologyRevision}</td><td>${statusBadge(item.status)}</td><td>${esc(item.resultSummary?.candidates ?? "—")}</td><td>${date(item.updatedAt)}</td><td><a class="text-button" href="/extraction?id=${esc(item.id)}">打开</a></td></tr>`,
    ) : emptyState("暂无抽取任务", "选择已发布本体并配置来源。")}</section>`;
  wireSearch(root, value => renderExtractionList(root, value));
  $("[data-new-extraction]", root)?.addEventListener("click", () => extractionForm(null, () => renderExtractionList(root, keyword)).catch(showError));
}

// 中文说明：候选证据展示（折叠）：证据是候选可信度的依据，必须与候选一起可见，不要默认展开以免列表过长。
function candidateEvidence(item) {
  const matches = item.evidence?.matches || [];
  return `<details class="evidence-details"><summary>${matches.length} 条证据</summary><pre>${esc(pretty(item.evidence))}</pre></details>`;
}

// 中文说明：单条候选复核弹窗（接受/驳回 + 说明 + 可编辑 payload）。
// 约束：payload 里概念与关系类型不可改动（由后端校验），仅允许修正属性值；保存带 candidate.version。
function candidateReviewDialog(job, run, candidate, reload) {
  openDialog("复核候选", `<div class="form-grid">${field("决定", select("decision", [{ value: "accepted", label: "接受" }, { value: "rejected", label: "驳回" }], candidate.reviewStatus === "rejected" ? "rejected" : "accepted", null))}${field("说明", input("note", candidate.reviewNote, 'maxlength="2000"'))}${field("候选内容 JSON", textarea("payload", pretty(candidate.payload), 'rows="12" required spellcheck="false"'), "概念或关系类型不可修改。", true)}</div>`, {
    wide: true,
    onSave: async form => {
      await post(`${EXTRACTION}/${job.id}/runs/${run.id}/review`, { items: [{
        candidateId: candidate.id, version: candidate.version, decision: form.get("decision"),
        note: form.get("note"), payload: json(form.get("payload"), "候选内容", {}),
      }] });
      toast("候选复核已保存"); await reload();
    },
  });
}

// 中文说明：抽取任务详情页：候选复核（逐条/批量）、执行抽取、写入图谱，以及来源配置与运行记录。
// 约束：只有 run.status === "review" 时勾选框与复核按钮可用；存在待复核候选时“写入图谱”按钮禁用
//   （对应后端要求候选全部处理完）；写入图谱需要选择目标图谱并带 graphVersion。
// 副作用：执行抽取、复核、materialize 都会真实写入数据；执行失败会先 reload 再抛出，保证页面显示后端错误原因。
async function renderExtractionDetail(root, id) {
  const job = await get(`${EXTRACTION}/${encodeURIComponent(id)}`);
  const reload = () => renderExtractionDetail(root, id);
  const run = job.runs?.[0];
  const candidates = run && ["review", "completed"].includes(run.status)
    ? await get(`${EXTRACTION}/${job.id}/runs/${run.id}/candidates?limit=500`) : [];
  const pending = candidates.filter(item => item.reviewStatus === "pending");
  const accepted = candidates.filter(item => item.reviewStatus === "accepted");
  root.innerHTML = pageHeader(job.name, job.description || "抽取任务",
    `<a class="button" href="/extraction">返回列表</a>${can("extraction.edit") && job.status !== "running" ? button("编辑配置", "data-edit-extraction") : ""}${can("extraction.execute") && job.status !== "running" ? button("执行抽取", "data-execute-extraction", true) : ""}`) +
    summary([["模式", label(job.mode)], ["状态", label(job.status)], ["本体修订", job.ontologyRevision], ["执行次数", job.executionCount]]) +
    (job.errorMessage ? `<div class="notice-inline error-notice">${esc(job.errorMessage)}</div>` : "") +
    `<section class="panel"><div class="panel-heading"><div><h2>候选复核</h2><p>${run ? `运行 #${run.runNumber} · ${label(run.status)}${run.status === "review" && pending.length ? ` · 还有 ${pending.length} 项待复核` : ""}` : "尚未执行"}</p></div><div class="heading-actions">${pending.length && can("extraction.edit") ? button("接受所选", 'data-batch-review="accepted"') + button("驳回所选", 'data-batch-review="rejected"') : ""}${accepted.length && can("extraction.execute") ? button("写入图谱", `data-materialize ${pending.length && run.status === "review" ? 'disabled title="请先接受或驳回全部候选"' : ""}`, true) : ""}</div></div>` +
    (candidates.length ? dataTable(["选择", "类型 / 内容", "证据", "复核状态", "操作"], candidates,
      item => `<tr><td><input type="checkbox" aria-label="选择候选" data-candidate-check value="${esc(item.id)}" ${item.reviewStatus !== "pending" || run.status !== "review" ? "disabled" : ""}></td><td><span class="cell-primary">${esc(item.payload.name || item.payload.relationTypeId || "关系候选")}</span><span class="cell-secondary">${esc(item.payload.businessKey || item.candidateKey)}</span></td><td>${candidateEvidence(item)}</td><td>${statusBadge(item.reviewStatus)}</td><td>${can("extraction.edit") && run.status === "review" ? `<button class="text-button" data-review-candidate="${esc(item.id)}">复核</button>` : "—"}</td></tr>`) : emptyState(run ? "本次未生成候选" : "尚未执行", run ? "检查识别词、文档内容或字段映射。" : "执行后在此逐项核对来源证据。")) + "</section>" +
    `<div class="resource-columns section-gap"><section class="panel"><div class="panel-heading"><h2>来源配置</h2></div><dl class="detail-grid compact-details"><div><dt>本体</dt><dd>${esc(job.ontologyId)}</dd></div><div><dt>知识库</dt><dd>${esc(job.knowledgeBaseId || "—")}</dd></div><div><dt>记录数</dt><dd>${job.recordCount}</dd></div><div><dt>文档数</dt><dd>${job.documentIds.length}</dd></div></dl></section><section class="panel"><div class="panel-heading"><h2>运行记录</h2></div>${job.runs?.length ? `<div class="compact-history">${job.runs.map(item => `<div><strong>#${item.runNumber} · ${esc(item.status)}</strong><span>${esc(item.errorMessage || `${item.summary?.candidates || 0} 个候选`)}</span><time>${date(item.startedAt)}</time></div>`).join("")}</div>` : emptyState("暂无运行", "")}</section></div>`;
  $("[data-edit-extraction]", root)?.addEventListener("click", () => extractionForm(job, reload).catch(showError));
  wireResourceRemoval(root, job, { permission: "extraction.delete", apiPath: EXTRACTION, listPath: "/extraction", title: "删除任务", note: "执行中的任务不能删除。" });
  $("[data-execute-extraction]", root)?.addEventListener("click", () => confirmAction("执行抽取", "将读取当前配置的真实来源并生成新的待复核候选。", async () => {
    try { await post(`${EXTRACTION}/${job.id}/execute`, { version: job.version }); }
    catch (error) { await reload(); throw error; }
    toast("抽取已完成，候选等待复核"); await reload();
  }, "执行"));
  const candidateById = new Map(candidates.map(item => [item.id, item]));
  $$('[data-review-candidate]', root).forEach(element => element.addEventListener("click", () => candidateReviewDialog(job, run, candidateById.get(element.dataset.reviewCandidate), reload)));
  $$('[data-batch-review]', root).forEach(element => element.addEventListener("click", async () => {
    const selected = $$('[data-candidate-check]:checked', root).map(box => candidateById.get(box.value));
    if (!selected.length) { toast("请先选择待复核候选", true); return; }
    try {
      await post(`${EXTRACTION}/${job.id}/runs/${run.id}/review`, { items: selected.map(item => ({
        candidateId: item.id, version: item.version, decision: element.dataset.batchReview, note: "批量复核",
      })) });
      toast("候选已批量复核"); await reload();
    } catch (error) { showError(error); }
  }));
  $("[data-materialize]", root)?.addEventListener("click", async () => {
    try {
      const graphs = await get(GRAPHS);
      openDialog("写入目标图谱", `<div class="form-grid">${field("目标图谱", select("graphId", graphs.map(item => ({ value: item.id, label: `${item.name} · 本体修订 ${item.ontologyRevision}` })), "", "请选择"), "", true)}</div>`, {
        saveLabel: "写入图谱",
        onSave: async form => {
          const graph = graphs.find(item => item.id === form.get("graphId"));
          if (!graph) throw new Error("请选择目标图谱");
          await post(`${EXTRACTION}/${job.id}/materialize`, { graphId: graph.id, graphVersion: graph.version, runId: run.id });
          toast("已接受候选已写入图谱"); await reload();
        },
      });
    } catch (error) { showError(error); }
  });
}

/**
 * 中文说明：抽取页面入口（列表 / 详情分发）。
 */
export async function renderExtraction(root) {
  return queryId() ? renderExtractionDetail(root, queryId()) : renderExtractionList(root);
}

// 中文说明：图谱表单。已有实体后不允许更换本体（本体变更会让既有实体/关系失去语义），提示语与后端一致。
async function graphForm(record, reload) {
  const ontologies = (await get(ONTOLOGY)).filter(item => item.publishedRevision);
  openDialog(record ? "编辑图谱" : "新建图谱", `<div class="form-grid">${field("图谱名称", input("name", record?.name, 'required maxlength="200"'))}${field("本体模型", select("ontologyId", ontologies.map(item => ({ value: item.id, label: `${item.name} · 修订 ${item.publishedRevision}` })), record?.ontologyId, "请选择已发布本体"), record?.entityCount ? "已有实体后不能更换本体。" : "")}${field("说明", textarea("description", record?.description, 'maxlength="4000"'), "", true)}</div>`, {
    onSave: async form => {
      const payload = { name: form.get("name"), ontologyId: form.get("ontologyId"), description: form.get("description") };
      if (record) { payload.version = record.version; await put(`${GRAPHS}/${record.id}`, payload); }
      else await post(GRAPHS, payload);
      toast("图谱已保存"); await reload();
    },
  });
}

// 中文说明：纯 SVG 环形布局的关系可视化（不引入图形库）。
// 设计约束：所有节点/连线文本都经 esc 转义，并使用 <title> 提供悬浮完整信息；
//   节点数为 0 时返回空状态而不是空画布。半径随节点数增长但有上限，避免超出画布。
function graphSvg(view) {
  const nodes = view.nodes || [], edges = view.edges || [];
  if (!nodes.length) return emptyState("暂无可视化节点", "新增实体或从抽取任务写入候选。") ;
  const width = 860, height = 390, radius = Math.min(145, 55 + nodes.length * 7);
  const positions = new Map(nodes.map((node, index) => {
    const angle = (Math.PI * 2 * index / nodes.length) - Math.PI / 2;
    return [node.id, { x: width / 2 + Math.cos(angle) * radius, y: height / 2 + Math.sin(angle) * radius }];
  }));
  const edgeMarkup = edges.map(edge => {
    const source = positions.get(edge.source), target = positions.get(edge.target);
    return source && target ? `<line x1="${source.x}" y1="${source.y}" x2="${target.x}" y2="${target.y}"><title>${esc(edge.label)}</title></line>` : "";
  }).join("");
  const nodeMarkup = nodes.map(node => {
    const point = positions.get(node.id);
    const label = String(node.label || node.name).slice(0, 12);
    return `<g transform="translate(${point.x} ${point.y})"><circle r="25" fill="${esc(node.color || "#5f83ad")}"></circle><text y="42" text-anchor="middle">${esc(label)}</text><title>${esc(node.name)} · ${esc(node.businessKey)}</title></g>`;
  }).join("");
  return `<div class="graph-canvas"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="知识图谱关系图"><g class="graph-edges">${edgeMarkup}</g><g class="graph-nodes">${nodeMarkup}</g></svg></div>`;
}

// 中文说明：图谱列表页。
async function renderGraphList(root, keyword = "") {
  const items = await get(GRAPHS + (keyword ? "?keyword=" + encodeURIComponent(keyword) : ""));
  root.innerHTML = pageHeader("知识图谱", "编辑实体关系、探索网络并发布固定修订。",
    can("graphs.create") ? button("新建图谱", "data-new-graph", true) : "") +
    `<section class="panel">${listToolbar("搜索图谱名称", keyword)}${items.length ? dataTable(
      ["图谱", "实体 / 关系", "本体修订", "状态", "最后修改", "操作"], items,
      item => `<tr><td><a class="cell-primary" href="/graphs?id=${esc(item.id)}">${esc(item.name)}</a><span class="cell-secondary">${esc(item.description || "—")}</span></td><td>${item.entityCount} / ${item.relationCount}</td><td>${item.ontologyRevision}</td><td>${statusBadge(item.status)}</td><td>${date(item.updatedAt)}</td><td><a class="text-button" href="/graphs?id=${esc(item.id)}">打开</a></td></tr>`,
    ) : emptyState("暂无知识图谱", "选择已发布本体后创建。")}</section>`;
  wireSearch(root, value => renderGraphList(root, value));
  $("[data-new-graph]", root)?.addEventListener("click", () => graphForm(null, () => renderGraphList(root, keyword)).catch(showError));
}

// 中文说明：实体表单。业务键（businessKey）在概念内唯一，用于对齐外部数据；属性 JSON 需满足概念的必填属性。
function entityForm(graph, ontology, record, reload) {
  openDialog(record ? "编辑实体" : "新增实体", `<div class="form-grid">${field("概念", select("conceptId", ontology.concepts.map(item => ({ value: item.id, label: `${item.name} · ${item.code}` })), record?.conceptId, "请选择"))}${field("业务键", input("businessKey", record?.businessKey, 'required maxlength="300"'))}${field("显示名称", input("name", record?.name, 'required maxlength="300"'), "", true)}${field("属性 JSON", textarea("properties", pretty(record?.properties), 'rows="8" spellcheck="false"'), "必须包含所选概念中 required=true 的全部属性。", true)}</div>`, {
    wide: true,
    onSave: async form => {
      const payload = { conceptId: form.get("conceptId"), businessKey: form.get("businessKey"), name: form.get("name"), properties: json(form.get("properties"), "属性", {}) };
      if (record) { payload.version = record.version; await put(`${GRAPHS}/${graph.id}/entities/${record.id}`, payload); }
      else await post(`${GRAPHS}/${graph.id}/entities`, payload);
      toast("实体已保存"); await reload();
    },
  });
}

// 中文说明：图谱内关系表单：关系类型决定允许的起点/终点概念，基数由本体定义（此处不做前端推断）。
function graphRelationForm(graph, ontology, entities, record, reload) {
  const entityChoices = entities.map(item => ({ value: item.id, label: `${item.name} · ${item.businessKey}` }));
  openDialog(record ? "编辑关系" : "新增关系", `<div class="form-grid">${field("关系类型", select("relationTypeId", ontology.relationTypes.map(item => ({ value: item.id, label: `${item.name} · ${item.cardinality}` })), record?.relationTypeId, "请选择"), "", true)}${field("起点实体", select("sourceEntityId", entityChoices, record?.sourceEntityId, "请选择"))}${field("终点实体", select("targetEntityId", entityChoices, record?.targetEntityId, "请选择"))}${field("关系属性 JSON", textarea("properties", pretty(record?.properties), 'rows="7" spellcheck="false"'), "仅填写所选关系类型已定义的属性，并包含 required=true 的字段。", true)}</div>`, {
    wide: true,
    onSave: async form => {
      const payload = { relationTypeId: form.get("relationTypeId"), sourceEntityId: form.get("sourceEntityId"), targetEntityId: form.get("targetEntityId"), properties: json(form.get("properties"), "关系属性", {}) };
      if (record) { payload.version = record.version; await put(`${GRAPHS}/${graph.id}/relations/${record.id}`, payload); }
      else await post(`${GRAPHS}/${graph.id}/relations`, payload);
      toast("关系已保存"); await reload();
    },
  });
}

// 中文说明：图谱详情页：统计条 + 关系探索（explore，支持按实体名/业务键过滤，深度固定 1 层、上限 300 节点）
//   + 实体/关系两张表 + 发布修订。删除实体/关系走统一确认弹窗，存在引用时后端拒绝。
async function renderGraphDetail(root, id, query = "") {
  const [graph, entities, relations] = await Promise.all([
    get(`${GRAPHS}/${encodeURIComponent(id)}`), get(`${GRAPHS}/${encodeURIComponent(id)}/entities?limit=500`),
    get(`${GRAPHS}/${encodeURIComponent(id)}/relations?limit=500`),
  ]);
  const [ontology, view] = await Promise.all([
    get(`${ONTOLOGY}/${graph.ontologyId}`), get(`${GRAPHS}/${graph.id}/explore?query=${encodeURIComponent(query)}&depth=1&limit=300`),
  ]);
  const reload = () => renderGraphDetail(root, id, query);
  const entityById = new Map(entities.map(item => [item.id, item]));
  const relationById = new Map(relations.map(item => [item.id, item]));
  root.innerHTML = pageHeader(graph.name, graph.description || "知识图谱",
    `<a class="button" href="/graphs">返回列表</a>${can("graphs.edit") ? button("编辑", "data-edit-graph") : ""}${can("graphs.create") ? button("新增实体", "data-add-entity") + (entities.length ? button("新增关系", "data-add-relation") : "") : ""}${can("graphs.publish") ? button("发布修订", "data-publish-graph", true) : ""}`) +
    summary([["状态", label(graph.status)], ["实体", graph.statistics.entityCount], ["关系", graph.statistics.relationCount], ["孤立实体", graph.statistics.isolatedEntityCount], ["正式修订", graph.publishedRevision || "—"]]) +
    `<section class="panel"><div class="panel-heading"><h2>关系探索</h2><form class="inline-search" data-graph-search><input name="query" value="${esc(query)}" placeholder="实体名称或业务键" aria-label="搜索图谱"><button class="button" type="submit">查询</button></form></div>${graphSvg(view)}</section>
    <div class="resource-columns section-gap"><section class="panel"><div class="panel-heading"><h2>实体</h2></div>${entities.length ? dataTable(["名称 / 业务键", "概念", "属性", "操作"], entities,
      item => `<tr><td><span class="cell-primary">${esc(item.name)}</span><span class="cell-secondary">${esc(item.businessKey)}</span></td><td>${esc(item.conceptName || item.conceptId)}</td><td><code>${esc(Object.keys(item.properties).join("、") || "—")}</code></td><td><div class="row-actions">${can("graphs.edit") ? `<button class="text-button" data-edit-entity="${esc(item.id)}">编辑</button>` : ""}${can("graphs.delete") ? `<button class="text-button danger" data-delete-entity="${esc(item.id)}">删除</button>` : ""}</div></td></tr>`) : emptyState("暂无实体", "")}</section>
    <section class="panel"><div class="panel-heading"><h2>关系</h2></div>${relations.length ? dataTable(["关系", "起点 → 终点", "来源", "操作"], relations,
      item => `<tr><td>${esc(item.relationName || item.relationTypeId)}</td><td>${esc(entityById.get(item.sourceEntityId)?.name || item.sourceEntityId)} → ${esc(entityById.get(item.targetEntityId)?.name || item.targetEntityId)}</td><td>${esc(item.provenance?.source || "manual")}</td><td><div class="row-actions">${can("graphs.edit") ? `<button class="text-button" data-edit-graph-relation="${esc(item.id)}">编辑</button>` : ""}${can("graphs.delete") ? `<button class="text-button danger" data-delete-graph-relation="${esc(item.id)}">删除</button>` : ""}</div></td></tr>`) : emptyState("暂无关系", "")}</section></div>`;
  $("[data-graph-search]", root)?.addEventListener("submit", event => { event.preventDefault(); renderGraphDetail(root, id, String(new FormData(event.currentTarget).get("query") || "").trim()).catch(showError); });
  $("[data-edit-graph]", root)?.addEventListener("click", () => graphForm(graph, reload).catch(showError));
  wireResourceRemoval(root, graph, { permission: "graphs.delete", apiPath: GRAPHS, listPath: "/graphs", title: "删除图谱", note: "仍被 Bot 使用时系统会拒绝。" });
  $("[data-add-entity]", root)?.addEventListener("click", () => entityForm(graph, ontology, null, reload));
  $("[data-add-relation]", root)?.addEventListener("click", () => graphRelationForm(graph, ontology, entities, null, reload));
  $$('[data-edit-entity]', root).forEach(element => element.addEventListener("click", () => entityForm(graph, ontology, entityById.get(element.dataset.editEntity), reload)));
  $$('[data-edit-graph-relation]', root).forEach(element => element.addEventListener("click", () => graphRelationForm(graph, ontology, entities, relationById.get(element.dataset.editGraphRelation), reload)));
  $$('[data-delete-entity]', root).forEach(element => element.addEventListener("click", () => {
    const item = entityById.get(element.dataset.deleteEntity);
    confirmAction("删除实体", `确认删除“${item.name}”？存在关系时系统会拒绝。`, async () => { await remove(`${GRAPHS}/${graph.id}/entities/${item.id}?version=${item.version}`); toast("实体已删除"); await reload(); }, "删除");
  }));
  $$('[data-delete-graph-relation]', root).forEach(element => element.addEventListener("click", () => {
    const item = relationById.get(element.dataset.deleteGraphRelation);
    confirmAction("删除关系", "确认删除这条关系？", async () => { await remove(`${GRAPHS}/${graph.id}/relations/${item.id}?version=${item.version}`); toast("关系已删除"); await reload(); }, "删除");
  }));
  $("[data-publish-graph]", root)?.addEventListener("click", () => openPublish("发布图谱修订", graph.version, async payload => { await post(`${GRAPHS}/${graph.id}/publish`, payload); toast("图谱正式修订已发布"); await reload(); }));
}

/**
 * 中文说明：图谱页面入口（列表 / 详情分发）。
 */
export async function renderGraphs(root) {
  return queryId() ? renderGraphDetail(root, queryId()) : renderGraphList(root);
}

// 中文说明：Bot 绑定的多选组（知识库/图谱/工作流/Skills/工具共用）。
// 非 published 状态的绑定对象会在名称后追加状态，提醒发布前后行为差异。
function bindingGroup(label, name, options, selected) {
  return field(label, checkboxes(name, options.map(item => ({ value: item.id, label: item.name + (item.status && item.status !== "published" ? ` · ${item.status}` : "") })), selected), "", true);
}

// 中文说明：Bot 表单。当前实现只支持知识库与图谱问答，工作流/Skills/工具的绑定可保存但不会在本页问答复用——
// 该提示必须保留，避免用户以为绑定后会自动执行流程。绑定选项来自 GET /bots/binding-options。
async function botForm(record, reload) {
  const options = await get(`${BOTS}/binding-options`);
  const defaults = record || { botType: "agent", modelProfile: "chat", apiEnabled: false, knowledgeBaseIds: [], graphIds: [], workflowIds: [], skillIds: [], toolIds: [] };
  openDialog(record ? "编辑 Bot" : "新建 Bot", `<p class="notice-inline">当前 Bot 支持知识库与图谱问答。工作流、技能和工具的关联可以保存，但不会在本页问答中自动执行；执行任务请进入 AI 工作台或可视化工作流。</p><div class="form-grid">
    ${field("名称", input("name", defaults.name, 'required maxlength="200"'))}${field("类型", select("botType", [{ value: "agent", label: "Agent" }, { value: "knowledge", label: "知识问答" }, { value: "chatflow", label: "Chatflow" }], defaults.botType, null))}
    ${field("模型配置", input("modelProfile", defaults.modelProfile, 'required maxlength="80"'))}${field("API 调用", select("apiEnabled", [{ value: "false", label: "关闭" }, { value: "true", label: "启用" }], String(defaults.apiEnabled), null))}
    ${field("指令", textarea("instructions", defaults.instructions, 'rows="6" maxlength="20000"'), "", true)}
    ${bindingGroup("知识库", "knowledgeBaseIds", options.knowledgeBases, defaults.knowledgeBaseIds)}
    ${bindingGroup("知识图谱", "graphIds", options.graphs, defaults.graphIds)}
    ${bindingGroup("工作流", "workflowIds", options.workflows, defaults.workflowIds)}
    ${bindingGroup("Skills", "skillIds", options.skills, defaults.skillIds)}
    ${bindingGroup("工具", "toolIds", options.tools, defaults.toolIds)}
    ${field("欢迎语", input("welcomeMessage", defaults.welcomeMessage, 'maxlength="1000"'))}${field("说明", textarea("description", defaults.description, 'maxlength="4000"'), "", true)}
  </div>`, {
    wide: true,
    onSave: async form => {
      const payload = {
        name: form.get("name"), description: form.get("description"), botType: form.get("botType"),
        instructions: form.get("instructions"), modelProfile: form.get("modelProfile"),
        knowledgeBaseIds: form.getAll("knowledgeBaseIds"), graphIds: form.getAll("graphIds"),
        workflowIds: form.getAll("workflowIds"), skillIds: form.getAll("skillIds"), toolIds: form.getAll("toolIds"),
        welcomeMessage: form.get("welcomeMessage"), apiEnabled: form.get("apiEnabled") === "true",
      };
      if (record) { payload.version = record.version; await put(`${BOTS}/${record.id}`, payload); }
      else await post(BOTS, payload);
      toast("Bot 已保存"); await reload();
    },
  });
}

// 中文说明：Bot 列表页（资源绑定数量为各类型求和）。
async function renderBotList(root, keyword = "") {
  const items = await get(BOTS + (keyword ? "?keyword=" + encodeURIComponent(keyword) : ""));
  root.innerHTML = pageHeader("Bot 与智能体", "配置知识库与图谱问答，调试后发布给业务系统调用。",
    can("bots.create") ? button("新建 Bot", "data-new-bot", true) : "") +
    `<section class="panel">${listToolbar("搜索 Bot 名称", keyword)}${items.length ? dataTable(
      ["Bot", "类型", "资源绑定", "状态", "API", "最后修改", "操作"], items,
      item => `<tr><td><a class="cell-primary" href="/bots?id=${esc(item.id)}">${esc(item.name)}</a><span class="cell-secondary">${esc(item.description || "—")}</span></td><td>${esc(item.botType)}</td><td>${Object.values(item.bindingCounts).reduce((sum, value) => sum + value, 0)}</td><td>${statusBadge(item.status)}</td><td>${item.apiEnabled ? "启用" : "关闭"}</td><td>${date(item.updatedAt)}</td><td><a class="text-button" href="/bots?id=${esc(item.id)}">打开</a></td></tr>`,
    ) : emptyState("暂无 Bot", "创建后绑定知识库或图谱，测试回答并发布。")}</section>`;
  wireSearch(root, value => renderBotList(root, value));
  $("[data-new-bot]", root)?.addEventListener("click", () => botForm(null, () => renderBotList(root, keyword)).catch(showError));
}

// 中文说明：创建 API Key。明文 key 只在创建响应里返回一次，创建后用第二个弹窗展示并提示“关闭后无法再次查看”；
// 复制走统一 clipboard 模块（局域网 HTTP 下也能工作）。到期时间可空（表示长期有效）。
function keyDialog(bot, reload) {
  openDialog("创建 API Key", `<div class="form-grid">${field("名称", input("name", "业务系统调用", 'required maxlength="160"'))}${field("到期时间", input("expiresAt", "", 'type="datetime-local"'))}</div>`, {
    saveLabel: "创建",
    onSave: async form => {
      const raw = form.get("expiresAt");
      const value = await post(`${BOTS}/${bot.id}/api-keys`, { name: form.get("name"), expiresAt: raw ? new Date(raw).toISOString() : null });
      openDialog("请立即保存 API Key", `<div class="one-time-secret"><p>关闭后无法再次查看。</p><code>${esc(value.key)}</code><button class="button" type="button" data-copy-key>复制</button></div>`, {
        onReady: dialog => $("[data-copy-key]", dialog)?.addEventListener("click", async () => {
          try { await copyText(value.key); toast("API Key 已复制"); }
          catch (error) { toast(error.message, true); }
        }),
      });
      await reload();
    },
  });
}

// 中文说明：Bot 运行记录表（渠道、问题、回答/错误、耗时）。问答内容做截断展示，完整内容请查看后端记录。
function botRunHistory(runs) {
  return runs.length ? dataTable(["渠道", "问题", "状态", "耗时", "时间"], runs,
    item => `<tr><td>${esc(item.channel === "api" ? "API" : "调试")}</td><td><span class="cell-primary">${esc(item.query.slice(0, 60))}</span><span class="cell-secondary">${esc(item.answer.slice(0, 90) || item.errorMessage)}</span></td><td>${statusBadge(item.status)}</td><td>${item.durationMs} ms</td><td>${date(item.createdAt)}</td></tr>`) : emptyState("暂无运行", "");
}

// 中文说明：Bot 详情页：调试问答（retrieval 只检索证据 / model 模型归纳，可选用正式修订快照）、
//   资源绑定概览、运行记录、API Key 管理与正式修订记录。
// 约束：调试失败后仍要刷新运行记录（失败也会落库），刷新失败另行提示；
//   API Key 只在 apiEnabled 且已发布时才允许创建；吊销走统一确认弹窗且不可恢复。
async function renderBotDetail(root, id) {
  const bot = await get(`${BOTS}/${encodeURIComponent(id)}`);
  const [runs, keys, revisions] = await Promise.all([
    get(`${BOTS}/${bot.id}/runs?limit=50`), get(`${BOTS}/${bot.id}/api-keys`), get(`${BOTS}/${bot.id}/revisions`),
  ]);
  const reload = () => renderBotDetail(root, id);
  root.innerHTML = pageHeader(bot.name, bot.description || "业务 Bot",
    `<a class="button" href="/bots">返回列表</a>${can("bots.edit") ? button("编辑配置", "data-edit-bot") : ""}${can("bots.publish") ? button("发布修订", "data-publish-bot", true) : ""}`) +
    summary([["类型", label(bot.botType)], ["状态", label(bot.status)], ["正式修订", bot.publishedRevision || "—"], ["API", bot.apiEnabled ? "启用" : "关闭"]]) +
    `<div class="resource-columns"><section class="panel"><div class="panel-heading"><h2>调试</h2></div><form class="bot-debug-form" data-bot-debug><textarea name="query" aria-label="调试问题" required maxlength="8000" placeholder="输入需要基于已绑定资源回答的问题"></textarea><div><select name="mode" aria-label="调试方式"><option value="retrieval">只检索证据</option><option value="model">模型归纳</option></select><label><input type="checkbox" name="usePublished" ${bot.publishedRevision ? "" : "disabled"}> 使用正式修订</label><button class="button primary" type="submit">运行</button></div></form><div data-debug-result></div></section>
    <section class="panel"><div class="panel-heading"><div><h2>资源绑定</h2><p>编辑后发布才进入正式快照</p></div></div><div class="binding-summary">${Object.entries(bot.bindingCounts).map(([name, count]) => `<span>${esc(({ knowledgeBases: "知识库", graphs: "图谱", workflows: "流程", skills: "Skills", tools: "工具" })[name])}<strong>${count}</strong></span>`).join("")}</div>${bot.instructions ? `<div class="instruction-preview">${esc(bot.instructions)}</div>` : ""}</section></div>
    <div class="resource-columns section-gap"><section class="panel"><div class="panel-heading"><h2>运行记录</h2></div><div data-bot-run-history>${botRunHistory(runs)}</div></section>
    <section class="panel"><div class="panel-heading"><h2>API Key</h2>${can("bots.publish") && bot.apiEnabled && bot.publishedRevision ? button("创建", "data-create-bot-key") : ""}</div>${keys.length ? dataTable(["名称 / 前缀", "状态", "到期", "最后使用", "操作"], keys,
      item => `<tr><td><span class="cell-primary">${esc(item.name)}</span><span class="cell-secondary"><code>${esc(item.keyPrefix)}…</code></span></td><td>${statusBadge(item.status)}</td><td>${date(item.expiresAt)}</td><td>${date(item.lastUsedAt)}</td><td>${item.status === "active" && can("bots.publish") ? `<button class="text-button danger" data-revoke-key="${esc(item.id)}">吊销</button>` : ""}</td></tr>`) : emptyState("暂无 API Key", bot.apiEnabled && bot.publishedRevision ? "点击创建生成调用凭据。" : bot.apiEnabled ? "发布 Bot 后可创建。" : "当前未启用 API。")}</section></div>
    ${revisions.length ? `<details class="panel resource-history"><summary>正式修订记录</summary><div class="compact-history">${revisions.map(item => `<div><strong>修订 ${item.revision}</strong><span>${esc(item.reason)}</span><time>${date(item.createdAt)}</time></div>`).join("")}</div></details>` : ""}`;
  $("[data-edit-bot]", root)?.addEventListener("click", () => botForm(bot, reload).catch(showError));
  wireResourceRemoval(root, bot, { permission: "bots.delete", apiPath: BOTS, listPath: "/bots", title: "删除 Bot", note: "该 Bot 的 API Key 将同时吊销。" });
  $("[data-publish-bot]", root)?.addEventListener("click", () => openPublish("发布 Bot 修订", bot.version, async payload => { await post(`${BOTS}/${bot.id}/publish`, payload); toast("Bot 正式修订已发布"); await reload(); }));
  $("[data-create-bot-key]", root)?.addEventListener("click", () => keyDialog(bot, reload));
  const keyById = new Map(keys.map(item => [item.id, item]));
  $$('[data-revoke-key]', root).forEach(element => element.addEventListener("click", () => {
    const key = keyById.get(element.dataset.revokeKey);
    confirmAction("吊销 API Key", `确认吊销“${key.name}”？该操作不可恢复。`, async () => { await remove(`${BOTS}/${bot.id}/api-keys/${key.id}?version=${key.version}`); toast("API Key 已吊销"); await reload(); }, "吊销");
  }));
  $("[data-bot-debug]", root)?.addEventListener("submit", async event => {
    event.preventDefault(); const form = new FormData(event.currentTarget); const submit = $('[type="submit"]', event.currentTarget); submit.disabled = true;
    try {
      const value = await post(`${BOTS}/${bot.id}/debug`, { query: form.get("query"), mode: form.get("mode"), usePublished: form.has("usePublished") });
      $("[data-debug-result]", root).innerHTML = `<div class="debug-answer"><strong>回答</strong><p>${esc(value.answer)}</p><details><summary>${value.references.length} 条来源</summary><pre>${esc(pretty(value.references))}</pre></details></div>`;
      toast("调试已完成");
    } catch (error) { showError(error); } finally {
      try {
        const latestRuns = await get(`${BOTS}/${bot.id}/runs?limit=50`);
        const history = $("[data-bot-run-history]", root);
        if (history) history.innerHTML = botRunHistory(latestRuns);
      } catch { toast("运行记录刷新失败，请刷新页面重试", true); }
      submit.disabled = false;
    }
  });
}

/**
 * 中文说明：Bot 页面入口（列表 / 详情分发）。
 */
export async function renderBots(root) {
  return queryId() ? renderBotDetail(root, queryId()) : renderBotList(root);
}
