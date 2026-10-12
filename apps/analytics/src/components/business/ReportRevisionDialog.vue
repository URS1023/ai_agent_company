<script setup lang="ts">
/**
 * 报表历史版本弹窗（查看修订并恢复到新草稿）。
 * 路由：无独立路由，由报表管理/设计器在需要回滚时打开。
 * 依赖接口：api/report.js 的 reportApi.revisions（GET /api/v1/reports/{id}/revisions）与 restore（POST .../restore）。
 * 关键交互：恢复前必须二次确认（说明会替换当前未保存编辑、历史记录不删除）；恢复成功后 emit('restored') 由父组件刷新数据。
 * 约束：restore 必须带当前 report.version（乐观锁）与目标 revision；用户取消确认不视为错误（过滤 'cancel'/'close'）。
 * 错误呈现：加载与恢复失败都写入 error 内联展示。
 */
import { onMounted, ref } from 'vue'
import { ElMessageBox } from 'element-plus'
import reportApi from '../../api/report'
import { can } from '../../api/request'
const props = defineProps<{ report: Record<string, any> }>()
const emit = defineEmits<{ close: []; restored: [report: Record<string, any>] }>()
const rows = ref<Record<string, any>[]>([])
const loading = ref(false)
const restoring = ref(false)
const error = ref('')
const selected = ref<Record<string, any> | null>(null)
async function load() {
  loading.value = true; error.value = ''
  try { rows.value = (await reportApi.revisions(props.report.id)).data }
  catch (cause) { error.value = (cause as Error).message }
  finally { loading.value = false }
}
function count(row: Record<string, any>) {
  try { return JSON.parse(row.snapshot.content).widgets.length } catch { return '—' }
}
/** 恢复指定修订为新草稿：GET/POST 走 reportApi，成功后把新报表数据交给父组件（由父组件决定如何刷新编辑器）。 */
async function restore(row: Record<string, any>) {
  try {
    await ElMessageBox.confirm('将历史版本恢复为新草稿，当前未保存的编辑会被替换。历史记录不会删除。', '恢复版本 ' + row.revision, { type: 'warning' })
    restoring.value = true; error.value = ''
    const result = await reportApi.restore(props.report.id, { version: props.report.version, revision: row.revision })
    emit('restored', result.data)
  } catch (cause) { if (cause !== 'cancel' && cause !== 'close') error.value = (cause as Error).message }
  finally { restoring.value = false }
}
function download() {
  if (!selected.value?.available || !can('dashboards.export')) return
  const href = URL.createObjectURL(new Blob([selected.value.snapshot.content], { type: 'application/json' }))
  const link = document.createElement('a'); link.href = href
  link.download = 'report-' + props.report.id + '-v' + selected.value.revision + '.json'; link.click()
  setTimeout(() => URL.revokeObjectURL(href), 1000)
}
onMounted(load)
</script>
<template>
  <el-dialog :model-value="true" title="报表版本历史" width="min(1000px, 96vw)" @close="emit('close')">
    <p>{{ report.reportName }} · 当前加载版本 {{ report.version }}。并发修改时恢复操作会被拒绝，请先刷新确认。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-table :data="rows" v-loading="loading" max-height="420" empty-text="暂无历史版本">
      <el-table-column prop="revision" label="版本" width="75" /><el-table-column prop="reason" label="操作" min-width="140" />
      <el-table-column prop="createTime" label="时间（UTC）" min-width="200" />
      <el-table-column label="组件" width="75"><template #default="{ row }">{{ count(row) }}</template></el-table-column>
      <el-table-column label="操作" min-width="190"><template #default="{ row }">
        <template v-if="row.available"><el-button link @click="selected = row">查看配置</el-button>
        <el-button v-if="can('dashboards.edit')" link type="primary" :disabled="restoring" @click="restore(row)">恢复此版本</el-button></template>
        <span v-else>缺少历史数据源权限</span>
      </template></el-table-column>
    </el-table>
    <details v-if="selected" open><summary>版本 {{ selected.revision }} · 完整配置</summary>
      <el-button v-if="can('dashboards.export')" size="small" @click="download">下载 JSON</el-button>
      <pre>{{ selected.snapshot.content }}</pre>
    </details>
    <template #footer><el-button @click="load">刷新列表</el-button><el-button @click="emit('close')">关闭</el-button></template>
  </el-dialog>
</template>
