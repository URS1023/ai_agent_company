<script setup lang="ts">
/**
 * WorkflowFileInput —— 工作流输入的文件选择控件（开始节点的 file 类变量使用）。
 *
 * 依赖接口：`api/request` 的 get('/api/files')、post('/api/files/batch')（批量上传），
 * 返回 { id, name } 形式的资产；v-model 在单选时是资产对象、多选时是数组。
 * 关键约束：
 * - 最多 20 个文件（与后端批量上传上限一致）：超出时拒绝并给出中文提示，不静默截断；
 * - 历史值可能只有 ID 字符串（无名称），selected 会先用文件库补齐名称、查不到则退化为显示 ID，
 *   不能假设 modelValue 一定是对象；
 * - 上传/加载失败只提示错误并保留已选项，不自动重试；上传中禁用移除与再次选择；
 * - 单选选定后自动收起文件库面板。
 */
import { computed, ref } from 'vue'
import { get, post } from '../../api/request'

type Asset = { id: string; name: string }
const props = defineProps<{ modelValue: unknown; multiple: boolean; disabled: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: unknown] }>()
const busy = ref(false)
const error = ref('')
const library = ref<Asset[]>([])
const open = ref(false)
const query = ref('')
const page = ref(1)
const total = ref(0)
/**
 * 归一化已选资产：兼容"仅存 ID 字符串"的历史值，名称优先取文件库、缺失时回退显示 ID。
 * 因此这里不能假定 modelValue 一定是 { id, name } 对象。
 */
const selected = computed<Asset[]>(() => {
  const values = Array.isArray(props.modelValue) ? props.modelValue : props.modelValue ? [props.modelValue] : []
  return values.map(value => typeof value === 'string' ? { id: value, name: library.value.find(item => item.id === value)?.name || value } : value as Asset)
})
function update(values: Asset[]) { emit('update:modelValue', props.multiple ? values : values[0]) }
/** 选择资产：去重 + 上限 20 校验；单选模式选完即收起面板。 */
function choose(asset?: Asset) {
  if (!asset) return
  if (selected.value.some(item => item.id === asset.id)) return
  if (props.multiple && selected.value.length >= 20) { error.value = '最多选择 20 个文件'; return }
  update(props.multiple ? [...selected.value, asset] : [asset])
  if (!props.multiple) open.value = false
}
async function loadLibrary() {
  open.value = !open.value
  if (!open.value) return
  await searchLibrary(1)
}
async function searchLibrary(nextPage = 1) {
  error.value = ''
  try {
    const response = await get<{ items: Asset[]; total: number }>('/api/files', { q: query.value, page: nextPage, page_size: 50 })
    library.value = response.items
    page.value = nextPage
    total.value = response.total
  }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '文件列表加载失败' }
}
/** 批量上传：与已选数量合并后仍受 20 个上限约束；无论成败都清空 input，允许重复选择同名文件。 */
async function upload(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  if (!files.length) return
  error.value = ''
  if (files.length + (props.multiple ? selected.value.length : 0) > 20) { error.value = '最多选择 20 个文件'; input.value = ''; return }
  busy.value = true
  try {
    const form = new FormData()
    for (const file of files) form.append('files', file)
    const response = await post<{ items: Asset[] }>('/api/files/batch', form)
    update(props.multiple ? [...selected.value, ...response.items] : response.items.slice(0, 1))
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '上传失败，请重试' }
  finally { busy.value = false; input.value = '' }
}
</script>

<template>
  <div class="wf-file-input">
    <div v-for="asset in selected" :key="asset.id" class="wf-file-chip"><span>{{ asset.name }}</span><button type="button" :disabled="disabled || busy" :aria-label="`移除 ${asset.name}`" @click="update(selected.filter(item => item.id !== asset.id))">×</button></div>
    <label class="wf-file-upload">{{ busy ? '正在上传…' : '点击上传文件' }}<input type="file" :multiple="multiple" :disabled="disabled || busy" aria-label="上传工作流输入文件" @change="upload" /></label>
    <button type="button" :disabled="disabled || busy" @click="loadLibrary">{{ open ? '收起文件库' : '从文件库选择' }}</button>
    <template v-if="open">
      <input v-model="query" type="search" aria-label="搜索已有文件" placeholder="搜索文件名称" @keydown.enter.prevent="searchLibrary(1)" />
      <button type="button" @click="searchLibrary(1)">搜索</button>
      <select size="5" aria-label="选择已有文件" :disabled="disabled || busy" @change="choose(library.find(item => item.id === ($event.target as HTMLSelectElement).value))"><option value="" disabled selected>请选择文件</option><option v-for="asset in library" :key="asset.id" :value="asset.id">{{ asset.name }}</option></select>
      <div v-if="total > 50"><button type="button" :disabled="page === 1" @click="searchLibrary(page - 1)">上一页</button><span>{{ page }} / {{ Math.ceil(total / 50) }}</span><button type="button" :disabled="page * 50 >= total" @click="searchLibrary(page + 1)">下一页</button></div>
    </template>
    <small v-if="open && !library.length">文件库为空，请上传文件</small>
    <p v-if="error" role="alert" class="wf-error">{{ error }}</p>
  </div>
</template>

<style scoped>
.wf-file-input{display:grid;gap:8px}.wf-file-chip{display:flex;align-items:center;justify-content:space-between;padding:8px 12px;border:1px solid #e4e7ec;border-radius:8px}.wf-file-chip span{overflow:hidden;text-overflow:ellipsis}.wf-file-input button{cursor:pointer;background:white;border:1px solid #e4e7ec;border-radius:6px;padding:6px 10px;color:#475467}.wf-file-upload{position:relative;text-align:center;padding:18px;border:1px dashed #c8ceda;border-radius:8px;color:#155eef;cursor:pointer}.wf-file-upload input{position:absolute;inset:0;opacity:0;cursor:pointer;width:100%}.wf-file-input small{color:#667085}
</style>
