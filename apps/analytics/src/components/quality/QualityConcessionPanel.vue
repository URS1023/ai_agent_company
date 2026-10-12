<script setup lang="ts">
/**
 * 让步结算面板（在质检产品工作台内嵌显示，路由 /quality 页面正文下方）。
 * 职责：维护让步规则（增改）、选择质量证据来源（已完成工作流 / 质检记录 / 手工证据）生成审批申请、
 *   按规则试算扣量与结算重量，并对申请做批准/驳回。
 * 依赖接口：api/quality.ts 的 concessionRules / concessions（evaluate/decide/sources）、api/qualityProducts.ts（产品目录）；
 *   状态与分页由 useQualityConcessions 托管。
 * 关键交互（幂等约束）：试算请求用 concessionEvaluationSnapshot 做内容指纹，内容未变时复用上次 requestKey，
 *   避免重复点击产生多份申请；一旦试算成功即清空 lastEvaluation（下次提交视为新请求）。
 * 手工来源的 recordId 在多次试算间保持稳定（manualRecordId），防止同一次手工录入被拆成两条申请。
 * 权限：新增/编辑规则受 can('quality.create' | 'quality.edit')；审批需在申请列表中填写意见后才允许提交。
 * 错误呈现：error 与 productsError 分开展示（产品目录失败不阻塞规则与申请区），各自提供重试入口。
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref, shallowRef } from 'vue'
import { can } from '../../api/request'
import { QUALITY_PAGE_SIZE, concessionEvaluationSnapshot, concessionStatusLabel, normalizeConcessionRuleDraft, quality, readQualityArrayPage, type ConcessionRequest, type ConcessionRule, type ConcessionSourceField, type ConcessionSourceOption, type QualityRecord } from '../../api/quality'
import { qualityProducts, type QualityProduct } from '../../api/qualityProducts'
import { useQualityConcessions } from '../../composables/useQuality'
import { createClientId } from '../../utils/randomId'

const {
  rules, requests, sources, workflowSources, inspectionSources, loading, pageLoading, busy, error,
  rulesHasMore, requestsHasMore, workflowSourcesHasMore, inspectionSourcesHasMore,
  load, loadMoreRules, loadMoreRequests, loadMoreWorkflowSources, loadMoreInspectionSources,
  saveRule, evaluate, decide,
} = useQualityConcessions()
const ruleFormOpen = ref(false)
const sourceKind = ref<'workflow' | 'inspection' | 'manual'>('workflow')
const selectedRuleId = ref('')
const selectedSourceId = ref('')
const inspectionProductCode = ref('')
const sourceFields = reactive({ originalWeight: '', productCode: '', metricValue: '', metricUnit: '', standardMin: '', standardMax: '' })
const ruleForm = reactive<Partial<ConcessionRule>>({
  code: '', name: '', productCodes: [], metricIds: [], weightUnit: '', metricUnit: '', standardMin: null, standardMax: null,
  minDeviation: null, maxDeviation: null, deductionMode: 'rate', deductionValue: '', roundingScale: null,
  roundingMode: 'exact', status: 'active', description: '',
})
const manualForm = reactive({ sampleId: '', productCode: '', originalWeight: '', value: '', standardMin: '', standardMax: '' })
const metrics = ref<QualityRecord[]>([]), products = ref<QualityProduct[]>([])
const productsError = shallowRef(''), productsLoading = shallowRef(false)
let productsController: AbortController | undefined
const metricsOffset = ref(0), metricsHasMore = ref(false), metricsLoading = ref(false)
const decisionNotes = reactive<Record<string, string>>({})
// 上一次试算的内容指纹与请求键：内容不变则复用同一 requestKey（幂等），内容变化才生成新键。
const lastEvaluation = ref<{ snapshot: string; requestKey: string; manualRecordId: string } | null>(null)
const selectedRule = computed(() => rules.value.find(row => row.id === selectedRuleId.value) || null)
const selectedSource = computed(() => sources.value.find(row => row.recordId === selectedSourceId.value && row.kind === sourceKind.value) || null)
const fieldOptions = computed(() => selectedSource.value?.fields || [])
const visibleRequests = computed(() => requests.value)
const metricId = computed(() => selectedRule.value?.metricIds?.[0] || '')
/** 工作流来源是否可试算：规则、来源与指标齐备，且规则要求的每个字段都已映射（缺项由服务端拒绝，前端先拦截）。 */
const workflowReady = computed(() => {
  const rule = selectedRule.value
  const source = selectedSource.value
  if (!rule || sourceKind.value !== 'workflow' || !source?.workflowId || !metricId.value) return false
  return Boolean(sourceFields.originalWeight && sourceFields.metricValue && sourceFields.metricUnit
    && (!rule.productCodes.length || sourceFields.productCode)
    && (!rule.standardMin || sourceFields.standardMin) && (!rule.standardMax || sourceFields.standardMax))
})
const inspectionReady = computed(() => sourceKind.value === 'inspection'
  && Boolean(selectedRule.value && selectedSource.value?.inspectionId && selectedSource.value.hasOriginalWeight
    && (!selectedRule.value.productCodes.length || inspectionProductCode.value))
)
const manualReady = computed(() => {
  const rule = selectedRule.value
  if (!rule || sourceKind.value !== 'manual' || !manualForm.sampleId.trim() || !manualForm.originalWeight.trim() || !manualForm.value.trim()) return false
  return Boolean((!rule.standardMin || manualForm.standardMin.trim()) && (!rule.standardMax || manualForm.standardMax.trim())
    && (!rule.productCodes.length || manualForm.productCode))
})
const selectedMetricId = computed({ get: () => ruleForm.metricIds?.[0] || '', set: (value: string) => { ruleForm.metricIds = value ? [value] : [] } })
const selectedProducts = computed({ get: () => ruleForm.productCodes || [], set: (value: string[]) => { ruleForm.productCodes = value } })
/** 产品下拉候选：目录产品 + 规则里已存在但目录中查不到的历史编码（标注为"历史产品编码"，保证旧规则可编辑）。 */
const productOptions = computed(() => {
  const configured = [...(ruleForm.productCodes || []), ...(selectedRule.value?.productCodes || [])]
  return [...products.value, ...configured.filter((code, index) => !configured.slice(0, index).includes(code) && !products.value.some(product => product.code === code)).map(code => ({ id: code, code, name: '历史产品编码', hasRequirements: true }))]
})

function resetSourceFields() {
  selectedSourceId.value = ''
  inspectionProductCode.value = ''
  Object.assign(sourceFields, { originalWeight: '', productCode: '', metricValue: '', metricUnit: '', standardMin: '', standardMax: '' })
}
function resetRuleForm(row?: ConcessionRule) {
  Object.assign(ruleForm, row ? { ...row, productCodes: [...row.productCodes], metricIds: [...row.metricIds] } : {
    id: undefined, version: undefined, createTime: undefined, updateTime: undefined,
    code: '', name: '', productCodes: [], metricIds: [], weightUnit: '', metricUnit: '', standardMin: null, standardMax: null,
    minDeviation: null, maxDeviation: null, deductionMode: 'rate', deductionValue: '', roundingScale: null,
    roundingMode: 'exact', status: 'active', description: '',
  })
  if (ruleForm.roundingMode === 'exact') ruleForm.roundingScale = null
  ruleFormOpen.value = true
}
function changeRoundingMode(value: ConcessionRule['roundingMode']) {
  ruleForm.roundingMode = value
  if (value === 'exact') ruleForm.roundingScale = null
}
/** 提交规则：先清洗（空串转 null、精确模式去落位参数、剔除空产品/指标），失败时保留弹窗与用户输入。 */
async function submitRule() {
  const payload = normalizeConcessionRuleDraft({ ...ruleForm, productCodes: (ruleForm.productCodes || []).filter(Boolean), metricIds: (ruleForm.metricIds || []).filter(Boolean) })
  await saveRule(payload)
  if (!error.value) ruleFormOpen.value = false
}
function fieldsByType(types: ConcessionSourceField['dataType'][]) {
  return fieldOptions.value.filter(field => types.includes(field.dataType))
}
function syncMetricUnit() {
  const metric = metrics.value.find(item => item.id === selectedMetricId.value)
  if (metric?.unit) ruleForm.metricUnit = metric.unit
}
function pathOf(key: string) { return key ? fieldOptions.value.find(field => field.path === key)?.path || '' : '' }
/**
 * 组装试算请求：计算内容指纹，内容与上次相同则复用 requestKey（幂等，不产生重复申请）。
 * 手工来源额外固定 manualRecordId，保证多次试算指向同一条手工证据。
 */
function prepareEvaluation(payload: Record<string, any>, manual: boolean) {
  const manualRecordId = manual
    ? lastEvaluation.value?.manualRecordId || `manual-${createClientId()}`
    : ''
  const prepared = {
    ...payload,
    source: manual ? { ...payload.source, recordId: manualRecordId } : payload.source,
  }
  const snapshot = concessionEvaluationSnapshot(prepared)
  if (lastEvaluation.value?.snapshot === snapshot) return { ...prepared, requestKey: lastEvaluation.value.requestKey }
  const requestKey = `concession-${createClientId()}`
  lastEvaluation.value = { snapshot, requestKey, manualRecordId }
  return { ...prepared, requestKey }
}
/**
 * 提交试算：按来源类型（workflow / inspection / manual）组装字段映射后调用 evaluate。
 * 约束：字段映射的键必须与后端约定的形状一致（如 `${metricId}.value`、originalWeight）；
 *   试算成功后清空 lastEvaluation，使下一次提交成为新的独立请求。
 */
async function submitEvaluation() {
  const rule = selectedRule.value
  if (!rule) return
  if (sourceKind.value === 'workflow') {
    const source = selectedSource.value
    if (!source || !source.workflowId) return
    const result = await evaluate(prepareEvaluation({
      sampleId: source.recordId,
      productCode: '',
      ruleId: rule.id,
       source: { kind: 'workflow', recordId: source.recordId, workflowId: source.workflowId, fields: {
         originalWeight: pathOf(sourceFields.originalWeight), ...(sourceFields.productCode ? { productCode: pathOf(sourceFields.productCode) } : {}), [`${metricId.value}.value`]: pathOf(sourceFields.metricValue),
         [`${metricId.value}.unit`]: pathOf(sourceFields.metricUnit), ...(sourceFields.standardMin ? { [`${metricId.value}.standardMin`]: pathOf(sourceFields.standardMin) } : {}),
         ...(sourceFields.standardMax ? { [`${metricId.value}.standardMax`]: pathOf(sourceFields.standardMax) } : {}),
      } },
      weightUnit: rule.weightUnit, measurements: [],
    }, false))
    if (result) lastEvaluation.value = null
    return
  }
  if (sourceKind.value === 'inspection') {
    const source = selectedSource.value
    if (!source || !source.inspectionId || !source.hasOriginalWeight) return
    const result = await evaluate(prepareEvaluation({ sampleId: source.sampleId, productCode: inspectionProductCode.value, inspectionId: source.inspectionId,
      ruleId: rule.id, source: { kind: 'inspection', recordId: source.recordId, fields: { report: 'inspection.results' } },
      weightUnit: rule.weightUnit, measurements: [] }, false))
    if (result) lastEvaluation.value = null
    return
  }
  const result = await evaluate(prepareEvaluation({ sampleId: manualForm.sampleId, productCode: manualForm.productCode,
    ruleId: rule.id, source: { kind: 'manual', recordId: 'manual', fields: {
      originalWeight: 'manual.originalWeight', [`${metricId.value}.value`]: `manual.${metricId.value}.value`,
      [`${metricId.value}.standardMin`]: `manual.${metricId.value}.standardMin`, [`${metricId.value}.standardMax`]: `manual.${metricId.value}.standardMax`,
    } }, originalWeight: manualForm.originalWeight, weightUnit: rule.weightUnit,
    measurements: [{ metricId: metricId.value, value: manualForm.value, unit: rule.metricUnit,
      standardMin: manualForm.standardMin || null, standardMax: manualForm.standardMax || null, sourceField: `manual.${metricId.value}.value` }],
  }, true))
  if (result) lastEvaluation.value = null
}
/** 审批：必须填写审批意见才提交（缺意见时静默不发请求）；成功后清空该行的意见草稿。 */
async function makeDecision(row: ConcessionRequest, decision: 'approved' | 'rejected') {
  const note = decisionNotes[row.id] || ''
  if (!note.trim()) return
  const succeeded = await decide(row, decision, note)
  if (succeeded) delete decisionNotes[row.id]
}
async function loadMetrics(reset = false) {
  if (metricsLoading.value || (!reset && !metricsHasMore.value)) return
  metricsLoading.value = true
  try {
    const offset = reset ? 0 : metricsOffset.value
    const page = (await quality.list('metrics', { limit: QUALITY_PAGE_SIZE + 1, offset })).data
    const result = readQualityArrayPage(page)
    metrics.value = reset ? result.items : [...metrics.value, ...result.items]
    metricsOffset.value = offset + result.items.length
    metricsHasMore.value = result.hasMore
  } catch (cause) { if (!error.value) error.value = (cause as Error).message } finally { metricsLoading.value = false }
}
function sourceLabel(row: ConcessionSourceOption) { return row.kind === 'workflow' ? '已完成工作流' : '质检记录' }
async function loadSelectors() {
  await Promise.all([loadMetrics(true), loadProducts()])
}
async function loadProducts() {
  productsController?.abort()
  const controller = new AbortController()
  productsController = controller
  productsLoading.value = true
  productsError.value = ''
  try {
    const response = await qualityProducts(controller.signal)
    if (!controller.signal.aborted) products.value = response.data.products
  } catch (cause) {
    if (!controller.signal.aborted) productsError.value = '产品目录加载失败：' + (cause as Error).message
  } finally {
    if (!controller.signal.aborted) productsLoading.value = false
  }
}
onMounted(() => { void Promise.all([load(), loadSelectors()]) })
onBeforeUnmount(() => productsController?.abort())
</script>

<template>
  <section class="quality-concession-panel" aria-labelledby="quality-concession-title">
    <header class="quality-concession-heading">
      <div><span>INCOMING QUALITY</span><h2 id="quality-concession-title">让步结算</h2><p>选择已完成质量证据，按业务配置规则生成审批申请。只有批准后的结算重量可用于结算。</p></div>
      <button type="button" :disabled="loading || pageLoading || busy" @click="load">刷新</button>
    </header>
    <div v-if="productsError" class="quality-feedback wf-error concession-products-error" role="alert"><p>{{ productsError }}</p><button type="button" :disabled="productsLoading" @click="loadProducts">重试产品目录</button></div>
    <p v-if="error" class="quality-feedback wf-error" role="alert">{{ error }}</p>
    <p v-if="loading" class="quality-feedback" role="status">正在读取让步规则、质量证据和审批记录…</p>
    <template v-else>
      <div class="quality-concession-grid">
        <article class="quality-concession-card">
          <div class="quality-concession-card-heading"><div><h3>已配置规则</h3><p>多指标或不同单位请分别配置规则；系统不会补默认比例。已加载 {{ rules.length }} 条。</p></div><div class="quality-concession-pagination-actions"><button v-if="can('quality.create')" type="button" @click="resetRuleForm()">新增规则</button><button v-if="rulesHasMore" type="button" :disabled="pageLoading || busy" @click="loadMoreRules">加载更多规则</button></div></div>
          <ul class="quality-concession-rules"><li v-for="row in rules" :key="row.id" :class="{ selected: selectedRuleId === row.id }"><button type="button" @click="selectedRuleId = row.id || ''"><strong>{{ row.name }}</strong><small>{{ row.code }} · {{ row.metricIds[0] }} · {{ row.metricUnit }} → {{ row.weightUnit }}</small><small>扣量 {{ row.deductionMode === 'rate' ? '比例' : '固定重量' }} {{ row.deductionValue }} · {{ row.status === 'active' ? '启用' : '停用' }}</small></button><button v-if="can('quality.edit')" type="button" class="link-button" @click="resetRuleForm(row)">编辑</button></li></ul>
          <p v-if="!rules.length" class="quality-concession-empty">尚未配置正式让步规则，所有申请将保持待确认。</p>
        </article>
        <article class="quality-concession-card">
          <div class="quality-concession-card-heading"><div><h3>生成审批申请</h3><p>自动来源由服务端读取已保存执行结果；手工来源保留明确标记。</p></div></div>
          <div class="quality-concession-tabs"><button type="button" :class="{ active: sourceKind === 'workflow' }" @click="sourceKind = 'workflow'; resetSourceFields()">完成的工作流</button><button type="button" :class="{ active: sourceKind === 'inspection' }" @click="sourceKind = 'inspection'; resetSourceFields()">质检记录</button><button type="button" :class="{ active: sourceKind === 'manual' }" @click="sourceKind = 'manual'; resetSourceFields()">手工证据</button></div>
          <label>让步规则<select v-model="selectedRuleId"><option value="">请选择已配置规则</option><option v-for="row in rules.filter(item => item.status === 'active')" :key="row.id" :value="row.id">{{ row.name }} · {{ row.metricIds[0] }}</option></select></label>
          <template v-if="sourceKind === 'workflow'">
            <label>已完成质量工作流<select v-model="selectedSourceId" @change="Object.assign(sourceFields, { originalWeight: '', productCode: '', metricValue: '', metricUnit: '', standardMin: '', standardMax: '' })"><option value="">请选择执行记录</option><option v-for="row in workflowSources" :key="row.recordId" :value="row.recordId">{{ sourceLabel(row) }} · {{ row.label }}</option></select></label><div class="quality-concession-pagination"><span>已加载 {{ workflowSources.length }} 条工作流来源</span><button v-if="workflowSourcesHasMore" type="button" :disabled="pageLoading || busy" @click="loadMoreWorkflowSources">加载更多</button></div>
            <p class="quality-concession-note">请从实际执行结果中选择重量字段；字段名称可以是业务系统中的毛重、净重等名称，服务端只读取你选择的字段。</p>
            <div v-if="selectedSource && metricId" class="quality-concession-fields"><label>原始重量字段<select v-model="sourceFields.originalWeight"><option value="">请选择字段</option><option v-for="field in fieldsByType(['number','text'])" :key="field.path" :value="field.path">{{ field.key }} · {{ field.path }}</option></select></label><label v-if="selectedRule?.productCodes.length">产品编码字段<select v-model="sourceFields.productCode"><option value="">请选择字段</option><option v-for="field in fieldsByType(['text'])" :key="field.path" :value="field.path">{{ field.key }} · {{ field.path }}</option></select></label><label>指标实测字段<select v-model="sourceFields.metricValue"><option value="">请选择字段</option><option v-for="field in fieldsByType(['number','text'])" :key="field.path" :value="field.path">{{ field.key }} · {{ field.path }}</option></select></label><label>指标单位字段<select v-model="sourceFields.metricUnit"><option value="">请选择字段</option><option v-for="field in fieldsByType(['text'])" :key="field.path" :value="field.path">{{ field.key }} · {{ field.path }}</option></select></label><label>标准下限字段<select v-model="sourceFields.standardMin"><option value="">请选择字段</option><option v-for="field in fieldsByType(['number','text'])" :key="field.path" :value="field.path">{{ field.key }} · {{ field.path }}</option></select></label><label>标准上限字段<select v-model="sourceFields.standardMax"><option value="">请选择字段</option><option v-for="field in fieldsByType(['number','text'])" :key="field.path" :value="field.path">{{ field.key }} · {{ field.path }}</option></select></label></div>
          </template>
          <template v-else-if="sourceKind === 'inspection'"><label>已完成质检记录<select v-model="selectedSourceId"><option value="">请选择质检记录</option><option v-for="row in inspectionSources" :key="row.recordId" :value="row.recordId">{{ sourceLabel(row) }} · {{ row.label }}</option></select></label><div class="quality-concession-pagination"><span>已加载 {{ inspectionSources.length }} 条质检来源</span><button v-if="inspectionSourcesHasMore" type="button" :disabled="pageLoading || busy" @click="loadMoreInspectionSources">加载更多</button></div><label v-if="selectedRule?.productCodes.length">产品编码<select v-model="inspectionProductCode"><option value="">请选择产品</option><option v-for="product in productOptions" :key="product.code" :value="product.code">{{ product.name }} · {{ product.code }}</option></select></label><p class="quality-concession-note">服务端将从固定质检报告读取实测值和标准；只有报告快照实际含原始重量时才可计算。产品范围规则需明确选择产品。</p></template>
           <template v-else><div class="quality-concession-fields"><label>样本 / 来料单据<input v-model="manualForm.sampleId" maxlength="160" /></label><label>产品编码<select v-model="manualForm.productCode"><option value="">配置全产品或不指定</option><option v-for="product in productOptions" :key="product.code" :value="product.code">{{ product.name }} · {{ product.code }}</option></select></label><label>实际原始重量<input v-model="manualForm.originalWeight" inputmode="decimal" placeholder="按规则重量单位填写" /></label><label>指标实测值<input v-model="manualForm.value" inputmode="decimal" /></label><label>实际标准下限<input v-model="manualForm.standardMin" inputmode="decimal" /></label><label>实际标准上限<input v-model="manualForm.standardMax" inputmode="decimal" /></label></div><p class="quality-concession-note">手工来源会固定为 manual，并要求审批；不会被标记为数据库或工作流自动读取。</p></template>
           <button type="button" class="wf-primary" :disabled="!can('quality.create') || busy || !selectedRuleId || (sourceKind === 'workflow' && !workflowReady) || (sourceKind === 'inspection' && !inspectionReady) || (sourceKind === 'manual' && !manualReady)" @click="submitEvaluation">生成待审批申请</button><p v-if="!can('quality.create')" class="quality-concession-note">当前账号没有创建让步申请权限，只能查看已有申请和审批结果。</p>
        </article>
      </div>
      <article class="quality-concession-card quality-concession-requests"><div class="quality-concession-card-heading"><div><h3>申请与审批</h3><p>待确认和待审批都不可用于结算；批准后会显示可结算重量。已加载 {{ requests.length }} 条。</p></div><button v-if="requestsHasMore" type="button" :disabled="pageLoading || busy" @click="loadMoreRequests">加载更多申请</button></div><div v-if="visibleRequests.length" class="quality-concession-request-list"><article v-for="row in visibleRequests" :key="row.id"><header><strong>{{ row.sampleId }}</strong><span :class="'status-' + row.status">{{ concessionStatusLabel(row.status) }}</span></header><p>{{ row.reason }}</p><dl><div><dt>原始重量</dt><dd>{{ row.originalWeight || '—' }}<span v-if="row.weightUnit"> {{ row.weightUnit }}</span></dd></div><div><dt>候选扣量</dt><dd>{{ row.proposedDeduction || '—' }}<span v-if="row.weightUnit"> {{ row.weightUnit }}</span></dd></div><div><dt>候选结算重量</dt><dd>{{ row.proposedSettlementWeight || '—' }}<span v-if="row.weightUnit"> {{ row.weightUnit }}</span></dd></div><div><dt>结算重量</dt><dd>{{ row.settlementWeight || '—' }}<span v-if="row.weightUnit"> {{ row.weightUnit }}</span></dd></div></dl><p v-if="row.decisionNote">审批意见：{{ row.decisionNote }}</p><p v-if="row.decidedAt">处理时间：{{ row.decidedAt }}</p><textarea v-if="row.status === 'pending' && can('quality.review')" v-model="decisionNotes[row.id]" rows="2" maxlength="3000" placeholder="填写审批依据" /><div v-if="row.status === 'pending' && can('quality.review')" class="wf-actions"><button type="button" :disabled="busy || !decisionNotes[row.id]?.trim()" @click="makeDecision(row, 'rejected')">驳回</button><button type="button" class="wf-primary" :disabled="busy || !decisionNotes[row.id]?.trim()" @click="makeDecision(row, 'approved')">批准结算重量</button></div></article></div><p v-else class="quality-concession-empty">暂无让步申请。</p></article>
    </template>
    <el-dialog v-model="ruleFormOpen" :title="ruleForm.id ? '编辑让步规则' : '新增让步规则'" width="min(720px, 95vw)" destroy-on-close><form class="quality-concession-rule-form" @submit.prevent="submitRule"><div class="quality-concession-fields"><label>规则编码<input v-model="ruleForm.code" maxlength="80" required /></label><label>规则名称<input v-model="ruleForm.name" maxlength="160" required /></label><label>规则状态<select v-model="ruleForm.status"><option value="active">启用</option><option value="inactive">停用</option></select></label><label>适用产品（可多选；不选表示规则配置的全产品范围）<select v-model="selectedProducts" multiple><option v-for="product in productOptions" :key="product.code" :value="product.code">{{ product.name }} · {{ product.code }}</option></select></label><label>指标（每条规则一个）<select v-model="selectedMetricId" required @change="syncMetricUnit"><option value="">请选择质检指标</option><option v-for="metric in metrics" :key="metric.id" :value="metric.id">{{ metric.name }} · {{ metric.unit || '无单位' }}</option></select></label><div class="quality-concession-pagination"><span>已加载 {{ metrics.length }} 项指标</span><button v-if="metricsHasMore" type="button" :disabled="metricsLoading" @click="loadMetrics()">加载更多指标</button></div><label>重量单位<input v-model="ruleForm.weightUnit" required /></label><label>指标单位<input v-model="ruleForm.metricUnit" required /></label><label>标准下限<input v-model="ruleForm.standardMin" inputmode="decimal" /></label><label>标准上限<input v-model="ruleForm.standardMax" inputmode="decimal" /></label><label>偏差下限<input v-model="ruleForm.minDeviation" inputmode="decimal" /></label><label>偏差上限<input v-model="ruleForm.maxDeviation" inputmode="decimal" /></label><label>扣量方式<select v-model="ruleForm.deductionMode"><option value="rate">按比例</option><option value="weight">按固定重量</option></select></label><label>扣量值<input v-model="ruleForm.deductionValue" inputmode="decimal" required /></label><label class="quality-concession-full">舍入方式<select :value="ruleForm.roundingMode" @change="changeRoundingMode(($event.target as HTMLSelectElement).value as ConcessionRule['roundingMode'])"><option value="exact">保留精确结果</option><option value="half_up">四舍五入</option><option value="half_even">银行家舍入</option><option value="down">向下舍入</option><option value="up">向上舍入</option></select></label><label v-if="ruleForm.roundingMode !== 'exact'">舍入小数位<input v-model.number="ruleForm.roundingScale" type="number" min="0" max="12" required /></label><label class="quality-concession-full">业务确认说明<textarea v-model="ruleForm.description" rows="3" maxlength="3000" /></label></div><div class="wf-actions"><button type="button" @click="ruleFormOpen = false">取消</button><button type="submit" class="wf-primary" :disabled="busy">保存规则</button></div></form></el-dialog>
  </section>
</template>

<style scoped>
.quality-concession-panel{margin-top:24px;padding:22px;border:1px solid #dce4ef;border-radius:12px;background:#f8fafc;color:#17263c}.quality-concession-heading,.quality-concession-card-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}.quality-concession-heading span{color:#155eef;font-size:11px;font-weight:700;letter-spacing:.12em}.quality-concession-heading h2{margin:6px 0;font-size:22px}.quality-concession-heading p,.quality-concession-card-heading p,.quality-concession-note{margin:0;color:#667085;font-size:12px;line-height:1.7}.quality-concession-grid{display:grid;grid-template-columns:minmax(0,.85fr) minmax(0,1.15fr);gap:16px;margin-top:18px}.quality-concession-card{padding:17px;border:1px solid #e4e7ec;border-radius:9px;background:#fff}.quality-concession-card h3{margin:0 0 4px;font-size:16px}.quality-concession-card-heading button{white-space:nowrap}.quality-concession-pagination-actions,.quality-concession-pagination{display:flex;align-items:center;justify-content:flex-end;gap:8px;flex-wrap:wrap}.quality-concession-pagination{justify-content:space-between;margin-top:5px;color:#667085;font-size:11px}.quality-concession-pagination button,.quality-concession-pagination-actions button{padding:6px 9px;border:1px solid #d0d5dd;border-radius:6px;background:#fff;color:#475467}.quality-concession-pagination button:disabled,.quality-concession-pagination-actions button:disabled{cursor:not-allowed;opacity:.55}.quality-concession-rules{padding:0;margin:14px 0 0;list-style:none}.quality-concession-rules li{display:flex;align-items:center;border-top:1px solid #eef2f6}.quality-concession-rules li.selected{background:#f2f6ff}.quality-concession-rules li>button:first-child{display:grid;gap:4px;flex:1;padding:11px 8px;text-align:left;border:0;background:transparent}.quality-concession-rules small{color:#667085;font-size:11px}.link-button{border:0;background:transparent;color:#155eef}.quality-concession-empty{margin:16px 0 0;color:#8a5b13;font-size:12px}.quality-concession-tabs{display:flex;gap:6px;margin:15px 0 12px;flex-wrap:wrap}.quality-concession-tabs button{border:1px solid #d0d5dd;border-radius:6px;background:#fff;color:#475467;padding:8px 11px}.quality-concession-tabs button.active{border-color:#155eef;background:#eff4ff;color:#155eef}.quality-concession-card label{display:grid;gap:5px;margin:10px 0;color:#344054;font-size:12px}.quality-concession-card input,.quality-concession-card select,.quality-concession-card textarea{box-sizing:border-box;width:100%;min-height:36px;border:1px solid #d0d5dd;border-radius:5px;padding:7px 9px;background:#fff;color:#17263c}.quality-concession-fields{display:grid;grid-template-columns:1fr 1fr;gap:0 12px}.quality-concession-fields label{min-width:0}.quality-concession-full{grid-column:1/-1}.quality-concession-card>.wf-primary{margin-top:12px}.quality-concession-requests{margin-top:16px}.quality-concession-request-list{display:grid;gap:12px;margin-top:14px}.quality-concession-request-list article{padding:13px;border:1px solid #e4e7ec;border-radius:7px;background:#fbfcfe}.quality-concession-request-list header{display:flex;justify-content:space-between;gap:12px}.quality-concession-request-list p{color:#667085;font-size:12px;line-height:1.6}.quality-concession-request-list dl{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:10px 0}.quality-concession-request-list dt{color:#667085;font-size:11px}.quality-concession-request-list dd{margin:4px 0 0;font-weight:650}.status-pending{color:#9a6700}.status-needs_confirmation{color:#9a6700}.status-approved{color:#067647}.status-rejected{color:#b42318}.quality-concession-rule-form{padding:2px 2px 0}.quality-concession-rule-form .wf-actions{justify-content:flex-end;margin-top:16px}@media(max-width:800px){.quality-concession-grid{grid-template-columns:1fr}}@media(max-width:600px){.quality-concession-panel{padding:15px}.quality-concession-heading{align-items:stretch;flex-direction:column}.quality-concession-fields,.quality-concession-request-list dl{grid-template-columns:1fr}.quality-concession-full{grid-column:auto}.quality-concession-tabs button{flex:1}.quality-concession-card button{min-height:40px}}
</style>
