<script lang="ts">
  import type { ChatProps, NativeMessage } from "../../public";
  import Markdown from "../reused/Markdown.svelte";
  import StructuredOutputRenderer from "../reused/StructuredOutputRenderer.svelte";
  import { messageError } from "../messages";
  export let data: ChatProps;
  export let message: NativeMessage;
  let copied = false;
  async function copy() {
    try {
      await navigator.clipboard.writeText(message.content || "");
      copied = true;
    } catch {
      copied = false;
    }
  }
</script>

<!-- 原ResponseMessage的名称/内容/输出过程/操作分区，保留完整output，不添加本机模型循环。 -->
<div class="flex w-full gap-3">
  <div
    class="shrink-0 size-7 rounded-full bg-gray-900 text-white flex items-center justify-center text-xs"
    aria-hidden="true"
  >
    W
  </div>
  <div class="grow min-w-0">
    <div class="text-sm font-semibold mb-1">
      {data.snapshot.models.find((item) => item.id === message.model)?.name ??
        "Open WebUI"}
    </div>
    {#if message.output?.length}<StructuredOutputRenderer
        id={message.id}
        chatId={data.snapshot.conversationId}
        messageId={message.id}
        output={message.output}
        done={message.done ?? false}
        save={data.snapshot.canWrite &&
          data.snapshot.status === "waiting_approval"}
        editCodeBlock={false}
        allowEmbeds={false}
        onResolve={data.actions.resolve}
      />{:else}<Markdown
        content={message.content ?? ""}
        done={message.done ?? false}
      />{/if}
    {#if !message.content && !message.output?.length && !message.done}<span
        class="text-sm text-gray-500">处理中…</span
      >{/if}
    {#if message.error}<div role="alert" class="text-sm text-red-700">
        {messageError(message.error)}
      </div>{/if}
    {#if message.content || (message.done && data.snapshot.canWrite)}<div
        class="flex gap-1 pt-2"
      >
        {#if message.content}<button
            type="button"
            aria-label="复制回答"
            class="text-xs text-gray-500 rounded-lg px-2 py-1 hover:bg-gray-100"
            on:click={copy}>{copied ? "已复制" : "复制"}</button
          >{/if}
        {#if message.done && data.snapshot.canWrite}<button
            type="button"
            aria-label="重新生成"
            title="重新生成"
            class="inline-flex size-7 shrink-0 items-center justify-center rounded-lg p-1.5 text-gray-500 hover:bg-gray-100 disabled:opacity-40"
            disabled={data.snapshot.busy ||
              data.snapshot.uncertain ||
              !!data.snapshot.editing ||
              !data.snapshot.modelId}
            on:click={() => data.actions.regenerate(message.id)}
          >
            <svg
              class="size-4"
              aria-hidden="true"
              stroke-width="1.5"
              viewBox="-2 -2 28 28"
              fill="none"
              ><path
                d="M21.1679 8C19.6247 4.46819 16.1006 2 11.9999 2C6.81459 2 2.55104 5.94668 2.04932 11"
                stroke="currentColor"
                stroke-linecap="round"
                stroke-linejoin="round"
              /><path
                d="M17 8H21.4C21.7314 8 22 7.73137 22 7.4V3"
                stroke="currentColor"
                stroke-linecap="round"
                stroke-linejoin="round"
              /><path
                d="M2.88146 16C4.42458 19.5318 7.94874 22 12.0494 22C17.2347 22 21.4983 18.0533 22 13"
                stroke="currentColor"
                stroke-linecap="round"
                stroke-linejoin="round"
              /><path
                d="M7.04932 16H2.64932C2.31795 16 2.04932 16.2686 2.04932 16.6V21"
                stroke="currentColor"
                stroke-linecap="round"
                stroke-linejoin="round"
              /></svg
            >
          </button>{/if}
      </div>{/if}
  </div>
</div>
