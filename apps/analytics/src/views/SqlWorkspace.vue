<script setup lang="ts">
/**
 * SQL 查询工作区页面（路由 /analytics/sql，属于数据分析布局的子路由）。
 * 职责：把 useSqlQuery 的状态拆给左侧数据源/表浏览器、SQL 编辑器与结果面板，形成"选源 → 写 SQL → 执行 → 看结果"闭环。
 * 关键交互：编辑器受控绑定 sql/parameters/maxRows/selectedTemplate；模板下拉可套用已有模板；
 *   executing/saving/templateLoading 分别驱动执行、保存、套用模板的按钮 loading 与禁用；
 *   结果面板以 result.queryId 作 key，保证每次执行后重建（避免复用上一次的渲染状态）。
 * 错误呈现：顶部 el-alert 展示 error（来自 composable，含后端 message）。
 * 约束：页面本身不直接调用接口，所有请求与权限判断都在 useSqlQuery / 子组件内完成。
 */
import { useSqlQuery } from '../composables/useSqlQuery'
import QueryResultPanel from '../components/business/QueryResultPanel.vue'
import SqlSourceBrowser from '../components/business/SqlSourceBrowser.vue'
import SqlQueryEditor from '../components/business/SqlQueryEditor.vue'

const { sources, tables, sourceId, source, loading, error, sql, parameterJson, maxRows, executing, saving,
  result, sqlTemplates, selectedTemplate, templateLoading, sampleTable, run, saveTemplate, useTemplate, stopWaiting } = useSqlQuery()
</script>
<template>
  <div class="sql-workspace">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <div class="sql-layout">
      <SqlSourceBrowser v-model="sourceId" :sources="sources" :tables="tables" :loading="loading" @sample="sampleTable" />
      <SqlQueryEditor v-model:sql="sql" v-model:parameters="parameterJson" v-model:max-rows="maxRows"
        v-model:selected-template="selectedTemplate" :templates="sqlTemplates" :source-id="sourceId"
        :row-limit="source?.maxRows || 10000" :executing="executing" :saving="saving" :template-loading="templateLoading"
        @run="run" @save="saveTemplate" @use-template="useTemplate" @stop="stopWaiting" />
    </div>
    <QueryResultPanel v-if="result" :key="result.queryId" :result="result" />
  </div>
</template>
