<script setup lang="ts">
import { cloneDto } from "../../utils/cloneDto";
import { computed, reactive, watch } from "vue";
const props = defineProps<{
  concepts: Record<string, any>[];
  entity?: Record<string, any> | null;
  busy: boolean;
  errors: Record<string, string>;
}>();
const emit = defineEmits<{
  submit: [payload: Record<string, unknown>];
  cancel: [];
}>();
const form = reactive({
  name: "",
  type: "",
  properties: {} as Record<string, any>,
});
watch(
  () => props.entity,
  (entity) => {
    Object.assign(form, {
      name: entity?.name ?? "",
      type: entity?.type ?? props.concepts[0]?.name ?? "",
      properties: cloneDto(entity?.properties ?? {}),
    });
  },
  { immediate: true },
);
const fields = computed(() =>
  Object.entries(
    props.concepts.find((x) => x.name === form.type)?.properties ?? {},
  ),
);
function submit() {
  emit("submit", {
    name: form.name,
    ...(props.entity ? {} : { type: form.type }),
    properties: form.properties,
  });
}
</script>
<template>
  <el-form label-position="top" @submit.prevent="submit"
    ><el-form-item label="实体名称" :error="errors.name"
      ><el-input v-model="form.name" required /></el-form-item
    ><el-form-item label="实体类型" :error="errors.type"
      ><el-select v-model="form.type" :disabled="!!entity"
        ><el-option
          v-for="concept in concepts"
          :key="concept.name"
          :value="concept.name"
          :label="concept.name" /></el-select></el-form-item
    ><el-form-item v-for="[key, type] in fields" :key="key" :label="key"
      ><el-input-number
        v-if="type === 'number'"
        v-model="form.properties[key]" /><el-switch
        v-else-if="type === 'boolean'"
        v-model="form.properties[key]" /><el-input
        v-else
        v-model="form.properties[key]"
    /></el-form-item>
    <div class="row-actions" style="justify-content: flex-end">
      <el-button @click="emit('cancel')">取消</el-button
      ><el-button type="primary" native-type="submit" :loading="busy"
        >保存实体</el-button
      >
    </div></el-form
  >
</template>
