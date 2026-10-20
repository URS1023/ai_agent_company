<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessageBox } from "element-plus";
import { api, type Entity, type ListResult } from "../../api/client";
import { useCollection } from "../../composables/useCollection";
import { useOperation } from "../../composables/useOperation";
import { useSession } from "../../stores/session";
const emit = defineEmits<{ incident: [] }>();
const session = useSession();
const list = useCollection(() => "/alert-rules");
const op = useOperation();
const devices = ref<Entity[]>([]);
const queries = ref<Entity[]>([]);
const selected = ref<Entity | null>(null);
const dialog = ref(false);
const evaluation = ref<Entity | null>(null);
const result = ref<any>(null);
const form = reactive({
  name: "",
  code: "",
  device_id: "",
  operator: "gt",
  threshold: "",
  unit: "",
  dedupe_minutes: 60,
  status: "active",
});
const source = reactive({
  query_id: "",
  value_field: "",
  time_field: "",
  unit_field: "",
  create_incident: true,
});
const columns = computed(() => {
  const query = queries.value.find((item) => item.id === source.query_id);
  return query?.columns ?? query?.result?.columns ?? [];
});
function edit(row: Entity | null) {
  selected.value = row;
  Object.assign(form, {
    name: row?.name ?? "",
    code: row?.code ?? "",
    device_id: row?.device_id ?? "",
    operator: row?.operator ?? "gt",
    threshold: row?.threshold ?? "",
    unit: row?.unit ?? "",
    dedupe_minutes: row?.dedupe_minutes ?? 60,
    status: row?.status ?? "active",
  });
  dialog.value = true;
}
async function save() {
  await op.run(async () => {
    if (selected.value)
      await api.patch(`/alert-rules/${selected.value.id}`, {
        ...form,
        revision: selected.value.revision,
      });
    else await api.post("/alert-rules", form);
    dialog.value = false;
  }, "规则已保存");
  await list.load();
}
async function remove(row: Entity) {
  try {
    await ElMessageBox.confirm(`删除规则“${row.name}”？`, "删除确认", {
      type: "warning",
    });
    await op.run(
      () => api.delete(`/alert-rules/${row.id}`, row.revision),
      "规则已删除",
    );
    await list.load();
  } catch {}
}
async function evaluate() {
  if (!evaluation.value) return;
  await op.run(async () => {
    result.value = await api.post(
      `/alert-rules/${evaluation.value!.id}/source-evaluations`,
      {
        ...source,
        unit_field: source.unit_field || null,
        unit: evaluation.value!.unit,
      },
    );
    evaluation.value = null;
  }, "规则评估已完成");
}
onMounted(async () => {
  await list.load();
  await op.run(async () => {
    devices.value = (await api.get<ListResult>("/devices?limit=200"))
      .items as Entity[];
    if (session.can("m06.read"))
      queries.value = (await api.get<ListResult>("/queries?limit=200"))
        .items as Entity[];
  });
});
</script>
<template>
  <div class="page-stack">
    <section class="panel toolbar">
      <el-input
        v-model="list.query.value"
        placeholder="规则名称"
        clearable
        @keyup.enter="list.load"
      /><el-button @click="list.load">查询</el-button
      ><span class="spacer"></span
      ><el-button
        v-if="session.can('m10.write')"
        type="primary"
        @click="edit(null)"
        >新增预警规则</el-button
      >
    </section>
    <section class="panel">
      <el-table :data="list.items.value" stripe v-loading="list.loading.value"
        ><el-table-column
          prop="name"
          label="规则名称"
          min-width="210"
        /><el-table-column label="设备" min-width="180"
          ><template #default="{ row }">{{
            devices.find((device) => device.id === row.device_id)?.name ??
            row.device_id
          }}</template></el-table-column
        ><el-table-column label="阈值" min-width="150"
          ><template #default="{ row }"
            >{{
              { gt: ">", gte: "≥", lt: "<", lte: "≤" }[row.operator as "gt"]
            }}
            {{ row.threshold }} {{ row.unit }}</template
          ></el-table-column
        ><el-table-column
          prop="dedupe_minutes"
          label="合并间隔(分钟)"
          width="145"
        /><el-table-column label="操作" width="220"
          ><template #default="{ row }"
            ><el-button
              v-if="session.can('m10.write') && session.can('m06.read')"
              link
              type="primary"
              @click="evaluation = row"
              >评估</el-button
            ><el-button
              v-if="session.can('m10.write')"
              link
              type="primary"
              @click="edit(row)"
              >编辑</el-button
            ><el-button
              v-if="session.can('m10.delete')"
              link
              type="danger"
              @click="remove(row)"
              >删除</el-button
            ></template
          ></el-table-column
        ></el-table
      >
    </section>
    <section v-if="result" class="panel">
      <div class="panel-heading">
        <h3>{{ result.matched ? "命中预警规则" : "未命中预警规则" }}</h3>
        <el-button
          v-if="result.incident"
          type="primary"
          @click="emit('incident')"
          >查看预警</el-button
        >
      </div>
      <el-descriptions :column="3" border
        ><el-descriptions-item label="实测"
          >{{ result.value }} {{ result.unit }}</el-descriptions-item
        ><el-descriptions-item label="阈值"
          >{{ result.threshold }} {{ result.unit }}</el-descriptions-item
        ><el-descriptions-item label="处置记录">{{
          result.incident ? "已生成" : "未生成"
        }}</el-descriptions-item></el-descriptions
      >
    </section>
    <el-dialog
      v-model="dialog"
      :title="selected ? '编辑预警规则' : '新增预警规则'"
      width="720px"
      ><el-form label-position="top" @submit.prevent="save"
        ><div class="form-grid">
          <el-form-item label="规则名称" :error="op.fields.value.name"
            ><el-input v-model="form.name" required /></el-form-item
          ><el-form-item label="设备" :error="op.fields.value.device_id"
            ><el-select v-model="form.device_id" filterable
              ><el-option
                v-for="device in devices"
                :key="device.id"
                :value="device.id"
                :label="device.name" /></el-select></el-form-item
          ><el-form-item label="比较方式"
            ><el-select v-model="form.operator"
              ><el-option label="大于" value="gt" /><el-option
                label="大于等于"
                value="gte" /><el-option label="小于" value="lt" /><el-option
                label="小于等于"
                value="lte" /></el-select></el-form-item
          ><el-form-item label="阈值" :error="op.fields.value.threshold"
            ><el-input
              v-model="form.threshold"
              type="number"
              step="any"
              required /></el-form-item
          ><el-form-item label="单位"
            ><el-input v-model="form.unit" /></el-form-item
          ><el-form-item label="合并间隔(分钟)"
            ><el-input-number
              v-model="form.dedupe_minutes"
              :min="1"
              :max="10080"
              :precision="0"
          /></el-form-item>
        </div>
        <el-button
          type="primary"
          native-type="submit"
          :loading="op.busy.value"
          :disabled="!form.device_id"
          >保存规则</el-button
        ></el-form
      ></el-dialog
    ><el-dialog
      :model-value="!!evaluation"
      :title="evaluation?.name ?? '评估预警'"
      width="720px"
      @close="evaluation = null"
      ><el-form label-position="top" @submit.prevent="evaluate"
        ><el-form-item label="数据查询"
          ><el-select v-model="source.query_id" filterable
            ><el-option
              v-for="query in queries"
              :key="query.id"
              :value="query.id"
              :label="query.name ?? query.sql" /></el-select
        ></el-form-item>
        <div class="form-grid">
          <el-form-item
            v-for="field in [
              { key: 'value_field', label: '监测值字段' },
              { key: 'time_field', label: '采样时间字段' },
              { key: 'unit_field', label: '单位字段' },
            ]"
            :key="field.key"
            :label="field.label"
            ><el-select
              v-model="(source as any)[field.key]"
              clearable
              filterable
              ><el-option
                v-for="column in columns"
                :key="column"
                :value="column"
                :label="column" /></el-select
          ></el-form-item>
        </div>
        <el-checkbox v-model="source.create_incident"
          >命中后生成预警</el-checkbox
        >
        <div style="text-align: right">
          <el-button
            type="primary"
            native-type="submit"
            :loading="op.busy.value"
            :disabled="
              !source.query_id || !source.value_field || !source.time_field
            "
            >执行评估</el-button
          >
        </div></el-form
      ></el-dialog
    >
  </div>
</template>
