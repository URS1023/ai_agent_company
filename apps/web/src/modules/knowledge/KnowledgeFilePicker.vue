<script setup lang="ts">
/** 复用文件管理的授权列表；选择已有原件不上传、不触发索引。 */
import { onMounted, onScopeDispose, ref, shallowRef } from "vue";
import type { Entity, ListResult } from "../../api/client";
import { knowledgeFileError } from "./knowledge";
import { useKnowledgeRequests } from "./useKnowledgeRequests";
const selected = defineModel<string>({ required: true });
const emit = defineEmits<{ name: [value: string] }>();
const transport = useKnowledgeRequests();
const items = ref<Entity[]>([]),
  query = shallowRef(""),
  page = shallowRef(1),
  total = shallowRef(0),
  loading = shallowRef(false),
  error = shallowRef("");
const retained = ref<Entity>();
let sequence = 0;
async function load() {
  const current = ++sequence;
  loading.value = true;
  error.value = "";
  try {
    const result = await transport.send<ListResult<Entity>>(
      `/files?q=${encodeURIComponent(query.value)}&limit=20&offset=${(page.value - 1) * 20}`,
    );
    if (!transport.active() || current !== sequence) return;
    items.value = result.items;
    if (
      retained.value &&
      !items.value.some((item) => item.id === retained.value?.id)
    )
      items.value.unshift(retained.value);
    total.value = result.total;
  } catch (failure) {
    if (transport.active() && current === sequence) {
      items.value = [];
      total.value = 0;
      selected.value = "";
      error.value = failure instanceof Error ? failure.message : "无法读取文件";
    }
  } finally {
    if (transport.active() && current === sequence) loading.value = false;
  }
}
function search(value: string) {
  query.value = value;
  page.value = 1;
  void load();
}
function choose(id: string) {
  retained.value = items.value.find((item) => item.id === id);
  emit("name", retained.value?.name ?? "");
}
async function changePage(value: number) {
  if (loading.value || value === page.value) return;
  page.value = value;
  await load();
}
function disabled(file: Entity) {
  return Boolean(
    knowledgeFileError({
      name: file.name ?? "",
      size: file.size ?? file.size_bytes ?? 0,
    }),
  );
}
onMounted(load);
onScopeDispose(() => {
  items.value = [];
  retained.value = undefined;
});
</script>
<template>
  <div class="knowledge-file-picker">
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-select
      v-model="selected"
      filterable
      remote
      :remote-method="search"
      :loading="loading"
      placeholder="选择平台文件"
      aria-label="平台文件"
      @change="choose"
    >
      <el-option
        v-for="item in items"
        :key="item.id"
        :value="item.id"
        :label="item.name"
        :disabled="disabled(item)"
      />
      <template #footer
        ><el-pagination
          :current-page="page"
          :page-size="20"
          :total="total"
          :disabled="loading || Boolean(error)"
          layout="prev, pager, next"
          @current-change="changePage"
      /></template>
    </el-select>
  </div>
</template>
