/** 质检计划、证据展示及合成图白名单纯回归；不请求平台、业务源或模型。 */
import assert from "node:assert/strict";
import { readFile, stat } from "node:fs/promises";
import ts from "typescript";
import { parse, compileScript, compileTemplate } from "@vue/compiler-sfc";
import { createRenderer, h, nextTick, reactive } from "vue";

let checks = 0;
const check = (action) => {
  action();
  checks++;
};
const sourceRoot = new URL("../src/modules/manufacturing/", import.meta.url);
const dataUrl = (source) =>
  `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
function transpile(source, filename) {
  const result = ts.transpileModule(source, {
    fileName: filename,
    reportDiagnostics: true,
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
    },
  });
  check(() => assert.equal(result.diagnostics?.length ?? 0, 0));
  return result.outputText;
}
const helperUrl = dataUrl(
  transpile(
    await readFile(new URL("inspectionPresentation.ts", sourceRoot), "utf8"),
    "inspectionPresentation.ts",
  ),
);
const helpers = await import(helperUrl);
const measurementUrl = dataUrl(
  transpile(
    await readFile(new URL("measurementPresentation.ts", sourceRoot), "utf8"),
    "measurementPresentation.ts",
  ),
);
for (const value of [1, 16, 10000])
  check(() => assert.equal(helpers.expectedSamplesError(value), ""));
for (const value of [undefined, null, "16", 0, -1, 1.5, 10001, NaN, Infinity])
  check(() => assert.ok(helpers.expectedSamplesError(value)));
check(() =>
  assert.deepEqual(
    helpers.inspectionSampleCounts(
      { expected_samples: 16, actual_samples: 11, missing_samples: 5 },
      1,
    ),
    { expected: 16, actual: 11, missing: 5 },
  ),
);
check(() =>
  assert.deepEqual(
    helpers.inspectionSampleCounts({
      expected_samples: 16,
      actual_samples: 16,
      missing_samples: 0,
    }),
    { expected: 16, actual: 16, missing: 0 },
  ),
);
check(() =>
  assert.deepEqual(helpers.inspectionSampleCounts(null, 16), {
    expected: 16,
    actual: null,
    missing: null,
  }),
);
check(() =>
  assert.deepEqual(
    helpers.inspectionSampleCounts({
      actual_samples: "16",
      missing_samples: -1,
    }),
    { expected: null, actual: null, missing: null },
  ),
);
const metric = {
  metric_id: "appearance",
  name: "包装外观",
  category: "appearance",
  outcome: "review",
  reason: "存在低置信度或待复核缺陷",
  observation: {
    image_ref: "/demo/quality/uncertain.svg",
    defects: [
      {
        defect_type: "封口",
        severity: "review",
        confidence: "0.7999999999999999999",
      },
    ],
  },
};
const observed = helpers.projectInspectionObservation(metric);
check(() => assert.equal(observed.reason, metric.reason));
check(() =>
  assert.equal(observed.defects[0].confidence, "0.7999999999999999999"),
);
check(() => assert.equal(observed.has_observation, true));
check(() => assert.equal(metric.observation.reason, undefined));
check(() => assert.equal(helpers.observationText(observed), "1处缺陷"));
check(() =>
  assert.equal(helpers.observationText({ defects: [] }), "未发现缺陷"),
);
check(() =>
  assert.equal(
    helpers.observationText(
      helpers.projectInspectionObservation({
        observation: null,
        outcome: "incomplete",
      }),
    ),
    "未提交结果",
  ),
);
check(() =>
  assert.equal(
    helpers.observationText({ value: "0.0000", unit: "g" }),
    "0.0000",
  ),
);
check(() => assert.equal(helpers.observationText({ value: 0 }), "0"));
check(() => assert.equal(helpers.observationText({ defects: null }), "—"));
for (const [value, label] of Object.entries({
  minor: "轻微",
  moderate: "中度",
  severe: "严重",
  review: "待复核",
}))
  check(() => assert.equal(helpers.defectSeverity(value), label));
const names = ["normal", "seam-open", "wrinkle", "uncertain", "uninspected"];
for (const name of names) {
  const path = `/demo/quality/${name}.svg`;
  check(() => assert.equal(helpers.inspectionDemoImage(path), path));
  const asset = await stat(new URL(`../public${path}`, import.meta.url));
  check(() => assert.ok(asset.size > 0));
}
for (const ref of [
  "https://example.com/demo/quality/normal.svg",
  "//example.com/normal.svg",
  "javascript:alert(1)",
  "/demo/quality/normal.svg?x=1",
  "/demo/quality/normal.svg#x",
  "/demo/quality/%6eormal.svg",
  "/demo/quality/../quality/normal.svg",
  "/demo/quality/other.svg",
  "/demo/quality/Normal.svg",
  " /demo/quality/normal.svg",
  "/demo/quality/normal.svg ",
  "data:image/svg+xml,<svg/>",
  "__proto__",
  null,
])
  check(() => assert.equal(helpers.inspectionDemoImage(ref), null));

const vueUrl = import.meta.resolve("vue");
globalThis.__inspectionPresentation = {
  api: {
    async get(path) {
      if (path.startsWith("/devices?"))
        return { items: [{ id: "device-one", name: "循环泵" }], total: 1 };
      if (path.startsWith("/inspection-templates?"))
        return {
          items: [
            {
              id: "standard-one",
              name: "检验标准",
              target_type: "device",
              revision: 3,
            },
          ],
          total: 1,
        };
      if (path.startsWith("/product-batches?")) return { items: [], total: 0 };
      throw new Error("禁止其他请求");
    },
  },
};
const mockUrl = dataUrl(
  "export const {api}=globalThis.__inspectionPresentation; export default {}; ",
);
async function component(filename) {
  const source = await readFile(new URL(filename, sourceRoot), "utf8");
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
  const compiled = transpile(script.content, `${filename}.ts`)
    .replaceAll('"vue"', JSON.stringify(vueUrl))
    .replaceAll("'vue'", JSON.stringify(vueUrl))
    .replaceAll('"./inspectionPresentation"', JSON.stringify(helperUrl))
    .replaceAll('"./measurementPresentation"', JSON.stringify(measurementUrl))
    .replaceAll('"../../api/client"', JSON.stringify(mockUrl))
    .replace(/from "[^"\n]+\.vue"/g, `from ${JSON.stringify(mockUrl)}`);
  return { source, Comp: (await import(dataUrl(compiled))).default };
}
const renderer = createRenderer({
  createElement: (tag) => ({ tag, children: [] }),
  createText: (text) => ({ text }),
  createComment: (text) => ({ text }),
  setElementText(node, text) {
    node.text = text;
  },
  setText(node, text) {
    node.text = text;
  },
  insert(child, parent) {
    parent.children.push(child);
    child.parent = parent;
  },
  remove() {},
  parentNode: (node) => node.parent,
  nextSibling: () => null,
  patchProp() {},
});
const { Comp } = await component("InspectionCreateForm.vue");
const props = reactive({ busy: false, errors: {} });
const events = [];
let state;
const app = renderer.createApp({
  setup() {
    state = Comp.setup(props, {
      expose() {},
      emit: (...event) => events.push(event),
    });
    return () => h("div");
  },
});
app.mount({ children: [] });
for (let i = 0; i < 4; i++) {
  await Promise.resolve();
  await nextTick();
}
check(() => assert.equal(state.form.expected_samples, 1));
state.form.name = "设备批检";
state.form.target_id = "device-one";
state.form.template_id = "standard-one";
state.form.expected_samples = 16;
state.submit();
check(() => assert.equal(events.at(-1)[1].expected_samples, 16));
check(() => assert.equal(events.at(-1)[1].template_revision, 3));
const emitted = events.length;
for (const value of [undefined, 0, 1.5, 10001]) {
  state.form.expected_samples = value;
  state.submit();
  check(() => assert.equal(events.length, emitted));
}
state.form.expected_samples = 16;
props.busy = true;
state.submit();
check(() => assert.equal(events.length, emitted));
app.unmount();

const observationComponent = await component("InspectionObservation.vue");
check(() =>
  assert.ok(observationComponent.source.includes('v-if="previewVisible"')),
);
check(() =>
  assert.ok(observationComponent.source.includes(':src="demoImage"')),
);
check(() =>
  assert.equal(
    /:src="(?:props\.)?observation\.image_ref"|v-html|:href=/.test(
      observationComponent.source,
    ),
    false,
  ),
);
delete globalThis.__inspectionPresentation;
process.stdout.write(
  JSON.stringify({
    passed: true,
    checks,
    http_requests: 0,
    sql_queries: 0,
    model_calls: 0,
  }) + "\n",
);
