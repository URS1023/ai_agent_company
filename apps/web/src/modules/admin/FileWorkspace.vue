<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useSession } from "../../stores/session";
import { ElMessageBox } from "element-plus";
import { api, request, type Entity } from "../../api/client";
import { useCollection } from "../../composables/useCollection";
import { useOperation } from "../../composables/useOperation";
const session = useSession();
const list = useCollection(() => "/files");
const op = useOperation();
const input = ref<HTMLInputElement | null>(null);
const detail = ref<Entity | null>(null);
async function upload(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0];
  if (!file) return;
  await op.run(async () => {
    const data = new FormData();
    data.append("file", file);
    await request("/files", { method: "POST", body: data });
    await list.load();
    if (input.value) input.value.value = "";
  }, "文件已上传");
}
async function download(row: Entity) {
  await op.run(async () => {
    const link = await api.post<{ url: string }>(
      `/files/${row.id}/download-links`,
      {},
    );
    const anchor = document.createElement("a");
    anchor.href = link.url;
    anchor.download = row.name ?? "文件";
    anchor.click();
  });
}
async function remove(row: Entity) {
  await op.run(async () => {
    const check = await api.post(`/files/${row.id}/deletion-previews`, {});
    if (!check.can_delete) {
      detail.value = { ...row, references: check.references };
      throw new Error("文件仍被引用，请查看引用记录");
    }
    await ElMessageBox.confirm(`删除“${row.name}”？`, "删除确认", {
      type: "warning",
    });
    await api.delete(`/files/${row.id}`, row.revision);
    await list.load();
  }, "已删除");
}
function size(bytes: number) {
  return bytes < 1024
    ? `${bytes} B`
    : bytes < 1048576
      ? `${(bytes / 1024).toFixed(1)} KB`
      : `${(bytes / 1048576).toFixed(1)} MB`;
}
onMounted(list.load);
</script>
<template>
  <div class="page-stack">
    <div class="panel toolbar">
      <el-input
        v-model="list.query.value"
        placeholder="文件名称"
        clearable
        @keyup.enter="list.load"
      /><el-button @click="list.load">查询</el-button
      ><span class="spacer"></span
      ><input ref="input" type="file" hidden @change="upload" /><el-button
        v-if="session.can('m17.write')"
        type="primary"
        :loading="op.busy.value"
        @click="input?.click()"
        >上传文件</el-button
      >
    </div>
    <section class="panel">
      <el-table :data="list.items.value" stripe v-loading="list.loading.value"
        ><el-table-column
          prop="name"
          label="文件名称"
          min-width="260"
        /><el-table-column
          prop="content_type"
          label="文件类型"
          min-width="200"
          show-overflow-tooltip
        /><el-table-column label="大小" width="120"
          ><template #default="{ row }">{{
            size(row.size ?? row.size_bytes ?? 0)
          }}</template></el-table-column
        ><el-table-column
          prop="created_at"
          label="上传时间"
          width="190"
        /><el-table-column label="操作" width="210"
          ><template #default="{ row }"
            ><el-button link type="primary" @click="detail = row"
              >详情</el-button
            ><el-button link type="primary" @click="download(row)"
              >下载</el-button
            ><el-button
              v-if="session.can('m17.delete')"
              link
              type="danger"
              @click="remove(row)"
              >删除</el-button
            ></template
          ></el-table-column
        ></el-table
      >
    </section>
    <el-drawer
      :model-value="!!detail"
      :title="detail?.name ?? '文件详情'"
      size="600px"
      @close="detail = null"
      ><el-descriptions :column="1" border
        ><el-descriptions-item
          v-for="(value, key) in detail"
          :key="key"
          :label="String(key)"
          >{{
            typeof value === "object" ? JSON.stringify(value) : value
          }}</el-descriptions-item
        ></el-descriptions
      ></el-drawer
    >
  </div>
</template>
