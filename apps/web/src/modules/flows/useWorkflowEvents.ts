import { onScopeDispose, ref, shallowRef, watch } from "vue";
import { ApiError } from "../../api/client";
import { useWorkflowRequests } from "./useWorkflowRequests";
export interface WorkflowEvent {
  cursor: number;
  run_id: string;
  event_sequence: number;
  event_type: string;
  data: Record<string, any>;
  created_at: string;
}
/** cursor属于持久事件账本；只在打开记录或用户点击时加载，不轮询执行器。 */
export function useWorkflowEvents(
  getRunId: () => string,
  denied: (message: string) => void,
) {
  const transport = useWorkflowRequests();
  const items = ref<WorkflowEvent[]>([]),
    cursor = shallowRef(0),
    hasMore = shallowRef(false),
    loading = shallowRef(false),
    error = shallowRef("");
  let generation = 0;
  async function load() {
    const runId = getRunId();
    if (!runId || !transport.active() || loading.value) return;
    const ticket = generation,
      after = cursor.value;
    loading.value = true;
    error.value = "";
    try {
      const result = await transport.send<{
        items: WorkflowEvent[];
        next_cursor: number;
        has_more: boolean;
      }>(
        `/workflows/runs/${encodeURIComponent(runId)}/events?after=${after}&limit=100`,
      );
      if (!transport.active() || ticket !== generation || runId !== getRunId())
        return;
      if (
        result.items.some(
          (item) =>
            item.run_id !== runId ||
            !Number.isInteger(item.cursor) ||
            item.cursor <= after,
        ) ||
        !Number.isInteger(result.next_cursor) ||
        result.next_cursor < after ||
        (result.has_more && result.next_cursor === after)
      )
        throw new Error("事件分页未能确认，请重新打开记录");
      const seen = new Set(items.value.map((item) => item.cursor));
      items.value = [
        ...items.value,
        ...result.items.filter((item) => !seen.has(item.cursor)),
      ];
      cursor.value = result.next_cursor;
      hasMore.value = result.has_more;
    } catch (failure) {
      if (!transport.active() || ticket !== generation) return;
      error.value =
        failure instanceof Error ? failure.message : "无法读取运行事件";
      if (
        failure instanceof ApiError &&
        [401, 403, 404].includes(failure.status)
      ) {
        items.value = [];
        cursor.value = 0;
        hasMore.value = false;
        denied(error.value);
      }
    } finally {
      if (transport.active() && ticket === generation) loading.value = false;
    }
  }
  function clear() {
    generation++;
    items.value = [];
    cursor.value = 0;
    hasMore.value = false;
    loading.value = false;
    error.value = "";
  }
  watch(
    getRunId,
    () => {
      clear();
      if (getRunId()) void load();
    },
    { immediate: true, flush: "sync" },
  );
  onScopeDispose(clear);
  return { items, cursor, hasMore, loading, error, load, clear };
}
