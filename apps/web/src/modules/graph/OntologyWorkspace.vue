<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api, type Entity, type ListResult } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import { useSession } from "../../stores/session";
import OntologyForm from "./OntologyForm.vue";
const session = useSession();
const op = useOperation();
const records = ref<Entity[]>([]);
const dialog = ref(false);
const selected = ref<Entity | null>(null);
async function load() {
  await op.run(async () => {
    records.value = (await api.get<ListResult>("/ontologies"))
      .items as Entity[];
  });
}
function edit(row: Entity | null) {
  selected.value = row;
  dialog.value = true;
}
async function save(data: Record<string, unknown>) {
  await op.run(async () => {
    if (selected.value)
      await api.patch(`/ontologies/${selected.value.id}`, {
        ...data,
        revision: selected.value.revision,
      });
    else await api.post("/ontologies", data);
    dialog.value = false;
  }, "本体已保存");
  await load();
}
onMounted(load);
</script>
<template>
  <div class="page-stack">
    <div class="page-heading">
      <h2>本体管理</h2>
      <el-button
        v-if="session.can('m09.write')"
        type="primary"
        @click="edit(null)"
        >新增本体</el-button
      >
    </div>
    <section class="panel">
      <el-table :data="records" stripe v-loading="op.busy.value"
        ><el-table-column
          prop="name"
          label="名称"
          min-width="200"
        /><el-table-column label="实体类型" min-width="240"
          ><template #default="{ row }">{{
            row.concepts?.map((item: any) => item.name).join("、")
          }}</template></el-table-column
        ><el-table-column label="关系类型" min-width="200"
          ><template #default="{ row }">{{
            row.relations?.map((item: any) => item.name).join("、")
          }}</template></el-table-column
        ><el-table-column
          prop="revision"
          label="版本"
          width="90"
        /><el-table-column label="操作" width="100"
          ><template #default="{ row }"
            ><el-button
              v-if="session.can('m09.write')"
              link
              type="primary"
              @click="edit(row)"
              >编辑</el-button
            ></template
          ></el-table-column
        ></el-table
      >
    </section>
    <el-dialog
      v-model="dialog"
      :title="selected ? '编辑本体' : '新增本体'"
      width="850px"
      destroy-on-close
      ><OntologyForm
        :entity="selected"
        :busy="op.busy.value"
        :errors="op.fields.value"
        @submit="save"
        @cancel="dialog = false"
    /></el-dialog>
  </div>
</template>
