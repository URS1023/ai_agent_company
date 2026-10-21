/** 展示精度、原始证据及图表完成状态纯回归；无浏览器、API、数据库或模型。 */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";
import { parse, compileScript, compileTemplate } from "@vue/compiler-sfc";
import { createRenderer, h, reactive, shallowRef } from "vue";
let checks = 0;
const check = (action) => {
  action();
  checks++;
};
const root = new URL("../src/modules/manufacturing/", import.meta.url);
const dataUrl = (text) =>
  `data:text/javascript;base64,${Buffer.from(text).toString("base64")}`;
function js(text, name) {
  const output = ts.transpileModule(text, {
    fileName: name,
    reportDiagnostics: true,
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
    },
  });
  check(() => assert.equal(output.diagnostics?.length ?? 0, 0));
  return output.outputText;
}
const measurementUrl = dataUrl(
  js(
    await readFile(new URL("measurementPresentation.ts", root), "utf8"),
    "measurementPresentation.ts",
  ),
);
const inspectionUrl = dataUrl(
  js(
    await readFile(new URL("inspectionPresentation.ts", root), "utf8"),
    "inspectionPresentation.ts",
  ),
);
const { measurementText, originalMeasurement, comparisonText } = await import(
  measurementUrl
);
for (const [input, expected] of [
  ["23.857213973999023", "23.857"],
  ["1.549372911453247", "1.549"],
  ["1.2345", "1.235"],
  ["-1.2345", "-1.235"],
  ["99.9999", "100"],
  ["9007199254740993.12355", "9007199254740993.124"],
  ["0.0000", "0"],
  [0, "0"],
  [null, "—"],
  [undefined, "—"],
  [true, "—"],
  ["", "—"],
  ["bad", "—"],
  [NaN, "—"],
  [Infinity, "—"],
  ["1e-7", "< 0.001"],
  ["-0.0000001", "> -0.001"],
  ["1.25e2", "125"],
  [".1255", "0.126"],
  ["00023.8574", "23.857"],
])
  check(() => assert.equal(measurementText(input), expected));
check(() => assert.equal(measurementText("53.58531697591146", 2), "53.59"));
check(() => assert.equal(measurementText("-0.0058127", 2, 2), "-0.58"));
check(() => assert.equal(measurementText(null, 2, 2), "—"));
check(() =>
  assert.equal(originalMeasurement("23.857213973999023"), "23.857213973999023"),
);
check(() => assert.equal(originalMeasurement("0.0000"), "0.0000"));
check(() => assert.equal(originalMeasurement(null), null));
check(() => assert.equal(comparisonText(true), "可比较"));
check(() => assert.equal(comparisonText(false), "不可比较"));
check(() => assert.equal(comparisonText(null), "—"));
check(() => assert.equal(comparisonText("false"), "—"));

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
const chartRef = shallowRef(null);
globalThis.__manufacturingPresentation = { chartRef };
const vueUrl = import.meta.resolve("vue");
const vueStub = dataUrl(
  `export * from ${JSON.stringify(vueUrl)}; export const useTemplateRef=()=>globalThis.__manufacturingPresentation.chartRef;`,
);
const emptyStub = dataUrl(
  "export const use=()=>{};export const CanvasRenderer={};export const LineChart={};export const BarChart={};export const PieChart={};export const GridComponent={};export const TooltipComponent={};export const LegendComponent={};export const TitleComponent={};export default {};",
);
async function component(name) {
  const source = await readFile(new URL(name, root), "utf8");
  const { descriptor, errors } = parse(source, { filename: name });
  check(() => assert.deepEqual(errors, []));
  const script = compileScript(descriptor, { id: name });
  const template = compileTemplate({
    source: descriptor.template.content,
    filename: name,
    id: name,
    compilerOptions: { bindingMetadata: script.bindings },
  });
  check(() => assert.deepEqual(template.errors, []));
  let code = js(script.content, name + ".ts");
  for (const [specifier, replacement] of Object.entries({
    vue: vueStub,
    "./measurementPresentation": measurementUrl,
    "./inspectionPresentation": inspectionUrl,
    "echarts/core": emptyStub,
    "echarts/renderers": emptyStub,
    "echarts/charts": emptyStub,
    "echarts/components": emptyStub,
    "vue-echarts": emptyStub,
    "../../components/common/StatusTag.vue": emptyStub,
  }))
    code = code
      .replaceAll(JSON.stringify(specifier), JSON.stringify(replacement))
      .replaceAll(`'${specifier}'`, JSON.stringify(replacement));
  return (await import(dataUrl(code))).default;
}
function mount(Comp, props) {
  let state;
  const events = [];
  const app = renderer.createApp({
    setup() {
      state = Comp.setup(props, {
        expose() {},
        emit: (...args) => events.push(args),
      });
      return () => h("div");
    },
  });
  app.mount({ children: [] });
  return { state, events, close: () => app.unmount() };
}
const props = reactive({
  observation: { value: "23.857213973999023", unit: "℃" },
  preservePrecision: false,
  showImageReference: true,
});
const observed = mount(await component("InspectionObservation.vue"), props);
check(() => assert.equal(observed.state.summary.value, "23.857"));
check(() =>
  assert.equal(observed.state.originalValue.value, "23.857213973999023"),
);
check(() => assert.equal(observed.state.hasDetails.value, true));
props.preservePrecision = true;
check(() => assert.equal(observed.state.summary.value, "23.857213973999023"));
props.observation = { value: null, has_observation: false };
check(() => assert.equal(observed.state.summary.value, "未提交结果"));
observed.close();

const previousMatchMedia = globalThis.matchMedia;
globalThis.matchMedia = () => ({ matches: false });
const chartProps = reactive({
  rows: [
    {
      range: "50–60",
      baseline_mean: 53.06602909997136,
      recent_mean: 52.538602108425565,
    },
  ],
  xField: "range",
  yField: "recent_mean",
  type: "bar",
  height: 300,
  unit: "",
  series: [
    { field: "baseline_mean", name: "基准期" },
    { field: "recent_mean", name: "近期" },
  ],
});
const chart = mount(await component("TrendChart.vue"), chartProps);
let applied;
chartRef.value = { getOption: () => applied };
const applyCurrent = () => {
  const option = chart.state.option.value;
  applied = { xAxis: [option.xAxis], series: option.series };
};
check(() => assert.equal(chart.state.option.value.animation, true));
check(() => assert.equal(chart.state.rendering.value, true));
check(() =>
  assert.equal(
    chart.state.option.value.series[0].data[0],
    chartProps.rows[0].baseline_mean,
  ),
);
check(() => assert.equal(chart.state.option.value.series[1].type, "bar"));
chart.state.finished();
check(() => assert.equal(chart.state.rendering.value, true));
applyCurrent();
chart.state.finished();
check(() => assert.equal(chart.state.rendering.value, false));
check(() => assert.equal(chart.events.length, 1));
chartProps.rows = [
  { range: "60–70", baseline_mean: 55.0000001, recent_mean: null },
];
check(() => assert.equal(chart.state.rendering.value, true));
chart.state.finished();
check(() => assert.equal(chart.state.rendering.value, true));
check(() => assert.equal(chart.state.option.value.series[1].data[0], null));
applyCurrent();
chart.state.finished();
check(() => assert.equal(chart.state.rendering.value, false));
chart.close();
const eventCount = chart.events.length;
chart.state.finished();
check(() => assert.equal(chart.events.length, eventCount));
globalThis.matchMedia = previousMatchMedia;
delete globalThis.__manufacturingPresentation;
console.log(
  JSON.stringify({
    passed: true,
    checks,
    browser_count: 0,
    requests: 0,
    sql_queries: 0,
    model_calls: 0,
  }),
);
