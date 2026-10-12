<script setup lang="ts">
/**
 * 数据源管理主体（数据源工作区的"数据库 / 文件"页签内容）。
 * 路由：无独立路由，由 DataSourcesWorkspace 在 /datasources 内渲染（HTTP 源与写入目标为其它页签）。
 * 依赖接口：useDataSources（api/request.ts → /api/v1/analytics/datasources 系列）；治理策略、表结构、配置导入导出均为弹窗组件。
 * 关键交互：分页与关键字改动都会重新加载（改每页条数时先回到第 1 页）；健康检查、启停、元数据同步、删除均在二次确认后执行；
 *   "配置导入导出"用于跨环境迁移（凭据不随包导出）。
 * 权限：编辑/删除/执行分别对应 datasources.edit / delete / execute，导入需 datasources.create（通过 canImport 传入）。
 * 错误呈现：加载错误用 ElMessage（loadError 优先）+ 卡片内联提示；删除确认被取消不算错误。
 */
import { onMounted, shallowRef, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { can } from '../api/request'
import { useDataSources } from '../composables/useDataSources'
import DataSourceCard from './datasources/DataSourceCard.vue'
import DataSourceEditorDialog from './datasources/DataSourceEditorDialog.vue'
import DataSourcePolicyDialog from './datasources/DataSourcePolicyDialog.vue'
import DataSourceSchemaDialog from './datasources/DataSourceSchemaDialog.vue'
import DataSourceTransferDialog from './datasources/DataSourceTransferDialog.vue'
import type { DataSourceSummary, DataSourceTable } from './datasources/types'

const router = useRouter()
const {
  sources,
  total,
  page: loadedPage,
  loading,
  loadError,
  load,
  remove,
  toggle,
  checkHealth,
  sync,
} = useDataSources()

const currentPage = shallowRef(1)
const searchQuery = shallowRef('')
const appliedKeyword = shallowRef('')
const pageSize = shallowRef(12)
const pageSizes = [12, 24, 36, 48] as const
const selectedSource = shallowRef<DataSourceSummary | null>(null)
const editorOpen = shallowRef(false)
const schemaOpen = shallowRef(false)
const policyOpen = shallowRef(false)
const transferOpen = shallowRef(false)

// 每页条数变化必须回到第 1 页再加载（否则会停留在越界页码导致空列表）；loadedPage 回写保证分页器与后端返回一致。
watch([currentPage, pageSize], ([, size], [, oldSize]) => {
  if (size !== oldSize && currentPage.value !== 1) { currentPage.value = 1; return }
  void refresh()
})
watch(loadedPage, page => { currentPage.value = page })

onMounted(() => {
  void refresh()
})

async function refresh() {
  try {
    await load({ page: currentPage.value, pageSize: pageSize.value, keyword: appliedKeyword.value })
  } catch (caught) {
    ElMessage.error(loadError.value || ('加载数据源失败：' + (caught as Error).message))
  }
}

/** 搜索：把输入写入"已应用关键字"再回到第 1 页；已在首页时直接刷新（避免多余的重置步骤）。 */
function searchSources() {
  appliedKeyword.value = searchQuery.value.trim()
  if (currentPage.value !== 1) currentPage.value = 1
  else void refresh()
}

function addSource() {
  selectedSource.value = null
  editorOpen.value = true
}

function editSource(source: DataSourceSummary) {
  selectedSource.value = source
  editorOpen.value = true
}

function viewSchema(source: DataSourceSummary) {
  selectedSource.value = source
  schemaOpen.value = true
}

function editPolicy(source: DataSourceSummary) {
  selectedSource.value = source
  policyOpen.value = true
}

/** 健康检查：POST .../health-checks，成功显示延迟（毫秒），失败提示连接检查失败（不做自动重试）。 */
async function handleHealth(source: DataSourceSummary) {
  try {
    const result = await checkHealth(source)
    ElMessage.success(`连接正常 · ${result.latencyMs} ms`)
  } catch (caught) {
    ElMessage.error('连接检查失败：' + (caught as Error).message)
  }
}

/** 元数据同步：POST .../sync，成功后按返回的业务表/字段数量提示（会刷新列表以更新同步状态与描述）。 */
async function handleSync(source: DataSourceSummary) {
  try {
    const result = await sync(source)
    const tables = Number(result.businessTables || 0)
    const fields = Number(result.fields || 0)
    ElMessage.success(`元数据已同步：${tables} 张业务表、${fields} 个字段`)
  } catch (caught) {
    ElMessage.error('元数据同步失败：' + (caught as Error).message)
  }
}

async function handleToggle(source: DataSourceSummary) {
  try {
    await toggle(source)
    ElMessage.success(source.enabled ? '数据源已停用' : '数据源已启用')
  } catch (caught) {
    ElMessage.error('状态更新失败：' + (caught as Error).message)
  }
}

async function handleDelete(source: DataSourceSummary) {
  try {
    await ElMessageBox.confirm(
      '移除后停止新查询，但不会删除外部数据库、历史结果或已生成报表。',
      `移除数据源「${source.name}」`,
      { type: 'warning', confirmButtonText: '确认移除', cancelButtonText: '取消' },
    )
    await remove(source)
    ElMessage.success('数据源已移除')
  } catch (caught) {
    if (caught === 'cancel' || caught === 'close') return
    ElMessage.error('移除失败：' + (caught as Error).message)
  }
}

function queryTable(table: DataSourceTable) {
  const source = selectedSource.value
  if (!source) return
  schemaOpen.value = false
  void router.push({
    path: '/data-query',
    query: {
      datasourceId: source.id,
      datasourceName: source.name,
      tableName: table.tableName,
      tableComment: table.tableComment || '',
      queryHint: `查询${table.tableComment || table.tableName}的数据`,
    },
  })
}
</script>

<template>
  <div class="datasource-manage">
    <header class="page-header">
      <div class="header-icon"><i class="fa-solid fa-database" /></div>
      <div class="header-content">
        <h2>数据源管理</h2>
        <span class="item-count">{{ total }} 个连接 · 自动同步注释与语义索引</span>
      </div>
      <div class="header-actions">
        <el-button v-if="can('datasources.view')" plain @click="transferOpen = true">
          <i class="fa-solid fa-arrow-right-arrow-left" />配置迁移
        </el-button>
        <el-button v-if="can('datasources.create')" type="primary" class="create-btn" @click="addSource">
          <i class="fa-solid fa-plus" />添加数据源
        </el-button>
      </div>
    </header>

    <main class="content-container">
      <form class="datasource-search" @submit.prevent="searchSources">
        <el-input v-model="searchQuery" placeholder="搜索数据源名称" aria-label="数据源名称" maxlength="200" clearable @clear="searchSources" />
        <el-button native-type="submit" :loading="loading">搜索</el-button>
        <el-button :disabled="loading" @click="refresh">刷新</el-button>
      </form>
      <el-alert v-if="loadError" :title="loadError" type="error" :closable="false" show-icon />
      <el-empty
        v-if="!loading && !loadError && !sources.length"
        :description="appliedKeyword ? '没有匹配的数据源，请调整搜索条件' : '尚未配置可用数据源'"
      >
        <el-button v-if="can('datasources.create')" type="primary" @click="addSource">添加只读连接</el-button>
      </el-empty>
      <div v-else class="datasource-cards" v-loading="loading">
        <TransitionGroup name="card-list">
          <DataSourceCard
            v-for="source in sources"
            :key="source.id"
            :source="source"
            :can-execute="can('datasources.execute')"
            :can-edit="can('datasources.edit')"
            :can-delete="can('datasources.delete')"
            @health="handleHealth"
            @view="viewSchema"
            @sync="handleSync"
            @policy="editPolicy"
            @edit="editSource"
            @toggle="handleToggle"
            @delete="handleDelete"
          />
        </TransitionGroup>
      </div>
    </main>

    <div v-if="total > 12" class="pagination-wrapper">
      <label class="page-size-picker">
        <span>每页</span>
        <select v-model.number="pageSize" aria-label="每页显示的数据源数量">
          <option v-for="size in pageSizes" :key="size" :value="size">{{ size }} 条</option>
        </select>
      </label>
      <el-pagination
        v-model:current-page="currentPage"
        :page-size="pageSize"
        :total="total"
        layout="total, prev, pager, next, jumper"
        background
      />
    </div>

    <DataSourceEditorDialog v-model="editorOpen" :source="selectedSource" @saved="refresh" />
    <DataSourceSchemaDialog v-model="schemaOpen" :source="selectedSource" @query="queryTable" />
    <DataSourcePolicyDialog
      v-model="policyOpen"
      :source="selectedSource"
      :can-manage-schedule="can('system.operations.manage')"
      @saved="refresh"
    />
    <DataSourceTransferDialog
      v-model="transferOpen"
      :can-import="can('datasources.create')"
      @imported="refresh"
    />
  </div>
</template>

<style scoped src="@/styles/datasource-manage.css"></style>
