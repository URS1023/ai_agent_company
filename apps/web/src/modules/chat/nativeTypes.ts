import type { NativeHistory, FileView } from "@openwebui-chat";
export type { NativeHistory, FileView };
export interface NativeIdentity {
  id: string;
  integration_id: string;
  status: string;
  revision: number;
  error_code?: string;
}
export interface NativeConversation {
  id: string;
  identity_id: string;
  integration_id: string;
  title: string;
  status: string;
  revision: number;
  active_turn_id: string | null;
  created_at: string;
  updated_at: string;
  history?: NativeHistory | null;
  message_attachments?: Record<string, NativeFile[]>;
}
export interface NativeTurn {
  id: string;
  conversation_id: string;
  identity_id: string;
  model_id: string;
  status: string;
  user_message_id: string;
  assistant_message_id: string;
  parent_message_id: string | null;
  idempotency_key: string;
  revision: number;
  error?: string;
  error_code?: string;
  intent_kind?: "send" | "regenerate" | "edit_send";
  source_message_id?: string;
}
export interface NativeFile {
  id: string;
  identity_id: string;
  status: string;
  revision: number;
  error_code?: string;
  source_file: { name: string; size: number; content_type: string };
}
export function fileView(row: NativeFile): FileView {
  return {
    id: row.id,
    name: row.source_file.name,
    size: row.source_file.size,
    content_type: row.source_file.content_type,
    status: row.status,
    revision: row.revision,
    error_code: row.error_code,
  };
}
export const nativePrefix = "/chat/native";
export const activeTurnStates = new Set([
  "reserved",
  "running",
  "waiting_approval",
  "cancel_requested",
]);
export const emptyHistory = (): NativeHistory => ({
  messages: {},
  currentId: null,
});
