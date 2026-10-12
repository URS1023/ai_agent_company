"use strict";
// 使用真实 Vue 状态与提交转换，所有来源及接口回执均为内存夹具。
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const root = path.resolve(__dirname, "../..");
const vue = require(path.join(root, "apps/analytics/node_modules/vue"));
const ts = require(path.join(root, "apps/analytics/node_modules/typescript"));
const read = name => fs.readFileSync(path.join(root, name), "utf8");
const copy = value => JSON.parse(JSON.stringify(value));
function execute(name, context) {
  const source = read(name).replace(/^import .*$/gm, "");
  const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
  const scope = { exports: {}, ...vue, Error, AbortController, ...context };
  vm.runInNewContext(code, scope); return scope.exports;
}
function fixture(method) {
  const calls = [], cleanup = [], scope = vue.effectScope();
  const currentSource = { id: "source-A", name: "本轮内存来源", version: 1, type: "http", method, enabled: true, allowAutomatic: true, bodies: [{}] };
  const rule = { id: "rule-A", name: "测试规则", version: 1 };
  const revision = { deviceId: "device-A", rules: [{ expression: { field: "temperature" } }] };
  const api = execute("apps/analytics/src/api/alertWorkflows.ts", {
    post: async (url, payload) => { calls.push({ url, payload: copy(payload) }); return { data: { workflow: { id: "workflow-A" } } }; },
    createClientId: () => "fixture-key",
  });
  const exported = execute("apps/analytics/src/composables/useAlertWorkflowWizard.ts", {
    onBeforeUnmount: callback => cleanup.push(callback), can: () => true, createClientId: () => "fixture-key",
    ...api,
    alerts: {
      revisions: async () => ({ data: [{ revision: 1, snapshot: revision }, { revision: 2, snapshot: revision }] }),
      get: async () => ({ data: { id: "device-A", datasourceId: currentSource.id } }),
      list: async () => ({ data: [{ id: "point-A", fieldName: "temperature", status: "active" }] }),
    },
    get: async url => ({ data: url.includes("collection") ? [copy(currentSource)] : [] }),
  });
  let state; scope.run(() => { state = exported.useAlertWorkflowWizard(rule); });
  return { state, calls, currentSource, close() { cleanup.forEach(callback => callback()); scope.stop(); } };
}

async function main() {
  for (const method of ["GET", "POST"]) {
    const f = fixture(method); await f.state.load(); f.state.selectRequiredPoints();
    assert.equal(f.state.form.value.confirmHttpWrites, false);
    assert.equal(f.state.httpWritesRequired.value, method !== "GET");
    if (method === "POST") {
      assert.equal(f.state.canSubmit.value, false); assert.equal(await f.state.submit(), null); assert.equal(f.calls.length, 0);
      f.state.form.value.confirmHttpWrites = true;
      // 同一绑定的普通表单修改不应撤销用户刚刚明确作出的许可。
      f.state.togglePoint("point-A", false); f.state.togglePoint("point-A", true);
      assert.equal(f.state.form.value.confirmHttpWrites, true);
    }
    assert.equal(f.state.canSubmit.value, true); await f.state.submit();
    assert.equal(f.calls.length, 1); assert.equal(f.calls[0].url, "/api/v1/alerts/workflows");
    assert.equal(f.calls[0].payload.confirmHttpWrites, method === "POST");
    assert.equal(f.calls[0].payload.writeback, null); assert.equal(f.calls[0].payload.schedule, null);
    f.close();
  }
  console.log("PASS GET无需确认，POST默认阻断且显式许可单独提交，不授权数据库写回");
  {
    const f = fixture("POST"); await f.state.load();
    for (const change of [
      () => { f.state.source.value = { ...f.state.source.value, id: "source-B" }; },
      () => { f.state.source.value = { ...f.state.source.value, version: 2 }; },
      () => { f.state.source.value = { ...f.state.source.value, method: "GET" }; },
      () => { f.state.form.value.source.datasourceId = "source-C"; },
      () => { f.state.form.value.source.datasourceVersion = 3; },
    ]) {
      f.state.form.value.confirmHttpWrites = true; change();
      assert.equal(f.state.form.value.confirmHttpWrites, false, "绑定变化时必须同步清除旧确认");
    }
    f.state.form.value.confirmHttpWrites = true; await f.state.chooseRevision(2);
    assert.equal(f.state.form.value.confirmHttpWrites, false); assert.equal(f.state.form.value.pointIds.length, 0);
    f.close();
  }
  console.log("PASS 来源、版本、方法或策略修订改变后重新确认，旧许可不会被静默沿用");
  console.log("PASS 2 组预警HTTP确认回归；真实HTTP、数据库、模型请求均为0");
}
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
