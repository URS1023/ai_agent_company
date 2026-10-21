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
    {#if message.content}<div class="flex gap-1 pt-2">
        <button
          type="button"
          aria-label="复制回答"
          class="text-xs text-gray-500 rounded-lg px-2 py-1 hover:bg-gray-100"
          on:click={copy}>{copied ? "已复制" : "复制"}</button
        >
      </div>{/if}
  </div>
</div>
