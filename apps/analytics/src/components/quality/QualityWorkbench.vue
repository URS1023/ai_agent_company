<script setup lang="ts">
/**
 * 质检工作台（路由 /quality/:section(metrics|templates|inspections)，权限 quality.view）。
 * 职责：按页签展示指标/模板/质检记录列表，承载新增与编辑弹窗、质检报告与复核弹窗、模板修订历史弹窗、
 *   批量运行面板（QualityRunPanel）与"配置并发布检测流程"向导（QualityWorkflowWizard）。
 * 依赖接口：全部通过 useQuality（api/quality.ts）；运行面板走 api/qualityRuns.ts；向导走 api/qualityWorkflows.ts。
 * 关键交互：分页固定 30 条；质量运行向导/表单未保存时离开路由需确认；向导创建成功后把 qualityWorkflow 写入 URL 并打开运行面板；
 *   从报告里点"查看所属检测批次"会带 qualityWorkflow + qualityRun 跳回运行结果。
 * 权限：canSave 依据是否已有 id 判定 quality.edit / quality.create；"配置检测流程"要求
 *   quality.* 与 workflows.*（view/create/publish）全部具备；"运行检测"要求 quality.execute + workflows.execute。
 * 错误呈现：error 在列表区与各弹窗内分别展示，便于就近定位。
 */
import { computed, shallowRef, toRef } from 'vue'
import { onBeforeRouteLeave, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { can } from '../../api/request'
import { qualitySections, type QualityKind } from '../../api/quality'
import type { QualityWorkflowCreated } from '../../api/qualityWorkflows'
import { useQuality } from '../../composables/useQuality'
import QualityMetricForm from './QualityMetricForm.vue'
import QualityTemplateForm from './QualityTemplateForm.vue'
import QualityInspectionForm from './QualityInspectionForm.vue'
import QualityTable from './QualityTable.vue'
import QualityReport from './QualityReport.vue'
import QualityRunPanel from './QualityRunPanel.vue'
import QualityWorkflowWizard from './QualityWorkflowWizard.vue'
import BusinessProcessGuide from '../BusinessProcessGuide.vue'
import '../../styles/workflows.css'
import '../../styles/quality.css'
const props = defineProps<{ kind: QualityKind; resultsOnly?: boolean }>(), router = useRouter()
const { rows, keyword, offset, loading, busy, error, formOpen, form, report, reportOpen, historyRows, historyOpen, historyOffset, historyId, load, open, save, submitInspection, remove, review, exportReport, history } = useQuality(toRef(props, 'kind'))
const runOpen = shallowRef(!!(router.currentRoute.value.query.qualityRun || router.currentRoute.value.query.qualityWorkflow))
const wizardOpen = shallowRef(false)
const guideOpen = shallowRef(false)
// 使用说明的步骤与注意事项（文案即业务约束：未检测/无缺陷/失败是三种不同结果等）。
const guideSteps = [
  { title: '维护检测指标', description: '分别配置数值范围、指定成分标准，或外观缺陷置信度与阻断等级。', path: '/quality/metrics', action: '质检指标' },
  { title: '创建检验模板', description: '按顺序选择指标并固定当时的完整标准；后续修改不会改写历史报告。', path: '/quality/templates', action: '检验模板' },
  { title: '选择共享设备', description: '设备台账同时服务预警和质检；自动检测还需为设备授权外观或成分服务。', path: '/alerts/devices', action: '设备台账' },
  { title: '录入或运行检测', description: '可人工录入实测值与缺陷，也可通过专用工作流批量调用检测设备。', path: '/quality/inspections', action: '质检记录' },
  { title: '复核与追溯', description: '机器原始判断保持不变，人工复核单独追加依据，并可导出完整证据报告。', path: '/quality/inspections', action: '查看报告' },
]
const guideNotes = [
  '带“示例”标识的指标、模板和记录由正式 API 创建，可直接打开查看完整判定。',
  '外观检测必须明确提交缺陷数组；未检测、无缺陷和检测失败不会被混作同一种结果。',
  '成分与数值使用精确十进制文本和明确单位，不自动换算或隐式四舍五入。',
]
const canSave = computed(() => can(form.value?.id ? 'quality.edit' : 'quality.create'))
const canCreateWorkflow = computed(() => ['quality.view', 'quality.create', 'workflows.view', 'workflows.create', 'workflows.publish'].every(can))
/** 关闭配置弹窗前的未保存确认；busy 期间不允许关闭（请求在途时不能丢数据）。 */
function closeForm(done: () => void) { if (!busy.value && window.confirm('关闭表单？未保存的质检配置或录入将丢失。')) done() }
function closeWizard(done: () => void) { if (window.confirm('关闭检测流程配置？尚未发布的设备、模板和服务选择将丢失。')) done() }
async function navigateGuide(path: string) { guideOpen.value = false; await router.push(path) }
/** 从报告跳到所属检测批次：关闭报告弹窗、打开运行面板，并把 qualityWorkflow + qualityRun 写入 URL 供刷新恢复。 */
function batch() { if (!report.value?.workflowId) return; reportOpen.value = false; runOpen.value = true; void router.replace({ query: { qualityWorkflow: report.value.workflowId, qualityRun: report.value.executionId } }) }
/**
 * 向导创建成功回调：关闭向导、切到运行面板，并在 URL 上只保留新的 qualityWorkflow（清掉旧的 qualityRun，
 * 否则会误展示上一次批次的运行结果）。reused=true 表示后端复用了既有流程，文案相应变为"已找回"。
 */
async function workflowCreated(value: QualityWorkflowCreated) {
  wizardOpen.value = false; runOpen.value = true
  const query: Record<string, any> = { ...router.currentRoute.value.query, qualityWorkflow: value.workflow.id }; delete query.qualityRun
  await router.replace({ query })
  ElMessage.success(value.reused ? '已找回此前创建的检测流程' : '检测流程已创建并发布，可直接添加样本运行')
}
function page(next: number) { offset.value = next; void load() }
onBeforeRouteLeave(() => !(formOpen.value || wizardOpen.value) || window.confirm('质检配置尚未保存，确定离开？'))
</script>
<template>
  <div class="business-page workflow-ui quality-ui"><header v-if="!resultsOnly" class="wf-heading"><div><h1>质量检查</h1><p>设备与指标形成检验标准，实际检测和人工复核分别追溯。</p></div><div class="wf-actions"><button type="button" @click="guideOpen = true">使用说明</button><button v-if="can('devices.view')" type="button" @click="router.push('/alerts/devices')">共享设备台账</button><button v-if="can('workflows.view')" type="button" @click="router.push('/workflows')">检测工作流</button></div></header><nav v-if="!resultsOnly" class="qc-tabs" aria-label="质检模块栏目"><button v-for="(label, key) in qualitySections" :key="key" type="button" :aria-current="kind === key ? 'page' : undefined" :class="{ active: kind === key }" @click="router.push('/quality/' + key)">{{ label }}</button></nav><QualityRunPanel v-if="kind === 'inspections' && runOpen" @report="open({ id: $event })" @completed="load" /><p v-if="error" role="alert" class="wf-error">{{ error }}</p><form class="wf-actions wf-list-filter" @submit.prevent="page(0)"><label>{{ kind === 'inspections' ? '样本 / 批次' : '名称' }}<input v-model="keyword" maxlength="160" /></label><button type="submit" :disabled="busy || loading">查询</button><button v-if="!resultsOnly && kind === 'inspections' && canCreateWorkflow" type="button" :disabled="busy" @click="wizardOpen = true">配置检测流程</button><button v-if="!resultsOnly && kind === 'inspections' && can('quality.execute') && can('workflows.execute')" type="button" class="wf-primary" @click="runOpen = true">运行检测</button><button type="button" v-if="!resultsOnly && can('quality.create')" class="wf-primary" :disabled="busy" @click="open()">{{ kind === 'inspections' ? '新增质检' : kind === 'templates' ? '新增模板' : '新增指标' }}</button></form><p v-if="loading" role="status">正在读取质检记录…</p><QualityTable :rows="rows" :kind="kind" :disabled="busy || loading" @open="open" @remove="remove" @history="history" /><div class="wf-actions wf-pagination"><button type="button" :disabled="busy || loading || !offset" @click="page(offset - 30)">上一页</button><span>第 {{ offset / 30 + 1 }} 页</span><button type="button" :disabled="busy || loading || rows.length < 30" @click="page(offset + 30)">下一页</button></div>
    <el-dialog v-model="wizardOpen" title="配置并发布检测流程" class="quality-dialog" width="min(1240px, 97vw)" :close-on-click-modal="false" :close-on-press-escape="false" :before-close="closeWizard" destroy-on-close><div class="workflow-ui quality-ui"><QualityWorkflowWizard @created="workflowCreated" @cancel="closeWizard(() => wizardOpen = false)" /></div></el-dialog>
    <el-dialog v-model="formOpen" :title="qualitySections[kind] + (form?.id ? ' · 配置' : ' · 新建')" class="quality-dialog" width="min(880px, 96vw)" :close-on-click-modal="false" :before-close="closeForm" destroy-on-close><div class="workflow-ui quality-ui"><QualityInspectionForm v-if="kind === 'inspections'" :disabled="busy || !canSave" @submit="submitInspection" /><form v-else-if="form" id="qc-config-form" @submit.prevent="save"><QualityMetricForm v-if="kind === 'metrics'" v-model="form" :disabled="busy || !canSave" /><QualityTemplateForm v-else v-model="form" :disabled="busy || !canSave" /></form><p v-if="error" class="wf-error" role="alert">{{ error }}</p></div><template #footer><div class="workflow-ui wf-actions"><button type="button" :disabled="busy" @click="closeForm(() => formOpen = false)">关闭</button><button v-if="canSave" type="submit" :form="kind === 'inspections' ? 'qc-inspection-form' : 'qc-config-form'" :disabled="busy" class="wf-primary">{{ kind === 'inspections' ? '保存质检记录' : '保存配置' }}</button></div></template></el-dialog>
    <el-dialog v-model="reportOpen" title="质检报告与复核" class="quality-dialog" width="min(1180px, 96vw)"><div class="workflow-ui quality-ui"><p v-if="error" role="alert" class="wf-error">{{ error }}</p><QualityReport v-if="report" :report="report" :disabled="busy" @review="review" @export="exportReport" @batch="batch" /></div></el-dialog>
    <el-dialog v-model="historyOpen" title="不可变检验标准修订" class="quality-dialog" width="min(880px, 96vw)"><div class="workflow-ui quality-ui"><p>以下标准属于所标明修订；修改指标不会自动更新这些历史标准。</p><details v-for="item in historyRows" :key="item.revision"><summary>v{{ item.revision }} · {{ item.createTime }}</summary><pre tabindex="0">{{ JSON.stringify(item.snapshot, null, 2) }}</pre></details><div class="wf-actions"><button type="button" :disabled="busy || !historyOffset" @click="history({ id: historyId }, historyOffset - 30)">较新修订</button><button type="button" :disabled="busy || historyRows.length < 30" @click="history({ id: historyId }, historyOffset + 30)">更早修订</button></div></div></el-dialog>
    <el-dialog v-model="guideOpen" title="质量检查使用说明" class="quality-dialog" width="min(820px, 96vw)" destroy-on-close><BusinessProcessGuide eyebrow="QUALITY CONTROL FLOW" title="从检验标准到可追溯结论" summary="指标定义判定口径，模板固定标准版本，质检记录保存实际证据，人工复核只追加不覆盖。" :steps="guideSteps" :notes="guideNotes" @navigate="navigateGuide" /></el-dialog>
  </div>
</template>
