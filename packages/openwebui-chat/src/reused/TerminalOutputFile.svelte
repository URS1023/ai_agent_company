<script lang="ts">
  export let item: any;
  export let chatId = "";
</script>

{#if item?.name}<span>{item.name}</span>{/if}
