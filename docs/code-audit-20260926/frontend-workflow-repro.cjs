/* 中文说明：只在 Node 内以假 API 与假计时器复现前端状态机，不启动浏览器、服务或数据库。 */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');
const ts = require(path.join(root, 'apps/analytics/node_modules/typescript'));
const vue = require(path.join(root, 'apps/analytics/node_modules/vue'));
const source = fs.readFileSync(path.join(root, 'apps/analytics/src/composables/useWorkflows.ts'), 'utf8').replace(/^import .*$/gm, '');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
const tick = async () => { for (let index = 0; index < 30; index++) await Promise.resolve(); };
const copy = value => JSON.parse(JSON.stringify(value));
const empty = () => ({ name: '初稿', description: '', domains: ['general'], status: 'draft', graph: { nodes: [], edges: [] } });

function fixture(mode) {
  const timers = new Map(), cleanup = [], id = vue.ref(mode === 'deadlock' ? 'new' : 'existing');
  let timerId = 0, saveCalls = 0, completeSave, navigationEntered = false, state;
  const workflows = {
    catalog: async () => ({ data: [] }), get: async () => ({ data: { ...empty(), id: 'existing', version: 1 } }),
    save: async snapshot => {
      saveCalls++;
      if (mode === 'retry') throw new Error('409：版本已改变');
      return new Promise(resolve => { completeSave = () => resolve({ data: { ...copy(snapshot), id: 'saved', version: 1 } }); });
    },
  };
  const context = {
    exports: {}, ...vue, AbortController, console,
    onBeforeUnmount: handler => cleanup.push(handler),
    useRouter: () => ({ replace: async () => {
      navigationEntered = true;
      /* 与 WorkflowWorkbench.vue 的 confirmLeave/onBeforeRouteUpdate 保持相同等待顺序。 */
      if (state.dirty.value) await state.flushDraft(false);
      id.value = 'saved';
    } }),
    ElMessage: { success() {}, info() {}, warning() {} }, ElMessageBox: {},
    can: () => true, get: async () => ({ data: [], items: [] }),
    copy, emptyWorkflow: empty, workflows, parseWorkflowJson: JSON.parse,
    createClientId: () => 'fake-id', ensureReadableWorkflowLayout: graph => graph, openTopLevel() {},
    setTimeout: callback => { const next = ++timerId; timers.set(next, callback); return next; },
    clearTimeout: key => timers.delete(key), window: { addEventListener() {}, removeEventListener() {} },
  };
  vm.runInNewContext(js, context);
  state = context.exports.useWorkflows(id);
  return { state, timers, cleanup: () => cleanup.forEach(handler => handler()), complete: () => completeSave(), calls: () => saveCalls, navigation: () => navigationEntered };
}

(async () => {
  const pending = fixture('deadlock');
  await tick();
  pending.state.updateMeta({ name: '提交中的版本' });
  let finished = false;
  void pending.state.save().then(() => { finished = true; });
  await tick();
  pending.state.updateMeta({ description: '保存尚未返回时继续编辑' });
  pending.complete();
  await tick();
  assert.equal(pending.navigation(), true);
  assert.equal(pending.state.dirty.value, true);
  assert.equal(pending.state.saving.value, true);
  assert.equal(finished, false);
  console.log('CONFIRMED 新建工作流保存返回时仍有新编辑：路由守卫等待保存，保存等待路由，saving 持续为 true。');
  pending.cleanup();

  const retry = fixture('retry');
  await tick();
  retry.state.updateMeta({ name: '冲突版本' });
  assert.equal(await retry.state.save(), false);
  for (let index = 0; index < 2; index++) {
    assert.equal(retry.timers.size, 1);
    const [key, callback] = [...retry.timers][0];
    retry.timers.delete(key); callback(); await tick();
  }
  assert.equal(retry.calls(), 3);
  assert.equal(retry.timers.size, 1);
  console.log('CONFIRMED 保存遭遇持续 409：无需新编辑即自动发起 3 次写请求，仍继续安排下次重试。');
  retry.cleanup();
})().catch(error => { console.error(error); process.exitCode = 1; });
