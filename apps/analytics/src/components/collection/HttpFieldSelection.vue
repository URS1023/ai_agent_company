<script setup lang="ts">
/**
 * HTTP 采集源的"记录路径 + 字段提取"配置块（v-model 绑定 ExtractField[]）。
 * 路由：无独立路由，嵌入 HttpSourceForm（数据源中心 → HTTP 接口）。
 * 依赖接口：api/collection.ts 的 httpCollection.preview（POST /extract-preview，离线提取，不访问上游）。
 * 关键交互：模式二选一 —— 保留全部顶层字段（fields 清空）或显式选择字段（至少 1 个，否则通过 invalid 事件阻断保存）；
 *   "从样本识别字段"/"预览所选字段"都只对粘贴的 JSON 样本做本地提取，绝不发 HTTP 请求；
 *   记录路径、样本或字段定义变化时自动清空上一次预览结果（避免展示过期预览）。
 * 约束：字段 JSONPath 相对每条记录；数值不自动转换（编号不会被当成数字）；单源字段上限 200 个。
 * 错误呈现：error 就近展示（合法 JSON 校验失败也会转成中文提示）。
 */
import { computed, ref, shallowRef, watch } from 'vue'
import { httpCollection, type ExtractField, type ExtractionResult, jsonCell } from '../../api/collection'
const fields = defineModel<ExtractField[]>({ required: true })
const props = defineProps<{ recordPath: string; disabled?: boolean }>()
const sample = shallowRef('')
const preview = shallowRef<ExtractionResult | null>(null)
const error = shallowRef('')
const busy = shallowRef(false)
const mode = shallowRef(fields.value.length ? 'selected' : 'all')
const candidates = ref<string[]>([])
const fieldTypes = ['auto', 'string', 'integer', 'decimal', 'boolean', 'json'] as const
const labels = { auto: '保持原值', string: '文本', integer: '整数', decimal: '精确小数', boolean: '布尔', json: 'JSON' }
const invalid = computed(() => mode.value === 'selected' && fields.value.length === 0)
const emit = defineEmits<{ invalid: [value: boolean] }>()
watch(invalid, value => emit('invalid', value), { immediate: true })
// 记录路径、样本内容或字段定义任一变化都会让旧预览失效，必须清空，避免用户按过期预览做配置。
watch(() => [props.recordPath, sample.value, JSON.stringify(fields.value)], () => { preview.value = null })
function setMode(value: string) { mode.value = value; if (value === 'all') fields.value = [] }
function add(name = '') {
  mode.value = 'selected'
  fields.value = [...fields.value, { name, path: name ? '$[' + JSON.stringify(name) + ']' : '$', type: 'auto', required: false, multiple: false, comment: '' }]
}
function patch(index: number, value: Partial<ExtractField>) { fields.value = fields.value.map((field, i) => i === index ? { ...field, ...value } : field) }
function remove(index: number) { fields.value = fields.value.filter((_, i) => i !== index) }
/**
 * 提取预览：discover=true 时传空字段列表，由后端按样本推断候选字段（columns.columnName）。
 * 约束：样本必须能 JSON.parse（否则提示"请粘贴合法的 JSON 响应样本"）；不产生任何网络请求到上游。
 */
async function test(discover = false) {
  error.value = ''; busy.value = true; preview.value = null
  try {
    let parsed: unknown
    try { parsed = JSON.parse(sample.value) } catch { throw new Error('请粘贴合法的 JSON 响应样本') }
    const result = (await httpCollection.preview(parsed, props.recordPath, discover ? [] : fields.value)).data
    if (discover) candidates.value = result.columns.map(column => column.columnName)
    preview.value = result
  } catch (cause) { error.value = (cause as Error).message }
  finally { busy.value = false }
}
</script>

<template>
  <section class="http-fields" aria-label="输出字段选择">
    <div class="http-radio-line">
      <label><input type="radio" name="http-field-mode" :checked="mode === 'all'" :disabled="disabled" @change="setMode('all')" /> 保留全部顶层字段</label>
      <label><input type="radio" name="http-field-mode" :checked="mode === 'selected'" :disabled="disabled" @change="setMode('selected')" /> 选择输出字段</label>
    </div>
    <p class="http-note">字段路径相对于每条记录。JSONPath 支持嵌套、索引、切片和条件过滤；数值转换需明确选择，编号不会自动转成数字。</p>
    <div v-if="mode === 'selected'" class="http-field-list">
      <article v-for="(field, index) in fields" :key="index" class="http-field-row">
        <label>输出名称<input :value="field.name" required :disabled="disabled" @input="patch(index, { name: ($event.target as HTMLInputElement).value })" /></label>
        <label>字段 JSONPath<input :value="field.path" required :disabled="disabled" @input="patch(index, { path: ($event.target as HTMLInputElement).value })" /></label>
        <label>数据类型<select :value="field.type" :disabled="disabled" @change="patch(index, { type: ($event.target as HTMLSelectElement).value as ExtractField['type'] })"><option v-for="type in fieldTypes" :key="type" :value="type">{{ labels[type] }}</option></select></label>
        <label>字段说明<input :value="field.comment" :disabled="disabled" @input="patch(index, { comment: ($event.target as HTMLInputElement).value })" /></label>
        <label class="http-check"><input type="checkbox" :checked="field.required" :disabled="disabled" @change="patch(index, { required: ($event.target as HTMLInputElement).checked })" /> 必填</label>
        <label class="http-check"><input type="checkbox" :checked="field.multiple" :disabled="disabled" @change="patch(index, { multiple: ($event.target as HTMLInputElement).checked })" /> 多值</label>
        <button type="button" :disabled="disabled" :aria-label="'移除字段 ' + (field.name || index + 1)" @click="remove(index)">移除</button>
      </article>
      <button type="button" :disabled="disabled || fields.length >= 200" @click="add()">添加字段</button>
      <p v-if="invalid" role="status" class="http-error">选择输出字段模式下，至少添加一个字段。</p>
    </div>
    <details class="http-preview-section">
      <summary>用响应样本识别字段、预览提取</summary>
      <label>JSON 响应样本<textarea v-model="sample" rows="5" spellcheck="false" placeholder="粘贴不含凭据的接口响应。仅离线提取，不发送 HTTP 请求。" :disabled="disabled || busy" /></label>
      <div class="http-actions"><button type="button" :disabled="busy || disabled" @click="test(true)">从样本识别字段</button><button type="button" :disabled="busy || disabled || invalid" @click="test()">预览所选字段</button></div>
      <div v-if="candidates.length" class="http-candidates"><span>选择保留：</span><label v-for="name in candidates" :key="name" class="http-check"><input type="checkbox" :checked="fields.some(field => field.name === name)" :disabled="disabled" @change="($event.target as HTMLInputElement).checked ? add(name) : remove(fields.findIndex(field => field.name === name))" />{{ name }}</label></div>
      <p v-if="error" class="http-error" role="alert">{{ error }}</p>
      <p v-if="busy" role="status">正在提取…</p>
      <template v-if="preview"><p class="http-note">样本预览 {{ preview.totalCount }} 行{{ preview.truncated ? '，还有未展示记录' : '' }}，不是设备采集结果。</p>
        <div class="http-table-wrap"><table class="http-table"><thead><tr><th v-for="column in preview.columns" :key="column.columnName">{{ column.columnName }}</th></tr></thead><tbody><tr v-for="(row, index) in preview.data" :key="index"><td v-for="column in preview.columns" :key="column.columnName">{{ jsonCell(row[column.columnName]) }}</td></tr></tbody></table></div>
      </template>
    </details>
  </section>
</template>
