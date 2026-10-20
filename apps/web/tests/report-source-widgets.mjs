/** 来源12项纯回归：执行真实Vue组件事件但只用内存宿主，无DOM/浏览器/网络/数据库。 */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";
import { parse, compileScript, compileTemplate } from "@vue/compiler-sfc";
import { createRenderer, h, nextTick } from "vue";

let checks = 0;
function check(action) {
  action();
  checks++;
}
function transpile(source, fileName) {
  const result = ts.transpileModule(source, {
    fileName,
    reportDiagnostics: true,
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
    },
  });
  assert.equal(result.diagnostics?.length, 0);
  return result.outputText;
}
function moduleUrl(source) {
  return `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
}
const helperPath = new URL(
  "../src/modules/reports/sourcePresentation.ts",
  import.meta.url,
);
const helperUrl = moduleUrl(
  transpile(await readFile(helperPath, "utf8"), helperPath.pathname),
);
const helper = await import(helperUrl);
const sourceId = (type) =>
  Object.entries(helper.sourcePresentationRegistry).find(
    ([, component]) => component === type,
  )[0];
const row = (name, date, value = null) => ({
  name,
  date,
  time: date,
  description: `${name}说明`,
  value,
  unit: "",
});
const widget = (component, extra = {}) => ({
  id: `test-${component}`,
  component,
  source_component_id: sourceId(component),
  config: { option: {} },
  ...extra,
});
const model = (component, rows, extra = {}) => ({
  status: "ready",
  component,
  rows,
  ...extra,
});

check(() =>
  assert.equal(Object.keys(helper.sourcePresentationRegistry).length, 12),
);
check(() =>
  assert.equal(
    new Set(Object.values(helper.sourcePresentationRegistry)).size,
    10,
  ),
);
const catalog = JSON.parse(
  await readFile(
    new URL(
      "../../../services/platform/agent_platform/modules/reports/resources/component_catalog.json",
      import.meta.url,
    ),
    "utf8",
  ),
);
const sourceItems = catalog.toolbarGroups.flatMap((group) =>
  group.categories.flatMap((category) => category.items),
);
for (const [id, component] of Object.entries(
  helper.sourcePresentationRegistry,
)) {
  check(() =>
    assert.equal(
      sourceItems.find((item) => item.id === id)?.compType,
      component,
    ),
  );
  check(() =>
    assert.equal(
      helper.isSourcePresentation({ source_component_id: id, component }),
      true,
    ),
  );
  check(() =>
    assert.equal(
      helper.isSourcePresentation({
        source_component_id: id,
        component: "JCommon",
      }),
      false,
    ),
  );
}
check(() =>
  assert.equal(
    helper.isSourcePresentation({
      component: "JCardScroll",
      source_component_id: "unknown",
    }),
    false,
  ),
);
check(() =>
  assert.equal(
    helper.sourceModel(
      widget("JCardScroll", { data: [{ name: "不能当可信结果" }] }),
    ),
    undefined,
  ),
);
check(() =>
  assert.equal(
    helper.sourceModel(
      widget("JCardScroll", { presentation_model: model("JCommon", []) }),
    ),
    undefined,
  ),
);
check(() =>
  assert.equal(
    helper.sourceEmptyMessage(
      widget("JCalendar", { binding: { query_id: "q" } }),
    ),
    "刷新数据",
  ),
);
check(() =>
  assert.equal(
    helper.sourceEmptyMessage(
      widget("JCalendar", {
        presentation_model: {
          status: "empty",
          component: "JCalendar",
          rows: [],
        },
      }),
    ),
    "暂无数据",
  ),
);

for (const [month, offset, expected] of [
  ["2024-12", 1, "2025-01"],
  ["2025-01", -1, "2024-12"],
  ["2024-02", 12, "2025-02"],
  ["0001-01", -1, "0001-01"],
  ["9999-12", 1, "9999-12"],
])
  check(() => assert.equal(helper.shiftMonth(month, offset), expected));
check(() => assert.equal(helper.validMonth("2024-13"), false));
check(() => assert.equal(helper.validMonth("0000-01"), false));
check(() =>
  assert.equal(
    helper.shanghaiDate(new Date("2024-02-28T17:00:00Z")),
    "2024-02-29",
  ),
);
const leapRows = [row("闰日维护", "2024-02-29"), row("三月维护", "2024-03-01")];
const grid = helper.calendarGrid("2024-02", leapRows, "2024-02-29");
check(() => assert.equal(grid.length, 42));
check(() => assert.equal(grid.filter((cell) => cell.inMonth).length, 29));
check(() => assert.equal(grid[0].date, "2024-01-29"));
check(() =>
  assert.equal(
    grid.find((cell) => cell.date === "2024-02-29").rows[0].name,
    "闰日维护",
  ),
);
check(() =>
  assert.equal(grid.find((cell) => cell.date === "2024-02-29").today, true),
);
check(() =>
  assert.equal(helper.firstCalendarDate("2024-02", leapRows), "2024-02-29"),
);
check(() => assert.deepEqual(helper.calendarGrid("not-month", leapRows), []));
check(() => assert.equal(helper.pageCount(10, 3), 4));
check(() => assert.equal(helper.pageIndex(0, -1, 4), 3));
check(() => assert.deepEqual(helper.pageRows([1, 2, 3, 4, 5], 1, 3), [4, 5]));

const exact = "12345678901234567890.000100";
const chartRows = [
  row("精确数值", "", exact),
  row("缺测", "", null),
  row("负值", "", "-3"),
];
for (const type of ["JCapsuleChart", "JBackgroundBar"]) {
  const option = helper.sourceChartOption(widget(type), model(type, chartRows));
  check(() => assert.equal(option.series[0].type, "bar"));
  check(() => assert.equal(option.series[0].showBackground, true));
  check(() => assert.equal(option.series[0].data[1], null));
  check(() => assert.equal(option.series[0].data[2], -3));
  check(() =>
    assert.equal(
      option.tooltip.formatter({ dataIndex: 0 }),
      `精确数值: ${exact}`,
    ),
  );
  check(() =>
    assert.equal(
      type === "JCapsuleChart" ? option.yAxis.type : option.xAxis.type,
      "category",
    ),
  );
}
for (const type of ["JColorGauge", "JAntvGauge"]) {
  const option = helper.sourceChartOption(
    widget(type),
    model(type, [row("温度", "", "61.2500")], {
      minimum: "0",
      maximum: "120",
      gauge_position: "0.5104166666666666666666666667",
      gauge_ticks: [
        { position: "0", value: "0" },
        { position: "0.2", value: "24" },
        { position: "0.4", value: "48" },
        { position: "0.6", value: "72" },
        { position: "0.8", value: "96" },
        { position: "1", value: "120" },
      ],
      gauge_stops: [
        { value: "60", color: "#22d3a6", position: "0.5" },
        { value: "120", color: "#f07178", position: "1" },
      ],
    }),
  );
  check(() => assert.equal(option.series[0].type, "gauge"));
  check(() => assert.equal(option.series[0].max, 1));
  check(() => assert.equal(option.series[0].axisLabel.formatter(0.6), "72"));
  check(() => assert.equal(option.series[0].detail.formatter(), "61.2500"));
  check(() =>
    assert.equal(option.series[0].pointer.show, type === "JColorGauge"),
  );
  check(() =>
    assert.equal(
      type === "JColorGauge"
        ? option.series[0].axisLine.lineStyle.color[0][0]
        : option.series[0].axisLine.lineStyle.color[0][1].type,
      type === "JColorGauge" ? 0.5 : "linear",
    ),
  );
}
{
  const raw = "9007199254740993.0001";
  const option = helper.sourceChartOption(
    widget("JColorGauge"),
    model("JColorGauge", [row("相邻大数量程", "", raw)], {
      minimum: "9007199254740993",
      maximum: "9007199254740993.001",
      gauge_position: "0.1",
      gauge_ticks: [
        { position: "0", value: "9007199254740993" },
        { position: "1", value: "9007199254740993.001" },
      ],
      gauge_stops: [
        { position: "1", value: "9007199254740993.001", color: "#38bdf8" },
      ],
    }),
  );
  check(() => assert.equal(option.series[0].data[0].value, 0.1));
  check(() => assert.equal(option.series[0].detail.formatter(), raw));
  check(() =>
    assert.equal(
      option.series[0].axisLabel.formatter(1),
      "9007199254740993.001",
    ),
  );
}

// 编译全部受影响SFC，避免只测辅助函数而漏掉模板及事件表达式。
const files = [
  "source/SourcePresentationWidget.vue",
  "source/SourceChartWidget.vue",
  "source/SourceCalendarWidget.vue",
  "source/SourceHistoryWidget.vue",
  "source/SourceCardsWidget.vue",
  "source/SourceWidgetProperties.vue",
  "ReportProperties.vue",
  "DisplayProperties.vue",
  "TemplateDataBindings.vue",
  "reused/JimuWidgetRenderer.vue",
];
const scripts = new Map();
for (const file of files) {
  const source = await readFile(
    new URL(`../src/modules/reports/${file}`, import.meta.url),
    "utf8",
  );
  const { descriptor, errors } = parse(source, { filename: file });
  check(() => assert.deepEqual(errors, []));
  const compiled = compileScript(descriptor, {
    id: file,
    inlineTemplate: true,
  });
  scripts.set(file, transpile(compiled.content, file.replace(/\.vue$/, ".ts")));
  const template = compileTemplate({
    source: descriptor.template.content,
    filename: file,
    id: file,
    compilerOptions: { bindingMetadata: compiled.bindings },
  });
  check(() => assert.deepEqual(template.errors, []));
}
async function component(file) {
  let script = scripts.get(file);
  script = script.replace(
    /from (["'])vue\1/g,
    `from ${JSON.stringify(import.meta.resolve("vue"))}`,
  );
  script = script.replace(
    /from (["'])\.\.\/sourcePresentation\1/g,
    `from ${JSON.stringify(helperUrl)}`,
  );
  return (await import(moduleUrl(script))).default;
}

// 最小内存宿主执行真实Vue事件与响应式更新，不创建浏览器或页面。
const node = (type, text = "") => ({
  type,
  text,
  props: {},
  children: [],
  parent: null,
});
const renderer = createRenderer({
  createElement: (type) => node(type),
  createText: (text) => node("text", text),
  createComment: (text) => node("comment", text),
  setText: (item, text) => (item.text = text),
  setElementText: (item, text) => {
    item.text = text;
    item.children = [];
  },
  patchProp: (item, key, before, value) => (item.props[key] = value),
  parentNode: (item) => item.parent,
  nextSibling: (item) =>
    item.parent?.children[item.parent.children.indexOf(item) + 1] ?? null,
  insert: (item, parent, anchor = null) => {
    if (item.parent)
      item.parent.children.splice(item.parent.children.indexOf(item), 1);
    const index = anchor ? parent.children.indexOf(anchor) : -1;
    parent.children.splice(index < 0 ? parent.children.length : index, 0, item);
    item.parent = parent;
  },
  remove: (item) => {
    if (item.parent)
      item.parent.children.splice(item.parent.children.indexOf(item), 1);
    item.parent = null;
  },
  insertStaticContent: (content, parent, anchor) => {
    const item = node("static", content);
    parent.children.splice(
      anchor ? parent.children.indexOf(anchor) : parent.children.length,
      0,
      item,
    );
    item.parent = parent;
    return [item, item];
  },
});
function all(root) {
  return [root, ...root.children.flatMap(all)];
}
function textOf(root) {
  return root.text + root.children.map(textOf).join("");
}
function button(root, name) {
  const found = all(root).find(
    (item) =>
      item.type === "button" &&
      (item.props["aria-label"] === name || textOf(item).trim() === name),
  );
  assert.ok(found, `未找到按钮 ${name}`);
  return found;
}
async function click(root, name) {
  const item = button(root, name);
  assert.notEqual(item.props.disabled, true);
  item.props.onClick({ stopPropagation() {} });
  await nextTick();
}

// 用轻量子组件替身验证实际renderer的入口契约，覆盖Boolean缺省值为false时的interactive兼容。
const stubUrl = moduleUrl(
  `import {h} from ${JSON.stringify(import.meta.resolve("vue"))}; export const isBusinessWidgetType=()=>false;export const safeAssetUrl=()=>'';export const presentationTypes=[];export const specialChartTypes=[];export default {props:['widget','preview'],emits:['data-click'],setup(props,{emit}){return()=>h('button',{onClick:()=>emit('data-click',{name:'实际记录'})},String(props.preview));}};`,
);
const rendererScript = scripts
  .get("reused/JimuWidgetRenderer.vue")
  .replace(
    /from\s+["']([^"']+)["']/g,
    (_, specifier) =>
      `from ${JSON.stringify(specifier === "vue" ? import.meta.resolve("vue") : specifier === "../sourcePresentation" ? helperUrl : stubUrl)}`,
  );
const WidgetRenderer = (await import(moduleUrl(rendererScript))).default;
for (const [flags, expected] of [
  [{ interactive: true }, true],
  [{ preview: true }, true],
  [{ interactive: false, preview: false }, false],
]) {
  const root = node("root");
  let drilldowns = 0;
  const app = renderer.createApp({
    render: () =>
      h(WidgetRenderer, {
        widget: widget("JCardScroll"),
        ...flags,
        onDrilldown: () => drilldowns++,
      }),
  });
  app.mount(root);
  check(() => assert.equal(textOf(root), String(expected)));
  await click(root, String(expected));
  check(() => assert.equal(drilldowns, expected ? 1 : 0));
  app.unmount();
}

const Calendar = await component("source/SourceCalendarWidget.vue");
for (const type of helper.sourceCalendarTypes) {
  const root = node("root");
  const values = model(type, leapRows, {
    calendar: { month: "2024-02", timezone: "Asia/Shanghai" },
  });
  const app = renderer.createApp({
    render: () => h(Calendar, { widget: widget(type), model: values }),
  });
  app.mount(root);
  check(() => assert.ok(textOf(root).includes("2024年2月")));
  check(() => assert.ok(textOf(root).includes("闰日维护")));
  await click(root, "下个月");
  check(() => assert.ok(textOf(root).includes("2024年3月")));
  check(() => assert.ok(textOf(root).includes("三月维护")));
  await click(root, "上个月");
  await click(root, "2024-02-28，0项日程");
  check(() => assert.ok(textOf(root).includes("暂无日程")));
  if (type === "JMultiViewCalendar") {
    await click(root, "日程");
    check(() => assert.equal(button(root, "日程").props["aria-pressed"], true));
    check(() => assert.ok(textOf(root).includes("闰日维护")));
  }
  await click(root, "今天");
  check(() => assert.ok(textOf(root).includes(helper.shanghaiDate())));
  app.unmount();
}

const Cards = await component("source/SourceCardsWidget.vue");
const History = await component("source/SourceHistoryWidget.vue");
{
  const root = node("root");
  const rawTime = "2024-02-29T08:30:00+08:00";
  const rows = [
    {
      ...row("已复核维修", "2024-02-29"),
      time: rawTime,
      time_display: "2024-02-29 08:30",
    },
  ];
  const app = renderer.createApp({
    render: () => h(History, { model: model("JDevHistory", rows) }),
  });
  app.mount(root);
  check(() => assert.ok(textOf(root).includes("2024-02-29 08:30")));
  check(() => assert.ok(!textOf(root).includes(rawTime)));
  check(() =>
    assert.equal(
      all(root).find((item) => item.type === "time").props.datetime,
      rawTime,
    ),
  );
  app.unmount();
}
for (const [id, type] of Object.entries(
  helper.sourcePresentationRegistry,
).filter(([, type]) => helper.sourceCardTypes.includes(type))) {
  const root = node("root");
  const w = widget(type, {
    source_component_id: id,
    title: "标题改变仍保留原变体",
    config: { option: { carousel: { page_size: 2, autoplay: false } } },
  });
  const rows = Array.from({ length: 5 }, (_, index) =>
    row(`真实卡片${index + 1}`, "2024-02-29", String(index)),
  );
  const app = renderer.createApp({
    render: () =>
      h(Cards, { widget: w, model: model(type, rows), preview: true }),
  });
  app.mount(root);
  check(() => assert.ok(textOf(root).includes("真实卡片1")));
  check(() => assert.ok(!textOf(root).includes("真实卡片3")));
  await click(root, "下一页卡片");
  check(() => assert.ok(textOf(root).includes("真实卡片3")));
  check(() => assert.ok(!textOf(root).includes("真实卡片1")));
  await click(root, "上一页卡片");
  check(() => assert.ok(textOf(root).includes("真实卡片1")));
  const v = helper.cardVariant(w);
  check(() =>
    assert.equal(v.direction === "vertical", id.includes("竖向+序号")),
  );
  check(() => assert.equal(v.highlight, id.includes("高亮")));
  const classes = all(root).find((item) => item.type === "section").props.class;
  check(() =>
    assert.equal(classes.includes("vertical"), v.direction === "vertical"),
  );
  check(() => assert.equal(classes.includes("highlight"), v.highlight));
  check(() =>
    assert.equal(classes.includes("carousel"), type === "JCardCarousel"),
  );
  app.unmount();
}

// 受控计时器只在内存执行一次回调，验证预览、悬停、暂停、页面隐藏和卸载的实际清理。
const savedGlobals = {
  setInterval: globalThis.setInterval,
  clearInterval: globalThis.clearInterval,
  document: globalThis.document,
  window: globalThis.window,
};
const timers = new Map();
const listeners = new Map();
let motionListener;
const motion = {
  matches: false,
  addEventListener: (_, action) => (motionListener = action),
  removeEventListener: () => (motionListener = undefined),
};
try {
  globalThis.setInterval = (action, duration) => {
    const id = {};
    timers.set(id, { action, duration });
    return id;
  };
  globalThis.clearInterval = (id) => timers.delete(id);
  globalThis.document = {
    visibilityState: "visible",
    addEventListener: (name, action) => listeners.set(name, action),
    removeEventListener: (name) => listeners.delete(name),
  };
  globalThis.window = { matchMedia: () => motion };
  const root = node("root");
  const w = widget("JCardCarousel", {
    config: {
      option: {
        carousel: { page_size: 1, autoplay: true, interval_seconds: 4 },
      },
    },
  });
  const rows = [row("第一张", "", "1"), row("第二张", "", "2")];
  const app = renderer.createApp({
    render: () =>
      h(Cards, {
        widget: w,
        model: model("JCardCarousel", rows),
        preview: true,
      }),
  });
  app.mount(root);
  await nextTick();
  check(() => assert.equal(timers.size, 1));
  check(() => assert.equal([...timers.values()][0].duration, 4000));
  [...timers.values()][0].action();
  await nextTick();
  check(() => assert.ok(textOf(root).includes("第二张")));
  const section = all(root).find((item) => item.type === "section");
  section.props.onMouseenter();
  await nextTick();
  check(() => assert.equal(timers.size, 0));
  section.props.onMouseleave();
  await nextTick();
  check(() => assert.equal(timers.size, 1));
  await click(root, "暂停");
  check(() => assert.equal(timers.size, 0));
  await click(root, "播放");
  check(() => assert.equal(timers.size, 1));
  globalThis.document.visibilityState = "hidden";
  listeners.get("visibilitychange")();
  await nextTick();
  check(() => assert.equal(timers.size, 0));
  globalThis.document.visibilityState = "visible";
  listeners.get("visibilitychange")();
  await nextTick();
  check(() => assert.equal(timers.size, 1));
  motion.matches = true;
  motionListener();
  await nextTick();
  check(() => assert.equal(timers.size, 0));
  app.unmount();
  check(() => assert.equal(listeners.size, 0));
  check(() => assert.equal(motionListener, undefined));
  check(() => assert.equal(timers.size, 0));
} finally {
  globalThis.setInterval = savedGlobals.setInterval;
  globalThis.clearInterval = savedGlobals.clearInterval;
  if (savedGlobals.document === undefined) delete globalThis.document;
  else globalThis.document = savedGlobals.document;
  if (savedGlobals.window === undefined) delete globalThis.window;
  else globalThis.window = savedGlobals.window;
}
console.log(
  JSON.stringify({
    status: "passed",
    checks,
    sfc: files.length,
    business_http: 0,
    queries: 0,
    models: 0,
    browser: 0,
  }),
);
