import { shallowRef, watch, onScopeDispose } from "vue";
import { api, stream } from "../../api/client";
import { createDeltaReader } from "../../api/sse";
import type { useScopedChatSessions } from "./useScopedChatSessions";
type Sessions = ReturnType<typeof useScopedChatSessions>;

/** 流只绑定发起时的用户、工作区和会话；停止与切换后忽略所有迟到回包。 */
export function useChatConversation(
  sessions: Sessions,
  isChatPage: () => boolean,
  scroll: () => Promise<void>,
) {
  const draft = shallowRef("");
  const sending = shallowRef(false);
  const modelName = shallowRef("");
  const profileReady = shallowRef(false);
  const requestError = shallowRef("");
  const controller = shallowRef<AbortController>();
  let activeRequest: symbol | undefined;
  let savePartial: (() => void) | undefined;
  let profileGeneration = 0;
  function stop() {
    savePartial?.();
    savePartial = undefined;
    activeRequest = undefined;
    controller.value?.abort();
    controller.value = undefined;
    sending.value = false;
  }
  function clear() {
    stop();
    draft.value = "";
    requestError.value = "";
    modelName.value = "";
    profileReady.value = false;
  }
  const stopWatch = watch(
    [sessions.generation, isChatPage],
    async () => {
      clear();
      const generation = ++profileGeneration;
      if (!sessions.key.value || !isChatPage()) return;
      const ticket = sessions.capture();
      if (!sessions.isCurrent(ticket)) return;
      try {
        const profile = await api.get<{ model: string }>("/chat/profile");
        if (
          generation !== profileGeneration ||
          !sessions.isCurrent(ticket) ||
          !isChatPage()
        )
          return;
        modelName.value = profile.model;
        profileReady.value = true;
      } catch (error) {
        if (generation === profileGeneration && sessions.isCurrent(ticket))
          requestError.value =
            error instanceof Error ? error.message : "模型未就绪";
      }
    },
    { immediate: true, flush: "sync" },
  );
  async function send() {
    const text = draft.value.trim();
    if (!text || sending.value || !profileReady.value || !isChatPage()) return;
    const session = sessions.active.value ?? sessions.create();
    if (!session) return;
    const ticket = sessions.capture();
    const request = Symbol("chat-request");
    activeRequest = request;
    const current = () =>
      activeRequest === request && sessions.isCurrent(ticket);
    const abort = new AbortController();
    controller.value = abort;
    sending.value = true;
    requestError.value = "";
    draft.value = "";
    session.turns.push({ role: "user", content: text });
    const reply = { role: "assistant" as const, content: "", reasoning: "" };
    session.turns.push(reply);
    // 使用已进入Vue响应式数组的回合，增量更新可立即反映到界面。
    const target = session.turns[session.turns.length - 1]!;
    sessions.touch(session.id, ticket);
    const reader = createDeltaReader((delta) => {
      if (!current()) return;
      if (delta.content) target.content += delta.content;
      if (delta.reasoning)
        target.reasoning = (target.reasoning ?? "") + delta.reasoning;
    });
    savePartial = () => {
      if (current()) {
        reader.flush();
        sessions.touch(session.id, ticket);
      }
    };
    await scroll();
    if (!current()) return;
    try {
      await stream(
        "/chat/completions",
        {
          session: session.id,
          messages: session.turns
            .filter((turn) => turn.content.trim())
            .map((turn) => ({ role: turn.role, content: turn.content })),
          max_tokens: 1600,
          temperature: 0.3,
        },
        (chunk) => {
          if (current()) {
            reader.push(chunk);
            void scroll();
          }
        },
        abort.signal,
      );
    } catch (error) {
      if (current() && !abort.signal.aborted)
        requestError.value =
          error instanceof Error ? error.message : "本次回答未完成";
    } finally {
      if (current()) {
        reader.flush();
        sessions.touch(session.id, ticket);
        activeRequest = undefined;
        savePartial = undefined;
        sending.value = false;
        controller.value = undefined;
        await scroll();
      }
    }
  }
  onScopeDispose(() => {
    profileGeneration++;
    stopWatch();
    clear();
  });
  return {
    draft,
    sending,
    modelName,
    profileReady,
    requestError,
    send,
    stop,
    clear,
  };
}
