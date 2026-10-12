<script setup lang="ts">
/**
 * 策略选择器（工作流 alert-evaluate 节点用）：选择预警策略并固定其修订版本，必要时可就地新建/编辑策略。
 * 路由：无独立路由，由工作流节点表单（WorkflowNodeForm）在需要 ruleId 的节点上渲染。
 * 依赖接口：api/alerts.ts（alerts.list('rules')、alerts.get/save('rules')、points/advice 候选项）。
 * 关键交互：选择策略时同时写回 ruleId 与 ruleRevision（两者成对，缺一会导致运行时报"修订不存在"）；
 *   设备被节点绑定时只允许选该设备下的策略；内嵌编辑器保存后需重新搜索以刷新修订号。
 * 加载/取消：搜索请求持有单个 AbortController，翻页/改关键字先 abort 上一次。
 * 权限：编辑入口按 can('alerts.edit' | 'alerts.create') 控制；无权限时只读选择。
 * 错误呈现：error 就近显示在面板内，不弹全局消息。
 */
import { computed, onBeforeUnmount, shallowRef, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { alerts, emptyAlertRecord, type AlertRecord } from '../../api/alerts'
import { can } from '../../api/request'
import AlertRuleForm from './AlertRuleForm.vue'

const props = defineProps<{
  config: Record<string, any>
  disabled: boolean
  device?: AlertRecord
}>()
const emit = defineEmits<{ update: [config: Record<string, any>] }>()
const query = shallowRef('')
const offset = shallowRef(0)
const loading = shallowRef(false)
const error = shallowRef('')
const choices = shallowRef<AlertRecord[]>([])
const current = shallowRef<AlertRecord | null>(null)
const revisions = shallowRef<AlertRecord[]>([])
const editor = shallowRef<AlertRecord | null>(null)
const editorLoading = shallowRef(false)
const editorSaving = shallowRef(false)
const points = shallowRef<AlertRecord[]>([])
const advice = shallowRef<AlertRecord[]>([])
const boundDeviceId = computed(() => String(props.device?.id || props.config.deviceId || ''))
const editorDevices = computed(() => props.device ? [props.device] : [])
let request: AbortController | null = null
let stopped = false
function closeEditor() { editor.value = null }

/** 搜索策略：limit 固定 30 按 offset 翻页；绑定设备时用 deviceId 过滤（跨设备策略不能用于当前节点）。 */
async function search(next = 0) {
  request?.abort()
  const active = new AbortController()
  request = active
  loading.value = true
  error.value = ''
  try {
    const response = await alerts.list('rules', {
      keyword: query.value,
      limit: 30,
      offset: next,
      ...(boundDeviceId.value ? { deviceId: boundDeviceId.value } : {}),
    }, active.signal)
    if (!stopped && active === request) {
      choices.value = response.data
      offset.value = next
    }
  } catch (cause) {
    if (!active.signal.aborted) error.value = (cause as Error).message
  } finally {
    if (active === request) loading.value = false
  }
}

/** 选中策略：ruleId 与 ruleRevision 必须同时写回（修订号为"固定版本"语义，策略后续编辑不影响已发布流程）。 */
function choose(id: string) {
  const selected = choices.value.find(item => item.id === id) || (current.value?.id === id ? current.value : null)
  if (!selected) return
  emit('update', {
    ...props.config,
    ...(boundDeviceId.value ? { deviceId: boundDeviceId.value } : {}),
    ruleId: selected.id,
    ruleRevision: selected.version,
  })
}

/** 编辑器资源：只取当前设备的测点（200 条上限）；专家经验取最近 200 条用于关联。 */
async function loadEditorResources() {
  if (!boundDeviceId.value) return
  const [loadedPoints, loadedAdvice] = await Promise.all([
    alerts.list('points', { deviceId: boundDeviceId.value, limit: 200 }),
    alerts.list('advice', { limit: 200 }),
  ])
  points.value = loadedPoints.data
  advice.value = loadedAdvice.data
}

async function openEditor(editing: boolean) {
  if (!boundDeviceId.value || editorLoading.value) return
  editorLoading.value = true
  error.value = ''
  try {
    await loadEditorResources()
    let row = editing ? current.value : null
    if (editing && !row && props.config.ruleId)
      row = (await alerts.get('rules', props.config.ruleId)).data
    editor.value = row
      ? JSON.parse(JSON.stringify(row))
      : {
          ...emptyAlertRecord('rules'),
          deviceId: boundDeviceId.value,
          name: `${props.device?.name || '设备'}预警策略`,
        }
    editor.value!.deviceId = boundDeviceId.value
  } catch (cause) {
    error.value = (cause as Error).message
  } finally {
    editorLoading.value = false
  }
}

async function saveEditor() {
  if (!editor.value || editorSaving.value) return
  editorSaving.value = true
  error.value = ''
  try {
    editor.value.deviceId = boundDeviceId.value
    const saved = (await alerts.save('rules', editor.value)).data
    current.value = saved
    choices.value = [saved, ...choices.value.filter(item => item.id !== saved.id)]
    emit('update', {
      ...props.config,
      deviceId: boundDeviceId.value,
      ruleId: saved.id,
      ruleRevision: saved.version,
    })
    revisions.value = (await alerts.revisions(saved.id!)).data
    closeEditor()
    ElMessage.success('预警策略已保存并绑定到当前节点')
  } catch (cause) {
    error.value = (cause as Error).message
  } finally {
    editorSaving.value = false
  }
}

watch([() => props.config.ruleId, boundDeviceId], async ([id], _, cleanup) => {
  let stale = false
  cleanup(() => { stale = true })
  current.value = null
  revisions.value = []
  if (!id || !can('alerts.view')) return
  try {
    const [rule, versions] = await Promise.all([alerts.get('rules', String(id)), alerts.revisions(String(id))])
    if (!stale && !stopped) {
      current.value = rule.data
      revisions.value = versions.data
      if (boundDeviceId.value && rule.data.deviceId !== boundDeviceId.value)
        error.value = '当前策略属于其他设备，请选择或新建当前设备的策略。'
    }
  } catch (cause) {
    if (!stale && !stopped) error.value = (cause as Error).message
  }
}, { immediate: true })

watch(boundDeviceId, () => {
  closeEditor()
  if (can('alerts.view')) void search()
}, { immediate: true })
onBeforeUnmount(() => { stopped = true; request?.abort() })
</script>

<template>
  <div class="alert-policy-select wf-inline-resource">
    <div v-if="device" class="wf-bound-resource">
      <small>当前设备</small>
      <strong>{{ device.name }}</strong>
      <span>{{ device.code }}{{ device.location ? ` · ${device.location}` : '' }}</span>
    </div>
    <p v-if="current" class="wf-current-resource"><span>当前策略</span><strong>{{ current.name }}</strong><small>固定 v{{ config.ruleRevision }}</small></p>
    <p v-if="!can('alerts.view')" class="wf-note">需要设备预警读取权限才能选择策略。</p>
    <template v-else>
      <label>预警策略
        <select :value="config.ruleId" :disabled="disabled || loading" @change="choose(($event.target as HTMLSelectElement).value)">
          <option value="">请选择策略</option>
          <option v-if="current && !choices.some(item => item.id === current?.id)" :value="current.id">{{ current.name }} · v{{ current.version }}</option>
          <option v-for="item in choices" :key="item.id" :value="item.id">{{ item.name }} · v{{ item.version }}</option>
        </select>
      </label>
      <label class="wf-inline-search">查找策略<input v-model="query" :disabled="disabled" placeholder="输入策略名称" @keydown.enter.prevent="search(0)" /></label>
      <div class="wf-inline-search-actions">
        <button type="button" :disabled="disabled || loading" @click="search(0)">查找</button>
        <button type="button" :disabled="disabled || loading || !offset" @click="search(offset - 30)">上一页</button>
        <button type="button" :disabled="disabled || loading || choices.length < 30" @click="search(offset + 30)">下一页</button>
      </div>
      <label v-if="current">固定策略修订
        <select :value="config.ruleRevision" :disabled="disabled" @change="$emit('update', { ...config, deviceId: boundDeviceId || undefined, ruleRevision: Number(($event.target as HTMLSelectElement).value) })">
          <option v-for="item in revisions" :key="item.revision" :value="item.revision">v{{ item.revision }} · {{ item.createTime }}</option>
        </select>
      </label>
      <div v-if="boundDeviceId" class="wf-inline-resource-actions">
        <button v-if="can('alerts.create')" type="button" class="wf-primary" :disabled="disabled || editorLoading" @click="openEditor(false)"><i class="fa-solid fa-plus" />新建策略</button>
        <button v-if="current && can('alerts.edit')" type="button" :disabled="disabled || editorLoading" @click="openEditor(true)"><i class="fa-regular fa-pen-to-square" />编辑当前策略</button>
      </div>
      <form v-if="editor" class="wf-inline-editor" @submit.prevent="saveEditor">
        <header><div><strong>{{ editor.id ? '编辑当前策略' : '新建预警策略' }}</strong><small>保存后自动绑定本节点，无需离开工作流</small></div><button type="button" aria-label="关闭策略编辑" @click="closeEditor"><i class="fa-solid fa-xmark" /></button></header>
        <AlertRuleForm v-model="editor" :devices="editorDevices" :advice="advice" :points="points" :locked-device-id="boundDeviceId" :disabled="disabled || editorSaving" />
        <footer><button type="button" :disabled="editorSaving" @click="closeEditor">取消</button><button type="submit" class="wf-primary" :disabled="editorSaving">{{ editorSaving ? '保存中…' : '保存并使用' }}</button></footer>
      </form>
      <p class="wf-note">策略在这里创建、编辑和选用；运行会生成可追溯的预警报告与问题。</p>
    </template>
    <p v-if="error" class="wf-error" role="alert">{{ error }}</p>
  </div>
</template>
