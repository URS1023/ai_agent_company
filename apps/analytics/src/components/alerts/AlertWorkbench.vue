<script setup lang="ts">
/**
 * 设备预警工作台（路由 /alerts/:section、/alerts/points，以及 AlertWorkspace 内的设备页签）。
 * 职责：按 section（devices/points/advice/rules/reports/problems）展示记录表与筛选，承载新增/编辑弹窗、
 *   预警报告与问题处置弹窗、策略修订历史弹窗，以及"配置并发布预警流程"向导弹窗。
 * 依赖接口：全部通过 useAlerts（api/alerts.ts + api/request.ts）完成；导出报告走 alerts.reports 导出端点。
 * 关键交互：分页固定 30 条；未保存就关闭表单/离开路由都会二次确认（关闭向导另需确认未发布设置会丢失）；
 *   向导创建成功后跳转 /workflows/{id}；"使用说明"弹窗展示业务闭环步骤（BusinessProcessGuide）。
 * 约束：页签与新增按钮均按 can(...) 控制；resultsOnly 模式下隐藏标题与页签（供嵌入型页面复用）。
 * 错误呈现：error 在列表区与各弹窗内分别展示（同一错误源，便于就近看到）。
 */
import { computed, shallowRef, toRef } from 'vue'
import { onBeforeRouteLeave, useRouter } from 'vue-router'
import { can } from '../../api/request'
import { formatRecordTime } from '../../utils/dateTime'
import { alertSections, equipmentStatus, type AlertSection } from '../../api/alerts'
import { useAlerts } from '../../composables/useAlerts'
import EquipmentForm from './EquipmentForm.vue'
import AlertRuleForm from './AlertRuleForm.vue'
import AlertTable from './AlertTable.vue'
import AlertReportView from './AlertReportView.vue'
import AlertWorkflowWizard from './AlertWorkflowWizard.vue'
import BusinessProcessGuide from '../BusinessProcessGuide.vue'
import '../../styles/workflows.css'
import '../../styles/alerts.css'
const props = defineProps<{ section: AlertSection; resultsOnly?: boolean }>(), router = useRouter()
// 使用说明的步骤与注意事项（文案即业务约束，修改需与后端行为一致：保存配置不读取设备等）。
const guideOpen = shallowRef(false)
const guideSteps = [
  { title: '登记设备', description: '维护设备编码、位置、用途，并明确绑定一个已授权的 HTTP 或数据库采集源。', path: '/alerts/devices', action: '设备台账' },
  { title: '映射测点', description: '把来源字段映射为温度、压力、里程等业务测点；保存配置不会读取设备。', path: '/alerts/points', action: '测点管理' },
  { title: '沉淀经验', description: '录入适用工况、告警说明和处置预案，供策略修订固化引用。', path: '/alerts/advice', action: '专家经验' },
  { title: '配置策略', description: '组合比较、范围、AND/OR 与计算字段，明确记录定位字段和问题等级。', path: '/alerts/rules', action: '预警策略' },
  { title: '发布并运行', description: '从策略配置完整工作流，固定来源、字段与规则版本；运行后才会采集真实数据。', path: '/workflows', action: '工作流' },
  { title: '处理闭环', description: '报告保留原始判断，问题列表追加确认、解决或忽略记录，处置不会改写原报告。', path: '/alerts/problems', action: '问题列表' },
]
const guideNotes = [
  '带“示例”标识的记录来自正式 API，可查看、编辑和运行；不覆盖已有业务数据。',
  '数据库只允许只读 SELECT；HTTP 请求体与 JSONPath 在数据源中心保存并固定版本。',
  '无数据、正常和发现问题是三个独立结果；只有实际运行才生成预警报告。',
]
const { rows, loading, busy, error, keyword, statusFilter, offset, devices, advice, sources, points, form, formOpen, editingKind, permission, registry, report, reportOpen, problems, problemOffset, handling, handlingOpen, actionStatus, actionNote, events, eventOffset, revisions, revisionsOpen, workflowRule, workflowOpen,
  load, edit, save, remove, openReport, reportPage, exportReport, openHandling, eventsPage, handle, history, createWorkflow, workflowCreated } = useAlerts(toRef(props, 'section'))
/** 可见页签：按 devices.view / alerts.view 过滤，无权限的页签直接不渲染（避免点进去必然被守卫拦回）。 */
const sections = computed(() => Object.entries(alertSections).filter(([key]) => can((['devices', 'points'].includes(key) ? 'devices' : 'alerts') + '.view')))
/** 保存权限：devices/points 走 devices.*，其余走 alerts.*；新建与编辑分别要求 .create / .edit。 */
const canSave = computed(() => can((['devices', 'points'].includes(editingKind.value) ? 'devices' : 'alerts') + (form.value?.id ? '.edit' : '.create')))
/** 关闭表单前的未保存确认；busy 期间不允许关闭，避免请求在途时表单数据被丢弃。 */
function closeForm(done: () => void) { if (!busy.value && window.confirm('关闭配置表单？尚未保存的修改将丢失。')) done() }
/** 关闭预警流程向导前的确认（向导里可能已选测点/计算/回写，未发布即全部丢弃）。 */
function closeWorkflow(done: () => void) { if (!busy.value && window.confirm('关闭预警流程配置？尚未发布的设置将丢失。')) done() }
/** 使用说明里的跳转：先关弹窗再路由，避免弹窗遮罩残留。 */
async function navigateGuide(path: string) { guideOpen.value = false; await router.push(path) }
// 路由离开守卫：表单打开且未保存时拦截（返回 false 会阻止导航，用户确认后才放行）。
onBeforeRouteLeave(() => !formOpen.value || window.confirm('配置尚未保存，确定离开此页面？'))
function page(next: number) { offset.value = next; void load() }
</script>
<template>
  <div class="business-page workflow-ui alerts-ui"><div v-if="!resultsOnly" class="wf-heading"><div><h1>设备与预警</h1><p>管理设备、测点和预警策略，从实际运行结果追踪问题与处置。</p></div><div class="wf-actions"><button type="button" @click="guideOpen = true">使用说明</button><button v-if="can('workflows.view')" @click="router.push('/workflows')">可视化工作流</button></div></div>
    <nav v-if="!resultsOnly" class="alert-tabs" aria-label="设备预警模块"><RouterLink v-for="[key, label] in sections" :key="key" :to="'/alerts/' + key" :aria-current="section === key ? 'page' : undefined" :class="{ active: section === key }">{{ label }}</RouterLink></nav>
    <section class="alert-content"><div class="wf-heading"><h2>{{ alertSections[section] }}</h2><button v-if="registry && can(permission + '.create')" class="wf-primary" :disabled="busy" @click="edit()">新增{{ { devices: '设备', points: '测点', advice: '经验', rules: '策略', reports: '', problems: '' }[section] }}</button></div>
      <form class="wf-actions alert-filter" @submit.prevent="page(0)"><label v-if="registry">名称<input v-model="keyword" placeholder="输入名称关键词" maxlength="200" /></label><label v-if="section === 'problems'">处置状态<select v-model="statusFilter"><option value="">全部状态</option><option v-for="key in ['open','acknowledged','resolved','ignored']" :key="key" :value="key">{{ equipmentStatus[key] }}</option></select></label><button type="submit" :disabled="busy || loading">查询</button><button type="button" :disabled="busy || loading" @click="load">刷新</button></form>
      <p v-if="error" class="wf-error" role="alert">{{ error }}</p><p v-if="loading" role="status">正在读取已授权记录…</p><AlertTable :rows="rows" :section="section" :permission="permission" :busy="busy || loading" @edit="edit" @remove="remove" @report="openReport" @handle="openHandling" @history="history" @workflow="createWorkflow" /><div class="wf-actions alert-pagination"><button :disabled="busy || loading || !offset" @click="page(offset - 30)">上一页</button><span>第 {{ Math.floor(offset / 30) + 1 }} 页</span><button :disabled="busy || loading || rows.length < 30" @click="page(offset + 30)">下一页</button></div>
    </section>
    <el-dialog v-model="formOpen" :title="alertSections[editingKind] + (form?.id ? ' · 配置' : ' · 新建')" width="min(880px, 96vw)" :close-on-click-modal="false" :before-close="closeForm"><form v-if="form" id="equipment-edit-form" class="workflow-ui alerts-ui" @submit.prevent="save"><AlertRuleForm v-if="editingKind === 'rules'" v-model="form" :devices="devices" :advice="advice" :points="points" :disabled="busy || !canSave" /><EquipmentForm v-else v-model="form" :kind="editingKind" :devices="devices" :sources="sources" :disabled="busy || !canSave" /><p v-if="error" class="wf-error" role="alert">{{ error }}</p></form><template #footer><button :disabled="busy" @click="closeForm(() => formOpen = false)">关闭</button><button v-if="canSave" type="submit" form="equipment-edit-form" class="wf-primary" :disabled="busy">保存配置</button></template></el-dialog>
    <el-dialog v-model="reportOpen" title="预警报告与问题" width="min(1180px, 96vw)"><div class="workflow-ui alerts-ui"><p v-if="error" class="wf-error" role="alert">{{ error }}</p><AlertReportView v-if="report" :report="report" :problems="problems" :offset="problemOffset" :busy="busy" @page="reportPage" @handle="openHandling" @export="exportReport" @workflow="router.push('/workflows/' + $event)" /></div></el-dialog>
    <el-dialog v-model="handlingOpen" title="问题处置与历史" width="min(760px, 96vw)" :close-on-click-modal="false"><div class="workflow-ui alerts-ui"><p>{{ handling?.ruleName }} · {{ handling?.recordKey }}</p><p>{{ handling?.message }}</p><form v-if="can('alerts.edit')" id="problem-action-form" @submit.prevent="handle"><fieldset :disabled="busy"><label>处置状态<select v-model="actionStatus"><option v-for="key in ['open','acknowledged','resolved','ignored']" :key="key" :value="key">{{ equipmentStatus[key] }}</option></select></label><label>处置理由<textarea v-model="actionNote" required maxlength="3000" rows="3" /></label></fieldset></form><p v-if="error" class="wf-error" role="alert">{{ error }}</p><h3>处置历史</h3><ol class="alert-events"><li v-for="event in events" :key="event.id"><strong>{{ equipmentStatus[event.previousStatus] }} → {{ equipmentStatus[event.status] }}</strong><p>{{ event.note }}</p><small>{{ formatRecordTime(event.createTime) }} · 操作人 {{ event.actorId }}</small></li></ol><p v-if="!events.length">暂无人工处置记录。</p><div class="wf-actions"><button :disabled="busy || !eventOffset" @click="eventsPage(eventOffset - 50)">上一页记录</button><button :disabled="busy || events.length < 50" @click="eventsPage(eventOffset + 50)">下一页记录</button></div></div><template #footer><button :disabled="busy" @click="handlingOpen = false">关闭</button><button v-if="can('alerts.edit')" type="submit" form="problem-action-form" class="wf-primary" :disabled="busy">保存处置</button></template></el-dialog>
    <el-dialog v-model="revisionsOpen" title="固定规则修订" width="min(850px, 96vw)"><div class="workflow-ui alerts-ui"><p>工作流按所选修订执行，不随策略编辑自动改变。以下为最近 100 个可访问修订。</p><details v-for="item in revisions" :key="item.revision"><summary>v{{ item.revision }} · {{ formatRecordTime(item.createTime) }}</summary><pre>{{ JSON.stringify(item.snapshot, null, 2) }}</pre></details></div></el-dialog>
    <el-dialog v-model="guideOpen" title="设备预警使用说明" width="min(820px, 96vw)" destroy-on-close><BusinessProcessGuide eyebrow="EQUIPMENT ALERT FLOW" title="从真实数据到问题闭环" summary="先配置台账与规则，再明确发布并运行工作流；报告、问题和处置历史均来自实际执行。" :steps="guideSteps" :notes="guideNotes" @navigate="navigateGuide" /></el-dialog>
    <el-dialog v-model="workflowOpen" title="配置并发布设备预警" class="alert-workflow-dialog" width="min(1180px, 97vw)" :close-on-click-modal="false" :close-on-press-escape="false" :before-close="closeWorkflow" destroy-on-close><div class="workflow-ui alerts-ui"><AlertWorkflowWizard v-if="workflowRule" :rule="workflowRule" @created="workflowCreated" @cancel="closeWorkflow(() => workflowOpen = false)" /></div></el-dialog>
  </div>
</template>
