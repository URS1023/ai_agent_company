<script setup lang="ts">
/**
 * 质检模板内联选择器（工作流 qc-evaluate 节点在已绑定设备时使用）。
 * 路由：无独立路由，由 QualityNodeForm 在 kind='qc-evaluate' 且传入 device 时渲染。
 * 依赖接口：api/quality.ts 的 quality.list('templates') / quality.get / quality.save('metrics'|'templates')。
 * 关键交互：选择模板时把 templateId 与 templateRevision 成对写回（模板修订号必须固定，否则历史标准会漂移）；
 *   可就地新建/编辑模板与指标：新建指标成功后会立即加入当前模板草稿（不会自动保存模板）。
 * 加载/取消：模板列表请求持有单个 AbortController，搜索/翻页先 abort 上一次。
 * 权限：保存指标/模板受 can('quality.create' | 'quality.edit') 约束；无权限时编辑器只读/隐藏入口。
 * 错误呈现：error 内联展示；成功保存指标额外用 ElMessage 提示。
 */
import { computed, onBeforeUnmount, shallowRef, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { can } from '../../api/request'
import { emptyQualityRecord, quality, type QualityRecord } from '../../api/quality'
import QualityMetricForm from './QualityMetricForm.vue'
import QualityTemplateForm from './QualityTemplateForm.vue'

const props = defineProps<{
  config: Record<string, any>
  device: Record<string, any>
  disabled: boolean
}>()
const emit = defineEmits<{ update: [config: Record<string, any>] }>()
const rows = shallowRef<QualityRecord[]>([])
const selected = shallowRef<QualityRecord | null>(null)
const query = shallowRef('')
const offset = shallowRef(0)
const loading = shallowRef(false)
const saving = shallowRef(false)
const error = shallowRef('')
const templateEditor = shallowRef<QualityRecord | null>(null)
const metricEditor = shallowRef<QualityRecord | null>(null)
let controller: AbortController | null = null
function closeMetric() { metricEditor.value = null }
function closeTemplate() { templateEditor.value = null; closeMetric() }

/** 可选模板：只保留启用状态、且设备类别匹配（模板未限定类别或设备无类别时视为兼容）的模板。 */
const compatibleRows = computed(() => rows.value.filter(row =>
  row.status === 'active' && (!row.deviceType || !props.device.category || row.deviceType === props.device.category),
))

async function load(next = 0) {
  controller?.abort()
  const active = new AbortController()
  controller = active
  loading.value = true
  error.value = ''
  try {
    const response = await quality.list('templates', { keyword: query.value, limit: 30, offset: next }, active.signal)
    if (!active.signal.aborted) {
      rows.value = response.data
      offset.value = next
    }
  } catch (cause) {
    if (!active.signal.aborted) error.value = (cause as Error).message
  } finally {
    if (!active.signal.aborted) loading.value = false
  }
}

/** 选择模板：写回 templateId + templateRevision，并自动补上当前设备 id（避免节点配置缺设备）。 */
function choose(id: string) {
  const row = rows.value.find(item => item.id === id) || (selected.value?.id === id ? selected.value : null)
  if (!row) return
  selected.value = row
  emit('update', {
    ...props.config,
    deviceId: props.device.id,
    templateId: row.id,
    templateRevision: row.version,
  })
}

async function openTemplate(editing: boolean) {
  error.value = ''
  try {
    let row = editing ? selected.value : null
    if (editing && !row && props.config.templateId)
      row = (await quality.get('templates', props.config.templateId)).data
    templateEditor.value = row
      ? JSON.parse(JSON.stringify(row))
      : {
          ...emptyQualityRecord('templates'),
          name: `${props.device.name || '设备'}检验模板`,
          deviceType: props.device.category || '',
        }
    closeMetric()
  } catch (cause) {
    error.value = (cause as Error).message
  }
}

/**
 * 新建指标并加入当前模板草稿：先保存指标（POST metrics）再本地追加到 metricIds（不自动保存模板，
 * 由用户在模板弹窗里确认保存，避免误改已发布标准）。
 */
async function saveMetric() {
  if (!metricEditor.value || !templateEditor.value || saving.value) return
  saving.value = true
  error.value = ''
  try {
    const saved = (await quality.save('metrics', metricEditor.value)).data
    if (!templateEditor.value.metricIds.includes(saved.id))
      templateEditor.value = { ...templateEditor.value, metricIds: [...templateEditor.value.metricIds, saved.id] }
    closeMetric()
    ElMessage.success('检验指标已创建并加入当前模板')
  } catch (cause) {
    error.value = (cause as Error).message
  } finally {
    saving.value = false
  }
}

async function saveTemplate() {
  if (!templateEditor.value || saving.value) return
  saving.value = true
  error.value = ''
  try {
    const saved = (await quality.save('templates', templateEditor.value)).data
    selected.value = saved
    rows.value = [saved, ...rows.value.filter(row => row.id !== saved.id)]
    emit('update', {
      ...props.config,
      deviceId: props.device.id,
      templateId: saved.id,
      templateRevision: saved.version,
    })
    closeTemplate()
    ElMessage.success('检验模板已保存并绑定到当前节点')
  } catch (cause) {
    error.value = (cause as Error).message
  } finally {
    saving.value = false
  }
}

watch(() => props.config.templateId, async (id, _, cleanup) => {
  selected.value = null
  if (!id) return
  const active = new AbortController()
  cleanup(() => active.abort())
  try {
    const response = await quality.get('templates', id, active.signal)
    if (!active.signal.aborted) selected.value = response.data
  } catch (cause) {
    if (!active.signal.aborted) error.value = (cause as Error).message
  }
}, { immediate: true })
void load()
onBeforeUnmount(() => controller?.abort())
</script>

<template>
  <div class="wf-inline-resource quality-inline-template">
    <div class="wf-bound-resource">
      <small>当前设备</small>
      <strong>{{ device.name }}</strong>
      <span>{{ device.code }}{{ device.category ? ` · ${device.category}` : '' }}</span>
    </div>
    <p v-if="selected" class="wf-current-resource"><span>当前模板</span><strong>{{ selected.name }}</strong><small>固定 v{{ config.templateRevision }}</small></p>
    <label>检验模板
      <select :value="config.templateId" :disabled="disabled || loading" @change="choose(($event.target as HTMLSelectElement).value)">
        <option value="">请选择模板</option>
        <option v-if="selected && !compatibleRows.some(row => row.id === selected?.id)" :value="selected.id">{{ selected.name }} · v{{ selected.version }}</option>
        <option v-for="row in compatibleRows" :key="row.id" :value="row.id">{{ row.name }} · v{{ row.version }}</option>
      </select>
    </label>
    <label class="wf-inline-search">查找模板<input v-model="query" :disabled="disabled" placeholder="输入模板名称" @keydown.enter.prevent="load(0)" /></label>
    <div class="wf-inline-search-actions">
      <button type="button" :disabled="disabled || loading" @click="load(0)">查找</button>
      <button type="button" :disabled="disabled || loading || !offset" @click="load(offset - 30)">上一页</button>
      <button type="button" :disabled="disabled || loading || rows.length < 30" @click="load(offset + 30)">下一页</button>
    </div>
    <div class="wf-inline-resource-actions">
      <button v-if="can('quality.create')" type="button" class="wf-primary" :disabled="disabled" @click="openTemplate(false)"><i class="fa-solid fa-plus" />新建模板</button>
      <button v-if="selected && can('quality.edit')" type="button" :disabled="disabled" @click="openTemplate(true)"><i class="fa-regular fa-pen-to-square" />编辑当前模板</button>
    </div>
    <form v-if="templateEditor" class="wf-inline-editor" @submit.prevent="saveTemplate">
      <header><div><strong>{{ templateEditor.id ? '编辑检验模板' : '新建检验模板' }}</strong><small>指标和模板均可在节点内完成</small></div><button type="button" aria-label="关闭模板编辑" @click="closeTemplate"><i class="fa-solid fa-xmark" /></button></header>
      <QualityTemplateForm v-model="templateEditor" :disabled="disabled || saving" />
      <div class="wf-inline-resource-actions"><button v-if="can('quality.create')" type="button" :disabled="disabled || saving" @click="metricEditor = { ...emptyQualityRecord('metrics'), name: `${device.name || '设备'}检验指标` }"><i class="fa-solid fa-plus" />新建指标并加入模板</button></div>
      <section v-if="metricEditor" class="wf-inline-subeditor">
        <header><strong>新建检验指标</strong><button type="button" aria-label="关闭指标编辑" @click="closeMetric"><i class="fa-solid fa-xmark" /></button></header>
        <QualityMetricForm v-model="metricEditor" :disabled="disabled || saving" />
        <div class="wf-inline-search-actions"><button type="button" :disabled="saving" @click="closeMetric">取消</button><button type="button" class="wf-primary" :disabled="saving" @click="saveMetric">{{ saving ? '保存中…' : '保存并加入模板' }}</button></div>
      </section>
      <footer><button type="button" :disabled="saving" @click="closeTemplate">取消</button><button type="submit" class="wf-primary" :disabled="saving">{{ saving ? '保存中…' : '保存并使用' }}</button></footer>
    </form>
    <p class="wf-note">模板与指标都在此节点创建和维护，无需跳转到其他模块。</p>
    <p v-if="error" class="wf-error" role="alert">{{ error }}</p>
  </div>
</template>
