<script lang="ts">
  import type { ChatProps, NativeMessage } from "../../public";
  import { siblings, deepestChild } from "../messages";
  import UserMessage from "./UserMessage.svelte";
  import ResponseMessage from "./ResponseMessage.svelte";
  export let data: ChatProps;
  export let message: NativeMessage;
  $: branchIds = siblings(data.snapshot.history, message);
  $: index = branchIds.indexOf(message.id);
  const branch = (offset: number) => {
    const id = branchIds[index + offset];
    if (id) data.actions.branch(deepestChild(data.snapshot.history, id));
  };
</script>

<!-- 原Message的用户/助手分流与兄弟导航；未知角色不冒充助手回复。 -->
<li
  role="listitem"
  class="flex flex-col justify-between px-3.5 mb-5 w-full max-w-[58rem] mx-auto rounded-lg group message-listitem"
>
  {#if message.role === "user"}<UserMessage
      {data}
      {message}
    />{:else if message.role === "assistant"}<ResponseMessage
      {data}
      {message}
    />{/if}
  {#if branchIds.length > 1}<nav
      aria-label="消息分支"
      class="flex gap-2 items-center text-xs text-gray-500 mt-1 {message.role ===
      'user'
        ? 'justify-end'
        : 'pl-10'}"
    >
      <button
        type="button"
        aria-label="上一分支"
        disabled={index <= 0}
        on:click={() => branch(-1)}>‹</button
      ><span>{index + 1}/{branchIds.length}</span><button
        type="button"
        aria-label="下一分支"
        disabled={index >= branchIds.length - 1}
        on:click={() => branch(1)}>›</button
      >
    </nav>{/if}
</li>
