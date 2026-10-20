/** 本机对话按登录用户和工作区分别存取；未标归属的旧记录不自动认领。 */
export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
  reasoning?: string;
}
export interface ChatSession {
  id: string;
  title: string;
  updated: number;
  turns: ChatTurn[];
}
export interface ChatScope {
  userId: string;
  workspaceId: string;
}
export interface ChatStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}
export const MAX_LOCAL_SESSIONS = 50;
const PREFIX = "qknow.chat.sessions.v2:";

export function chatScopeKey(scope: ChatScope | null): string {
  if (!scope?.userId || !scope.workspaceId) return "";
  return (
    PREFIX +
    encodeURIComponent(JSON.stringify([scope.userId, scope.workspaceId]))
  );
}
function validTurn(value: unknown): value is ChatTurn {
  if (!value || typeof value !== "object") return false;
  const turn = value as ChatTurn;
  return (
    ["user", "assistant"].includes(turn.role) &&
    typeof turn.content === "string" &&
    (turn.reasoning === undefined || typeof turn.reasoning === "string")
  );
}
function validSession(value: unknown): value is ChatSession {
  if (!value || typeof value !== "object") return false;
  const item = value as ChatSession;
  return (
    typeof item.id === "string" &&
    Boolean(item.id) &&
    typeof item.title === "string" &&
    Number.isFinite(item.updated) &&
    Array.isArray(item.turns) &&
    item.turns.every(validTurn)
  );
}
function localStore(storage?: ChatStorage): ChatStorage {
  return storage ?? globalThis.localStorage;
}
export function loadSessions(
  scope: ChatScope,
  storage?: ChatStorage,
): ChatSession[] {
  const key = chatScopeKey(scope);
  if (!key) return [];
  try {
    const raw = localStore(storage).getItem(key);
    if (!raw) return [];
    const data = JSON.parse(raw);
    if (
      data.version !== 2 ||
      data.user_id !== scope.userId ||
      data.workspace_id !== scope.workspaceId ||
      !Array.isArray(data.sessions) ||
      !data.sessions.every(validSession)
    )
      throw new Error();
    return data.sessions.slice(0, MAX_LOCAL_SESSIONS);
  } catch {
    throw new Error("无法读取本机对话记录");
  }
}
export function saveSessions(
  scope: ChatScope,
  sessions: ChatSession[],
  storage?: ChatStorage,
): void {
  const key = chatScopeKey(scope);
  if (!key) throw new Error("请先登录并选择工作区");
  try {
    localStore(storage).setItem(
      key,
      JSON.stringify({
        version: 2,
        user_id: scope.userId,
        workspace_id: scope.workspaceId,
        sessions: sessions.slice(0, MAX_LOCAL_SESSIONS),
      }),
    );
  } catch {
    throw new Error("无法保存本机对话，请检查浏览器存储空间");
  }
}
export function newSession(): ChatSession {
  return {
    id: crypto.randomUUID(),
    title: "新对话",
    updated: Date.now(),
    turns: [],
  };
}
export function titleFrom(turns: ChatTurn[]): string {
  const first = turns.find((t) => t.role === "user")?.content.trim() ?? "";
  return first ? first.slice(0, 24) : "新对话";
}
export function searchSessions(
  sessions: ChatSession[],
  text: string,
): ChatSession[] {
  const term = text.trim().toLocaleLowerCase();
  return [...sessions]
    .filter(
      (item) =>
        !term ||
        item.title.toLocaleLowerCase().includes(term) ||
        item.turns.some((turn) =>
          turn.content.toLocaleLowerCase().includes(term),
        ),
    )
    .sort((a, b) => b.updated - a.updated);
}
