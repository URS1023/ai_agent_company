import type {
  FileView,
  MessageEditDraft,
  NativeHistory,
} from "@openwebui-chat";
import type { NativeTurn } from "./nativeTypes";
/** 编辑附件只使用本条历史映射和显式替换，绝不读取普通输入框的附件选择。 */
export function editPayload(draft: MessageEditDraft, readyFiles: FileView[]) {
  const errors: Record<string, string> = {};
  if (!draft.content.trim()) errors.content = "请输入消息内容";
  else if (draft.content.length > 40000) errors.content = "消息内容最多40000字";
  const payload: { content: string; attachment_ids?: string[] } = {
    content: draft.content,
  };
  if (draft.attachmentMode === "remove") payload.attachment_ids = [];
  else if (draft.attachmentMode === "replace") {
    const ids = [...new Set(draft.attachmentIds)];
    if (
      !ids.length ||
      ids.length > 10 ||
      ids.some(
        (id) =>
          !readyFiles.some((file) => file.id === id && file.status === "ready"),
      )
    )
      errors.attachment_ids = "请选择1至10个已就绪附件";
    else payload.attachment_ids = ids;
  } else if (draft.attachmentMode !== "inherit")
    errors.attachment_ids = "请选择附件处理方式";
  return { payload, errors };
}
/** 只有准确的新原生用户/助手及正文父节点已落树，才释放本次编辑草稿；模型失败不抹去已保存用户消息。 */
export function editedUserSaved(
  history: NativeHistory,
  turn: NativeTurn,
  sourceId: string,
  content: string,
): boolean {
  const user = history.messages[turn.user_message_id],
    assistant = history.messages[turn.assistant_message_id];
  return (
    turn.intent_kind === "edit_send" &&
    turn.source_message_id === sourceId &&
    turn.user_message_id !== sourceId &&
    user?.id === turn.user_message_id &&
    user.role === "user" &&
    user.content === content &&
    user.parentId === turn.parent_message_id &&
    assistant?.id === turn.assistant_message_id &&
    assistant.role === "assistant" &&
    assistant.parentId === user.id &&
    (user.childrenIds ?? []).includes(assistant.id)
  );
}
