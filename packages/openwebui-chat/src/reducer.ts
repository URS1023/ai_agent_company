import type { NativeHistory, NativeFrame } from "../public";
import { mergeNativeMessages } from "./messages";
import {
  applyResponseStreamEvent,
  getOutputText,
} from "./reused/structuredOutput";
/** 提取原Chat.svelte response/chat completion分支，保留原output算法，不执行模型或工具循环。 */
export function reduceNativeFrame(
  history: NativeHistory,
  assistantId: string,
  frame: NativeFrame,
): NativeHistory {
  if (frame.event_type === "chat:messages")
    return mergeNativeMessages(history, frame.data);
  const next = structuredClone(history),
    message = next.messages[assistantId];
  if (!message || message.role !== "assistant") return next;
  const data = frame.data ?? {},
    type = frame.event_type;
  if (type === "response:completion") {
    message.output = applyResponseStreamEvent(message.output ?? [], data);
    if (data.type === "response.output_text.delta")
      message.content += data.delta ?? "";
    else if (data.type === "response.completed" || data.type?.endsWith(".done"))
      message.content = getOutputText(message.output) || message.content;
  } else if (type === "chat:completion") {
    if (Array.isArray(data.output)) {
      message.output = data.output;
      message.content = getOutputText(data.output);
    } else if (data.choices?.[0]?.message?.content)
      message.content += data.choices[0].message.content;
    else if (typeof data.choices?.[0]?.delta?.content === "string")
      message.content += data.choices[0].delta.content;
    else if (typeof data.content === "string") message.content = data.content;
    for (const key of ["done", "error", "usage", "sources"])
      if (data[key] !== undefined) message[key] = data[key];
  } else if (type === "chat:message:delta" || type === "message")
    message.content += typeof data.content === "string" ? data.content : "";
  else if (type === "chat:message" || type === "replace") {
    if (typeof data.content === "string") message.content = data.content;
  } else if (type === "chat:message:error")
    message.error = data.error ?? data.content ?? "本次回答未完成";
  else if (type === "status")
    message.statusHistory = [...(message.statusHistory ?? []), data];
  return next;
}
