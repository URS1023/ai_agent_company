<script setup lang="ts">
/**
 * "把已确认报表固化为模板"弹窗（只填数据、不改结构）。
 * 路由：无独立路由，由报表管理/设计器打开，submit 后由父组件调用 reportApi.createTemplateFromReport。
 * 依赖接口：无请求；提交体为 { name, description, category, tags[], enabled }。
 * 关键交互：打开时用源报表名生成默认模板名与描述（每次打开都按当前报表重置，不保留上次输入）；
 *   只做表单校验与规范化（tags 以逗号分隔转数组），不含任何写操作。
 * 约束：源报表不会被修改（固化为模板是新增资源）；saving 期间按钮 loading 并禁止重复提交。
 */
import { reactive, watch } from 'vue'
import { ElMessage } from 'element-plus'

interface ReportSummary {
  id: string
  reportName: string
  description?: string
}

interface TemplateForm {
  name: string
  description: string
  category: string
  tags: string
  enabled: boolean
}

const props = defineProps<{
  modelValue: boolean
  report: ReportSummary | null
  saving?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  submit: [value: { name: string; description: string; category: string; tags: string[]; enabled: boolean }]
}>()

const form = reactive<TemplateForm>({
  name: '',
  description: '',
  category: '自定义',
  tags: '',
  enabled: true,
})

watch(
  () => ({ open: props.modelValue, id: props.report?.id }),
  ({ open }) => {
    if (!open || !props.report) return
    form.name = `${props.report.reportName} 模板`
    form.description = props.report.description || ''
    form.category = '自定义'
    form.tags = ''
    form.enabled = true
  },
  { immediate: true },
)

function close() {
  emit('update:modelValue', false)
}

function submit() {
  const name = form.name.trim()
  if (!name) {
    ElMessage.warning('请输入模板名称')
    return
  }
  emit('submit', {
    name,
    description: form.description.trim(),
    category: form.category.trim() || '自定义',
    tags: [...new Set(form.tags.split(',').map(value => value.trim()).filter(Boolean))],
    enabled: form.enabled,
  })
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="保存为模板"
    width="min(560px, 94vw)"
    :close-on-click-modal="false"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <p class="template-contract">保留画布、组件、布局与样式；自动清空数据源、SQL和查询结果。</p>
    <el-form label-position="top" @submit.prevent="submit">
      <el-form-item label="模板名称" required>
        <el-input v-model="form.name" maxlength="200" show-word-limit autofocus />
      </el-form-item>
      <div class="template-form-grid">
        <el-form-item label="分类">
          <el-input v-model="form.category" maxlength="80" />
        </el-form-item>
        <el-form-item label="标签">
          <el-input v-model="form.tags" maxlength="300" placeholder="多个标签用逗号分隔" />
        </el-form-item>
      </div>
      <el-form-item label="说明">
        <el-input v-model="form.description" type="textarea" :rows="2" maxlength="2000" show-word-limit />
      </el-form-item>
      <el-checkbox v-model="form.enabled">保存后立即可用</el-checkbox>
    </el-form>
    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button type="primary" :loading="saving" @click="submit">保存模板</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.template-contract {
  margin: -4px 0 18px;
  padding: 9px 12px;
  border-left: 3px solid #3979e8;
  background: #f5f8fd;
  color: #52657d;
  font-size: 12px;
  line-height: 1.55;
}
.template-form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
@media (max-width: 560px) { .template-form-grid { grid-template-columns: 1fr; gap: 0; } }
</style>
