<script setup lang="ts">
import { reactive, watch } from "vue";
import type { Entity } from "../../api/client";
const props = defineProps<{
  entity?: Entity | null;
  type: "devices" | "products";
  busy: boolean;
  errors: Record<string, string>;
}>();
const emit = defineEmits<{
  submit: [value: Record<string, unknown>];
  cancel: [];
}>();
const form = reactive({
  name: "",
  code: "",
  device_type: "",
  location: "",
  description: "",
});
watch(
  () => props.entity,
  (entity) =>
    Object.assign(form, {
      name: entity?.name ?? "",
      code: entity?.code ?? "",
      device_type: entity?.device_type ?? "",
      location: entity?.location ?? "",
      description: entity?.description ?? "",
    }),
  { immediate: true },
);
function submit() {
  const { device_type, location, ...common } = form;
  emit(
    "submit",
    props.type === "devices" ? { ...common, device_type, location } : common,
  );
}
</script>
<template>
  <el-form label-position="top" @submit.prevent="submit"
    ><div class="form-grid">
      <el-form-item label="名称" :error="errors.name"
        ><el-input v-model="form.name" required maxlength="120" /></el-form-item
      ><el-form-item label="编码" :error="errors.code"
        ><el-input v-model="form.code" required maxlength="80" /></el-form-item
      ><el-form-item
        v-if="type === 'devices'"
        label="设备类型"
        :error="errors.device_type"
        ><el-input v-model="form.device_type" maxlength="120" /></el-form-item
      ><el-form-item
        v-if="type === 'devices'"
        label="位置"
        :error="errors.location"
        ><el-input v-model="form.location" maxlength="200" /></el-form-item
      ><el-form-item class="full-width" label="备注"
        ><el-input v-model="form.description" type="textarea" maxlength="1000"
      /></el-form-item>
    </div>
    <div class="row-actions" style="justify-content: flex-end">
      <el-button @click="emit('cancel')">取消</el-button
      ><el-button type="primary" native-type="submit" :loading="busy"
        >保存</el-button
      >
    </div></el-form
  >
</template>
