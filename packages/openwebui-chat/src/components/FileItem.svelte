<script lang="ts">
  import type { FileView } from "../../public";
  export let file: FileView;
  export let download: (id: string) => void;
  export let selected = false;
  export let choose: ((id: string) => void) | null = null;
</script>

<!-- 原FileItem的紧凑卡片与处理中状态；下载只接受平台映射ID。 -->
<div
  class="inline-flex items-center gap-2 rounded-xl border border-gray-200 bg-white px-3 py-2 max-w-full text-xs"
>
  {#if choose}<input
      type="checkbox"
      aria-label={`选择${file.name}`}
      checked={selected}
      disabled={file.status !== "ready"}
      on:change={() => choose?.(file.id)}
    />{/if}
  <button
    type="button"
    class="text-left truncate"
    on:click={() => download(file.id)}>{file.name}</button
  ><span class="text-gray-500 shrink-0">{Math.ceil(file.size / 1024)} KB</span>
  {#if file.status !== "ready"}<span
      >{(
        {
          processing: "处理中",
          uploading: "上传中",
          unknown: "待核对",
          failed: "失败",
          quarantined: "已隔离",
          deleted: "已删除",
        } as Record<string, string>
      )[file.status] ?? "待核对"}</span
    >{/if}
</div>
