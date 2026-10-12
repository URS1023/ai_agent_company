<script setup lang="ts">
/**
 * 结果写入目标管理（路由 /datasources?type=writeback，权限 datasources.writeback + datasources.edit 才可编辑）。
 * 职责：列出已授权的写入目标（表/字段/方式/版本状态），支持新增、编辑、移除授权。
 * 依赖接口：useWriteTargets（api/writeback.ts + api/analytics 数据源与表结构）。
 * 关键交互：编辑弹窗未保存时关闭需确认；离开路由时若编辑器打开也需确认（避免丢弃配置）；
 *   保存只登记授权，不会写入任何业务数据；移除授权不影响已写入的数据与历史凭据。
 * 错误呈现：列表错误与表结构错误（metadataError）分别展示；表结构失败时禁用保存按钮（字段无法校验）。
 */
import { onBeforeRouteLeave } from 'vue-router'
import { can } from '../../api/request'
import { modeNames } from '../../api/writeback'
import { useWriteTargets } from '../../composables/useWriteTargets'
import WriteTargetForm from './WriteTargetForm.vue'
import '../../styles/workflows.css'
import '../../styles/writeback.css'
const { rows, sources, tables, form, editor, busy, loading, offset, total, hasMore, error, metadataError, load, open, save, remove } = useWriteTargets()
const close = (done: () => void) => { if (!busy.value && window.confirm('离开目标编辑？未保存的配置将不保留。')) done() }
onBeforeRouteLeave(() => !editor.value || window.confirm('目标配置尚未保存，确定离开？'))
</script>
<template>
  <section class="workflow-ui write-targets"><div class="wf-heading"><div><h2>结果写入目标</h2><p>为工作流明确授权目标表与字段；配置保存不会自动写入数据。</p></div><button v-if="can('datasources.writeback') && can('datasources.edit')" :disabled="busy" @click="open()">新增写入目标</button></div>
    <p v-if="error && !editor" role="alert" class="wf-error">{{ error }}</p><p v-if="loading" role="status">正在读取目标…</p>
    <div class="wf-table-wrap" tabindex="0" role="region" aria-label="结果写入目标列表"><table class="wf-table"><thead><tr><th>名称</th><th>业务表</th><th>允许字段</th><th>方式</th><th>版本 / 状态</th><th>操作</th></tr></thead><tbody><tr v-for="row in rows" :key="row.id"><td>{{ row.name }}</td><td>{{ row.tableName }}</td><td>{{ row.columns.join('、') }}</td><td>{{ row.modes.map(mode => modeNames[mode]).join('、') }}</td><td>v{{ row.version }} · {{ row.status === 'active' ? '启用' : '停用' }}</td><td><div v-if="can('datasources.writeback') && can('datasources.edit')" class="wf-actions"><button :disabled="busy" @click="open(row)">编辑目标</button><button class="wf-danger" :disabled="busy" @click="remove(row)">移除授权</button></div><span v-else>只读</span></td></tr><tr v-if="!rows.length"><td colspan="6">没有可访问的写入目标，请先接入数据库，再明确授权业务表字段。</td></tr></tbody></table></div>
    <div class="wf-actions"><span>共 {{ total }} 项 · 第 {{ offset / 30 + 1 }} 页</span><button :disabled="loading || busy || !offset" @click="load(offset - 30)">上一页</button><button :disabled="loading || busy || !hasMore" @click="load(offset + 30)">下一页</button></div>
    <el-dialog class="write-target-dialog" v-model="editor" title="配置结果写入目标" width="min(780px, 96vw)" :close-on-click-modal="false" :before-close="close"><form id="write-target-form" class="workflow-ui" @submit.prevent="save"><WriteTargetForm v-model="form" :sources="sources" :tables="tables" :disabled="busy" /><p v-if="error || metadataError" role="alert" class="wf-error">{{ error || metadataError }}</p></form><template #footer><div class="workflow-ui"><div class="wf-actions"><button type="button" :disabled="busy" @click="close(() => editor = false)">取消</button><button type="submit" form="write-target-form" :disabled="busy || !!metadataError">保存写入授权</button></div></div></template></el-dialog>
  </section>
</template>
