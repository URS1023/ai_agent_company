<script setup lang="ts">
/**
 * SQL 工作区左侧的元数据浏览器（数据源选择 + 表/字段清单）。
 * 路由：无独立路由，由 SqlWorkspace（/analytics/sql）渲染。
 * 依赖接口：无请求（sources/tables 由 useSqlQuery → useSources 加载后传入）；sample 事件把表名回填到编辑器。
 * 关键交互：表清单支持本地按表名/注释过滤；展开表项即可查看字段与类型；"查询此表"生成基础查询并触发父组件。
 * 约束：只展示已授权（enabled）数据源与其可访问表；空态区分"未选数据源""无匹配表""无已授权表"三种情况。
 */
import { computed, ref } from 'vue'
import type { Source, Table } from '../../types'
const props = defineProps<{ sources: Source[]; tables: Table[]; loading: boolean }>()
const sourceId = defineModel<string>({ required: true })
const emit = defineEmits<{ sample: [table: string] }>()
const keyword = ref('')
const visibleTables = computed(() => props.tables.filter(table =>
  [table.tableName, table.tableComment].some(value => String(value || '').toLowerCase().includes(keyword.value.trim().toLowerCase())),
))
</script>
<template>
  <aside class="metadata-panel" v-loading="loading">
    <label class="control-label" for="sql-source">数据源</label>
    <el-select id="sql-source" v-model="sourceId" placeholder="选择已授权数据源" filterable>
      <el-option v-for="item in sources" :key="item.id" :label="item.name + ' · ' + item.databaseName" :value="item.id" />
    </el-select>
    <el-empty v-if="!sourceId" description="选择后查看表与字段" :image-size="70" />
    <template v-else>
      <el-input v-model="keyword" clearable placeholder="搜索表名或说明" aria-label="搜索数据表" />
      <p class="muted-note">{{ visibleTables.length }} 张表 · 点击表名查看字段</p>
      <el-collapse>
        <el-collapse-item v-for="table in visibleTables" :key="table.tableName" :name="table.tableName" :title="table.tableComment ? table.tableComment + ' · ' + table.tableName : table.tableName">
          <el-button size="small" @click="emit('sample', table.tableName)">查询此表</el-button>
          <ul class="field-list"><li v-for="column in table.columns" :key="column.columnName"><span>{{ column.columnName }}</span><small>{{ column.dataType }} {{ column.comment }}</small></li></ul>
        </el-collapse-item>
      </el-collapse>
      <el-empty v-if="!loading && !visibleTables.length" :description="keyword ? '没有匹配的数据表，请调整搜索词' : '暂无已授权数据表，请检查数据源配置'" :image-size="50" />
    </template>
  </aside>
</template>
