<script setup lang="ts">
/**
 * 提示词中心页面（路由 /prompts，权限 prompts.view）。
 * 职责：分类管理（新增/改名/移除）、提示词列表与搜索（分类 + 关键字 + 使用场景）、新增/编辑/另存副本/复制正文/移除，
 *   以及"使用"提示词 —— 问数分类写入 scopedKey('analysis-prompt') 后跳 /analytics，大屏分类写入 scopedKey('report-prompt') 后跳 /report/designer。
 * 关键交互：破坏性操作（移除提示词/分类）先 ElMessageBox 确认，用户取消不算错误（过滤 'cancel'/'close'）；
 *   保存前校验名称、正文、分类必填；内置提示词不可编辑/删除，只能复制或另存副本。
 * 权限：新增/编辑/删除按钮分别受 can('prompts.create' | 'prompts.edit' | 'prompts.delete') 控制。
 * 错误呈现：列表错误进 error（顶部 el-alert），操作类错误用 ElMessage。
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import { can, del, get, post, put, scopedKey } from '../api/request'
import type { Envelope, Prompt, PromptCategory } from '../types'

const router = useRouter()
const categories = ref<PromptCategory[]>([])
const items = ref<Prompt[]>([])
const activeCategory = ref('')
const keyword = ref('')
const mode = ref('')
const loading = ref(false)
const error = ref('')
const dialog = ref(false)
const saving = ref(false)
const editingId = ref('')
const form = ref({ name: '', content: '', description: '', categoryId: '', tags: '', mode: 'screen', version: 1 })
const category = computed(() => categories.value.find(item => item.id === activeCategory.value))

/** 加载分类列表：GET /api/v1/report-prompts/categories（无分页）；失败由调用方捕获。 */
async function loadCategories() {
  categories.value = (await get<Envelope<PromptCategory[]>>('/api/v1/report-prompts/categories')).data
}
/** 按当前筛选条件加载提示词：GET /api/v1/report-prompts/templates（categoryId/keyword/mode 均可选）。 */
async function load() {
  loading.value = true; error.value = ''
  try { items.value = (await get<Envelope<Prompt[]>>('/api/v1/report-prompts/templates', { categoryId: activeCategory.value || undefined, keyword: keyword.value, mode: mode.value || undefined })).data }
  catch (caught) { error.value = (caught as Error).message }
  finally { loading.value = false }
}
/**
 * 打开编辑弹窗：copy=true 表示"另存副本"（editingId 清空、名称加"· 副本"后缀，保存时新建而非覆盖）。
 * 约束：新建时默认分类取当前选中分类，避免用户回到空分类。
 */
function edit(item?: Prompt, copy = false) {
  editingId.value = item && !copy ? item.id : ''
  form.value = {
    name: item ? item.name + (copy ? ' · 副本' : '') : '',
    content: item?.content || '', description: item?.description || '',
    categoryId: item?.categoryId || activeCategory.value || '', tags: item?.tags || '', mode: item?.mode || 'screen',
    version: item?.version || 1,
  }
  dialog.value = true
}
async function save() {
  if (!form.value.name.trim() || !form.value.content.trim() || !form.value.categoryId) { ElMessage.warning('请填写名称、正文并选择分类'); return }
  saving.value = true
  try {
    const url = '/api/v1/report-prompts/templates' + (editingId.value ? '/' + editingId.value : '')
    await (editingId.value ? put(url, form.value) : post(url, form.value))
    dialog.value = false; await load(); ElMessage.success('提示词已保存')
  } catch (caught) { ElMessage.error((caught as Error).message) }
  finally { saving.value = false }
}
async function remove(item: Prompt) {
  try {
    await ElMessageBox.confirm('移除“' + item.name + '”？已使用的历史版本会保留。', '移除提示词', { type: 'warning' })
    await del('/api/v1/report-prompts/templates/' + item.id)
    await load()
  } catch (caught) { if (caught !== 'cancel' && caught !== 'close') ElMessage.error((caught as Error).message) }
}
async function changeCategory(editing = false) {
  const current = editing ? category.value : undefined
  try {
    const { value } = await ElMessageBox.prompt('分类名称', editing ? '修改分类' : '新建分类', {
      inputValue: current?.name || '', inputValidator: value => Boolean(String(value || '').trim()) || '请输入分类名称',
    })
    const payload = { name: value.trim(), description: current?.description || '', color: current?.color || '#2364c6',
      icon: current?.icon || 'fa-chart-pie', sortOrder: current?.sortOrder || 0, version: current?.version }
    if (current) await put('/api/v1/report-prompts/categories/' + current.id, payload)
    else activeCategory.value = (await post('/api/v1/report-prompts/categories', payload)).data.id
    await loadCategories(); await load()
  } catch (caught) { if (caught !== 'cancel' && caught !== 'close') ElMessage.error((caught as Error).message) }
}
async function removeCategory() {
  if (!category.value) return
  try {
    await ElMessageBox.confirm('只可移除没有提示词的自定义分类。', '移除分类', { type: 'warning' })
    await del('/api/v1/report-prompts/categories/' + category.value.id)
    activeCategory.value = ''; await loadCategories(); await load()
  } catch (caught) { if (caught !== 'cancel' && caught !== 'close') ElMessage.error((caught as Error).message) }
}
async function use(item: Prompt) {
  try {
    const used = (await post<Envelope<Prompt>>('/api/v1/report-prompts/templates/' + item.id + '/use')).data
    if (item.mode === 'query') {
      sessionStorage.setItem(scopedKey('analysis-prompt'), used.content)
      await router.push('/analytics')
    } else {
      sessionStorage.setItem(scopedKey('report-prompt'), JSON.stringify({ content: used.content, mode: used.mode, id: used.id, version: used.version }))
      await router.push('/report/designer')
    }
  } catch (caught) { ElMessage.error((caught as Error).message) }
}
async function copyText(item: Prompt) {
  try { await navigator.clipboard.writeText(item.content); await post('/api/v1/report-prompts/templates/' + item.id + '/use'); ElMessage.success('正文已复制'); await load() }
  catch (caught) { ElMessage.error((caught as Error).message) }
}
onMounted(async () => {
  try { await loadCategories(); await load() }
  catch (caught) { error.value = (caught as Error).message }
})
</script>
<template>
  <div class="business-page">
    <header class="business-heading"><div><p class="eyebrow">PROMPT LIBRARY</p><h1>提示词中心</h1><p>分类管理、编辑、复制和复用提示词；与分析模块及完整设计器同步。</p></div><el-button v-if="can('prompts.create')" type="primary" @click="edit()">新增提示词</el-button></header>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <div class="prompt-layout">
      <aside class="business-panel prompt-categories">
        <div class="panel-heading"><h2>分类</h2><el-button v-if="can('prompts.create')" link type="primary" @click="changeCategory()">新增</el-button></div>
        <button :class="{ active: !activeCategory }" @click="activeCategory = ''; load()">全部提示词</button>
        <button v-for="item in categories" :key="item.id" :class="{ active: activeCategory === item.id }" @click="activeCategory = item.id; load()"><span :style="{ color: item.color }">●</span> {{ item.name }} <small v-if="item.builtin">内置</small></button>
        <div v-if="category && !category.builtin" class="category-actions"><el-button v-if="can('prompts.edit')" size="small" @click="changeCategory(true)">编辑分类</el-button><el-button v-if="can('prompts.delete')" size="small" type="danger" plain @click="removeCategory">移除</el-button></div>
      </aside>
      <section class="business-panel prompt-content" v-loading="loading">
        <div class="list-toolbar"><el-input v-model="keyword" placeholder="搜索名称、正文或标签" clearable @keyup.enter="load" @clear="load" aria-label="搜索提示词" /><el-select v-model="mode" aria-label="提示词使用场景" placeholder="全部场景" clearable @change="load"><el-option value="screen" label="整屏生成" /><el-option value="widget" label="单组件生成" /><el-option value="query" label="数据查询" /></el-select><el-button @click="load">搜索</el-button></div>
        <el-empty v-if="!items.length && !loading" description="当前分类没有匹配的提示词" />
        <article v-for="item in items" :key="item.id" class="prompt-item">
          <div class="prompt-item-heading"><h3>{{ item.name }}</h3><el-tag size="small" :type="item.builtin ? 'info' : 'success'">{{ item.builtin ? '内置 · 可复制' : '自定义' }}</el-tag></div>
          <p class="prompt-description">{{ item.description }}</p>
          <p class="prompt-body">{{ item.content }}</p>
          <div class="prompt-item-footer"><small>{{ item.tags }} · 版本 {{ item.version }} · 复用 {{ item.useCount }} 次</small><div>
            <el-button link @click="copyText(item)">复制正文</el-button>
            <el-button v-if="can('prompts.create')" link @click="edit(item, true)">另存副本</el-button>
            <el-button v-if="!item.builtin && can('prompts.edit')" link @click="edit(item)">编辑</el-button>
            <el-button v-if="!item.builtin && can('prompts.delete')" link type="danger" @click="remove(item)">移除</el-button>
            <el-button type="primary" size="small" @click="use(item)">使用</el-button>
          </div></div>
        </article>
      </section>
    </div>
    <el-dialog v-model="dialog" :title="editingId ? '编辑提示词' : '新增提示词'" width="min(760px, 96vw)" :close-on-click-modal="false">
      <el-form label-position="top"><div class="two-column-form">
        <el-form-item label="名称" required><el-input v-model="form.name" maxlength="200" /></el-form-item>
        <el-form-item label="分类" required><el-select v-model="form.categoryId" placeholder="请选择"><el-option v-for="item in categories" :key="item.id" :value="item.id" :label="item.name" /></el-select></el-form-item>
        <el-form-item label="使用场景"><el-select v-model="form.mode"><el-option value="screen" label="整屏生成" /><el-option value="widget" label="单组件生成" /><el-option value="query" label="数据查询" /></el-select></el-form-item>
        <el-form-item label="标签（逗号分隔）"><el-input v-model="form.tags" maxlength="1000" /></el-form-item>
      </div><el-form-item label="用途说明"><el-input v-model="form.description" /></el-form-item><el-form-item label="提示词正文" required><el-input v-model="form.content" type="textarea" :rows="9" maxlength="30000" show-word-limit /></el-form-item></el-form>
      <template #footer><el-button @click="dialog = false">取消</el-button><el-button type="primary" :loading="saving" @click="save">保存</el-button></template>
    </el-dialog>
  </div>
</template>
