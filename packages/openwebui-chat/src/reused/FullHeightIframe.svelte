<script lang="ts">
  export let src = "";
  export let className = "";
  export let args: unknown;
  export let allowScripts = false;
  export let allowForms = false;
  export let allowSameOrigin = false;
  export let allowPopups = false;
</script>

<!-- 原嵌入尚未接入平台授权，首批不加载模型提供的URL。 -->
{#if src}<span class={className}>嵌入内容暂不可用</span>{/if}
