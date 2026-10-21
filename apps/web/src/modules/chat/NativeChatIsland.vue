<script setup lang="ts">
import { nextTick, onScopeDispose, shallowRef, watch } from "vue";
import type { ChatSnapshot, ChatActions, ChatHandle } from "@openwebui-chat";
const props = defineProps<{ snapshot: ChatSnapshot; actions: ChatActions }>();
const emit = defineEmits<{
  failure: [message: string];
  recovered: [message: string];
}>();
const host = shallowRef<HTMLElement>();
const failure = shallowRef("");
let mounted: ChatHandle | undefined,
  active = true,
  ticket = 0,
  failed = false;
async function synchronize() {
  const current = ++ticket;
  if (!host.value || failed) return;
  await nextTick();
  try {
    const library = await import("@openwebui-chat");
    if (!active || ticket !== current || !host.value) return;
    const options = { snapshot: props.snapshot, actions: props.actions };
    if (mounted) mounted.update(options);
    else mounted = library.mountNativeChat(host.value, options);
  } catch (error) {
    if (active && ticket === current) {
      // 先锁定再投影错误；父组件更新error不能再次触发失败的mount。
      failed = true;
      failure.value =
        error instanceof Error ? error.message : "对话界面加载失败";
      mounted?.unmount();
      mounted = undefined;
      emit("failure", failure.value);
    }
  }
}
watch([host, () => props.snapshot, () => props.actions], synchronize);
async function retry() {
  const previous = failure.value;
  failed = false;
  failure.value = "";
  await synchronize();
  if (active && !failed && mounted) emit("recovered", previous);
}
onScopeDispose(() => {
  active = false;
  ticket++;
  mounted?.unmount();
  mounted = undefined;
});
</script>
<template>
  <div class="native-chat-island">
    <div ref="host" v-show="!failure" class="native-chat-surface" />
    <el-result v-if="failure" icon="error" :title="failure">
      <template #extra
        ><el-button @click="retry">重新加载界面</el-button></template
      >
    </el-result>
  </div>
</template>
<style scoped>
.native-chat-island {
  height: 100%;
  min-height: 460px;
  min-width: 0;
}
.native-chat-surface {
  height: 100%;
  min-height: 460px;
}
</style>
