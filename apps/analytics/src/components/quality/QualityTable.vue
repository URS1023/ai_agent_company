<script setup lang="ts">
/**
 * 质检记录表格（指标 / 模板 / 质检记录三种 kind 共用）。
 * 路由：无独立路由，由 QualityWorkbench（/quality/:section）渲染。
 * 依赖接口：数据由父组件传入（api/quality.ts 的 quality.list），本组件不发请求；
 *   枚举中文取自 metricCategories / qualityOutcomes，操作按钮受 can('quality.edit' | 'quality.delete') 控制。
 * 关键交互：列随 kind 变化 —— 指标看类别与标准区间、模板看设备类型与修订号、记录看原始/复核判定；
 *   模板额外提供"修订标准"入口；记录行的删除按钮语义是"归档"（证据与复核历史保留）。
 * 约束：行高与操作列宽由 actionWidth 计算得出，保证按钮不换行；示例数据（DEMO-/示例·）打标提示。
 */
import { computed } from 'vue'
import { can } from '../../api/request'
import { metricCategories, qualityOutcomes, type QualityKind, type QualityRecord } from '../../api/quality'
const props = defineProps<{ rows: QualityRecord[]; kind: QualityKind; disabled: boolean }>()
defineEmits<{ open: [row: QualityRecord]; remove: [row: QualityRecord]; history: [row: QualityRecord] }>()
const actionWidth = computed(() => props.kind === 'templates' ? 230 : props.kind === 'inspections' ? 150 : 170)
/** 识别示例数据（样本号 DEMO- 前缀或名称以"示例·"开头），用于列表打标，避免被当作真实质检记录。 */
function isExample(row: QualityRecord) {
  return String(row.sampleId || '').startsWith('DEMO-') || String(row.name || '').startsWith('示例·')
}
</script>
<template>
  <div class="wf-table-wrap" tabindex="0" role="region" aria-label="质量检查记录列表"><table class="wf-table quality-table"><colgroup><col><col><col><col :style="{ width: actionWidth + 'px' }"></colgroup><thead><tr><th>{{ kind === 'inspections' ? '样本 / 批次' : '名称' }}</th><th>{{ kind === 'metrics' ? '检测类别' : kind === 'templates' ? '适用设备类别' : '原始判定' }}</th><th>{{ kind === 'inspections' ? '复核后判定' : '标准 / 修订' }}</th><th>操作</th></tr></thead><tbody><tr v-for="row in rows" :key="row.id"><td>{{ row.name || row.sampleId }}<span v-if="isExample(row)" class="quality-example-tag">示例</span><small>{{ row.updateTime || row.createTime }}</small></td><td>{{ kind === 'metrics' ? metricCategories[row.category] : kind === 'templates' ? row.deviceType || '不限设备类别' : qualityOutcomes[row.outcome] }}</td><td>{{ kind === 'inspections' ? qualityOutcomes[row.effectiveOutcome] : kind === 'metrics' && row.category !== 'appearance' ? '[' + (row.standardMin ?? '不限') + ', ' + (row.standardMax ?? '不限') + '] ' + row.unit : 'v' + row.version }}<small v-if="kind !== 'inspections'">{{ row.status === 'active' ? '启用' : '停用' }}</small></td><td class="quality-action-cell" :style="{ minWidth: actionWidth + 'px' }"><div class="wf-actions"><button type="button" :disabled="disabled" @click="$emit('open', row)">{{ kind === 'inspections' ? '查看报告' : can('quality.edit') ? '编辑' : '查看' }}</button><button v-if="kind === 'templates'" type="button" :disabled="disabled" @click="$emit('history', row)">修订标准</button><button v-if="can('quality.delete')" type="button" class="wf-danger" :disabled="disabled" @click="$emit('remove', row)">{{ kind === 'inspections' ? '归档' : '移除' }}</button></div></td></tr><tr v-if="!rows.length"><td colspan="4">当前筛选下暂无可访问记录。可先维护质检指标，再建立检验模板。</td></tr></tbody></table></div>
</template>
