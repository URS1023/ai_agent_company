<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { ElMessageBox } from "element-plus";
import { api, type Entity } from "../../api/client";
import { useCollection } from "../../composables/useCollection";
import { useOperation } from "../../composables/useOperation";
import { useSession } from "../../stores/session";
import AssetForm from "./AssetForm.vue";
import DeviceDetail from "./DeviceDetail.vue";
const props = defineProps<{ config?: string }>();
const type = computed(
  () =>
    (props.config === "products" ? "products" : "devices") as
      "devices" | "products",
);
const label = computed(() => (type.value === "devices" ? "设备" : "产品"));
const details = ref<Entity | null>(null);
const list = useCollection(() => `/${type.value}`);
const op = useOperation();
const session = useSession();
const selected = ref<Entity | null>(null);
const dialog = ref(false);
function edit(entity: Entity | null) {
  selected.value = entity;
  dialog.value = true;
}
async function save(data: Record<string, unknown>) {
  await op.run(async () => {
    if (selected.value)
      await api.patch(`/${type.value}/${selected.value.id}`, {
        ...data,
        revision: selected.value.revision,
      });
    else await api.post(`/${type.value}`, data);
    dialog.value = false;
    await list.load();
  }, "已保存");
}
async function remove(entity: Entity) {
  try {
    await ElMessageBox.confirm(`删除“${entity.name}”？`, "删除确认", {
      type: "warning",
    });
    await op.run(async () => {
      await api.delete(`/${type.value}/${entity.id}`, entity.revision);
      await list.load();
    }, "已删除");
  } catch {}
}
watch(type, () => list.load());
onMounted(list.load);
</script>
<template>
  <div class="page-stack">
    <div class="panel toolbar">
      <el-input
        v-model="list.query.value"
        :placeholder="`${label}名称或编码`"
        clearable
        @keyup.enter="list.load"
      /><el-button
        @click="
          list.page.value = 1;
          list.load();
        "
        >查询</el-button
      ><span class="spacer"></span
      ><el-button
        v-if="session.can(type === 'devices' ? 'm10.write' : 'm11.write')"
        type="primary"
        @click="edit(null)"
        >新增{{ label }}</el-button
      >
    </div>
    <section class="panel">
      <el-table :data="list.items.value" stripe v-loading="list.loading.value"
        ><el-table-column
          prop="code"
          label="编码"
          width="160"
        /><el-table-column prop="name" :label="`${label}名称`" min-width="200"
          ><template #default="{ row }"
            ><el-button
              v-if="type === 'devices'"
              link
              type="primary"
              @click="details = row"
              >{{ row.name }}</el-button
            ><span v-else>{{ row.name }}</span></template
          ></el-table-column
        ><el-table-column
          v-if="type === 'devices'"
          prop="device_type"
          label="设备类型"
          min-width="150"
        /><el-table-column
          v-if="type === 'devices'"
          prop="location"
          label="位置"
          min-width="160"
        /><el-table-column
          prop="updated_at"
          label="更新时间"
          width="185"
        /><el-table-column label="操作" width="155"
          ><template #default="{ row }"
            ><el-button
              v-if="session.can(type === 'devices' ? 'm10.write' : 'm11.write')"
              link
              type="primary"
              @click="edit(row)"
              >编辑</el-button
            ><el-button
              v-if="
                session.can(type === 'devices' ? 'm10.delete' : 'm11.delete')
              "
              link
              type="danger"
              @click="remove(row)"
              >删除</el-button
            ></template
          ></el-table-column
        ></el-table
      ><el-pagination
        v-model:current-page="list.page.value"
        :total="list.total.value"
        :page-size="list.size.value"
        layout="total,prev,pager,next"
        @current-change="list.load"
      />
    </section>
    <el-dialog
      v-model="dialog"
      :title="`${selected ? '编辑' : '新增'}${label}`"
      width="680px"
      destroy-on-close
      ><AssetForm
        :entity="selected"
        :type="type"
        :busy="op.busy.value"
        :errors="op.fields.value"
        @submit="save"
        @cancel="dialog = false" /></el-dialog
    ><el-drawer
      :model-value="!!details"
      :title="details?.name ?? '设备详情'"
      size="900px"
      destroy-on-close
      @close="details = null"
      ><DeviceDetail v-if="details" :device="details"
    /></el-drawer>
  </div>
</template>
