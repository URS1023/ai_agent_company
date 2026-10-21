import { computed, shallowRef, type ShallowRef } from "vue";
import { ApiError } from "../../api/client";
import type {
  NativeHistory,
  NativeTurn,
  NativeConversation,
} from "./nativeTypes";
import { emptyHistory, nativePrefix, activeTurnStates } from "./nativeTypes";
import type { useNativeTransport } from "./useNativeTransport";
import type {
  FileView,
  MessageEditDraft,
  MessageEditPatch,
} from "@openwebui-chat";
import { editPayload, editedUserSaved } from "./nativeBranchDraft";
interface Context {
  detail: ShallowRef<NativeConversation | undefined>;
  history: ShallowRef<NativeHistory>;
  draft: ShallowRef<string>;
  modelId: ShallowRef<string>;
  attachmentIds: ShallowRef<string[]>;
  canWrite: () => boolean;
  fail: (error: unknown) => void;
  refresh: () => Promise<void>;
  refreshSources?: () => Promise<void>;
  files?: () => FileView[];
  attachments?: () => Record<string, FileView[]>;
}
/** 每次发送只POST一次；断流/未知保留原key，恢复只核对原Turn，切换视图不暗中停止原任务。 */
export function useNativeTurn(
  transport: ReturnType<typeof useNativeTransport>,
  context: Context,
) {
  const current = shallowRef<NativeTurn>(),
    posting = shallowRef(false),
    uncertain = shallowRef(false),
    editing = shallowRef<MessageEditDraft>();
  let submittedEdit: { sourceId: string; content: string } | undefined;
  let key = "",
    generation = 0,
    streamGeneration = 0,
    eventCursor = 0,
    eventTurnId = "",
    controller: AbortController | undefined;
  const busy = computed(
      () =>
        posting.value ||
        (!!current.value && activeTurnStates.has(current.value.status)),
    ),
    canStop = computed(
      () =>
        !uncertain.value &&
        !!current.value &&
        ["reserved", "running", "waiting_approval"].includes(
          current.value.status,
        ),
    );
  function clear() {
    generation++;
    streamGeneration++;
    eventCursor = 0;
    eventTurnId = "";
    controller?.abort();
    controller = undefined;
    current.value = undefined;
    posting.value = false;
    uncertain.value = false;
    key = "";
    editing.value = undefined;
    submittedEdit = undefined;
  }
  function placeholder(turn: NativeTurn, text: string) {
    const history = structuredClone(context.history.value),
      parent = turn.parent_message_id;
    history.messages[turn.user_message_id] ??= {
      id: turn.user_message_id,
      parentId: parent,
      childrenIds: [turn.assistant_message_id],
      role: "user",
      content: text,
    };
    history.messages[turn.assistant_message_id] ??= {
      id: turn.assistant_message_id,
      parentId: turn.user_message_id,
      childrenIds: [],
      role: "assistant",
      content: "",
      model: turn.model_id,
      done: false,
    };
    if (parent && history.messages[parent])
      history.messages[parent].childrenIds = [
        ...new Set([
          ...(history.messages[parent].childrenIds ?? []),
          turn.user_message_id,
        ]),
      ];
    history.currentId = turn.assistant_message_id;
    context.history.value = history;
  }
  function noteFailure(value: NativeTurn) {
    if (["failed", "unknown"].includes(value.status))
      context.fail(
        new Error(
          value.error ??
            (value.status === "unknown"
              ? "本次运行需要核对原结果"
              : "本次回答未完成"),
        ),
      );
  }
  function observeSavedEdit() {
    const turn = current.value;
    if (
      submittedEdit &&
      turn &&
      turn.idempotency_key === key &&
      editedUserSaved(
        context.history.value,
        turn,
        submittedEdit.sourceId,
        submittedEdit.content,
      )
    ) {
      editing.value = undefined;
      submittedEdit = undefined;
    }
  }
  async function authoritative(ticket: number, streamTicket?: number) {
    if (!current.value) return;
    const value = await transport.send<NativeTurn>(
      `${nativePrefix}/turns/${encodeURIComponent(current.value.id)}`,
    );
    if (
      ticket !== generation ||
      (streamTicket !== undefined && streamTicket !== streamGeneration)
    )
      return;
    current.value = value;
    uncertain.value = value.status === "unknown";
    await context.refresh();
    observeSavedEdit();
    if (
      ticket === generation &&
      (streamTicket === undefined || streamTicket === streamGeneration)
    )
      noteFailure(value);
  }
  async function subscribe(turn: NativeTurn) {
    const ticket = generation;
    const streamTicket = ++streamGeneration;
    controller?.abort();
    const abort = new AbortController();
    controller = abort;
    if (eventTurnId !== turn.id) {
      eventTurnId = turn.id;
      eventCursor = 0;
    }
    const fromCursor = eventCursor;
    let rebuilt = fromCursor > 0;
    let missingTreeChecked = false;
    let sourcesRead = false;
    const valid = () =>
      ticket === generation &&
      streamTicket === streamGeneration &&
      transport.active();
    const timeout = setTimeout(() => abort.abort(), 100000);
    try {
      const response = await fetch(
        `/api/v1${nativePrefix}/turns/${encodeURIComponent(turn.id)}/events?after=${fromCursor}`,
        {
          credentials: "same-origin",
          signal: abort.signal,
          headers: { Accept: "text/event-stream" },
        },
      );
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new ApiError(
          response.status,
          body.error?.code ?? "native_event_failed",
          body.error?.message ?? "无法读取对话事件",
        );
      }
      if (!response.body) throw new Error("对话事件连接未就绪");
      const library = await import("@openwebui-chat");
      for await (const frame of library.readNativeEvents(response.body)) {
        if (!valid()) return;
        if (frame.event_type === "access_revoked") {
          context.fail(
            new ApiError(
              403,
              "native_chat_access_revoked",
              "当前对话来源已无权访问",
            ),
          );
          return;
        }
        if (frame.event_type === "reconcile_required") {
          uncertain.value = true;
          context.fail(new Error("实时记录已中断，请核对原结果"));
          return;
        }
        if (frame.sequence <= eventCursor) continue;
        eventCursor = frame.sequence;
        if (frame.event_type === "state") {
          if (current.value)
            current.value = { ...current.value, status: frame.data.status };
        } else if (frame.event_type === "attachments_updated")
          await context.refresh();
        else {
          // 202只预约；分支节点按原chat:messages或一次准确读取恢复，不能改写原消息冒充保存。
          if (
            frame.event_type !== "chat:messages" &&
            !context.history.value.messages[turn.assistant_message_id]
          ) {
            if (!missingTreeChecked) {
              missingTreeChecked = true;
              await context.refresh();
              if (!valid()) return;
            }
            if (!context.history.value.messages[turn.assistant_message_id]) {
              uncertain.value = true;
              context.fail(new Error("分支消息尚未确认，请核对原结果"));
              return;
            }
          }
          // 从零回放缓存时先清理该助手的已保存片段，避免重开进行中的会话重复拼接正文。
          if (!rebuilt) {
            const history = structuredClone(context.history.value);
            const message = history.messages[turn.assistant_message_id];
            if (message) {
              message.content = "";
              message.output = [];
              context.history.value = history;
            }
            rebuilt = true;
          }
          context.history.value = library.reduceNativeFrame(
            context.history.value,
            turn.assistant_message_id,
            frame,
          );
          observeSavedEdit();
          if (frame.event_type === "chat:messages" && !sourcesRead) {
            sourcesRead = true;
            await context.refreshSources?.();
            if (!valid()) return;
          }
        }
      }
      if (valid()) await authoritative(ticket, streamTicket);
    } catch (error) {
      if (valid()) {
        uncertain.value = true;
        context.fail(
          error instanceof DOMException && error.name === "AbortError"
            ? new Error("实时连接已中断，请核对原结果")
            : error,
        );
      }
    } finally {
      clearTimeout(timeout);
      if (controller === abort) controller = undefined;
    }
  }
  async function restore(id: string) {
    const ticket = generation;
    const value = await transport.send<NativeTurn>(
      `${nativePrefix}/turns/${encodeURIComponent(id)}`,
    );
    if (ticket !== generation) return;
    current.value = value;
    key = value.idempotency_key;
    uncertain.value = value.status === "unknown";
    noteFailure(value);
    if (["reserved", "running", "cancel_requested"].includes(value.status))
      void subscribe(value);
  }
  function idle() {
    const chat = context.detail.value;
    return (
      context.canWrite() &&
      !!chat &&
      chat.status === "ready" &&
      !chat.active_turn_id &&
      !busy.value &&
      !uncertain.value
    );
  }
  function beginEdit(sourceId: string) {
    const source = context.history.value.messages[sourceId];
    if (!idle() || editing.value || source?.role !== "user") return;
    editing.value = {
      sourceMessageId: sourceId,
      content: source.content,
      attachmentMode: "inherit",
      attachmentIds: [],
      inheritedAttachments: structuredClone(
        context.attachments?.()[sourceId] ?? [],
      ),
    };
  }
  function updateEdit(patch: MessageEditPatch) {
    if (!editing.value || busy.value || uncertain.value) return;
    editing.value = { ...editing.value, ...patch, errors: {} };
  }
  function cancelEdit() {
    if (busy.value || uncertain.value) return;
    editing.value = undefined;
    submittedEdit = undefined;
  }
  async function reserveIntent(
    path: string,
    body: Record<string, unknown>,
    text?: string,
  ) {
    const chat = context.detail.value;
    if (!chat) return;
    key = crypto.randomUUID();
    current.value = undefined;
    streamGeneration++;
    controller?.abort();
    controller = undefined;
    posting.value = true;
    const ticket = generation;
    try {
      const value = await transport.send<NativeTurn>(path, {
        method: "POST",
        headers: { "Idempotency-Key": key },
        body: JSON.stringify(body),
      });
      if (ticket !== generation) return;
      current.value = value;
      context.detail.value = { ...chat, active_turn_id: value.id };
      if (text !== undefined) {
        context.draft.value = "";
        context.attachmentIds.value = [];
        placeholder(value, text);
      } else {
        await context.refresh();
        if (ticket !== generation) return;
        observeSavedEdit();
      }
      if (["unknown", "failed"].includes(value.status)) {
        uncertain.value = value.status === "unknown";
        noteFailure(value);
        if (value.status === "failed" && text !== undefined)
          await context.refresh();
      } else if (["succeeded", "cancelled"].includes(value.status)) {
        if (text !== undefined) await context.refresh();
      } else void subscribe(value);
    } catch (error) {
      if (ticket !== generation) return;
      if (
        error instanceof ApiError &&
        [0, 408, 502, 503, 504].includes(error.status)
      ) {
        uncertain.value = true;
        if (error.fields.turn_id)
          current.value = {
            id: String(error.fields.turn_id),
            conversation_id: chat.id,
            status: "unknown",
            idempotency_key: key,
          } as NativeTurn;
      }
      if (error instanceof ApiError && editing.value) {
        const errors: Record<string, string> = {};
        for (const name of ["content", "attachment_ids"]) {
          const field = Object.entries(error.fields).find(
            ([path]) => path === name || path.startsWith(`${name}.`),
          );
          if (field) errors[name] = field[1];
        }
        editing.value = { ...editing.value, errors };
        if (
          ["native_chat_edit_conflict", "revision_conflict"].includes(
            error.code,
          )
        ) {
          try {
            await context.refresh();
          } catch (refreshError) {
            context.fail(refreshError);
            return;
          }
          if (ticket !== generation) return;
        }
      }
      context.fail(error);
    } finally {
      if (ticket === generation) posting.value = false;
    }
  }
  async function send() {
    const chat = context.detail.value,
      text = context.draft.value.trim();
    if (!idle() || !chat || editing.value || !text || !context.modelId.value)
      return;
    const selected = context.history.value.currentId,
      parent = selected ? context.history.value.messages[selected] : undefined;
    if (selected && (parent?.role !== "assistant" || parent.done !== true)) {
      context.fail(new Error("请选择已完成的助手消息继续对话"));
      return;
    }
    await reserveIntent(
      `${nativePrefix}/conversations/${encodeURIComponent(chat.id)}/turns`,
      {
        revision: chat.revision,
        model_id: context.modelId.value,
        content: text,
        parent_message_id: selected ?? null,
        attachment_ids: context.attachmentIds.value,
      },
      text,
    );
  }
  async function regenerate(sourceId: string) {
    const chat = context.detail.value,
      source = context.history.value.messages[sourceId];
    if (
      !idle() ||
      !chat ||
      editing.value ||
      !context.modelId.value ||
      source?.role !== "assistant" ||
      source.done !== true
    )
      return;
    await reserveIntent(
      `${nativePrefix}/conversations/${encodeURIComponent(chat.id)}/messages/${encodeURIComponent(sourceId)}/regenerations`,
      {
        revision: chat.revision,
        model_id: context.modelId.value,
      },
    );
  }
  async function editAndSend() {
    const chat = context.detail.value,
      draft = editing.value;
    if (!idle() || !chat || !draft || !context.modelId.value) return;
    const { payload, errors } = editPayload(draft, context.files?.() ?? []);
    editing.value = { ...draft, errors };
    if (Object.keys(errors).length) return;
    submittedEdit = {
      sourceId: draft.sourceMessageId,
      content: payload.content,
    };
    await reserveIntent(
      `${nativePrefix}/conversations/${encodeURIComponent(chat.id)}/messages/${encodeURIComponent(draft.sourceMessageId)}/edit-and-send`,
      {
        revision: chat.revision,
        model_id: context.modelId.value,
        ...payload,
      },
    );
  }
  async function verify() {
    const chat = context.detail.value;
    if (!chat || posting.value) return;
    posting.value = true;
    const ticket = generation;
    try {
      if (!current.value && key) {
        const list = await transport.send<{ items: NativeTurn[] }>(
          `${nativePrefix}/conversations/${encodeURIComponent(chat.id)}/turns?idempotency_key=${encodeURIComponent(key)}`,
        );
        if (ticket !== generation) return;
        current.value = list.items[0];
      }
      if (!current.value) {
        await context.refresh();
        throw new Error("尚未确认本次运行，请保留当前消息并核对原结果");
      }
      const value = context.canWrite()
        ? await transport.send<NativeTurn>(
            `${nativePrefix}/turns/${encodeURIComponent(current.value.id)}/reconcile`,
            { method: "POST", body: "{}" },
          )
        : await transport.send<NativeTurn>(
            `${nativePrefix}/turns/${encodeURIComponent(current.value.id)}`,
          );
      if (ticket !== generation) return;
      current.value = value;
      uncertain.value = value.status === "unknown";
      await context.refresh();
      if (ticket !== generation) return;
      observeSavedEdit();
      noteFailure(value);
      if (["running", "reserved", "cancel_requested"].includes(value.status))
        void subscribe(value);
    } catch (error) {
      if (ticket === generation) context.fail(error);
    } finally {
      if (ticket === generation) posting.value = false;
    }
  }
  async function action(
    kind: "stop" | "resolutions",
    payload: Record<string, unknown> = {},
  ) {
    const turn = current.value;
    if (
      !turn ||
      !context.canWrite() ||
      posting.value ||
      uncertain.value ||
      (kind === "stop" && !canStop.value)
    )
      return;
    posting.value = true;
    const ticket = generation;
    try {
      const result = await transport.send<{ turn: NativeTurn }>(
        `${nativePrefix}/turns/${encodeURIComponent(turn.id)}/${kind}`,
        {
          method: "POST",
          headers: { "Idempotency-Key": crypto.randomUUID() },
          body: JSON.stringify(payload),
        },
      );
      if (ticket !== generation) return;
      current.value = result.turn;
      uncertain.value = result.turn.status === "unknown";
      await context.refresh();
      if (ticket !== generation) return;
      observeSavedEdit();
      noteFailure(result.turn);
      if (
        ["reserved", "running", "cancel_requested"].includes(result.turn.status)
      )
        void subscribe(result.turn);
    } catch (error) {
      if (ticket === generation) {
        uncertain.value = true;
        context.fail(error);
      }
    } finally {
      if (ticket === generation) posting.value = false;
    }
  }
  return {
    current,
    posting,
    uncertain,
    editing,
    busy,
    canStop,
    clear,
    restore,
    send,
    beginEdit,
    updateEdit,
    cancelEdit,
    regenerate,
    editAndSend,
    verify,
    stop: () => action("stop"),
    resolve: (call_id: string, approved: boolean) =>
      action("resolutions", {
        call_id,
        action: approved ? "approve" : "reject",
      }),
  };
}
