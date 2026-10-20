import { onScopeDispose } from "vue";
import { request } from "../../api/client";

/** 空间或身份变化会卸载对应视图；立即关闭等待且禁止旧响应回写。 */
export function useKnowledgeRequests() {
  let active = true;
  const controllers = new Set<AbortController>();
  async function send<T>(
    path: string,
    options: RequestInit & { asBlob?: boolean; timeoutMs?: number } = {},
  ) {
    if (!active) throw new Error("页面已关闭");
    const controller = new AbortController();
    controllers.add(controller);
    try {
      return await request<T>(path, { ...options, signal: controller.signal });
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
