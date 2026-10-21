<script lang="ts">
  import type { ChatProps } from "../../public";
  import { currentMessages } from "../messages";
  import Message from "./Message.svelte";
  export let data: ChatProps;
  let count = 30;
  $: messages = currentMessages(data.snapshot.history);
</script>

<!-- 原Messages的parentId分支顺序与log语义，加载更早消息仅展开已授权树，不重新查询。 -->
<section class="w-full pt-2" aria-label="对话内容">
  {#if messages.length > count}<button
      type="button"
      class="block mx-auto text-xs text-gray-500 py-2"
      on:click={() => (count += 30)}>加载更早消息</button
    >{/if}
  <ul
    role="log"
    aria-live="polite"
    aria-relevant="additions"
    aria-atomic="false"
  >
    {#each messages.slice(-count) as message (message.id)}<Message
        {data}
        {message}
      />{/each}
  </ul>
  {#if !messages.length}<div
      class="h-full min-h-64 flex items-center justify-center text-xl font-semibold text-gray-800"
    >
      从这里开始提问
    </div>{/if}
</section>
