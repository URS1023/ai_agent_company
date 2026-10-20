/** 原生聚合的纯前端回归：无浏览器、网络、数据库或模型；检查实际入口与提交辅助函数。 */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";
import { parse, compileScript, compileTemplate } from "@vue/compiler-sfc";

async function moduleFrom(path) {
  const source = await readFile(new URL(path, import.meta.url), "utf8");
  const result = ts.transpileModule(source, {
    fileName: path,
    reportDiagnostics: true,
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
    },
  });
  assert.equal(result.diagnostics?.length, 0);
  return import(
    `data:text/javascript;base64,${Buffer.from(result.outputText).toString("base64")}`
  );
}
const target = await moduleFrom("../src/modules/native/nativeTargets.ts");
const schema = await moduleFrom("../src/modules/native/bindingSchema.ts");
const knowledge = await moduleFrom("../src/modules/native/knowledge.ts");
let checks = 0;
function check(action) {
  action();
  checks++;
}

for (const [mode, expected] of [
  ["workflow", "workflow"],
  ["chatflow", "workflow"],
  ["agent-chat", "agent"],
  ["agent_v2", "agent_v2"],
  ["openwebui_model", "model_chat"],
]) {
  check(() =>
    assert.deepEqual(
      target.bindingEntry({ app_mode: mode, native_id: "provider/model" }),
      { target: expected, native_id: "provider/model" },
    ),
  );
  check(() =>
    assert.equal(
      target.requiresNativeRun({ app_mode: mode }),
      ["agent_v2", "openwebui_model"].includes(mode),
    ),
  );
}
check(() =>
  assert.deepEqual(target.connectionEntry("openwebui", "connections"), {
    target: "chat",
  }),
);
check(() =>
  assert.deepEqual(target.connectionEntry("dify", "connections"), {
    target: "apps",
  }),
);
for (const engine of ["dify", "openwebui"])
  check(() =>
    assert.deepEqual(target.connectionEntry(engine, "models"), {
      target: "models",
    }),
  );
check(() =>
  assert.deepEqual(target.connectionEntry("openwebui", "dify-agents"), {
    target: "chat",
  }),
);
check(() =>
  assert.deepEqual(target.connectionEntry("dify", "openwebui"), {
    target: "apps",
  }),
);
for (const [config, value] of [
  ["openwebui", "chat"],
  ["models", "models"],
  ["dify-knowledge", "knowledge"],
  ["dify-agents", "agent"],
  ["dify-workflows", "workflow"],
  ["dify-triggers", "workflow"],
  ["dify-plugins", "plugins"],
])
  check(() => assert.equal(target.pageTarget(config), value));
check(() => assert.equal(target.targetPermissions.model_chat, "m02.read"));
check(() => assert.equal(target.targetPermissions.agent_v2, "m03.read"));
check(() => assert.equal(target.targetPermissions.plugins, "m12.admin"));
check(() =>
  assert.equal(
    target.applicationModeLabel("openwebui_model"),
    "Open WebUI模型",
  ),
);

const input = {
  type: "object",
  properties: {
    device_id: { type: "string", title: "设备" },
    count: { type: "integer", minimum: 1 },
  },
  required: ["device_id"],
  additionalProperties: false,
};
check(() =>
  assert.deepEqual(schema.parseInputSchema(JSON.stringify(input)), input),
);
check(() => assert.equal(schema.parseInputSchema("  "), null));
for (const value of [
  "{",
  "[]",
  "null",
  '{"type":"string"}',
  '{"properties":[]}',
  '{"required":[1]}',
  '{"required":["a","a"]}',
])
  check(() => assert.throws(() => schema.parseInputSchema(value)));
check(() =>
  assert.throws(
    () =>
      schema.parseInputSchema(
        JSON.stringify({ description: "质".repeat(34000) }),
      ),
    /100KB/,
  ),
);
const form = {
  native_id: "app-one",
  app_mode: "workflow",
  release_id: "workflow-release",
  credential_ref: "app-key-reference",
};
check(() =>
  assert.deepEqual(
    schema.applicationFields(form, JSON.stringify(input), false),
    { ...form, input_schema: input },
  ),
);
check(() =>
  assert.deepEqual(
    schema.applicationFields({ ...form, credential_ref: "" }, "", true),
    {
      native_id: "app-one",
      release_id: "workflow-release",
      credential_ref: null,
      input_schema: null,
    },
  ),
);
for (const mode of ["agent_v2", "openwebui_model"])
  check(() =>
    assert.deepEqual(
      schema.applicationFields({ ...form, app_mode: mode }, "{", false),
      { native_id: "app-one", app_mode: mode },
    ),
  );
check(() =>
  assert.deepEqual(
    schema.applicationFields(
      { ...form, app_mode: "chatflow" },
      JSON.stringify(input),
      true,
    ),
    {
      native_id: "app-one",
      credential_ref: "app-key-reference",
      input_schema: input,
    },
  ),
);

for (const name of [
  "SOP.TXT",
  "操作规程.md",
  "数据.csv",
  "设置.json",
  "运行.log",
])
  check(() =>
    assert.equal(knowledge.importFileError({ name, size: 2048 }), ""),
  );
for (const file of [
  { name: "资料.pdf", size: 200 },
  { name: "规程.txt", size: 0 },
  { name: "规程.txt", size: 2 * 1024 * 1024 + 1 },
])
  check(() => assert.ok(knowledge.importFileError(file)));
check(() =>
  assert.equal(
    knowledge.documentStatus({ indexing_status: "completed" }).label,
    "可检索",
  ),
);
check(() =>
  assert.equal(
    knowledge.documentStatus({ indexing_status: "completed", enabled: false })
      .label,
    "已停用",
  ),
);
check(() =>
  assert.equal(
    knowledge.documentStatus({ indexing_status: "completed", archived: true })
      .label,
    "已归档",
  ),
);
check(() =>
  assert.equal(
    knowledge.documentStatus({
      indexing_status: "indexing",
      display_status: "paused",
    }).processing,
    false,
  ),
);
check(() =>
  assert.equal(
    knowledge.documentStatus({ indexing_status: "indexing" }).processing,
    true,
  ),
);
check(() =>
  assert.equal(
    knowledge.documentStatus({ indexing_status: "invented" }).label,
    "待核对",
  ),
);
check(() =>
  assert.deepEqual(
    knowledge.citationFromRecord({
      citation_id: "citation-one",
      segment: {
        content: "真实来源",
        position: 3,
        document: { name: "设备规程" },
      },
      score: 0.85,
    }),
    {
      id: "citation-one",
      document_name: "设备规程",
      position: 3,
      content: "真实来源",
      score: 0.85,
    },
  ),
);
check(() => assert.equal(knowledge.scoreLabel(null), "—"));
check(() => assert.equal(knowledge.scoreLabel("bad"), "—"));
check(() => assert.equal(knowledge.scoreLabel(2), "2.0000"));

let compiled = 0;
for (const name of [
  "NativeBindingForm",
  "NativeWorkspace",
  "KnowledgeOperations",
  "KnowledgeDocuments",
  "KnowledgeImportForm",
  "KnowledgeSearch",
  "KnowledgeCitations",
]) {
  const filename = `../src/modules/native/${name}.vue`;
  const { descriptor, errors } = parse(
    await readFile(new URL(filename, import.meta.url), "utf8"),
    { filename },
  );
  assert.deepEqual(errors, []);
  const script = compileScript(descriptor, { id: name });
  const javascript = ts.transpileModule(script.content, {
    reportDiagnostics: true,
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
    },
  });
  assert.equal(javascript.diagnostics?.length, 0);
  const template = compileTemplate({
    id: name,
    filename,
    source: descriptor.template.content,
    compilerOptions: { bindingMetadata: script.bindings },
  });
  assert.deepEqual(template.errors, []);
  compiled++;
}
console.log(
  JSON.stringify({
    pure_assertions: checks,
    sfc_compiled: compiled,
    http_requests: 0,
    database_queries: 0,
    model_calls: 0,
  }),
);
