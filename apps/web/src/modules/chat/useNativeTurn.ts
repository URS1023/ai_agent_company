import { computed, shallowRef, type ShallowRef } from "vue";
import { ApiError } from "../../api/client";
import type {
  NativeHistory,
  NativeTurn,
  NativeConversation,
} from "./nativeTypes";
import { emptyHistory, nativePrefix, activeTurnStates } from "./nativeTypes";
import type { useNativeTransport } from "./useNativeTransport";
interface Context {
  detail: ShallowRef<NativeConversation | undefined>;
  history: ShallowRef<NativeHistory>;
  draft: ShallowRef<string>;
  modelId: ShallowRef<string>;
  attachmentIds: ShallowRef<string[]>;
  canWrite: () => boolean;
  fail: (error: unknown) => void;
  refresh: () => Promise<void>;
}
/** 每次发送只POST一次；断流/未知保留原key，恢复只核对原Turn，切换视图不暗中停止原任务。 */
export function useNativeTurn(
  transport: ReturnType<typeof useNativeTransport>,
  context: Context,
) {
  const current = shallowRef<NativeTurn>(),
    posting = shallowRef(false),
    uncertain = shallowRef(false);
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
  async function send() {
    const chat = context.detail.value,
      text = context.draft.value.trim();
    if (
      !context.canWrite() ||
      !chat ||
      chat.status !== "ready" ||
      chat.active_turn_id ||
      !text ||
      !context.modelId.value ||
      busy.value ||
      uncertain.value
    )
      return;
    const selected = context.history.value.currentId,
      parent = selected ? context.history.value.messages[selected] : undefined;
    if (selected && (parent?.role !== "assistant" || parent.done !== true)) {
      context.fail(new Error("请选择已完成的助手消息继续对话"));
      return;
    }
    key = crypto.randomUUID();
    posting.value = true;
    const ticket = generation;
    try {
      const value = await transport.send<NativeTurn>(
        `${nativePrefix}/conversations/${encodeURIComponent(chat.id)}/turns`,
        {
          method: "POST",
          headers: { "Idempotency-Key": key },
          body: JSON.stringify({
            revision: chat.revision,
            model_id: context.modelId.value,
            content: text,
            parent_message_id: selected ?? null,
            attachment_ids: context.attachmentIds.value,
          }),
        },
      );
      if (ticket !== generation) return;
      current.value = value;
      context.draft.value = "";
      context.attachmentIds.value = [];
      context.detail.value = { ...chat, active_turn_id: value.id };
      placeholder(value, text);
      if (["unknown", "failed"].includes(value.status)) {
        uncertain.value = value.status === "unknown";
        context.fail(new Error(value.error ?? "本次回答未完成"));
        if (value.status === "failed") await context.refresh();
      } else if (["succeeded", "cancelled"].includes(value.status)) {
        await context.refresh();
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
      context.fail(error);
    } finally {
      if (ticket === generation) posting.value = false;
    }
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
    busy,
    canStop,
    clear,
    restore,
    send,
    verify,
    stop: () => action("stop"),
    resolve: (call_id: string, approved: boolean) =>
      action("resolutions", {
        call_id,
        action: approved ? "approve" : "reject",
      }),
  };
}
