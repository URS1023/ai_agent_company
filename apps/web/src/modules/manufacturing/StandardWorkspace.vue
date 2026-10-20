<script setup lang="ts">
import { cloneDto } from "../../utils/cloneDto";
import { onMounted, reactive, ref } from "vue";
import { ElMessageBox } from "element-plus";
import { useSession } from "../../stores/session";
import { api, type Entity } from "../../api/client";
import { useCollection } from "../../composables/useCollection";
import { useOperation } from "../../composables/useOperation";
import MetricEditor, { type InspectionMetric } from "./MetricEditor.vue";
const session = useSession();
const list = useCollection(() => "/inspection-templates");
const op = useOperation();
const dialog = ref(false);
const selected = ref<Entity | null>(null);
const form = reactive({ name: "", target_type: "device", description: "" });
const metrics = ref<InspectionMetric[]>([]);
function edit(row: Entity | null) {
  selected.value = row;
  Object.assign(form, {
    name: row?.name ?? "",
    target_type: row?.target_type ?? "device",
    description: row?.description ?? "",
  });
  metrics.value = cloneDto(row?.metrics ?? []);
  dialog.value = true;
}
async function save() {
  await op.run(async () => {
    const payload = {
      ...form,
      metrics: metrics.value.map((m) => ({
        ...m,
        standard_min: m.standard_min === "" ? null : m.standard_min,
        standard_max: m.standard_max === "" ? null : m.standard_max,
      })),
    };
    if (selected.value)
      await api.patch(`/inspection-templates/${selected.value.id}`, {
        ...payload,
        revision: selected.value.revision,
      });
    else await api.post("/inspection-templates", payload);
    dialog.value = false;
    await list.load();
  }, "标准已保存");
}
async function remove(row: Entity) {
  try {
    await ElMessageBox.confirm(`删除“${row.name}”？`, "删除确认", {
      type: "warning",
    });
    await op.run(async () => {
      await api.delete(`/inspection-templates/${row.id}`, row.revision);
      await list.load();
    }, "已删除");
  } catch {}
}
onMounted(list.load);
</script>
<template>
  <div class="page-stack">
    <div class="panel toolbar">
      <el-input
        v-model="list.query.value"
        placeholder="标准名称"
        clearable
        @keyup.enter="list.load"
      /><el-button @click="list.load">查询</el-button
      ><span class="spacer"></span
      ><el-button
        v-if="session.can('m11.write')"
        type="primary"
        @click="edit(null)"
        >新增标准</el-button
      >
    </div>
    <section class="panel">
      <el-table :data="list.items.value" stripe v-loading="list.loading.value"
        ><el-table-column
          prop="name"
          label="标准名称"
          min-width="220"
        /><el-table-column label="检测对象" width="130"
          ><template #default="{ row }">{{
            row.target_type === "device" ? "设备" : "产品"
          }}</template></el-table-column
        ><el-table-column label="检测项目" width="110"
          ><template #default="{ row }">{{
            row.metrics?.length ?? 0
          }}</template></el-table-column
        ><el-table-column
          prop="revision"
          label="版本"
          width="80"
        /><el-table-column
          prop="updated_at"
          label="更新时间"
          min-width="180"
        /><el-table-column label="操作" width="150"
          ><template #default="{ row }"
            ><el-button
              v-if="session.can('m11.write')"
              link
              type="primary"
              @click="edit(row)"
              >编辑</el-button
            ><el-button
              v-if="session.can('m11.delete')"
              link
              type="danger"
              @click="remove(row)"
              >删除</el-button
            ></template
          ></el-table-column
        ></el-table
      >
    </section>
    <el-dialog
      v-model="dialog"
      :title="selected ? '编辑标准' : '新增标准'"
      width="1100px"
      destroy-on-close
      ><el-form label-position="top" @submit.prevent="save"
        ><div class="form-grid">
          <el-form-item label="标准名称" :error="op.fields.value.name"
            ><el-input
              v-model="form.name"
              required
              maxlength="160" /></el-form-item
          ><el-form-item label="检测对象"
            ><el-select v-model="form.target_type"
              ><el-option label="设备" value="device" /><el-option
                label="产品"
                value="product" /></el-select
          ></el-form-item>
        </div>
        <el-form-item label="检测项目" :error="op.fields.value.metrics"
          ><MetricEditor v-model="metrics" /></el-form-item
        ><el-form-item label="备注"
          ><el-input
            v-model="form.description"
            type="textarea"
            maxlength="1000"
        /></el-form-item>
        <div class="row-actions" style="justify-content: flex-end">
          <el-button @click="dialog = false">取消</el-button
          ><el-button
            type="primary"
            native-type="submit"
            :loading="op.busy.value"
            :disabled="!metrics.length"
            >保存</el-button
          >
        </div></el-form
      ></el-dialog
    >
  </div>
</template>
