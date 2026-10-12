<script setup lang="ts">
/**
 * "使用大屏模板"弹窗（选择生成方式与数据源，产出 TemplateUseRequest）。
 * 路由：无独立路由，由 TemplateCenter（/templates）以 v-model 控制显隐，submit 结果由页面去调用生成接口。
 * 依赖接口：无请求（数据源列表由父组件注入）；请求体形状由本文件导出的 TemplateUseRequest 约定，
 *   requirementDocument.schemaVersion 固定 'dashboard-requirement-v1'，必须与后端校验保持一致。
 * 关键交互：mode='empty' 只建空数据报表；mode='requirement' 需要填写需求文档（目标/指标/维度/筛选/时间范围），
 *   由后端 AI 仅填充数据绑定；表单校验失败用 ElMessage 提示且不 emit submit。
 * 约束：数据源可多选（生成时按所选范围做口径校验），saving 期间禁止重复提交。
 */
import { computed, reactive, watch } from 'vue'
import { ElMessage } from 'element-plus'

export interface DashboardTemplate {
  id: string
  name: string
  sourceKind?: string
  content?: { canvas?: { width?: number; height?: number } }
}

export interface DataSourceSummary {
  id: string
  name: string
  description?: string
  databaseName?: string
  metadataStatus?: string
}

export interface TemplateUseRequest {
  mode: 'empty' | 'requirement'
  reportName: string
  datasourceIds: string[]
  requirementDocument?: {
    schemaVersion: 'dashboard-requirement-v1'
    objective: string
    metrics: string[]
    dimensions: string[]
    filters: string[]
    timeRange: string
    notes: string
  }
}

const props = defineProps<{
  modelValue: boolean
  template: DashboardTemplate | null
  datasources: DataSourceSummary[]
  sourcesLoading?: boolean
  saving?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  submit: [value: TemplateUseRequest]
}>()

const form = reactive({
  mode: 'empty' as TemplateUseRequest['mode'],
  reportName: '',
  requirement: '',
  datasourceIds: [] as string[],
})

const selectedSources = computed(() => props.datasources.filter(item => form.datasourceIds.includes(item.id)))
const referenceTemplate = computed(() => props.template?.sourceKind === 'hifi-reference')

watch(
  () => ({ open: props.modelValue, id: props.template?.id }),
  ({ open }) => {
    if (!open || !props.template) return
    form.mode = 'empty'
    form.reportName = props.template.name
    form.requirement = ''
    form.datasourceIds = []
  },
  { immediate: true },
)

watch(
  () => props.datasources,
  rows => {
    if (props.modelValue && form.mode === 'requirement' && rows.length === 1 && !form.datasourceIds.length) {
      form.datasourceIds = [rows[0].id]
    }
  },
)

function selectMode(mode: TemplateUseRequest['mode']) {
  form.mode = mode
  if (mode === 'requirement' && props.datasources.length === 1 && !form.datasourceIds.length) {
    form.datasourceIds = [props.datasources[0].id]
  }
}

function close() {
  emit('update:modelValue', false)
}

function submit() {
  const reportName = form.reportName.trim()
  if (!reportName) {
    ElMessage.warning('请输入报表名称')
    return
  }
  if (form.mode === 'empty') {
    emit('submit', { mode: 'empty', reportName, datasourceIds: [] })
    return
  }
  const objective = form.requirement.trim()
  if (!objective) {
    ElMessage.warning('请输入客户需求')
    return
  }
  if (!form.datasourceIds.length) {
    ElMessage.warning('请选择本次使用的企业数据源')
    return
  }
  emit('submit', {
    mode: 'requirement',
    reportName,
    datasourceIds: [...form.datasourceIds],
    requirementDocument: {
      schemaVersion: 'dashboard-requirement-v1',
      objective,
      metrics: [],
      dimensions: [],
      filters: [],
      timeRange: '',
      notes: '',
    },
  })
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    :title="template ? `使用「${template.name}」` : '使用模板'"
    width="min(640px, 94vw)"
    :close-on-click-modal="false"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="use-mode" role="radiogroup" aria-label="模板使用方式">
      <button type="button" role="radio" :aria-checked="form.mode === 'empty'" :class="{ active: form.mode === 'empty' }" @click="selectMode('empty')">
        <i class="fa-regular fa-clone"></i><span><b>{{ referenceTemplate ? '参考示例副本' : '空数据副本' }}</b><small>{{ referenceTemplate ? '保留原图示例，再编辑数据' : '样式与模板完全一致' }}</small></span>
      </button>
      <button type="button" role="radio" :aria-checked="form.mode === 'requirement'" :class="{ active: form.mode === 'requirement' }" @click="selectMode('requirement')">
        <i class="fa-solid fa-wand-magic-sparkles"></i><span><b>按客户需求填充</b><small>只生成数据绑定</small></span>
      </button>
    </div>

    <el-form label-position="top" class="use-form" @submit.prevent="submit">
      <p v-if="referenceTemplate && form.mode === 'empty'" class="muted-note">副本中的数值来自参考 JPG 示例，未绑定企业数据。应用静态数据或绑定数据源后，数据区域按当前结果重绘；原图中的完整序列无法从截图恢复。</p>
      <el-form-item label="报表名称" required>
        <el-input v-model="form.reportName" maxlength="200" show-word-limit />
      </el-form-item>
      <template v-if="form.mode === 'requirement'">
        <el-form-item label="客户需求" required>
          <el-input
            v-model="form.requirement"
            type="textarea"
            :rows="4"
            maxlength="40000"
            placeholder="例：生成华北地区财务与设备大屏，按部门展示金额、趋势和预警数量"
          />
        </el-form-item>
        <el-form-item label="企业数据源" required>
          <el-select
            v-model="form.datasourceIds"
            multiple
            filterable
            collapse-tags
            collapse-tags-tooltip
            :loading="sourcesLoading"
            placeholder="选择本次授权的数据源"
            style="width: 100%"
          >
            <el-option v-for="source in datasources" :key="source.id" :label="source.name" :value="source.id">
              <span>{{ source.name }}</span>
              <small class="source-description">{{ source.description || source.databaseName || '已授权数据源' }}</small>
            </el-option>
          </el-select>
        </el-form-item>
        <div v-if="selectedSources.length" class="source-selection">{{ selectedSources.map(item => item.name).join('、') }}</div>
      </template>
    </el-form>

    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button type="primary" :loading="saving" @click="submit">
        {{ form.mode === 'empty' ? (referenceTemplate ? '创建参考示例副本' : '创建空数据报表') : '生成并保存草稿' }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.use-mode { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 20px; }
.use-mode button { display: grid; grid-template-columns: 32px 1fr; align-items: center; gap: 10px; min-height: 64px; padding: 10px 12px; border: 1px solid #d9e2ef; border-radius: 8px; background: #fff; color: #617087; text-align: left; cursor: pointer; }
.use-mode button > i { display: grid; place-items: center; width: 32px; height: 32px; border-radius: 7px; background: #eef3fa; color: #5d7da8; }
.use-mode span { display: grid; gap: 3px; }
.use-mode b { color: #25364d; font-size: 13px; }
.use-mode small { color: #8290a2; font-size: 11px; }
.use-mode button.active { border-color: #3979e8; background: #f5f8ff; box-shadow: 0 0 0 3px rgba(57, 121, 232, .09); }
.use-mode button.active > i { background: #3979e8; color: #fff; }
.use-form { padding-top: 2px; }
.source-description { float: right; max-width: 330px; overflow: hidden; color: #8a97a8; text-overflow: ellipsis; white-space: nowrap; }
.source-selection { margin-top: -10px; color: #718198; font-size: 11px; }
@media (max-width: 560px) { .use-mode { grid-template-columns: 1fr; } }
</style>
