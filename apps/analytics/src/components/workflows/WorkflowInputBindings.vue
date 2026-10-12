<script setup lang="ts">
/**
 * WorkflowInputBindings —— 结束/输出节点的结果引用绑定（选择上游节点的哪个字段作为输出）。
 *
 * 位置：WorkflowNodeForm 内 kind === 'end' 或需要输出映射的节点。
 * 依赖接口：`api/workflows` 的 FlowNode / FlowEdge / NodeReference（{ nodeId, path }）。
 * 关键约束：
 * - 只允许引用**祖先节点**（沿入边反向遍历），不允许引用自身或下游，否则会形成循环引用；
 * - 遍历时跳过已访问节点，避免图中存在环时死循环；
 * - path 使用 JSONPath 风格（默认 `$`），由用户手填或从候选路径选择，后端按此取值；
 * - end 节点默认输出名为 result，其他节点为 input，仅作默认值，用户可改。
 */
import { computed, shallowRef, watch } from 'vue'
import type { FlowNode, FlowEdge, NodeReference } from '../../api/workflows'

const props = defineProps<{ node: FlowNode; nodes: FlowNode[]; edges: FlowEdge[]; disabled: boolean }>()
const emit = defineEmits<{ update: [inputs: Record<string, NodeReference>] }>()
const isOutput = computed(() => props.node.kind === 'end')
const bindingLabel = computed(() => isOutput.value ? '输出' : '输入')
const open = shallowRef(false)
const keyword = shallowRef('')
const inputName = shallowRef(isOutput.value ? 'result' : 'input')
const customNode = shallowRef('')
const customPath = shallowRef('$')
const error = shallowRef('')
/**
 * 可引用的祖先节点集合：沿入边反向遍历，排除自身与已访问节点（防环），
 * 保证只能绑定上游输出，不会出现循环引用。
 */
const ancestors = computed(() => {
  const ids = new Set<string>()
  const pending = [props.node.id]
  while (pending.length) {
    const target = pending.pop()
    for (const edge of props.edges.filter(item => item.target === target)) {
      if (edge.source === props.node.id || ids.has(edge.source)) continue
      ids.add(edge.source)
      pending.push(edge.source)
    }
  }
  return props.nodes.filter(node => ids.has(node.id))
})
const known: Record<string, [string, string][]> = {
  'document-extractor': [['text', 'String']],
  'list-operator': [['result', 'Array'], ['first_record', 'Any'], ['last_record', 'Any'], ['count', 'Number']],
  'http-request': [['data', 'Array'], ['success', 'Boolean']],
  'database-query': [['data', 'Array'], ['totalCount', 'Number']],
  'report-analysis': [['data', 'Array'], ['totalCount', 'Number']],
  'jsonpath-extract': [['data', 'Array'], ['columns', 'Array']],
  llm: [['content', 'String'], ['model', 'String'], ['usage', 'Object']],
  condition: [['matched', 'Boolean'], ['data', 'Any']],
  knowledge: [['results', 'Array'], ['totalCount', 'Number']],
}
const options = computed(() => ancestors.value.map(node => {
  const fields: { name: string; type: string; label: string }[] = node.kind === 'start'
    ? (node.config.variables || []).map((field: { variable: string; type: string; label: string }) => ({ name: field.variable, type: field.type, label: field.label }))
    : (known[node.kind] || []).map(([name, type]) => ({ name, type, label: name }))
  const entries = [{ path: '$', name: '完整输出', type: 'Any', label: '完整输出' }, ...fields.map(field => ({ ...field, path: `$[${JSON.stringify(field.name)}]` }))]
  return { node, entries: entries.filter(field => `${node.title} ${field.name} ${field.label} ${field.type}`.toLowerCase().includes(keyword.value.trim().toLowerCase())) }
}).filter(group => group.entries.length))
watch(() => props.node.id, () => { open.value = false; keyword.value = ''; inputName.value = isOutput.value ? 'result' : 'input'; customNode.value = ''; customPath.value = '$'; error.value = '' })
function choose(nodeId: string, path: string) {
  if (props.disabled) return
  const name = inputName.value.trim()
  if (!/^[A-Za-z][A-Za-z0-9_]*$/.test(name) || ['__proto__', 'constructor', 'prototype'].includes(name)) { error.value = `${bindingLabel.value}名需以字母开头，仅包含字母、数字和下划线`; return }
  if (!ancestors.value.some(node => node.id === nodeId)) { error.value = '请先连接上游节点'; return }
  if (!path.trim()) { error.value = '请填写变量路径'; return }
  emit('update', { ...props.node.inputs, [name]: { nodeId, path: path.trim() } })
  error.value = ''; open.value = false
}
function remove(name: string) {
  const next = { ...props.node.inputs }; delete next[name]; emit('update', next)
}
function edit(name: string, reference: NodeReference) {
  inputName.value = name; customNode.value = reference.nodeId; customPath.value = reference.path; keyword.value = ''; open.value = true
}
function beginAdd() {
  let name = props.node.kind === 'set-operation' && !props.node.inputs.left ? 'left'
    : props.node.kind === 'set-operation' && !props.node.inputs.right ? 'right' : isOutput.value ? 'result' : 'input'
  let suffix = 2
  while (props.node.inputs[name]) name = `${isOutput.value ? 'result' : 'input'}${suffix++}`
  inputName.value = name; keyword.value = ''; customNode.value = ''; customPath.value = '$'; error.value = ''; open.value = true
}
function referenceLabel(reference: NodeReference) {
  if (reference.path === '$') return '完整输出'
  const upstream = props.nodes.find(node => node.id === reference.nodeId)
  const field = (upstream?.config.variables || []).find((item: { variable: string }) =>
    reference.path === `$.${item.variable}` || reference.path === `$[${JSON.stringify(item.variable)}]`)
  return field?.label || reference.path.replace(/^\$\./, '').replace(/^\$\["(.*)"\]$/, '$1')
}
</script>

<template>
  <section class="wf-binding" :aria-label="`${bindingLabel}变量选择`">
    <header><strong>{{ bindingLabel }}变量</strong><button type="button" :disabled="disabled" :aria-label="`添加${bindingLabel}变量`" @click="beginAdd">＋</button></header>
    <div v-for="(reference, name) in node.inputs" :key="name" class="wf-binding-row">
      <button type="button" :disabled="disabled" :aria-label="`修改${bindingLabel} ${name}`" @click="edit(String(name), reference)"><b>{{ name }}</b><span>{{ nodes.find(item => item.id === reference.nodeId)?.title || '节点已移除' }} · {{ referenceLabel(reference) }}</span></button>
      <button type="button" :disabled="disabled" :aria-label="`移除${bindingLabel} ${name}`" @click="remove(String(name))">×</button>
    </div>
    <button v-if="!Object.keys(node.inputs).length" type="button" class="wf-binding-empty" :disabled="disabled" @click="open = true">选择上游变量</button>
    <small v-if="!Object.keys(node.inputs).length">未指定变量时，使用唯一有效上游的完整输出。</small>
    <div v-if="open" class="wf-binding-picker" @keydown.esc.stop="open = false">
      <header><strong>选择变量</strong><button type="button" aria-label="关闭变量选择" @click="open = false">×</button></header>
      <label>{{ bindingLabel }}名<input v-model="inputName" :disabled="disabled" :placeholder="isOutput ? 'result' : 'input'" /></label>
      <input v-model="keyword" type="search" aria-label="搜索上游变量" placeholder="搜索节点、变量名称或类型" />
      <div class="wf-binding-options">
        <section v-for="group in options" :key="group.node.id"><h4>{{ group.node.title }}</h4><button v-for="field in group.entries" :key="field.path" type="button" :disabled="disabled" :aria-label="`${group.node.title} · ${field.label} · ${field.name}`" @click="choose(group.node.id, field.path)"><span>{{ field.label }}<small v-if="field.label !== field.name">{{ field.name }}</small></span><code>{{ field.type }}</code></button></section>
        <p v-if="!ancestors.length">请先连接上游节点，再选择它的输出变量。</p><p v-else-if="!options.length">没有匹配的变量</p>
      </div>
      <details><summary>自定义变量路径</summary><label>上游节点<select v-model="customNode"><option value="">请选择</option><option v-for="upstream in ancestors" :key="upstream.id" :value="upstream.id">{{ upstream.title }}</option></select></label><label>变量路径<input v-model="customPath" placeholder="$.data" /></label><button type="button" :disabled="disabled" @click="choose(customNode, customPath)">应用变量路径</button></details>
      <p v-if="error" role="alert" class="wf-error">{{ error }}</p>
    </div>
  </section>
</template>

<style scoped>
.wf-binding{display:grid;gap:8px;padding:16px 0;border-top:1px solid #eaecf0}.wf-binding header{display:flex;align-items:center;justify-content:space-between}.wf-binding button{cursor:pointer;border:1px solid #e4e7ec;border-radius:8px;background:white;padding:7px 10px;color:#344054}.wf-binding small{font-size:11px;color:#667085}.wf-binding-row{display:flex;gap:4px}.wf-binding-row>button:first-child{display:grid;gap:4px;flex:1;min-width:0;text-align:left}.wf-binding-row span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px}.wf-binding-empty{text-align:left;color:#667085!important;background:#f9fafb!important}.wf-binding-picker{display:grid;gap:10px;padding:12px;border:1px solid #d0d5dd;border-radius:12px;box-shadow:0 4px 12px #10182812}.wf-binding-picker label{display:grid;gap:4px}.wf-binding-options{max-height:260px;overflow:auto}.wf-binding-options h4{font-size:12px;color:#667085;margin:10px 0 4px}.wf-binding-options button{width:100%;display:flex;align-items:center;justify-content:space-between;text-align:left;border:0}.wf-binding-options button:hover{background:#eff4ff}.wf-binding-options small{display:block}.wf-binding-options code{font-size:11px;color:#667085}.wf-binding-picker summary{font-size:12px;color:#667085;cursor:pointer}
</style>
