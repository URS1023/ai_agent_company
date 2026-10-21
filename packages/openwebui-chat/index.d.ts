export type * from "./public";
export declare const mountNativeChat: import("./public").MountNativeChat;
export declare function reduceNativeFrame(
  history: import("./public").NativeHistory,
  assistantId: string,
  frame: import("./public").NativeFrame,
): import("./public").NativeHistory;
export declare function readNativeEvents(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<import("./public").NativeFrame>;
