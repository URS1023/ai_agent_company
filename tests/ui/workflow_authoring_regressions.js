"use strict";
// 运行真实组合式函数和表单逻辑，接口、路由、计时器均替换为内存夹具。
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm"), assert = require("node:assert/strict");
const root = path.resolve(__dirname, "../..");
const ts = require(path.join(root, "apps/analytics/node_modules/typescript"));
const vue = require(path.join(root, "apps/analytics/node_modules/vue"));
const { parseWorkflowJson } = require(path.join(root, "apps/analytics/src/utils/workflowJson.js"));
const read = name => fs.readFileSync(path.join(root, name), "utf8");
const copy = value => JSON.parse(JSON.stringify(value));
const tick = async () => { for (let index = 0; index < 40; index++) await Promise.resolve(); };
function execute(source, context) {
  const code = ts.transpileModule(source.replace(/^import .*$/gm, ""), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
  const scope = { exports: {}, ...vue, Error, RangeError, SyntaxError, AbortController, console, ...context };
  vm.runInNewContext(code, scope); return scope;
}
const empty = () => ({ name: "初稿", description: "", domains: ["general"], status: "draft", graph: { nodes: [], edges: [] } });
function saveFixture(isNew, generate, options = {}) {
  const timers = new Map(), cleanup = [], requests = [], scope = vue.effectScope();
  const id = vue.ref(isNew ? "new" : "existing");
  let timerId = 0, navigations = 0, state;
  const context = execute(read("apps/analytics/src/composables/useWorkflows.ts"), {
    onBeforeUnmount: handler => cleanup.push(handler),
    useRouter: () => ({ replace: async destination => {
      navigations++;
      if (state.dirty.value) await state.flushDraft(false);
      id.value = destination.split('/').at(-1); await vue.nextTick();
    } }),
    ElMessage: { success() {}, info() {}, warning() {} }, ElMessageBox: { confirm: options.confirm || (async () => true) },
    can: () => true, get: async () => ({ data: [], items: [] }), copy, emptyWorkflow: empty,
    workflows: { catalog: async () => ({ data: [] }), get: async identity => ({ data: { ...empty(), id: identity, version: 1 } }),
      save: snapshot => new Promise((resolve, reject) => requests.push({ snapshot: copy(snapshot), resolve, reject })),
      generate: generate || (async () => ({ data: { workflow: { ...empty(), name: 'AI 新草稿', graph: { nodes: [{ id: 'generated', kind: 'start' }], edges: [] } }, generation: { mode: 'model' } } })), ...options.workflows },
    parseWorkflowJson, createClientId: () => "fake-id", ensureReadableWorkflowLayout: graph => graph, openTopLevel: options.openTopLevel || (() => {}),
    setTimeout: callback => { const next = ++timerId; timers.set(next, callback); return next; }, clearTimeout: key => timers.delete(key),
    window: { addEventListener() {}, removeEventListener() {} },
    ...options.context,
  });
  scope.run(() => { state = context.exports.useWorkflows(id); });
  return { state, requests, timers, id, navigations: () => navigations,
    complete(index) { const request = requests[index]; request.resolve({ data: { ...request.snapshot, id: request.snapshot.id || "saved", version: index + 1 } }); },
    cleanup() { cleanup.forEach(handler => handler()); scope.stop(); } };
}

async function main() {
  {
    for (const saveFails of [false, true]) {
      const restores = [];
      const f = saveFixture(false, null, { workflows: { restore: (snapshot, revision) => new Promise(resolve => restores.push({ snapshot: copy(snapshot), revision, resolve })) } }); await tick();
      f.state.updateMeta({ name: '保存中的草稿' }); const saving = f.state.save(); await tick();
      f.state.updateMeta({ description: '保存途中新增编辑' });
      const restoring = f.state.restore(1); await tick();
      assert.equal(restores.length, 0, '恢复必须等待已发出的保存完成');
      if (saveFails) {
        f.requests[0].reject(new Error('保存版本冲突')); assert.equal(await saving, false); await restoring;
        assert.equal(restores.length, 0); assert.equal(f.state.dirty.value, true); assert.match(f.state.error.value, /保存/);
      } else {
        f.complete(0); await tick(); assert.equal(f.requests.length, 2); assert.equal(restores.length, 0);
        f.complete(1); assert.equal(await saving, true); await tick();
        assert.equal(restores.length, 1); assert.equal(restores[0].snapshot.version, 2);
        assert.equal(restores[0].snapshot.description, '保存途中新增编辑'); assert.equal(restores[0].revision, 1);
        restores[0].resolve({ data: { ...empty(), id: 'existing', name: '恢复后的修订', version: 3 } }); await restoring; await tick();
        assert.equal(f.state.flow.value.name, '恢复后的修订'); assert.equal(f.state.flow.value.version, 3);
        assert.equal(f.state.dirty.value, false); assert.equal(f.timers.size, 0); assert.equal(f.requests.length, 2);
      }
      f.cleanup();
    }
    console.log('PASS 恢复修订与单飞保存串行，使用最新版本；保存失败保留草稿并阻止恢复');
  }
  {
    // 确认对话框属于打开它的文档，离开后点击确认不能对新文档发出写操作。
    const cases = {
      publish: state => state.status(true), disable: state => state.status(false),
      restore: state => state.restore(1), remove: state => state.remove(state.flow.value),
      cancel: state => state.cancelRun(), removeNode: state => state.removeNode('same-node'),
      generate: state => { state.dirty.value = true; return state.generateAi({ name: 'A', prompt: '生成流程', resourceIds: [] }); },
    };
    for (const [name, invoke] of Object.entries(cases)) for (const unmount of [false, true]) {
      let confirm; const calls = [];
      const record = operation => async () => { calls.push(operation); return { data: {} }; };
      const f = saveFixture(false, record('generate'), {
        confirm: () => new Promise(resolve => { confirm = resolve; }),
        workflows: Object.fromEntries(['status', 'restore', 'remove', 'cancel'].map(operation => [operation, record(operation)])),
      }); await tick();
      f.state.flow.value.graph.nodes = [{ id: 'same-node', kind: 'text', inputs: {} }];
      f.state.result.value = { id: 'run-A', workflowId: 'existing', status: 'completed' };
      const pending = invoke(f.state); await tick(); assert.equal(typeof confirm, 'function', name);
      if (unmount) f.cleanup(); else { f.id.value = 'flow-B'; await vue.nextTick(); await tick(); f.state.flow.value.graph.nodes = [{ id: 'same-node', kind: 'text', inputs: {} }]; }
      confirm(true); await pending; await tick();
      assert.equal(calls.length, 0, name + ' 离开文档后不能继续派发');
      assert.equal(f.state.flow.value.graph.nodes[0].id, 'same-node', name);
      assert.equal(f.state.error.value, ''); if (!unmount) f.cleanup();
    }
    console.log('PASS 发布/停用/恢复/删除/取消/删除节点/AI确认后切换或卸载，不再派发旧操作');
  }
  {
    const cases = {
      validate: state => state.validate(), publish: state => state.status(true), restore: state => state.restore(1),
      history: state => state.openHistory(), show: state => state.showResult({ id: 'run-A' }),
      run: state => state.run(), resume: state => state.resumeRun({ answer: '继续' }), cancel: state => state.cancelRun(),
      remove: state => state.remove(state.flow.value), export: state => state.exportFlow(),
      import: state => state.importFlow({ size: 1, text: async () => JSON.stringify({ schemaVersion: 1, workflow: empty() }) }),
    };
    for (const [name, invoke] of Object.entries(cases)) for (const mode of ['switch', 'unmount', 'current']) {
      const calls = [], effects = [];
      const request = operation => (...args) => new Promise(resolve => calls.push({ operation, args: copy(args), resolve }));
      const f = saveFixture(false, null, {
        workflows: Object.fromEntries(['validate', 'status', 'restore', 'runs', 'revisions', 'result', 'run', 'resume', 'cancel', 'remove', 'export', 'save'].map(operation => [operation, request(operation)])),
        openTopLevel: url => effects.push(url),
        context: { Blob, URL: { createObjectURL: () => { effects.push('blob'); return 'blob:fixture'; }, revokeObjectURL() {} }, document: { createElement: () => ({ click: () => effects.push('download') }) } },
      }); await tick();
      f.state.result.value = { id: 'run-A', workflowId: 'existing', status: 'completed' };
      const pending = invoke(f.state); await tick(); assert(calls.length > 0, name);
      for (const call of calls) {
        if (['status', 'restore', 'remove', 'run'].includes(call.operation)) assert.equal(call.args[0].id, 'existing', name);
        if (['resume', 'cancel'].includes(call.operation)) assert.equal(call.args[0].id, 'run-A', name);
      }
      if (mode === 'unmount') f.cleanup(); else if (mode === 'switch') { f.id.value = 'flow-B'; await vue.nextTick(); await tick(); }
      // 保留当前界面快照；迟到回执不能改名字、校验、结果、面板、历史或自动保存计时器。
      const stateSnapshot = () => copy({ flow: f.state.flow.value, result: f.state.result.value, history: f.state.history.value, revisions: f.state.revisions.value, resultOpen: f.state.resultOpen.value, historyOpen: f.state.historyOpen.value, dirty: f.state.dirty.value });
      const before = stateSnapshot(), timersBefore = f.timers.size;
      for (const call of calls) call.resolve({ data: ['runs', 'revisions'].includes(call.operation) ? [{ id: 'late-A' }] : { ...empty(), id: 'late-A', name: '迟到A', valid: true, errors: [], status: 'completed' } });
      await pending; await tick();
      if (mode === 'current') {
        if (name === 'validate') assert.equal(f.state.flow.value.validation.valid, true);
        if (['publish', 'restore'].includes(name)) assert.equal(f.state.flow.value.id, 'late-A');
        if (['show', 'run', 'resume', 'cancel'].includes(name)) assert.equal(f.state.result.value.id, 'late-A');
        if (name === 'history') { assert.equal(f.state.history.value[0].id, 'late-A'); assert.equal(f.state.revisions.value[0].id, 'late-A'); }
        if (name === 'import') assert.equal(effects[0], '/workflows/late-A');
        if (name === 'export') assert.deepEqual(effects, ['blob', 'download']);
        if (name === 'remove') assert.equal(f.state.result.value, null, '移除完成后仍可正常重新加载当前列表');
      } else {
        assert.deepEqual(stateSnapshot(), before, name + ' 迟到回执必须隔离');
        assert.equal(f.timers.size, timersBefore, name); assert.equal(effects.length, 0, name);
      }
      assert.equal(f.state.error.value, '', name);
      if (mode !== 'unmount') f.cleanup();
    }
    console.log('PASS 验证/发布/恢复/历史/结果/运行/恢复运行/取消/移除/导入导出迟到回执不污染新文档或卸载状态');
  }
  {
    let finishRead; const f = saveFixture(false); await tick();
    const pending = f.state.importFlow({ size: 1, text: () => new Promise(resolve => { finishRead = resolve; }) }); await tick();
    f.id.value = 'flow-B'; await vue.nextTick(); await tick();
    finishRead(JSON.stringify({ schemaVersion: 1, workflow: empty() })); await pending;
    assert.equal(f.requests.length, 0, '文件读取后文档已切换，不再创建流程'); f.cleanup();
    const refreshes = [];
    const r = saveFixture(false, null, { workflows: { result: () => new Promise(resolve => refreshes.push(resolve)) } }); await tick();
    r.state.result.value = { id: 'run-A', workflowId: 'existing', status: 'completed' };
    const refreshA = r.state.refreshResult(); await tick();
    r.id.value = 'flow-B'; await vue.nextTick(); await tick();
    r.state.result.value = { id: 'run-B', workflowId: 'flow-B', status: 'completed' };
    const refreshB = r.state.refreshResult(); await tick(); assert.equal(refreshes.length, 2);
    refreshes[0]({ data: { id: 'run-A', status: 'completed' } }); await refreshA;
    assert.equal(r.state.resultRefreshing.value, true, 'A的刷新finally不能清除B的请求状态'); assert.equal(r.state.result.value.id, 'run-B');
    refreshes[1]({ data: { id: 'run-B', status: 'completed' } }); await refreshB;
    assert.equal(r.state.resultRefreshing.value, false); r.cleanup();
    console.log('PASS 导入读取途中切换不创建流程，旧运行刷新收尾不清除新文档请求状态');
  }
  {
    // 旧文档请求无论成功、失败或比新文档更晚结束，都不能污染新文档与其保存状态。
    for (const failed of [false, true]) for (const oldFirst of [false, true]) {
      const f = saveFixture(false); await tick();
      f.state.updateMeta({ name: 'A的旧修改' }); const saveA = f.state.save(); await tick();
      f.id.value = 'flow-B'; await vue.nextTick(); await tick();
      f.state.updateMeta({ name: 'B的新修改' }); const saveB = f.state.save(); await tick();
      assert.equal(f.requests.length, 2, 'B的保存不能等待已离开的A');
      assert.equal(f.requests[1].snapshot.id, 'flow-B');
      const finishA = async () => {
        if (failed) f.requests[0].reject(new Error('A的迟到保存失败')); else f.complete(0);
        await tick(); assert.equal(await saveA, false);
      };
      if (oldFirst) { await finishA(); assert.equal(f.state.saving.value, true, 'A的finally不能取消B保存中状态'); }
      f.complete(1); assert.equal(await saveB, true); await tick();
      if (!oldFirst) await finishA();
      assert.equal(f.state.flow.value.id, 'flow-B'); assert.equal(f.state.flow.value.name, 'B的新修改');
      assert.equal(f.state.dirty.value, false); assert.equal(f.state.saving.value, false);
      assert.equal(f.state.saveError.value, ''); assert.equal(f.state.error.value, '');
      assert.equal(f.requests.length, 2); assert.equal(f.timers.size, 0); f.cleanup();
    }
    const f = saveFixture(false); await tick();
    f.state.updateMeta({ name: 'A待保存' }); const saveA = f.state.save(); await tick();
    f.id.value = 'flow-B'; await vue.nextTick(); await tick(); f.state.updateMeta({ name: 'B等待自动保存' });
    f.requests[0].reject(new Error('A失败')); assert.equal(await saveA, false); await tick();
    assert.equal(f.timers.size, 1, 'A失败不能清掉B的自动保存'); assert.equal(f.state.saveError.value, '');
    const timer = [...f.timers.values()][0]; f.timers.clear(); timer(); await tick();
    assert.equal(f.requests[1].snapshot.id, 'flow-B'); f.complete(1); await tick();
    assert.equal(f.state.dirty.value, false); f.cleanup();
    console.log('PASS 跨文档旧保存成功/失败/收尾均隔离，新文档可独立保存且自动保存不被阻断');
  }
  {
    const generations = [];
    const f = saveFixture(false, () => new Promise((resolve, reject) => generations.push({ resolve, reject }))); await tick();
    const first = f.state.generateAi({ name: 'A生成', prompt: '生成设备工作流', resourceIds: [] }); await tick();
    f.id.value = 'flow-B'; await vue.nextTick(); await tick(); assert.equal(f.state.editable.value, true);
    const second = f.state.generateAi({ name: 'B生成', prompt: '生成巡检工作流', resourceIds: [] }); await tick();
    assert.equal(generations.length, 2, 'A的生成不能锁住B');
    generations[0].reject(new Error('A的迟到模型错误')); await first;
    assert.equal(f.state.error.value, ''); assert.equal(f.state.busy.value, true, 'A的finally不能提前释放B的忙碌状态');
    generations[1].resolve({ data: { workflow: { ...empty(), name: 'B生成结果' }, generation: { mode: 'model' } } }); await second;
    assert.equal(f.state.flow.value.id, 'flow-B'); assert.equal(f.state.flow.value.name, 'B生成结果'); assert.equal(f.state.busy.value, false);
    f.cleanup();
    const unmounted = saveFixture(false); await tick(); unmounted.state.updateMeta({ name: '卸载前编辑' });
    const pending = unmounted.state.save(); await tick(); unmounted.cleanup(); unmounted.complete(0);
    assert.equal(await pending, false); assert.equal(unmounted.timers.size, 0); assert.equal(unmounted.requests.length, 1);
    console.log('PASS 跨文档AI失败与busy收尾隔离，卸载后旧保存不更新画布或追加请求');
  }
  {
    let finishGeneration;
    const f = saveFixture(true, () => new Promise(resolve => { finishGeneration = resolve; })); await tick();
    f.state.updateMeta({ name: '新建流程' }); const saving = f.state.save(); await tick();
    const generating = f.state.generateAi({ name: '持久ID后的AI草稿', prompt: '生成设备工作流', resourceIds: [] }); await tick();
    f.complete(0); assert.equal(await saving, true); await tick(); assert.equal(f.id.value, 'saved');
    finishGeneration({ data: { workflow: { ...empty(), name: '持久ID后的AI草稿' }, generation: { mode: 'model' } } });
    await generating; assert.equal(f.state.flow.value.id, 'saved'); assert.equal(f.state.flow.value.name, '持久ID后的AI草稿');
    const nextSave = f.state.save(); await tick(); assert.equal(f.requests[1].snapshot.id, 'saved');
    f.complete(1); assert.equal(await nextSave, true); assert.equal(f.navigations(), 1); f.cleanup();
    console.log('PASS 新建流程真实保存并迁移正式ID后，原生成结果仍归本流程且继续保存');
  }
  {
    for (const mode of ['switch', 'unmount', 'first_save']) {
      let resolveGeneration;
      const f = saveFixture(mode === 'first_save', () => new Promise(resolve => { resolveGeneration = resolve; })); await tick();
      const pending = f.state.generateAi({ name: 'A的生成结果', prompt: '生成设备流程', resourceIds: [] }); await tick();
      if (mode === 'switch') { f.id.value = 'second'; await vue.nextTick(); await tick(); }
      if (mode === 'unmount') f.cleanup();
      if (mode === 'first_save') {
        // 同一新草稿获得持久ID仍属于原文档，允许接收正在生成的结果。
        f.state.flow.value.id = 'saved'; f.id.value = 'saved'; await vue.nextTick();
      }
      resolveGeneration({ data: { workflow: { ...empty(), name: 'A的生成结果' }, generation: { mode: 'model' } } }); await pending; await tick();
      if (mode === 'first_save') { assert.equal(f.state.flow.value.name, 'A的生成结果'); assert.equal(f.timers.size, 1); }
      else { assert.notEqual(f.state.flow.value?.name, 'A的生成结果'); assert.equal(f.timers.size, 0); assert.equal(f.requests.length, 0); }
      if (mode === 'switch') assert.equal(f.state.flow.value.id, 'second');
      if (mode !== 'unmount') f.cleanup();
    }
    console.log('PASS 跨工作流及卸载后的迟到生成不覆盖或保存，同文档首次保存换ID仍接受结果');
  }
  {
    // 同时覆盖无后续操作的自动保存，以及旧保存回执迟到时不得覆盖 AI 新图。
    for (const inFlight of [false, true]) {
      const f = saveFixture(false); await tick();
      let earlier;
      if (inFlight) { f.state.updateMeta({ name: '生成前草稿' }); earlier = f.state.save(); await tick(); }
      await f.state.generateAi({ name: 'AI 新草稿', prompt: '生成设备巡检流程', resourceIds: [] });
      assert.equal(f.state.flow.value.name, 'AI 新草稿'); assert.equal(f.timers.size, 1);
      if (inFlight) { f.complete(0); await tick(); assert.equal(f.state.flow.value.name, 'AI 新草稿'); }
      if (!inFlight) { const timer = [...f.timers.values()][0]; f.timers.clear(); timer(); await tick(); }
      const index = inFlight ? 1 : 0;
      assert.equal(f.requests[index].snapshot.graph.nodes[0].id, 'generated');
      f.complete(index); await tick(); if (earlier) await earlier;
      assert.equal(f.state.dirty.value, false); assert.equal(f.state.flow.value.name, 'AI 新草稿'); f.cleanup();
    }
    console.log('PASS AI 草稿无需额外点击即自动保存，生成前迟到的保存回执不覆盖新画布');
  }
  {
    const props = vue.reactive({ modelValue: { source: 'constant', value: 7 }, fields: [], label: '比较值' });
    const emitted = [], scope = vue.effectScope(); let form;
    const script = read('apps/analytics/src/components/workflows/WorkflowValueOperand.vue').match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
    scope.run(() => { form = execute(script + '\nglobalThis.form = { editValue, draft, inputError };', {
      parseWorkflowJson, defineProps: () => props, defineEmits: () => (event, value) => {
        emitted.push({ event, value }); props.modelValue = value;
      },
    }).form; });
    for (const value of ['9007199254740993', '0.10000000000000000001', '1e999', '']) {
      form.editValue({ target: { value } }); await tick();
      assert.equal(emitted.length, 0); assert.equal(props.modelValue.value, 7);
      assert.equal(form.draft.value, value); assert.match(form.inputError.value, /未应用/);
      props.modelValue = { source: 'constant', value: 7 }; await tick();
      assert.equal(form.draft.value, value); assert.match(form.inputError.value, /未应用/);
    }
    form.editValue({ target: { value: '0.1' } }); await tick();
    assert.equal(props.modelValue.value, 0.1); assert.equal(form.inputError.value, '');
    props.modelValue = { source: 'constant', value: '' }; await tick();
    form.editValue({ target: { value: '0.10000000000000000001' } }); await tick();
    assert.equal(props.modelValue.value, '0.10000000000000000001'); scope.stop();
    console.log('PASS 固定数值拒绝不安全整数、小数及无效输入，原草稿保留，固定文本保持精确值');
  }
  {
    const f = saveFixture(true); await tick();
    f.state.updateMeta({ name: "第一次保存" });
    let finished = false; const save = f.state.save().then(value => { finished = true; return value; });
    await tick(); f.state.updateMeta({ description: "保存期间的新编辑" });
    f.complete(0); await tick();
    assert.equal(f.navigations(), 1); assert.equal(f.requests.length, 2);
    assert.equal(f.requests[1].snapshot.id, "saved"); assert.equal(f.requests[1].snapshot.description, "保存期间的新编辑");
    f.complete(1); await tick();
    assert(finished); assert(await save); assert.equal(f.state.saving.value, false); assert.equal(f.state.dirty.value, false);
    assert.equal(f.id.value, "saved"); assert.equal(f.state.flow.value.description, "保存期间的新编辑");
    assert.equal(f.navigations(), 1); f.cleanup();
    console.log("PASS 新工作流保存期间继续编辑：两次串行保存、一次地址迁移，无守卫死锁或草稿丢失");
  }
  {
    const f = saveFixture(false); await tick(); f.state.updateMeta({ name: "冲突草稿" });
    const save = f.state.save(); await tick(); f.requests[0].reject(new Error("409：版本已改变")); assert.equal(await save, false);
    assert.equal(f.timers.size, 0); assert.equal(f.requests.length, 1); assert(f.state.dirty.value);
    f.state.updateMeta({ description: "错误后仍可编辑" }); await tick();
    assert.equal(f.timers.size, 0); assert.match(f.state.saveError.value, /暂停/);
    const retry = f.state.save(); await tick(); assert.equal(f.requests.length, 2); f.complete(1); assert(await retry);
    assert.equal(f.state.saveError.value, ""); f.cleanup();
    console.log("PASS 保存失败暂停自动请求，保留草稿，新编辑不触发暗中重试，手动保存可恢复");
  }
  {
    const variables = [{ variable: "payload", type: "json_object", label: "参数", required: true }];
    const props = vue.reactive({ modelValue: "{}", variables, disabled: false }); const emitted = [];
    const script = read("apps/analytics/src/components/workflows/WorkflowStartInputForm.vue").match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
    const scope = vue.effectScope(); let form;
    scope.run(() => { form = execute(script + "\nglobalThis.form = { editJson, setValue, complexDraft, fieldErrors, parseError };", {
      parseWorkflowJson, defineProps: () => props, defineEmits: () => (event, value) => { emitted.push({ event, value }); if (event === "update:modelValue") props.modelValue = value; },
    }).form; });
    const unsafe = '{"amount":0.10000000000000000001}';
    form.editJson(variables[0], { target: { value: unsafe } }); await tick();
    assert.equal(props.modelValue, "{}"); assert.equal(form.complexDraft(variables[0]), unsafe);
    assert.match(form.fieldErrors.value.payload, /精度/); assert.equal(emitted.filter(item => item.event === "validity").at(-1).value, false);
    form.editJson(variables[0], { target: { value: '{"amount":"0.10000000000000000001"}' } }); await tick();
    assert.equal(parseWorkflowJson(props.modelValue).payload.amount, "0.10000000000000000001");
    assert.equal(emitted.filter(item => item.event === "validity").at(-1).value, true);
    props.modelValue = '{"payload":{"amount":0.10000000000000000001}}'; await tick();
    form.setValue("other", "新字段"); assert.equal(props.modelValue, '{"payload":{"amount":0.10000000000000000001}}');
    assert.match(form.parseError.value, /精度/); scope.stop();
    console.log("PASS 结构化JSON与完整JSON统一拒绝不安全精度；原草稿保留，字符串精确值可提交");
  }
  {
    const requests = [], scope = vue.effectScope();
    const source = read("apps/analytics/src/composables/usePublishedWorkflow.ts");
    const context = execute(source, { workflows: { published: (id, signal) => new Promise((resolve, reject) => requests.push({ id, signal, resolve, reject })) } });
    const flow = vue.ref({ ...empty(), id: "flow", version: 3, status: "published", publishedRevision: 1,
      graph: { nodes: [{ kind: "start", config: { variables: [{ variable: "batchId" }] } }], edges: [] } });
    let state; scope.run(() => { state = context.exports.usePublishedWorkflow(flow); }); await tick();
    assert.equal(state.ready.value, false); assert.equal(state.contract.value, null);
    const published = version => ({ data: { revision: 1, version, graph: { nodes: [{ kind: "start", config: { variables: [{ variable: "sampleId" }] } }], edges: [] } } });
    requests[0].resolve(published(3)); await tick(); assert(state.ready.value);
    assert.equal(state.contract.value.graph.nodes[0].config.variables[0].variable, "sampleId");
    flow.value.version = 4; await tick(); assert.equal(state.ready.value, false);
    flow.value.version = 5; await tick(); assert(requests[1].signal.aborted);
    requests[1].resolve(published(4)); await tick(); assert.equal(state.contract.value, null);
    requests[2].reject(new Error("无法读取发布修订")); await tick(); assert.match(state.error.value, /发布修订/); assert.equal(state.ready.value, false);
    scope.stop(); console.log("PASS 正式表单读取发布契约；草稿变量不泄漏，旧请求不能覆盖，失败时阻止运行");
  }
  {
    const text = read("apps/web/assets/platform/capabilities.js");
    const source = text.slice(text.indexOf("function skillSpec("), text.indexOf("function skillDialog("));
    const context = execute(source + "\nglobalThis.skillSpec = skillSpec;");
    const previous = { name: "旧名", recommended_tools: ["query"], required_resources: [{ type: "file" }], examples: [{ input: "例" }],
      input_description: "原始输入", output_description: "输出说明", custom_extension: { keep: true } };
    const form = new Map(Object.entries({ name: "新名", description: "说明", instructions: "原步骤", rules: "规则" }));
    const updated = context.skillSpec(form, previous);
    assert.equal(updated.name, "新名");
    for (const key of ["recommended_tools", "required_resources", "examples", "input_description", "output_description", "custom_extension"]) assert.deepEqual(updated[key], previous[key]);
    assert.equal(previous.name, "旧名"); assert.deepEqual(copy(context.skillSpec(form).recommended_tools), []);
    console.log("PASS Skill只修改可见字段，原工具/资源/示例/输入输出与扩展配置完整保留");
  }
  {
    const text = read("apps/web/assets/platform/model-governance.js");
    const source = text.slice(text.indexOf("function runtimeOptions("), text.indexOf("function profileDialog("));
    const context = execute(source + "\nglobalThis.runtimeOptions = runtimeOptions;", { URL });
    const form = new Map(Object.entries({ endpoint: "https://example.invalid/v1", protocol: "openai-completions", thinking: "disabled", reasoning_effort: "off", context_window: "32768", max_output_tokens: "4096" }));
    const options = context.runtimeOptions(form, { input_modalities: ["text"], compat: { supportsStore: false } });
    assert.equal(options.thinking, "disabled"); assert.equal(options.reasoning_effort, "off"); assert.equal(options.context_window, 32768);
    assert.deepEqual(options.compat, { supportsStore: false });
    form.set("reasoning_effort", "high"); assert.throws(() => context.runtimeOptions(form), /关闭思考/);
    form.set("thinking", "enabled"); form.set("context_window", "4096"); assert.throws(() => context.runtimeOptions(form), /上下文窗口/);
    console.log("PASS 模型运行参数正确提交并保留兼容项，冲突思考与预算被拒绝");
  }
  console.log("PASS 16 前端功能回归组；真实HTTP、数据库、模型请求为0");
}
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
