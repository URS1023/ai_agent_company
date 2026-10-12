<script setup lang="ts">
/**
 * 数据源数据结构浏览弹窗（表/索引清单 + 单表字段详情 + 跳转查询）。
 * 路由：无独立路由，由 DataSourceManage 在 /datasources 内以 v-model:open 控制显隐。
 * 依赖接口：GET /api/v1/analytics/datasources/{id}/tables/info 与 .../tables/{tableName}/schema（表名需编码）。
 * 关键交互：搜索在本地按表名/表注释过滤；点某行查看字段明细（columnsOpen 二级弹窗）；
 *   query 事件把表名上抛给父组件跳转到查询页（本组件不直接导航）。
 * 约束：只读元数据接口，不执行任何业务查询；行数/体积未统计时显示"未统计"而不是 0，避免误导。
 * 错误呈现：加载失败用 ElMessage.error 提示，弹窗保持可重试（关闭再打开会重新拉取）。
 */
import { computed, shallowRef, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { can, get } from '../../api/request'
import type { DataSourceColumn, DataSourceSummary, DataSourceTable } from './types'

type Envelope<T> = { data: T }

const props = defineProps<{ source: DataSourceSummary | null }>()
const emit = defineEmits<{ query: [table: DataSourceTable] }>()
const open = defineModel<boolean>({ default: false })
const loading = shallowRef(false)
const tables = shallowRef<DataSourceTable[]>([])
const search = shallowRef('')
const columnsOpen = shallowRef(false)
const columnsLoading = shallowRef(false)
const columns = shallowRef<DataSourceColumn[]>([])
const tableName = shallowRef('')

const filteredTables = computed(() => {
  const keyword = search.value.trim().toLocaleLowerCase()
  if (!keyword) return tables.value
  return tables.value.filter((table) => (
    table.tableName.toLocaleLowerCase().includes(keyword) ||
    (table.tableComment || '').toLocaleLowerCase().includes(keyword)
  ))
})

watch([open, () => props.source?.id], async ([visible]) => {
  if (!visible || !props.source) return
  loading.value = true
  search.value = ''
  tables.value = []
  try {
    const response = await get<Envelope<DataSourceTable[]>>(
      `/api/v1/analytics/datasources/${props.source.id}/tables/info`,
    )
    tables.value = response.data
  } catch (caught) {
    ElMessage.error('加载表结构失败：' + (caught as Error).message)
  } finally {
    loading.value = false
  }
}, { immediate: true })

/** 查看字段明细：GET .../tables/{tableName}/schema（表名必须 encodeURIComponent，表名可能含特殊字符）。 */
async function viewColumns(table: DataSourceTable) {
  if (!props.source) return
  tableName.value = table.tableName
  columnsOpen.value = true
  columnsLoading.value = true
  columns.value = []
  try {
    const response = await get<Envelope<DataSourceTable>>(
      `/api/v1/analytics/datasources/${props.source.id}/tables/${encodeURIComponent(table.tableName)}/schema`,
    )
    columns.value = response.data.columns || []
  } catch (caught) {
    ElMessage.error('加载字段失败：' + (caught as Error).message)
  } finally {
    columnsLoading.value = false
  }
}

function formatNumber(value?: number | null) {
  return value === null || value === undefined ? '未统计' : value.toLocaleString()
}

function formatBytes(value?: number | null) {
  if (value === null || value === undefined) return '未统计'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let number = Number(value)
  let unit = 0
  while (number >= 1024 && unit < units.length - 1) {
    number /= 1024
    unit += 1
  }
  return `${number.toFixed(unit ? 1 : 0)} ${units[unit]}`
}
</script>

<template>
  <el-dialog v-model="open" :title="source ? `${source.name} · 数据结构` : '数据结构'" width="min(1180px, 96vw)">
    <el-input v-model="search" class="schema-search" placeholder="搜索表名或注释" clearable />
    <el-table :data="filteredTables" height="min(520px, 62vh)" v-loading="loading" stripe>
      <el-table-column prop="tableName" label="表 / 索引" min-width="210">
        <template #default="scope">
          <strong class="table-name"><i class="fa-solid fa-table" />{{ scope.row.tableName }}</strong>
        </template>
      </el-table-column>
      <el-table-column prop="tableComment" label="业务注释" min-width="240">
        <template #default="scope">{{ scope.row.tableComment || '未提供注释' }}</template>
      </el-table-column>
      <el-table-column prop="rowCount" label="数据量" width="130" align="right">
        <template #default="scope">{{ formatNumber(scope.row.rowCount) }}{{ scope.row.rowCountEstimated ? '（估）' : '' }}</template>
      </el-table-column>
      <el-table-column prop="dataSizeBytes" label="存储" width="110" align="right">
        <template #default="scope">{{ formatBytes(scope.row.dataSizeBytes) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="190" fixed="right">
        <template #default="scope">
          <el-button size="small" @click="viewColumns(scope.row)">查看字段</el-button>
          <el-button v-if="can('analytics.view')" size="small" type="primary" plain @click="emit('query', scope.row)">查询</el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-dialog>

  <el-dialog v-model="columnsOpen" :title="`字段结构 · ${tableName}`" width="min(940px, 96vw)" append-to-body>
    <el-table :data="columns" v-loading="columnsLoading" stripe max-height="520">
      <el-table-column prop="columnName" label="字段名" min-width="190">
        <template #default="scope">
          <strong class="column-name"><i :class="scope.row.primaryKey ? 'fa-solid fa-key primary-key' : 'fa-regular fa-circle'" />{{ scope.row.columnName }}</strong>
        </template>
      </el-table-column>
      <el-table-column prop="dataType" label="数据类型" width="170" />
      <el-table-column prop="comment" label="字段注释" min-width="240">
        <template #default="scope">{{ scope.row.comment || '未提供注释' }}</template>
      </el-table-column>
      <el-table-column prop="nullable" label="可空" width="80" align="center">
        <template #default="scope">{{ scope.row.nullable ? '是' : '否' }}</template>
      </el-table-column>
    </el-table>
  </el-dialog>
</template>

<style scoped>
.schema-search { max-width: 420px; margin-bottom: 14px; }
.table-name, .column-name { display: inline-flex; align-items: center; gap: 7px; color: #183d62; font-size: 12px; }
.table-name i { color: #2b6ca9; }
.column-name i { color: #a4b0bd; font-size: 8px; }
.column-name .primary-key { color: #b44b42; font-size: 12px; }
</style>
