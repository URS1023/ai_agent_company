import { computed, onScopeDispose, shallowRef } from "vue";
import { ApiError } from "../../api/client";
import {
  nativePrefix,
  emptyHistory,
  activeTurnStates,
  type NativeConversation,
  type NativeIdentity,
  type NativeTurn,
  type NativeHistory,
  fileView,
} from "./nativeTypes";
import {
  useNativeTransport,
  nativeError,
  accessLost,
} from "./useNativeTransport";
import { useNativeFiles } from "./useNativeFiles";
import { useNativeTurn } from "./useNativeTurn";
/** 平台会话目录与原生详情装配；消息树只从原生读取，浏览器不保存或提交整棵树。 */
export function useNativeChat(
  canWrite: () => boolean,
  canDelete: () => boolean,
) {
  const transport = useNativeTransport(),
    error = shallowRef(""),
    items = shallowRef<NativeConversation[]>([]),
    total = shallowRef(0),
    page = shallowRef(1),
    loading = shallowRef(false),
    busy = shallowRef(false),
    instances = shallowRef<{ id: string; name: string }[]>([]),
    models = shallowRef<{ id: string; name: string }[]>([]),
    modelId = shallowRef(""),
    detail = shallowRef<NativeConversation>(),
    history = shallowRef<NativeHistory>(emptyHistory()),
    draft = shallowRef(""),
    identityReview = shallowRef("");
  let generation = 0,
    directoryGeneration = 0;
  function fail(failure: unknown) {
    if (!transport.active() || failure === "cancel") return;
    error.value = nativeError(failure);
    if (failure instanceof ApiError && failure.fields.identity_id)
      identityReview.value = String(failure.fields.identity_id);
    if (accessLost(failure)) {
      generation++;
      directoryGeneration++;
      items.value = [];
      total.value = 0;
      detail.value = undefined;
      history.value = emptyHistory();
      draft.value = "";
      models.value = [];
      modelId.value = "";
      files.clear();
      turn.clear();
    }
  }
  const files = useNativeFiles(
    transport,
    () => detail.value?.identity_id ?? "",
    canWrite,
    fail,
  );
  const turn = useNativeTurn(transport, {
    detail,
    history,
    draft,
    modelId,
    attachmentIds: files.selected,
    canWrite,
    fail,
    refresh: refreshDetail,
  });
  async function loadDirectory() {
    const ticket = ++directoryGeneration;
    loading.value = true;
    try {
      const value = await transport.send<{
        items: NativeConversation[];
        total: number;
      }>(
        `${nativePrefix}/conversations?limit=20&offset=${(page.value - 1) * 20}`,
      );
      if (ticket !== directoryGeneration) return;
      items.value = value.items;
      total.value = value.total;
    } catch (failure) {
      if (ticket === directoryGeneration) fail(failure);
    } finally {
      if (transport.active() && ticket === directoryGeneration)
        loading.value = false;
    }
  }
  async function initialize(autoOpen = true) {
    try {
      const value = await transport.send<{
        items: { id: string; name: string }[];
      }>(`${nativePrefix}/instances`);
      instances.value = value.items;
      await loadDirectory();
      if (autoOpen && items.value[0]) await open(items.value[0].id);
    } catch (failure) {
      fail(failure);
    }
  }
  async function refreshDetail() {
    const id = detail.value?.id,
      ticket = generation;
    if (!id) return;
    const value = await transport.send<NativeConversation>(
      `${nativePrefix}/conversations/${encodeURIComponent(id)}`,
    );
    if (ticket === generation && detail.value?.id === id) {
      detail.value = value;
      history.value = value.history ?? emptyHistory();
    }
  }
  async function open(id: string) {
    const ticket = ++generation;
    turn.clear();
    files.clear();
    detail.value = undefined;
    history.value = emptyHistory();
    models.value = [];
    modelId.value = "";
    draft.value = "";
    error.value = "";
    loading.value = true;
    try {
      const value = await transport.send<NativeConversation>(
        `${nativePrefix}/conversations/${encodeURIComponent(id)}`,
      );
      if (ticket !== generation) return;
      detail.value = value;
      history.value = value.history ?? emptyHistory();
      const available = await transport.send<{
        items: { id: string; name: string }[];
      }>(
        `${nativePrefix}/identities/${encodeURIComponent(value.identity_id)}/models`,
      );
      if (ticket !== generation) return;
      models.value = available.items;
      if (!models.value.some((item) => item.id === modelId.value))
        modelId.value = models.value[0]?.id ?? "";
      await files.load();
      if (ticket !== generation) return;
      if (value.active_turn_id) await turn.restore(value.active_turn_id);
    } catch (failure) {
      if (ticket === generation) fail(failure);
    } finally {
      if (transport.active() && ticket === generation) loading.value = false;
    }
  }
  async function create(integrationId: string, title: string) {
    if (!canWrite() || busy.value || !title.trim()) return false;
    busy.value = true;
    error.value = "";
    try {
      const identity = await transport.send<NativeIdentity>(
        `${nativePrefix}/identities`,
        {
          method: "POST",
          body: JSON.stringify({ integration_id: integrationId }),
        },
      );
      if (identity.status !== "ready") {
        identityReview.value = identity.id;
        throw new Error(
          identity.status === "auth_required"
            ? "对话身份需要人工核对"
            : "对话身份尚未就绪",
        );
      }
      const value = await transport.send<NativeConversation>(
        `${nativePrefix}/conversations`,
        {
          method: "POST",
          headers: { "Idempotency-Key": crypto.randomUUID() },
          body: JSON.stringify({
            identity_id: identity.id,
            title: title.trim(),
          }),
        },
      );
      await loadDirectory();
      await open(value.id);
      return true;
    } catch (failure) {
      if (failure instanceof ApiError && failure.fields.identity_id)
        identityReview.value = String(failure.fields.identity_id);
      fail(failure);
      return false;
    } finally {
      if (transport.active()) busy.value = false;
    }
  }
  async function reconcileIdentity() {
    if (!canWrite() || !identityReview.value || busy.value || turn.busy.value)
      return;
    busy.value = true;
    try {
      const identity = await transport.send<NativeIdentity>(
        `${nativePrefix}/identities/${encodeURIComponent(identityReview.value)}/reconcile`,
        { method: "POST", body: "{}" },
      );
      if (identity.status === "ready") {
        identityReview.value = "";
        error.value = "";
        if (detail.value) await open(detail.value.id);
      } else fail(new Error("对话身份仍需人工核对"));
    } catch (failure) {
      fail(failure);
    } finally {
      if (transport.active()) busy.value = false;
    }
  }
  async function rename(title: string) {
    if (!canWrite() || !detail.value || busy.value || !title.trim()) return;
    const current = detail.value;
    busy.value = true;
    try {
      const value = await transport.send<NativeConversation>(
        `${nativePrefix}/conversations/${encodeURIComponent(current.id)}`,
        {
          method: "PATCH",
          body: JSON.stringify({
            revision: current.revision,
            title: title.trim(),
          }),
        },
      );
      if (detail.value?.id === current.id)
        detail.value = { ...detail.value, ...value };
      await loadDirectory();
    } catch (failure) {
      fail(failure);
    } finally {
      if (transport.active()) busy.value = false;
    }
  }
  async function remove(id: string) {
    if (!canDelete() || busy.value) return;
    const target =
      detail.value?.id === id
        ? detail.value
        : items.value.find((item) => item.id === id);
    if (!target) return;
    busy.value = true;
    try {
      await transport.send(
        `${nativePrefix}/conversations/${encodeURIComponent(id)}`,
        {
          method: "DELETE",
          body: JSON.stringify({ revision: target.revision }),
        },
      );
      if (detail.value?.id === id) {
        generation++;
        turn.clear();
        files.clear();
        detail.value = undefined;
        history.value = emptyHistory();
        draft.value = "";
      }
      await loadDirectory();
    } catch (failure) {
      fail(failure);
    } finally {
      if (transport.active()) busy.value = false;
    }
  }
  function branch(id: string) {
    if (history.value.messages[id])
      history.value = { ...history.value, currentId: id };
  }
  const messageAttachments = computed(() =>
    Object.fromEntries(
      Object.entries(detail.value?.message_attachments ?? {}).map(
        ([id, values]) => [id, values.map(fileView)],
      ),
    ),
  );
  onScopeDispose(() => {
    generation++;
    turn.clear();
    files.clear();
    detail.value = undefined;
    history.value = emptyHistory();
    draft.value = "";
    models.value = [];
  });
  return {
    transport,
    error,
    items,
    total,
    page,
    loading,
    busy,
    instances,
    models,
    modelId,
    detail,
    history,
    draft,
    identityReview,
    files,
    turn,
    messageAttachments,
    initialize,
    loadDirectory,
    open,
    create,
    rename,
    remove,
    branch,
    reconcileIdentity,
  };
}
