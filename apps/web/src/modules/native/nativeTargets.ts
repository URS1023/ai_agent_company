/** 原生入口只决定目标类型，实际URL仍由后端校验并生成。 */
export type NativeTarget =
  | "apps"
  | "workflow"
  | "agent"
  | "agent_v2"
  | "knowledge"
  | "plugins"
  | "chat"
  | "model_chat"
  | "models";
export const targetPermissions: Record<NativeTarget, string> = {
  apps: "m13.read",
  workflow: "m04.read",
  agent: "m03.read",
  agent_v2: "m03.read",
  knowledge: "m08.read",
  plugins: "m12.admin",
  chat: "m02.read",
  model_chat: "m02.read",
  models: "m15.read",
};
export interface NativeEntry {
  target: NativeTarget;
  native_id?: string;
}
export function pageTarget(config?: string): NativeTarget {
  if (config === "openwebui") return "chat";
  if (config === "models") return "models";
  if (config === "dify-knowledge") return "knowledge";
  if (config === "dify-agents") return "agent";
  if (config === "dify-workflows" || config === "dify-triggers")
    return "workflow";
  if (config === "dify-plugins") return "plugins";
  return "apps";
}
export function bindingEntry(record: Record<string, any>): NativeEntry {
  if (record.app_mode === "openwebui_model")
    return { target: "model_chat", native_id: record.native_id };
  if (record.app_mode === "agent_v2")
    return { target: "agent_v2", native_id: record.native_id };
  if (record.app_mode === "workflow" || record.app_mode === "chatflow")
    return { target: "workflow", native_id: record.native_id };
  return { target: "agent", native_id: record.native_id };
}
export function connectionEntry(engine: string, config?: string): NativeEntry {
  if (config === "models") return { target: "models" };
  if (config === "connections")
    return { target: engine === "openwebui" ? "chat" : "apps" };
  const target = pageTarget(config);
  if (
    engine === "openwebui" &&
    !["chat", "models", "knowledge"].includes(target)
  )
    return { target: "chat" };
  if (engine === "dify" && target === "chat") return { target: "apps" };
  return { target };
}
export function requiresNativeRun(record: Record<string, any>) {
  return (
    record.app_mode === "agent_v2" || record.app_mode === "openwebui_model"
  );
}
export function applicationModeLabel(mode: string) {
  return (
    (
      {
        workflow: "工作流",
        chatflow: "对话流",
        "agent-chat": "Agent",
        agent_v2: "Agent工作流",
        openwebui_model: "Open WebUI模型",
      } as Record<string, string>
    )[mode] ?? "应用"
  );
}
