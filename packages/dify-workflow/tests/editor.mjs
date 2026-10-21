/** 原组件提取纯验收：真实源码打包到内存并服务端渲染；无浏览器/HTTP/数据库/模型。 */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import { build } from "../../../apps/web/node_modules/esbuild/lib/main.js";
const base = new URL("../", import.meta.url);
const root = new URL("../../../", import.meta.url);
let checks = 0;
const check = (action) => {
  action();
  checks++;
};
const bundle = await build({
  stdin: {
    contents: `export * from './public';export * from './src/draft';export * as helpers from './src/vendor/workflow/nodes/if-else/use-config.helpers';export {default as StartNode} from './src/vendor/workflow/nodes/start/node';export {default as StartPanel} from './src/vendor/workflow/nodes/start/panel';export {default as EndPanel} from './src/vendor/workflow/nodes/end/panel';export {default as IfElseNode} from './src/vendor/workflow/nodes/if-else/node';export {default as IfElsePanel} from './src/vendor/workflow/nodes/if-else/panel';export {EditorContext} from './src/EditorContext';export {createElement} from 'react';export {renderToStaticMarkup} from 'react-dom/server';export {ReactFlowProvider} from 'reactflow';`,
    resolveDir: fileURLToPath(base),
    loader: "tsx",
  },
  bundle: true,
  write: false,
  format: "esm",
  platform: "node",
  jsx: "automatic",
  nodePaths: [fileURLToPath(new URL("apps/web/node_modules", root))],
  banner: {
    js: `import {createRequire as __createRequire} from 'node:module';const require=__createRequire(${JSON.stringify(new URL("apps/web/package.json", root).href)});`,
  },
  logLevel: "silent",
});
const module = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`
);
const graph = {
  format: "graph",
  engine: "graphon",
  engine_version: "0.8.0",
  root_node_id: "start",
  variables: [{ variable: "process.original", value: { kept: false } }],
  original_extra: { remain: "不可丢失" },
  graph: {
    viewport: { x: 4, y: 8, zoom: 0.75 },
    unknown_graph_property: 0,
    nodes: [
      {
        id: "start",
        type: "custom",
        position: { x: 10, y: 20 },
        data: {
          type: "start",
          title: "开始",
          variables: [
            {
              variable: "temperature",
              label: "温度",
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
          ],
          retained: { value: 0 },
        },
      },
      {
        id: "branch",
        type: "custom",
        position: { x: 300, y: 20 },
        data: {
          type: "if-else",
          title: "温度判断",
          _targetBranches: [
            { id: "true", name: "IF" },
            { id: "false", name: "ELSE" },
          ],
          cases: [
            {
              case_id: "true",
              logical_operator: "and",
              conditions: [
                {
                  id: "condition",
                  varType: "number",
                  variable_selector: ["start", "temperature"],
                  comparison_operator: "≥",
                  value: 0,
                },
              ],
            },
          ],
        },
      },
      {
        id: "end",
        type: "custom",
        position: { x: 600, y: 20 },
        data: {
          type: "end",
          title: "结束",
          outputs: [
            {
              variable: "temperature",
              value_selector: ["start", "temperature"],
            },
          ],
          arbitrary: "{{#start.temperature#}}",
        },
      },
      {
        id: "detached",
        type: "custom",
        position: { x: 600, y: 300 },
        data: { type: "end", title: "旁路结束", outputs: [] },
      },
    ],
    edges: [
      {
        id: "a",
        source: "start",
        target: "branch",
        sourceHandle: "source",
        targetHandle: "target",
        data: { retained: "是" },
      },
      {
        id: "b",
        source: "branch",
        target: "end",
        sourceHandle: "true",
        targetHandle: "target",
      },
    ],
  },
};
const initialGraph = module.createInitialDefinition();
check(() => assert.deepEqual(module.validateDraft(initialGraph), {}));
check(() => assert.equal(initialGraph.graph.nodes.length, 2));
check(() =>
  assert.equal(
    initialGraph.graph.nodes[1].data.outputs[0].value_selector[0],
    initialGraph.root_node_id,
  ),
);
check(() =>
  assert.notEqual(
    module.createInitialDefinition().root_node_id,
    initialGraph.root_node_id,
  ),
);
check(() => assert.deepEqual(module.editingBlockers(graph), []));
const copy = module.cloneDefinition(graph);
copy.graph.nodes[0].data.variables[0].default = 20;
check(() => assert.equal(graph.graph.nodes[0].data.variables[0].default, 0));
check(() => assert.equal(copy.graph.nodes[0].data.variables[1].default, false));
const variables = module.availableVariables(graph, "end");
check(() => assert.equal(variables.length, 2));
check(() => assert.deepEqual(variables[0].selector, ["start", "temperature"]));
check(() => assert.equal(variables[1].type, "boolean"));
check(() => assert.deepEqual(module.availableVariables(graph, "detached"), []));
check(() =>
  assert.deepEqual(
    [...module.beforeNodeIds(graph.graph.nodes, graph.graph.edges, "end")],
    ["branch", "start"],
  ),
);
const renamed = module.renameReferences(graph, "start", "temperature", "heat");
check(() =>
  assert.deepEqual(
    renamed.graph.nodes[1].data.cases[0].conditions[0].variable_selector,
    ["start", "heat"],
  ),
);
check(() =>
  assert.deepEqual(renamed.graph.nodes[2].data.outputs[0].value_selector, [
    "start",
    "heat",
  ]),
);
check(() =>
  assert.equal(renamed.graph.nodes[2].data.arbitrary, "{{#start.heat#}}"),
);
check(() =>
  assert.deepEqual(graph.graph.nodes[2].data.outputs[0].value_selector, [
    "start",
    "temperature",
  ]),
);
check(() => assert.deepEqual(renamed.original_extra, graph.original_extra));
check(() => assert.deepEqual(renamed.variables, graph.variables));
check(() => assert.deepEqual(renamed.graph.edges[0].data, { retained: "是" }));
check(() =>
  assert.deepEqual(renamed.graph.viewport, { x: 4, y: 8, zoom: 0.75 }),
);
check(() =>
  assert.equal(module.isVariableUsed(graph, "start", "temperature"), true),
);
check(() =>
  assert.equal(module.isVariableUsed(graph, "start", "enabled"), false),
);
check(() =>
  assert.deepEqual(
    module
      .removeBranch(graph, "branch", "true")
      .graph.edges.map((edge) => edge.id),
    ["a"],
  ),
);
check(() => assert.equal(graph.graph.edges.length, 2));
const valid = module.cloneDefinition(graph);
valid.graph.nodes = valid.graph.nodes.filter((node) => node.id !== "detached");
check(() => assert.deepEqual(module.validateDraft(valid), {}));
const boolean = module.cloneDefinition(valid);
boolean.graph.nodes[1].data.cases[0].conditions[0] = {
  id: "bool",
  varType: "boolean",
  variable_selector: ["start", "enabled"],
  comparison_operator: "is",
  value: false,
};
check(() => assert.deepEqual(module.validateDraft(boolean), {}));
const missing = module.cloneDefinition(valid);
delete missing.graph.nodes[1].data.cases[0].conditions[0].value;
check(() =>
  assert.match(module.validateDraft(missing)["nodes.branch"], /补全/),
);
const dangling = module.cloneDefinition(valid);
dangling.graph.nodes[2].data.outputs[0].value_selector = ["unknown", "x"];
check(() =>
  assert.match(module.validateDraft(dangling)["nodes.end"], /已连接/),
);
for (const type of ["answer", "loop", "iteration", "assigner", "llm", "tool"]) {
  const unsupported = module.cloneDefinition(valid);
  unsupported.graph.nodes[1].data.type = type;
  check(() =>
    assert.equal(module.editingBlockers(unsupported).length > 0, true),
  );
  check(() =>
    assert.deepEqual(module.cloneDefinition(unsupported), unsupported),
  );
}
const schema = module.cloneDefinition(valid);
schema.graph.nodes[0].data.variables[0].type = "json_object";
check(() => assert.match(module.editingBlockers(schema)[0], /字段配置/));
const initial = valid.graph.nodes[1].data;
const add = module.helpers.addCase(initial);
check(() => assert.equal(add.cases.length, 2));
check(() => assert.equal(initial.cases.length, 1));
check(() => assert.equal(add._targetBranches.at(-1).id, "false"));
const condition = module.helpers.addCondition({
  inputs: initial,
  caseId: "true",
  valueSelector: ["start", "enabled"],
  variable: { variable: "enabled", type: "boolean" },
  isVarFileAttribute: false,
});
check(() => assert.equal(condition.cases[0].conditions.at(-1).value, false));
check(() =>
  assert.equal(
    module.helpers.toggleConditionLogicalOperator(initial, "true").cases[0]
      .logical_operator,
    "or",
  ),
);
const sorted = module.helpers.sortCases(
  add,
  [...add.cases].reverse().map((item) => ({ ...item, id: item.case_id })),
);
check(() => assert.equal(sorted.cases[0].case_id, add.cases[1].case_id));
check(() =>
  assert.equal(
    module.helpers.removeCase(add, add.cases[1].case_id).cases.length,
    1,
  ),
);
const context = (id, readOnly = false) => ({
  definition: valid,
  readOnly,
  selectedId: id,
  change() {},
  patchNode() {},
  reportError() {},
});
const render = (component, nodeId, readOnly = false) => {
  const node = valid.graph.nodes.find((item) => item.id === nodeId);
  return module.renderToStaticMarkup(
    module.createElement(
      module.ReactFlowProvider,
      null,
      module.createElement(
        module.EditorContext.Provider,
        { value: context(nodeId, readOnly) },
        module.createElement(component, { id: nodeId, data: node.data }),
      ),
    ),
  );
};
check(() => assert.match(render(module.StartNode, "start"), /temperature/));
check(() => assert.match(render(module.IfElseNode, "branch"), /IF/));
check(() => assert.match(render(module.IfElseNode, "branch"), /ELSE/));
check(() => assert.match(render(module.IfElseNode, "branch"), />0</));
check(() =>
  assert.doesNotMatch(render(module.IfElseNode, "branch"), /请选择条件/),
);
check(() => assert.match(render(module.StartPanel, "start"), /输入字段/));
check(() => assert.match(render(module.EndPanel, "end"), /输出变量/));
check(() => assert.match(render(module.IfElsePanel, "branch"), /添加条件/));
check(() =>
  assert.match(render(module.IfElsePanel, "branch", true), /disabled/),
);
check(() =>
  assert.doesNotMatch(
    render(module.StartPanel, "start", true),
    /aria-label="添加 输入字段"/,
  ),
);
check(() =>
  assert.doesNotMatch(
    render(module.StartPanel, "start"),
    /userinput.files|LEGACY/,
  ),
);
const manifest = JSON.parse(
  await readFile(new URL("SOURCE_MANIFEST.json", base), "utf8"),
);
for (const item of manifest.files) {
  const original = await readFile(new URL(item.source, root));
  check(() =>
    assert.equal(
      createHash("sha256").update(original).digest("hex"),
      item.sha256,
    ),
  );
}
check(() => assert.equal(manifest.files.length, 21));
console.log(
  JSON.stringify({
    passed: true,
    checks,
    source_files: manifest.files.length,
    browser: 0,
    http: 0,
    database: 0,
    model: 0,
  }),
);
