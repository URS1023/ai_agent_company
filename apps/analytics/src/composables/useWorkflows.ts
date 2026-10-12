/**
 * 工作流编辑器/工作台的核心组合式函数（列表、图编辑、保存、运行、历史与监控）。
 * 职责：加载工作流与节点目录、维护画布图（含撤销/重做）、自动保存与脏标记、发布/停用、试运行与运行结果、
 *   AI 生成入口、运行历史与版本回滚，以及运行所需的资源与字段推导。
 * 加载/取消：列表与详情共用一个 AbortController（每次 load 前 abort）；documentSerial 隔离跨流程异步动作，editSerial 防止旧保存回执覆盖新编辑。
 * 保存策略：自动保存用定时器 + 单飞 savePromise 串行化；失败后暂停自动写入，保留草稿供手动重试。
 * 运行策略：controlPending 记录运行/恢复/取消的请求体与幂等键，confirmWrites 必须由用户显式勾选。
 * 权限：editable 由 can('workflows.edit' | 'workflows.create') 与 busy 共同决定，无权限时编辑器只读。
 * 错误暴露：error（页面提示）、saveError（保存失败单独提示）、generationNotice（AI 生成提示），另配合 ElMessage/ElMessageBox 交互。
 */
import { computed, onBeforeUnmount, ref, shallowRef, watch, type Ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { can, get } from '../api/request'
import { copy, emptyWorkflow, workflows, type Workflow, type FlowRun, type NodeKind, type FlowNode, type FlowGraph } from '../api/workflows'
import { parseWorkflowJson } from '../utils/workflowJson.js'
import { createClientId } from '../utils/randomId'
import { ensureReadableWorkflowLayout } from '../utils/workflowLayout'
import { openTopLevel } from '../utils/topLevelNavigation'

/**
 * 工作流编辑器状态机。
 * 参数：id 为当前工作流 id（可为空表示新建），变化时重新加载详情并重置画布与运行态。
 * 返回：列表与分页、flow/graph（含撤销重做）、catalog/sources/resources、运行结果与运行控制、
 *   AI 生成与历史版本状态，以及各操作函数（后续定义）。
 * 副作用：读写工作流（保存/发布/运行/回滚）、操作 URL（openTopLevel）、ElMessage/ElMessageBox 提示。
 */
export function useWorkflows(id: Ref<string>) {
  const router = useRouter()
  const rows = ref<Workflow[]>([]), flow = ref<Workflow | null>(null), catalog = ref<NodeKind[]>([]), sources = ref<any[]>([])
  const resources = ref<{ knowledgeBases: any[]; bots: any[]; tools: any[]; workflows: Workflow[]; models: any[] }>({ knowledgeBases: [], bots: [], tools: [], workflows: [], models: [] })
  const loading = shallowRef(false), busy = shallowRef(false), saving = shallowRef(false), error = shallowRef(''), generationNotice = shallowRef(''), dirty = shallowRef(false)
  const lastSavedAt = shallowRef<number | null>(null), saveError = shallowRef('')
  const keyword = shallowRef(''), offset = shallowRef(0), selected = shallowRef(''), result = shallowRef<FlowRun | null>(null)
  const graphPast = ref<FlowGraph[]>([]), graphFuture = ref<FlowGraph[]>([])
  const canUndo = computed(() => graphPast.value.length > 0), canRedo = computed(() => graphFuture.value.length > 0)
  const confirmWrites = shallowRef(false), resultRefreshing = shallowRef(false)
  const resultOpen = shallowRef(false), runOpen = shallowRef(false), runInputs = shallowRef('{}'), runMode = shallowRef('draft')
  const aiOpen = shallowRef(false)
  const historyOpen = shallowRef(false), history = ref<FlowRun[]>([]), revisions = ref<any[]>([]), historyOffset = shallowRef(0)
  let controller: AbortController | null = null, stopped = false
  let autoSaveTimer: ReturnType<typeof setTimeout> | null = null
  let savePromise: Promise<boolean> | null = null
  let autoSaveBlocked = false
  let editSerial = 0
  let documentSerial = 0
  let pending: { body: string; key: string } | null = null
  let controlPending: { action: string; execution: string; body: string; key: string } | null = null
  const editable = computed(() => !!flow.value && can(flow.value.id ? 'workflows.edit' : 'workflows.create') && !busy.value)
  const selectedNode = computed(() => flow.value?.graph.nodes.find(node => node.id === selected.value))
  const fields = computed(() => [...new Set<string>([
    ...sources.value.filter(source => flow.value?.graph.nodes.some(node => node.config.datasourceId === source.id)).flatMap(source => source.fields?.map((field: any) => field.name) || []),
    ...(flow.value?.graph.nodes.flatMap(node => node.kind === 'arithmetic' ? node.config.fields?.map((field: any) => field.name) || [] : []) || []),
    ...(flow.value?.graph.nodes.flatMap(node => node.kind === 'read-values' ? node.config.fields || [] : node.kind === 'calculate-values' ? (node.config.calculations || []).map((c: any) => c.name) : []) || []),
    ...Object.values(result.value?.result?.nodes || {}).flatMap(node => node.output?.columns?.map((column: any) => column.columnName) || []),
  ])].filter(Boolean).slice(0, 200))
  async function load() {
    controller?.abort(); controller = new AbortController(); const active = controller
    loading.value = true; error.value = ''; generationNotice.value = ''; result.value = null
    try {
      if (!id.value) {
        const data = (await workflows.list(keyword.value, offset.value, active.signal)).data
        if (!stopped && active === controller) { rows.value = data; flow.value = null }
      } else {
        const [record, kinds] = await Promise.all([id.value === 'new' ? Promise.resolve(emptyWorkflow()) : workflows.get(id.value, active.signal).then(value => value.data), workflows.catalog()])
        if (stopped || active !== controller) return
        const readableGraph = ensureReadableWorkflowLayout(record.graph)
        const layoutAdjusted = readableGraph !== record.graph
        flow.value = { ...record, graph: readableGraph }; catalog.value = kinds.data
        selected.value = record.validation?.errors.find(issue => issue.nodeId && !['start', 'end'].includes(record.graph.nodes.find(node => node.id === issue.nodeId)?.kind || ''))?.nodeId || ''
        dirty.value = false
        saveError.value = ''
        autoSaveBlocked = false
        lastSavedAt.value = record.updateTime ? Date.parse(record.updateTime) || Date.now() : (record.id ? Date.now() : null)
        graphPast.value = []; graphFuture.value = []
        if (layoutAdjusted && record.id && can('workflows.edit')) {
          markDirty()
          ElMessage.info('已自动展开重叠节点，连线现在保持清晰可见')
        }
        sources.value = []; resources.value = { knowledgeBases: [], bots: [], tools: [], workflows: [], models: [] }
        const isCurrent = () => !stopped && active === controller
        const tasks: Promise<void>[] = []
        if (can('datasources.view')) tasks.push(Promise.all([get('/api/v1/collection/sources', { limit: 500 }, { signal: active.signal }), get('/api/v1/analytics/datasources', {}, { signal: active.signal })]).then(([http, sql]) => { if (isCurrent()) sources.value = [...http.data, ...sql.data] }))
        tasks.push(get<any>('/api/v1/workflows', { limit: 200, offset: 0 }, { signal: active.signal }).then(value => { if (isCurrent()) resources.value.workflows = value.data || [] }))
        if (can('bots.view')) tasks.push(Promise.all([get<any>('/api/v1/bots/binding-options', {}, { signal: active.signal }), get<any>('/api/v1/bots', {}, { signal: active.signal })]).then(([options, bots]) => { if (isCurrent()) { resources.value.tools = options.data?.tools || []; resources.value.bots = bots.data || [] } }))
        if (can('knowledge.view')) tasks.push(get<any>('/api/knowledge', {}, { signal: active.signal }).then(value => { if (isCurrent()) resources.value.knowledgeBases = value.items || [] }))
        if (can('models.view')) tasks.push(get<any>('/api/models/profiles', {}, { signal: active.signal }).then(value => { if (isCurrent()) resources.value.models = value.items || [] }))
        const outcomes = await Promise.allSettled(tasks)
        const failures = outcomes.filter(item => item.status === 'rejected')
        if (isCurrent() && failures.length) error.value = '流程已打开，但部分可选资源暂时无法加载。刷新页面可重试。'
      }
    } catch (cause) { if (!active.signal.aborted && !stopped) error.value = (cause as Error).message }
    finally { if (!stopped && active === controller) loading.value = false }
  }
  async function action(callback: (isCurrent: () => boolean) => Promise<void>) {
    if (busy.value || stopped) return
    const actionDocument = documentSerial
    // 每次异步动作都绑定当前文档，确认框和网络回执返回后必须重新核对归属。
    const isCurrent = () => !stopped && actionDocument === documentSerial
    busy.value = true; error.value = ''
    try { await callback(isCurrent) }
    catch (cause) { if (isCurrent() && cause instanceof Error) error.value = cause.message }
    finally { if (isCurrent()) busy.value = false }
  }
  function scheduleAutoSave() {
    if (stopped || autoSaveBlocked || !flow.value || !dirty.value) return
    if (autoSaveTimer) clearTimeout(autoSaveTimer)
    autoSaveTimer = setTimeout(() => { autoSaveTimer = null; void persistDraft(false) }, 700)
  }
  function markDirty() {
    editSerial += 1
    dirty.value = true
    if (!autoSaveBlocked) saveError.value = ''
    scheduleAutoSave()
  }
  function assignGraph(graph: FlowGraph) {
    if (!flow.value) return
    flow.value.graph = graph
    flow.value.validation = undefined
    markDirty()
    if (selected.value && !graph.nodes.some(node => node.id === selected.value)) selected.value = ''
  }
  function updateGraph(graph: FlowGraph) {
    if (!flow.value || !editable.value || JSON.stringify(flow.value.graph) === JSON.stringify(graph)) return
    graphPast.value.push(copy(flow.value.graph))
    if (graphPast.value.length > 80) graphPast.value.shift()
    graphFuture.value = []
    assignGraph(graph)
  }
  function undoGraph() {
    if (!flow.value || !editable.value) return
    const previous = graphPast.value.pop()
    if (!previous) return
    graphFuture.value.push(copy(flow.value.graph))
    assignGraph(previous)
  }
  function redoGraph() {
    if (!flow.value || !editable.value) return
    const next = graphFuture.value.pop()
    if (!next) return
    graphPast.value.push(copy(flow.value.graph))
    assignGraph(next)
  }
  function updateNode(node: FlowNode) { if (flow.value) updateGraph({ ...flow.value.graph, nodes: flow.value.graph.nodes.map(item => item.id === node.id ? node : item) }) }
  function upsertSource(source: any) { const index = sources.value.findIndex(item => item.id === source.id); sources.value = index < 0 ? [source, ...sources.value] : sources.value.map(item => item.id === source.id ? source : item) }
  function updateMeta(patch: Partial<Workflow>) { if (!flow.value || !editable.value) return; flow.value = { ...flow.value, ...patch }; markDirty() }
  async function persistDraft(notify: boolean) {
    if (stopped) return false
    if (autoSaveTimer) { clearTimeout(autoSaveTimer); autoSaveTimer = null }
    if (savePromise) return savePromise
    if (!flow.value || !dirty.value) return true
    if (!flow.value.name.trim()) { saveError.value = '请填写流程名称'; return false }
    if (!flow.value.domains.length) { saveError.value = '请至少选择一项适用业务'; return false }
    const snapshot = copy(flow.value)
    const savingSerial = editSerial
    const savingDocument = documentSerial
    const isCurrentDocument = () => !stopped && savingDocument === documentSerial
    let createdId: string | undefined
    let succeeded = false
    saving.value = true
    savePromise = (async () => {
      try {
        // 先让出一个微任务，保证同步失败也发生在 savePromise 登记之后。
        const saved = (await Promise.resolve().then(() => workflows.save(snapshot))).data
        if (!isCurrentDocument() || !flow.value) return false
        if (savingSerial === editSerial) {
          flow.value = saved
          dirty.value = false
        } else {
          flow.value = {
            ...flow.value,
            id: saved.id,
            version: saved.version,
            status: saved.status,
            publishedRevision: saved.publishedRevision,
            updateTime: saved.updateTime,
          }
        }
        saveError.value = ''
        autoSaveBlocked = false
        succeeded = true
        lastSavedAt.value = Date.now()
        if (notify) ElMessage.success('草稿已保存，正式版本未自动改变')
        if (!snapshot.id && id.value === 'new' && saved.id) createdId = saved.id
        return true
      } catch (cause) {
        if (!isCurrentDocument()) return false
        autoSaveBlocked = true
        if (autoSaveTimer) { clearTimeout(autoSaveTimer); autoSaveTimer = null }
        saveError.value = (cause instanceof Error ? cause.message : '自动保存失败') + '；自动保存已暂停，请检查后手动保存。'
        return false
      } finally {
        // 离开的文档仍可收到网络回执，但不能清除新文档的单飞请求和计时器。
        if (isCurrentDocument()) {
          saving.value = false
          savePromise = null
          if (succeeded && dirty.value) scheduleAutoSave()
        }
      }
    })()
    const saved = await savePromise
    // 保存单飞状态先结束，再更新新建地址；路由守卫可安全冲刷保存期间产生的新编辑。
    if (saved && createdId && isCurrentDocument() && id.value === 'new') await router.replace('/workflows/' + createdId)
    return saved
  }
  async function flushDraft(notify = false) {
    const flushingDocument = documentSerial
    let showNotice = notify
    while (dirty.value || savePromise) {
      const saved = await persistDraft(showNotice)
      showNotice = false
      if (!saved || stopped || flushingDocument !== documentSerial) return false
    }
    return !stopped && flushingDocument === documentSerial
  }
  async function save() {
    const savingDocument = documentSerial
    const saved = await flushDraft(true)
    if (!saved && !stopped && savingDocument === documentSerial && saveError.value) error.value = saveError.value
    return saved
  }
  async function validate() {
    await action(async isCurrent => {
      if (!flow.value) return
      const graph = copy(flow.value.graph), validatingSerial = editSerial
      const validation = (await workflows.validate(graph)).data
      if (isCurrent() && flow.value && validatingSerial === editSerial) flow.value.validation = validation
    })
  }
  async function status(publish: boolean) {
    const publishingDocument = documentSerial
    const saved = !(dirty.value || savePromise) || await flushDraft(false)
    if (stopped || publishingDocument !== documentSerial) return
    if (!saved) { error.value = saveError.value || '自动保存失败，暂时无法发布'; return }
    await action(async isCurrent => {
      if (!flow.value?.id) return
      const snapshot = copy(flow.value)
      await ElMessageBox.confirm(publish ? '发布当前修订供正式任务使用？发布不会立即执行设备请求。' : '停用后将阻止新的正式运行，保留历史记录。', publish ? '发布工作流' : '停用工作流', { confirmButtonText: '确认', cancelButtonText: '取消' })
      if (!isCurrent()) return
      const updated = (await workflows.status(snapshot, publish)).data
      if (!isCurrent()) return
      flow.value = updated
      ElMessage.success(publish ? '工作流已发布' : '工作流已停用')
    })
  }
  async function remove(record: Workflow) {
    await action(async isCurrent => {
      const snapshot = copy(record)
      await ElMessageBox.confirm('移除“' + snapshot.name + '”？保留历史修订和执行记录。', '移除流程', { confirmButtonText: '确认移除', cancelButtonText: '取消' })
      if (!isCurrent()) return
      await workflows.remove(snapshot)
      if (isCurrent()) await load()
    })
  }
  function dropNode(nodeId: string) {
    if (!flow.value || !editable.value) return
    const current = flow.value.graph.nodes.find(node => node.id === nodeId)
    if (!current || current.kind === 'start') return
    const graph = copy(flow.value.graph)
    graph.nodes = graph.nodes.filter(node => node.id !== nodeId).map(node => ({ ...node, inputs: Object.fromEntries(Object.entries(node.inputs).filter(([, ref]) => ref.nodeId !== nodeId)) }))
    graph.edges = graph.edges.filter(edge => edge.source !== nodeId && edge.target !== nodeId)
    updateGraph(graph)
    selected.value = ''
  }
  async function removeNode(nodeId: string) {
    if (!flow.value || !editable.value) return
    const removingDocument = documentSerial
    try {
      await ElMessageBox.confirm('移除此节点及其相关连线和输入引用？', '移除节点', { confirmButtonText: '确认移除', cancelButtonText: '取消' })
      if (!stopped && removingDocument === documentSerial) dropNode(nodeId)
    } catch { /* 用户取消时保留画布。 */ }
  }
  function removeNodeImmediate(nodeId: string) { dropNode(nodeId) }
  async function openHistory() {
    await action(async isCurrent => {
      if (!flow.value?.id) return
      historyOpen.value = true
      const workflowId = flow.value.id
      const records = await Promise.all([workflows.runs(workflowId, historyOffset.value).then(value => value.data), workflows.revisions(workflowId).then(value => value.data)])
      if (isCurrent()) [history.value, revisions.value] = records
    })
  }
  async function showResult(run: Pick<FlowRun, 'id'>) {
    await action(async isCurrent => {
      if (!flow.value?.id) return
      const workflowId = flow.value.id, executionId = run.id
      const response = (await workflows.result(workflowId, executionId)).data
      if (!isCurrent()) return
      result.value = response; resultOpen.value = true
    })
  }
  async function restore(revision: number) {
    await action(async isCurrent => {
      if (!flow.value?.id) return
      await ElMessageBox.confirm('将修订 v' + revision + ' 复制为新草稿？当前未保存修改将被替换，正式版本不变。', '恢复修订', { confirmButtonText: '恢复为新草稿', cancelButtonText: '取消' })
      if (!isCurrent()) return
      // 先收齐当前保存及其间的新编辑，避免恢复与旧保存争用版本、互相覆盖画布。
      const saved = await flushDraft(false)
      if (!isCurrent() || !flow.value?.id) return
      if (!saved) { error.value = saveError.value || '草稿保存失败，暂时无法恢复修订'; return }
      const snapshot = copy(flow.value)
      const restored = (await workflows.restore(snapshot, revision)).data
      if (!isCurrent()) return
      editSerial += 1
      flow.value = restored; dirty.value = false; historyOpen.value = false
      lastSavedAt.value = restored.updateTime ? Date.parse(restored.updateTime) || Date.now() : Date.now()
      selected.value = restored.validation?.errors.find(issue => issue.nodeId)?.nodeId || ''
    })
  }
  async function prepareRun() {
    const preparingDocument = documentSerial
    error.value = ''
    const saved = !(dirty.value || savePromise) || await flushDraft(false)
    if (stopped || preparingDocument !== documentSerial) return
    if (!saved) { error.value = saveError.value || '自动保存失败，暂时无法运行'; return }
    if (!flow.value?.id) { error.value = '工作流尚未完成自动保存'; return }
    runMode.value = can('workflows.edit') ? 'draft' : 'published'; confirmWrites.value = false; runOpen.value = true
  }
  async function run() {
    await action(async isCurrent => {
      if (!flow.value?.id) return
      const snapshot = copy(flow.value), mode = runMode.value, writesConfirmed = confirmWrites.value
      let inputs: any
      try { inputs = parseWorkflowJson(runInputs.value) }
      catch (cause) { if (cause instanceof SyntaxError) throw new Error('运行输入不是有效 JSON'); throw cause }
      if (!inputs || typeof inputs !== 'object' || Array.isArray(inputs)) throw new Error('开始节点输入需要 JSON 对象')
      const body = JSON.stringify({ id: snapshot.id, version: snapshot.version, mode, inputs, confirmWrites: writesConfirmed })
      if (pending?.body !== body) pending = { body, key: createClientId() }
      const response = await workflows.run(snapshot, inputs, mode, pending!.key, writesConfirmed)
      if (!isCurrent()) return
      if (!response.data?.id) throw new Error(response.message || '没有收到运行标识，请先查看历史记录')
      result.value = response.data; runOpen.value = false; resultOpen.value = true
      if (response.data.status !== 'running') pending = null
    })
  }
  async function exportFlow() {
    await action(async isCurrent => {
      if (!flow.value?.id) return
      const workflowId = flow.value.id
      const data = (await workflows.export(workflowId)).data
      if (!isCurrent()) return
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }))
      const anchor = document.createElement('a')
      anchor.href = url; anchor.download = 'workflow-' + workflowId + '.json'; anchor.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    })
  }
  async function generateAi(value: { prompt: string; name: string; resourceIds: string[] }) {
    await action(async isCurrent => {
      if (!flow.value) return
      if (dirty.value) await ElMessageBox.confirm('AI 草稿会替换当前未保存的画布，是否继续？', '生成工作流', { confirmButtonText: '继续生成', cancelButtonText: '取消' })
      if (!isCurrent() || !flow.value) return
      const generated = (await workflows.generate(value.prompt, value.name, copy(flow.value.domains), value.resourceIds)).data
      // 长时间生成期间可能已切换流程；旧结果不得覆盖并自动保存到另一份文档。
      if (!isCurrent() || !flow.value) return
      flow.value = { ...generated.workflow, id: flow.value.id, version: flow.value.version, status: flow.value.status, publishedRevision: flow.value.publishedRevision }
      // AI 替换画布同样计入编辑代次并触发自动保存，避免旧保存回执覆盖新草稿。
      markDirty()
      selected.value = generated.workflow.validation?.errors.find(item => item.nodeId)?.nodeId || flow.value.graph.nodes[0]?.id || ''
      aiOpen.value = false
      generationNotice.value = (generated.generation?.warnings || []).join(' ')
      if (generated.generation?.mode === 'fallback') ElMessage.warning('模型输出不可用，已改为安全可编辑草稿')
      else if (generationNotice.value) ElMessage.warning('AI 草稿已生成，并已标出需要确认的配置')
      else ElMessage.success('AI 草稿已生成，正在自动保存，请检查配置后发布')
    })
  }
  async function resumeRun(input: unknown) {
    await action(async isCurrent => {
      if (!result.value) return
      const snapshot = copy(result.value), body = JSON.stringify(input)
      if (!controlPending || controlPending.action !== 'resume' || controlPending.execution !== snapshot.id || controlPending.body !== body) controlPending = { action: 'resume', execution: snapshot.id, body, key: createClientId() }
      const response = await workflows.resume(snapshot, input, controlPending.key)
      if (!isCurrent() || result.value?.id !== snapshot.id) return
      result.value = response.data
      if (!['running', 'waiting'].includes(response.data.status)) controlPending = null
    })
  }
  async function cancelRun() {
    await action(async isCurrent => {
      if (!result.value) return
      const snapshot = copy(result.value)
      await ElMessageBox.confirm('取消后不会撤回已经发送的 HTTP、工具或数据库操作，确定继续？', '取消运行', { confirmButtonText: '确认取消', cancelButtonText: '返回' })
      if (!isCurrent() || result.value?.id !== snapshot.id) return
      if (!controlPending || controlPending.action !== 'cancel' || controlPending.execution !== snapshot.id) controlPending = { action: 'cancel', execution: snapshot.id, body: '', key: createClientId() }
      const response = await workflows.cancel(snapshot, controlPending.key)
      if (!isCurrent() || result.value?.id !== snapshot.id) return
      result.value = response.data
      if (response.data.status === 'cancelled') controlPending = null
    })
  }
  async function importFlow(file: File) {
    await action(async isCurrent => {
      if (file.size > 1024 * 1024) throw new Error('流程定义不能超过 1 MB')
      const text = await file.text()
      if (!isCurrent()) return
      const data = parseWorkflowJson(text)
      if (data.schemaVersion !== 1 || !data.workflow?.graph) throw new Error('请选择本平台导出的流程定义')
      const record = await workflows.save({ name: data.workflow.name, description: data.workflow.description || '', domains: data.workflow.domains || ['general'], graph: ensureReadableWorkflowLayout(data.workflow.graph), status: 'draft' })
      if (isCurrent()) openTopLevel('/workflows/' + record.data.id)
    })
  }
  async function refreshResult() {
    const current = result.value
    if (!current || resultRefreshing.value || busy.value) return
    const refreshingDocument = documentSerial
    resultRefreshing.value = true
    try {
      const latest = (await workflows.result(current.workflowId, current.id)).data
      if (!stopped && result.value === current) { result.value = latest; error.value = '' }
    } catch (cause) {
      if (!stopped && result.value === current) error.value = '暂时无法更新运行状态，可点击“刷新结果”重试：' + (cause as Error).message
    } finally { if (!stopped && refreshingDocument === documentSerial) resultRefreshing.value = false }
  }
  watch(() => [resultOpen.value, result.value?.id, result.value?.status], (_, __, onCleanup) => {
    if (!resultOpen.value || !['running', 'waiting', 'cancel_requested'].includes(result.value?.status || '')) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>
    const schedule = () => { timer = setTimeout(async () => { await refreshResult(); if (!cancelled && !stopped) schedule() }, 3000) }
    schedule()
    onCleanup(() => { cancelled = true; clearTimeout(timer) })
  })
  watch(id, (current, previous) => {
    // 首次保存仅把同一草稿迁移到正式 id，不能重新加载覆盖正在编辑的画布。
    if (previous === 'new' && current === flow.value?.id) return
    documentSerial += 1
    // 新文档有独立保存状态；旧请求继续在后端收尾，其迟到结果由文档代次拒绝。
    if (autoSaveTimer) { clearTimeout(autoSaveTimer); autoSaveTimer = null }
    savePromise = null; saving.value = false; busy.value = false; resultRefreshing.value = false
    autoSaveBlocked = false; saveError.value = ''; lastSavedAt.value = null
    flow.value = null; dirty.value = false; historyOffset.value = 0
    graphPast.value = []; graphFuture.value = []
    runOpen.value = false; resultOpen.value = false; historyOpen.value = false; aiOpen.value = false
    pending = null; controlPending = null
    void load()
  }, { immediate: true })
  const beforeUnload = (event: BeforeUnloadEvent) => { if (dirty.value) { event.preventDefault(); event.returnValue = '' } }
  window.addEventListener('beforeunload', beforeUnload)
  onBeforeUnmount(() => { stopped = true; controller?.abort(); if (autoSaveTimer) clearTimeout(autoSaveTimer); autoSaveTimer = null; savePromise = null; window.removeEventListener('beforeunload', beforeUnload) })
  return { rows, flow, catalog, sources, loading, busy, saving, lastSavedAt, saveError, error, generationNotice, dirty, keyword, offset, selected, selectedNode, editable, fields,
    canUndo, canRedo,
    result, resultOpen, resultRefreshing, refreshResult, runOpen, runInputs, runMode, confirmWrites, aiOpen, historyOpen, history, revisions, historyOffset, resources,
    load, updateGraph, undoGraph, redoGraph, updateNode, upsertSource, updateMeta, save, flushDraft, validate, status, remove, removeNode, removeNodeImmediate, openHistory, showResult, restore, prepareRun, run, resumeRun, cancelRun, generateAi, exportFlow, importFlow }
}
