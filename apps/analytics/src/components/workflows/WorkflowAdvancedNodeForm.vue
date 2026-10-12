<script setup lang="ts">
/**
 * WorkflowAdvancedNodeForm —— 高级/AI 类节点的配置表单（llm、agent、knowledge、question、classifier、
 * extractor、setvar、text、template、aggregator、loop、iterator、while、parallel、subflow、tool、code、delay 等）。
 *
 * 位置：WorkflowNodeForm 按 kind 分派渲染（advancedKinds 集合）。
 * 依赖接口：`api/workflows`（FlowGraph / FlowNode / NodeKind / Workflow）、`utils/randomId.createClientId`、
 * `utils/workflowJson.parseWorkflowJson`、ExpressionEditor（变量/条件表达式）、
 * WorkflowSubgraphEditor（子流程内嵌编排，异步组件按需加载）。
 * 关键约束：
 * - 可选项来自后端下发的 resources（知识库/智能体/工具/工作流/模型）与 catalog，未列出的资源不得手填；
 * - 子流程只能引用**已发布**的工作流（正式版本），草稿不可作为子流程目标；
 * - 配置修改以 config 事件上抛，由 WorkflowNodeForm 统一合并进节点；
 * - toolError / variableError 分别承载工具参数与变量值的 JSON 解析错误，错误状态下不写入配置。
 */
import { computed, defineAsyncComponent, shallowRef } from 'vue'
import type { FlowGraph, FlowNode, NodeKind, Workflow } from '../../api/workflows'
import { createClientId } from '../../utils/randomId'
import { parseWorkflowJson } from '../../utils/workflowJson.js'
import ExpressionEditor from './ExpressionEditor.vue'

const WorkflowSubgraphEditor = defineAsyncComponent(() => import('./WorkflowSubgraphEditor.vue'))

interface ResourceOptions {
  knowledgeBases: any[]
  bots: any[]
  tools: any[]
  workflows: Workflow[]
  models: any[]
}

const props = defineProps<{ node: FlowNode; disabled: boolean; fields: string[]; resources: ResourceOptions; sources: any[]; catalog: NodeKind[] }>()
const emit = defineEmits<{ update: [config: Record<string, any>] }>()
const config = computed(() => props.node.config)
const kind = computed(() => props.node.kind)
const toolError = shallowRef(''), variableError = shallowRef('')
const subgraphEditorOpen = shallowRef(false)
const patch = (key: string, value: unknown) => emit('update', { ...config.value, [key]: value })
const patchItem = (key: string, index: number, value: object) => patch(key, (config.value[key] || []).map((item: any, current: number) => current === index ? { ...item, ...value } : item))
const removeItem = (key: string, index: number) => patch(key, (config.value[key] || []).filter((_: unknown, current: number) => current !== index))
const csv = (value: string) => value.split(',').map(item => item.trim()).filter(Boolean)
const modelNames = computed(() => {
  const names = props.resources.models.map(item => item.profile_name || item.profileName || item.name).filter(Boolean)
  return [...new Set(['reasoning', 'chat', ...names])]
})
function selectSubflow(event: Event) {
  const id = (event.target as HTMLSelectElement).value
  const selected = props.resources.workflows.find(item => item.id === id)
  emit('update', { ...config.value, workflowId: selected?.id || '', workflowVersion: selected?.version || 1 })
}
function parseToolArguments(event: Event) {
  try {
    const value = JSON.parse((event.target as HTMLTextAreaElement).value)
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('参数必须是 JSON 对象')
    patch('arguments', value)
    toolError.value = ''
  } catch (cause) {
    toolError.value = (cause as Error).message
  }
}
function addClassifierCase() {
  patch('cases', [...config.value.cases, {
    id: 'case_' + createClientId().slice(0, 8), label: '', description: '',
    expression: { op: 'gte', args: [{ field: '' }, { value: 0 }] },
  }])
}
function addQuestionField() {
  patch('fields', [...(config.value.fields || []), {
    name: 'field_' + createClientId().slice(0, 8), label: '', type: 'text-input',
    required: false, options: [], placeholder: '',
  }])
}
function saveSubgraph(value: FlowGraph) {
  // A configured visual body is the execution source. Clear legacy formula
  // updates in the same draft so validation does not ask for two loop modes.
  emit('update', { ...config.value, subgraph: value, updates: [] })
  subgraphEditorOpen.value = false
}
function variableSource(item: Record<string, any>) {
  return item.path !== undefined && item.path !== null ? 'path' : 'value'
}
function changeVariableSource(index: number, source: string) {
  const current = config.value.variables[index]
  const replacement = source === 'path'
    ? { name: current.name, path: '$.input' }
    : { name: current.name, value: null }
  patch('variables', config.value.variables.map((item: any, currentIndex: number) => currentIndex === index ? replacement : item))
  variableError.value = ''
}
function parseVariableValue(index: number, event: Event) {
  const input = event.target as HTMLInputElement
  try {
    const value = parseWorkflowJson(input.value)
    patchItem('variables', index, { value })
    input.value = JSON.stringify(value)
    variableError.value = ''
  } catch (cause) {
    input.value = JSON.stringify(config.value.variables[index].value ?? null)
    variableError.value = (cause as Error).message
  }
}
</script>

<template>
  <div class="wf-advanced-form">
    <template v-if="['llm', 'extractor'].includes(kind)">
      <label>模型配置<select :value="config.modelProfile" @change="patch('modelProfile', ($event.target as HTMLSelectElement).value)"><option v-for="name in modelNames" :key="name" :value="name">{{ name }}</option></select></label>
      <label>系统约束<textarea :value="config.systemPrompt" rows="3" @input="patch('systemPrompt', ($event.target as HTMLTextAreaElement).value)" /></label>
      <label>提示词模板<textarea :value="config.promptTemplate" rows="4" @input="patch('promptTemplate', ($event.target as HTMLTextAreaElement).value)" /></label>
      <label>温度<input type="number" min="0" max="2" step="0.1" :value="config.temperature" @change="patch('temperature', Number(($event.target as HTMLInputElement).value))" /></label>
      <template v-if="kind === 'extractor'">
        <div v-for="(field, index) in config.fields" :key="index" class="wf-rule-card">
          <label>字段名<input :value="field.name" @input="patchItem('fields', index, { name: ($event.target as HTMLInputElement).value })" /></label>
          <label>类型<select :value="field.type" @change="patchItem('fields', index, { type: ($event.target as HTMLSelectElement).value })"><option v-for="type in ['string', 'number', 'integer', 'boolean', 'array', 'object']" :key="type">{{ type }}</option></select></label>
          <label>字段口径<input :value="field.description" @input="patchItem('fields', index, { description: ($event.target as HTMLInputElement).value })" /></label>
          <label class="wf-check"><input type="checkbox" :checked="field.required" @change="patchItem('fields', index, { required: ($event.target as HTMLInputElement).checked })" />必填</label>
          <button :disabled="config.fields.length <= 1" @click="removeItem('fields', index)">移除字段</button>
        </div>
        <button @click="patch('fields', [...config.fields, { name: '', type: 'string', description: '', required: false }])">添加抽取字段</button>
      </template>
    </template>

    <template v-else-if="kind === 'knowledge'">
      <label>知识库<select multiple :value="config.knowledgeBaseIds" @change="patch('knowledgeBaseIds', Array.from(($event.target as HTMLSelectElement).selectedOptions).map(item => item.value))"><option v-for="item in resources.knowledgeBases" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
      <label>检索问题模板<textarea :value="config.queryTemplate" rows="3" @input="patch('queryTemplate', ($event.target as HTMLTextAreaElement).value)" /></label>
      <div class="wf-inline-fields"><label>检索方式<select :value="config.mode" @change="patch('mode', ($event.target as HTMLSelectElement).value)"><option value="hybrid">混合</option><option value="keyword">关键词</option><option value="semantic">语义</option></select></label><label>返回条数<input type="number" min="1" max="20" :value="config.topK" @change="patch('topK', Number(($event.target as HTMLInputElement).value))" /></label><label>最低相关分数<input type="number" min="0" step="0.05" :value="config.minScore" @change="patch('minScore', Number(($event.target as HTMLInputElement).value))" /></label></div>
    </template>

    <template v-else-if="kind === 'agent'">
      <label>Bot / 智能体<select :value="config.botId" @change="patch('botId', ($event.target as HTMLSelectElement).value)"><option value="">请选择</option><option v-for="item in resources.bots" :key="item.id" :value="item.id">{{ item.name }} · {{ item.status }}</option></select></label>
      <label>执行方式<select :value="config.mode" @change="patch('mode', ($event.target as HTMLSelectElement).value)"><option value="model">基于证据调用模型</option><option value="retrieval">只返回检索证据</option></select></label>
      <label>问题模板<textarea :value="config.queryTemplate" rows="3" @input="patch('queryTemplate', ($event.target as HTMLTextAreaElement).value)" /></label>
      <label class="wf-check"><input type="checkbox" :checked="config.usePublished" @change="patch('usePublished', ($event.target as HTMLInputElement).checked)" />运行已发布版本</label>
    </template>

    <template v-else-if="kind === 'question'">
      <label>等待问题<textarea :value="config.prompt" rows="3" @input="patch('prompt', ($event.target as HTMLTextAreaElement).value)" /></label>
      <label>回答字段<input :value="config.answerKey" @input="patch('answerKey', ($event.target as HTMLInputElement).value)" /></label>
      <label class="wf-check"><input type="checkbox" :checked="config.required" @change="patch('required', ($event.target as HTMLInputElement).checked)" />必须回答后恢复</label>
      <div v-for="(field, index) in (config.fields || [])" :key="field.name || index" class="wf-rule-card">
        <label>字段名<input :value="field.name" @input="patchItem('fields', index, { name: ($event.target as HTMLInputElement).value })" /></label>
        <label>显示名称<input :value="field.label" @input="patchItem('fields', index, { label: ($event.target as HTMLInputElement).value })" /></label>
        <label>字段类型<select :value="field.type" @change="patchItem('fields', index, { type: ($event.target as HTMLSelectElement).value, options: ($event.target as HTMLSelectElement).value === 'select' ? field.options || [] : [] })"><option value="text-input">单行文本</option><option value="paragraph">多行文本</option><option value="number">数值</option><option value="select">下拉选择</option><option value="checkbox">勾选</option><option value="json">JSON</option><option value="json_object">JSON 对象</option></select></label>
        <label v-if="field.type === 'select'">候选项（逗号分隔）<input :value="(field.options || []).join(', ')" @change="patchItem('fields', index, { options: csv(($event.target as HTMLInputElement).value) })" /></label>
        <label class="wf-check"><input type="checkbox" :checked="field.required" @change="patchItem('fields', index, { required: ($event.target as HTMLInputElement).checked })" />必填</label>
        <button :disabled="(config.fields || []).length <= 1" @click="removeItem('fields', index)">移除字段</button>
      </div>
      <button @click="addQuestionField">添加结构化字段</button>
      <p class="wf-note">没有结构化字段时兼容单文本回答；配置字段后，恢复请求必须提交对象，服务端会逐字段校验类型和候选项。</p>
    </template>

    <template v-else-if="kind === 'delay'">
      <label>等待秒数<input type="number" min="1" max="604800" :value="config.seconds" @change="patch('seconds', Number(($event.target as HTMLInputElement).value))" /></label>
      <p class="wf-note">运行状态会持久化；到期后可从运行结果恢复，不重复执行上游节点。</p>
    </template>

    <template v-else-if="kind === 'classifier'">
      <label>分类方式<select :value="config.mode || 'rules'" @change="patch('mode', ($event.target as HTMLSelectElement).value)"><option value="rules">规则表达式</option><option value="model">模型分类</option></select></label>
      <template v-if="config.mode === 'model'">
        <label>模型配置<select :value="config.modelProfile || 'reasoning'" @change="patch('modelProfile', ($event.target as HTMLSelectElement).value)"><option v-for="name in modelNames" :key="name" :value="name">{{ name }}</option></select></label>
        <label>系统约束<textarea :value="config.systemPrompt || ''" rows="3" placeholder="只依据输入证据选择一个分类，不补造缺失信息。" @input="patch('systemPrompt', ($event.target as HTMLTextAreaElement).value)" /></label>
        <label>分类问题模板<textarea :value="config.promptTemplate || '{{ input }}'" rows="3" placeholder="请根据输入选择最符合的分类：{{ input }}" @input="patch('promptTemplate', ($event.target as HTMLTextAreaElement).value)" /></label>
        <label>温度<input type="number" min="0" max="2" step="0.1" :value="config.temperature ?? 0.1" @change="patch('temperature', Number(($event.target as HTMLInputElement).value))" /></label>
        <p class="wf-note">模型模式会把分类编码、名称和说明提供给模型；运行时仍只接受配置中的分类编码。</p>
      </template>
      <div v-for="(item, index) in config.cases" :key="item.id" class="wf-rule-card"><label>分类编码<input :value="item.id" @input="patchItem('cases', index, { id: ($event.target as HTMLInputElement).value })" /></label><label>分类名称<input :value="item.label" @input="patchItem('cases', index, { label: ($event.target as HTMLInputElement).value })" /></label><label>分类说明<textarea :value="item.description || ''" rows="2" placeholder="说明什么情况下归入此类" @input="patchItem('cases', index, { description: ($event.target as HTMLTextAreaElement).value })" /></label><ExpressionEditor v-if="(config.mode || 'rules') === 'rules'" :model-value="item.expression" :fields="fields" @update:model-value="patchItem('cases', index, { expression: $event })" /><p v-else class="wf-note">模型模式使用分类说明，不执行此分类的规则表达式。</p><button :disabled="config.cases.length <= 1" @click="removeItem('cases', index)">移除此分类</button></div>
      <button @click="addClassifierCase">添加分类</button>
      <label>未匹配编码<input :value="config.fallback" @input="patch('fallback', ($event.target as HTMLInputElement).value)" /></label>
    </template>

    <template v-else-if="kind === 'setvar'">
      <label class="wf-check"><input type="checkbox" :checked="config.keepInput" @change="patch('keepInput', ($event.target as HTMLInputElement).checked)" />保留对象输入字段</label>
      <div v-for="(item, index) in config.variables" :key="index" class="wf-rule-card"><label>变量名<input :value="item.name" @input="patchItem('variables', index, { name: ($event.target as HTMLInputElement).value })" /></label><label>变量值来源<select :value="variableSource(item)" @change="changeVariableSource(index, ($event.target as HTMLSelectElement).value)"><option value="path">上游 JSONPath</option><option value="value">固定 JSON 值</option></select></label><label v-if="variableSource(item) === 'path'">输入 JSONPath<input :value="item.path" placeholder="$.input.field" @input="patchItem('variables', index, { path: ($event.target as HTMLInputElement).value })" /></label><label v-else>固定值（JSON）<input :value="JSON.stringify(item.value ?? null)" @change="parseVariableValue(index, $event)" /></label><button :disabled="config.variables.length <= 1" @click="removeItem('variables', index)">移除变量</button></div>
      <p v-if="variableError" role="alert" class="wf-error">{{ variableError }}</p>
      <button @click="patch('variables', [...config.variables, { name: '', path: '$.input' }])">添加变量</button>
    </template>

    <template v-else-if="kind === 'text'">
      <label>文本操作<select :value="config.operation" @change="patch('operation', ($event.target as HTMLSelectElement).value)"><option value="stringify">JSON 转文本</option><option value="trim">去除首尾空白</option><option value="upper">转大写</option><option value="lower">转小写</option><option value="replace">替换</option><option value="split">拆分数组</option><option value="join">连接数组</option></select></label>
      <template v-if="config.operation === 'replace'"><label>查找<input :value="config.find" @input="patch('find', ($event.target as HTMLInputElement).value)" /></label><label>替换为<input :value="config.replacement" @input="patch('replacement', ($event.target as HTMLInputElement).value)" /></label></template>
      <label v-if="['split', 'join'].includes(config.operation)">分隔符<input :value="config.separator" @input="patch('separator', ($event.target as HTMLInputElement).value)" /></label>
    </template>

    <template v-else-if="kind === 'template'">
      <label>文本模板<textarea :value="config.template" rows="5" @input="patch('template', ($event.target as HTMLTextAreaElement).value)" /></label><p class="wf-note">使用 <code v-pre>{{ input }}</code> 或 <code v-pre>{{ $.field }}</code>，不执行脚本。</p>
    </template>

    <template v-else-if="kind === 'aggregator'">
      <label>分组字段（逗号分隔）<input :value="config.groupBy?.join(', ')" @change="patch('groupBy', csv(($event.target as HTMLInputElement).value))" /></label>
      <div v-for="(item, index) in config.metrics" :key="index" class="wf-rule-card"><label>结果字段<input :value="item.name" @input="patchItem('metrics', index, { name: ($event.target as HTMLInputElement).value })" /></label><label>聚合方式<select :value="item.operation" @change="patchItem('metrics', index, { operation: ($event.target as HTMLSelectElement).value })"><option v-for="op in ['count', 'sum', 'avg', 'min', 'max', 'count_distinct']" :key="op">{{ op }}</option></select></label><label>来源字段<input :value="item.field" list="workflow-fields" @input="patchItem('metrics', index, { field: ($event.target as HTMLInputElement).value })" /></label><button :disabled="config.metrics.length <= 1" @click="removeItem('metrics', index)">移除指标</button></div>
      <button @click="patch('metrics', [...config.metrics, { name: '', operation: 'count', field: '' }])">添加聚合指标</button>
    </template>

    <template v-else-if="['loop', 'iterator', 'while'].includes(kind)">
      <label v-if="kind === 'loop'">固定次数<input type="number" min="1" max="100" :value="config.iterations" @change="patch('iterations', Number(($event.target as HTMLInputElement).value))" /></label>
      <template v-if="kind === 'while'"><ExpressionEditor :model-value="config.expression" :fields="fields" @update:model-value="patch('expression', $event)" /><label>最大次数<input type="number" min="1" max="100" :value="config.maxIterations" @change="patch('maxIterations', Number(($event.target as HTMLInputElement).value))" /></label></template>
      <template v-if="!config.subgraph">
        <div v-for="(item, index) in config.updates" :key="index" class="wf-rule-card"><label>更新字段<input :value="item.name" @input="patchItem('updates', index, { name: ($event.target as HTMLInputElement).value })" /></label><label>计算公式<input :value="item.formula" placeholder="amount * 2" @input="patchItem('updates', index, { formula: ($event.target as HTMLInputElement).value })" /></label><button @click="removeItem('updates', index)">移除更新</button></div>
        <button @click="patch('updates', [...(config.updates || []), { name: '', formula: '' }])">添加字段更新</button>
      </template>
      <p v-else class="wf-note">已配置可视化循环体，运行时按子图执行；如需使用计算公式，请先清空子图。</p>
      <label v-if="kind === 'loop'" class="wf-check"><input type="checkbox" :checked="config.keepHistory" @change="patch('keepHistory', ($event.target as HTMLInputElement).checked)" />保留每轮结果</label>
      <div class="wf-subgraph-launcher"><div><strong>循环体子图</strong><small v-if="config.subgraph">已配置 {{ config.subgraph.nodes?.length || 0 }} 个节点、{{ config.subgraph.edges?.length || 0 }} 条连线</small><small v-else>尚未配置；打开编辑器后会自动创建开始和结束节点</small></div><div class="wf-subgraph-launch-actions"><button type="button" :disabled="disabled" @click="subgraphEditorOpen = true">{{ config.subgraph ? '编辑子图' : '创建子图' }}</button><button v-if="config.subgraph" type="button" :disabled="disabled" @click="patch('subgraph', null)">清空</button></div></div>
      <p class="wf-note">使用可视化编辑器添加节点、拖动连线和编辑属性。当前循环体明确不支持人工提问或延迟节点；每轮或每项会以当前值进入子图。</p>
      <WorkflowSubgraphEditor v-if="subgraphEditorOpen" :graph="config.subgraph" :catalog="catalog" :sources="sources" :resources="resources" :fields="fields" :disabled="disabled" @save="saveSubgraph" @cancel="subgraphEditorOpen = false" />
    </template>

    <template v-else-if="kind === 'parallel'">
      <label>分支标签（逗号分隔）<input :value="config.branchNames?.join(', ')" @change="patch('branchNames', csv(($event.target as HTMLInputElement).value))" /></label><p class="wf-note">从该节点连接多条下游连线；各分支使用同一份不可变输入，在汇合节点收口。</p>
    </template>

    <template v-else-if="kind === 'subflow'">
      <label>已发布子流程<select :value="config.workflowId" @change="selectSubflow"><option value="">请选择</option><option v-for="item in resources.workflows" :key="item.id" :value="item.id" :disabled="item.status !== 'published'">{{ item.name }} · {{ item.status }}</option></select></label>
      <label>超时秒数<input type="number" min="1" max="300" :value="config.timeoutSeconds" @change="patch('timeoutSeconds', Number(($event.target as HTMLInputElement).value))" /></label>
      <label class="wf-check"><input type="checkbox" :checked="config.confirmWrites" @change="patch('confirmWrites', ($event.target as HTMLInputElement).checked)" />允许子流程执行其已配置写回</label>
    </template>

    <template v-else-if="kind === 'tool'">
      <label>工具<select :value="config.toolName" @change="patch('toolName', ($event.target as HTMLSelectElement).value)"><option value="">请选择已发布工具</option><option v-for="item in resources.tools" :key="item.id || item.name" :value="item.name">{{ item.name }}</option></select></label>
      <label>参数 JSON<textarea :value="JSON.stringify(config.arguments || {}, null, 2)" rows="6" @change="parseToolArguments" /></label><p v-if="toolError" role="alert" class="wf-error">{{ toolError }}</p>
      <label class="wf-check"><input type="checkbox" :checked="config.includeInput" @change="patch('includeInput', ($event.target as HTMLInputElement).checked)" />将上游值作为 input 参数</label>
    </template>

    <template v-else-if="kind === 'code'">
      <label>受控 Python<textarea :value="config.code" rows="9" spellcheck="false" @input="patch('code', ($event.target as HTMLTextAreaElement).value)" /></label><label>超时秒数<input type="number" min="5" max="300" :value="config.timeoutSeconds" @change="patch('timeoutSeconds', Number(($event.target as HTMLInputElement).value))" /></label><p class="wf-note">inputs 是上方配置的命名输入；默认上游值可用 input。把结果赋给 result，或定义 main 函数，参数名与输入变量一致，并返回结果。输出位于 data.result。</p>
    </template>
    <datalist id="workflow-fields"><option v-for="field in fields" :key="field" :value="field" /></datalist>
  </div>
</template>
