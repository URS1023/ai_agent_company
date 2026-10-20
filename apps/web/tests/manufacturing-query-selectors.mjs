/** 制造查询控件与设备方案纯回归：内存Vue宿主、Mock平台元数据，零实际取数。 */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";
import { parse, compileScript, compileTemplate } from "@vue/compiler-sfc";
import {
  createRenderer,
  h,
  ref,
  shallowRef,
  computed,
  reactive,
  nextTick,
} from "vue";
let checks = 0;
function check(action) {
  action();
  checks++;
}
const root = new URL("../src/modules/", import.meta.url);
function url(text) {
  return `data:text/javascript;base64,${Buffer.from(text).toString("base64")}`;
}
function js(text, fileName) {
  const result = ts.transpileModule(text, {
    fileName,
    reportDiagnostics: true,
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
    },
  });
  check(() => assert.equal(result.diagnostics?.length ?? 0, 0));
  return result.outputText;
}
const vueUrl = import.meta.resolve("vue");
const catalogs = [],
  requests = [];
let response = async () => ({
  items: [{ id: "device-one", name: "循环泵" }],
  total: 1,
});
globalThis.__manufacturingPickerTest = {
  api: {
    async get(path) {
      requests.push(path);
      if (
        !path.startsWith("/devices?") &&
        !path.startsWith("/measurement-points?")
      )
        throw new Error("禁止业务取数");
      return response(path);
    },
  },
  async allRecords(path) {
    assert.equal(path, "/devices");
    return [{ id: "device-one", name: "循环泵" }];
  },
  useOperation() {
    const busy = shallowRef(false);
    return {
      busy,
      fields: ref({}),
      error: shallowRef(""),
      async run(action) {
        busy.value = true;
        try {
          return await action();
        } finally {
          busy.value = false;
        }
      },
    };
  },
  usePreparationQueries() {
    const queryId = shallowRef(""),
      query = ref(null),
      selecting = shallowRef(false),
      loading = shallowRef(false);
    const item = {
      queryId,
      query,
      selecting,
      loading,
      columns: computed(() =>
        selecting.value
          ? []
          : (query.value?.columns ?? query.value?.result?.columns ?? []),
      ),
      loadCount: 0,
      async load() {
        item.loadCount++;
      },
      error: shallowRef(""),
    };
    catalogs.push(item);
    return item;
  },
};
const stubUrl = url(
  "export const {api,useOperation,usePreparationQueries,allRecords}=globalThis.__manufacturingPickerTest; export default {};",
);
async function component(relative) {
  const filename = relative.split("/").at(-1);
  const source = await readFile(new URL(relative, root), "utf8");
  const parsed = parse(source, { filename });
  check(() => assert.deepEqual(parsed.errors, []));
  const script = compileScript(parsed.descriptor, { id: filename });
  const template = compileTemplate({
    source: parsed.descriptor.template.content,
    filename,
    id: filename,
    compilerOptions: { bindingMetadata: script.bindings },
  });
  check(() => assert.deepEqual(template.errors, []));
  let compiled = js(script.content, filename + ".ts")
    .replaceAll('"vue"', JSON.stringify(vueUrl))
    .replaceAll("'vue'", JSON.stringify(vueUrl));
  for (const specifier of [
    "../../api/client",
    "../../composables/useOperation",
    "../data/usePreparationQueries",
    "./workflowOptions",
  ])
    compiled = compiled.replaceAll(
      JSON.stringify(specifier),
      JSON.stringify(stubUrl),
    );
  compiled = compiled.replace(
    /from "[^"\n]+\.vue"/g,
    `from ${JSON.stringify(stubUrl)}`,
  );
  return { source, compiled };
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
async function settle() {
  for (let i = 0; i < 4; i++) {
    await Promise.resolve();
    await nextTick();
  }
}
async function mount(relative, props) {
  const Comp = (await import(url((await component(relative)).compiled)))
    .default;
  let state;
  const events = [];
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
  await settle();
  return { state, events, stop: () => app.unmount() };
}
const props = reactive({ busy: false, errors: {} });
const maintenance = await mount("manufacturing/MaintenanceForm.vue", props);
const form = maintenance.state.form;
const catalog = catalogs.at(-1);
form.device_id = "device-one";
await settle();
check(() => assert.equal(requests.length, 1));
check(() => assert.equal(catalog.loadCount, 0));
maintenance.state.selectPreset({
  device_id: "wrong-device",
  name: "不得覆盖名称",
  datasource_id: "local-source",
  sql: "SELECT reading FROM isolated_table WHERE device = :device",
  value_field: "reading",
  time_field: "observed_at",
  load_field: "load_value",
  period_field: "phase",
  parameters: { device: "pump-02" },
  condition_fields: ["product"],
});
check(() => assert.equal(form.device_id, "device-one"));
check(() => assert.equal(form.name, "循环泵维护分析"));
check(() => assert.deepEqual(maintenance.state.expanded.value, []));
check(() => assert.equal(maintenance.state.ready.value, true));
check(() => assert.ok(maintenance.state.columns.value.includes("reading")));
check(() => assert.ok(maintenance.state.columns.value.includes("product")));
check(() => assert.equal(requests.length, 1));
maintenance.state.submit();
check(() => assert.equal(maintenance.events.at(-1)[0], "submit"));
check(() =>
  assert.deepEqual(maintenance.events.at(-1)[1].parameters, {
    device: "pump-02",
  }),
);
maintenance.state.sourceMode.value = "query";
await settle();
check(() => assert.equal(catalog.loadCount, 1));
check(() => assert.ok(maintenance.state.expanded.value.includes("mapping")));
check(() => assert.equal(form.sql, ""));
catalog.queryId.value = "saved-query";
catalog.query.value = {
  id: "saved-query",
  datasource_id: "history-source",
  sql: "SELECT v,t,l FROM source",
  columns: ["v", "t", "l"],
  parameters: { line: "A" },
};
await settle();
check(() => assert.equal(form.datasource_id, "history-source"));
check(() => assert.equal(form.sql, "SELECT v,t,l FROM source"));
form.value_field = "v";
form.time_field = "t";
form.load_field = "l";
maintenance.state.submit();
check(() =>
  assert.deepEqual(maintenance.events.at(-1)[1].parameters, { line: "A" }),
);
form.device_id = "device-two";
await settle();
check(() => assert.equal(form.datasource_id, ""));
check(() => assert.equal(catalog.queryId.value, ""));
check(() => assert.equal(maintenance.state.ready.value, false));
maintenance.state.expanded.value = [];
props.errors.value_field = "字段无效";
await settle();
check(() => assert.ok(maintenance.state.expanded.value.includes("mapping")));
maintenance.stop();

const point = {
  id: "point-one",
  name: "泵温监测",
  datasource_id: "source-one",
  mapping: { analysis: { sql: "SELECT v FROM demo", value_field: "v" } },
};
response = async () => ({ items: [point], total: 1 });
const pickerProps = reactive({ deviceId: "device-one" });
const picker = await mount(
  "manufacturing/MaintenancePresetPicker.vue",
  pickerProps,
);
check(() => assert.equal(picker.events.length, 1));
check(() => assert.equal(picker.events[0][1].datasource_id, "source-one"));
check(() => assert.equal(picker.state.selected.value, "point-one"));
check(() =>
  assert.ok(
    requests.every(
      (path) =>
        path.startsWith("/devices?") || path.startsWith("/measurement-points?"),
    ),
  ),
);
const deferred = () => {
  let resolve;
  const promise = new Promise((ok) => {
    resolve = ok;
  });
  return { promise, resolve };
};
const old = deferred();
response = async (path) =>
  path.includes("old-device")
    ? old.promise
    : { items: [{ ...point, id: "new-point", name: "新设备方案" }], total: 1 };
pickerProps.deviceId = "old-device";
await settle();
pickerProps.deviceId = "new-device";
await settle();
const beforeLate = picker.events.length;
old.resolve({ items: [{ ...point, id: "old-point" }], total: 1 });
await settle();
check(() => assert.equal(picker.events.length, beforeLate));
check(() => assert.equal(picker.state.selected.value, "new-point"));
picker.stop();

const energy = await mount(
  "manufacturing/EnergyForm.vue",
  reactive({
    initial: { query_id: "energy-history", device_id: "device-one" },
    busy: false,
    errors: {},
  }),
);
const energyCatalog = catalogs.at(-1);
check(() => assert.equal(energyCatalog.queryId.value, "energy-history"));
energyCatalog.selecting.value = true;
energyCatalog.query.value = {
  id: "energy-history",
  result: {
    columns: ["timestamp", "period", "energy", "production", "load"],
    rows: [
      {
        timestamp: "2026-10-01T00:00:00+08:00",
        period: "baseline",
        energy: "10",
        production: "2",
        load: "50",
      },
      {
        timestamp: "2026-10-02T00:00:00+08:00",
        period: "recent",
        energy: "8",
        production: "2",
        load: "50",
      },
    ],
  },
};
energyCatalog.selecting.value = false;
await settle();
check(() => assert.equal(energy.state.form.baseline_value, "baseline"));
check(() => assert.equal(energy.state.form.recent_value, "recent"));
check(() => assert.equal(energy.state.form.energy_field, "energy"));
check(() => assert.equal(energy.state.ready.value, true));
energy.state.submit();
check(() => assert.equal(energy.events.at(-1)[1].query_id, "energy-history"));
energy.stop();

for (const relative of [
  "data/DataQualityWorkspace.vue",
  "manufacturing/InspectionDetail.vue",
  "manufacturing/DeviceDetail.vue",
  "manufacturing/AlertRuleWorkspace.vue",
  "manufacturing/EnergyForm.vue",
]) {
  const result = await component(relative);
  check(() => assert.ok(result.source.includes("PreparationQuerySelect")));
  check(() =>
    assert.equal(/\/queries\?limit=(100|200)/.test(result.source), false),
  );
}
delete globalThis.__manufacturingPickerTest;
process.stdout.write(
  JSON.stringify({
    passed: true,
    checks,
    sfc_count: 7,
    http_requests: 0,
    sql_queries: 0,
    model_calls: 0,
  }) + "\n",
);
