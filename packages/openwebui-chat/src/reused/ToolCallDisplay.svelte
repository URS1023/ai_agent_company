<script lang="ts">
  import { decode } from "html-entities";
  export let id = "";
  export let attributes: any = {};
  export let resultContent = "";
  export let open = false;
  export let grouped = false;
  export let allowEmbeds = false;
  export let className = "";
  export let resolvable = false;
  export let resolving = false;
  export let onResolve: (approved: boolean) => unknown = () => {};
  export let buttonClassName = "";
  function json(value: unknown) {
    if (typeof value !== "string") return JSON.stringify(value, null, 2);
    try {
      return JSON.stringify(JSON.parse(decode(value)), null, 2);
    } catch {
      return decode(value);
    }
  }
  $: pending = ["pending", "queued", "requires_approval"].includes(
    attributes.status,
  );
</script>

<!-- 精提原ToolCallDisplay的名称/状态/参数/结果/批准分区，所有动作交平台核当前来源。 -->
<section
  {id}
  class="{className} rounded-xl border border-gray-200 bg-gray-50/50 p-2 my-2"
>
  <button
    type="button"
    aria-expanded={open}
    class="{buttonClassName} w-full flex items-center gap-2 text-left"
    on:click={() => (open = !open)}
    ><span aria-hidden="true">⚒</span><strong class="text-sm"
      >{attributes.name || "工具调用"}</strong
    ><span class="grow"></span><span class="text-xs text-gray-500"
      >{pending
        ? "等待确认"
        : attributes.status === "completed"
          ? "已完成"
          : attributes.status === "failed"
            ? "失败"
            : attributes.done === "true"
              ? "已完成"
              : "处理中"}</span
    ><span>{open ? "⌃" : "⌄"}</span></button
  >
  {#if open}<div class="space-y-2 mt-2 text-xs">
      {#if attributes.arguments}<pre
          class="overflow-auto rounded-lg bg-white p-2 whitespace-pre-wrap">{json(
            attributes.arguments,
          )}</pre>{/if}{#if resultContent}<pre
          class="overflow-auto rounded-lg bg-white p-2 whitespace-pre-wrap">{json(
            resultContent,
          )}</pre>{/if}
    </div>{/if}
  {#if resolvable && pending}<div class="flex gap-2 mt-2">
      <button
        type="button"
        disabled={resolving}
        class="rounded-lg border px-3 py-1"
        on:click={() => onResolve(false)}>拒绝</button
      ><button
        type="button"
        disabled={resolving}
        class="rounded-lg bg-gray-900 text-white px-3 py-1"
        on:click={() => onResolve(true)}>允许</button
      >
    </div>{/if}
</section>
