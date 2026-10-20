<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { api, type Entity, type ListResult } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import { useSession } from "../../stores/session";
import StatusTag from "../../components/common/StatusTag.vue";
import ResultTable from "../../components/common/ResultTable.vue";
const session = useSession();
const op = useOperation();
const records = ref<Entity[]>([]);
const ontologies = ref<Entity[]>([]);
const files = ref<Entity[]>([]);
const graphs = ref<Entity[]>([]);
const dialog = ref(false);
const detail = ref<Entity | null>(null);
const graphId = ref("");
const form = reactive({
  name: "",
  ontology_id: "",
  file_id: "",
  entity_type: "",
  name_field: "name",
  field_mapping: {} as Record<string, string>,
});
const ontology = computed(() =>
  ontologies.value.find((item) => item.id === form.ontology_id),
);
const properties = computed(() =>
  Object.keys(
    ontology.value?.concepts?.find(
      (concept: any) => concept.name === form.entity_type,
    )?.properties ?? {},
  ),
);
const relevantGraphs = computed(() =>
  graphs.value.filter(
    (graph) => graph.ontology_id === detail.value?.ontology_id,
  ),
);
async function load() {
  await op.run(async () => {
    const [jobs, schemas, documents, graphList] = await Promise.all([
      api.get<ListResult>("/extraction-jobs"),
      api.get<ListResult>("/ontologies"),
      session.can("m17.read")
        ? api.get<ListResult>("/files?limit=200")
        : Promise.resolve({ items: [], total: 0 }),
      api.get<ListResult>("/graphs"),
    ]);
    records.value = jobs.items as Entity[];
    ontologies.value = schemas.items as Entity[];
    files.value = documents.items.filter((file) =>
      /\.(csv|json)$/i.test(file.name),
    ) as Entity[];
    graphs.value = graphList.items as Entity[];
  });
}
async function create() {
  await op.run(async () => {
    detail.value = await api.post<Entity>("/extraction-jobs", {
      ...form,
      mode: "structured",
      field_mapping: Object.fromEntries(
        Object.entries(form.field_mapping).filter(
          ([key, value]) => properties.value.includes(key) && value,
        ),
      ),
    });
    dialog.value = false;
  }, "抽取已完成，等待复核");
  await load();
}
async function review(decision: string) {
  if (!detail.value) return;
  await op.run(async () => {
    detail.value = await api.post<Entity>(
      `/extraction-jobs/${detail.value!.id}/reviews`,
      { revision: detail.value!.revision, decision, graph_id: graphId.value },
    );
  }, "复核已保存");
  await load();
}
onMounted(load);
</script>
<template>
  <div class="page-stack">
    <div class="page-heading">
      <h2>抽取任务</h2>
      <el-button
        v-if="session.can('m09.write')"
        type="primary"
        @click="dialog = true"
        >新建抽取</el-button
      >
    </div>
    <section class="panel">
      <el-table :data="records" stripe v-loading="op.busy.value"
        ><el-table-column
          prop="name"
          label="任务名称"
          min-width="220"
        /><el-table-column label="候选实体" width="120"
          ><template #default="{ row }">{{
            row.candidates?.length ?? 0
          }}</template></el-table-column
        ><el-table-column label="状态" width="130"
          ><template #default="{ row }"
            ><StatusTag :value="row.status" /></template></el-table-column
        ><el-table-column
          prop="created_at"
          label="创建时间"
          width="190"
        /><el-table-column width="90"
          ><template #default="{ row }"
            ><el-button link type="primary" @click="detail = row"
              >复核</el-button
            ></template
          ></el-table-column
        ></el-table
      >
    </section>
    <el-dialog
      v-model="dialog"
      title="新建抽取任务"
      width="760px"
      destroy-on-close
      ><el-form label-position="top" @submit.prevent="create"
        ><div class="form-grid">
          <el-form-item label="任务名称"
            ><el-input v-model="form.name" required /></el-form-item
          ><el-form-item label="本体"
            ><el-select
              v-model="form.ontology_id"
              @change="
                form.entity_type = '';
                form.field_mapping = {};
              "
              ><el-option
                v-for="item in ontologies"
                :key="item.id"
                :value="item.id"
                :label="item.name" /></el-select></el-form-item
          ><el-form-item label="源文件"
            ><el-select v-model="form.file_id" filterable
              ><el-option
                v-for="file in files"
                :key="file.id"
                :value="file.id"
                :label="file.name" /></el-select></el-form-item
          ><el-form-item label="实体类型"
            ><el-select v-model="form.entity_type"
              ><el-option
                v-for="concept in ontology?.concepts ?? []"
                :key="concept.name"
                :value="concept.name"
                :label="concept.name" /></el-select></el-form-item
          ><el-form-item label="名称字段"
            ><el-input v-model="form.name_field" required /></el-form-item
          ><el-form-item
            v-for="property in properties"
            :key="property"
            :label="`${property}字段`"
            ><el-input v-model="form.field_mapping[property]"
          /></el-form-item>
        </div>
        <el-button
          type="primary"
          native-type="submit"
          :loading="op.busy.value"
          :disabled="!form.file_id || !form.entity_type"
          >抽取实体</el-button
        ></el-form
      ></el-dialog
    ><el-drawer
      :model-value="!!detail"
      :title="detail?.name ?? '抽取复核'"
      size="850px"
      @close="detail = null"
      ><template v-if="detail"
        ><ResultTable
          :rows="detail.candidates ?? []"
          :columns="['name', 'type', 'properties']"
          :labels="{
            name: '实体名称',
            type: '实体类型',
            properties: '属性',
          }" />
        <div
          v-if="detail.status === 'pending_review' && session.can('m09.write')"
          class="row-actions"
          style="margin-top: 25px"
        >
          <el-select v-model="graphId" placeholder="选择目标图谱"
            ><el-option
              v-for="graph in relevantGraphs"
              :key="graph.id"
              :value="graph.id"
              :label="graph.name" /></el-select
          ><el-button
            type="primary"
            :disabled="!graphId"
            :loading="op.busy.value"
            @click="review('approve')"
            >复核并入库</el-button
          ><el-button
            type="danger"
            plain
            :loading="op.busy.value"
            @click="review('reject')"
            >拒绝</el-button
          >
        </div>
        <StatusTag v-else :value="detail.status" /></template
    ></el-drawer>
  </div>
</template>
