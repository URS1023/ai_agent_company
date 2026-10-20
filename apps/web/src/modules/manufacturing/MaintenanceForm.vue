<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { api, type Entity, type ListResult } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
const props = defineProps<{ busy: boolean; errors: Record<string, string> }>();
const emit = defineEmits<{
  submit: [data: Record<string, unknown>];
  cancel: [];
}>();
const op = useOperation();
const devices = ref<Entity[]>([]);
const queries = ref<Entity[]>([]);
const queryId = ref("");
const form = reactive({
  name: "",
  device_id: "",
  datasource_id: "",
  sql: "",
  value_field: "",
  time_field: "",
  load_field: "",
  period_field: "",
  bin_width: 10,
  min_samples: 5,
  minimum_coverage: 0.8,
  change_threshold: 0.15,
});
const query = computed(() => queries.value.find((q) => q.id === queryId.value));
const columns = computed(
  () => query.value?.columns ?? query.value?.result?.columns ?? [],
);
function selectQuery() {
  if (query.value) {
    form.datasource_id = query.value.datasource_id;
    form.sql = query.value.sql;
  }
}
onMounted(() =>
  op.run(async () => {
    const [deviceList, queryList] = await Promise.all([
      api.get<ListResult>("/devices?limit=200"),
      api.get<ListResult>("/queries?limit=200"),
    ]);
    devices.value = deviceList.items as Entity[];
    queries.value = queryList.items as Entity[];
  }),
);
function submit() {
  emit("submit", { ...form, parameters: query.value?.parameters ?? {} });
}
</script>
<template>
  <el-form
    label-position="top"
    @submit.prevent="submit"
    v-loading="op.busy.value"
    ><div class="form-grid">
      <el-form-item label="分析名称" :error="errors.name"
        ><el-input v-model="form.name" required maxlength="160" /></el-form-item
      ><el-form-item label="设备" :error="errors.device_id"
        ><el-select v-model="form.device_id" filterable
          ><el-option
            v-for="device in devices"
            :key="device.id"
            :value="device.id"
            :label="device.name" /></el-select></el-form-item
      ><el-form-item
        label="数据查询"
        class="full-width"
        :error="errors.datasource_id ?? errors.sql"
        ><el-select v-model="queryId" filterable @change="selectQuery"
          ><el-option
            v-for="item in queries"
            :key="item.id"
            :value="item.id"
            :label="
              item.name ?? item.sql ?? item.id
            " /></el-select></el-form-item
      ><el-form-item
        v-for="field in [
          { key: 'value_field', label: '监测值' },
          { key: 'time_field', label: '采样时间' },
          { key: 'load_field', label: '工况负载' },
          { key: 'period_field', label: '对比阶段' },
        ]"
        :key="field.key"
        :label="field.label"
        :error="errors[field.key]"
        ><el-select
          v-model="(form as any)[field.key]"
          filterable
          allow-create
          clearable
          ><el-option
            v-for="column in columns"
            :key="String(column)"
            :value="String(column)"
            :label="String(column)" /></el-select
      ></el-form-item>
    </div>
    <el-collapse
      ><el-collapse-item title="分析参数" name="parameters"
        ><div class="form-grid">
          <el-form-item label="负载分组间隔"
            ><el-input-number
              v-model="form.bin_width"
              :min="0.01"
              :step="0.01" /></el-form-item
          ><el-form-item label="每组最少样本"
            ><el-input-number
              v-model="form.min_samples"
              :min="2"
              :max="1000" /></el-form-item
          ><el-form-item label="最低工况覆盖率"
            ><el-input-number
              v-model="form.minimum_coverage"
              :min="0.01"
              :max="1"
              :step="0.01" /></el-form-item
          ><el-form-item label="变化阈值"
            ><el-input-number
              v-model="form.change_threshold"
              :min="0"
              :max="10"
              :step="0.01"
          /></el-form-item></div></el-collapse-item
    ></el-collapse>
    <div
      class="row-actions"
      style="justify-content: flex-end; margin-top: 20px"
    >
      <el-button @click="emit('cancel')">取消</el-button
      ><el-button
        type="primary"
        native-type="submit"
        :loading="busy"
        :disabled="
          !form.device_id || !queryId || !form.value_field || !form.load_field
        "
        >发起分析</el-button
      >
    </div></el-form
  >
</template>
