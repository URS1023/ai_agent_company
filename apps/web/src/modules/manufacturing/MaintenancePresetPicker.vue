<script setup lang="ts">
import { ref, shallowRef, watch } from "vue";
import { api, type Entity, type ListResult } from "../../api/client";

const props = defineProps<{ deviceId: string }>();
const emit = defineEmits<{ select: [value: Record<string, unknown>] }>();
const points = ref<Entity[]>([]);
const selected = shallowRef("");
const loading = shallowRef(false);
const error = shallowRef("");

watch(
  () => props.deviceId,
  async (id, _, cleanup) => {
    let current = true;
    cleanup(() => {
      current = false;
    });
    points.value = [];
    selected.value = "";
    error.value = "";
    loading.value = false;
    if (!id) return;
    loading.value = true;
    try {
      const result = await api.get<ListResult>(
        `/measurement-points?device_id=${encodeURIComponent(id)}&limit=200`,
      );
      if (current) {
        points.value = (result.items as Entity[]).filter(
          (point) => point.datasource_id && point.mapping?.analysis,
        );
        if (points.value.length === 1) {
          selected.value = points.value[0]!.id;
          choose(selected.value);
        }
      }
    } catch (cause) {
      if (current)
        error.value =
          cause instanceof Error ? cause.message : "监测方案读取失败";
    } finally {
      if (current) loading.value = false;
    }
  },
  { immediate: true },
);

function choose(id: string) {
  const point = points.value.find((item) => item.id === id);
  if (point)
    emit("select", {
      ...point.mapping.analysis,
      datasource_id: point.datasource_id,
    });
}
</script>

<template>
  <el-form-item label="监测方案" :error="error" class="full-width">
    <el-select
      v-model="selected"
      :loading="loading"
      :disabled="!deviceId"
      filterable
      placeholder="选择监测方案"
      no-data-text="该设备尚未配置监测方案"
      @change="choose"
    >
      <el-option
        v-for="point in points"
        :key="point.id"
        :label="point.name"
        :value="point.id"
      />
    </el-select>
  </el-form-item>
</template>
