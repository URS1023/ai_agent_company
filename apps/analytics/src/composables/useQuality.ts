/**
 * 质检工作台的组合式函数（指标 / 模板 / 质检记录三个页签共用）。
 * 职责：按 kind 加载列表与关键字筛选、打开记录报告、编辑保存、归档、复核、导出与模板历史版本查看。
 * 加载/取消策略：列表请求持有单个 AbortController，切换 kind 或重新 load 时先 abort 上一次，过期响应不回写。
 * 忙碌锁与错误：写操作统一走 action()（busy 期间直接返回，避免重复提交），异常写入 error 由页面提示条展示。
 * 权限与约束：只读与写入均由后端权限码控制；归档是软删除语义（保留证据与复核历史）。
 */
import { onBeforeUnmount, ref, shallowRef, watch, type Ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import request from '../api/request'
import { emptyQualityRecord, quality, qualityBase, QUALITY_PAGE_SIZE, readQualityArrayPage, type ConcessionRequest, type ConcessionRule, type ConcessionSourceOption, type QualityKind, type QualityRecord } from '../api/quality'

/**
 * 质检列表状态机。
 * 参数：kind 为当前页签（metrics/templates/inspections）；inspections 只读报告，不做配置编辑。
 * 返回：列表与分页、表单/报告/历史版本弹窗状态，以及 load/open/save/submitInspection/remove/review/exportReport/history。
 * 副作用：save/remove/review/submitInspection 会写库并刷新列表；exportReport 触发浏览器下载；支持 ?inspection= 深链自动打开报告。
 * 失败表现：错误写入 error；删除/归档有二次确认，取消确认不会产生错误提示。
 */
export function useQuality(kind: Ref<QualityKind>) {
  const route = useRoute(), rows = shallowRef<QualityRecord[]>([]), keyword = shallowRef(''), offset = shallowRef(0)
  const loading = shallowRef(false), busy = shallowRef(false), error = shallowRef(''), formOpen = shallowRef(false), form = ref<QualityRecord | null>(null)
  const report = shallowRef<QualityRecord | null>(null), reportOpen = shallowRef(false), historyRows = shallowRef<QualityRecord[]>([]), historyOpen = shallowRef(false), historyOffset = shallowRef(0), historyId = shallowRef('')
  let controller: AbortController | null = null, stopped = false
  async function action(callback: () => Promise<void>) { if (busy.value) return; busy.value = true; error.value = ''; try { await callback() } catch (cause) { if (cause instanceof Error && !stopped) error.value = cause.message } finally { busy.value = false } }
  async function load() {
    controller?.abort(); const active = new AbortController(); controller = active; loading.value = true; error.value = ''; rows.value = []
    try { const response = await quality.list(kind.value, { keyword: keyword.value, offset: offset.value }, active.signal); if (!active.signal.aborted && !stopped) rows.value = response.data }
    catch (cause) { if (!active.signal.aborted && !stopped) error.value = (cause as Error).message }
    finally { if (!active.signal.aborted && !stopped) loading.value = false }
  }
  async function openReport(id: string) { await action(async () => { report.value = (await quality.get('inspections', id)).data; reportOpen.value = true }) }
  async function open(row?: QualityRecord) { if (kind.value === 'inspections' && row?.id) { await openReport(row.id); return }; await action(async () => { form.value = kind.value === 'inspections' ? {} : row?.id ? (await quality.get(kind.value, row.id)).data : emptyQualityRecord(kind.value); formOpen.value = true }) }
  async function save() { await action(async () => { if (!form.value || kind.value === 'inspections') return; await quality.save(kind.value, form.value); formOpen.value = false; await load() }) }
  async function submitInspection(payload: object) { await action(async () => { report.value = (await quality.createInspection(payload)).data; formOpen.value = false; reportOpen.value = true; await load() }) }
  async function remove(row: QualityRecord) { await action(async () => { await ElMessageBox.confirm(kind.value === 'inspections' ? '归档此记录？原始证据与复核历史将保留。' : '移除此配置？旧模板标准和历史报告会保留，新检测将停止引用。', '确认操作', { confirmButtonText: '确认', cancelButtonText: '取消' }); await quality.remove(kind.value, row); await load() }) }
  async function review(payload: object) { await action(async () => { if (!report.value) return; report.value = (await quality.review(report.value, payload)).data; await load() }) }
  async function exportReport() { await action(async () => { if (!report.value) return; const response = await request.get(qualityBase + '/inspections/' + report.value.id + '/export', { responseType: 'blob' }); const url = URL.createObjectURL(response.data), link = document.createElement('a'); link.href = url; link.download = 'quality-' + report.value.id + '.html'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000) }) }
  async function history(row: QualityRecord, next = 0) { await action(async () => { historyId.value = row.id!; historyOffset.value = next; historyRows.value = (await quality.revisions(row.id!, next)).data; historyOpen.value = true }) }
  watch(kind, () => { offset.value = 0; keyword.value = ''; formOpen.value = false; reportOpen.value = false; void load() }, { immediate: true })
  watch(() => route.query.inspection, id => { if (typeof id === 'string') void openReport(id) }, { immediate: true })
  onBeforeUnmount(() => { stopped = true; controller?.abort() })
  return { rows, keyword, offset, loading, busy, error, formOpen, form, report, reportOpen, historyRows, historyOpen, historyOffset, historyId, load, open, save, submitInspection, remove, review, exportReport, history }
}

/**
 * 让步接收（规则 / 申请 / 来源）的组合式函数。
 * 状态：rules、requests、workflowSources/inspectionSources（合并为 sources）、loading/pageLoading/busy/error 及各分区 hasMore。
 * 加载/取消策略：用 generation 令牌表示"请求代际"，beginRequest 保证同一时刻只有一个请求在飞；
 *   过期请求（token !== generation）的结果与错误一律丢弃；dispose 后不再回写状态。
 * 分页策略：数组型接口不返回分页元数据，故多取一条（QUALITY_PAGE_SIZE + 1）来推断 hasMore，
 *   满页即保留"加载更多"按钮，属于保守回退。
 * 错误暴露：run()/各 loadMore 把异常写入 error 并返回 false（evaluate 返回 null），不向调用方抛出。
 */
/** Quality main-entry state for rules, source selection and approval requests. */
export function useQualityConcessions() {
  const rules = shallowRef<ConcessionRule[]>([]), requests = shallowRef<ConcessionRequest[]>([])
  const workflowSources = shallowRef<ConcessionSourceOption[]>([]), inspectionSources = shallowRef<ConcessionSourceOption[]>([])
  const sources = shallowRef<ConcessionSourceOption[]>([]), loading = shallowRef(false), pageLoading = shallowRef(false), busy = shallowRef(false), error = shallowRef('')
  const rulesOffset = shallowRef(0), requestsOffset = shallowRef(0), workflowSourcesOffset = shallowRef(0), inspectionSourcesOffset = shallowRef(0)
  const rulesHasMore = shallowRef(false), requestsHasMore = shallowRef(false), workflowSourcesHasMore = shallowRef(false), inspectionSourcesHasMore = shallowRef(false)
  let stopped = false, generation = 0, requestInFlight = false
  const arrayPageLimit = QUALITY_PAGE_SIZE + 1

  function beginRequest() {
    if (stopped || requestInFlight) return 0
    requestInFlight = true
    generation += 1
    return generation
  }
  function isCurrent(token: number) { return token === generation && !stopped }

  // 数组型接口没有分页元数据：请求时多取一行，用"是否满页"推断还有更多；offset 只累加实际收到的条数。
  function applyArrayPage<T>(target: { value: T[] }, offset: { value: number }, hasMore: { value: boolean }, page: T[], append: boolean) {
    const result = readQualityArrayPage(page)
    target.value = append ? [...target.value, ...result.items] : result.items
    offset.value += result.items.length
    // Array endpoints do not currently return page metadata. Request one
    // extra row and keep the button for a full page as a conservative fallback
    // when a deployment caps the requested limit.
    hasMore.value = result.hasMore
  }

  function applySourcePage(target: { value: ConcessionSourceOption[] }, offset: { value: number }, hasMore: { value: boolean }, page: { items: ConcessionSourceOption[]; offset: number; hasMore: boolean }, append: boolean) {
    target.value = append ? [...target.value, ...page.items] : page.items
    offset.value = page.offset + page.items.length
    hasMore.value = page.hasMore
  }

  function mergeSources() {
    sources.value = [...workflowSources.value, ...inspectionSources.value]
  }

  async function run(callback: () => Promise<void>) {
    if (busy.value) return false
    busy.value = true; error.value = ''
    try { await callback(); return true } catch (cause) { if (!stopped) error.value = (cause as Error).message; return false } finally { busy.value = false }
  }
  async function load() {
    const token = beginRequest()
    if (!token) return
    loading.value = true; error.value = ''
    try {
      const [ruleResult, requestResult, workflowResult, inspectionResult] = await Promise.all([
        quality.concessionRules.list({ limit: arrayPageLimit, offset: 0 }), quality.concessions.list({ limit: arrayPageLimit, offset: 0 }),
        quality.concessions.sources({ kind: 'workflow', limit: QUALITY_PAGE_SIZE, offset: 0 }), quality.concessions.sources({ kind: 'inspection', limit: QUALITY_PAGE_SIZE, offset: 0 }),
      ])
      if (isCurrent(token)) {
        rulesOffset.value = requestsOffset.value = workflowSourcesOffset.value = inspectionSourcesOffset.value = 0
        applyArrayPage(rules, rulesOffset, rulesHasMore, ruleResult.data, false)
        applyArrayPage(requests, requestsOffset, requestsHasMore, requestResult.data, false)
        applySourcePage(workflowSources, workflowSourcesOffset, workflowSourcesHasMore, workflowResult.data, false)
        applySourcePage(inspectionSources, inspectionSourcesOffset, inspectionSourcesHasMore, inspectionResult.data, false)
        mergeSources()
      }
    } catch (cause) { if (isCurrent(token)) error.value = (cause as Error).message } finally { if (isCurrent(token)) loading.value = false; if (token === generation) requestInFlight = false }
  }

  async function loadMoreRules() {
    if (loading.value || pageLoading.value || !rulesHasMore.value) return
    const token = beginRequest()
    if (!token) return
    pageLoading.value = true
    try {
      const result = await quality.concessionRules.list({ limit: arrayPageLimit, offset: rulesOffset.value })
      if (isCurrent(token)) applyArrayPage(rules, rulesOffset, rulesHasMore, result.data, true)
    } catch (cause) { if (isCurrent(token)) error.value = (cause as Error).message } finally { if (isCurrent(token)) pageLoading.value = false; if (token === generation) requestInFlight = false }
  }
  async function loadMoreRequests() {
    if (loading.value || pageLoading.value || !requestsHasMore.value) return
    const token = beginRequest()
    if (!token) return
    pageLoading.value = true
    try {
      const result = await quality.concessions.list({ limit: arrayPageLimit, offset: requestsOffset.value })
      if (isCurrent(token)) applyArrayPage(requests, requestsOffset, requestsHasMore, result.data, true)
    } catch (cause) { if (isCurrent(token)) error.value = (cause as Error).message } finally { if (isCurrent(token)) pageLoading.value = false; if (token === generation) requestInFlight = false }
  }
  async function loadMoreWorkflowSources() {
    if (loading.value || pageLoading.value || !workflowSourcesHasMore.value) return
    const token = beginRequest()
    if (!token) return
    pageLoading.value = true
    try {
      const result = await quality.concessions.sources({ kind: 'workflow', limit: QUALITY_PAGE_SIZE, offset: workflowSourcesOffset.value })
      if (isCurrent(token)) { applySourcePage(workflowSources, workflowSourcesOffset, workflowSourcesHasMore, result.data, true); mergeSources() }
    } catch (cause) { if (isCurrent(token)) error.value = (cause as Error).message } finally { if (isCurrent(token)) pageLoading.value = false; if (token === generation) requestInFlight = false }
  }
  async function loadMoreInspectionSources() {
    if (loading.value || pageLoading.value || !inspectionSourcesHasMore.value) return
    const token = beginRequest()
    if (!token) return
    pageLoading.value = true
    try {
      const result = await quality.concessions.sources({ kind: 'inspection', limit: QUALITY_PAGE_SIZE, offset: inspectionSourcesOffset.value })
      if (isCurrent(token)) { applySourcePage(inspectionSources, inspectionSourcesOffset, inspectionSourcesHasMore, result.data, true); mergeSources() }
    } catch (cause) { if (isCurrent(token)) error.value = (cause as Error).message } finally { if (isCurrent(token)) pageLoading.value = false; if (token === generation) requestInFlight = false }
  }

  async function saveRule(row: Partial<ConcessionRule>) { await run(async () => { await quality.concessionRules.save(row); await load() }) }
  async function evaluate(payload: object) { let result: ConcessionRequest | null = null; await run(async () => { result = (await quality.concessions.evaluate(payload)).data; await load() }); return result }
  async function decide(row: ConcessionRequest, decision: 'approved' | 'rejected', note: string) { return run(async () => { await quality.concessions.decide(row, decision, note); await load() }) }
  function dispose() { stopped = true; generation += 1; requestInFlight = false }
  onBeforeUnmount(dispose)
  return {
    rules, requests, sources, workflowSources, inspectionSources, loading, pageLoading, busy, error,
    rulesHasMore, requestsHasMore, workflowSourcesHasMore, inspectionSourcesHasMore,
    load, loadMoreRules, loadMoreRequests, loadMoreWorkflowSources, loadMoreInspectionSources,
    saveRule, evaluate, decide, dispose,
  }
}
