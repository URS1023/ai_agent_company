<script setup lang="ts">
/**
 * HTTP 采集源配置表单（新建/编辑）。
 * 路由：无独立路由，由 HttpSourcesManager 在"数据源中心 → HTTP 接口"弹窗内渲染。
 * 依赖接口：本组件不发请求；保存时把 HttpSourceInput 交给父组件（httpCollection.save）。
 * 关键交互：请求头/请求体用 JSON 文本编辑，提交前统一解析校验（非法 JSON 会给出中文提示且不提交）；
 *   批量请求体开关在切换时尝试保持原有 JSON 结构（解析失败则保留原草稿供修正）；
 *   凭据三态处理：保持现有 / 全部替换 / 清空（替换时必须填全且名称不重复，凭据只加密保存不回显）；
 *   responseFormat='text' 时清空 recordPath 与字段（文本源的字段由后续代码节点提取）；
 *   GET 会自动去掉分页（分页仅对 GET 有效，POST 批量请求不接受分页参数）。
 * 权限：允许访问内网（allowPrivate）需 can('system.admin')；POST 允许自动调用需 can('datasources.publish')。
 * 错误呈现：error（父组件传入）与 localError（本地校验）合并展示。
 */
import { reactive, shallowRef } from 'vue'
import { can } from '../../api/request'
import type { HttpSource, HttpSourceInput } from '../../api/collection'
import HttpFieldSelection from './HttpFieldSelection.vue'
import HttpPaginationFields from './HttpPaginationFields.vue'
const props = defineProps<{ source: HttpSource | null; busy: boolean; error: string }>()
const emit = defineEmits<{ save: [payload: HttpSourceInput]; cancel: [] }>()
const initial: HttpSourceInput = { name: '', url: '', method: 'GET', headers: {}, bodies: [null], recordPath: '$', fields: [], allowPrivate: false, allowAutomatic: false, timeoutSeconds: 15, batchTimeoutSeconds: 60, maxRows: 1000, continueOnError: false, enabled: true, description: '' }
initial.responseFormat = 'json'
initial.pagination = null
if (props.source) for (const key of Object.keys(initial) as (keyof HttpSourceInput)[]) Object.assign(initial, { [key]: props.source[key] })
const form = reactive<HttpSourceInput>(JSON.parse(JSON.stringify(initial)))
const headersText = shallowRef(JSON.stringify(form.headers, null, 2))
const batch = shallowRef(form.bodies.length > 1)
const bodyText = shallowRef(JSON.stringify(batch.value ? form.bodies : form.bodies[0], null, 2))
const credentialMode = shallowRef('preserve')
const credentials = reactive([{ name: '', value: '' }])
const localError = shallowRef('')
const invalidFields = shallowRef(false)
/** 切换批量/单条模式：尽量保持已填写的 JSON 结构；解析失败时保留原草稿（由用户手工修正）。 */
function toggleBatch() {
  try { const value = JSON.parse(bodyText.value); bodyText.value = JSON.stringify(batch.value ? [value] : (Array.isArray(value) && value.length === 1 ? value[0] : value), null, 2) } catch { /* Preserve invalid draft for correction. */ }
}
/**
 * 提交前校验与组装：请求头必须是"名称→文本值"的 JSON 对象；POST 的请求体必须是合法 JSON（批量模式要求数组）；
 * 凭据按三态处理（preserve 不动、replace 全量替换且校验非空/不重复、clear 传空对象）。
 * 失败表现：只写 localError，不向上抛（表单保持可编辑）。
 */
function save() {
  localError.value = ''
  try {
    const headers = JSON.parse(headersText.value)
    if (!headers || Array.isArray(headers) || typeof headers !== 'object' || Object.values(headers).some(value => typeof value !== 'string')) throw new Error('请求头应为名称到文本值的 JSON 对象')
    let bodies: unknown[] = [null]
    if (form.method === 'POST') {
      const parsed = JSON.parse(bodyText.value)
      if (batch.value && !Array.isArray(parsed)) throw new Error('批量模式的请求体应为 JSON 数组，每项代表一次请求')
      bodies = batch.value ? parsed : [parsed]
    }
    const payload: HttpSourceInput = { ...form, headers, bodies, version: props.source?.version, credentials: null }
    if (form.method !== 'GET') payload.pagination = null
    if (form.responseFormat === 'text') { payload.recordPath = '$'; payload.fields = [] }
    if (credentialMode.value === 'clear') payload.credentials = {}
    if (credentialMode.value === 'replace') {
      if (credentials.some(row => !row.name.trim() || !row.value) || new Set(credentials.map(row => row.name.trim())).size !== credentials.length) throw new Error('凭据名称和值不能为空，名称不能重复；替换时需填写全部引用的凭据')
      payload.credentials = Object.fromEntries(credentials.map(row => [row.name.trim(), row.value]))
    }
    emit('save', payload)
  } catch (cause) { localError.value = cause instanceof SyntaxError ? '请求头或请求体不是合法 JSON，请检查引号和逗号' : (cause as Error).message }
}
</script>

<template>
  <form class="http-form" @submit.prevent="save">
    <p v-if="error || localError" role="alert" class="http-error">{{ error || localError }}</p>
    <fieldset :disabled="busy"><legend>基本信息</legend><div class="http-form-grid">
      <label>数据源名称<input v-model="form.name" maxlength="200" required placeholder="例如：生产设备运行指标" /></label>
      <label>请求方式<select v-model="form.method"><option>GET</option><option>POST</option></select></label>
      <label>响应格式<select v-model="form.responseFormat"><option value="json">JSON 数据</option><option value="text">网页 / 文本</option></select></label>
      <label class="http-full">接口地址<input v-model="form.url" type="url" required maxlength="2000" placeholder="https://设备服务/api/metrics" /></label>
      <label class="http-full">用途说明<textarea v-model="form.description" rows="2" maxlength="2000" placeholder="数据口径、设备范围和接口用途" /></label>
    </div></fieldset>
    <fieldset :disabled="busy"><legend>请求配置</legend>
      <HttpPaginationFields v-if="form.method === 'GET'" v-model="form.pagination" />
      <label>请求头 · JSON 对象<textarea v-model="headersText" rows="3" spellcheck="false" /></label>
      <p class="http-note">认证示例：Authorization 使用 Bearer ${secret.access}，在下方凭据中设置 access。不要在地址或请求头里填写明文密钥。</p>
      <template v-if="form.method === 'POST'"><label class="http-check"><input v-model="batch" type="checkbox" @change="toggleBatch" /> 批量请求体（每项单独发送一次）</label>
        <label>{{ batch ? '批量请求体 · JSON 数组' : '单条请求体 · JSON' }}<textarea v-model="bodyText" rows="5" spellcheck="false" /></label>
        <p class="http-note">最多 100 条、合计 1 MB。保存不会调用接口，手动采集前需确认；失败不会自动重试。</p><details><summary>质检工作流动态参数</summary><p class="http-note">保存一条 POST 模板，将 JSON 值完整写为 ${input.sampleId}；外观图片可用 ${input.imageBase64}、${input.imageMimeType}、${input.imageSha256}、${input.imageWidth}、${input.imageHeight}、${input.imageOrientation}。在质检节点选择“上游样本逐条绑定”。目标地址与请求头不能使用动态参数，开启自动采集表示授权此模板。展开后单条最多 8 MB、整批最多 16 MB。</p></details>
      </template>
      <details><summary>加密凭据{{ source?.hasCredentials ? ' · 已配置 ' + source.credentialNames.join('、') : '' }}</summary>
        <label>凭据处理<select v-model="credentialMode"><option value="preserve">保持现有凭据</option><option value="replace">设置 / 替换全部凭据</option><option value="clear">清空凭据</option></select></label>
        <template v-if="credentialMode === 'replace'"><div v-for="(credential, index) in credentials" :key="index" class="http-credential-row">
          <label>凭据名称<input v-model="credential.name" autocomplete="off" placeholder="access" /></label><label>凭据值<input v-model="credential.value" type="password" autocomplete="new-password" /></label>
          <button type="button" :disabled="credentials.length === 1" @click="credentials.splice(index, 1)">移除</button>
        </div><button type="button" :disabled="credentials.length >= 30" @click="credentials.push({ name: '', value: '' })">添加凭据</button></template>
        <p class="http-note">凭据只加密保存、不回显；请求或响应日志不记录其原文。替换时请填写全部引用项。</p>
      </details>
    </fieldset>
    <fieldset v-if="form.responseFormat !== 'text'" :disabled="busy"><legend>记录与字段</legend>
      <label>记录 JSONPath<input v-model="form.recordPath" required placeholder="例如：$.data.items[*]" /></label>
      <HttpFieldSelection v-model="form.fields" :record-path="form.recordPath" :disabled="busy" @invalid="invalidFields = $event" />
    </fieldset>
    <p v-else class="http-note">每页返回一条记录：body 为网页或文本原文，contentType 为内容类型，url 为该页地址。可在后续代码节点中提取字段；网页脚本不会在页面内执行。</p>
    <fieldset :disabled="busy"><legend>运行与使用范围</legend><div class="http-form-grid http-limits">
      <label>单条超时（秒）<input v-model.number="form.timeoutSeconds" type="number" min="1" max="60" required /></label>
      <label>整批超时（秒）<input v-model.number="form.batchTimeoutSeconds" type="number" min="1" max="120" required /></label>
      <label>整批最大行数<input v-model.number="form.maxRows" type="number" min="1" max="10000" required /></label>
    </div><div class="http-checks">
      <label class="http-check"><input v-model="form.continueOnError" type="checkbox" /> 单条失败后继续其他请求</label>
      <label class="http-check"><input v-model="form.enabled" type="checkbox" /> 启用数据源</label>
      <label class="http-check"><input v-model="form.allowPrivate" type="checkbox" :disabled="!can('system.admin')" /> 允许访问此本机 / 内网设备（管理员授权）</label>
      <label class="http-check"><input v-model="form.allowAutomatic" type="checkbox" :disabled="form.method === 'POST' && !can('datasources.publish')" /> 确认仅查询数据，允许报表自动调用</label>
    </div><p class="http-note">POST 自动调用需数据源发布权限；更改已批准的请求需重新校验。上游重定向和基础设施元数据地址始终禁止。</p></fieldset>
    <div class="http-form-footer"><button type="button" :disabled="busy" @click="emit('cancel')">取消</button><button class="http-primary" type="submit" :disabled="busy || (form.responseFormat !== 'text' && invalidFields)">{{ busy ? '正在保存…' : source ? '保存修改' : '创建数据源' }}</button></div>
  </form>
</template>
