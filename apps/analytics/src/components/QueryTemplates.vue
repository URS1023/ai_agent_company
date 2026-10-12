<script setup lang="ts">
/**
 * 查询模板管理弹窗（自然语言 / SQL 模板的增删改查与套用）。
 * 路由：无独立路由，由智能查询页（/analytics）以 v-model:visible 打开。
 * 依赖接口：api/request.ts 直接调用 /api/v1/analytics/query-templates（GET/POST/PUT/DELETE）。
 * 关键交互：use-template 事件把模板正文、模式与命名参数交给父组件填入输入框（不直接执行查询）；
 *   新增/编辑共用同一表单，编辑时带 version 做乐观锁；删除需二次确认。
 * 权限：新增/编辑/删除分别受 can('analytics.create' | 'analytics.edit' | 'analytics.delete') 控制。
 * 错误呈现：失败用 ElMessage 提示后端 message，列表保持原有数据。
 */
import { computed, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Edit, Plus, Search } from '@element-plus/icons-vue'
import { can, del, get, post, put } from '../api/request'
import type { Envelope, QueryTemplate } from '../types'

const emit = defineEmits<{
  (event: 'use-template', content: string, mode: QueryTemplate['mode'], parameters: QueryTemplate['parameters']): void
}>()

const visible = ref(false)
const loading = ref(false)
const saving = ref(false)
const keyword = ref('')
const category = ref('全部')
const templates = ref<QueryTemplate[]>([])
const editorVisible = ref(false)
const editingId = ref('')
const parameterText = ref('{}')
const form = reactive({
  name: '',
  category: '未分类',
  content: '',
  mode: 'natural_language' as QueryTemplate['mode'],
  version: undefined as number | undefined,
})

const categories = computed(() => [
  '全部',
  ...Array.from(new Set(templates.value.map(item => item.category || '未分类'))).sort((a, b) => a.localeCompare(b, 'zh-CN')),
])
const filteredTemplates = computed(() => {
  const term = keyword.value.trim().toLowerCase()
  return templates.value.filter(item => {
    const inCategory = category.value === '全部' || item.category === category.value
    const text = [item.name, item.category, item.content].join(' ').toLowerCase()
    return inCategory && (!term || text.includes(term))
  })
})

function resetForm() {
  editingId.value = ''
  form.name = ''
  form.category = category.value === '全部' ? '未分类' : category.value
  form.content = ''
  form.mode = 'natural_language'
  form.version = undefined
  parameterText.value = '{}'
}

async function loadTemplates() {
  loading.value = true
  try {
    const response = await get<Envelope<QueryTemplate[]>>('/api/v1/analytics/query-templates')
    templates.value = response.data
    if (!categories.value.includes(category.value)) category.value = '全部'
  } catch (error) {
    ElMessage.error((error as Error).message || '查询模板读取失败')
  } finally {
    loading.value = false
  }
}

async function open() {
  visible.value = true
  await loadTemplates()
}

function createTemplate() {
  resetForm()
  editorVisible.value = true
}

function editTemplate(item: QueryTemplate) {
  editingId.value = item.id
  form.name = item.name
  form.category = item.category
  form.content = item.content
  form.mode = item.mode
  form.version = item.version
  parameterText.value = JSON.stringify(item.parameters || {}, null, 2)
  editorVisible.value = true
}

/**
 * 保存模板：先校验名称/正文必填，再解析参数 JSON（必须是对象，数组或标量一律拒绝），
 * 编辑态附加 version 做乐观锁；校验失败只提示不提交。
 */
async function saveTemplate() {
  if (!form.name.trim() || !form.content.trim()) {
    ElMessage.warning('请输入模板名称和查询内容')
    return
  }
  let parameters: Record<string, unknown>
  try {
    const parsed = JSON.parse(parameterText.value || '{}')
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error()
    parameters = parsed
  } catch {
    ElMessage.warning('参数定义必须是 JSON 对象')
    return
  }
  const payload = {
    name: form.name.trim(),
    category: form.category.trim() || '未分类',
    content: form.content.trim(),
    mode: form.mode,
    parameters,
    ...(editingId.value ? { version: form.version } : {}),
  }
  saving.value = true
  try {
    if (editingId.value) {
      await put('/api/v1/analytics/query-templates/' + editingId.value, payload)
      ElMessage.success('查询模板已更新')
    } else {
      await post('/api/v1/analytics/query-templates', payload)
      ElMessage.success('查询模板已保存')
    }
    editorVisible.value = false
    await loadTemplates()
  } catch (error) {
    ElMessage.error((error as Error).message || '查询模板保存失败')
  } finally {
    saving.value = false
  }
}

async function useTemplate(item: QueryTemplate) {
  try {
    const response = await post<Envelope<QueryTemplate>>('/api/v1/analytics/query-templates/' + item.id + '/use')
    emit('use-template', response.data.content, response.data.mode, response.data.parameters || {})
    visible.value = false
    ElMessage.success('模板已填入查询区')
  } catch (error) {
    ElMessage.error((error as Error).message || '查询模板暂不可用')
  }
}

async function removeTemplate(item: QueryTemplate) {
  try {
    await ElMessageBox.confirm(`删除查询模板“${item.name}”？已有查询记录不会被删除。`, '删除模板', {
      confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning',
    })
    await del('/api/v1/analytics/query-templates/' + item.id)
    ElMessage.success('查询模板已删除')
    await loadTemplates()
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error((error as Error).message || '查询模板删除失败')
  }
}

defineExpose({ open })
</script>

<template>
  <div class="query-templates">
    <el-dialog v-model="visible" title="查询模板" width="min(920px, 92vw)" :close-on-click-modal="false" destroy-on-close>
      <div class="template-toolbar">
        <el-input v-model="keyword" clearable placeholder="搜索名称、分类或查询内容" :prefix-icon="Search" />
        <el-select v-model="category" aria-label="模板分类">
          <el-option v-for="item in categories" :key="item" :label="item" :value="item" />
        </el-select>
        <el-button v-if="can('analytics.create')" type="primary" :icon="Plus" @click="createTemplate">新增模板</el-button>
      </div>

      <div v-loading="loading" class="template-list">
        <article v-for="item in filteredTemplates" :key="item.id" class="template-item">
          <div class="template-main">
            <div class="template-title-row">
              <strong>{{ item.name }}</strong>
              <el-tag size="small" effect="plain">{{ item.category }}</el-tag>
              <el-tag size="small" :type="item.mode === 'sql' ? 'warning' : 'primary'">
                {{ item.mode === 'sql' ? 'SQL' : '自然语言' }}
              </el-tag>
            </div>
            <p>{{ item.content }}</p>
            <small>已复用 {{ item.usageCount }} 次 · 版本 {{ item.version }}</small>
          </div>
          <div class="template-actions">
            <el-button type="primary" size="small" @click="useTemplate(item)">使用</el-button>
            <el-button v-if="can('analytics.edit')" size="small" :icon="Edit" aria-label="编辑模板" @click="editTemplate(item)" />
            <el-button v-if="can('analytics.delete')" size="small" :icon="Delete" aria-label="删除模板" @click="removeTemplate(item)" />
          </div>
        </article>
        <el-empty v-if="!loading && !filteredTemplates.length" description="当前范围内没有查询模板" :image-size="72">
          <el-button v-if="can('analytics.create')" type="primary" @click="createTemplate">新增第一个模板</el-button>
        </el-empty>
      </div>
      <template #footer><el-button @click="visible = false">关闭</el-button></template>
    </el-dialog>

    <el-dialog v-model="editorVisible" :title="editingId ? '编辑查询模板' : '新增查询模板'" width="min(680px, 92vw)" append-to-body :close-on-click-modal="false">
      <el-form label-position="top">
        <div class="editor-grid">
          <el-form-item label="模板名称" required><el-input v-model="form.name" maxlength="160" show-word-limit /></el-form-item>
          <el-form-item label="分类"><el-input v-model="form.category" maxlength="100" /></el-form-item>
        </div>
        <el-form-item label="查询方式" required>
          <el-radio-group v-model="form.mode"><el-radio-button value="natural_language">自然语言</el-radio-button><el-radio-button value="sql">参数化 SQL</el-radio-button></el-radio-group>
        </el-form-item>
        <el-form-item :label="form.mode === 'sql' ? '只读 SQL' : '提示词'" required>
          <el-input v-model="form.content" type="textarea" :rows="7" maxlength="30000" show-word-limit :class="{ 'code-editor': form.mode === 'sql' }" />
        </el-form-item>
        <details class="parameter-editor">
          <summary>参数定义（可选 JSON 对象）</summary>
          <el-input v-model="parameterText" type="textarea" :rows="5" class="code-editor" />
        </details>
      </el-form>
      <template #footer>
        <el-button @click="editorVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveTemplate">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.template-toolbar { display: grid; grid-template-columns: minmax(240px, 1fr) 150px auto; gap: 10px; margin-bottom: 14px; }
.template-list { min-height: 220px; max-height: min(62vh, 620px); overflow: auto; border-top: 1px solid #e3e8ef; }
.template-item { display: flex; align-items: center; gap: 18px; padding: 15px 4px; border-bottom: 1px solid #e8edf4; }
.template-main { min-width: 0; flex: 1; }
.template-title-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.template-title-row strong { color: #17263c; font-size: 14px; }
.template-main p { margin: 8px 0 7px; color: #40546c; line-height: 1.6; font-size: 13px; white-space: pre-wrap; overflow-wrap: anywhere; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.template-main small { color: #7a899d; }
.template-actions { display: flex; gap: 6px; flex-shrink: 0; }
.template-actions :deep(.el-button) { margin-left: 0; }
.editor-grid { display: grid; grid-template-columns: 1.6fr 1fr; gap: 14px; }
.parameter-editor { border-top: 1px solid #e3e8ef; padding-top: 10px; }
.parameter-editor summary { cursor: pointer; color: #40546c; font-size: 13px; margin-bottom: 10px; }
.code-editor :deep(textarea) { font-family: Consolas, 'SFMono-Regular', monospace; }
@media (max-width: 680px) {
  .template-toolbar, .editor-grid { grid-template-columns: 1fr; }
  .template-item { align-items: flex-start; flex-direction: column; }
  .template-actions { align-self: flex-end; }
}
</style>
