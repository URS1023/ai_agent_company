<script setup lang="ts">
/** 版本恢复交给服务端进行修订校验，恢复结果作为新的当前版本。 */
import { ref } from "vue";
import { ElMessageBox } from "element-plus";
import { api, type Entity, type ListResult } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import { useSession } from "../../stores/session";
const props = defineProps<{ report: Entity }>();
const emit = defineEmits<{ restored: [report: Entity] }>();
const session = useSession();
const op = useOperation();
const open = ref(false);
const items = ref<Record<string, any>[]>([]);
async function show() {
  await op.run(async () => {
    const response = await api.get<ListResult>(`/reports/${props.report.id}/revisions`);
    items.value = response.items.map(item=>({...item,revision:item.number,name:item.snapshot?.name,display_time:formatTime(item.created_at)}));
    open.value = true;
  });
}
function formatTime(value:string) {
  const date=new Date(value);
  return Number.isNaN(date.getTime())?'—':date.toLocaleString('zh-CN',{hour12:false});
}
async function restore(version: Record<string, any>) {
  try {
    await ElMessageBox.confirm(`恢复到版本 ${version.revision}？`, "恢复确认", {
      type: "warning",
    });
    await op.run(async () => {
      const report = await api.post<Entity>(
        `/reports/${props.report.id}/restorations`,
        { revision: props.report.revision, target_revision: version.revision },
      );
      emit("restored", report);
      open.value = false;
    }, "版本已恢复");
  } catch {}
}
</script>
<template>
  <el-button @click="show">历史版本</el-button>
  <el-dialog v-model="open" title="历史版本" width="700px">
    <el-table :data="items" max-height="500"
      ><el-table-column
        prop="revision"
        label="版本"
        width="90"
      /><el-table-column prop="name" label="名称" /><el-table-column
        prop="display_time"
        label="保存时间"
        width="210"
      /><el-table-column
        v-if="session.can('m07.write')"
        label="操作"
        width="100"
        ><template #default="{ row }"
          ><el-button
            link
            type="primary"
            :disabled="row.revision === report.revision"
            @click="restore(row)"
            >恢复</el-button
          ></template
        ></el-table-column
      ></el-table
    >
  </el-dialog>
</template>
