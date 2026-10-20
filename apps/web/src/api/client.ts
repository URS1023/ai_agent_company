/** 同源会话请求：凭据只使用HttpOnly Cookie，写操作带CSRF且不自动重试。 */
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public fields: Record<string, string> = {},
    public traceId?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
let csrf = "";
export function setCsrf(value: string) {
  csrf = value;
}
export interface ListResult<T = Record<string, any>> {
  items: T[];
  total: number;
}
export type Entity = Record<string, any> & {
  id: string;
  revision: number;
  name?: string;
};

export async function request<T = any>(
  path: string,
  options: RequestInit & { asBlob?: boolean } = {},
): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 30000);
  const headers = new Headers(options.headers);
  const method = (options.method ?? "GET").toUpperCase();
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && csrf)
    headers.set("X-CSRF-Token", csrf);
  try {
    const response = await fetch(`/api/v1${path}`, {
      ...options,
      headers,
      credentials: "same-origin",
      signal: options.signal
        ? AbortSignal.any([options.signal, controller.signal])
        : controller.signal,
    });
    if (response.status === 204) return undefined as T;
    if (response.ok && options.asBlob) return (await response.blob()) as T;
    const type = response.headers.get("content-type") ?? "";
    const body = type.includes("json")
      ? await response.json()
      : await response.text();
    if (!response.ok) {
      const error = typeof body === "object" ? (body.error ?? {}) : {};
      const validation = Array.isArray(body?.detail)
        ? Object.fromEntries(
            body.detail.map((x: any) => [x.loc?.at(-1) ?? "form", x.msg]),
          )
        : {};
      throw new ApiError(
        response.status,
        error.code ?? "request_failed",
        error.message ??
          (typeof body?.detail === "string"
            ? body.detail
            : "操作失败，请稍后重试"),
        error.fields ?? validation,
        body?.trace_id,
      );
    }
    return body as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === "AbortError")
      throw new ApiError(408, "timeout", "请求超时，请检查服务状态");
    throw new ApiError(0, "network_error", "无法连接服务，请检查网络连接");
  } finally {
    window.clearTimeout(timeout);
  }
}
export const api = {
  get: <T = any>(p: string) => request<T>(p),
  post: <T = any>(p: string, data: unknown) =>
    request<T>(p, { method: "POST", body: JSON.stringify(data) }),
  postBlob: (p: string, data: unknown) =>
    request<Blob>(p, {
      method: "POST",
      body: JSON.stringify(data),
      asBlob: true,
    }),
  patch: <T = any>(p: string, data: unknown) =>
    request<T>(p, { method: "PATCH", body: JSON.stringify(data) }),
  put: <T = any>(p: string, data: unknown) =>
    request<T>(p, { method: "PUT", body: JSON.stringify(data) }),
  delete: <T = any>(p: string, revision?: number) =>
    request<T>(
      `${p}${revision === undefined ? "" : `${p.includes("?") ? "&" : "?"}revision=${revision}`}`,
      { method: "DELETE" },
    ),
};
