/** SSE 增量解析：网络分片可能把一行事件切成两半，必须先缓冲再按行处理。 */
export interface DeltaReader {
  push(chunk: string): void;
  flush(): void;
}

/**
 * 逐块喂入文本，回调拿到已解析的增量。
 * onDelta 收到 content 与 reasoning_content 两段，UI 自行决定如何展示。
 */
export function createDeltaReader(
  onDelta: (delta: { content?: string; reasoning?: string }) => void,
): DeltaReader {
  let buffer = "";
  const handle = (line: string) => {
    if (!line.startsWith("data:")) return;
    const payload = line.slice(5).trim();
    if (!payload || payload === "[DONE]") return;
    try {
      const delta = JSON.parse(payload)?.choices?.[0]?.delta ?? {};
      const content =
        typeof delta.content === "string" && delta.content ? delta.content : undefined;
      const reasoning =
        typeof delta.reasoning_content === "string" && delta.reasoning_content
          ? delta.reasoning_content
          : undefined;
      if (content || reasoning) onDelta({ content, reasoning });
    } catch {
      /* 该行不是完整 JSON，丢弃以免污染已生成内容 */
    }
  };
  return {
    push(chunk: string) {
      buffer += chunk;
      const lines = buffer.split("\n");
      // 最后一段可能是不完整行，留到下一次拼接。
      buffer = lines.pop() ?? "";
      for (const line of lines) handle(line.trim());
    },
    flush() {
      if (buffer.trim()) handle(buffer.trim());
      buffer = "";
    },
  };
}
