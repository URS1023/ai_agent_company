import { computed, ref, shallowRef, onScopeDispose } from "vue";
import { ApiError, request } from "../../api/client";
export interface WorkflowRunResult {
  id: string;
  status: string;
  input: string;
  steps: {
    index: number;
    name: string;
    type: string;
    status: string;
    output: string;
    error?: string;
  }[];
  output: string;
  error?: string;
  started_at: string;
  format?: "graph" | "legacy_steps";
  inputs?: Record<string, unknown>;
  outputs?: unknown;
  definition_snapshot?: Record<string, any>;
  workflow_revision?: number;
  graph_progress?: {
    completed_nodes?: number;
    event_count?: number;
    last_event_sequence?: number;
  };
}
interface RunIntent {
  key: string;
  input: string | Record<string, unknown>;
  status: string;
  run: WorkflowRunResult | null;
  error: string;
  fields: Record<string, string>;
}
/** 一次运行意图只POST一次；未知结果只能读取核对，明确的新运行才生成新键。 */
export function useWorkflowRunIntent(getWorkflowId: () => string) {
  const intents = ref<Record<string, RunIntent>>({});
  const running = shallowRef(false);
  const checking = shallowRef(false);
  const permissionError = shallowRef("");
  const current = computed(() => intents.value[getWorkflowId()]);
  const uncertain = computed(() =>
    ["running", "unknown"].includes(current.value?.status ?? ""),
  );
  const currentRun = computed(() => current.value?.run ?? null);
  let disposed = false;
  let generation = 0;
  const controllers = new Set<AbortController>();
  function valid(id: string, key: string) {
    return !disposed && intents.value[id]?.key === key;
  }
  function confirmedShape(result: WorkflowRunResult): WorkflowRunResult {
    if (
      !result ||
      typeof result.id !== "string" ||
      !["running", "unknown", "succeeded", "failed"].includes(result.status) ||
      !Array.isArray(result.steps)
    )
      throw new Error("无法确认运行结果");
    return result;
  }
  function clear() {
    generation++;
    for (const controller of controllers) controller.abort();
    controllers.clear();
    intents.value = {};
    running.value = false;
    checking.value = false;
    permissionError.value = "";
  }
  function denied(failure: unknown) {
    if (!(failure instanceof ApiError) || ![403, 404].includes(failure.status))
      return false;
    clear();
    permissionError.value = failure.message;
    return true;
  }
  async function start(
    input: string | Record<string, unknown>,
    confirmNew = false,
  ): Promise<boolean> {
    const id = getWorkflowId();
    if (
      disposed ||
      !id ||
      running.value ||
      checking.value ||
      (uncertain.value && !confirmNew)
    )
      return false;
    const key = crypto.randomUUID();
    const currentGeneration = ++generation;
    permissionError.value = "";
    intents.value[id] = {
      key,
      input,
      status: "running",
      run: null,
      error: "",
      fields: {},
    };
    const abort = new AbortController();
    controllers.add(abort);
    running.value = true;
    try {
      const result = confirmedShape(
        await request<WorkflowRunResult>(
          `/workflows/${encodeURIComponent(id)}/runs`,
          {
            method: "POST",
            headers: { "Idempotency-Key": key },
            body: JSON.stringify(
              typeof input === "string" ? { input } : { inputs: input },
            ),
            signal: abort.signal,
            timeoutMs: 100000,
          },
        ),
      );
      if (valid(id, key)) {
        intents.value[id]!.run = result;
        intents.value[id]!.status = result.status;
        intents.value[id]!.error = result.error ?? "";
      }
    } catch (failure) {
      if (valid(id, key)) {
        if (denied(failure)) return false;
        const definite =
          failure instanceof ApiError &&
          failure.status >= 400 &&
          failure.status < 500 &&
          failure.status !== 408 &&
          !failure.fields.run_id;
        intents.value[id]!.status = definite ? "failed" : "unknown";
        intents.value[id]!.error =
          failure instanceof Error ? failure.message : "本次运行结果尚未确认";
        if (failure instanceof ApiError)
          intents.value[id]!.fields = failure.fields;
      }
    } finally {
      controllers.delete(abort);
      if (!disposed && generation === currentGeneration) running.value = false;
    }
    return true;
  }
  async function verify() {
    const id = getWorkflowId(),
      intent = current.value;
    if (!intent || running.value || checking.value) return;
    const key = intent.key;
    const currentGeneration = generation;
    const abort = new AbortController();
    controllers.add(abort);
    checking.value = true;
    intent.error = "";
    try {
      const list = await request<{ items: WorkflowRunResult[] }>(
        `/workflows/${encodeURIComponent(id)}/runs?idempotency_key=${encodeURIComponent(key)}`,
        { signal: abort.signal },
      );
      if (!valid(id, key)) return;
      const found = list.items[0];
      if (!found) {
        intent.error = "尚未找到本次运行记录";
        return;
      }
      const result = confirmedShape(
        await request<WorkflowRunResult>(
          `/workflows/runs/${encodeURIComponent(found.id)}`,
          { signal: abort.signal },
        ),
      );
      if (valid(id, key)) {
        intent.run = result;
        intent.status = result.status;
        intent.error = result.error ?? "";
      }
    } catch (failure) {
      if (valid(id, key)) {
        if (denied(failure)) return;
        intent.run = null;
        intent.status = "unknown";
        intent.error =
          failure instanceof Error ? failure.message : "无法核对本次运行";
      }
    } finally {
      controllers.delete(abort);
      if (!disposed && generation === currentGeneration) checking.value = false;
    }
  }
  onScopeDispose(() => {
    disposed = true;
    clear();
  });
  return {
    current,
    currentRun,
    uncertain,
    running,
    checking,
    permissionError,
    start,
    verify,
    clear,
  };
}
