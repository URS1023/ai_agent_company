/**
 * aiGenerationProgress —— AI 生成过程的"阶段进度"模型（纯计算，无网络、无计时器）。
 *
 * 位置：AiGenerationConsole 展示阶段/进度/遥测，JimuBigScreenReplica 维护生成会话状态。
 * 关键约束：
 * - 这是**表现层**进度：百分比与阶段只用于让用户感知进度，绝不代表后端真实完成度，
 *   业务上"是否生成成功"必须以接口返回为准（completeGenerationSession 的 state 由调用方给定）；
 * - 随机数使用会话内自持的伪随机状态（nextRandom）：同一 seed 必须复现同一条进度曲线，
 *   便于测试与刷新后继续；不得改用 Math.random；
 * - 所有函数按"传入 now"计算，不读系统时钟，便于回放与单测；
 * - AI_GENERATION_STAGES 的 id/label/color 是界面文案与配色来源，新增阶段需同步
 *   STAGE_STARTS / SESSION_STAGE_CEILINGS 等并行数组的长度。
 */
export const AI_GENERATION_STAGES = Object.freeze([
  { id: 'intent', label: '意图深度解析', detail: '拆解业务目标与分析口径', icon: 'fa-brain', color: '#54ff87' },
  { id: 'vector', label: '向量库语义召回', detail: 'BAAI/bge-m3 正在检索相关表', icon: 'fa-cubes-stacked', color: '#23c7f3' },
  { id: 'knowledge', label: '知识库关系扩展', detail: '推导主从表、引用字段与指标口径', icon: 'fa-diagram-project', color: '#31d6a3' },
  { id: 'generation', label: 'AI 深度学习编排', detail: '并行生成 SQL、图表和组件配置', icon: 'fa-microchip', color: '#f0b84b' },
  { id: 'render', label: '数据渲染与审查', detail: '执行质量检查并装配可视化界面', icon: 'fa-wand-magic-sparkles', color: '#ff6d6d' }
])

const STAGE_STARTS = [0, 2600, 6200, 10400, 14800]
const STAGE_PROGRESS = [4, 22, 43, 67, 83]
const STAGE_END_PROGRESS = [22, 43, 67, 83, 92]
const SESSION_STAGE_CEILINGS = [18, 38, 61, 82, 96]
const SESSION_STAGE_MIN_MS = [1800, 2600, 3000, 3400, 4200]
const SESSION_STAGE_EVENTS = [
  '正在拆解业务目标与指标口径',
  '正在从向量库召回候选数据表',
  '正在扩展知识图谱与字段关系',
  '正在并行生成 SQL 与组件配置',
  '正在审查布局并合成视觉图层'
]

function nextRandom(session) {
  session.randomState = (session.randomState + 0x6D2B79F5) >>> 0
  let value = session.randomState
  value = Math.imul(value ^ (value >>> 15), value | 1)
  value ^= value + Math.imul(value ^ (value >>> 7), value | 61)
  return ((value ^ (value >>> 14)) >>> 0) / 4294967296
}

function randomBetween(session, min, max) {
  return min + (max - min) * nextRandom(session)
}

function sessionSnapshot(session) {
  const stageStart = session.activeIndex === 0 ? 0 : SESSION_STAGE_CEILINGS[session.activeIndex - 1]
  const stageEnd = SESSION_STAGE_CEILINGS[session.activeIndex]
  return {
    percent: Math.round(session.percent * 10) / 10,
    activeIndex: session.activeIndex,
    state: session.state,
    isPaused: session.isPaused,
    event: session.event,
    nextTickMs: session.nextTickMs,
    qualityScore: Math.round(session.qualityScore),
    stageProgress: Math.round(clamp((session.percent - stageStart) / Math.max(1, stageEnd - stageStart), 0, 1) * 100),
    telemetry: { ...session.telemetry }
  }
}

/**
 * 创建生成会话（纯状态对象，不含定时器）。
 * @param {number} seed 随机种子：同种子可复现同一进度曲线
 * @param {number} now 起始时间戳
 * @returns {object} 会话状态，交由 advanceGenerationSession 推进
 */
export function createGenerationSession(seed = Date.now(), now = Date.now()) {
  const normalizedSeed = (Number(seed) || 1) >>> 0
  return {
    seed: normalizedSeed,
    randomState: normalizedSeed,
    createdAt: now,
    stageStartedAt: now,
    nextUpdateAt: now + 220,
    pauseUntil: 0,
    percent: 3,
    activeIndex: 0,
    state: 'running',
    isPaused: false,
    event: SESSION_STAGE_EVENTS[0],
    nextTickMs: 220,
    qualityScore: 62,
    pauseCount: 0,
    breakthroughCount: 0,
    telemetry: {
      vectorHits: 6,
      relationEdges: 2,
      sqlDrafts: 0,
      renderNodes: 0,
      candidateTables: 3,
      throughput: 18
    }
  }
}

function updateTelemetry(session, progressed) {
  const random = () => nextRandom(session)
  session.telemetry.vectorHits = Math.min(168, session.telemetry.vectorHits + (session.activeIndex >= 1 ? Math.floor(random() * 4) : Math.floor(random() * 2)))
  session.telemetry.relationEdges = Math.min(96, session.telemetry.relationEdges + (session.activeIndex >= 2 ? Math.floor(random() * 3) : Math.floor(random() * 2)))
  session.telemetry.sqlDrafts = Math.min(18, session.telemetry.sqlDrafts + (session.activeIndex >= 3 && random() > .58 ? 1 : 0))
  session.telemetry.renderNodes = Math.min(48, session.telemetry.renderNodes + (session.activeIndex >= 4 && random() > .42 ? 1 : 0))
  session.telemetry.candidateTables = Math.min(24, session.telemetry.candidateTables + (session.activeIndex >= 1 && random() > .74 ? 1 : 0))
  session.telemetry.throughput = Math.round(12 + random() * 76 + (progressed ? 16 : 0))
  session.qualityScore = Math.min(97, session.qualityScore + (progressed ? random() * .62 : random() * .08))
}

function advanceStageWhenReady(session, tickAt) {
  const ceiling = SESSION_STAGE_CEILINGS[session.activeIndex]
  const residence = tickAt - session.stageStartedAt
  if (
    session.activeIndex < SESSION_STAGE_CEILINGS.length - 1
    && session.percent >= ceiling - .5
    && residence >= SESSION_STAGE_MIN_MS[session.activeIndex]
  ) {
    session.activeIndex += 1
    session.stageStartedAt = tickAt
    session.event = SESSION_STAGE_EVENTS[session.activeIndex]
    return true
  }
  return false
}

/**
 * 把会话推进到指定时间点（按时间片补算，不依赖真实定时器）。
 * @returns {object} 会话快照（percent/activeIndex/state/isPaused/event/telemetry 等）
 * 非 running 状态直接返回快照，不修改会话。
 */
export function advanceGenerationSession(session, now = Date.now()) {
  if (!session || session.state !== 'running') return sessionSnapshot(session)
  const targetTime = Math.max(session.createdAt, Number(now) || session.createdAt)
  let guard = 0

  while (session.nextUpdateAt <= targetTime && guard < 160) {
    guard += 1
    const tickAt = session.nextUpdateAt

    if (session.pauseUntil > tickAt) {
      session.isPaused = true
      session.event = `计算节点暂驻 · ${SESSION_STAGE_EVENTS[session.activeIndex]}`
      updateTelemetry(session, false)
      session.nextTickMs = Math.round(randomBetween(session, 180, 520))
      session.nextUpdateAt = tickAt + session.nextTickMs
      continue
    }

    session.isPaused = false
    advanceStageWhenReady(session, tickAt)
    const ceiling = SESSION_STAGE_CEILINGS[session.activeIndex]
    const distance = Math.max(0, ceiling - session.percent)
    const residence = tickAt - session.stageStartedAt
    const elapsed = tickAt - session.createdAt
    const pauseProbability = residence < 900 ? .08 : Math.max(.08, .22 - residence / 42000)
    const roll = nextRandom(session)
    const forceFirstPause = session.pauseCount === 0 && elapsed >= 620
    const forceFirstBreakthrough = session.pauseCount > 0
      && session.breakthroughCount === 0
      && elapsed >= 1450
    let wasBreakthrough = false

    if (distance > .7 && !forceFirstBreakthrough && (forceFirstPause || roll < pauseProbability)) {
      const pauseDuration = Math.round(randomBetween(
        session,
        480,
        session.breakthroughCount === 0 ? 720 : 2200
      ))
      session.pauseUntil = tickAt + pauseDuration
      session.pauseCount += 1
      session.isPaused = true
      session.event = `正在校准推理路径 · ${Math.ceil(pauseDuration / 100) / 10}s`
      updateTelemetry(session, false)
    } else {
      const breakthrough = distance > 3 && (forceFirstBreakthrough || roll < pauseProbability + .2)
      const increment = breakthrough
        ? randomBetween(session, 2.5, 6.8)
        : randomBetween(session, .2, 1.8)
      session.percent = Math.min(ceiling, session.percent + increment)
      session.event = breakthrough
        ? `计算突破 +${increment.toFixed(1)}% · ${SESSION_STAGE_EVENTS[session.activeIndex]}`
        : SESSION_STAGE_EVENTS[session.activeIndex]
      updateTelemetry(session, true)
      if (breakthrough) {
        session.breakthroughCount += 1
        wasBreakthrough = true
      }
      advanceStageWhenReady(session, tickAt)
    }

    session.nextTickMs = Math.round(randomBetween(session, wasBreakthrough ? 480 : 180, 720))
    session.nextUpdateAt = tickAt + session.nextTickMs
  }

  session.isPaused = session.pauseUntil > targetTime
  return sessionSnapshot(session)
}

/**
 * 结束会话：success 时把进度收敛到 100%、停在最后阶段并抬高质量分；
 * error 时保留当前阶段信息并改写事件文案。会话为空时退回 resolveGenerationProgress(0, state)。
 * @returns {object} 会话快照
 */
export function completeGenerationSession(session, state = 'success', now = Date.now()) {
  if (!session) return resolveGenerationProgress(0, state)
  advanceGenerationSession(session, now)
  session.state = state
  session.isPaused = false
  session.pauseUntil = 0
  if (state === 'success') {
    session.percent = 100
    session.activeIndex = AI_GENERATION_STAGES.length - 1
    session.qualityScore = Math.max(96, session.qualityScore)
    session.event = '质量审查通过 · 可视化图层汇聚完成'
  } else if (state === 'error') {
    session.event = `计算链路中断 · ${SESSION_STAGE_EVENTS[session.activeIndex]}`
  }
  return sessionSnapshot(session)
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value))
}

/**
 * 仅按已耗时推算进度（无会话状态时的兜底路径，例如刷新后只剩耗时信息）。
 * @param {number} elapsedMs 已耗时毫秒
 * @param {string} status 运行状态（running / success / error 等）
 * @returns {object} 与会话快照同构的进度对象
 */
export function resolveGenerationProgress(elapsedMs = 0, status = 'running') {
  const elapsed = Math.max(0, Number(elapsedMs) || 0)
  let activeIndex = STAGE_STARTS.length - 1
  for (let index = 0; index < STAGE_STARTS.length - 1; index += 1) {
    if (elapsed < STAGE_STARTS[index + 1]) {
      activeIndex = index
      break
    }
  }

  const stageStart = STAGE_STARTS[activeIndex]
  const stageEnd = STAGE_STARTS[activeIndex + 1] ?? 19000
  const ratio = clamp((elapsed - stageStart) / Math.max(1, stageEnd - stageStart), 0, 1)
  const runningPercent = Math.round(
    STAGE_PROGRESS[activeIndex]
      + (STAGE_END_PROGRESS[activeIndex] - STAGE_PROGRESS[activeIndex]) * ratio
  )
  const percent = status === 'success' ? 100 : Math.min(92, runningPercent)

  return {
    percent,
    activeIndex: status === 'success' ? AI_GENERATION_STAGES.length - 1 : activeIndex,
    state: status,
    telemetry: {
      vectorHits: Math.min(128, Math.floor(elapsed / 115) + 6),
      relationEdges: Math.min(64, Math.floor(elapsed / 310) + 2),
      sqlDrafts: Math.min(12, Math.floor(Math.max(0, elapsed - 7200) / 820)),
      renderNodes: Math.min(36, Math.floor(Math.max(0, elapsed - 11200) / 360))
    }
  }
}
