<script lang="ts">
  import type { ChatProps } from "../../public";
  import FileItem from "./FileItem.svelte";
  export let data: ChatProps;
  function choose(id: string) {
    const ids = data.snapshot.attachmentIds.includes(id)
      ? data.snapshot.attachmentIds.filter((item) => item !== id)
      : [...data.snapshot.attachmentIds, id];
    if (ids.length <= 10) data.actions.attach(ids);
  }
</script>

<!-- 精提原MessageInput的圆角输入/附件/发送停止结构，去除未接入的云盘、语音与终端入口。 -->
<div class="w-full max-w-[58rem] mx-auto px-3.5 pb-3">
  <div class="flex flex-wrap gap-2 mb-2">
    {#each data.snapshot.files as file (file.id)}<div>
        <FileItem
          {file}
          download={data.actions.download}
          selected={data.snapshot.attachmentIds.includes(file.id)}
          choose={data.snapshot.canWrite ? choose : null}
        />{#if ["processing", "uploading"].includes(file.status)}<button
            class="text-xs ml-1"
            type="button"
            disabled={data.snapshot.busy}
            on:click={() => data.actions.refreshFile(file.id)}>刷新</button
          >{:else if file.status === "unknown"}<button
            class="text-xs ml-1"
            type="button"
            on:click={() => data.actions.reconcileFile(file.id)}>核对</button
          >{/if}
      </div>{/each}
  </div>
  <form
    class="rounded-3xl border border-gray-200 bg-gray-50/70 px-4 py-3"
    on:submit|preventDefault={() => data.actions.send()}
  >
    <textarea
      aria-label="输入消息"
      rows="3"
      maxlength="40000"
      disabled={!data.snapshot.canWrite}
      value={data.snapshot.draft}
      on:input={(event) => data.actions.draft(event.currentTarget.value)}
      on:keydown={(event) => {
        if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
          event.preventDefault();
          if (!data.snapshot.busy && data.snapshot.draft.trim())
            data.actions.send();
        }
      }}
      class="bg-transparent outline-hidden w-full resize-none text-[0.9375rem]"
      placeholder="发送消息"
    ></textarea>
    <div class="flex items-center gap-2">
      <label
        class="cursor-pointer rounded-full p-1 text-xl"
        aria-label="添加附件"
        ><input
          aria-label="上传聊天附件"
          type="file"
          class="sr-only"
          accept=".txt,.md,.csv,.json,.log,.pdf"
          disabled={!data.snapshot.canWrite || data.snapshot.busy}
          on:change={(event) => {
            const file = event.currentTarget.files?.[0];
            if (file) data.actions.upload(file);
            event.currentTarget.value = "";
          }}
        />＋</label
      ><select
        aria-label="对话模型"
        class="min-w-0 max-w-[60%] bg-transparent text-xs text-gray-600"
        disabled={data.snapshot.busy || !data.snapshot.canWrite}
        value={data.snapshot.modelId}
        on:change={(event) => data.actions.model(event.currentTarget.value)}
        ><option value="">选择模型</option
        >{#each data.snapshot.models as model}<option value={model.id}
            >{model.name}</option
          >{/each}</select
      ><span class="grow"></span>{#if data.snapshot.canStop}<button
          type="button"
          aria-label="停止回答"
          on:click={() => data.actions.stop()}
          class="rounded-full bg-gray-900 text-white size-8">■</button
        >{:else if data.snapshot.uncertain}<button
          type="button"
          on:click={() => data.actions.verify()}
          class="rounded-full border px-3 py-1 text-sm">核对结果</button
        >{:else}<button
          type="submit"
          aria-label="发送消息"
          disabled={data.snapshot.busy ||
            !data.snapshot.canWrite ||
            !data.snapshot.modelId ||
            !data.snapshot.draft.trim()}
          class="bg-gray-900 text-white disabled:opacity-40 transition rounded-full p-[0.3125rem] self-center"
          ><svg viewBox="0 0 16 16" fill="currentColor" class="size-5"
            ><path
              fill-rule="evenodd"
              d="M8 14a.75.75 0 0 1-.75-.75V4.56L4.03 7.78a.75.75 0 0 1-1.06-1.06l4.5-4.5a.75.75 0 0 1 1.06 0l4.5 4.5a.75.75 0 0 1-1.06 1.06L8.75 4.56v8.69A.75.75 0 0 1 8 14Z"
              clip-rule="evenodd"
            /></svg
          ></button
        >{/if}
    </div>
  </form>
</div>
