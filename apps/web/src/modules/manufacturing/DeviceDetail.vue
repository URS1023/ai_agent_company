<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { ElMessageBox } from "element-plus";
import { api, type Entity, type ListResult } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import { useSession } from "../../stores/session";
import ResultTable from "../../components/common/ResultTable.vue";
import PreparationQuerySelect from "../data/PreparationQuerySelect.vue";
import { usePreparationQueries } from "../data/usePreparationQueries";
const props = defineProps<{ device: Entity }>();
const session = useSession();
const op = useOperation();
const points = ref<Entity[]>([]);
const catalog = usePreparationQueries();
const queries = catalog.queries;
const telemetry = ref<any[]>([]);
const dialog = ref(false);
const selected = ref<Entity | null>(null);
const queryId = catalog.queryId;
const form = reactive({ name: "", code: "", unit: "" });
const sourceQuery = catalog.query;
async function load() {
  await op.run(async () => {
    const pointList = await api.get<ListResult>(
      `/measurement-points?device_id=${props.device.id}&limit=200`,
    );
    points.value = pointList.items.filter(
      (point) => point.device_id === props.device.id,
    ) as Entity[];
    if (session.can("m06.read")) await catalog.load();
  });
}
function edit(record: Entity | null) {
  selected.value = record;
  Object.assign(form, {
    name: record?.name ?? "",
    code: record?.code ?? "",
    unit: record?.unit ?? "",
  });
  queryId.value =
    queries.value.find(
      (query) =>
        query.sql === record?.mapping?.sql &&
        query.datasource_id === record?.datasource_id,
    )?.id ?? "";
  dialog.value = true;
}
async function save() {
  await op.run(async () => {
    const query = sourceQuery.value;
    const payload = {
      ...form,
      device_id: props.device.id,
      datasource_id:
        query?.datasource_id ?? selected.value?.datasource_id ?? null,
      mapping: query
        ? { sql: query.sql, parameters: query.parameters ?? {} }
        : (selected.value?.mapping ?? {}),
    };
    if (selected.value)
      await api.patch(`/measurement-points/${selected.value.id}`, {
        ...payload,
        revision: selected.value.revision,
      });
    else await api.post("/measurement-points", payload);
    dialog.value = false;
  }, "测点已保存");
  await load();
}
async function remove(record: Entity) {
  try {
    await ElMessageBox.confirm(`删除测点“${record.name}”？`, "删除确认", {
      type: "warning",
    });
    await op.run(
      () => api.delete(`/measurement-points/${record.id}`, record.revision),
      "测点已删除",
    );
    await load();
  } catch {}
}
async function read() {
  await op.run(async () => {
    telemetry.value = (
      await api.get<ListResult>(
        `/devices/${props.device.id}/telemetry?limit=500`,
      )
    ).items;
  });
}
onMounted(load);
</script>
<template>
  <div class="page-stack">
    <el-descriptions :column="2" border
      ><el-descriptions-item label="设备编码">{{
        device.code
      }}</el-descriptions-item
      ><el-descriptions-item label="设备类型">{{
        device.device_type
      }}</el-descriptions-item
      ><el-descriptions-item label="位置">{{
        device.location
      }}</el-descriptions-item
      ><el-descriptions-item label="更新时间">{{
        device.updated_at
      }}</el-descriptions-item></el-descriptions
    >
    <div class="panel-heading">
      <h3>监测点</h3>
      <el-button
        v-if="session.can('m10.write')"
        type="primary"
        @click="edit(null)"
        >新增测点</el-button
      >
    </div>
    <el-table :data="points" stripe v-loading="op.busy.value"
      ><el-table-column prop="name" label="测点名称" /><el-table-column
        prop="unit"
        label="单位"
        width="85"
      /><el-table-column label="数据映射" min-width="160"
        ><template #default="{ row }">{{
          row.mapping?.sql ? "已配置" : "未配置"
        }}</template></el-table-column
      ><el-table-column width="150"
        ><template #default="{ row }"
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
    <div class="panel-heading">
      <h3>监测数据</h3>
      <el-button
        v-if="session.can('m06.read')"
        :loading="op.busy.value"
        :disabled="!points.length"
        @click="read"
        >读取测点数据</el-button
      >
    </div>
    <section v-for="point in telemetry" :key="point.point_id">
      <h3>{{ point.name }}</h3>
      <ResultTable :rows="point.rows" :columns="point.columns" />
    </section>
    <el-empty
      v-if="!telemetry.length"
      description="暂无读取数据"
      :image-size="60"
    /><el-dialog
      v-model="dialog"
      :title="selected ? '编辑测点' : '新增测点'"
      width="650px"
      append-to-body
      destroy-on-close
      ><el-form label-position="top" @submit.prevent="save"
        ><div class="form-grid">
          <el-form-item label="测点名称" :error="op.fields.value.name"
            ><el-input v-model="form.name" required /></el-form-item
          ><el-form-item label="测点编码"
            ><el-input v-model="form.code" /></el-form-item
          ><el-form-item label="单位"
            ><el-input v-model="form.unit" /></el-form-item
          ><el-form-item label="数据查询"
            ><PreparationQuerySelect :catalog="catalog"
          /></el-form-item>
        </div>
        <el-button
          type="primary"
          native-type="submit"
          :loading="op.busy.value"
          :disabled="
            catalog.selecting.value ||
            catalog.loading.value ||
            Boolean(catalog.error.value)
          "
          >保存测点</el-button
        ></el-form
      ></el-dialog
    >
  </div>
</template>
