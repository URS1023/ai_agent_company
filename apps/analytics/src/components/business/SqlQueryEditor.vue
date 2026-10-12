<script setup lang="ts">
/**
 * SQL 编辑器（受控组件：sql / parameters / maxRows / selectedTemplate 均由父组件持有）。
 * 路由：无独立路由，由 SqlWorkspace（/analytics/sql）渲染。
 * 依赖接口：无请求；run/save/useTemplate/stop 事件交父组件（useSqlQuery）处理。
 * 关键交互：Ctrl/⌘ + Enter 执行；模板下拉可选已有查询模板并触发 useTemplate；执行中显示进度并提供"停止等待"。
 * 约束：只允许只读 SELECT / 非递归 WITH（权限与只读性由后端强制）；maxRows 上限取数据源的 rowLimit，
 *   不能超过数据源配置的最大行数；按钮显隐受 can('analytics.create' | 'analytics.execute') 控制。
 */
import { can } from '../../api/request'
import type { QueryTemplate } from '../../types'
defineProps<{ templates: QueryTemplate[]; executing: boolean; saving: boolean; templateLoading: boolean; sourceId: string; rowLimit: number }>()
const sql = defineModel<string>('sql', { required: true })
const parameters = defineModel<string>('parameters', { required: true })
const maxRows = defineModel<number>('maxRows', { required: true })
const selectedTemplate = defineModel<string>('selectedTemplate', { required: true })
const emit = defineEmits<{ run: []; save: []; useTemplate: []; stop: [] }>()
</script>
<template>
  <section class="sql-editor-panel" aria-label="只读 SQL 编辑">
    <div class="editor-toolbar"><h2>只读 SQL</h2><el-select v-model="selectedTemplate" :loading="templateLoading" aria-label="复用查询模板" placeholder="复用查询模板" clearable @change="emit('useTemplate')"><el-option v-for="item in templates" :key="item.id" :label="item.name" :value="item.id" /><template #empty><p class="template-empty">暂无 SQL 模板，编写查询后点击“保存为查询模板”。</p></template></el-select></div>
    <el-input v-model="sql" type="textarea" :rows="11" placeholder="SELECT ...&#10;支持只读 SELECT、非递归 WITH、聚合与命名参数 :name" aria-label="SQL 语句" class="code-input" @keydown.ctrl.enter.prevent="emit('run')" @keydown.meta.enter.prevent="emit('run')" />
    <details class="query-parameters" :open="parameters !== '{}'"><summary>命名参数 JSON</summary><el-input v-model="parameters" type="textarea" :rows="4" aria-label="SQL 命名参数 JSON" class="code-input" /></details>
    <div class="editor-actions"><label>最多返回 <el-input-number v-model="maxRows" :min="1" :max="rowLimit" aria-label="最大返回行数" /> 行</label><div class="sql-buttons">
      <el-button v-if="can('analytics.create')" :loading="saving" :disabled="!sql.trim() || templateLoading" @click="emit('save')">保存为查询模板</el-button>
      <el-button v-if="can('analytics.execute')" type="primary" :loading="executing" :disabled="!sourceId || !sql.trim() || templateLoading" @click="emit('run')">执行查询</el-button>
    </div></div>
    <p class="muted-note">Ctrl / ⌘ + Enter 执行。切换标签时保留本次会话的 SQL 和参数。查询只读，行数与超时受数据源配置限制。</p>
    <div v-if="executing" class="sql-progress" role="status"><span>正在校验并执行查询…</span><el-button text @click="emit('stop')">停止等待</el-button></div>
  </section>
</template>
<style scoped>
.sql-buttons { display:flex; flex-wrap:wrap; gap:8px; }
.sql-buttons :deep(.el-button) { margin-left:0; }
.sql-progress { display:flex; justify-content:space-between; align-items:center; color:#40546c; font-size:13px; }
.template-empty { padding:12px; max-width:280px; font-size:12px; color:#5b6d85; line-height:1.7; }
</style>
