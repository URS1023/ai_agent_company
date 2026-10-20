<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { api, type ListResult } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import { scopedAssetUrl } from "../../utils/safeContent";
const emit = defineEmits<{ select: [item: Record<string, any>]; close: [] }>();
const op = useOperation();
const items = ref<Record<string, any>[]>([]);
const query = ref("");
const group = ref("");
const groups = ref<Record<string, any>[]>([]);
const supported = ref(true);
const visible = computed(() =>
  items.value.filter(
    (item) =>
      (!supported.value || item.renderer_supported) &&
      (!group.value || item.group === group.value) &&
      (!query.value || `${item.name} ${item.path}`.includes(query.value)),
  ),
);
onMounted(() =>
  op.run(async () => {
    const response = await api.get<
      ListResult & { group_status: Record<string, any>[] }
    >("/report-components/catalog");
    items.value = response.items;
    groups.value = response.group_status;
  }),
);
</script>
<template>
  <div class="page-stack">
    <div class="toolbar">
      <el-input v-model="query" clearable placeholder="搜索组件" />
      <el-select
        v-model="group"
        clearable
        placeholder="全部分类"
        style="width: 160px"
      >
        <el-option
          v-for="item in groups"
          :key="item.id"
          :value="item.id"
          :label="item.name"
        /> </el-select
      ><el-checkbox v-model="supported">仅显示可用</el-checkbox
      ><span class="spacer"></span
      ><span class="muted">{{ visible.length }} / {{ items.length }}</span>
    </div>
    <div class="catalog-items" v-loading="op.busy.value">
      <button
        v-for="item in visible"
        :key="item.id"
        :disabled="!item.renderer_supported"
        :title="item.renderer_supported ? item.path : '暂不可用'"
        @click="emit('select', item)"
      >
        <img
          v-if="item.imageUrl"
          :src="scopedAssetUrl(item.imageUrl, '/')"
          alt=""
          loading="lazy"
        />
        <span v-else class="component-symbol" aria-hidden="true">{{
          item.compType === "JDragBorder"
            ? "▣"
            : item.compType === "JDragDecoration"
              ? "✧"
              : item.group === "chart"
                ? "▥"
                : "▤"
        }}</span>
        <strong>{{ item.name }}</strong>
      </button>
    </div>
  </div>
</template>
<style scoped>
.catalog-items {
  display: grid;
  grid-template-columns: repeat(4, minmax(150px, 1fr));
  gap: 12px;
  max-height: 580px;
  overflow: auto;
}
.catalog-items button {
  display: flex;
  flex-direction: column;
  gap: 10px;
  background: var(--surface);
  border: 1px solid #dce4f1;
  color: var(--text);
  text-align: left;
  padding: 16px;
  align-items: center;
  justify-content: center;
  min-height: 130px;
  border-radius: 2px;
}
.catalog-items img {
  width: 100%;
  height: 80px;
  object-fit: contain;
}
.catalog-items .component-symbol {
  display: grid;
  place-items: center;
  font-size: 42px;
  color: #4395ce;
  height: 70px;
}
.catalog-items button:hover:enabled {
  border-color: var(--brand-500);
  background: #f7faff;
}
.catalog-items button:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.catalog-items span {
  font-size: 12px;
  color: var(--text-muted);
  line-height: 1.6;
}
</style>
