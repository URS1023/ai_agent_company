/** Server-only route implementation; authentication remains in native Dify.
 * Only registered public business routes are forwarded. Internal read-node APIs
 * and caller-selected upstreams are deliberately outside this browser boundary.
 */
type ProxyOptions = {
  enabled: boolean
  upstream?: string
  fetcher?: typeof fetch
}

const routes: [RegExp, readonly string[]][] = [
  [/^health$/, ['GET']],
  [/^v1\/me$/, ['GET']],
  [/^v1\/run-requests\/lookup$/, ['GET']],
  [/^v1\/sources\/capabilities$/, ['GET']],
  [/^v1\/sources$/, ['GET', 'POST']],
  [/^v1\/sources\/[\w-]{1,128}$/, ['GET', 'PUT']],
  [/^v1\/devices$/, ['GET', 'POST']],
  [/^v1\/devices\/[\w-]{1,128}$/, ['GET', 'PUT', 'DELETE']],
  [/^v1\/devices\/[\w-]{1,128}\/(?:alert|quality)\/schedule$/, ['GET', 'POST']],
  [/^v1\/devices\/[\w-]{1,128}\/(?:alert|quality)\/schedule\/actors$/, ['GET']],
  [/^v1\/schedules\/[\w-]{1,128}$/, ['GET']],
  [/^v1\/schedules\/[\w-]{1,128}\/state$/, ['POST']],
  [/^v1\/dashboards$/, ['GET', 'POST']],
  [/^v1\/dashboard-templates$/, ['GET']],
  [/^v1\/dashboard-queries$/, ['GET']],
  [/^v1\/dashboards\/[\w-]{1,128}$/, ['GET']],
  [/^v1\/dashboards\/[\w-]{1,128}\/bindings$/, ['GET', 'PUT']],
  [/^v1\/dashboards\/[\w-]{1,128}\/refresh$/, ['POST']],
  [/^v1\/dashboards\/[\w-]{1,128}\/sql-proposals$/, ['POST']],
  [/^v1\/dashboards\/[\w-]{1,128}\/sql-proposals\/[\w-]{1,128}$/, ['GET']],
  [/^v1\/dashboards\/[\w-]{1,128}\/sql-proposals\/[\w-]{1,128}\/trial$/, ['POST']],
  [/^v1\/dashboards\/[\w-]{1,128}\/sql-proposals\/[\w-]{1,128}\/preview$/, ['POST']],
  [/^v1\/dashboards\/[\w-]{1,128}\/sql-proposals\/[\w-]{1,128}\/trials\/[\w-]{1,128}$/, ['GET']],
  [/^v1\/dashboards\/[\w-]{1,128}\/sql-proposals\/[\w-]{1,128}\/trials$/, ['GET']],
  [
    /^v1\/dashboards\/[\w-]{1,128}\/sql-proposals\/[\w-]{1,128}\/trials\/[\w-]{1,128}\/(?:assessment|preview|sample-check(?:\/automatic)?)$/,
    ['GET'],
  ],
  [/^v1\/devices\/[\w-]{1,128}\/bindings\/(?:alert|quality)$/, ['GET', 'PUT']],
  [/^v1\/devices\/[\w-]{1,128}\/bindings\/(?:alert|quality)\/runs$/, ['GET', 'POST']],
  [/^v1\/devices\/[\w-]{1,128}\/bindings\/(?:alert|quality)\/workflow-setups$/, ['GET', 'POST']],
  [/^v1\/workflow-setups\/[\w-]{1,128}$/, ['GET']],
  [/^v1\/workflow-setups\/[\w-]{1,128}\/provisioning$/, ['GET', 'POST']],
  [/^v1\/workflow-setups\/[\w-]{1,128}\/provisioning-profiles$/, ['GET']],
  [/^v1\/workflow-provisioning\/[\w-]{1,128}$/, ['GET']],
  [/^v1\/workflow-provisioning\/[\w-]{1,128}\/advance$/, ['POST']],
  [/^v1\/workflow-provisioning\/[\w-]{1,128}\/enrollment$/, ['GET', 'POST']],
  [/^v1\/workflow-enrollments\/[\w-]{1,128}$/, ['GET']],
  [/^v1\/workflow-enrollments\/[\w-]{1,128}\/advance$/, ['POST']],
  [/^v1\/workflow-enrollments\/[\w-]{1,128}\/activation$/, ['GET', 'POST']],
  [/^v1\/workflow-enrollments\/[\w-]{1,128}\/activation-specifications$/, ['GET']],
  [/^v1\/workflow-activations\/[\w-]{1,128}$/, ['GET']],
  [/^v1\/workflow-activations\/[\w-]{1,128}\/revoke$/, ['POST']],
  [/^v1\/runs\/[\w-]{1,128}(?:\/events)?$/, ['GET']],
]
const authCookies = new Set([
  'access_token',
  '__Host-access_token',
  'csrf_token',
  '__Host-csrf_token',
])
const requestHeaders = [
  'authorization',
  'x-csrf-token',
  'origin',
  'content-type',
  'if-match',
  'idempotency-key',
]

class ProxyFailure extends Error {
  readonly status: number
  readonly code: string
  constructor(status: number, code: string) {
    super(code)
    this.status = status
    this.code = code
  }
}

function failure(status: number, code: string) {
  return Response.json({ code }, { status, headers: { 'Cache-Control': 'private, no-store' } })
}

function upstreamOrigin(value: string | undefined) {
  if (!value) throw new ProxyFailure(503, 'enterprise_service_unconfigured')
  const url = new URL(value)
  if (
    !['http:', 'https:'].includes(url.protocol) ||
    !url.hostname ||
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    !['', '/'].includes(url.pathname) ||
    url.port === '0'
  ) {
    throw new ProxyFailure(503, 'enterprise_service_unconfigured')
  }
  return url.origin
}

async function readBounded(
  stream: ReadableStream<Uint8Array> | null,
  limit: number,
  signal: AbortSignal,
  overflowStatus: number,
): Promise<ArrayBuffer> {
  if (!stream) return new ArrayBuffer(0)
  const reader = stream.getReader()
  const chunks: Uint8Array[] = []
  let size = 0
  try {
    while (true) {
      signal.throwIfAborted()
      let abort = () => {}
      const stopped = new Promise<never>((_resolve, reject) => {
        abort = () => reject(new ProxyFailure(504, 'enterprise_proxy_interrupted'))
        signal.addEventListener('abort', abort, { once: true })
      })
      const chunk = await Promise.race([reader.read(), stopped]).finally(() =>
        signal.removeEventListener('abort', abort),
      )
      if (chunk.done) break
      size += chunk.value.byteLength
      if (size > limit) throw new ProxyFailure(overflowStatus, 'enterprise_payload_too_large')
      chunks.push(chunk.value)
    }
    const bytes = new Uint8Array(size)
    let offset = 0
    for (const chunk of chunks) {
      bytes.set(chunk, offset)
      offset += chunk.byteLength
    }
    return bytes.buffer
  } finally {
    void reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}

export async function proxyEnterprise(
  request: Request,
  segments: string[],
  options: ProxyOptions,
): Promise<Response> {
  if (!options.enabled) return failure(404, 'not_found')
  if (segments.some((segment) => !/^[\w-]+$/.test(segment))) return failure(404, 'not_found')
  const route = segments.join('/')
  if (!routes.some(([pattern, methods]) => pattern.test(route) && methods.includes(request.method)))
    return failure(404, 'not_found')
  let origin: string
  try {
    origin = upstreamOrigin(options.upstream)
  } catch {
    return failure(503, 'enterprise_service_unconfigured')
  }
  const writes = !['GET', 'HEAD'].includes(request.method)
  if (writes && !request.headers.get('origin')) return failure(403, 'access_denied')
  if (
    request.body &&
    request.headers.get('content-type')?.split(';')[0]?.trim() !== 'application/json'
  )
    return failure(415, 'enterprise_json_required')
  const headers = new Headers({ Accept: 'application/json' })
  for (const name of requestHeaders) {
    const value = request.headers.get(name)
    if (value) headers.set(name, value)
  }
  const cookies = (request.headers.get('cookie') || '')
    .split(';')
    .map((value) => value.trim())
    .filter((value) => authCookies.has(value.split('=', 1)[0]!))
    .join('; ')
  if (cookies) headers.set('cookie', cookies)
  const signal = AbortSignal.any([request.signal, AbortSignal.timeout(20_000)])
  try {
    const body = request.body
      ? await readBounded(request.body, 1024 * 1024, signal, 413)
      : undefined
    const response = await (options.fetcher ?? fetch)(
      `${origin}/enterprise/api/${route}${new URL(request.url).search}`,
      {
        method: request.method,
        headers,
        body,
        signal,
        redirect: 'manual',
        cache: 'no-store',
      },
    )
    if (response.status >= 300 && response.status < 400) {
      void response.body?.cancel().catch(() => {})
      return failure(502, 'enterprise_upstream_redirect')
    }
    if (response.status === 204)
      return new Response(null, { status: 204, headers: { 'Cache-Control': 'private, no-store' } })
    if (response.headers.get('content-type')?.split(';')[0]?.trim() !== 'application/json') {
      void response.body?.cancel().catch(() => {})
      return failure(502, 'enterprise_upstream_invalid_response')
    }
    const content = await readBounded(response.body, 8 * 1024 * 1024, signal, 502)
    const outgoing = new Headers({
      'Content-Type': 'application/json',
      'Cache-Control': 'private, no-store',
    })
    const etag = response.headers.get('etag')
    if (etag) outgoing.set('etag', etag)
    return new Response(content, { status: response.status, headers: outgoing })
  } catch (error) {
    if (signal.aborted) return failure(504, 'enterprise_proxy_interrupted')
    if (error instanceof ProxyFailure) return failure(error.status, error.code)
    return failure(502, 'enterprise_upstream_unavailable')
  }
}
