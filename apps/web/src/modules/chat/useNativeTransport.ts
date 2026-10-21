import { onScopeDispose } from "vue";
import { request, ApiError } from "../../api/client";
/** 单视图拥有请求；切换用户/工作区由外层key卸载，取消并丢弃全部迟到响应。 */
export function useNativeTransport() {
  let active = true;
  const controllers = new Set<AbortController>();
  async function send<T = any>(
    path: string,
    options: RequestInit & { timeoutMs?: number; asBlob?: boolean } = {},
  ): Promise<T> {
    if (!active) throw "cancel";
    const controller = new AbortController();
    controllers.add(controller);
    try {
      const value = await request<T>(path, {
        ...options,
        signal: controller.signal,
      });
      if (!active) throw "cancel";
      return value;
    } catch (error) {
      if (!active) throw "cancel";
      throw error;
    } finally {
      controllers.delete(controller);
    }
  }
  const cancel = () => {
    active = false;
    for (const controller of controllers) controller.abort();
    controllers.clear();
  };
  onScopeDispose(cancel);
  return { send, active: () => active, cancel };
}
export function nativeError(error: unknown): string {
  return error instanceof Error ? error.message : "操作失败，请核对当前状态";
}
export function accessLost(error: unknown): boolean {
  return error instanceof ApiError && [401, 403, 404].includes(error.status);
}
