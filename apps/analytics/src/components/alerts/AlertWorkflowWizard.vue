<script setup lang="ts">
/**
 * 预警流程向导（在 AlertWorkbench 的"配置并发布设备预警"弹窗内使用）。
 * 职责：分节展示流程名称、固定策略修订、数据采集、测点字段、计算字段与通知/回写/调度选项，
 *   并用底部提示统一展示 incompleteReason（未满足的阻断原因）。
 * 依赖接口：状态全部来自 useAlertWorkflowWizard（api/alerts、api/alertWorkflows、api/collection、api/analytics）；
 *   回写配置复用工作流的 OutputTableForm，字段清单 resultFields 必须与后端预警结果表结构保持一致。
 * 关键交互：提交即"创建并发布"（HTTP 不会在此时发送，数据库只做结构校验）；取消需要父组件二次确认；
 *   回写与调度选项分别受 can('alerts.writeback')+can('datasources.writeback') 与 can('system.operations.manage') 约束。
 * 错误呈现：error / sourceReady / missingFields 都以内联提示展示，避免用户在最后一步才发现缺配置。
 */
import { onMounted } from 'vue'
import { can } from '../../api/request'
import type { AlertRecord } from '../../api/alerts'
import type { AlertWorkflowCreated } from '../../api/alertWorkflows'
import { useAlertWorkflowWizard } from '../../composables/useAlertWorkflowWizard'
import OutputTableForm from '../writeback/OutputTableForm.vue'

const props = defineProps<{ rule: AlertRecord }>()
const emit = defineEmits<{ created: [value: AlertWorkflowCreated]; cancel: [] }>()
const { form, revisions, device, points, source, loading, busy, error, snapshot, rawRequiredFields,
  selectedFields, missingFields, sourceReady, sourceSummary, httpWritesRequired, nodeCount, incompleteReason, canSubmit,
  load, chooseRevision, togglePoint, selectRequiredPoints, addCalculation, removeCalculation,
  updateWriteback, submit } = useAlertWorkflowWizard(props.rule)
// 可写回的业务字段白名单：与后端预警结果表列名一一对应，改一处必须同步另一处。
const resultFields = ['alertReportId', 'deviceId', 'alertRuleId', 'alertRuleRevision', 'outcome', 'rowIndex', 'ruleId', 'ruleName', 'severity', 'message']
/** 提交：只在成功时向父组件抛 created（父组件据此关闭弹窗并跳转工作流编辑页）；失败由向导内 error 展示。 */
async function create() { const value = await submit(); if (value) emit('created', value) }
/** 通知结果多选（发现问题 / 无数据 / 正常）；用集合去重，避免重复勾选写入重复项。 */
function outcome(value: string, selected: boolean) {
  form.value.notification.outcomes = selected
    ? [...new Set([...form.value.notification.outcomes, value])]
    : form.value.notification.outcomes.filter(item => item !== value)
}
onMounted(load)
</script>

<template>
  <form id="alert-workflow-wizard" class="alert-workflow-wizard" @submit.prevent="create">
    <header class="alert-wizard-head">
      <div><span>预警流程</span><h2>{{ snapshot?.name || rule.name }}</h2></div>
      <dl><div><dt>设备</dt><dd>{{ device?.name || '—' }}</dd></div><div><dt>已选测点</dt><dd>{{ form.pointIds.length }}</dd></div><div><dt>节点</dt><dd>{{ nodeCount }}</dd></div></dl>
    </header>

    <p v-if="error" class="wf-error" role="alert">{{ error }}</p><p v-if="loading" role="status">正在读取策略、设备和数据源…</p>
    <section class="alert-wizard-grid">
      <label>流程名称<input v-model="form.name" required maxlength="200" :disabled="busy" /></label>
      <label>固定策略修订<select :value="form.ruleRevision" :disabled="busy || loading" @change="chooseRevision(Number(($event.target as HTMLSelectElement).value))"><option v-for="item in revisions" :key="item.revision" :value="item.revision">v{{ item.revision }} · {{ item.createTime }}</option></select></label>
      <label class="alert-span">业务说明<textarea v-model="form.description" rows="2" maxlength="2000" :disabled="busy" placeholder="可选：产线、班组和适用范围" /></label>
    </section>

    <section class="alert-wizard-section">
      <div class="alert-section-title"><div><h3>数据采集</h3><p>{{ source?.name || '未绑定来源' }} <span v-if="sourceSummary">· {{ sourceSummary }}</span></p></div><RouterLink to="/datasources">数据源配置</RouterLink></div>
      <p v-if="sourceReady" class="wf-error">{{ sourceReady }}</p>
      <div v-if="httpWritesRequired" class="alert-option">
        <label class="alert-option-toggle"><input v-model="form.confirmHttpWrites" type="checkbox" :disabled="busy || loading" /><span>允许此 HTTP 来源执行非 GET 请求</span></label>
        <p class="alert-compact-note">此来源使用 {{ source?.method }}，请求可能改变外部设备或系统。勾选后，发布流程每次运行（含定时运行）均可按上述固定版本发送请求；不授权数据库写回。来源或配置版本改变后需重新确认。</p>
      </div>
      <template v-if="source?.type === 'http'"><div class="alert-source-facts"><span>{{ source.method }}</span><span>请求体 {{ source.bodies?.length || 0 }} 条</span><span>记录路径 {{ source.recordPath }}</span><span>配置版本 v{{ source.version }}</span></div><p class="alert-compact-note">运行时复用已保存的单条或批量 JSON 请求体，并按数据源 JSONPath 先提取字段；创建流程不会请求设备。</p></template>
      <div v-else-if="source" class="alert-db-config"><label class="alert-span">只读 SQL<textarea v-model="form.source.query" rows="5" spellcheck="false" :disabled="busy" placeholder="SELECT device_code, temperature FROM equipment_readings WHERE collected_at >= :since" /></label><label>最多返回行数<input v-model.number="form.source.maxRows" type="number" min="1" max="10000" :disabled="busy" /></label><label>命名参数（JSON 对象）<input v-model="form.source.parametersJson" spellcheck="false" :disabled="busy" /></label></div>
    </section>

    <section class="alert-wizard-section">
      <div class="alert-section-title"><div><h3>选择测点字段</h3><p>必需字段：{{ [...rawRequiredFields].join('、') || '无' }}</p></div><button type="button" :disabled="busy || loading" @click="selectRequiredPoints">勾选必需测点</button></div>
      <div class="alert-point-grid"><label v-for="item in points" :key="item.id" :class="['alert-point', { selected: form.pointIds.includes(item.id || ''), required: rawRequiredFields.has(item.fieldName) }]" ><input type="checkbox" :checked="form.pointIds.includes(item.id || '')" :disabled="busy || item.status !== 'active'" @change="togglePoint(item.id || '', ($event.target as HTMLInputElement).checked)" /><span><strong>{{ item.name }}</strong><small>{{ item.fieldName }}{{ item.unit ? ' · ' + item.unit : '' }}{{ rawRequiredFields.has(item.fieldName) ? ' · 必需' : '' }}</small></span></label></div>
      <p v-if="!points.length && !loading" class="alert-empty">当前设备没有可选测点，请先到测点管理新增字段。</p><p v-if="missingFields.length" class="wf-error">尚缺：{{ missingFields.join('、') }}</p><p v-else-if="selectedFields.size" class="alert-compact-note">仅提取已勾选的 {{ selectedFields.size }} 个字段，不自动加入其他测点。</p>
    </section>

    <section class="alert-wizard-section">
      <div class="alert-section-title"><div><h3>计算字段</h3><p>可选；按顺序执行加、减、乘、除和括号。</p></div><button type="button" :disabled="busy || form.calculations.length >= 100" @click="addCalculation">添加计算</button></div>
      <div v-if="form.calculations.length" class="alert-calculation-list"><div v-for="item in form.calculations" :key="item.key" class="alert-calculation"><label>输出字段<input v-model="item.name" maxlength="120" :disabled="busy" placeholder="例如：折算温度" /></label><label>公式<input v-model="item.formula" maxlength="2000" :disabled="busy" placeholder="temperature * 1.8 + 32" /></label><button type="button" :disabled="busy" @click="removeCalculation(item.key)">移除</button></div></div>
    </section>

    <section class="alert-options">
      <section class="alert-option"><label class="alert-option-toggle"><input v-model="form.notificationEnabled" type="checkbox" :disabled="busy" /><span>站内通知</span></label><fieldset v-if="form.notificationEnabled" :disabled="busy"><label>收件范围<select v-model="form.notification.audience"><option value="executor">本次执行人</option><option v-if="can('system.operations.manage')" value="all">工作区全部启用成员</option></select></label><label>最低问题等级<select v-model="form.notification.minimumSeverity"><option value="info">提示</option><option value="warning">警告</option><option value="critical">严重</option></select></label><div class="alert-outcomes"><label v-for="item in [['issues','发现问题'],['no_data','无数据'],['normal','正常']]" :key="item[0]"><input type="checkbox" :checked="form.notification.outcomes.includes(item[0])" @change="outcome(item[0], ($event.target as HTMLInputElement).checked)" />{{ item[1] }}</label></div></fieldset></section>
      <section v-if="can('alerts.writeback') && can('datasources.writeback')" class="alert-option"><label class="alert-option-toggle"><input v-model="form.writebackEnabled" type="checkbox" :disabled="busy" /><span>写入授权目标表</span></label><OutputTableForm v-if="form.writebackEnabled" :config="form.writeback" :disabled="busy" :fields="resultFields" @update="updateWriteback" /></section>
      <section v-if="can('system.operations.manage')" class="alert-option"><label class="alert-option-toggle"><input v-model="form.scheduleEnabled" type="checkbox" :disabled="busy" /><span>定时运行</span></label><fieldset v-if="form.scheduleEnabled" :disabled="busy"><label>方式<select v-model="form.schedule.scheduleKind"><option value="interval">固定间隔</option><option value="cron">Cron</option></select></label><label>{{ form.schedule.scheduleKind === 'interval' ? '间隔秒数' : '五段 Cron' }}<input v-model="form.schedule.scheduleExpression" /></label><label>时区<input v-model="form.schedule.timezone" /></label><label>创建状态<select v-model="form.schedule.status"><option value="paused">先暂停，核对后启用</option><option value="active">立即启用</option></select></label></fieldset></section>
    </section>

    <footer class="alert-wizard-footer"><div><strong>{{ incompleteReason || '配置已具备发布条件' }}</strong><p>提交后直接创建并发布；HTTP 不会在此时发送，数据库只读取结构校验 SQL。</p></div><div class="wf-actions"><button type="button" :disabled="busy" @click="$emit('cancel')">取消</button><button type="submit" class="wf-primary" :disabled="!canSubmit">{{ busy ? '正在校验并发布…' : '创建并发布' }}</button></div></footer>
  </form>
</template>
