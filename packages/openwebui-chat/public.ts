/** Vue与Svelte共享的平台协议；不包含原生凭据、原生服务地址或全局应用状态。 */
export interface NativeMessage {
  id: string;
  role: string;
  parentId: string | null;
  childrenIds?: string[];
  content: string;
  output?: Record<string, any>[];
  done?: boolean;
  timestamp?: number;
  model?: string;
  error?: any;
  [key: string]: any;
}
export interface NativeHistory {
  messages: Record<string, NativeMessage>;
  currentId: string | null;
}
export interface FileView {
  id: string;
  name: string;
  size: number;
  content_type: string;
  status: string;
  revision: number;
  error_code?: string | null;
}
export type EditAttachmentMode = "inherit" | "replace" | "remove";
export interface MessageEditDraft {
  sourceMessageId: string;
  content: string;
  attachmentMode: EditAttachmentMode;
  attachmentIds: string[];
  inheritedAttachments: FileView[];
  errors?: Record<string, string>;
}
export type MessageEditPatch = Partial<
  Pick<MessageEditDraft, "content" | "attachmentMode" | "attachmentIds">
>;
export interface ChatSnapshot {
  conversationId: string;
  history: NativeHistory;
  displayName: string;
  models: { id: string; name: string }[];
  modelId: string;
  canWrite: boolean;
  busy: boolean;
  status: string;
  draft: string;
  files: FileView[];
  attachmentIds: string[];
  messageAttachments: Record<string, FileView[]>;
  error: string;
  canStop: boolean;
  uncertain: boolean;
  editing?: MessageEditDraft;
}
export interface ChatActions {
  draft: (text: string) => void;
  model: (id: string) => void;
  send: () => void;
  stop: () => void;
  verify: () => void;
  branch: (id: string) => void;
  upload: (file: File) => void;
  attach: (ids: string[]) => void;
  refreshFile: (id: string) => void;
  reconcileFile: (id: string) => void;
  download: (id: string) => void;
  resolve: (callId: string, approved: boolean) => void;
  beginEdit: (messageId: string) => void;
  updateEdit: (patch: MessageEditPatch) => void;
  cancelEdit: () => void;
  editAndSend: () => void;
  regenerate: (assistantId: string) => void;
}
export interface ChatProps {
  snapshot: ChatSnapshot;
  actions: ChatActions;
}
export interface ChatHandle {
  update: (props: ChatProps) => void;
  unmount: () => void;
}
export type MountNativeChat = (
  host: HTMLElement,
  props: ChatProps,
) => ChatHandle;
export interface NativeFrame {
  sequence: number;
  event_type: string;
  data: any;
}
