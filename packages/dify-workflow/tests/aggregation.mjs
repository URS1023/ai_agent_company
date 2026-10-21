/** 原聚合节点纯验收：真实组件SSR、原helpers及本地草稿操作；零浏览器/网络/模型。 */
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { readFile } from "node:fs/promises";
import { build } from "../../../apps/web/node_modules/esbuild/lib/main.js";
const base = new URL("../", import.meta.url),
  root = new URL("../../../", import.meta.url);
let checks = 0;
const check = (action) => {
  action();
  checks++;
};
const bundle = await build({
  stdin: {
    contents: `export * from './src/draft';export {simpleInputSchema} from './src/variables';export * as helpers from './src/vendor/workflow/nodes/variable-assigner/use-config.helpers';export {default as Node} from './src/vendor/workflow/nodes/variable-assigner/node';export {default as Panel} from './src/vendor/workflow/nodes/variable-assigner/panel';export {default as StartPanel} from './src/vendor/workflow/nodes/start/panel';export {default as useConfig} from './src/vendor/workflow/nodes/variable-assigner/use-config';export {default as useStartConfig} from './src/vendor/workflow/nodes/start/use-config';export {EditorContext} from './src/EditorContext';export {createElement} from 'react';export {renderToStaticMarkup} from 'react-dom/server';export {ReactFlowProvider} from 'reactflow';`,
    resolveDir: fileURLToPath(base),
    loader: "tsx",
  },
  bundle: true,
  write: false,
  platform: "node",
  format: "esm",
  jsx: "automatic",
  nodePaths: [fileURLToPath(new URL("apps/web/node_modules", root))],
  banner: {
    js: `import {createRequire as __createRequire} from 'node:module';const require=__createRequire(${JSON.stringify(new URL("apps/web/package.json", root).href)});`,
  },
  logLevel: "silent",
});
const lib = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`
);
function fixture() {
  return {
    format: "graph",
    engine: "graphon",
    engine_version: "0.8.0",
    root_node_id: "start",
    retained: { zero: 0, flag: false },
    graph: {
      viewport: { x: 1, y: 2, zoom: 1 },
      nodes: [
        {
          id: "start",
          position: { x: 0, y: 0 },
          data: {
            type: "start",
            title: "开始",
            variables: [
              {
                variable: "reading",
                label: "读数",
                type: "number",
                required: true,
                default: 0,
              },
              {
                variable: "enabled",
                label: "启用",
                type: "checkbox",
                required: false,
                default: false,
              },
              {
                variable: "text",
                label: "文本",
                type: "text-input",
                required: false,
                default: "",
              },
              {
                variable: "payload",
                label: "批次",
                type: "json_object",
                required: true,
                json_schema: {
                  type: "object",
                  properties: {
                    values: { type: "array", items: { type: "number" } },
                    settings: {
                      type: "object",
                      properties: { label: { type: "string" } },
                    },
                  },
                },
                default: { values: [], settings: { label: "" } },
                unknown: { retained: true },
              },
            ],
          },
        },
        {
          id: "agg",
          position: { x: 200, y: 0 },
          data: {
            type: "variable-aggregator",
            title: "变量聚合",
            output_type: "number",
            variables: [["start", "reading"]],
            advanced_settings: { group_enabled: false, groups: [] },
            unknown: { retained: true },
          },
        },
        {
          id: "end",
          position: { x: 400, y: 0 },
          data: {
            type: "end",
            title: "结束",
            outputs: [
              { variable: "reading", value_selector: ["agg", "output"] },
            ],
          },
        },
      ],
      edges: [
        {
          id: "in",
          source: "start",
          target: "agg",
          targetHandle: "target",
          metadata: { retained: true },
        },
        { id: "out", source: "agg", target: "end" },
      ],
    },
  };
}
let definition = fixture(),
  changes = [],
  errors = [];
const context = (nodeId, readonly = false) => ({
  definition,
  selectedId: nodeId,
  readOnly: readonly,
  change(next) {
    definition = next;
    changes.push(next);
  },
  patchNode() {},
  reportError(message) {
    errors.push(message);
  },
});
function render(Component, nodeId = "agg", readonly = false) {
  const node = definition.graph.nodes.find((node) => node.id === nodeId);
  return lib.renderToStaticMarkup(
    lib.createElement(
      lib.ReactFlowProvider,
      null,
      lib.createElement(
        lib.EditorContext.Provider,
        { value: context(nodeId, readonly) },
        lib.createElement(Component, { id: nodeId, data: node.data }),
      ),
    ),
  );
}
function controls(readonly = false, kind = "aggregation") {
  let actions;
  function Capture() {
    const id = kind === "start" ? "start" : "agg";
    actions = (kind === "start" ? lib.useStartConfig : lib.useConfig)(
      id,
      definition.graph.nodes.find((node) => node.id === id).data,
    );
    return null;
  }
  lib.renderToStaticMarkup(
    lib.createElement(
      lib.EditorContext.Provider,
      { value: context(kind === "start" ? "start" : "agg", readonly) },
      lib.createElement(Capture),
    ),
  );
  return actions;
}
check(() => assert.deepEqual(lib.editingBlockers(definition), []));
check(() => assert.deepEqual(lib.validateDraft(definition), {}));
const malformed = fixture();
malformed.graph.nodes[1].data.advanced_settings = {
  group_enabled: true,
  groups: null,
};
check(() =>
  assert.equal(lib.readableAggregation(malformed.graph.nodes[1].data), false),
);
check(() => assert.ok(lib.editingBlockers(malformed).length));
const candidates = lib.availableVariables(definition, "agg");
check(() =>
  assert.ok(
    candidates.some(
      (option) =>
        JSON.stringify(option.selector) ===
          JSON.stringify(["start", "payload", "values"]) &&
        option.type === "array[number]",
    ),
  ),
);
check(() =>
  assert.ok(
    candidates.some(
      (option) =>
        JSON.stringify(option.selector) ===
          JSON.stringify(["start", "payload", "settings", "label"]) &&
        option.type === "string",
    ),
  ),
);
check(() =>
  assert.equal(
    candidates.some((option) => option.selector.includes("0")),
    false,
  ),
);
check(() =>
  assert.ok(
    lib
      .availableVariables(definition, "end")
      .some(
        (option) =>
          JSON.stringify(option.selector) ===
            JSON.stringify(["agg", "output"]) && option.type === "number",
      ),
  ),
);
check(() =>
  assert.equal(
    lib.helpers.filterVarByType("number")({ type: "boolean" }),
    false,
  ),
);
check(() =>
  assert.equal(lib.helpers.filterVarByType("number")({ type: "number" }), true),
);
check(() =>
  assert.equal(
    lib.helpers.filterVarByType("array[number]")({ type: "array[any]" }),
    false,
  ),
);
check(() =>
  assert.equal(lib.helpers.filterVarByType("any")({ type: "boolean" }), true),
);
const renamed = lib.renameReferences(definition, "start", "reading", "heat");
check(() =>
  assert.deepEqual(renamed.graph.nodes[1].data.variables, [["start", "heat"]]),
);
check(() =>
  assert.deepEqual(definition.graph.nodes[1].data.variables, [
    ["start", "reading"],
  ]),
);
check(() =>
  assert.equal(lib.isSelectorUsed(definition, ["agg", "output"]), true),
);
check(() =>
  assert.equal(lib.isSelectorUsed(definition, ["start", "unused"]), false),
);
check(() => assert.match(render(lib.Node), /reading/));
check(() => assert.match(render(lib.Node), /data-handleid="target"/));
check(() => assert.match(render(lib.Panel), /分组聚合/));
check(() => assert.match(render(lib.Panel, "agg", true), /disabled/));
check(() =>
  assert.doesNotMatch(
    render(lib.Node, "agg", true),
    /aria-label="添加聚合变量"/,
  ),
);
const startHtml = render(lib.StartPanel, "start");
check(() =>
  assert.doesNotMatch(startHtml, /aria-label="编辑批次"|aria-label="删除批次"/),
);
check(() => assert.match(startHtml, /批次/));
const storedInput = structuredClone(
  definition.graph.nodes[0].data.variables[3],
);
controls(false, "start").handleVarListChange(
  definition.graph.nodes[0].data.variables.filter(
    (field) => field.variable !== "payload",
  ),
);
check(() => assert.equal(changes.length, 0));
check(() =>
  assert.deepEqual(definition.graph.nodes[0].data.variables[3], storedInput),
);
check(() => assert.match(errors.at(-1), /只读/));
const complex = fixture();
complex.graph.nodes[0].data.variables[3].json_schema = {
  type: "object",
  properties: { bad: { $ref: "https://example.com/schema" } },
};
check(() => assert.ok(lib.editingBlockers(complex).length));
check(() =>
  assert.equal(
    lib.simpleInputSchema({
      type: "object",
      properties: { bad: { $ref: "#/x" } },
    }),
    null,
  ),
);
const bad = fixture();
bad.graph.nodes[1].data.output_type = "string";
check(() => assert.match(lib.validateDraft(bad)["nodes.agg"], /类型匹配/));
bad.graph.nodes[1].data.output_type = "number";
bad.graph.nodes[1].data.variables.push(["start", "reading"]);
check(() => assert.match(lib.validateDraft(bad)["nodes.agg"], /重复/));
controls().handleGroupEnabledChange(true);
const group = definition.graph.nodes[1].data.advanced_settings.groups[0];
check(() => assert.equal(group.group_name, "Group1"));
check(() => assert.equal(group.output_type, "number"));
check(() => assert.match(group.groupId, /^[a-f0-9]{32}$/));
check(() =>
  assert.equal(definition.graph.edges[0].targetHandle, group.groupId),
);
check(() =>
  assert.deepEqual(definition.graph.nodes[2].data.outputs[0].value_selector, [
    "agg",
    "Group1",
    "output",
  ]),
);
check(() => assert.deepEqual(definition.retained, { zero: 0, flag: false }));
check(() => assert.deepEqual(lib.validateDraft(definition), {}));
check(() =>
  assert.match(
    render(lib.Node),
    new RegExp(`data-handleid="${group.groupId}"`),
  ),
);
check(() => assert.match(render(lib.Panel), /Group1/));
controls().handleVarGroupNameChange(group.groupId)("Reading");
check(() =>
  assert.equal(
    definition.graph.nodes[1].data.advanced_settings.groups[0].group_name,
    "Reading",
  ),
);
check(() =>
  assert.deepEqual(definition.graph.nodes[2].data.outputs[0].value_selector, [
    "agg",
    "Reading",
    "output",
  ]),
);
check(() =>
  assert.ok(
    lib
      .availableVariables(definition, "end")
      .some(
        (option) =>
          JSON.stringify(option.selector) ===
          JSON.stringify(["agg", "Reading", "output"]),
      ),
  ),
);
const beforeRemove = changes.length;
controls().handleGroupRemoved(group.groupId)();
check(() => assert.equal(changes.length, beforeRemove));
controls().handleAddGroup();
const second = definition.graph.nodes[1].data.advanced_settings.groups[1];
check(() => assert.equal(second.output_type, "any"));
check(() => assert.ok(lib.validateDraft(definition)["nodes.agg"]));
check(() =>
  assert.deepEqual(
    lib.availableVariables(definition, "agg", second.groupId),
    [],
  ),
);
controls().handleVarGroupNameChange(second.groupId)("Reading");
check(() => assert.match(errors.at(-1), /已存在/));
controls().handleVarGroupNameChange(second.groupId)("bad-name");
check(() => assert.match(errors.at(-1), /检查分组名称/));
controls().handleGroupRemoved(second.groupId)();
check(() =>
  assert.equal(
    definition.graph.nodes[1].data.advanced_settings.groups.length,
    1,
  ),
);
controls().handleGroupEnabledChange(false);
check(() =>
  assert.deepEqual(definition.graph.nodes[2].data.outputs[0].value_selector, [
    "agg",
    "output",
  ]),
);
check(() => assert.equal(definition.graph.edges[0].targetHandle, "target"));
check(() => assert.deepEqual(lib.validateDraft(definition), {}));
const beforeReadonly = changes.length;
controls(true).handleGroupEnabledChange(true);
controls(true).handleAddGroup();
check(() => assert.equal(changes.length, beforeReadonly));
definition.graph.nodes[1].data.type = "variable-assigner";
check(() => assert.deepEqual(lib.editingBlockers(definition), []));
check(() => assert.deepEqual(lib.validateDraft(definition), {}));
check(() => assert.match(render(lib.Panel), /分组聚合/));
definition.graph.nodes[1].data.type = "assigner";
check(() => assert.ok(lib.editingBlockers(definition).length));
const modal = await readFile(
  new URL("src/controls/ConfigVariableModal.tsx", base),
  "utf8",
);
check(() => assert.match(modal, /max=\{40000\}/));
check(() => assert.doesNotMatch(modal, /200000/));
console.log(
  JSON.stringify({
    passed: true,
    checks,
    browser: 0,
    http: 0,
    database: 0,
    model: 0,
  }),
);
