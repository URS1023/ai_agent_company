"use strict";
// 编译真实组件并检查渲染语义与键盘事件；不构建产品入口、不访问接口或数据库。
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const root = path.resolve(__dirname, '../..');
const dependency = name => require(path.join(root, 'apps/analytics/node_modules', name));
const vue = dependency('vue'), ts = dependency('typescript'), compiler = dependency('@vue/compiler-sfc');
const postcss = dependency('postcss');
compiler.registerTS(() => ts);
// 使用 Vue 的真实挂载/渲染上下文；内存宿主仅替代 DOM，避免把指令警告误当成组件缺陷。
const hostNode = (tag = '') => ({ tagName: tag.toUpperCase(), children: [], parent: null, options: [], addEventListener() {}, removeEventListener() {} });
const renderer = vue.createRenderer({
  createElement: hostNode, createText: text => ({ ...hostNode(), text }), createComment: text => ({ ...hostNode(), text }),
  setText: (node, text) => { node.text = text; }, setElementText: (node, text) => { node.children = []; node.text = text; },
  parentNode: node => node.parent, nextSibling: node => node.parent?.children[node.parent.children.indexOf(node) + 1] || null,
  patchProp: (node, name, _before, value) => { node[name] = value; },
  insert: (node, parent, anchor = null) => {
    if (node.parent) node.parent.children.splice(node.parent.children.indexOf(node), 1);
    node.parent = parent; const index = parent.children.indexOf(anchor);
    parent.children.splice(index < 0 ? parent.children.length : index, 0, node);
  },
  remove: node => { if (node.parent) node.parent.children.splice(node.parent.children.indexOf(node), 1); node.parent = null; },
});
const stylesheet = postcss.parse(['workflows.css', 'workflow-node-contracts.css']
  .map(name => fs.readFileSync(path.join(root, 'apps/analytics/src/styles', name), 'utf8')).join('\n'));
function declaration(selector, property) {
  let value;
  stylesheet.walkRules(rule => {
    if (rule.selectors.includes(selector)) rule.walkDecls(property, item => { value = item.value; });
  });
  assert.notEqual(value, undefined, `${selector} 缺少 ${property}`);
  return value;
}
function fixture(name, props, imports = {}) {
  const filename = path.join(root, 'apps/analytics/src/components/workflows', name);
  const { descriptor } = compiler.parse(fs.readFileSync(filename, 'utf8'), { filename });
  const compiled = compiler.compileScript(descriptor, { id: 'workflow-a11y-test', inlineTemplate: true,
    fs: { fileExists: file => fs.existsSync(file) && fs.statSync(file).isFile(), readFile: file => fs.readFileSync(file, 'utf8') } });
  const code = ts.transpileModule(compiled.content, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
  const exports = {}, emitted = [];
  vm.runInNewContext(code, { exports, require: name => {
    if (name === 'vue') return vue;
    if (name in imports) return imports[name];
    if (name.endsWith('.css')) return {};
    if (name.endsWith('.vue')) return { default: { render: () => null } };
    throw new Error(`未登记依赖：${name}`);
  } });
  let vnode;
  const app = renderer.createApp({ setup() {
    const render = exports.default.setup(props, { expose() {}, emit: (name, ...args) => emitted.push({ name, args }) });
    return () => { vnode = render(props, []); return vnode; };
  } });
  app.mount(hostNode('root'));
  return { render: () => { app._instance.update(); return vnode; }, emitted, close: () => app.unmount() };
}
function descendants(vnode) {
  if (!vnode || typeof vnode !== 'object') return [];
  return [vnode, ...(Array.isArray(vnode.children) ? vnode.children.flatMap(descendants) : [])];
}
function nodeFixture() {
  const viewport = vue.ref({ x: 0, y: 0, zoom: 1 });
  const props = vue.reactive({ id: 'node-1', selected: true, connectable: false,
    data: { node: { id: 'node-1', kind: 'text', title: '文本处理', config: {} }, issues: [], disabled: false, connectedSources: [], connectedTargets: [] } });
  return { viewport, ...fixture('DifyWorkflowNode.vue', props, {
    '@vue-flow/core': { Handle: { render: () => null }, Position: { Top: 'top', Right: 'right', Bottom: 'bottom', Left: 'left' }, useVueFlow: () => ({ viewport }) },
    '../../api/workflows': { flowStatus: {} },
  }) };
}
test('节点分组保留自身键盘移动，子按钮与菜单使用各自的原生键盘行为', () => {
  const f = nodeFixture();
  try {
    const node = f.render();
    assert.equal(node.props.role, 'group'); assert.equal(node.props.tabindex, '0');
    const target = {}, key = (value, own = true, shiftKey = false) => {
      const event = { key: value, target: own ? target : {}, currentTarget: target, shiftKey,
        prevented: false, stopped: false, preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; } };
      node.props.onKeydown(event); return event;
    };
    assert(key('Enter').prevented); assert.equal(f.emitted.at(-1).name, 'select');
    assert(key('ArrowRight').prevented); assert.equal(f.emitted.at(-1).args[0].x, 20);
    key('ArrowUp', true, true); assert.equal(f.emitted.at(-1).args[0].y, -5);
    const count = f.emitted.length;
    for (const name of ['Enter', ' ', 'ArrowRight']) assert.equal(key(name, false).prevented, false);
    assert.equal(f.emitted.length, count);
    const run = descendants(node).find(item => item.type === 'button' && item.props?.['aria-label'] === '运行此节点');
    run.props.onClick(); assert.equal(f.emitted.at(-1).name, 'inspectRun');
    assert(descendants(node).some(item => item.type === 'summary' && item.props?.['aria-label'] === '节点操作'));
  } finally { f.close(); }
});
test('最小到最大画布缩放均保留至少24像素操作目标，并以底边固定避免覆盖节点', () => {
  const f = nodeFixture();
  try {
    for (const zoom of [.25, .5, .95, 1, 2]) {
      f.viewport.value.zoom = zoom;
      const toolbar = descendants(f.render()).find(item => item.props?.class === 'wf-node-tools nodrag nopan nowheel');
      const scale = Number(toolbar.props.style.transform.match(/^scale\((.+)\)$/)[1]);
      for (const selector of ['.workflow-ui .wf-node-tools button', '.workflow-ui .wf-node-tools summary']) {
        for (const axis of ['width', 'height']) assert(parseFloat(declaration(selector, axis)) * scale * zoom >= 24, `${zoom} ${selector} ${axis}`);
      }
    }
    assert.equal(declaration('.wf-node-tools', 'transform-origin'), 'bottom right');
    assert.equal(declaration('.wf-node-tools', 'bottom'), 'calc(100% + 4px)');
    assert.equal(declaration('.wf-node:focus-within>.wf-node-tools', 'visibility'), 'visible');
  } finally { f.close(); }
});
test('结果表和原始输出提供命名的可聚焦滚动区域及可见焦点', () => {
  const f = fixture('WorkflowRunResult.vue', vue.reactive({ busy: false, refreshing: false, compact: false,
    run: { id: 'run-1', status: 'completed', result: { outputs: { node: [{ temperature: 25 }] }, nodes: { node: { nodeId: 'node', status: 'completed', output: [{ temperature: 25 }] } } } },
  }), { '../../api/workflows': { flowStatus: { completed: '已完成' } }, '../../api/collection': { jsonCell: value => JSON.stringify(value) }, '../../api/request': { can: () => false } });
  try {
    const nodes = descendants(f.render());
    const areas = [nodes.find(item => item.props?.class === 'wf-table-wrap'), nodes.find(item => item.type === 'pre')];
    for (const area of areas) {
      assert(area); assert.equal(area.props.tabindex, '0'); assert.equal(area.props.role, 'region'); assert(area.props['aria-label']);
    }
    for (const selector of ['.wf-result .wf-table-wrap:focus-visible', '.wf-result pre:focus-visible']) assert.match(declaration(selector, 'outline'), /^2px solid /);
  } finally { f.close(); }
});
test('异常处理下拉具备可访问名称，选择失败分支仍提交原节点配置', () => {
  const node = { id: 'node-1', title: '文本处理', kind: 'text', config: { template: '原始内容' }, errorStrategy: 'none' };
  const f = fixture('WorkflowNodeForm.vue', vue.reactive({ node, nodes: [node], edges: [], sources: [],
    resources: { knowledgeBases: [], bots: [], tools: [], workflows: [], models: [] },
    disabled: false, fields: [], catalog: [], errors: [], validationKnown: false, initialTab: 'settings',
  }), { '../../api/workflows': { copy: value => JSON.parse(JSON.stringify(value)) },
    '../../utils/workflowJson.js': { parseWorkflowJson: JSON.parse }, '../../utils/randomId': { createClientId: () => 'fixture' } });
  try {
    const panel = descendants(f.render()).find(item => item.props?.class === 'wf-node-policy-panel wf-error-strategy');
    const control = descendants(panel).find(item => item.type === 'select');
    assert(control); assert.equal(control.props['aria-label'], '异常处理方式');
    assert.equal(control.props.value, 'none');
    control.props.onChange({ target: { value: 'fail-branch' } });
    const change = f.emitted.at(-1);
    assert.equal(change.name, 'errorStrategy'); assert.equal(change.args[0].id, node.id);
    assert.equal(change.args[0].errorStrategy, 'fail-branch'); assert.equal(change.args[0].config.template, '原始内容');
  } finally { f.close(); }
});
test('节点说明、下一步提示与检查通过说明在浅色背景满足普通文字对比度', () => {
  const luminance = color => color.match(/[a-f\d]{2}/gi).map(value => parseInt(value, 16) / 255)
    .map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4)
    .reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
  for (const selector of ['.wf-node-heading>div>small', '.wf-panel-title small', '.wf-checklist-empty', '.wf-next-step>header small']) {
    const foreground = luminance(declaration(selector, 'color'));
    for (const background of ['#ffffff', '#fcfcfd', '#f9fafb']) assert((luminance(background) + .05) / (foreground + .05) >= 4.5, selector);
  }
});
