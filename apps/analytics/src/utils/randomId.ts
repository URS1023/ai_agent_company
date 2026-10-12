/**
 * randomId —— 客户端关联标识 / 幂等键生成。
 *
 * 位置：工作流节点与连线的前端临时 id、报表组件的本地 id 等。
 * 关键约束：
 * - 必须在安全上下文（https/localhost）、局域网 http 与旧浏览器下都可用：
 *   优先 crypto.randomUUID，其次 getRandomValues，最后退化为带时间/计数器种子的伪随机填充；
 * - 生成的 id 只用于客户端关联与幂等，**不得**用作认证或密钥（认证密钥一律由服务端生成）；
 * - 返回值始终是标准 UUID 字符串格式（小写十六进制，带连字符）。
 */
let fallbackSequence = 0

function fillFallback(bytes: Uint8Array): void {
  fallbackSequence = (fallbackSequence + 1) >>> 0
  const monotonic = typeof performance === 'object' ? Math.floor(performance.now() * 1000) : 0
  let seed = (Date.now() ^ monotonic ^ fallbackSequence) >>> 0

  for (let index = 0; index < bytes.length; index += 1) {
    seed = Math.imul(seed ^ (seed >>> 15), 1 | seed)
    seed ^= seed + Math.imul(seed ^ (seed >>> 7), 61 | seed)
    bytes[index] = ((seed ^ (seed >>> 14)) + Math.floor(Math.random() * 256)) & 0xff
  }
}

function formatUuid(bytes: Uint8Array): string {
  const hex = Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

/**
 * Create client correlation and idempotency identifiers across secure, LAN HTTP,
 * and legacy browser contexts. Authentication keys remain server-generated.
 */
export function createClientId(): string {
  const webCrypto = typeof globalThis.crypto === 'object' ? globalThis.crypto : undefined
  if (webCrypto && typeof webCrypto.randomUUID === 'function') {
    try {
      return webCrypto.randomUUID()
    } catch {
      // Continue with getRandomValues when a browser exposes but blocks randomUUID.
    }
  }

  const bytes = new Uint8Array(16)
  if (webCrypto && typeof webCrypto.getRandomValues === 'function') {
    webCrypto.getRandomValues(bytes)
  } else {
    fillFallback(bytes)
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  return formatUuid(bytes)
}
