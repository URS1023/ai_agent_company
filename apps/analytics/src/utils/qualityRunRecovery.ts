/**
 * Browser-only recovery for a quality-run request whose response was not
 * confirmed.  The stored payload is deliberately a small allow-list of the
 * public quality-run contract: it never contains file bytes, credentials or
 * the login token.
 *
 * 中文补充：质检运行（quality-run）请求发出后若未收到确认响应，用它把"待确认的运行请求"
 * 暂存在本标签页的 sessionStorage 中，供用户刷新/重进后恢复重试，避免重复发起真实质检运行。
 * 关键约束：
 * - 只允许存"公开运行契约"的白名单字段（版本、发布修订、幂等键、写库确认、可选旧版超时、分组与样本 ID），
 *   绝不写入文件字节、凭据或登录令牌；扩大字段前必须重新评估隐私与安全影响；
 * - 键必须位于 request.ts 的工作区/用户作用域之下，身份切换时要清理其它作用域残留（见 purgeOtherQualityRunRecovery）；
 * - 记录有 24 小时 TTL 且带 schema 版本号，解析失败/过期/不匹配一律视为无效并删除；
 * - 存储被浏览器禁用时所有读写静默降级（返回 null/false），不得让质检流程因存储异常而中断。
 */

export const QUALITY_RUN_RECOVERY_SCOPE = 'quality-run-pending'
export const QUALITY_RUN_RECOVERY_SCHEMA = 1
export const QUALITY_RUN_RECOVERY_TTL_MS = 24 * 60 * 60 * 1000

export interface QualityRunRecoverySample {
  sampleId: string
  imageFileId: string | null
}

export interface QualityRunRecoveryPayload {
  version: number
  publishedRevision: number
  idempotencyKey: string
  confirmWrites: boolean
  /** 兼容旧版恢复记录；提交时由 API 层剔除，不再作为整轮截止时间发送。 */
  timeoutSeconds?: number
  groups: Array<{
    nodeId: string
    samples: QualityRunRecoverySample[]
  }>
}

export interface QualityRunRecoveryRecord {
  workflowId: string
  entryPath: string
  savedAt: number
  payload: QualityRunRecoveryPayload
}

interface StorageLike {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
  removeItem(key: string): void
  key(index: number): string | null
  readonly length: number
}

const text = (value: unknown, maximum: number): value is string => typeof value === 'string' && value.length > 0 && value.length <= maximum
const clonePayload = (payload: QualityRunRecoveryPayload): QualityRunRecoveryPayload => ({
  version: payload.version,
  publishedRevision: payload.publishedRevision,
  idempotencyKey: payload.idempotencyKey,
  confirmWrites: payload.confirmWrites,
  ...(payload.timeoutSeconds === undefined ? {} : { timeoutSeconds: payload.timeoutSeconds }),
  groups: payload.groups.map(group => ({
    nodeId: group.nodeId,
    samples: group.samples.map(sample => ({ sampleId: sample.sampleId, imageFileId: sample.imageFileId })),
  })),
})

function parsePayload(value: unknown): QualityRunRecoveryPayload | null {
  if (!value || typeof value !== 'object') return null
  const candidate = value as Record<string, unknown>
  if (!Number.isInteger(candidate.version) || Number(candidate.version) < 1
    || !Number.isInteger(candidate.publishedRevision) || Number(candidate.publishedRevision) < 1
    || typeof candidate.confirmWrites !== 'boolean'
    || (candidate.timeoutSeconds !== undefined
      && (!Number.isInteger(candidate.timeoutSeconds) || Number(candidate.timeoutSeconds) < 1 || Number(candidate.timeoutSeconds) > 300))
    || typeof candidate.idempotencyKey !== 'string' || !/^[A-Za-z0-9_-]{8,100}$/.test(candidate.idempotencyKey)
    || !Array.isArray(candidate.groups) || candidate.groups.length < 1 || candidate.groups.length > 30) return null

  let sampleCount = 0
  const groups: QualityRunRecoveryPayload['groups'] = []
  const groupIds = new Set<string>()
  for (const rawGroup of candidate.groups) {
    if (!rawGroup || typeof rawGroup !== 'object') return null
    const group = rawGroup as Record<string, unknown>
    if (!text(group.nodeId, 80) || !Array.isArray(group.samples) || group.samples.length < 1 || group.samples.length > 100
      || groupIds.has(group.nodeId)) return null
    groupIds.add(group.nodeId)
    const samples: QualityRunRecoverySample[] = []
    const sampleIds = new Set<string>()
    for (const rawSample of group.samples) {
      if (!rawSample || typeof rawSample !== 'object') return null
      const sample = rawSample as Record<string, unknown>
      if (!text(sample.sampleId, 160) || !String(sample.sampleId).trim() || sampleIds.has(sample.sampleId)) return null
      if (sample.imageFileId !== null && !text(sample.imageFileId, 26)) return null
      sampleIds.add(sample.sampleId)
      samples.push({ sampleId: sample.sampleId, imageFileId: sample.imageFileId as string | null })
      sampleCount += 1
    }
    groups.push({ nodeId: group.nodeId, samples })
  }
  if (sampleCount < 1 || sampleCount > 100) return null
  return { version: candidate.version as number, publishedRevision: candidate.publishedRevision as number,
    idempotencyKey: candidate.idempotencyKey, confirmWrites: candidate.confirmWrites,
    ...(candidate.timeoutSeconds === undefined ? {} : { timeoutSeconds: candidate.timeoutSeconds as number }), groups }
}

function parseRecord(raw: string | null, now: number): QualityRunRecoveryRecord | null {
  if (!raw) return null
  try {
    const value = JSON.parse(raw) as Record<string, unknown>
    if (value.schema !== QUALITY_RUN_RECOVERY_SCHEMA || !text(value.workflowId, 80) || !text(value.entryPath, 300)
      || !Number.isInteger(value.savedAt) || now - Number(value.savedAt) > QUALITY_RUN_RECOVERY_TTL_MS
      || now - Number(value.savedAt) < -5 * 60 * 1000) return null
    const payload = parsePayload(value.payload)
    return payload ? { workflowId: value.workflowId, entryPath: value.entryPath, savedAt: value.savedAt as number, payload } : null
  } catch {
    return null
  }
}

function browserSessionStorage(): StorageLike | undefined {
  try {
    return typeof sessionStorage === 'undefined' ? undefined : sessionStorage
  } catch {
    return undefined
  }
}

function readStored(storage: StorageLike, key: string): string | null {
  try { return storage.getItem(key) } catch { return null }
}

function recoveryKeys(storage: StorageLike): string[] {
  const keys: string[] = []
  try {
    for (let index = 0; index < storage.length; index += 1) {
      const key = storage.key(index)
      if (key?.includes(`:${QUALITY_RUN_RECOVERY_SCOPE}:`)) keys.push(key)
    }
  } catch { /* Browser policy may revoke storage after initialization. */ }
  return keys
}

/**
 * 生成恢复记录的存储键（位于 request.ts 的工作区/用户作用域前缀之下）。
 * entryPath 与 workflowId 做 URL 编码，避免路径分隔符破坏键结构；任一参数为空时返回空串（调用方据此禁用恢复）。
 */

/** Build one key below request.ts's workspace/user scope. */
export function qualityRunRecoveryKey(scopePrefix: string, entryPath: string, workflowId: string): string {
  if (!scopePrefix || !entryPath || !workflowId) return ''
  return `${scopePrefix}:${encodeURIComponent(entryPath)}:${encodeURIComponent(workflowId)}`
}

/**
 * 创建恢复记录的读写器（load/save/clear）。
 * load 会校验 workflowId 与 entryPath 是否匹配当前上下文，不匹配即删除残留记录并返回 null
 * （避免把 A 工作流的待确认请求恢复到 B 工作流上）；存储不可用时 save 返回 false 而非抛错。
 */
export function createQualityRunRecoveryStore(
  key: string,
  storage: StorageLike | undefined = browserSessionStorage(),
  now: () => number = Date.now,
) {
  return {
    load(workflowId: string, entryPath: string): QualityRunRecoveryRecord | null {
      if (!key || !storage) return null
      const record = parseRecord(readStored(storage, key), now())
      if (!record || record.workflowId !== workflowId || record.entryPath !== entryPath) {
        if (readStored(storage, key) !== null) {
          try { storage.removeItem(key) } catch { /* Storage may be disabled. */ }
        }
        return null
      }
      return record
    },
    save(workflowId: string, entryPath: string, payload: QualityRunRecoveryPayload): boolean {
      if (!key || !storage) return false
      const record = { schema: QUALITY_RUN_RECOVERY_SCHEMA, workflowId, entryPath, savedAt: now(), payload: clonePayload(payload) }
      try {
        storage.setItem(key, JSON.stringify(record))
        return true
      } catch {
        return false
      }
    },
    clear(): void {
      if (!key || !storage) return
      try { storage.removeItem(key) } catch { /* Storage may be disabled. */ }
    },
  }
}

/**
 * 清理本标签页中所有非法或已过期的质检运行恢复记录。
 * 判定与 load 共用 parseRecord（含 schema 校验与 TTL），保证"过期"定义只有一处。
 */

/** Remove malformed and expired quality-run records from this browser session. */
export function purgeExpiredQualityRunRecovery(storage: StorageLike | undefined = browserSessionStorage(), now: () => number = Date.now): void {
  if (!storage) return
  const keys = recoveryKeys(storage)
  for (const key of keys) {
    if (!parseRecord(readStored(storage, key), now())) {
      try { storage.removeItem(key) } catch { /* Storage may be disabled. */ }
    }
  }
}

/**
 * 身份（用户/工作区）变化时丢弃本标签页中其它作用域的恢复记录，避免串号读取。
 */

/** On an identity change, discard stale user/workspace entries in this tab. */
export function purgeOtherQualityRunRecoveryScopes(
  currentScopePrefix: string,
  storage: StorageLike | undefined = browserSessionStorage(),
): void {
  if (!storage || !currentScopePrefix) return
  const keys = recoveryKeys(storage).filter(key => !key.startsWith(currentScopePrefix))
  for (const key of keys) {
    try { storage.removeItem(key) } catch { /* Storage may be disabled. */ }
  }
}

/**
 * 宿主显式登出时清空全部质检运行恢复记录（不论作用域）。
 */

/** Remove every quality-run recovery record when the host explicitly logs out. */
export function clearQualityRunRecovery(storage: StorageLike | undefined = browserSessionStorage()): void {
  if (!storage) return
  const keys = recoveryKeys(storage)
  for (const key of keys) {
    try { storage.removeItem(key) } catch { /* Storage may be disabled. */ }
  }
}
