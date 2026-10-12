<script setup lang="ts">
/**
 * 数据源配置导入/导出弹窗（配置包在环境间迁移）。
 * 路由：无独立路由，由 DataSourceManage 在 /datasources 内以 v-model:open 控制显隐。
 * 依赖接口：GET /api/v1/analytics/datasources/config-export（blob 下载）与 POST .../config-import（dryRun 预检 / 正式导入）。
 * 关键交互（安全约束）：
 *   1) 必须先"预检"（dryRun=true）并展示新增/更新/跳过/改名/因凭据被停用的明细，才能执行正式导入；
 *   2) 正式导入前会校验"当前请求体签名"与预检时一致，文件或冲突处理方式变化即要求重新预检（防止预检结果被套用到别的包）；
 *   3) 导出/导入内容不含凭据，导入后凭据缺失的数据源会被停用（disabledForCredential）。
 * 约束：配置包大小上限 1 MB、formatVersion 必须为 1；canImport 为 false 时禁止选文件与导入。
 * 加载/取消：revision/fileRevision 作代际，切换冲突模式、关闭弹窗或换文件都会作废在途预检结果。
 * 错误呈现：所有失败用 ElMessage.error 提示后端 message。
 */
import { onBeforeUnmount, shallowRef, watch } from 'vue'
import { ElMessage } from 'element-plus'
import axios from '../../api/request'
import { post } from '../../api/request'

type Bundle = { formatVersion: 1; exportedAt?: string; items: unknown[] }
type ImportResult = {
  dryRun: boolean
  created: number
  updated: number
  skipped: number
  renamed: number
  disabledForCredential: number
  items: Array<{ action: string; id: string; name: string; renamed: boolean; disabledForCredential: boolean }>
}
type Envelope<T> = { data: T }

const props = defineProps<{ canImport: boolean }>()
const emit = defineEmits<{ imported: [] }>()
const open = defineModel<boolean>({ default: false })
const bundle = shallowRef<Bundle | null>(null)
const filename = shallowRef('')
const conflictMode = shallowRef<'skip' | 'rename' | 'update'>('skip')
const preview = shallowRef<ImportResult | null>(null)
const exporting = shallowRef(false)
const checking = shallowRef(false)
const importing = shallowRef(false)

let revision = 0
let fileRevision = 0
let validatedBody = ''
/** 作废已通过预检的结果：冲突模式变化、换文件、关闭弹窗都必须让上次预检失效。 */
function invalidatePreview() {
  revision += 1
  validatedBody = ''
  preview.value = null
  checking.value = false
}
watch(conflictMode, invalidatePreview, { flush: 'sync' })
watch(open, () => { fileRevision += 1; invalidatePreview() }, { flush: 'sync' })
onBeforeUnmount(() => { fileRevision += 1; invalidatePreview() })

/**
 * 导出配置包：GET config-export（responseType=blob），文件名取 content-disposition，兜底 datasource-config.json。
 * 副作用：创建临时 <a> 触发下载，objectURL 在 1s 后释放；导出内容不含凭据（成功提示已说明）。
 */
async function exportConfigurations() {
  exporting.value = true
  try {
    const response = await axios.get('/api/v1/analytics/datasources/config-export', { responseType: 'blob' })
    const name = String(response.headers['content-disposition'] || '').match(/filename="([^"]+)"/)?.[1] || 'datasource-config.json'
    const url = URL.createObjectURL(response.data)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = name
    anchor.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
    ElMessage.success('数据源配置包已导出，凭据未包含在文件中')
  } catch (caught) {
    ElMessage.error('导出失败：' + (caught as Error).message)
  } finally {
    exporting.value = false
  }
}

async function selectFile(event: Event) {
  if (!props.canImport || importing.value) return
  const selectedRevision = ++fileRevision
  invalidatePreview()
  const file = (event.target as HTMLInputElement).files?.[0]
  bundle.value = null
  preview.value = null
  filename.value = file?.name || ''
  if (!file) return
  if (file.size > 1024 * 1024) {
    ElMessage.error('配置包不能超过 1 MB')
    return
  }
  try {
    const parsed = JSON.parse(await file.text()) as Partial<Bundle>
    if (selectedRevision !== fileRevision) return
    if (parsed.formatVersion !== 1 || !Array.isArray(parsed.items) || !parsed.items.length) {
      throw new Error('不是可识别的数据源配置包')
    }
    bundle.value = parsed as Bundle
  } catch (caught) {
    if (selectedRevision !== fileRevision) return
    ElMessage.error('读取配置包失败：' + (caught as Error).message)
  }
}

function requestBody(dryRun: boolean) {
  if (!bundle.value) throw new Error('请先选择配置包')
  return {
    formatVersion: bundle.value.formatVersion,
    exportedAt: bundle.value.exportedAt || '',
    items: bundle.value.items,
    conflictMode: conflictMode.value,
    dryRun,
  }
}

/**
 * 预检（dryRun=true）：把配置包与冲突处理方式提交后端校验，返回逐项动作明细供用户确认；不修改任何数据。
 * 约束：结果只在仍属同一代际（currentRevision === revision）时写入，避免旧响应覆盖新选择的文件。
 */
async function validateImport() {
  if (!props.canImport || !bundle.value || importing.value || checking.value) return
  invalidatePreview()
  const currentRevision = revision
  const body = requestBody(true)
  const signature = JSON.stringify(body)
  checking.value = true
  try {
    const result = (await post<Envelope<ImportResult>>(
      '/api/v1/analytics/datasources/config-import', body,
    )).data
    if (currentRevision !== revision) return
    preview.value = result
    validatedBody = signature
    ElMessage.success('配置包预检通过')
  } catch (caught) {
    if (currentRevision !== revision) return
    preview.value = null
    ElMessage.error('预检失败：' + (caught as Error).message)
  } finally {
    if (currentRevision === revision) checking.value = false
  }
}

/**
 * 正式导入：必须已有 dryRun 预检结果，且当前请求体签名与预检时完全一致（否则要求重新预检）；
 * 成功后 emit('imported') 由父组件刷新列表，并清空签名（防止重复导入同一包）。
 */
async function applyImport() {
  if (!props.canImport || !bundle.value || !preview.value?.dryRun || importing.value || checking.value) return
  if (validatedBody !== JSON.stringify(requestBody(true))) {
    invalidatePreview()
    ElMessage.error('文件或处理方式已变化，请重新预检')
    return
  }
  const currentRevision = revision
  importing.value = true
  try {
    const result = (await post<Envelope<ImportResult>>(
      '/api/v1/analytics/datasources/config-import', requestBody(false),
    )).data
    if (currentRevision === revision) preview.value = result
    validatedBody = ''
    emit('imported')
    ElMessage.success(`配置已导入：新增 ${result.created}，更新 ${result.updated}，跳过 ${result.skipped}`)
  } catch (caught) {
    ElMessage.error('导入失败：' + (caught as Error).message)
  } finally {
    importing.value = false
  }
}
</script>

<template>
  <el-dialog v-model="open" title="数据源配置迁移" width="min(700px, 96vw)" :close-on-click-modal="false" :show-close="!importing" :close-on-press-escape="!importing">
    <div class="transfer-grid">
      <section class="transfer-block">
        <div class="block-head"><i class="fa-solid fa-file-export" /><div><h3>导出配置</h3><p>导出当前授权范围内的连接参数、说明和访问范围。</p></div></div>
        <el-button :loading="exporting" @click="exportConfigurations">下载 JSON 配置包</el-button>
      </section>
      <section class="transfer-block">
        <div class="block-head"><i class="fa-solid fa-file-import" /><div><h3>批量导入</h3><p>整包预检通过后再原子写入，不会导入密码或覆盖业务数据。</p></div></div>
        <input type="file" accept="application/json,.json" aria-label="选择数据源 JSON 配置包" :disabled="!canImport || importing" @change="selectFile" />
        <div class="import-options">
          <label>同名配置</label>
          <el-select v-model="conflictMode" :disabled="!canImport || importing" aria-label="同名配置处理方式">
            <el-option value="skip" label="跳过" />
            <el-option value="rename" label="自动重命名" />
            <el-option value="update" label="更新现有配置" />
          </el-select>
          <span>{{ filename || '尚未选择文件' }}</span>
        </div>
        <div class="import-actions">
          <el-button :disabled="!bundle || !canImport || importing" :loading="checking" @click="validateImport">预检</el-button>
          <el-button type="primary" :disabled="!preview || !canImport || !preview.dryRun || checking" :loading="importing" @click="applyImport">确认导入</el-button>
        </div>
      </section>
    </div>

    <dl v-if="preview" class="preview-summary">
      <div><dt>新增</dt><dd>{{ preview.created }}</dd></div>
      <div><dt>更新</dt><dd>{{ preview.updated }}</dd></div>
      <div><dt>跳过</dt><dd>{{ preview.skipped }}</dd></div>
      <div><dt>重命名</dt><dd>{{ preview.renamed }}</dd></div>
      <div><dt>待补凭据</dt><dd>{{ preview.disabledForCredential }}</dd></div>
    </dl>
    <p class="security-note"><i class="fa-solid fa-shield-halved" />需要密码的新增连接会保持停用，管理员须在编辑页重新安全填写凭据。</p>
  </el-dialog>
</template>

<style scoped>
.transfer-grid { display: grid; grid-template-columns: .82fr 1.18fr; gap: 14px; }
.transfer-block { min-width: 0; padding: 16px; border: 1px solid #dce4ec; background: #fff; border-radius: 6px; }
.block-head { display: flex; align-items: flex-start; gap: 10px; margin-bottom: 15px; }
.block-head > i { display: grid; width: 30px; height: 30px; place-items: center; color: #2867a4; background: #eaf1f8; border-radius: 4px; }
.block-head h3 { margin: 0 0 5px; color: #173956; font-size: 13px; }
.block-head p { margin: 0; color: #5d7185; font-size: 10.5px; line-height: 1.55; }
input[type='file'] { width: 100%; color: #52677e; font-size: 11px; }
.import-options { display: grid; grid-template-columns: 74px 150px minmax(0, 1fr); align-items: center; gap: 8px; margin-top: 14px; }
.import-options label { color: #52677e; font-size: 11px; }
.import-options span { overflow: hidden; color: #596e83; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.import-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
.preview-summary { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); margin: 16px 0 0; border: 1px solid #dce4ec; background: #f8fafc; }
.preview-summary div { padding: 10px 12px; border-right: 1px solid #e1e7ed; }
.preview-summary div:last-child { border-right: 0; }
.preview-summary dt { color: #596d81; font-size: 9px; }
.preview-summary dd { margin: 3px 0 0; color: #194c7c; font: 650 17px ui-monospace, Consolas, monospace; }
.security-note { display: flex; align-items: center; gap: 7px; margin: 14px 0 0; color: #5c7084; font-size: 10.5px; }
.security-note i { color: #2d7963; }
@media (max-width: 700px) {
  .transfer-grid { grid-template-columns: 1fr; }
  .import-options { grid-template-columns: 76px minmax(0, 1fr); }
  .import-options span { grid-column: 1 / -1; }
  .preview-summary { grid-template-columns: repeat(3, 1fr); }
}
</style>
