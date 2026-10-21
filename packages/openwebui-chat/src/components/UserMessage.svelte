<script lang="ts">
  import type { ChatProps, NativeMessage } from "../../public";
  import FileItem from "./FileItem.svelte";
  export let data: ChatProps;
  export let message: NativeMessage;
</script>

<!-- 原UserMessage的右侧气泡/原件分区；未支持编辑重发时不挂无效操作。 -->
<div class="flex justify-end w-full mb-2">
  <div class="max-w-[85%]">
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
  </div>
</div>
