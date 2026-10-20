import {
  computed,
  onScopeDispose,
  reactive,
  ref,
  shallowRef,
  watch,
} from "vue";
import { ApiError, request, type ListResult } from "../../api/client";
import type { ChatScope } from "./chatStore";

export type MemoryType = "user" | "context";
export interface MemoryRecord {
  id: string;
  workspace_id: string;
  user_id: string;
  content: string;
  type: MemoryType;
  revision: number;
  created_at: string;
  updated_at: string;
}
export interface MemoryDraft {
  content: string;
  type: MemoryType;
}
export const memoryTypes: { value: MemoryType; label: string }[] = [
  { value: "user", label: "个人偏好" },
  { value: "context", label: "背景资料" },
];

/** 个人记忆只消费管理接口；身份世代变化会清空内容并作废所有迟到响应。 */
export function useMemories(
  getScope: () => ChatScope | null,
  can: (permission: string) => boolean,
) {
  const scopeKey = computed(() => {
    const scope = getScope();
    return scope?.userId && scope.workspaceId
      ? JSON.stringify([scope.userId, scope.workspaceId])
      : "";
  });
  const canRead = computed(() => Boolean(scopeKey.value) && can("m02.read"));
  const canWrite = computed(() => canRead.value && can("m02.write"));
  const canDelete = computed(() => canRead.value && can("m02.delete"));
  const query = shallowRef("");
  const type = shallowRef<MemoryType | "">("");
  const page = shallowRef(1);
  const size = 20;
  const total = shallowRef(0);
  const items = ref<MemoryRecord[]>([]);
  const loading = shallowRef(false);
  const reading = shallowRef(false);
  const saving = shallowRef(false);
  const error = shallowRef("");
  const editorError = shallowRef("");
  const fields = ref<Record<string, string>>({});
  const visible = shallowRef(false);
  const editing = ref<MemoryRecord | null>(null);
  const conflict = shallowRef(false);
  const uncertain = shallowRef(false);
  const draft = reactive<MemoryDraft>({ content: "", type: "user" });
  const controllers = new Set<AbortController>();
  let generation = 0,
    listSequence = 0,
    editorSequence = 0,
    disposed = false;
  let listController: AbortController | undefined;
  const capture = () => generation;
  const isCurrent = (ticket: number) =>
    !disposed && Boolean(scopeKey.value) && ticket === generation;
  async function send<T>(path: string, options: RequestInit = {}) {
    const controller = new AbortController();
    controllers.add(controller);
    try {
      return await request<T>(path, { ...options, signal: controller.signal });
    } finally {
      controllers.delete(controller);
    }
  }
  function clearEditor() {
    editorSequence++;
    visible.value = false;
    editing.value = null;
    draft.content = "";
    draft.type = "user";
    editorError.value = "";
    fields.value = {};
    conflict.value = false;
    uncertain.value = false;
    reading.value = false;
  }
  function clearFacts() {
    generation++;
    listSequence++;
    // 中止等待不等于撤销服务端写入；旧身份的结果仍须靠世代检查隔离。
    for (const controller of controllers) controller.abort();
    controllers.clear();
    listController?.abort();
    items.value = [];
    total.value = 0;
    loading.value = false;
    saving.value = false;
    clearEditor();
  }
  function denied(failure: unknown) {
    if (failure instanceof ApiError && [403, 404].includes(failure.status)) {
      clearFacts();
      error.value = failure.message;
      return true;
    }
    return false;
  }
  const message = (failure: unknown) =>
    failure instanceof Error ? failure.message : "操作失败";
  async function load() {
    if (!canRead.value) return;
    const ticket = capture(),
      sequence = ++listSequence;
    listController?.abort();
    const controller = new AbortController();
    listController = controller;
    loading.value = true;
    error.value = "";
    items.value = [];
    total.value = 0;
    const parameters = new URLSearchParams({
      q: query.value.trim(),
      limit: String(size),
      offset: String((page.value - 1) * size),
    });
    if (type.value) parameters.set("type", type.value);
    try {
      const result = await request<ListResult<MemoryRecord>>(
        `/chat/memories?${parameters}`,
        { signal: controller.signal },
      );
      if (isCurrent(ticket) && sequence === listSequence) {
        items.value = result.items;
        total.value = result.total;
      }
    } catch (failure) {
      if (isCurrent(ticket) && sequence === listSequence && !denied(failure))
        error.value = message(failure);
    } finally {
      if (isCurrent(ticket) && sequence === listSequence) loading.value = false;
    }
  }
  async function search() {
    page.value = 1;
    await load();
  }
  async function changePage(value: number) {
    if (loading.value || error.value || value === page.value || value < 1)
      return;
    page.value = value;
    await load();
  }
  function create() {
    if (!canWrite.value || saving.value) return;
    clearEditor();
    visible.value = true;
  }
  async function edit(id: string) {
    if (!canRead.value || saving.value) return;
    clearEditor();
    const ticket = capture(),
      sequence = ++editorSequence;
    reading.value = true;
    error.value = "";
    try {
      const record = await send<MemoryRecord>(
        `/chat/memories/${encodeURIComponent(id)}`,
      );
      if (!isCurrent(ticket) || sequence !== editorSequence) return;
      editing.value = record;
      draft.content = record.content;
      draft.type = record.type;
      visible.value = true;
    } catch (failure) {
      if (isCurrent(ticket) && sequence === editorSequence && !denied(failure))
        error.value = message(failure);
    } finally {
      if (isCurrent(ticket) && sequence === editorSequence)
        reading.value = false;
    }
  }
  function close() {
    if (!saving.value) clearEditor();
  }
  async function save() {
    if (
      !canWrite.value ||
      !visible.value ||
      saving.value ||
      conflict.value ||
      uncertain.value
    )
      return false;
    fields.value = {};
    const content = draft.content.trim();
    if (!content || content.length > 6000) {
      fields.value = { content: content ? "内容不能超过6000字" : "请输入内容" };
      return false;
    }
    const ticket = capture();
    saving.value = true;
    editorError.value = "";
    const record = editing.value;
    const path = record
      ? `/chat/memories/${encodeURIComponent(record.id)}`
      : "/chat/memories";
    try {
      await send<MemoryRecord>(path, {
        method: record ? "PATCH" : "POST",
        body: JSON.stringify({
          content,
          type: draft.type,
          ...(record ? { revision: record.revision } : {}),
        }),
      });
      if (!isCurrent(ticket)) return false;
      clearEditor();
      await load();
      return isCurrent(ticket);
    } catch (failure) {
      if (!isCurrent(ticket) || denied(failure)) return false;
      editorError.value = message(failure);
      if (failure instanceof ApiError) {
        fields.value = failure.fields;
        conflict.value = failure.status === 409;
        // 写入可能已经完成，核对前禁用再次保存，避免网络异常造成重复新增。
        uncertain.value =
          [0, 408].includes(failure.status) || failure.status >= 500;
        if (uncertain.value)
          editorError.value = "保存结果待核对，请返回列表确认";
      }
      return false;
    } finally {
      if (isCurrent(ticket)) saving.value = false;
    }
  }
  async function remove(record: MemoryRecord, ticket = capture()) {
    if (!isCurrent(ticket) || !canDelete.value || saving.value) return;
    saving.value = true;
    error.value = "";
    try {
      await send(
        `/chat/memories/${encodeURIComponent(record.id)}?revision=${record.revision}`,
        { method: "DELETE" },
      );
      if (!isCurrent(ticket)) return;
      if (items.value.length === 1 && page.value > 1) page.value--;
      await load();
    } catch (failure) {
      if (isCurrent(ticket) && !denied(failure)) error.value = message(failure);
    } finally {
      if (isCurrent(ticket)) saving.value = false;
    }
  }
  async function checkList() {
    close();
    await load();
  }
  watch(
    [scopeKey, canRead, canWrite, canDelete],
    () => {
      clearFacts();
      query.value = "";
      type.value = "";
      page.value = 1;
      error.value = "";
      if (canRead.value) void load();
    },
    { immediate: true, flush: "sync" },
  );
  onScopeDispose(() => {
    disposed = true;
    clearFacts();
  });
  return {
    items,
    total,
    query,
    type,
    page,
    size,
    loading,
    reading,
    saving,
    error,
    editorError,
    fields,
    visible,
    editing,
    conflict,
    uncertain,
    draft,
    canRead,
    canWrite,
    canDelete,
    capture,
    isCurrent,
    load,
    search,
    changePage,
    create,
    edit,
    close,
    save,
    remove,
    checkList,
  };
}
