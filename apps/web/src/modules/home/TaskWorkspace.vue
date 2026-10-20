<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { ElMessageBox } from "element-plus";
import { api, type Entity } from "../../api/client";
import { useCollection } from "../../composables/useCollection";
import { useOperation } from "../../composables/useOperation";
import StatusTag from "../../components/common/StatusTag.vue";
const route = useRoute();
const list = useCollection(() => "/tasks");
const op = useOperation();
const selected = ref<Entity | null>(null);
const events = ref<Record<string, any>[]>([]);
async function open(row: Entity) {
  await op.run(async () => {
    const [task, eventList] = await Promise.all([
      api.get<Entity>(`/tasks/${row.id}`),
      api.get(`/tasks/${row.id}/events?after=0`),
    ]);
    selected.value = task;
    events.value = eventList.items;
  });
}
async function cancel(row: Entity) {
  try {
    await ElMessageBox.confirm(`取消“${row.title}”？`, "取消任务", {
      type: "warning",
    });
    await op.run(async () => {
      selected.value = await api.post<Entity>(
        `/tasks/${row.id}/cancellations`,
        {},
      );
      await list.load();
    }, "取消请求已提交");
  } catch {}
}
onMounted(async () => {
  await list.load();
  if (typeof route.query.id === "string")
    await open({ id: route.query.id, revision: 1 });
});
</script>
<template>
  <div class="page-stack">
    <div class="panel toolbar">
      <el-input
        v-model="list.query.value"
        placeholder="任务名称"
        clearable
        @keyup.enter="list.load"
      /><el-button @click="list.load">查询</el-button>
    </div>
    <section class="panel">
      <el-table :data="list.items.value" stripe v-loading="list.loading.value"
        ><el-table-column
          prop="title"
          label="任务"
          min-width="240"
        /><el-table-column label="状态" width="130"
          ><template #default="{ row }"
            ><StatusTag :value="row.status" /></template></el-table-column
        ><el-table-column
          prop="kind"
          label="任务类型"
          width="140"
        /><el-table-column
          prop="engine"
          label="执行引擎"
          width="140"
        /><el-table-column
          prop="created_at"
          label="创建时间"
          width="190"
        /><el-table-column width="150"
          ><template #default="{ row }"
            ><el-button link type="primary" @click="open(row)">详情</el-button
            ><el-button
              v-if="
                ['queued', 'running', 'waiting_review'].includes(row.status)
              "
              link
              type="danger"
              @click="cancel(row)"
              >取消</el-button
            ></template
          ></el-table-column
        ></el-table
      ><el-pagination
        v-model:current-page="list.page.value"
        :total="list.total.value"
        layout="total,prev,pager,next"
        @current-change="list.load"
      />
    </section>
    <el-drawer
      :model-value="!!selected"
      :title="selected?.title ?? '任务详情'"
      size="640px"
      @close="selected = null"
      ><template v-if="selected"
        ><div class="row-actions" style="margin-bottom: 20px">
          <StatusTag :value="selected.status" /><el-button
            @click="open(selected)"
            >刷新</el-button
          >
        </div>
        <el-descriptions :column="2" border
          ><el-descriptions-item label="任务类型">{{
            selected.kind
          }}</el-descriptions-item
          ><el-descriptions-item label="执行引擎">{{
            selected.engine
          }}</el-descriptions-item
          ><el-descriptions-item label="开始时间">{{
            selected.created_at
          }}</el-descriptions-item
          ><el-descriptions-item label="更新时间">{{
            selected.updated_at
          }}</el-descriptions-item></el-descriptions
        ><el-tabs style="margin-top: 20px"
          ><el-tab-pane label="执行记录"
            ><el-timeline
              ><el-timeline-item
                v-for="event in events"
                :key="event.sequence"
                :timestamp="event.created_at"
                >{{ event.type }}
                <pre class="json-preview">{{
                  JSON.stringify(event.data, null, 2)
                }}</pre>
              </el-timeline-item></el-timeline
            ></el-tab-pane
          ><el-tab-pane label="任务结果">
            <pre class="json-preview">{{
              JSON.stringify(selected.outputs ?? selected.error ?? {}, null, 2)
            }}</pre></el-tab-pane
          ><el-tab-pane label="输入">
            <pre class="json-preview">{{
              JSON.stringify(selected.inputs ?? {}, null, 2)
            }}</pre>
          </el-tab-pane></el-tabs
        ></template
      ></el-drawer
    >
  </div>
</template>
