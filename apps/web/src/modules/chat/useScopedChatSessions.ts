import { computed, ref, shallowRef, watch, onScopeDispose } from "vue";
import {
  chatScopeKey,
  loadSessions,
  saveSessions,
  newSession,
  titleFrom,
  MAX_LOCAL_SESSIONS,
  type ChatScope,
  type ChatSession,
  type ChatStorage,
} from "./chatStore";
export interface ChatScopeTicket {
  key: string;
  generation: number;
}

/** 同步切换归属并作废旧世代，防止迟到的分片或finally将旧会话写给新用户。 */
export function useScopedChatSessions(
  getScope: () => ChatScope | null,
  onChange: () => void = () => {},
  storage?: ChatStorage,
) {
  const sessions = ref<ChatSession[]>([]);
  const activeId = shallowRef("");
  const storageError = shallowRef("");
  const key = computed(() => chatScopeKey(getScope()));
  const generation = shallowRef(0);
  let disposed = false;
  let readFailed = false;
  const active = computed(() =>
    sessions.value.find((item) => item.id === activeId.value),
  );
  const turns = computed(() => active.value?.turns ?? []);
  function capture(): ChatScopeTicket {
    return { key: key.value, generation: generation.value };
  }
  function isCurrent(ticket: ChatScopeTicket): boolean {
    return (
      !disposed &&
      Boolean(ticket.key) &&
      ticket.key === key.value &&
      ticket.generation === generation.value
    );
  }
  function reload() {
    generation.value++;
    onChange();
    sessions.value = [];
    activeId.value = "";
    storageError.value = "";
    readFailed = false;
    const scope = getScope();
    if (!scope) return;
    try {
      sessions.value = loadSessions(scope, storage);
      activeId.value = sessions.value[0]?.id ?? "";
    } catch (error) {
      readFailed = true;
      storageError.value = (error as Error).message;
    }
  }
  const stopWatch = watch(key, reload, { immediate: true, flush: "sync" });
  function persist(ticket = capture()): boolean {
    if (!isCurrent(ticket)) return false;
    if (readFailed) return false;
    const scope = getScope();
    if (!scope) return false;
    try {
      saveSessions(scope, sessions.value, storage);
      storageError.value = "";
      return true;
    } catch (error) {
      storageError.value = (error as Error).message;
      return false;
    }
  }
  function create(): ChatSession | undefined {
    if (!key.value || readFailed) return;
    if (sessions.value.length >= MAX_LOCAL_SESSIONS) {
      storageError.value = "本机对话已达50条，请先删除不需要的记录";
      return;
    }
    const session = newSession();
    const previous = sessions.value;
    sessions.value = [session, ...previous];
    if (!persist()) {
      sessions.value = previous;
      return;
    }
    activeId.value = session.id;
    return sessions.value[0];
  }
  function select(id: string): boolean {
    if (!sessions.value.some((item) => item.id === id)) return false;
    activeId.value = id;
    return true;
  }
  function remove(id: string): boolean {
    const previous = sessions.value;
    sessions.value = previous.filter((item) => item.id !== id);
    if (!persist()) {
      sessions.value = previous;
      return false;
    }
    if (activeId.value === id) activeId.value = sessions.value[0]?.id ?? "";
    return true;
  }
  function touch(id: string, ticket = capture()): boolean {
    if (!isCurrent(ticket)) return false;
    const session = sessions.value.find((item) => item.id === id);
    if (!session) return false;
    session.updated = Date.now();
    session.title = titleFrom(session.turns);
    return persist(ticket);
  }
  function clearSession(id: string): boolean {
    const previous = sessions.value;
    sessions.value = previous.map((item) =>
      item.id === id ? { ...newSession(), id } : item,
    );
    if (!persist()) {
      sessions.value = previous;
      return false;
    }
    return true;
  }
  onScopeDispose(() => {
    disposed = true;
    generation.value++;
    stopWatch();
    onChange();
    sessions.value = [];
    activeId.value = "";
  });
  return {
    sessions,
    activeId,
    active,
    turns,
    storageError,
    key,
    generation,
    capture,
    isCurrent,
    reload,
    persist,
    create,
    select,
    remove,
    touch,
    clearSession,
  };
}
