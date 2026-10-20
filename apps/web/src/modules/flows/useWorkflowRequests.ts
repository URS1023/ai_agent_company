import { onScopeDispose } from "vue";
import { request } from "../../api/client";
/** 流程视图卸载立即关闭所属请求，调用方须在每次await后核active再写状态。 */
export function useWorkflowRequests() {
  let active = true;
  const controllers = new Set<AbortController>();
  async function send<T>(
    path: string,
    options: RequestInit & { timeoutMs?: number } = {},
  ) {
    if (!active) throw new Error("页面已关闭");
    const controller = new AbortController();
    controllers.add(controller);
    try {
      const result = await request<T>(path, {
        ...options,
        signal: controller.signal,
      });
      if (!active) throw "cancel";
      return result;
    } catch (failure) {
      if (!active) throw "cancel";
      throw failure;
    } finally {
      controllers.delete(controller);
    }
  }
  onScopeDispose(() => {
    active = false;
    for (const controller of controllers) controller.abort();
    controllers.clear();
  });
  return { send, active: () => active };
}
