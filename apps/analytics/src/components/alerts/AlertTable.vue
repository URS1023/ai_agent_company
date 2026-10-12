<script setup lang="ts">
/**
 * 预警模块通用记录表格（设备台账/测点/专家经验/预警策略/预警报告/问题列表共用）。
 * 路由：无独立路由，由 AlertWorkbench（/alerts/:section）与 AlertReportView 内嵌使用。
 * 依赖接口：数据本身由父组件传入（api/alerts.ts 的 alerts.list），本组件只做列定义与展示；
 *   中文枚举取自 api/alerts.ts 的 equipmentStatus/outcomeNames 与 api/workflows.ts 的 flowStatus。
 * 关键交互：按 section 决定列与操作按钮 —— 报告看报告、问题找来源报告/处置、登记表编辑/修订历史/配置预警流程/移除；
 *   按钮可用性由 can(permission + '.edit'/'.delete') 与 busy 忙碌锁共同决定，无编辑权限时按钮文案降级为"查看"。
 * 约束：searches 由父组件负责，本组件不发请求；示例数据（DEMO- 前缀或"示例·"）额外打标，避免被当作真实台账使用。
 */
import { computed } from 'vue'
import { can } from '../../api/request'
import { equipmentStatus, outcomeNames, type AlertRecord, type AlertSection } from '../../api/alerts'
import { flowStatus } from '../../api/workflows'
import { formatRecordTime } from '../../utils/dateTime'
const props = defineProps<{ rows: AlertRecord[]; section: AlertSection; busy: boolean; permission: string; reportContext?: boolean }>()
defineEmits<{ edit: [row: AlertRecord]; remove: [row: AlertRecord]; report: [id: string]; handle: [row: AlertRecord]; history: [row: AlertRecord]; workflow: [row: AlertRecord] }>()
/** 各 section 的列定义（键必须是后端字段名，值为中文表头）；未列出的 section 取不到列并会渲染空表。 */
const columns = computed(() => ({
  devices: [['code', '设备编码'], ['name', '设备名称'], ['category', '类型'], ['location', '安装位置'], ['status', '状态']],
  points: [['code', '测点号'], ['name', '测点名称'], ['fieldName', '采集字段'], ['unit', '单位'], ['status', '状态']],
  advice: [['name', '经验名称'], ['operatingCondition', '适用工况'], ['planShort', '预案简称'], ['status', '状态']],
  rules: [['name', '策略名称'], ['version', '当前修订'], ['recordKeyField', '问题定位字段'], ['status', '状态']],
  reports: [['title', '报告名称'], ['deviceName', '设备'], ['outcome', '判定结果'], ['executionStatus', '流程状态'], ['createTime', '创建时间']],
  problems: [['recordKey', '记录定位'], ['ruleName', '规则项'], ['message', '问题说明'], ['severity', '等级'], ['status', '处置状态']],
}[props.section]))
const badgeColumn = computed(() => props.section === 'reports' ? 'title' : props.section === 'problems' ? 'ruleName' : 'name')
const actionWidth = computed(() => props.section === 'rules' ? 390 : props.section === 'problems' ? 230 : props.section === 'reports' ? 130 : 170)
/** 单元格取值：时间/状态/判定结果/流程状态/等级分别走各自的映射表，未知枚举与空值统一回退为原值或 '—'。 */
function value(row: AlertRecord, key: string) { return key === 'createTime' ? formatRecordTime(row[key]) : key === 'status' ? equipmentStatus[row[key]] : key === 'outcome' ? outcomeNames[row[key]] : key === 'executionStatus' ? flowStatus[row[key]] : key === 'severity' ? ({ info: '提示', warning: '警告', critical: '严重' } as Record<string, string>)[row[key]] : row[key] ?? '—' }
/** 识别示例/演示记录（编码 DEMO- 前缀或名称以"示例·"开头），用于在列表上打标以免误当真数据。 */
function isExample(row: AlertRecord) { return String(row.code || '').startsWith('DEMO-') || [row.name, row.title, row.ruleName].some(item => String(item || '').startsWith('示例·')) }
</script>
<template>
  <div class="wf-table-wrap" tabindex="0" role="region" aria-label="设备预警记录列表">
    <table class="wf-table alert-table">
      <colgroup><col v-for="column in columns" :key="column[0]" /><col :style="{ width: actionWidth + 'px' }" /></colgroup>
      <thead><tr><th v-for="column in columns" :key="column[0]" scope="col">{{ column[1] }}</th><th scope="col">操作</th></tr></thead>
      <tbody>
        <tr v-for="row in rows" :key="row.id"><td v-for="column in columns" :key="column[0]">{{ value(row, column[0]) }}<span v-if="column[0] === badgeColumn && isExample(row)" class="alert-example-tag">示例</span></td><td class="alert-action-cell" :style="{ minWidth: actionWidth + 'px' }"><div class="wf-actions">
          <template v-if="section === 'reports'"><button type="button" :disabled="busy" @click="$emit('report', row.id!)">查看报告</button></template>
          <template v-else-if="section === 'problems'"><button v-if="!reportContext" type="button" :disabled="busy" @click="$emit('report', row.reportId)">来源报告</button><button type="button" :disabled="busy" @click="$emit('handle', row)">{{ can('alerts.edit') ? '处理问题' : '处置历史' }}</button></template>
          <template v-else>
            <button v-if="!can(permission + '.edit')" type="button" :disabled="busy" @click="$emit('edit', row)">查看</button><button v-if="can(permission + '.edit')" type="button" :disabled="busy" @click="$emit('edit', row)">编辑</button>
            <template v-if="section === 'rules'"><button type="button" :disabled="busy" @click="$emit('history', row)">修订历史</button><button v-if="can('workflows.create') && can('workflows.publish') && can('alerts.publish')" type="button" :disabled="busy" @click="$emit('workflow', row)">配置预警流程</button></template>
            <button v-if="can(permission + '.delete')" type="button" class="wf-danger" :disabled="busy" @click="$emit('remove', row)">移除</button>
          </template>
        </div></td></tr>
        <tr v-if="!rows.length"><td :colspan="columns.length + 1">当前筛选下没有可访问的记录。</td></tr>
      </tbody>
    </table>
  </div>
</template>
