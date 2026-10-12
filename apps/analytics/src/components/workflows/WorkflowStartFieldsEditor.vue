<script setup lang="ts">
/**
 * WorkflowStartFieldsEditor —— 开始节点输入字段（StartInputVariable）的定义编辑器。
 *
 * 位置：WorkflowNodeForm 中 kind === 'start' 时渲染（弹窗式增删改）。
 * 依赖接口：`api/workflows` 的 copy / StartInputVariable。
 * 关键约束：
 * - 字段类型集合（types）必须与后端 StartInputVariable.type 契约一致；新增类型需同步
 *   WorkflowStartInputForm 的渲染与 WorkflowDebugPanel/WorkflowPublishedRunner 的填写逻辑；
 * - 变量名（variable）是下游引用键，重名或非法字符会导致运行期取值失败，因此在此处校验；
 * - 选项（options）仅对 select 类型有意义，切换类型时应清理无用配置。
 */
import { computed, ref } from 'vue'
import { copy, type StartInputVariable } from '../../api/workflows'

const props = defineProps<{ modelValue: StartInputVariable[]; disabled: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: StartInputVariable[]] }>()

const types: { value: StartInputVariable['type']; label: string }[] = [
  { value: 'text-input', label: '短文本' }, { value: 'paragraph', label: '段落' },
  { value: 'select', label: '下拉选择' }, { value: 'number', label: '数字' },
  { value: 'url', label: 'URL' }, { value: 'checkbox', label: '复选框' },
  { value: 'json', label: 'JSON' }, { value: 'json_object', label: 'JSON 对象' },
  { value: 'file', label: '单文件' }, { value: 'file-list', label: '文件列表' },
]
const editing = ref<number | null>(null)
const showing = ref(false)
const draft = ref<StartInputVariable>(blank())
const defaultText = ref('')
const optionText = ref('')
const error = ref('')
const modalTitle = computed(() => editing.value === null ? '添加输入字段' : '编辑输入字段')

function blank(): StartInputVariable {
  return { variable: '', label: '', type: 'text-input', required: false, maxLength: 256, default: null, hint: '', placeholder: '', unit: '', options: [] }
}
function readableType(value: string) { return types.find(item => item.value === value)?.label || value }
function open(index: number | null) {
  showing.value = true
  editing.value = index
  draft.value = index === null ? blank() : copy(props.modelValue[index]!)
  optionText.value = (draft.value.options || []).join('\n')
  defaultText.value = draft.value.default === null || draft.value.default === undefined
    ? '' : typeof draft.value.default === 'string' ? draft.value.default : JSON.stringify(draft.value.default, null, 2)
  error.value = ''
}
function close() { showing.value = false; editing.value = null; error.value = '' }
function parseDefault() {
  if (!defaultText.value.trim()) return null
  if (draft.value.type === 'number') {
    const value = Number(defaultText.value)
    if (!Number.isFinite(value)) throw new Error('默认值必须是数字')
    return value
  }
  if (draft.value.type === 'checkbox') return defaultText.value === 'true'
  if (draft.value.type === 'json' || draft.value.type === 'json_object') {
    const value = JSON.parse(defaultText.value)
    if (draft.value.type === 'json_object' && (!value || Array.isArray(value) || typeof value !== 'object')) throw new Error('默认值必须是 JSON 对象')
    if (draft.value.type === 'json' && (!value || !['object'].includes(typeof value))) throw new Error('默认值必须是 JSON 对象或数组')
    return value
  }
  if (draft.value.type === 'file-list') return defaultText.value.split(/\r?\n/).map(value => value.trim()).filter(Boolean)
  return defaultText.value
}
function save() {
  const variable = draft.value.variable.trim()
  const label = draft.value.label.trim()
  if (!/^[A-Za-z][A-Za-z0-9_]*$/.test(variable)) { error.value = '变量名需以字母开头，只能包含字母、数字和下划线'; return }
  if (!label) { error.value = '请输入字段名称'; return }
  if (props.modelValue.some((item, index) => item.variable === variable && index !== editing.value)) { error.value = '变量名不能重复'; return }
  const options = optionText.value.split(/\r?\n|,/).map(value => value.trim()).filter(Boolean)
  if (draft.value.type === 'select' && !options.length) { error.value = '下拉选择至少需要一个候选项'; return }
  try {
    const item: StartInputVariable = {
      ...copy(draft.value), variable, label,
      options: draft.value.type === 'select' ? [...new Set(options)] : [],
      default: parseDefault(),
      maxLength: ['text-input', 'paragraph', 'url', 'select', 'json', 'json_object'].includes(draft.value.type)
        ? Number(draft.value.maxLength || 256) : null,
    }
    if (item.type === 'select' && item.default !== null && !item.options!.includes(String(item.default))) throw new Error('默认值必须属于候选项')
    const next = copy(props.modelValue)
    if (editing.value === null) next.push(item)
    else next.splice(editing.value, 1, item)
    emit('update:modelValue', next)
    close()
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '字段配置无效' }
}
function remove(index: number) { emit('update:modelValue', props.modelValue.filter((_, itemIndex) => itemIndex !== index)) }
function move(index: number, offset: number) {
  const target = index + offset
  if (target < 0 || target >= props.modelValue.length) return
  const next = copy(props.modelValue)
  const [item] = next.splice(index, 1)
  next.splice(target, 0, item!)
  emit('update:modelValue', next)
}
</script>

<template>
  <section class="wf-start-fields">
    <header><div><strong>输入字段</strong><small>设置的输入可在工作流程中使用</small></div><button type="button" :disabled="disabled || modelValue.length >= 50" aria-label="添加输入字段" @click="open(null)"><i class="fa-solid fa-plus" aria-hidden="true" />添加</button></header>
    <div v-if="modelValue.length" class="wf-start-field-list">
      <article v-for="(item, index) in modelValue" :key="item.variable" class="wf-start-field-row">
        <i class="fa-solid fa-cube" aria-hidden="true" />
        <button type="button" class="wf-start-field-main" :disabled="disabled" @click="open(index)"><strong>{{ item.variable }}</strong><span v-if="item.label">· {{ item.label }}</span></button>
        <small v-if="item.required">必填</small><b>{{ readableType(item.type) }}</b>
        <div v-if="!disabled" class="wf-start-field-actions"><button type="button" :disabled="index === 0" title="上移" @click="move(index, -1)"><i class="fa-solid fa-chevron-up" /></button><button type="button" :disabled="index === modelValue.length - 1" title="下移" @click="move(index, 1)"><i class="fa-solid fa-chevron-down" /></button><button type="button" title="删除" @click="remove(index)"><i class="fa-regular fa-trash-can" /></button></div>
      </article>
    </div>
    <button v-else type="button" class="wf-start-empty" :disabled="disabled" @click="open(null)"><i class="fa-solid fa-plus" aria-hidden="true" /><span>添加第一个输入字段</span><small>支持文本、数字、选择、JSON 和文件</small></button>
  </section>

  <Teleport to="body">
    <div v-if="showing" class="wf-field-modal-layer" role="presentation" @mousedown.self="close">
      <section class="wf-field-modal" role="dialog" aria-modal="true" :aria-label="modalTitle">
        <header><div><h2>{{ modalTitle }}</h2><p>配置运行前需要用户填写的值</p></div><button type="button" aria-label="关闭" @click="close"><i class="fa-solid fa-xmark" /></button></header>
        <div class="wf-field-modal-body">
          <label>字段类型<select v-model="draft.type" :disabled="disabled"><option v-for="item in types" :key="item.value" :value="item.value">{{ item.label }}</option></select></label>
          <div class="wf-field-grid"><label>变量名<input v-model="draft.variable" maxlength="80" placeholder="例如 query" :disabled="disabled" /></label><label>显示名称<input v-model="draft.label" maxlength="120" placeholder="例如 查询内容" :disabled="disabled" /></label></div>
          <label>占位提示<input v-model="draft.placeholder" maxlength="200" placeholder="告诉用户应该输入什么" :disabled="disabled" /></label>
          <label>帮助说明<textarea v-model="draft.hint" rows="2" maxlength="500" placeholder="显示在字段下方，可留空" :disabled="disabled" /></label>
          <label v-if="draft.type === 'select'">候选项<textarea v-model="optionText" rows="4" placeholder="每行一个候选项" :disabled="disabled" /></label>
          <div class="wf-field-grid">
            <label v-if="['text-input','paragraph','url','select','json','json_object'].includes(draft.type)">最大长度<input :value="draft.maxLength || 256" type="number" min="1" max="100000" :disabled="disabled" @input="draft.maxLength = Number(($event.target as HTMLInputElement).value)" /></label>
            <label v-if="draft.type === 'number'">单位<input v-model="draft.unit" maxlength="30" placeholder="例如 部、℃" :disabled="disabled" /></label>
            <label class="wf-switch-row"><span>必填</span><input v-model="draft.required" type="checkbox" :disabled="disabled" /></label>
          </div>
          <label v-if="draft.type === 'checkbox'">默认值<select v-model="defaultText" :disabled="disabled"><option value="">不设置</option><option value="true">选中</option><option value="false">未选中</option></select></label>
          <label v-else>默认值<textarea v-if="['paragraph','json','json_object','file-list'].includes(draft.type)" v-model="defaultText" rows="4" :placeholder="draft.type === 'file-list' ? '每行一个文件标识' : '可留空'" :disabled="disabled" /><input v-else v-model="defaultText" :type="draft.type === 'number' ? 'number' : 'text'" placeholder="可留空" :disabled="disabled" /></label>
          <p v-if="error" class="wf-error" role="alert">{{ error }}</p>
        </div>
        <footer><button type="button" @click="close">取消</button><button type="button" class="wf-primary" :disabled="disabled" @click="save">保存字段</button></footer>
      </section>
    </div>
  </Teleport>
</template>
