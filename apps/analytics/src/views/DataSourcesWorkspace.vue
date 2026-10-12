<script setup lang="ts">
/**
 * 数据源工作区页面（路由 /datasources，权限 datasources.view）。
 * 职责：以页签组织三类资源 —— 数据库/文件数据源、HTTP 采集源、结果写入目标；
 *   并承载"从 CSV/Excel 导入创建独立分析数据源"的弹窗流程。
 * 关键交互：页签切换写回 URL 的 ?type=（database 时不带参数），刷新后保持当前页签；
 *   导入成功后用 version++ 触发 DataSourceManage 重新挂载以刷新列表。
 * 约束：导入按钮仅在 can('datasources.create') 时出现；导入是"新建独立数据源"，不覆盖原文件与已有库；
 *   文件大小/行数上限（20MB / 5 万行）由后端校验，前端只做必填与提示。
 * 错误呈现：导入失败以 ElMessage.error 显示后端 message。
 */
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import axios from 'axios'
import { can } from '../api/request'
import DataSourceManage from '../components/DataSourceManage.vue'
import HttpSourcesManager from '../components/collection/HttpSourcesManager.vue'
import WriteTargetsManager from '../components/writeback/WriteTargetsManager.vue'
import { useRoute, useRouter } from 'vue-router'
const route = useRoute()
const router = useRouter()
const sourceTab = ref(['http', 'writeback'].includes(String(route.query.type)) ? String(route.query.type) : 'database')
/** 页签切换：把当前页签写进 URL（database 视为默认值，不写参数），便于刷新与分享链接定位。 */
function changeTab() { void router.replace({ path: '/datasources', query: sourceTab.value !== 'database' ? { type: sourceTab.value } : {} }) }
/** 导入弹窗的局部状态：version 递增用于导入成功后强制重挂载数据源列表组件；table/encoding 为表单默认值。 */
const version = ref(0)
const dialog = ref(false)
const saving = ref(false)
const file = ref<File | null>(null)
const name = ref('')
const table = ref('imported_data')
const encoding = ref('utf-8-sig')
/** 清空导入表单并打开弹窗；file 置空以强制用户重新选择文件（input 无法由程序写值）。 */
function openImport() {
  file.value = null
  name.value = ''
  table.value = 'imported_data'
  encoding.value = 'utf-8-sig'
  dialog.value = true
}
/**
 * 上传表格文件并创建独立分析数据源：POST /api/v1/analytics/datasources/import（multipart）。
 * 关键字段：file（必填）、name、table_name、encoding（utf-8-sig / gb18030）。
 * 副作用：成功后关闭弹窗并 version++（重挂载列表组件以拉取新数据源）。
 * 失败表现：ElMessage.error 展示后端 message；saving 期间按钮 loading 且拦截重复提交。
 */
async function upload() {
  if (saving.value) return
  if (!file.value) { ElMessage.warning('请选择 CSV 或 XLSX 文件'); return }
  const form = new FormData()
  form.append('file', file.value)
  form.append('name', name.value)
  form.append('table_name', table.value)
  form.append('encoding', encoding.value)
  saving.value = true
  try { await axios.post('/api/v1/analytics/datasources/import', form); dialog.value = false; version.value++; ElMessage.success('独立分析数据源已创建，原文件与旧库未覆盖') }
  catch (caught) { ElMessage.error((caught as Error).message) }
  finally { saving.value = false }
}
</script>
<template>
  <div class="business-page datasource-workspace">
    <el-tabs v-model="sourceTab" class="datasource-type-tabs" @tab-change="changeTab"><el-tab-pane label="数据库 / 文件" name="database" /><el-tab-pane label="HTTP 接口" name="http" /><el-tab-pane label="结果写入目标" name="writeback" /></el-tabs>
    <template v-if="sourceTab === 'database'"><div class="file-source-action"><p>数据库连接、元数据和健康检查，也可导入本地表格建立独立数据源。</p><el-button v-if="can('datasources.create')" @click="openImport">从 CSV / Excel 导入</el-button></div>
      <DataSourceManage :key="version" />
    </template>
    <HttpSourcesManager v-else-if="sourceTab === 'http'" /><WriteTargetsManager v-else />
    <el-dialog v-model="dialog" title="从表格创建分析数据源" width="min(600px, 96vw)" :close-on-click-modal="false" destroy-on-close>
      <el-form label-position="top">
        <el-form-item label="数据文件" required><input type="file" accept=".csv,.xlsx" aria-label="选择 CSV 或 XLSX 数据文件" @change="file = ($event.target as HTMLInputElement).files?.[0] || null" /></el-form-item>
        <el-form-item label="数据源名称"><el-input v-model="name" placeholder="例如：设备运行记录" /></el-form-item>
        <el-form-item label="数据表名称"><el-input v-model="table" /></el-form-item>
        <el-form-item label="CSV 编码"><el-select v-model="encoding"><el-option value="utf-8-sig" label="UTF-8" /><el-option value="gb18030" label="GB18030 / 中文 Windows 导出" /></el-select></el-form-item>
      </el-form><p class="muted-note">最多 20 MB / 5 万行，第一行为唯一字段名。保留原始文本和编号，数值查询可显式转换字段类型。</p>
      <template #footer><el-button :disabled="saving" @click="dialog = false">取消</el-button><el-button type="primary" :disabled="!file" :loading="saving" @click="upload">导入</el-button></template>
    </el-dialog>
  </div>
</template>
