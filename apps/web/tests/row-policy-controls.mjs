/** 行权限控件/提交会话纯回归：只用Vue内存宿主和Mock API，不启动浏览器/HTTP/数据库。 */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";
import { parse, compileScript, compileTemplate } from "@vue/compiler-sfc";
import { createRenderer, h, nextTick, shallowRef, ref } from "vue";

let checks = 0;
function check(action) {
  action();
  checks++;
}
function moduleUrl(source) {
  return `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
}
function transpile(source, name) {
  const result = ts.transpileModule(source, {
    fileName: name,
    reportDiagnostics: true,
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
    },
  });
  check(() => assert.equal(result.diagnostics?.length, 0));
  return result.outputText;
}
const helperSource = await readFile(
  new URL("../src/modules/data/rowPolicy.ts", import.meta.url),
  "utf8",
);
const helperUrl = moduleUrl(transpile(helperSource, "rowPolicy.ts"));
const policy = await import(helperUrl);
const columns = [
  { name: "owner_id", type: "TEXT" },
  { name: "temperature", type: "DECIMAL" },
  { name: "enabled", type: "BOOLEAN" },
];
const subjects = {
  roles: [{ id: "role-one", name: "质检员" }],
  users: [{ id: "user-one", name: "张工" }],
};
const draft = () => ({ mode: "rules", rules: [policy.newRule(columns[0])] });

const none = policy.policyPayload({ mode: "rules", rules: [] });
check(() => assert.deepEqual(none, { mode: "rules", rules: [] }));
check(() =>
  assert.deepEqual(
    policy.policyPayload({ mode: "all", rules: [policy.newRule()] }),
    { mode: "all", rules: [] },
  ),
);
const numeric = policy.newCondition(columns[1]);
for (const value of [
  "0",
  "-2.5000",
  "12.3400",
  "1e3",
  "0.1",
  "9007199254740992",
]) {
  numeric.value_text = value;
  check(() => assert.equal(policy.literalValue(numeric), Number(value)));
}
for (const value of [
  "",
  "NaN",
  "Infinity",
  "1e309",
  "9007199254740993",
  "0.123456789123456789",
]) {
  numeric.value_text = value;
  check(() => assert.throws(() => policy.literalValue(numeric)));
}
const text = policy.newCondition(columns[0]);
text.value_text = "  原值含空格  ";
check(() => assert.equal(policy.literalValue(text), "  原值含空格  "));
text.value_text = "";
check(() => assert.equal(policy.literalValue(text), ""));
numeric.operator = "in";
numeric.values_text = ["1", "2.00", "-3"];
check(() => assert.deepEqual(policy.literalValue(numeric), [1, 2, -3]));
numeric.values_text = Array.from({ length: 101 }, () => "1");
check(() => assert.throws(() => policy.literalValue(numeric)));
const boolean = policy.newCondition(columns[2]);
check(() => assert.throws(() => policy.literalValue(boolean)));
boolean.value_boolean = false;
check(() => assert.equal(policy.literalValue(boolean), false));
boolean.operator = "in";
boolean.values_boolean = [true, false];
check(() => assert.deepEqual(policy.literalValue(boolean), [true, false]));
check(() =>
  assert.ok(!policy.operatorsFor(boolean).some((item) => item.value === "gt")),
);

for (const valueSource of ["current_user_id", "current_workspace_id"]) {
  for (const operator of ["eq", "ne", "in", "not_in"]) {
    const form = draft();
    Object.assign(form.rules[0].conditions[0], {
      value_source: valueSource,
      operator,
      value_text: "旧值不可提交",
    });
    const payload = policy.policyPayload(form);
    check(() =>
      assert.deepEqual(payload.rules[0].conditions[0], {
        field: "owner_id",
        operator,
        value_source: valueSource,
      }),
    );
    check(() =>
      assert.deepEqual(policy.policyErrors(form, columns, subjects), {}),
    );
  }
}
for (const operator of ["is_null", "not_null"]) {
  const form = draft();
  Object.assign(form.rules[0].conditions[0], {
    operator,
    value_source: "current_user_id",
    value_text: "旧值",
  });
  check(() =>
    assert.deepEqual(policy.policyPayload(form).rules[0].conditions[0], {
      field: "owner_id",
      operator,
      value_source: "literal",
    }),
  );
}
const roleDraft = draft();
roleDraft.rules[0].subject_type = "role";
check(() =>
  assert.ok(
    policy.policyErrors(roleDraft, columns, subjects)["rules.0.subject_ids"],
  ),
);
roleDraft.rules[0].subject_ids = ["role-one"];
check(() =>
  assert.deepEqual(policy.policyErrors(roleDraft, columns, subjects), {}),
);
roleDraft.rules[0].subject_ids = ["unknown-role"];
check(() =>
  assert.ok(
    policy.policyErrors(roleDraft, columns, subjects)["rules.0.subject_ids"],
  ),
);
roleDraft.rules[0].conditions = [];
check(() =>
  assert.ok(
    policy.policyErrors(roleDraft, columns, subjects)["rules.0.conditions"],
  ),
);
const invalid = draft();
invalid.rules[0].conditions[0].field = "other_table_field";
check(() =>
  assert.ok(
    policy.policyErrors(invalid, columns, subjects)[
      "rules.0.conditions.0.field"
    ],
  ),
);
const dto = {
  id: "policy-one",
  datasource_id: "source-one",
  table_name: "assets",
  revision: 3,
  mode: "rules",
  rules: [
    {
      subject_type: "user",
      subject_ids: ["user-one"],
      conditions: [
        {
          field: "enabled",
          operator: "eq",
          value_source: "literal",
          value: false,
        },
        {
          field: "owner_id",
          operator: "in",
          value_source: "literal",
          value: ["甲", "乙"],
        },
        {
          field: "temperature",
          operator: "gte",
          value_source: "literal",
          value: 12.34,
        },
      ],
    },
  ],
};
check(() =>
  assert.deepEqual(policy.policyPayload(policy.policyDraft(dto, columns)), {
    mode: dto.mode,
    rules: dto.rules,
  }),
);
check(() =>
  assert.equal(
    policy.policyFingerprint(policy.policyDraft(dto, columns)),
    policy.policyFingerprint(policy.policyDraft(dto, columns)),
  ),
);

const host = createRenderer({
  createElement: (type) => ({ type, children: [], props: {}, parent: null }),
  createText: (text) => ({ text }),
  createComment: (text) => ({ text }),
  setText: (node, text) => {
    node.text = text;
  },
  setElementText: (node, text) => {
    node.text = text;
  },
  patchProp: (node, key, _old, value) => {
    node.props[key] = value;
  },
  parentNode: (node) => node.parent,
  nextSibling: () => null,
  insert: (child, parent) => {
    child.parent = parent;
    parent.children.push(child);
  },
  remove: () => {},
});
const state = {
  requests: [],
  confirmations: [],
  accept: true,
  policies: [],
  rejectSave: false,
  rejectLoad: false,
};
globalThis.__rowPolicyPureTest = {
  api: {
    async get(path) {
      state.requests.push({ method: "GET", path });
      if (state.rejectLoad && path.endsWith("row-policies"))
        throw new Error("权限配置读取失败");
      return path.endsWith("row-policy-subjects")
        ? structuredClone(subjects)
        : {
            items: structuredClone(state.policies),
            total: state.policies.length,
          };
    },
    async post(path, body) {
      state.requests.push({
        method: "POST",
        path,
        body: structuredClone(body),
      });
      return {
        id: "policy-new",
        datasource_id: "source-one",
        revision: 1,
        ...body,
      };
    },
    async patch(path, body) {
      state.requests.push({
        method: "PATCH",
        path,
        body: structuredClone(body),
      });
      if (state.rejectSave)
        throw Object.assign(new Error("字段已更新"), {
          fields: { path: "rules.0.conditions.0.field" },
        });
      return { ...dto, ...body, revision: body.revision + 1 };
    },
  },
  operation() {
    const busy = shallowRef(false),
      error = shallowRef(""),
      fields = ref({});
    return {
      busy,
      error,
      fields,
      async run(action) {
        if (busy.value) return;
        busy.value = true;
        error.value = "";
        fields.value = {};
        try {
          return await action();
        } catch (failure) {
          error.value = failure.message;
          fields.value = failure.fields ?? {};
        } finally {
          busy.value = false;
        }
      },
    };
  },
  async confirm(message) {
    state.confirmations.push(message);
    if (!state.accept) throw "cancel";
  },
};
const vueUrl = import.meta.resolve("vue");
const stubUrl = moduleUrl(
  `export const api=globalThis.__rowPolicyPureTest.api; export const useOperation=globalThis.__rowPolicyPureTest.operation; export const ElMessageBox={confirm:globalThis.__rowPolicyPureTest.confirm};`,
);
let composable = transpile(
  await readFile(
    new URL("../src/modules/data/useRowPolicyEditor.ts", import.meta.url),
    "utf8",
  ),
  "useRowPolicyEditor.ts",
);
composable = composable
  .replaceAll('"vue"', JSON.stringify(vueUrl))
  .replaceAll('"element-plus"', JSON.stringify(stubUrl))
  .replaceAll('"../../api/client"', JSON.stringify(stubUrl))
  .replaceAll('"../../composables/useOperation"', JSON.stringify(stubUrl))
  .replaceAll('"./rowPolicy"', JSON.stringify(helperUrl));
const { useRowPolicyEditor } = await import(moduleUrl(composable));
const tables = [
  { name: "assets", columns },
  { name: "orders", columns },
];
async function flush() {
  for (let index = 0; index < 4; index++) {
    await Promise.resolve();
    await nextTick();
  }
}
async function mount(policies, rejectLoad = false) {
  state.policies = policies;
  state.requests = [];
  state.confirmations = [];
  state.accept = true;
  state.rejectSave = false;
  state.rejectLoad = rejectLoad;
  let editor;
  const root = { children: [] };
  const app = host.createApp({
    setup() {
      editor = useRowPolicyEditor("source-one", () => tables);
      return () => h("div");
    },
  });
  app.mount(root);
  await flush();
  return { editor, app };
}
let mounted = await mount([]);
check(() => assert.equal(mounted.editor.ready.value, true));
check(() => assert.equal(mounted.editor.canSave.value, false));
check(() =>
  assert.deepEqual(
    state.requests.map((item) => item.path),
    [
      "/datasources/source-one/row-policies",
      "/datasources/source-one/row-policy-subjects",
    ],
  ),
);
mounted.editor.setScope("rules");
mounted.editor.draft.rules[0].subject_type = "role";
mounted.editor.draft.rules[0].subject_ids = ["role-one"];
mounted.editor.draft.rules[0].conditions[0].value_source = "current_user_id";
check(() => assert.equal(mounted.editor.canSave.value, true));
await mounted.editor.save();
await flush();
check(() =>
  assert.deepEqual(state.requests.at(-1), {
    method: "POST",
    path: "/datasources/source-one/row-policies",
    body: {
      table_name: "assets",
      mode: "rules",
      rules: [
        {
          subject_type: "role",
          subject_ids: ["role-one"],
          conditions: [
            {
              field: "owner_id",
              operator: "eq",
              value_source: "current_user_id",
            },
          ],
        },
      ],
    },
  }),
);
check(() => assert.equal(mounted.editor.dirty.value, false));
mounted.app.unmount();

mounted = await mount([], true);
check(() => assert.equal(mounted.editor.ready.value, false));
check(() => assert.equal(mounted.editor.canSave.value, false));
check(() =>
  assert.equal(mounted.editor.loading.error.value, "权限配置读取失败"),
);
mounted.editor.setScope("none");
await mounted.editor.save();
check(() =>
  assert.equal(
    state.requests.filter(
      (item) => item.method === "POST" || item.method === "PATCH",
    ).length,
    0,
  ),
);
mounted.app.unmount();

mounted = await mount([dto]);
mounted.editor.setScope("all");
state.accept = false;
await mounted.editor.save();
check(() =>
  assert.equal(
    state.requests.filter((item) => item.method === "PATCH").length,
    0,
  ),
);
state.accept = true;
await mounted.editor.save();
await flush();
check(() =>
  assert.deepEqual(state.requests.at(-1), {
    method: "PATCH",
    path: "/row-policies/policy-one",
    body: { mode: "all", rules: [], revision: 3 },
  }),
);
check(() => assert.equal(mounted.editor.dirty.value, false));
mounted.editor.setScope("rules");
state.accept = false;
await mounted.editor.selectTable("orders");
check(() => assert.equal(mounted.editor.table.value, "assets"));
state.accept = true;
await mounted.editor.selectTable("orders");
check(() => assert.equal(mounted.editor.table.value, "orders"));
check(() => assert.equal(mounted.editor.dirty.value, false));
mounted.app.unmount();

mounted = await mount([dto]);
mounted.editor.draft.rules[0].conditions[2].value_text = "13";
await flush();
state.rejectSave = true;
await mounted.editor.save();
check(() =>
  assert.equal(
    mounted.editor.errors.value["rules.0.conditions.0.field"],
    "字段已更新",
  ),
);
check(() => assert.equal(mounted.editor.dirty.value, true));
check(() => assert.equal(mounted.editor.canSave.value, false));
check(() =>
  assert.equal(
    state.requests.filter((item) => item.method === "PATCH").length,
    1,
  ),
);
mounted.app.unmount();

for (const name of [
  "DataPermissionWorkspace",
  "RowPolicyPanel",
  "RowPolicyRuleForm",
  "RowPolicyConditionForm",
]) {
  const file = new URL(`../src/modules/data/${name}.vue`, import.meta.url);
  const source = await readFile(file, "utf8");
  const { descriptor, errors } = parse(source, { filename: name + ".vue" });
  check(() => assert.equal(errors.length, 0));
  const script = compileScript(descriptor, { id: name });
  check(() => assert.ok(script.content));
  const template = compileTemplate({
    source: descriptor.template.content,
    filename: name + ".vue",
    id: name,
    compilerOptions: { bindingMetadata: script.bindings },
  });
  check(() => assert.equal(template.errors.length, 0));
}
delete globalThis.__rowPolicyPureTest;
console.log(
  JSON.stringify({
    passed: true,
    checks,
    sfc_compiled: 4,
    http_requests: 0,
    sql_queries: 0,
    models: 0,
  }),
);
