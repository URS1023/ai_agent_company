<script lang="ts">
  import type { ChatProps, NativeMessage } from "../../public";
  import FileItem from "./FileItem.svelte";
  import MessageEditor from "./MessageEditor.svelte";
  import EditPencil from "../reused/EditPencil.svelte";
  export let data: ChatProps;
  export let message: NativeMessage;
</script>

<!-- 原UserMessage的右侧气泡/原件和编辑分区；不原位改写历史消息。 -->
<div class="flex justify-end w-full mb-2">
  <div
    class={data.snapshot.editing?.sourceMessageId === message.id
      ? "w-full"
      : "max-w-[85%]"}
  >
    {#if data.snapshot.editing?.sourceMessageId === message.id}
      <MessageEditor {data} />
    {:else}
      <div class="flex flex-wrap justify-end gap-2 mb-2">
        {#each data.snapshot.messageAttachments[message.id] ?? [] as file (file.id)}<FileItem
            {file}
            download={data.actions.download}
          />{/each}
      </div>
      <div
        class="rounded-3xl bg-gray-100 px-4 py-2.5 whitespace-pre-wrap break-words text-[0.9375rem]"
        dir="auto"
      >
        {message.content}
      </div>
      {#if data.snapshot.canWrite}<div class="flex justify-end pt-1">
          <button
            type="button"
            aria-label="编辑消息"
            title="编辑消息"
            class="inline-flex size-7 items-center justify-center rounded-lg text-gray-500 hover:bg-gray-100 disabled:opacity-40"
            disabled={data.snapshot.busy ||
              data.snapshot.uncertain ||
              !!data.snapshot.editing}
            on:click={() => data.actions.beginEdit(message.id)}
            ><EditPencil /></button
          >
        </div>{/if}
    {/if}
  </div>
</div>
