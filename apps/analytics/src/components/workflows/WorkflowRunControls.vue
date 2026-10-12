<script setup lang="ts">
/**
 * WorkflowRunControls —— 运行中的控制条：等待输入的应答提交、延迟等待的倒计时、取消运行。
 *
 * 位置：WorkflowRunResult 顶部（调试面板 / 日志页结果 / 版本与运行弹窗共用）。
 * 依赖接口：`api/workflows.FlowRun`（run.result.waiting 描述等待类型、字段定义与 resumeAt）。
 * 关键约束：
 * - 只渲染 waiting / running / cancel_requested 三种状态的控件，其他状态不显示；
 * - 结构化字段需按类型转换后提交：number 必须可解析、json_object 必须是对象，
 *   转换失败只提示错误且不提交（避免把无效输入写进运行快照）；
 * - 必填校验在提交时二次执行，前端禁用态不能作为唯一防线；
 * - 等待节点或运行变化时重置答案与错误，防止把上一次提问的答案提交给新节点；
 * - resumeAt 到点前不允许提交（倒计时每秒刷新 clock），避免用户抢在服务端可恢复时间之前提交。
 */
import { computed, onBeforeUnmount, onMounted, reactive, shallowRef, watch } from 'vue'
import type { FlowRun } from '../../api/workflows'

const props = defineProps<{ run: FlowRun; busy: boolean }>()
const emit = defineEmits<{ resume: [input: unknown]; cancel: [] }>()
const answer = shallowRef('')
const structuredAnswer = reactive<Record<string, any>>({})
const answerError = shallowRef('')
const clock = shallowRef(Date.now())
let timer = 0
const waiting = computed(() => props.run.result?.waiting || {})
const fields = computed(() => waiting.value.fields || [])
/** 切换等待节点时清空作答与错误，避免上一次提问的答案被提交到新节点。 */
watch([() => props.run.id, () => waiting.value.nodeId], () => {
  answer.value = ''
  answerError.value = ''
  Object.keys(structuredAnswer).forEach(key => delete structuredAnswer[key])
  fields.value.forEach(field => { structuredAnswer[field.name] = field.type === 'checkbox' ? false : '' })
}, { immediate: true })
const delayReady = computed(() => !waiting.value.resumeAt || clock.value >= Date.parse(waiting.value.resumeAt))
/** 必填校验：checkbox 必须勾选，其他类型必须非空（字符串需 trim 后非空）。 */
const structuredReady = computed(() => fields.value.every(field => {
  const value = structuredAnswer[field.name]
  if (field.type === 'checkbox') return !field.required || value === true
  return !field.required || (value !== undefined && value !== null && (typeof value !== 'string' || value.trim() !== ''))
}))
/**
 * 提交应答：无字段定义时按纯文本回复（兼容 question 节点的自由问答），
 * 有字段时按类型组装对象；任何转换失败都停在本地并提示，不提交半成品。
 */
function submitAnswer() {
  if (!fields.value.length) {
    emit('resume', answer.value)
    return
  }
  try {
    const output: Record<string, unknown> = {}
    fields.value.forEach(field => {
      const value = structuredAnswer[field.name]
      if (field.type === 'number') {
        if (value === '' || value === null || value === undefined) return
        const number = Number(value)
        if (!Number.isFinite(number)) throw new Error(`${field.label || field.name}必须是数值`)
        output[field.name] = number
      } else if (field.type === 'json' || field.type === 'json_object') {
        if (value === '' || value === null || value === undefined) return
        const parsed = JSON.parse(String(value))
        if (field.type === 'json_object' && (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))) throw new Error(`${field.label || field.name}必须是 JSON 对象`)
        output[field.name] = parsed
      } else {
        output[field.name] = value
      }
    })
    if (!structuredReady.value) throw new Error('请先填写所有必填字段')
    answerError.value = ''
    emit('resume', output)
  } catch (cause) {
    answerError.value = cause instanceof Error ? cause.message : '提问输入无效'
  }
}
onMounted(() => { timer = window.setInterval(() => { clock.value = Date.now() }, 1000) })
onBeforeUnmount(() => window.clearInterval(timer))
</script>

<template>
  <section v-if="['waiting', 'running', 'cancel_requested'].includes(run.status)" class="wf-run-controls" aria-label="运行控制">
    <template v-if="run.status === 'waiting'">
      <template v-if="waiting.kind === 'question'">
        <p>{{ waiting.prompt || '请补充输入' }}</p>
        <template v-if="fields.length">
          <label v-for="field in fields" :key="field.name">
            {{ field.label || field.name }}<span v-if="field.required"> *</span>
            <select v-if="field.type === 'select'" v-model="structuredAnswer[field.name]" :disabled="busy"><option value="">请选择</option><option v-for="option in field.options || []" :key="option" :value="option">{{ option }}</option></select>
            <input v-else-if="field.type === 'number'" v-model="structuredAnswer[field.name]" type="number" :placeholder="field.placeholder" :disabled="busy" />
            <input v-else-if="field.type === 'checkbox'" v-model="structuredAnswer[field.name]" type="checkbox" :disabled="busy" />
            <textarea v-else v-model="structuredAnswer[field.name]" :rows="field.type === 'paragraph' || field.type === 'json' || field.type === 'json_object' ? 5 : 2" :placeholder="field.placeholder" :disabled="busy" />
          </label>
          <p v-if="answerError" class="wf-error" role="alert">{{ answerError }}</p>
          <button class="wf-primary" :disabled="busy || !structuredReady" @click="submitAnswer">提交并继续</button>
        </template>
        <template v-else>
          <label><textarea v-model="answer" rows="3" :disabled="busy" /></label>
          <button class="wf-primary" :disabled="busy || (waiting.required && !answer.trim())" @click="submitAnswer">提交并继续</button>
        </template>
      </template>
      <template v-else>
        <p>等待至 {{ waiting.resumeAt || '指定时间' }}</p>
        <button class="wf-primary" :disabled="busy || !delayReady" @click="emit('resume', null)">{{ delayReady ? '继续运行' : '尚未到期' }}</button>
      </template>
    </template>
    <p v-else>{{ run.status === 'cancel_requested' ? '正在等待当前节点安全结束。' : '运行正在执行，可请求在当前节点后停止。' }}</p>
    <button v-if="run.status !== 'cancel_requested'" class="wf-danger" :disabled="busy" @click="emit('cancel')">取消运行</button>
  </section>
</template>
