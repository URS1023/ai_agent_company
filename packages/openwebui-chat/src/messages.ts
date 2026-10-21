import type { NativeHistory, NativeMessage } from "../public";
/** 原Messages.svelte沿parentId回溯的分支展示，补有限循环保护；不把树写回后端。 */
export function currentMessages(
  history: NativeHistory,
  limit = 1000,
): NativeMessage[] {
  const messages: NativeMessage[] = [],
    seen = new Set<string>();
  let message = history.messages[history.currentId ?? ""];
  while (message && messages.length < limit && !seen.has(message.id)) {
    seen.add(message.id);
    messages.push(message);
    message = history.messages[message.parentId ?? ""];
  }
  return messages.reverse();
}
/** 原getDeepestChildId：分支切换优先显示该兄弟分支的最后叶子。 */
export function deepestChild(history: NativeHistory, id: string): string {
  let current = id;
  const seen = new Set<string>();
  while (!seen.has(current)) {
    seen.add(current);
    const children = history.messages[current]?.childrenIds ?? [];
    const child = children.at(-1);
    if (!child || !history.messages[child]) break;
    current = child;
  }
  return current;
}
export function siblings(
  history: NativeHistory,
  message: NativeMessage,
): string[] {
  return message.parentId
    ? (history.messages[message.parentId]?.childrenIds ?? [])
    : Object.values(history.messages)
        .filter((item) => item.parentId === null)
        .map((item) => item.id);
}
/** 原错误保留可读原因，不把对象、堆栈或未知字段直接铺到消息区。 */
export function messageError(error: unknown): string {
  if (typeof error === "string") return error;
  if (error && typeof error === "object") {
    const record = error as Record<string, unknown>;
    for (const key of ["content", "message", "detail"])
      if (typeof record[key] === "string" && record[key]) return record[key];
  }
  return "本次回答未完成";
}
