<script lang="ts">
  import { tick } from "svelte";
  import type { ChatProps } from "../../public";
  import EditAttachments from "./EditAttachments.svelte";
  export let data: ChatProps;
  let textarea: HTMLTextAreaElement,
    focusedSource = "";
  $: draft = data.snapshot.editing;
  $: disabled =
    data.snapshot.busy || data.snapshot.uncertain || !data.snapshot.canWrite;
  async function focusSource(source: string) {
    if (focusedSource === source) return;
    focusedSource = source;
    await tick();
    const element = textarea;
    element?.focus({ preventScroll: true });
  }
  $: if (draft) focusSource(draft.sourceMessageId);
</script>

<!-- 原UserMessage编辑区的圆角表单与取消/发送；草稿由Vue持有，服务端保存前不丢失。 -->
{#if draft}
  <form
    aria-label="编辑消息"
    class="w-full rounded-3xl bg-gray-50 border border-gray-200 px-4 py-3"
    on:submit|preventDefault={() => data.actions.editAndSend()}
  >
    <textarea
      bind:this={textarea}
      aria-label="编辑消息内容"
      rows="4"
      maxlength="40000"
      class="w-full bg-transparent resize-none text-[0.9375rem] outline-hidden"
      {disabled}
      value={draft.content}
      on:input={(event) =>
        data.actions.updateEdit({ content: event.currentTarget.value })}
      on:keydown={(event) => {
        if (event.key === "Escape" && !disabled) {
          event.preventDefault();
          data.actions.cancelEdit();
        }
        if (
          event.key === "Enter" &&
          (event.ctrlKey || event.metaKey) &&
          !event.isComposing &&
          !disabled
        ) {
          event.preventDefault();
          data.actions.editAndSend();
        }
      }}
    ></textarea>
    {#if draft.errors?.content}<p role="alert" class="text-sm text-red-700">
        {draft.errors.content}
      </p>{/if}
    <EditAttachments {data} />
    <div class="mt-2 flex items-center gap-2">
      <select
        aria-label="编辑使用模型"
        class="min-w-0 max-w-[55%] bg-transparent text-xs text-gray-600"
        {disabled}
        value={data.snapshot.modelId}
        on:change={(event) => data.actions.model(event.currentTarget.value)}
      >
        <option value="">选择模型</option
        >{#each data.snapshot.models as model}<option value={model.id}
            >{model.name}</option
          >{/each}
      </select><span class="grow"></span>
      <button
        type="button"
        class="rounded-lg px-2.5 py-1 text-xs text-gray-600 hover:bg-gray-100 disabled:opacity-50"
        {disabled}
        on:click={() => data.actions.cancelEdit()}>取消</button
      >
      {#if data.snapshot.uncertain}<button
          type="button"
          class="rounded-lg border px-2.5 py-1 text-xs"
          disabled={data.snapshot.busy}
          on:click={() => data.actions.verify()}>核对结果</button
        >
      {:else if data.snapshot.canStop}<button
          type="button"
          aria-label="停止回答"
          class="rounded-lg border px-2.5 py-1 text-xs"
          on:click={() => data.actions.stop()}>停止</button
        >
      {:else}<button
          type="submit"
          class="rounded-lg bg-gray-900 px-2.5 py-1 text-xs text-white disabled:opacity-40"
          disabled={disabled ||
            !draft.content.trim() ||
            !data.snapshot.modelId ||
            (draft.attachmentMode === "replace" && !draft.attachmentIds.length)}
          >保存并发送</button
        >{/if}
    </div>
  </form>
{/if}
