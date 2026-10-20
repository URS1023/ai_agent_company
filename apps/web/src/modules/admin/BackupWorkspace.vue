<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api, type Entity, type ListResult } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import StatusTag from "../../components/common/StatusTag.vue";
const op = useOperation();
const records = ref<Entity[]>([]);
const drill = ref<Entity | null>(null);
async function load() {
  await op.run(async () => {
    records.value = (await api.get<ListResult>("/backups")).items as Entity[];
  });
}
async function backup() {
  await op.run(async () => {
    await api.post("/backup-jobs", {});
  }, "备份已完成");
  await load();
}
async function restore(row: Entity) {
  await op.run(async () => {
    drill.value = await api.post<Entity>("/restore-drills", {
      backup_id: row.id,
    });
  }, "恢复演练已完成");
}
onMounted(load);
</script>
<template>
  <div class="page-stack">
    <div class="page-heading">
      <h2>备份管理</h2>
      <el-button type="primary" :loading="op.busy.value" @click="backup"
        >创建备份</el-button
      >
    </div>
    <section class="panel">
      <el-table :data="records" stripe
        ><el-table-column
          prop="name"
          label="备份"
          min-width="220"
        /><el-table-column label="状态" width="120"
          ><template #default="{ row }"
            ><StatusTag :value="row.status" /></template></el-table-column
        ><el-table-column
          prop="size"
          label="大小(字节)"
          width="140"
        /><el-table-column
          prop="created_at"
          label="时间"
          width="190"
        /><el-table-column label="操作" width="140"
          ><template #default="{ row }"
            ><el-button link type="primary" @click="restore(row)"
              >恢复演练</el-button
            ></template
          ></el-table-column
        ></el-table
      >
    </section>
    <section v-if="drill" class="panel">
      <div class="panel-heading">
        <h3>演练结果</h3>
        <StatusTag :value="drill.status" />
      </div>
      <el-descriptions :column="2" border
        ><el-descriptions-item label="完整性">{{
          drill.integrity
        }}</el-descriptions-item
        ><el-descriptions-item label="表数量">{{
          drill.table_count
        }}</el-descriptions-item
        ><el-descriptions-item label="时间">{{
          drill.created_at
        }}</el-descriptions-item></el-descriptions
      >
    </section>
  </div>
</template>
