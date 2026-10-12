/**
 * 设备预警流程向导（Alert Workflow Wizard）的组合式函数。
 * 职责：按"策略修订快照 → 设备 → 数据源 → 测点 → 计算字段 → 通知/回写/调度"逐步校验，
 *   并用 incompleteReason 汇总第一个阻断原因，供向导底部提示与提交按钮禁用。
 * 关键约束：
 *   1) 约束字段来自策略修订快照（expression 里的 field + recordKeyField），计算字段名可抵扣必需字段，
 *      最终 missingFields 非空时不允许提交（避免生成必然失败的工作流）；
 *   2) 调度开关要求 can('system.operations.manage') 权限；
 *   3) 表单整体替换（form.value = {...}）以触发依赖计算属性重算。
 * 加载/取消：持有单个 AbortController，load 前 abort 上一次；卸载后不再写状态。
 * 错误暴露：error（页面提示条）；submit 失败返回 null 而不是抛出，调用方据此决定是否关闭向导弹窗。
 */
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { alerts, type AlertRecord } from '../api/alerts'
import { alertWorkflows, newAlertCalculation, type AlertWorkflowCreated, type AlertWorkflowDraft } from '../api/alertWorkflows'
import type { HttpSource } from '../api/collection'
import { can, get } from '../api/request'
import { createClientId } from '../utils/randomId'

type SourceChoice = Partial<Omit<HttpSource, 'id' | 'name' | 'type' | 'version' | 'enabled'>>
  & { id: string; name: string; type: string; version: number; enabled: boolean; [key: string]: any }

/** 草稿初值：requestKey 为一次性幂等键（重新打开向导会生成新键，重复提交同一次向导才复用）；默认关闭调度与回写。 */
function emptyDraft(rule: AlertRecord): AlertWorkflowDraft {
  return {
    requestKey: createClientId().replaceAll('-', ''),
    name: (rule.name || '设备') + ' · 自动预警',
    description: '',
    ruleId: rule.id || '',
    ruleRevision: Number(rule.version || 1),
    source: { datasourceId: '', datasourceVersion: 1, query: '', parametersJson: '{}', maxRows: 1000 },
    confirmHttpWrites: false,
    pointIds: [], calculations: [],
    notificationEnabled: false,
    notification: { audience: 'executor', outcomes: ['issues', 'no_data'], minimumSeverity: 'warning' },
    writebackEnabled: false,
    writeback: { targetId: '', targetVersion: 1, mode: 'insert', recordPath: '$.issues', fields: [], keys: [] },
    scheduleEnabled: false,
    schedule: { scheduleKind: 'interval', scheduleExpression: '3600', timezone: 'Asia/Shanghai', status: 'paused' },
  }
}

/** 递归收集策略表达式引用到的字段名（服务只有单字段表达式也算一个引用）。 */
function expressionFields(expression: unknown): Set<string> {
  if (!expression || typeof expression !== 'object' || Array.isArray(expression)) return new Set()
  const value = expression as Record<string, any>
  if (typeof value.field === 'string' && Object.keys(value).length === 1) return new Set([value.field])
  const output = new Set<string>()
  for (const item of value.args || []) for (const field of expressionFields(item)) output.add(field)
  return output
}

/** 提取公式中出现的标识符（支持中文等 Unicode 字母），用于判断"计算字段依赖了哪些原始字段"。 */
function formulaFields(formula: string): Set<string> {
  return new Set(formula.match(/[\p{L}_][\p{L}\p{N}_]*/gu) || [])
}

function validParameters(text: string): boolean {
  try { const value = JSON.parse(text || '{}'); return !!value && typeof value === 'object' && !Array.isArray(value) }
  catch { return false }
}

/**
 * 预警流程向导状态。
 * 参数：rule 为入口处选中的预警策略记录（用其 id/version 定位修订）。
 * 返回：form（草稿）、revisions/device/points/source（上下文数据）、loading/busy/error，
 *   以及 snapshot/policyFields/rawRequiredFields/missingFields/sourceReady/sourceSummary/nodeCount/incompleteReason/canSubmit 等派生校验态，
 *   加 load/chooseRevision/togglePoint/selectRequiredPoints/addCalculation/removeCalculation/updateWriteback/submit。
 * 副作用：load 会并发拉取策略修订、设备、测点、HTTP 与 SQL 数据源；submit 会创建并发布工作流。
 * 失败表现：加载与提交错误写入 error；submit 失败返回 null（弹窗保持打开，user 可修正后重试）。
 */
export function useAlertWorkflowWizard(rule: AlertRecord) {
  const form = ref<AlertWorkflowDraft>(emptyDraft(rule))
  const revisions = shallowRef<AlertRecord[]>([]), device = shallowRef<AlertRecord | null>(null)
  const points = shallowRef<AlertRecord[]>([]), source = shallowRef<SourceChoice | null>(null)
  const loading = shallowRef(false), busy = shallowRef(false), error = shallowRef('')
  let request: AbortController | null = null, stopped = false

  const httpWritesRequired = computed(() => source.value?.type === 'http' && source.value.method !== 'GET')
  // 许可只针对用户看到的固定来源、方法和版本；绑定变化后立即撤销，不能携带旧勾选。
  watch([() => source.value?.id, () => source.value?.version, () => source.value?.method,
    () => form.value.source.datasourceId, () => form.value.source.datasourceVersion], () => {
    form.value.confirmHttpWrites = false
  }, { flush: 'sync' })

  const snapshot = computed<AlertRecord | null>(() => revisions.value.find(item => Number(item.revision) === form.value.ruleRevision)?.snapshot || null)
  const policyFields = computed(() => {
    const fields = new Set<string>()
    for (const item of snapshot.value?.rules || []) for (const field of expressionFields(item.expression)) fields.add(field)
    if (snapshot.value?.recordKeyField) fields.add(snapshot.value.recordKeyField)
    return fields
  })
  const calculationNames = computed(() => new Set(form.value.calculations.map(item => item.name.trim()).filter(Boolean)))
  const rawRequiredFields = computed(() => {
    const fields = new Set([...policyFields.value].filter(name => !calculationNames.value.has(name)))
    const generated = new Set<string>()
    for (const calculation of form.value.calculations) {
      for (const field of formulaFields(calculation.formula)) if (!generated.has(field)) fields.add(field)
      if (calculation.name.trim()) generated.add(calculation.name.trim())
    }
    return fields
  })
  const selectedPoints = computed(() => points.value.filter(item => !!item.id && form.value.pointIds.includes(item.id)))
  const selectedFields = computed(() => new Set(selectedPoints.value.map(item => item.fieldName)))
  const missingFields = computed(() => [...rawRequiredFields.value].filter(field => !selectedFields.value.has(field)))
  const sourceReady = computed(() => {
    if (!source.value) return '设备来源不可访问或尚未配置'
    if (!source.value.enabled) return '数据源已停用'
    if (source.value.type === 'http' && !source.value.allowAutomatic) return 'HTTP 来源尚未允许工作流自动采集'
    if (httpWritesRequired.value && !form.value.confirmHttpWrites) return '请明确确认此 HTTP 来源的非 GET 请求及其外部副作用'
    if (source.value.type !== 'http' && !form.value.source.query.trim()) return '请填写数据库只读查询'
    return ''
  })
  const nodeCount = computed(() => 5 + Number(form.value.calculations.length > 0)
    + Number(form.value.notificationEnabled) + Number(form.value.writebackEnabled))
  const incompleteReason = computed(() => {
    if (loading.value) return '正在加载策略与设备配置'
    if (!form.value.name.trim()) return '请填写流程名称'
    if (!snapshot.value || !device.value) return '策略修订或设备不可用'
    if (sourceReady.value) return sourceReady.value
    if (!form.value.pointIds.length) return '请手动选择参与预警的测点'
    if (missingFields.value.length) return '缺少策略或计算字段：' + missingFields.value.join('、')
    if (new Set(selectedPoints.value.map(item => item.fieldName)).size !== selectedPoints.value.length) return '所选测点存在重复采集字段'
    if (form.value.calculations.some(item => !item.name.trim() || !item.formula.trim())) return '请完整填写计算字段和公式'
    if (!validParameters(form.value.source.parametersJson)) return 'SQL 参数必须是 JSON 对象'
    if (form.value.notificationEnabled && !form.value.notification.outcomes.length) return '至少选择一种通知结果'
    if (form.value.writebackEnabled && (!form.value.writeback.targetId || !form.value.writeback.fields?.length)) return '请完整配置授权写入目标与字段映射'
    if (form.value.scheduleEnabled && !form.value.schedule.scheduleExpression.trim()) return '请填写调度间隔或 cron'
    if (form.value.scheduleEnabled && !can('system.operations.manage')) return '当前账号没有调度任务管理权限'
    return ''
  })
  const canSubmit = computed(() => !busy.value && !incompleteReason.value)
  const sourceSummary = computed(() => {
    if (!source.value) return ''
    if (source.value.type === 'http') {
      const count = Array.isArray(source.value.bodies) ? source.value.bodies.length : 0
      return `${source.value.method} · ${count > 1 ? count + ' 条批量请求体' : '单条请求'} · ${source.value.fields?.length || '全部'} 个 JSONPath 字段`
    }
    return `${source.value.type.toUpperCase()} · v${source.value.version} · 最多 ${form.value.source.maxRows} 行`
  })

  // 切换策略修订：必须重新解析设备与其数据源，并清空已选测点/计算（它们依赖旧修订的字段）。
  async function applyRevision(revision: number) {
    form.value.confirmHttpWrites = false
    const selected = revisions.value.find(item => Number(item.revision) === revision)?.snapshot
    if (!selected) throw new Error('所选策略修订不存在或不可访问')
    const selectedDevice = (await alerts.get('devices', selected.deviceId)).data
    const [pointRows, http, sql] = await Promise.all([
      alerts.list('points', { limit: 200, deviceId: selected.deviceId }),
      get<{ data: SourceChoice[] }>('/api/v1/collection/sources', { limit: 500 }),
      get<{ data: SourceChoice[] }>('/api/v1/analytics/datasources'),
    ])
    const selectedSource = [...http.data, ...sql.data].find(item => item.id === selectedDevice.datasourceId) || null
    device.value = selectedDevice; points.value = pointRows.data; source.value = selectedSource
    form.value = {
      ...form.value, ruleRevision: revision, pointIds: [], calculations: [], confirmHttpWrites: false,
      source: { datasourceId: selectedSource?.id || '', datasourceVersion: Number(selectedSource?.version || 1), query: '', parametersJson: '{}', maxRows: Number(selectedSource?.maxRows || 1000) },
    }
  }

  // 加载：策略修订列表为空或没有可访问修订时直接报错，避免向导停留在半可用状态。
  async function load() {
    request?.abort(); const active = new AbortController(); request = active
    loading.value = true; error.value = ''
    try {
      revisions.value = (await alerts.revisions(rule.id!)).data
      const revision = revisions.value.some(item => Number(item.revision) === Number(rule.version))
        ? Number(rule.version) : Number(revisions.value[0]?.revision || 0)
      if (!revision) throw new Error('没有可访问的策略修订')
      await applyRevision(revision)
    } catch (cause) { if (!active.signal.aborted && !stopped) error.value = (cause as Error).message }
    finally { if (!stopped && active === request) loading.value = false }
  }
  async function chooseRevision(revision: number) {
    if (busy.value || loading.value) return
    loading.value = true; error.value = ''
    try { await applyRevision(revision) }
    catch (cause) { if (!stopped) error.value = (cause as Error).message }
    finally { if (!stopped) loading.value = false }
  }
  function togglePoint(id: string, selected: boolean) {
    form.value = { ...form.value, pointIds: selected ? [...form.value.pointIds, id] : form.value.pointIds.filter(item => item !== id) }
  }
  function selectRequiredPoints() {
    const requiredIds = points.value.filter(item => rawRequiredFields.value.has(item.fieldName)).map(item => item.id!)
    form.value = { ...form.value, pointIds: [...new Set([...form.value.pointIds, ...requiredIds])] }
  }
  function addCalculation() { form.value = { ...form.value, calculations: [...form.value.calculations, newAlertCalculation()] } }
  function removeCalculation(key: string) { form.value = { ...form.value, calculations: form.value.calculations.filter(item => item.key !== key) } }
  function updateWriteback(config: Record<string, any>) { form.value = { ...form.value, writeback: config } }
  // 提交：以 canSubmit 作为唯一闸门（忙碌中或缺原因一律不提交），失败返回 null 由界面决定是否保留弹窗。
  async function submit(): Promise<AlertWorkflowCreated | null> {
    if (!canSubmit.value) return null
    busy.value = true; error.value = ''
    try { return (await alertWorkflows.create(form.value)).data }
    catch (cause) { if (!stopped) error.value = (cause as Error).message; return null }
    finally { if (!stopped) busy.value = false }
  }

  onBeforeUnmount(() => { stopped = true; request?.abort() })
  return { form, revisions, device, points, source, loading, busy, error, snapshot, policyFields,
    rawRequiredFields, selectedFields, missingFields, sourceReady, sourceSummary, httpWritesRequired, nodeCount,
    incompleteReason, canSubmit, load, chooseRevision, togglePoint, selectRequiredPoints,
    addCalculation, removeCalculation, updateWriteback, submit }
}
