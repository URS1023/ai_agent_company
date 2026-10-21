import type { NativeFrame } from "../public";
import { EventSourceParserStream } from "eventsource-parser/stream";
/** 复用原streaming的SSE分帧；无随机分字、自动重连或正文日志。 */
export async function* readNativeEvents(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<NativeFrame> {
  const decoder = new TextDecoder();
  const reader = body
    .pipeThrough(
      new TransformStream<Uint8Array, string>({
        transform: (chunk, stream) => {
          stream.enqueue(decoder.decode(chunk, { stream: true }));
        },
        flush: (stream) => {
          const rest = decoder.decode();
          if (rest) stream.enqueue(rest);
        },
      }),
    )
    .pipeThrough(new EventSourceParserStream())
    .getReader();
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) return;
      if (!value) continue;
      if (value.data.length > 2 * 1024 * 1024)
        throw new Error("对话事件过大，请核对原结果");
      if (
        ["reconcile_required", "access_revoked"].includes(value.event ?? "")
      ) {
        yield {
          sequence: 0,
          event_type: value.event!,
          data: JSON.parse(value.data),
        };
        return;
      }
      const frame = JSON.parse(value.data);
      if (
        !Number.isSafeInteger(frame.sequence) ||
        frame.sequence < 1 ||
        typeof frame.event_type !== "string"
      )
        throw new Error("对话事件无法识别，请核对原结果");
      yield frame;
    }
  } finally {
    await reader.cancel();
    reader.releaseLock();
  }
}
