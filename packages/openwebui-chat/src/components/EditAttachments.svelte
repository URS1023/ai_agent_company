<script lang="ts">
  import type { ChatProps } from "../../public";
  import FileItem from "./FileItem.svelte";
  export let data: ChatProps;
  $: draft = data.snapshot.editing;
  $: ready = data.snapshot.files.filter((file) => file.status === "ready");
  $: disabled =
    data.snapshot.busy || data.snapshot.uncertain || !data.snapshot.canWrite;
  function choose(id: string) {
    if (!draft || disabled) return;
    const ids = draft.attachmentIds.includes(id)
      ? draft.attachmentIds.filter((value) => value !== id)
      : [...draft.attachmentIds, id];
    if (ids.length <= 10) data.actions.updateEdit({ attachmentIds: ids });
  }
</script>

{#if draft && (draft.inheritedAttachments.length || ready.length || draft.attachmentMode !== "inherit")}
  <div class="space-y-2 mt-2">
    <select
      aria-label="编辑附件方式"
      class="text-sm rounded-lg border border-gray-200 bg-white px-2 py-1"
      {disabled}
      value={draft.attachmentMode}
      on:change={(event) =>
        data.actions.updateEdit({
          attachmentMode: event.currentTarget.value as
            "inherit" | "replace" | "remove",
        })}
    >
      <option value="inherit">保留原附件</option><option value="replace"
        >替换附件</option
      ><option value="remove">不带附件</option>
    </select>
    {#if draft.attachmentMode === "inherit"}
      <div class="flex flex-wrap gap-2">
        {#each draft.inheritedAttachments as file (file.id)}<FileItem
            {file}
            download={data.actions.download}
          />{/each}
      </div>
    {:else if draft.attachmentMode === "replace"}
      <div class="flex flex-wrap gap-2">
        {#each ready as file (file.id)}
          <label
            class="flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-2 py-1 text-xs"
          >
            <input
              type="checkbox"
              aria-label={`替换为${file.name}`}
              checked={draft.attachmentIds.includes(file.id)}
              disabled={disabled ||
                (!draft.attachmentIds.includes(file.id) &&
                  draft.attachmentIds.length >= 10)}
              on:change={() => choose(file.id)}
            />{file.name}
          </label>
        {/each}
      </div>
      <label
        class="inline-flex cursor-pointer items-center rounded-lg border border-gray-200 px-2 py-1 text-xs"
        >添加附件
        <input
          type="file"
          aria-label="上传替换附件"
          class="sr-only"
          accept=".txt,.md,.csv,.json,.log,.pdf"
          {disabled}
          on:change={(event) => {
            const file = event.currentTarget.files?.[0];
            if (file) data.actions.upload(file);
            event.currentTarget.value = "";
          }}
        />
      </label>
    {/if}
    {#if draft.errors?.attachment_ids}<p
        role="alert"
        class="text-sm text-red-700"
      >
        {draft.errors.attachment_ids}
      </p>{/if}
  </div>
{:else if draft}
  <label
    class="inline-flex cursor-pointer items-center rounded-lg border border-gray-200 px-2 py-1 text-xs mt-2"
    >添加附件
    <input
      type="file"
      aria-label="上传替换附件"
      class="sr-only"
      accept=".txt,.md,.csv,.json,.log,.pdf"
      {disabled}
      on:change={(event) => {
        const file = event.currentTarget.files?.[0];
        if (file) {
          data.actions.updateEdit({ attachmentMode: "replace" });
          data.actions.upload(file);
        }
        event.currentTarget.value = "";
      }}
    />
  </label>
{/if}
