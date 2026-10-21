<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { api, type Entity, type ListResult } from "../../api/client";
import { expectedSamplesError } from "./inspectionPresentation";
const props = defineProps<{
  targetType?: string;
  busy: boolean;
  errors: Record<string, string>;
}>();
const emit = defineEmits<{
  submit: [data: Record<string, unknown>];
  cancel: [];
}>();
const form = reactive({
  name: "",
  target_type: props.targetType ?? "device",
  target_id: "",
  batch_id: "",
  template_id: "",
  expected_samples: 1 as number | undefined,
});
const sampleCountError = computed(() =>
  expectedSamplesError(form.expected_samples),
);
const assets = ref<Entity[]>([]);
const templates = ref<Entity[]>([]);
const batches = ref<Entity[]>([]);
const relevantTemplates = computed(() =>
  templates.value.filter((x) => x.target_type === form.target_type),
);
const template = computed(() =>
  templates.value.find((x) => x.id === form.template_id),
);
async function loadAssets() {
  assets.value = (
    await api.get<ListResult>(
      form.target_type === "device"
        ? "/devices?limit=200"
        : "/products?limit=200",
    )
  ).items as Entity[];
  form.target_id = "";
  form.template_id = "";
}
watch(() => form.target_type, loadAssets);
onMounted(async () => {
  await loadAssets();
  templates.value = (
    await api.get<ListResult>("/inspection-templates?limit=200")
  ).items as Entity[];
  batches.value = (await api.get<ListResult>("/product-batches?limit=200"))
    .items as Entity[];
});
function submit() {
  if (props.busy || sampleCountError.value) return;
  emit("submit", {
    name: form.name,
    target_type: form.target_type,
    [form.target_type === "device" ? "device_id" : "product_id"]:
      form.target_id,
    batch_id: form.batch_id || null,
    template_id: form.template_id,
    template_revision: template.value?.revision,
    expected_samples: form.expected_samples,
  });
}
</script>
<template>
  <el-form label-position="top" @submit.prevent="submit"
    ><el-form-item label="任务名称" :error="errors.name"
      ><el-input v-model="form.name" required maxlength="160"
    /></el-form-item>
    <div class="form-grid">
      <el-form-item label="检测类型"
        ><el-radio-group v-model="form.target_type" :disabled="!!targetType"
          ><el-radio-button value="device">设备质检</el-radio-button
          ><el-radio-button value="product"
            >产品质检</el-radio-button
          ></el-radio-group
        ></el-form-item
      ><el-form-item
        :label="form.target_type === 'device' ? '设备' : '产品'"
        :error="errors.device_id ?? errors.product_id"
        ><el-select v-model="form.target_id" filterable placeholder="请选择"
          ><el-option
            v-for="item in assets"
            :key="item.id"
            :value="item.id"
            :label="`${item.name} · ${item.code}`" /></el-select></el-form-item
      ><el-form-item label="检测标准" :error="errors.template_id"
        ><el-select v-model="form.template_id" filterable placeholder="请选择"
          ><el-option
            v-for="item in relevantTemplates"
            :key="item.id"
            :value="item.id"
            :label="`${item.name} · v${item.revision}`" /></el-select></el-form-item
      ><el-form-item v-if="form.target_type === 'product'" label="批次"
        ><el-select v-model="form.batch_id" clearable filterable
          ><el-option
            v-for="batch in batches"
            :key="batch.id"
            :value="batch.id"
            :label="batch.name ?? batch.external_ref ?? batch.id" /></el-select
      ></el-form-item>
      <el-form-item
        label="计划样本数"
        :error="errors.expected_samples || sampleCountError"
      >
        <el-input-number
          v-model="form.expected_samples"
          :min="1"
          :max="10000"
          :step="1"
          :precision="0"
          step-strictly
          controls-position="right"
        />
      </el-form-item>
    </div>
    <div class="row-actions" style="justify-content: flex-end">
      <el-button @click="emit('cancel')">取消</el-button
      ><el-button
        type="primary"
        native-type="submit"
        :loading="busy"
        :disabled="!form.target_id || !form.template_id || !!sampleCountError"
        >创建任务</el-button
      >
    </div></el-form
  >
</template>
