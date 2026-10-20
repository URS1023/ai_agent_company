/** 知识原件管理纯回归：真实Vue作用域、Mock传输，无文件外发或向量请求。 */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";
import { parse, compileScript, compileTemplate } from "@vue/compiler-sfc";
import { effectScope } from "vue";
let checks = 0;
const check = (action) => {
  action();
  checks++;
};
const directory = new URL("../src/modules/knowledge/", import.meta.url);
const asUrl = (text) =>
  `data:text/javascript;base64,${Buffer.from(text).toString("base64")}`;
const vueUrl = import.meta.resolve("vue");
function compile(source, filename) {
  const result = ts.transpileModule(source, {
    fileName: filename,
    reportDiagnostics: true,
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
    },
  });
  check(() => assert.equal(result.diagnostics?.length ?? 0, 0));
  return result.outputText.replaceAll('"vue"', JSON.stringify(vueUrl));
}
class ApiError extends Error {
  constructor(status, message, fields = {}) {
    super(message);
    Object.assign(this, { status, fields });
  }
}
const requests = [];
let respond = async () => ({ items: [], total: 0 });
globalThis.__knowledgeTest = {
  ApiError,
  async request(path, options = {}) {
    requests.push({ path, ...options });
    return respond(path, options);
  },
};
const apiUrl = asUrl(
  "export const {ApiError,request}=globalThis.__knowledgeTest;",
);
const typesSource = await readFile(new URL("knowledge.ts", directory), "utf8");
const typesUrl = asUrl(compile(typesSource, "knowledge.ts"));
const types = await import(typesUrl);
const requestsUrl = asUrl(
  compile(
    await readFile(new URL("useKnowledgeRequests.ts", directory), "utf8"),
    "useKnowledgeRequests.ts",
  ).replaceAll('"../../api/client"', JSON.stringify(apiUrl)),
);
const logicUrl = asUrl(
  compile(
    await readFile(new URL("useKnowledgeDocuments.ts", directory), "utf8"),
    "useKnowledgeDocuments.ts",
  )
    .replaceAll('"../../api/client"', JSON.stringify(apiUrl))
    .replaceAll('"./useKnowledgeRequests"', JSON.stringify(requestsUrl))
    .replaceAll('"./knowledge"', JSON.stringify(typesUrl)),
);
const { useKnowledgeDocuments } = await import(logicUrl);
const doc = (id = "manual", extra = {}) => ({
  id,
  name: "点检手册",
  source: "file",
  char_count: 8,
  chunk_count: 1,
  status: "ready",
  created_at: "2026-10-21T03:00:00+00:00",
  source_file: {
    id: "source",
    name: "点检手册.pdf",
    size: 128,
    revision: 1,
    content_type: "application/pdf",
  },
  ...extra,
});
const chunk = {
  id: "chunk",
  ordinal: 0,
  content: "润滑检查",
  source_location: { page: 2 },
};
function pending() {
  let resolve;
  const promise = new Promise((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}
const scope = effectScope();
let invalidations = 0;
const ui = scope.run(() =>
  useKnowledgeDocuments("space /甲", () => invalidations++),
);
check(() =>
  assert.equal(
    types.knowledgeFileError({ name: "手册.PDF", size: 20 * 1024 * 1024 }),
    "",
  ),
);
check(() =>
  assert.match(
    types.knowledgeFileError({ name: "手册.pdf", size: 20 * 1024 * 1024 + 1 }),
    /20MB/,
  ),
);
check(() =>
  assert.notEqual(types.knowledgeFileError({ name: "脚本.exe", size: 1 }), ""),
);
check(() =>
  assert.notEqual(types.knowledgeFileError({ name: "空.txt", size: 0 }), ""),
);
check(() =>
  assert.equal(
    types.sourceLocation({ sheet: "设备表", row: 3 }),
    "工作表「设备表」 · 第3行",
  ),
);
check(() => assert.equal(types.sourceLocation({ paragraph: 2 }), "第2段"));
check(() =>
  assert.equal(types.sourceLocation({ table: 1, row: 4 }), "表1 · 第4行"),
);
check(() => assert.equal(types.sourceLocation({ slide: 2 }), "第2张幻灯片"));
check(() => assert.equal(types.sourceLocation({}), ""));
check(() =>
  assert.equal(
    types.documentStatus(doc("failed", { status: "failed" })).label,
    "处理失败",
  ),
);
check(() =>
  assert.equal(
    types.documentStatus(doc("parsed", { status: "parsed" })).label,
    "待建索引",
  ),
);
check(() =>
  assert.equal(
    types.documentStatus(doc("indexing", { status: "indexing" })).label,
    "索引中",
  ),
);
check(() =>
  assert.equal(
    types.canRebuild(doc("indexing", { status: "indexing" })),
    false,
  ),
);
check(() =>
  assert.equal(
    types.canRebuild(
      doc("indexing", {
        status: "indexing",
        parser: { index_deadline_at: "invalid" },
      }),
    ),
    false,
  ),
);
check(() =>
  assert.equal(
    types.canRebuild(
      doc("indexing", {
        status: "indexing",
        parser: { index_deadline_at: "2026-10-21T03:00:00+00:00" },
      }),
      Date.parse("2026-10-21T03:00:00+00:00"),
    ),
    false,
  ),
);
check(() =>
  assert.equal(
    types.canRebuild(
      doc("indexing", {
        status: "indexing",
        parser: { index_deadline_at: "2026-10-21T03:00:00+00:00" },
      }),
      Date.parse("2026-10-21T03:00:01+00:00"),
    ),
    true,
  ),
);
respond = async () => ({ items: [doc()], total: 42 });
await ui.load();
check(() =>
  assert.equal(
    requests.at(-1).path,
    "/kb/spaces/space%20%2F%E7%94%B2/documents?q=&limit=20&offset=0",
  ),
);
await ui.changePage(2);
check(() => assert.match(requests.at(-1).path, /offset=20$/));
ui.query.value = " 标准 #? ";
await ui.search();
check(() =>
  assert.equal(
    new URL(requests.at(-1).path, "http://mock.invalid").searchParams.get("q"),
    "标准 #?",
  ),
);
check(() => assert.equal(ui.page.value, 1));

ui.beginImport();
const beforeInvalid = requests.length;
await ui.importDocument({
  kind: "upload",
  file: { name: "过大.pdf", size: 30 * 1024 * 1024 },
});
check(() => assert.equal(requests.length, beforeInvalid));
check(() => assert.match(ui.fields.value.file, /20MB/));
const file = new File(["润滑检查"], "手册.txt", { type: "text/plain" });
respond = async (path, options) =>
  options.method === "POST"
    ? doc("failed-parse", {
        status: "failed",
        error: "文件没有可提取的正文",
        chunk_count: 0,
        char_count: 0,
      })
    : { items: [doc("failed-parse", { status: "failed" })], total: 1 };
const failed = await ui.importDocument({
  kind: "upload",
  file,
  name: " 原件 ",
});
check(() => assert.equal(failed.status, "failed"));
check(() => assert.equal(ui.detail.value.status, "failed"));
check(() => assert.equal(ui.detail.value.error, "文件没有可提取的正文"));
check(() => assert.equal(ui.detailOpen.value, true));
check(() => assert.equal(ui.importing.value, false));
check(() => assert.equal(ui.chunks.value.length, 0));
const upload = requests.findLast((item) => item.path.endsWith("/uploads"));
check(() => assert.equal(upload.timeoutMs, 100000));
check(() => assert.equal(upload.body instanceof FormData, true));
check(() => assert.equal(upload.body.get("file").name, "手册.txt"));
check(() => assert.equal(upload.body.get("name"), "原件"));
check(() =>
  assert.equal(
    requests.some((item) => item.path.includes("index-jobs")),
    false,
  ),
);

respond = async (path, options) =>
  options.method === "POST"
    ? doc("ready")
    : path.includes("/chunks?")
      ? { items: [chunk], total: 21 }
      : { items: [doc("ready")], total: 1 };
ui.beginImport();
await ui.importDocument({
  kind: "paste",
  name: " 中文说明 ",
  text: " 保留文本导入 ",
});
const paste = requests.findLast((item) => item.method === "POST");
check(() =>
  assert.deepEqual(JSON.parse(paste.body), {
    name: "中文说明",
    text: "保留文本导入",
    source: "paste",
  }),
);
check(() => assert.equal(ui.chunks.value[0].ordinal, 0));
check(() =>
  assert.equal(
    types.sourceLocation(ui.chunks.value[0].source_location),
    "第2页",
  ),
);
await ui.changeChunkPage(2);
check(() => assert.match(requests.at(-1).path, /chunks\?limit=20&offset=20$/));
ui.beginImport();
await ui.importDocument({
  kind: "file",
  file_id: "owned-file",
  name: " 共享手册 ",
});
const existing = requests.findLast((item) => item.method === "POST");
check(() => assert.match(existing.path, /\/file-imports$/));
check(() =>
  assert.deepEqual(JSON.parse(existing.body), {
    file_id: "owned-file",
    name: "共享手册",
  }),
);

respond = async (path) =>
  path.includes("/chunks?") ? { items: [chunk], total: 1 } : doc();
await ui.open("manual");
respond = async (path, options) =>
  options.method === "POST"
    ? doc("manual", { status: "failed", error: "向量服务不可用" })
    : { items: [], total: 0 };
const beforeIndex = requests.filter((item) =>
  item.path.includes("index-jobs"),
).length;
await ui.rebuild(ui.detail.value);
check(() =>
  assert.equal(
    requests.filter((item) => item.path.includes("index-jobs")).length,
    beforeIndex + 1,
  ),
);
check(() => assert.equal(ui.detail.value.status, "failed"));
check(() =>
  assert.equal(
    requests.findLast((item) => item.path.includes("index-jobs")).timeoutMs,
    100000,
  ),
);
respond = async () => {
  throw new ApiError(408, "超时");
};
await ui.rebuild(ui.detail.value);
const unknownIndexCount = requests.length;
check(() => assert.equal(ui.indexUncertain.value, true));
await ui.rebuild(ui.detail.value);
check(() => assert.equal(requests.length, unknownIndexCount));
check(() => assert.match(ui.detailError.value, /待确认/));

ui.beginImport();
respond = async () => {
  throw new ApiError(422, "格式不正确", { name: "请修改名称" });
};
await ui.importDocument({ kind: "upload", file });
check(() => assert.equal(ui.fields.value.name, "请修改名称"));
check(() => assert.equal(ui.uncertain.value, false));
respond = async () => {
  throw new ApiError(0, "断线");
};
await ui.importDocument({ kind: "upload", file });
const unknownUploadCount = requests.length;
check(() => assert.equal(ui.uncertain.value, true));
await ui.importDocument({ kind: "upload", file });
check(() => assert.equal(requests.length, unknownUploadCount));
check(() => assert.match(ui.importError.value, /待确认/));

const previousDocument = globalThis.document;
const previousCreate = URL.createObjectURL,
  previousRevoke = URL.revokeObjectURL;
const anchors = [],
  revoked = [],
  blobs = [];
globalThis.document = {
  createElement(tag) {
    assert.equal(tag, "a");
    const anchor = {
      click() {
        anchors.push({ href: this.href, download: this.download });
      },
    };
    return anchor;
  },
};
URL.createObjectURL = (blob) => {
  blobs.push(blob);
  return `blob:mock-${blobs.length}`;
};
URL.revokeObjectURL = (url) => revoked.push(url);
try {
  respond = async () => new Blob(["原件字节"], { type: "application/pdf" });
  await ui.download(doc());
  check(() =>
    assert.equal(requests.at(-1).path, "/kb/documents/manual/source"),
  );
  check(() => assert.equal(requests.at(-1).asBlob, true));
  check(() => assert.equal(anchors[0].download, "点检手册.pdf"));
  const actualText = await blobs[0].text();
  check(() => assert.equal(actualText, "原件字节"));
  const delayedList = pending();
  respond = () => delayedList.promise;
  const oldLoad = ui.load();
  respond = async () => {
    throw new ApiError(403, "原件访问权限已撤销");
  };
  await ui.download(doc());
  check(() => assert.equal(ui.items.value.length, 0));
  check(() => assert.equal(ui.detail.value, null));
  check(() => assert.equal(ui.chunks.value.length, 0));
  check(() => assert.equal(ui.busy.value, false));
  check(() => assert.equal(ui.downloading.value, false));
  check(() => assert.equal(invalidations, 1));
  check(() => assert.equal(revoked.includes("blob:mock-1"), true));
  delayedList.resolve({ items: [doc("late-private")], total: 1 });
  await oldLoad;
  check(() => assert.equal(ui.items.value.length, 0));
  check(() => assert.match(ui.error.value, /撤销/));
  respond = async (path) =>
    path.includes("/chunks?") ? { items: [chunk], total: 1 } : doc();
  await ui.open("manual");
  respond = async () => {
    throw new ApiError(404, "文档不存在");
  };
  await ui.download(doc());
  check(() => assert.equal(ui.detailOpen.value, false));
  check(() => assert.equal(invalidations, 2));
  const delayedDownload = pending();
  respond = () => delayedDownload.promise;
  const downloading = ui.download(doc());
  const signal = requests.at(-1).signal;
  scope.stop();
  delayedDownload.resolve(new Blob(["迟到原件"]));
  await downloading;
  check(() => assert.equal(signal.aborted, true));
  check(() => assert.equal(anchors.length, 1));
  check(() => assert.equal(ui.items.value.length, 0));
  check(() => assert.equal(ui.query.value, ""));
} finally {
  globalThis.document = previousDocument;
  URL.createObjectURL = previousCreate;
  URL.revokeObjectURL = previousRevoke;
}

const uploadScope = effectScope();
const other = uploadScope.run(() => useKnowledgeDocuments("other", () => {}));
const delayedUpload = pending();
respond = () => delayedUpload.promise;
other.beginImport();
const lateImport = other.importDocument({ kind: "upload", file });
const oldSignal = requests.at(-1).signal;
uploadScope.stop();
delayedUpload.resolve(doc("old-owner"));
await lateImport;
check(() => assert.equal(oldSignal.aborted, true));
check(() => assert.equal(other.detail.value, null));
check(() => assert.equal(other.items.value.length, 0));
check(() => assert.equal(other.importing.value, false));
const disposedCount = requests.length;
await other.remove(doc());
await other.importDocument({ kind: "upload", file });
check(() => assert.equal(requests.length, disposedCount));
check(() => assert.equal(other.busy.value, false));

const filenames = [
  "KnowledgeWorkspace.vue",
  "KnowledgeLibrary.vue",
  "KnowledgeDocuments.vue",
  "KnowledgeDocumentDetail.vue",
  "KnowledgeImportDialog.vue",
  "KnowledgeFilePicker.vue",
  "KnowledgeSearch.vue",
];
for (const filename of filenames) {
  const source = await readFile(new URL(filename, directory), "utf8");
  const { descriptor, errors } = parse(source, { filename });
  check(() => assert.deepEqual(errors, []));
  const script = compileScript(descriptor, { id: filename });
  const template = compileTemplate({
    source: descriptor.template.content,
    filename,
    id: filename,
    compilerOptions: { bindingMetadata: script.bindings },
  });
  check(() => assert.deepEqual(template.errors, []));
  compile(script.content, filename + ".ts");
  check(() => assert.equal(source.includes("v-html"), false));
  if (filename === "KnowledgeWorkspace.vue") {
    check(() => assert.match(source, /user\.id/));
    check(() => assert.match(source, /workspace\.id/));
    check(() => assert.match(source, /:key="scopeKey"/));
  }
  if (filename === "KnowledgeDocuments.vue")
    check(() => assert.match(source, /ElMessageBox\.confirm/));
  if (filename === "KnowledgeDocumentDetail.vue")
    check(() => assert.match(source, /下载原件/));
}
check(() =>
  assert.equal(
    requests.some((item) => item.path.startsWith("/files")),
    false,
  ),
);
delete globalThis.__knowledgeTest;
console.log(
  JSON.stringify({
    passed: true,
    assertions: checks,
    compiled_components: filenames.length,
    actual_http: 0,
    actual_sql: 0,
    embedding_calls: 0,
  }),
);
