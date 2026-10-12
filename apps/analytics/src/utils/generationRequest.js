/**
 * generationRequest —— AI 生成请求的"同一请求复用同一 sessionId"包装（幂等与去重）。
 *
 * 位置：报表/大屏 AI 生成的发送层。
 * 关键约束：
 * - 幂等前提：超时/网络中断时服务端可能已经开始生成，重试必须复用完全相同的请求体与 sessionId，
 *   否则会重复扣费并产生两份生成结果；
 * - 只在**明确未开始生成**的失败上丢弃缓存：校验/鉴权类状态码（400/401/403/404/422）
 *   或错误体已带 generationId（说明生成器已给出终态）。网关/传输错误与"进行中"冲突保持缓存，
 *   只允许用户主动重试时复用原 key；
 * - generationSeed / "本次设计种子" 行是前端本地提示信息，发送前必须剔除，避免影响请求指纹。
 */

/** Keep the exact request/key when a timeout leaves the server result unknown. */
export function createGenerationRequester({ send, createId, scope = () => '' }) {
  const pending = new Map()
  return function generate(kind, data, options) {
    const normalized = { ...data }
    delete normalized.generationSeed
    for (const key of ['theme', 'requirement']) {
      if (typeof normalized[key] === 'string') normalized[key] = normalized[key].replace(/^本次(?:组件)?设计种子：.*(?:\r?\n|$)/gm, '')
    }
    const identity = JSON.stringify([scope(), kind, normalized])
    let attempt = pending.get(identity)
    if (!attempt) {
      attempt = { body: { ...JSON.parse(JSON.stringify(data)), sessionId: data.sessionId || createId() }, promise: null }
      pending.set(identity, attempt)
    }
    if (attempt.promise) return attempt.promise
    attempt.promise = Promise.resolve().then(() => send(kind, attempt.body, options)).then(result => {
      pending.delete(identity)
      return result
    }, error => {
      const status = error.response?.status
      const body = error.response?.data
      // Validation/authentication failures cannot have started a generation.
      // A generation ID on an error identifies the generator's terminal
      // failure response. Gateway/transport errors and in-progress conflicts
      // remain ambiguous: only a user retry may resend the original key.
      if ([400, 401, 403, 404, 422].includes(status) || body?.generation_id || body?.generationId) pending.delete(identity)
      attempt.promise = null
      throw error
    })
    return attempt.promise
  }
}
