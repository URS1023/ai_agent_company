<script setup lang="ts">
/**
 * WorkflowHttpRequestForm —— http-request 节点的请求头/查询参数/请求体/Auth/方法配置表单。
 *
 * 位置：WorkflowNodeForm 中 kind === 'http-request' 时渲染。
 * 依赖接口：`utils/workflowJson.parseWorkflowJson`（宽松 JSON 解析）、`utils/randomId.createClientId`
 * （行级临时 id）；输出 update 事件回传完整 HTTP 配置。
 * 关键约束：
 * - 请求体三种模式语义固定：inherit 表示沿用绑定数据源里已保存的请求体（节点不覆盖），
 *   object 按字段填写，json 兼容已有数组/基础值；切换模式需按当前内容转换而不是清空；
 * - 行编辑采用"本地表格 + 提交时解析"的方式，用 ownSnapshot 区分"外部变更"与"自己刚提交的值"，
 *   避免监听 config 时把用户正在编辑的行覆盖回旧值；
 * - 参数与请求头不允许重复键（请求头忽略大小写），重复时给出中文错误且不提交；
 * - 参数值需按声明类型解码（number/boolean/null/text），无法识别的类型标记为 unsupported 并保留原值。
 */
import { computed, ref, watch } from 'vue'
import { parseWorkflowJson } from '../../utils/workflowJson.js'
import { createClientId } from '../../utils/randomId'

type HttpConfig = Record<string, any>
type ParameterValueType = 'text' | 'number' | 'boolean' | 'null' | 'unsupported'
type BodyValueType = 'text' | 'number' | 'boolean' | 'null' | 'json'
type BodyMode = 'inherit' | 'object' | 'json'

interface HeaderRow {
  id: string
  key: string
  value: string
}

interface ParameterRow {
  id: string
  key: string
  value: string
  valueType: ParameterValueType
}

interface BodyRow {
  id: string
  key: string
  value: string
  valueType: BodyValueType
}

const props = defineProps<{ config: HttpConfig; disabled: boolean }>()
const emit = defineEmits<{ update: [config: HttpConfig] }>()

const headers = ref<HeaderRow[]>([])
const params = ref<ParameterRow[]>([])
const bodyRows = ref<BodyRow[]>([])
const bodyMode = ref<BodyMode>('inherit')
const bodyJson = ref('')
const error = ref('')

type ConfigSnapshot = { headers: string; params: string; body: string }
let ownSnapshot: ConfigSnapshot | null = null

const hasBodyRows = computed(() => bodyRows.value.length > 0)
const bodyModeHelp = computed(() => bodyMode.value === 'inherit'
  ? '不设置节点请求体，沿用绑定数据源中已保存的请求体。'
  : bodyMode.value === 'object'
    ? '常见 JSON 对象可按字段填写；对象、数组和模板值可在字段类型中选择。'
    : '兼容已有 JSON 数组或基础值；请求体支持 JSON 格式。')

function rowId(prefix: string) {
  return `${prefix}_${createClientId().slice(0, 8)}`
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}

function textValue(value: unknown): string {
  if (typeof value === 'string') return value
  if (value === undefined) return ''
  try { return JSON.stringify(value) ?? '' } catch { return String(value) }
}

function parameterType(value: unknown): ParameterValueType {
  if (value === null) return 'null'
  if (typeof value === 'boolean') return 'boolean'
  if (typeof value === 'number' && Number.isFinite(value)) return 'number'
  if (typeof value === 'string') return 'text'
  return 'unsupported'
}

function bodyType(value: unknown): BodyValueType {
  if (value === null) return 'null'
  if (typeof value === 'boolean') return 'boolean'
  if (typeof value === 'number' && Number.isFinite(value)) return 'number'
  if (typeof value === 'string') return 'text'
  return 'json'
}

function snapshotValue(value: unknown): string {
  try { return JSON.stringify(value ?? null) ?? String(value) } catch { return String(value) }
}

function configSnapshot(config: HttpConfig = props.config): ConfigSnapshot {
  return {
    headers: snapshotValue(config.headers || {}),
    params: snapshotValue(config.params || {}),
    body: snapshotValue(config.body),
  }
}

function sameSnapshot(left: ConfigSnapshot, right: ConfigSnapshot) {
  return left.headers === right.headers && left.params === right.params && left.body === right.body
}

function syncFromConfig() {
  const configuredHeaders = asRecord(props.config.headers) || {}
  headers.value = Object.entries(configuredHeaders).map(([key, value]) => ({ id: rowId('header'), key, value: String(value ?? '') }))

  const configuredParams = asRecord(props.config.params) || {}
  params.value = Object.entries(configuredParams).map(([key, value]) => ({
    id: rowId('param'), key, value: textValue(value), valueType: parameterType(value),
  }))

  const configuredBody = props.config.body
  if (configuredBody === null || configuredBody === undefined) {
    bodyMode.value = 'inherit'
    bodyRows.value = []
    bodyJson.value = ''
  } else if (asRecord(configuredBody)) {
    bodyMode.value = 'object'
    bodyRows.value = Object.entries(configuredBody).map(([key, value]) => ({
      id: rowId('body'), key, value: textValue(value), valueType: bodyType(value),
    }))
    bodyJson.value = JSON.stringify(configuredBody, null, 2)
  } else {
    bodyMode.value = 'json'
    bodyRows.value = []
    bodyJson.value = JSON.stringify(configuredBody, null, 2) ?? ''
  }
  error.value = ''
}

watch(() => [props.config.headers, props.config.params, props.config.body], () => {
  const incoming = configSnapshot()
  if (ownSnapshot && sameSnapshot(incoming, ownSnapshot)) {
    ownSnapshot = null
    return
  }
  ownSnapshot = null
  syncFromConfig()
}, { immediate: true, deep: true })

function emitConfig(patch: HttpConfig = {}, unsetBody = false) {
  if (props.disabled) return
  const next = { ...props.config, ...patch }
  // ``body: null`` is the catalog's legacy placeholder. Omitting it is the
  // only way to preserve a saved source body when this node chooses inherit.
  if (unsetBody || (bodyMode.value === 'inherit' && !Object.prototype.hasOwnProperty.call(patch, 'body'))) delete next.body
  ownSnapshot = configSnapshot(next)
  emit('update', next)
}

/** 检测重复键：请求头按忽略大小写比较（HTTP 头名大小写不敏感），参数按精确比较。 */
function duplicateKeys(rows: Array<{ key: string }>, caseInsensitive = false) {
  const seen = new Set<string>()
  for (const row of rows) {
    const key = row.key.trim()
    if (!key) continue
    const normalized = caseInsensitive ? key.toLowerCase() : key
    if (seen.has(normalized)) return key
    seen.add(normalized)
  }
  return ''
}

/** 提交请求头：有重复键时只提示错误、不改配置；成功后记录快照以免被自身监听回灌覆盖。 */
function commitHeaders() {
  const duplicate = duplicateKeys(headers.value, true)
  if (duplicate) { error.value = `请求头名称重复：${duplicate}`; return }
  const value: Record<string, string> = {}
  for (const row of headers.value) {
    const key = row.key.trim()
    if (key) value[key] = row.value
  }
  error.value = ''
  emitConfig({ headers: value })
}

/** 按声明类型把文本解码成实际值；unsupported 类型原样保留文本，避免丢用户数据。 */
function decodeParameter(row: ParameterRow): string | number | boolean | null {
  if (row.valueType === 'text') return row.value
  if (row.valueType === 'null') return null
  if (row.valueType === 'boolean') {
    if (row.value !== 'true' && row.value !== 'false') throw new Error(`查询参数“${row.key || '未命名'}”必须选择 true 或 false`)
    return row.value === 'true'
  }
  if (row.valueType === 'number') {
    if (!row.value.trim() || !Number.isFinite(Number(row.value))) throw new Error(`查询参数“${row.key || '未命名'}”必须是有效数字`)
    return Number(row.value)
  }
  throw new Error(`查询参数“${row.key || '未命名'}”包含无法发送的旧值，请改成文本、数字、布尔值或空值`)
}

function commitParams() {
  const duplicate = duplicateKeys(params.value)
  if (duplicate) { error.value = `查询参数名称重复：${duplicate}`; return }
  const value: Record<string, string | number | boolean | null> = {}
  try {
    for (const row of params.value) {
      const key = row.key.trim()
      if (key) value[key] = decodeParameter(row)
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '查询参数无效'
    return
  }
  error.value = ''
  emitConfig({ params: value })
}

function addHeader() { if (!props.disabled) headers.value.push({ id: rowId('header'), key: '', value: '' }) }
function removeHeader(index: number) { if (props.disabled) return; headers.value.splice(index, 1); commitHeaders() }
function addParam() { if (!props.disabled) params.value.push({ id: rowId('param'), key: '', value: '', valueType: 'text' }) }
function removeParam(index: number) { if (props.disabled) return; params.value.splice(index, 1); commitParams() }

function decodeBodyValue(row: BodyRow): unknown {
  if (row.valueType === 'text') return row.value
  if (row.valueType === 'null') return null
  if (row.valueType === 'boolean') {
    if (row.value !== 'true' && row.value !== 'false') throw new Error(`请求体字段“${row.key || '未命名'}”必须选择 true 或 false`)
    return row.value === 'true'
  }
  if (row.valueType === 'number') {
    if (!row.value.trim() || !Number.isFinite(Number(row.value))) throw new Error(`请求体字段“${row.key || '未命名'}”必须是有效数字`)
    return Number(row.value)
  }
  if (!row.value.trim()) throw new Error(`请求体字段“${row.key || '未命名'}”的 JSON 值不能为空`)
  return parseWorkflowJson(row.value)
}

function collectBodyObject() {
  const duplicate = duplicateKeys(bodyRows.value)
  if (duplicate) throw new Error(`请求体字段名称重复：${duplicate}`)
  const value: Record<string, unknown> = {}
  for (const row of bodyRows.value) {
    const key = row.key.trim()
    if (key) value[key] = decodeBodyValue(row)
  }
  return value
}

function applyObjectBody() {
  if (props.disabled) return
  try {
    const value = collectBodyObject()
    bodyJson.value = JSON.stringify(value, null, 2)
    error.value = ''
    emitConfig({ body: value })
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '请求体字段无效'
  }
}

function applyJsonBody() {
  if (!bodyJson.value.trim()) { error.value = '请输入 JSON 请求体，或选择“沿用数据源请求体”'; return }
  try {
    const value = parseWorkflowJson(bodyJson.value)
    if (value === null) throw new Error('空白请求体会沿用数据源；请改用其他 JSON 值，或选择“沿用数据源请求体”')
    error.value = ''
    emitConfig({ body: value })
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '请求体 JSON 无效'
  }
}

function changeBodyMode(value: BodyMode) {
  if (props.disabled) return
  if (value === 'inherit') {
    bodyMode.value = value
    bodyRows.value = []
    bodyJson.value = ''
    error.value = ''
    emitConfig({}, true)
    return
  }
  if (value === 'json') {
    try {
      if (bodyMode.value === 'object') bodyJson.value = JSON.stringify(collectBodyObject(), null, 2)
      bodyMode.value = value
      error.value = ''
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : '请求体字段无效'
    }
    return
  }
  if (bodyMode.value === 'json' && bodyJson.value.trim()) {
    try {
      const parsed = parseWorkflowJson(bodyJson.value)
      const object = asRecord(parsed)
      if (object) bodyRows.value = Object.entries(object).map(([key, item]) => ({ id: rowId('body'), key, value: textValue(item), valueType: bodyType(item) }))
      else bodyRows.value = []
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : '请求体 JSON 无效'
      return
    }
  }
  bodyMode.value = value
  error.value = ''
}

function addBodyRow() { if (!props.disabled) bodyRows.value.push({ id: rowId('body'), key: '', value: '', valueType: 'text' }) }
function removeBodyRow(index: number) { if (props.disabled) return; bodyRows.value.splice(index, 1); applyObjectBody() }

function patchAuth(key: string, value: unknown) {
  if (props.disabled) return
  emitConfig({ auth: { ...(props.config.auth || { type: 'none', headerName: 'Authorization' }), [key]: value } })
}

function changeMethod(method: string) {
  emitConfig({ method: method || null, ...(method === 'GET' ? {} : { retry: 0 }) })
}
</script>

<template>
  <section class="wf-http-editor" aria-label="HTTP 请求设置">
    <fieldset :disabled="disabled" class="wf-http-editor-fieldset">
    <label>请求方法
      <select :value="config.method || ''" @change="changeMethod(($event.target as HTMLSelectElement).value)">
        <option value="">沿用数据源方法</option><option v-for="method in ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']" :key="method" :value="method">{{ method }}</option>
      </select>
    </label>
    <label>请求 URL（可选模板）
      <input :value="config.url || ''" placeholder="沿用数据源地址；支持 JSONPath 模板" @change="emitConfig({ url: ($event.target as HTMLInputElement).value || null })" />
    </label>

    <section class="wf-http-editor-section">
      <header><div><strong>请求头</strong><small>每行一个名称和值；按固定文本或绑定数据源凭据发送，不引用上游对象模板。</small></div><button type="button" @click="addHeader">添加请求头</button></header>
      <div v-if="headers.length" class="wf-http-row-list">
        <div v-for="row in headers" :key="row.id" class="wf-http-row">
          <label><span>名称</span><input v-model="row.key" placeholder="例如 Accept" @change="commitHeaders" /></label>
          <label><span>值</span><input v-model="row.value" placeholder="例如 application/json；认证请用凭据选择" @change="commitHeaders" /></label>
          <button type="button" class="wf-http-remove" aria-label="删除请求头" title="删除请求头" @click="removeHeader(headers.indexOf(row))">删除</button>
        </div>
      </div>
      <p v-else class="wf-http-empty">暂未覆盖请求头，执行时沿用数据源设置。</p>
    </section>

    <section class="wf-http-editor-section">
      <header><div><strong>查询参数</strong><small>参数值可保留文本、数字、布尔值或空值类型，模板会在运行时渲染。</small></div><button type="button" @click="addParam">添加参数</button></header>
      <div v-if="params.length" class="wf-http-row-list">
        <div v-for="row in params" :key="row.id" class="wf-http-row wf-http-param-row">
          <label><span>名称</span><input v-model="row.key" placeholder="例如 page" @change="commitParams" /></label>
          <label v-if="row.valueType !== 'null'"><span>值</span><input v-model="row.value" :type="row.valueType === 'number' ? 'number' : 'text'" :placeholder="row.valueType === 'unsupported' ? '请改为支持的类型' : '例如 {{ $.page }}'" @change="commitParams" /></label>
          <span v-else class="wf-http-null-value">空值</span>
          <label><span>类型</span><select v-model="row.valueType" @change="commitParams"><option value="text">文本</option><option value="number">数字</option><option value="boolean">布尔</option><option value="null">空值</option><option v-if="row.valueType === 'unsupported'" value="unsupported">旧对象值（请修改）</option></select></label>
          <button type="button" class="wf-http-remove" aria-label="删除查询参数" title="删除查询参数" @click="removeParam(params.indexOf(row))">删除</button>
          <small v-if="row.valueType === 'unsupported'" class="wf-http-row-warning">旧配置包含对象或数组值，请改成文本、数字、布尔或空值。</small>
        </div>
      </div>
      <p v-else class="wf-http-empty">暂未添加查询参数。</p>
    </section>

    <section class="wf-http-editor-section">
      <header><div><strong>请求体</strong><small>{{ bodyModeHelp }}</small></div></header>
      <label>配置方式
        <select :value="bodyMode" @change="changeBodyMode(($event.target as HTMLSelectElement).value as BodyMode)">
          <option value="inherit">沿用数据源请求体</option><option value="object">JSON 对象字段</option><option value="json">JSON 数组或基础值</option>
        </select>
      </label>
      <div v-if="bodyMode === 'object'" class="wf-http-body-object">
        <div v-if="hasBodyRows" class="wf-http-row-list">
          <div v-for="row in bodyRows" :key="row.id" class="wf-http-row wf-http-body-row">
            <label><span>字段名</span><input v-model="row.key" placeholder="例如 sampleId" @change="applyObjectBody" /></label>
            <label v-if="row.valueType !== 'null'"><span>字段值</span><textarea v-model="row.value" :rows="row.valueType === 'json' ? 3 : 1" :placeholder="row.valueType === 'json' ? '{ &quot;value&quot;: 1 }' : '支持 {{ $.sampleId }} 模板；敏感值只能引用绑定凭据'" @change="applyObjectBody" /></label>
            <span v-else class="wf-http-null-value">空值</span>
            <label><span>类型</span><select v-model="row.valueType" @change="applyObjectBody"><option value="text">文本 / 模板</option><option value="number">数字</option><option value="boolean">布尔</option><option value="null">空值</option><option value="json">对象或数组 JSON</option></select></label>
            <button type="button" class="wf-http-remove" aria-label="删除请求体字段" title="删除请求体字段" @click="removeBodyRow(bodyRows.indexOf(row))">删除</button>
          </div>
        </div>
        <p v-else class="wf-http-empty">还没有字段。点击“添加字段”创建 JSON 对象。</p>
        <div class="wf-http-add-row"><button type="button" @click="addBodyRow">添加字段</button><button type="button" @click="applyObjectBody">应用请求体</button></div>
      </div>
      <div v-else-if="bodyMode === 'json'" class="wf-http-body-json">
        <textarea v-model="bodyJson" rows="7" spellcheck="false" aria-label="兼容 JSON 请求体" placeholder='例如 [{ &quot;id&quot;: &quot;{{ $.id }}&quot; }]' />
        <button type="button" @click="applyJsonBody">应用 JSON 请求体</button>
      </div>
    </section>

    <div class="wf-http-inline-fields">
      <label>响应格式<select :value="config.responseFormat || ''" @change="emitConfig({ responseFormat: ($event.target as HTMLSelectElement).value || null })"><option value="">沿用数据源</option><option value="json">JSON</option><option value="text">网页 / 文本</option></select></label>
      <label>超时秒数<input type="number" min="1" max="60" :value="config.timeoutSeconds || ''" @change="emitConfig({ timeoutSeconds: Number(($event.target as HTMLInputElement).value) || null })" /></label>
      <label>读取失败重试<input type="number" min="0" max="5" :disabled="config.method !== 'GET'" :value="config.retry ?? 0" @change="emitConfig({ retry: Number(($event.target as HTMLInputElement).value) })" /><small>选择 GET 后可设置，写请求不自动重发。</small><button v-if="config.method !== 'GET' && Number(config.retry) > 0" type="button" @click="emitConfig({ retry: 0 })">清除旧重试设置</button></label>
    </div>
    <div class="wf-http-inline-fields">
      <label>认证方式<select :value="config.auth?.type || 'none'" @change="patchAuth('type', ($event.target as HTMLSelectElement).value)"><option value="none">沿用数据源请求头</option><option value="bearer">Bearer 凭据</option><option value="basic">Basic 凭据</option><option value="api-key">API Key 凭据</option></select></label>
      <label v-if="config.auth?.type && config.auth.type !== 'none'">凭据名称<input :value="config.auth?.credentialName || ''" @change="patchAuth('credentialName', ($event.target as HTMLInputElement).value || null)" /></label>
      <label v-if="config.auth?.type === 'api-key'">认证请求头<input :value="config.auth?.headerName || 'X-API-Key'" @change="patchAuth('headerName', ($event.target as HTMLInputElement).value)" /></label>
    </div>
    <label class="wf-http-confirm"><input type="checkbox" :checked="config.confirmWrites" @change="emitConfig({ confirmWrites: ($event.target as HTMLInputElement).checked })" />确认该 HTTP 写请求可能产生外部副作用（GET 不需要）</label>
    <p v-if="error" class="wf-error" role="alert">{{ error }}</p>
    <p class="wf-note">请求仍绑定所选 HTTP 数据源，凭据和地址授权继续生效；覆盖项只作用于本次工作流运行。支持 JSON 请求体，留空或选择“沿用数据源请求体”会保留数据源中已保存的请求体。</p>
    </fieldset>
  </section>
</template>

<style scoped>
.wf-http-editor { display: grid; gap: 12px; }
.wf-http-editor-fieldset { display: grid; gap: 12px; min-width: 0; margin: 0; padding: 0; border: 0; }
.wf-http-editor > label, .wf-http-inline-fields > label { display: grid; gap: 5px; color: #344054; font-size: 10px; font-weight: 600; }
.wf-http-editor input:not([type="checkbox"]), .wf-http-editor select, .wf-http-editor textarea { box-sizing: border-box; width: 100%; min-height: 31px; padding: 6px 8px; border: 1px solid #d0d5dd; border-radius: 7px; background: #fff; color: #344054; font: 10px/1.45 inherit; }
.wf-http-editor textarea { resize: vertical; font-family: Consolas, "Microsoft YaHei", monospace; }
.wf-http-editor-section { display: grid; gap: 8px; padding-top: 10px; border-top: 1px solid #eaecf0; }
.wf-http-editor-section > header { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
.wf-http-editor-section > header > div { display: grid; gap: 3px; }
.wf-http-editor-section strong { color: #344054; font-size: 10px; }
.wf-http-editor-section small { color: #98a2b3; font-size: 8px; font-weight: 400; line-height: 13px; }
.wf-http-editor button { min-height: 28px; padding: 4px 8px; border: 1px solid #d0d5dd; border-radius: 7px; background: #fff; color: #475467; font-size: 9px; cursor: pointer; }
.wf-http-editor button:hover { border-color: #84adff; color: #155eef; }
.wf-http-row-list { display: grid; gap: 7px; }
.wf-http-row { display: grid; grid-template-columns: minmax(75px, 1fr) minmax(100px, 1.4fr) auto; align-items: end; gap: 7px; padding: 8px; border: 1px solid #eaecf0; border-radius: 8px; background: #f9fafb; }
.wf-http-param-row, .wf-http-body-row { grid-template-columns: minmax(70px, 1fr) minmax(80px, 1.3fr) minmax(78px, .8fr) auto; }
.wf-http-row label { display: grid; gap: 4px; min-width: 0; }
.wf-http-row label > span { color: #667085; font-size: 8px; font-weight: 500; }
.wf-http-row .wf-http-remove { min-width: 36px; padding-inline: 5px; color: #b42318; }
.wf-http-empty { margin: 0; padding: 8px; border: 1px dashed #d0d5dd; color: #98a2b3; font-size: 9px; line-height: 14px; text-align: center; }
.wf-http-null-value { align-self: end; min-height: 31px; padding: 7px 8px; border: 1px solid #eaecf0; border-radius: 7px; background: #f2f4f7; color: #667085; font-size: 9px; }
.wf-http-row-warning { grid-column: 1 / -1; color: #b54708 !important; }
.wf-http-body-object, .wf-http-body-json { display: grid; gap: 8px; }
.wf-http-add-row { display: flex; flex-wrap: wrap; gap: 6px; }
.wf-http-inline-fields { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.wf-http-confirm { display: flex !important; grid-template-columns: none !important; align-items: center; gap: 7px; color: #667085 !important; font-size: 9px !important; font-weight: 400 !important; }
.wf-http-confirm input { width: 16px; height: 16px; accent-color: #155eef; }
.wf-http-editor .wf-error { margin: 0; color: #b42318; font-size: 9px; line-height: 14px; }
.wf-http-editor .wf-note { margin: 0; color: #98a2b3; font-size: 8px; line-height: 13px; }
@media (max-width: 520px) { .wf-http-row, .wf-http-param-row, .wf-http-body-row, .wf-http-inline-fields { grid-template-columns: 1fr; } .wf-http-row .wf-http-remove { justify-self: start; } }
</style>
