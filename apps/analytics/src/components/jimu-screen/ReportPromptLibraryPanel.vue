<script setup>
/**
 * ReportPromptLibraryPanel —— 报表/大屏 AI 提示词模板库面板（分类浏览、搜索、保存为模板、套用）。
 *
 * 使用场景：JimuBigScreenReplica 的 AI 生成弹窗内。
 * 依赖接口：`api/reportPrompt.reportPromptApi`（模板 CRUD）、`api/request.can`（编辑/删除权限）、
 * `utils/reportPromptLibrary`（mergePromptLibrary / filterPromptTemplates / appendPromptText）。
 * 关键约束：
 * - 模板内容通过 appendPromptText 追加到 v-model，不整体覆盖用户已写好的提示词；
 * - 新增/删除等写操作受 can 权限控制，无权限时按钮禁用而不是隐藏（保持界面可预期）；
 * - 分类与模板列表由接口返回，组件内不内置默认模板，避免与后端数据不一致。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { reportPromptApi } from '@/api/reportPrompt'
import { can } from '@/api/request'
import { appendPromptText, filterPromptTemplates, mergePromptLibrary } from '@/utils/reportPromptLibrary'

const props = defineProps({
  modelValue: { type: String, default: '' },
  mode: { type: String, default: 'screen' }
})

const emit = defineEmits(['update:modelValue', 'template-used'])

const categories = ref([])
const templates = ref([])
const selectedCategoryId = ref('')
const keyword = ref('')
const loading = ref(false)
const saving = ref(false)
const activeTemplateId = ref('')
const errorMessage = ref('')

const categoryIconPool = ['fa-chart-pie', 'fa-layer-group', 'fa-user-check', 'fa-shield-halved', 'fa-wand-magic-sparkles']
const mergedCategories = computed(() => {
  const merged = mergePromptLibrary(categories.value, templates.value)
  return [...merged].sort((left, right) => {
    const leftSystem = Number(left.createdBy) === 0 ? 0 : 1
    const rightSystem = Number(right.createdBy) === 0 ? 0 : 1
    return leftSystem - rightSystem || Number(left.sortOrder || 0) - Number(right.sortOrder || 0)
  })
})
const activeCategory = computed(() => mergedCategories.value.find(item => String(item.id) === String(selectedCategoryId.value)))
const visibleTemplates = computed(() => filterPromptTemplates(activeCategory.value?.templates || templates.value, keyword.value))
const promptLabel = computed(() => props.mode === 'screen' ? '整屏提示词' : '组件提示词')

function categoryIconClass(category, index) {
  const icon = String(category?.icon || '').trim()
  return icon.startsWith('fa-') ? icon : categoryIconPool[index % categoryIconPool.length]
}

function payloadData(payload) {
  if (Array.isArray(payload)) return payload
  if (Array.isArray(payload?.data)) return payload.data
  if (Array.isArray(payload?.result)) return payload.result
  return []
}

function payloadObject(payload) {
  return payload?.data && !Array.isArray(payload.data) ? payload.data : payload?.result || payload
}

async function loadCategories() {
  const response = await reportPromptApi.listCategories()
  categories.value = payloadData(response)
  if (!categories.value.length) {
    selectedCategoryId.value = ''
    templates.value = []
    return
  }
  if (!categories.value.some(item => String(item.id) === String(selectedCategoryId.value))) {
    selectedCategoryId.value = String(categories.value[0].id)
  }
}

async function loadTemplates() {
  const response = await reportPromptApi.listTemplates({
    keyword: keyword.value.trim() || undefined
  })
  templates.value = payloadData(response)
}

async function refresh() {
  loading.value = true
  errorMessage.value = ''
  try {
    await loadCategories()
    await loadTemplates()
  } catch (error) {
    errorMessage.value = error?.message || '提示词库暂时不可用'
  } finally {
    loading.value = false
  }
}

function chooseCategory(category) {
  selectedCategoryId.value = String(category.id)
}

async function useTemplate(template) {
  try {
    await reportPromptApi.useTemplate(template.id)
    activeTemplateId.value = String(template.id)
    emit('update:modelValue', appendPromptText(props.modelValue, template.content || ''))
    emit('template-used', template)
    template.useCount = (template.useCount || 0) + 1
    ElMessage.success(`已追加：${template.name}`)
  } catch (error) { ElMessage.error(error.message || '提示词使用失败') }
}

async function createCategory() {
  try {
    const { value } = await ElMessageBox.prompt('创建一个可复用的提示词栏目', '新建栏目', {
      confirmButtonText: '创建',
      cancelButtonText: '取消',
      inputPlaceholder: '例如：财务分析、设备质检',
      inputValidator: value => String(value || '').trim() ? true : '请输入栏目名称'
    })
    const response = await reportPromptApi.createCategory({ name: value.trim() })
    const created = payloadObject(response)
    await loadCategories()
    if (created?.id) selectedCategoryId.value = String(created.id)
    await loadTemplates()
    ElMessage.success('栏目已创建')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error?.message || '栏目创建失败')
  }
}

async function saveCurrentPrompt() {
  const content = String(props.modelValue || '').trim()
  if (!content) {
    ElMessage.warning('请先在输入框中写入提示词')
    return
  }
  if (!selectedCategoryId.value) {
    ElMessage.warning('请先选择保存栏目')
    return
  }
  try {
    const { value } = await ElMessageBox.prompt('为当前提示词设置一个名称', `保存到「${activeCategory.value?.name || '提示词库'}」`, {
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputPlaceholder: '例如：季度设备异常分析',
      inputValidator: value => String(value || '').trim() ? true : '请输入提示词名称'
    })
    saving.value = true
    await reportPromptApi.createTemplate({
      categoryId: String(selectedCategoryId.value),
      mode: props.mode,
      name: value.trim(),
      content,
      description: props.mode === 'screen' ? '整屏生成提示词' : '组件生成提示词',
      tags: props.mode === 'screen' ? '整屏,驾驶舱' : '组件,图表'
    })
    await loadTemplates()
    ElMessage.success('提示词已保存，可重复使用')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error?.message || '提示词保存失败')
  } finally {
    saving.value = false
  }
}

async function deleteTemplate(template) {
  if (Number(template.createdBy) === 0) {
    ElMessage.info('系统模板仅支持复制使用')
    return
  }
  try {
    await ElMessageBox.confirm(`确认删除「${template.name}」吗？`, '删除提示词', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning'
    })
    await reportPromptApi.deleteTemplate(template.id)
    await loadTemplates()
    ElMessage.success('提示词已删除')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error?.message || '删除失败')
  }
}

watch(keyword, () => {
  loadTemplates().catch(error => { errorMessage.value = error?.message || '提示词搜索失败' })
})

onMounted(refresh)
</script>

<template>
  <section class="prompt-library-panel">
    <div class="prompt-library-head">
      <div>
        <span class="prompt-eyebrow"><i class="fa-solid fa-book-atlas"></i> PROMPT WORKSPACE</span>
        <strong>提示词资产库</strong>
      </div>
      <button class="prompt-icon-btn" title="刷新" :disabled="loading" @click="refresh">
        <i class="fa-solid fa-rotate" :class="{ 'fa-spin': loading }"></i>
      </button>
    </div>

    <div class="prompt-library-toolbar">
      <span><i class="fa-solid fa-folder-tree"></i> 模块栏目</span>
      <button v-if="can('prompts.create')" @click="createCategory"><i class="fa-solid fa-plus"></i> 新建栏目</button>
    </div>
    <div class="prompt-category-strip">
      <button
        v-for="(category, index) in mergedCategories"
        :key="category.id"
        class="prompt-category"
        :class="{ active: String(selectedCategoryId) === String(category.id) }"
        :style="{ '--category-color': category.color || '#2f7cf6' }"
        @click="chooseCategory(category)"
      >
        <i :class="['fa-solid', categoryIconClass(category, index)]"></i>
        <b class="prompt-category-code">{{ String(index + 1).padStart(2, '0') }}</b>
        <span>{{ category.name }}</span>
        <small>{{ category.templates?.length || 0 }}</small>
      </button>
      <span v-if="!categories.length && !loading" class="prompt-empty-inline">暂无栏目</span>
    </div>

    <div class="prompt-template-head">
      <div>
        <span class="prompt-eyebrow">{{ activeCategory?.name || '提示词' }}</span>
        <strong>可复用指令</strong>
      </div>
      <span class="prompt-count">{{ visibleTemplates.length }} 条</span>
    </div>
    <label class="prompt-search">
      <i class="fa-solid fa-magnifying-glass"></i>
      <input v-model="keyword" aria-label="搜索提示词" placeholder="搜索名称、指标、场景" />
    </label>

    <div class="prompt-template-list">
      <div v-if="loading" class="prompt-state"><i class="fa-solid fa-spinner fa-spin"></i> 正在同步提示词资产</div>
      <div v-else-if="errorMessage" class="prompt-state prompt-state-error"><i class="fa-solid fa-triangle-exclamation"></i> {{ errorMessage }}</div>
      <div v-else-if="!visibleTemplates.length" class="prompt-state"><i class="fa-regular fa-folder-open"></i> 当前栏目还没有匹配的提示词</div>
      <article
        v-for="template in visibleTemplates"
        :key="template.id"
        class="prompt-template-card"
        :class="{ active: activeTemplateId === String(template.id) }"
        role="button" tabindex="0"
        @keydown.enter="useTemplate(template)" @keydown.space.prevent="useTemplate(template)"
        @click="useTemplate(template)"
      >
        <div class="prompt-template-icon"><i class="fa-solid fa-terminal"></i></div>
        <div class="prompt-template-copy">
          <strong>{{ template.name }}</strong>
          <p>{{ template.description || template.content }}</p>
          <span class="prompt-template-meta"><i class="fa-solid fa-chart-simple"></i> {{ template.useCount || 0 }} 次使用 <em v-if="Number(template.createdBy) === 0">系统模板</em><em v-else>我的模板</em></span>
        </div>
        <button v-if="Number(template.createdBy) !== 0 && can('prompts.delete')" class="prompt-delete-btn" :aria-label="'删除提示词 ' + template.name" title="删除" @click.stop="deleteTemplate(template)">
          <i class="fa-regular fa-trash-can"></i>
        </button>
        <i class="fa-solid fa-arrow-up-right-from-square prompt-use-arrow"></i>
      </article>
    </div>

    <div class="prompt-library-foot">
      <span><i class="fa-solid fa-circle-info"></i> 点击卡片将语句追加到{{ promptLabel }}输入框</span>
      <button v-if="can('prompts.create')" class="prompt-save-btn" :disabled="saving" @click="saveCurrentPrompt"><i class="fa-solid fa-bookmark"></i> 保存当前提示词</button>
    </div>
  </section>
</template>

<style scoped>
.prompt-library-panel {
  min-width: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
  color: #dce7f6;
}
.prompt-library-head,
.prompt-template-head,
.prompt-library-toolbar,
.prompt-library-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.prompt-library-head { padding-bottom: 13px; border-bottom: 1px solid rgba(78, 127, 180, .26); }
.prompt-library-head strong,
.prompt-template-head strong { display: block; margin-top: 4px; color: #f4f8ff; font-size: 14px; letter-spacing: .02em; }
.prompt-eyebrow { color: #6c9bc8; font: 9px/1.2 Consolas, monospace; letter-spacing: .1em; }
.prompt-eyebrow i { margin-right: 5px; color: #62c6ff; }
.prompt-icon-btn,
.prompt-library-toolbar button,
.prompt-save-btn,
.prompt-delete-btn {
  border: 1px solid rgba(72, 145, 218, .38);
  background: rgba(19, 50, 85, .64);
  color: #8fc8f7;
  cursor: pointer;
}
.prompt-icon-btn { width: 28px; height: 28px; border-radius: 8px; }
.prompt-icon-btn:hover,
.prompt-library-toolbar button:hover,
.prompt-save-btn:hover { border-color: #55c7ff; color: #e5f6ff; box-shadow: 0 0 14px rgba(60, 185, 255, .15); }
.prompt-library-toolbar { margin-top: 14px; color: #84a9ce; font-size: 10px; }
.prompt-library-toolbar span i { margin-right: 5px; color: #55c5ff; }
.prompt-library-toolbar button { padding: 5px 8px; font-size: 10px; }
.prompt-library-toolbar button i { margin-right: 4px; }
.prompt-category-strip { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; max-height: 184px; margin: 9px 0 16px; overflow-y: auto; overflow-x: hidden; padding: 1px 2px 2px 0; }
.prompt-category { display: grid; grid-template-columns: 14px 17px minmax(0, 1fr) auto; align-items: center; gap: 5px; min-width: 0; padding: 7px 8px; border: 1px solid rgba(61, 111, 161, .35); border-radius: 8px; background: rgba(11, 28, 50, .76); color: #7fa3c7; font-size: 10px; cursor: pointer; }
.prompt-category i { color: var(--category-color); font-size: 10px; }.prompt-category-code { color: #54779e; font: 8px Consolas, monospace; font-weight: 500; }.prompt-category span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.prompt-category small { min-width: 15px; padding: 1px 4px; border-radius: 8px; background: rgba(109, 154, 200, .12); color: #8bb3d7; font-size: 9px; }
.prompt-category.active { border-color: var(--category-color); background: color-mix(in srgb, var(--category-color) 13%, #0b1b2e); color: #eef8ff; box-shadow: 0 0 15px color-mix(in srgb, var(--category-color) 18%, transparent); }
.prompt-empty-inline { align-self: center; color: #6385a8; font-size: 10px; }
.prompt-template-head { margin-bottom: 9px; }
.prompt-count { color: #6d91b7; font: 10px Consolas, monospace; }
.prompt-search { display: flex; align-items: center; gap: 8px; height: 32px; padding: 0 9px; border: 1px solid rgba(63, 119, 171, .34); border-radius: 7px; background: rgba(4, 14, 27, .7); color: #63bdf1; }
.prompt-search input { min-width: 0; flex: 1; border: 0; outline: 0; background: transparent; color: #d8e8f7; font-size: 11px; }
.prompt-search input::placeholder { color: #597a9d; }
.prompt-search kbd { padding: 2px 4px; border: 1px solid rgba(98, 147, 196, .35); border-radius: 3px; color: #6688a9; font: 9px Consolas, monospace; }
.prompt-template-list { display: grid; gap: 7px; margin-top: 10px; min-height: 130px; max-height: 240px; overflow: auto; padding-right: 2px; }
.prompt-template-card { position: relative; display: grid; grid-template-columns: 27px minmax(0, 1fr) 18px; gap: 9px; align-items: start; padding: 10px; border: 1px solid rgba(55, 105, 153, .27); border-radius: 8px; background: linear-gradient(110deg, rgba(17, 43, 72, .78), rgba(7, 22, 40, .92)); cursor: pointer; transition: .2s ease; }
.prompt-template-card:hover,
.prompt-template-card.active { border-color: #46bbf7; transform: translateX(2px); box-shadow: 0 0 18px rgba(55, 175, 244, .14), inset 2px 0 #53c8ff; }
.prompt-template-icon { display: grid; place-items: center; width: 27px; height: 27px; border-radius: 7px; background: rgba(37, 133, 197, .17); color: #58caff; font-size: 11px; }
.prompt-template-copy { min-width: 0; }
.prompt-template-copy strong { display: block; overflow: hidden; color: #edf7ff; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.prompt-template-copy p { display: -webkit-box; overflow: hidden; margin: 5px 0 7px; color: #779bbb; font-size: 9px; line-height: 1.45; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.prompt-template-meta { display: flex; align-items: center; gap: 7px; color: #5f83a5; font-size: 9px; }
.prompt-template-meta i { color: #4caee4; }
.prompt-template-meta em { padding: 2px 5px; border: 1px solid rgba(85, 157, 207, .25); border-radius: 3px; color: #79afd3; font-style: normal; }
.prompt-use-arrow { align-self: center; color: #548db7; font-size: 10px; }
.prompt-template-card:hover .prompt-use-arrow,
.prompt-template-card.active .prompt-use-arrow { color: #70d5ff; }
.prompt-delete-btn { position: absolute; right: 25px; top: 8px; width: 20px; height: 20px; border: 0; background: transparent; color: #6f8da9; font-size: 10px; }
.prompt-delete-btn:hover { color: #ff7e9f; }
.prompt-state { display: grid; place-items: center; gap: 7px; min-height: 130px; border: 1px dashed rgba(80, 133, 183, .3); border-radius: 8px; color: #6e94b8; font-size: 10px; }
.prompt-state i { color: #5ec5fb; font-size: 16px; }
.prompt-state-error { color: #f29ba9; }
.prompt-state-error i { color: #ff7f91; }
.prompt-library-foot { margin-top: 11px; padding-top: 11px; border-top: 1px solid rgba(78, 127, 180, .22); color: #6486a7; font-size: 9px; }
.prompt-library-foot > span { min-width: 0; line-height: 1.4; }
.prompt-library-foot > span i { margin-right: 4px; color: #6dbdf1; }
.prompt-save-btn { flex: 0 0 auto; padding: 7px 9px; font-size: 10px; }
.prompt-save-btn i { margin-right: 4px; }

/* Workspace variant used by the AI orchestration center */
.prompt-library-panel { color: #dfeaff; }
.prompt-library-head { padding-bottom: 15px; border-bottom-color: rgba(83, 139, 213, .3); }
.prompt-library-head strong, .prompt-template-head strong { color: #f2f6ff; font-size: 15px; }
.prompt-eyebrow { color: #6f9fd5; }
.prompt-eyebrow i { color: #8d84ff; }
.prompt-icon-btn, .prompt-library-toolbar button, .prompt-save-btn, .prompt-delete-btn { border-color: rgba(93, 143, 222, .42); background: rgba(24, 54, 101, .56); color: #9abfff; }
.prompt-library-toolbar { color: #89a9d2; }
.prompt-library-toolbar span i { color: #78b9ff; }
.prompt-category { border-color: rgba(76, 128, 199, .34); border-radius: 9px; background: rgba(11, 29, 59, .78); color: #86a9d6; }
.prompt-category.active { background: color-mix(in srgb, var(--category-color) 14%, #0b1b39); box-shadow: 0 0 18px color-mix(in srgb, var(--category-color) 18%, transparent); }
.prompt-category.active .prompt-category-code { color: #bbd2ff; }
.prompt-template-head { margin-top: 2px; }
.prompt-count { color: #7197c5; }
.prompt-search { border-color: rgba(76, 132, 205, .42); border-radius: 8px; background: rgba(3, 14, 33, .78); }
.prompt-search:focus-within { border-color: #6caeff; box-shadow: 0 0 0 3px rgba(76, 153, 255, .09); }
.prompt-template-card { border-color: rgba(67, 120, 187, .32); border-radius: 9px; background: linear-gradient(110deg, rgba(17, 43, 82, .82), rgba(7, 22, 46, .94)); }
.prompt-template-card:hover, .prompt-template-card.active { border-color: #6a9dff; box-shadow: 0 0 20px rgba(77, 136, 255, .14), inset 2px 0 #79bdff; }
.prompt-template-icon { background: rgba(74, 111, 224, .2); color: #84b8ff; }
.prompt-template-copy strong { color: #edf5ff; }.prompt-template-copy p { color: #7f9fc7; }.prompt-template-meta { color: #688ab6; }.prompt-template-meta i { color: #6ab9ff; }
.prompt-library-foot { border-top-color: rgba(78, 132, 202, .25); color: #6f91b9; }
.prompt-library-foot > span i { color: #78b9ff; }
.prompt-save-btn { border-color: rgba(109, 125, 244, .55); background: linear-gradient(100deg, rgba(48, 105, 216, .7), rgba(102, 74, 208, .72)); color: #f3f6ff; box-shadow: 0 6px 18px rgba(62, 86, 211, .18); }
@media (max-width: 980px) {
  .prompt-template-list { max-height: 190px; }
}
</style>
