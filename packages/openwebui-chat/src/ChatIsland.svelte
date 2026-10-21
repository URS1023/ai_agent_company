<script lang="ts">
  import { setContext, tick, onDestroy } from "svelte";
  import type { ChatProps, NativeHistory } from "../public";
  import { createChatScope } from "./scope";
  import Messages from "./components/Messages.svelte";
  import MessageInput from "./components/MessageInput.svelte";
  export let data: ChatProps;
  export function update(next: ChatProps) {
    data = next;
  }
  const scope = createChatScope();
  setContext("i18n", scope.i18n);
  setContext("native-chat-scope", scope);
  let scrollBox: HTMLDivElement,
    autoScroll = true;
  let scrollTicket = 0,
    disposed = false,
    previousHistory: NativeHistory | undefined,
    previousAutoScroll: boolean | undefined;
  async function followContent(history: NativeHistory, enabled: boolean) {
    if (previousHistory === history && previousAutoScroll === enabled) return;
    previousHistory = history;
    previousAutoScroll = enabled;
    const ticket = ++scrollTicket;
    if (!enabled) return;
    await tick();
    if (disposed || ticket !== scrollTicket) return;
    // 沿原Messages的局部DOM别名赋值，避免绑定对象被编译为mutate并反复触发更新。
    const element = scrollBox;
    if (element) element.scrollTop = element.scrollHeight;
  }
  $: followContent(data.snapshot.history, autoScroll);
  onDestroy(() => {
    disposed = true;
    scrollTicket++;
  });
</script>

<div class="owui-root h-full flex flex-col bg-white text-gray-900">
  <header class="flex items-center px-5 py-2 text-sm border-b border-gray-100">
    <strong>Open WebUI</strong><span class="grow"
    ></span>{#if data.snapshot.status === "waiting_approval"}<span
        >等待确认</span
      >{:else if data.snapshot.status === "cancel_requested"}<span
        >正在停止</span
      >{:else if ["reserved", "running"].includes(data.snapshot.status)}<span
        >回答中…</span
      >{/if}
  </header>
  {#if data.snapshot.error}<div
      role="alert"
      class="text-sm text-red-700 bg-red-50 mx-4 mt-3 rounded-xl px-3 py-2"
    >
      {data.snapshot.error}
    </div>{/if}
  <div
    bind:this={scrollBox}
    class="grow min-h-0 overflow-y-auto pt-4 pb-4"
    on:scroll={() =>
      (autoScroll =
        scrollBox.scrollHeight - scrollBox.scrollTop <=
        scrollBox.clientHeight + 60)}
  >
    <Messages {data} />
  </div>
  <MessageInput {data} />
</div>
