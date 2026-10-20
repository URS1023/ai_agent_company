<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessageBox } from "element-plus";
import { useSession } from "../../stores/session";
import { api, type Entity, type ListResult } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import GraphCanvas from "./GraphCanvas.vue";
import EntityForm from "./EntityForm.vue";
const session = useSession();
const op = useOperation();
const graphs = ref<Entity[]>([]);
const ontologies = ref<Entity[]>([]);
const graph = ref<Entity | null>(null);
const selected = ref<Record<string, any> | null>(null);
const selectedTypes = ref<string[]>([]);
const search = ref("");
const graphDialog = ref(false);
const entityDialog = ref(false);
const relationDialog = ref(false);
const editEntity = ref<Record<string, any> | null>(null);
const form = reactive({ name: "", ontology_id: "" });
const relation = reactive({ source: "", target: "", type: "", properties: {} });
const ontology = computed(
  () =>
    graph.value?.ontology_snapshot ??
    ontologies.value.find((x) => x.id === graph.value?.ontology_id),
);
const types = computed(
  () =>
    ontology.value?.concepts?.map((x: any) => x.name) ??
    Array.from(new Set(graph.value?.nodes?.map((x: any) => x.type) ?? [])),
);
const nodes = computed(() =>
  (graph.value?.nodes ?? []).filter(
    (node: any) =>
      (!selectedTypes.value.length ||
        selectedTypes.value.includes(node.type)) &&
      (!search.value || node.name.includes(search.value)),
  ),
);
const nodeIds = computed(
  () => new Set(nodes.value.map((node: any) => node.id)),
);
const edges = computed(() =>
  (graph.value?.edges ?? []).filter(
    (edge: any) =>
      nodeIds.value.has(edge.source) && nodeIds.value.has(edge.target),
  ),
);
async function load() {
  await op.run(async () => {
    const [graphList, ontologyList] = await Promise.all([
      api.get<ListResult>("/graphs?limit=100"),
      api.get<ListResult>("/ontologies?limit=100"),
    ]);
    graphs.value = graphList.items as Entity[];
    ontologies.value = ontologyList.items as Entity[];
  });
  if (graphs.value[0] && !graph.value) await open(graphs.value[0].id);
}
async function open(id: string) {
  await op.run(async () => {
    graph.value = await api.get<Entity>(`/graphs/${id}`);
    selectedTypes.value = [];
    selected.value = null;
  });
}
async function create() {
  await op.run(async () => {
    const record = await api.post<Entity>("/graphs", form);
    graphs.value.push(record);
    graph.value = record;
    graphDialog.value = false;
  }, "图谱已创建");
  if (graph.value) await open(graph.value.id);
}
function entity(row: Record<string, any> | null) {
  editEntity.value = row;
  entityDialog.value = true;
}
async function saveEntity(payload: Record<string, unknown>) {
  if (!graph.value) return;
  await op.run(async () => {
    if (editEntity.value)
      await api.patch(
        `/graphs/${graph.value!.id}/entities/${editEntity.value.id}`,
        { ...payload, revision: editEntity.value.revision },
      );
    else await api.post(`/graphs/${graph.value!.id}/entities`, payload);
    entityDialog.value = false;
  }, "实体已保存");
  await open(graph.value.id);
}
async function removeEntity() {
  if (!graph.value || !selected.value) return;
  try {
    await ElMessageBox.confirm(
      `删除实体“${selected.value.name}”？`,
      "删除确认",
      { type: "warning" },
    );
    await op.run(
      () =>
        api.delete(
          `/graphs/${graph.value!.id}/entities/${selected.value!.id}`,
          selected.value!.revision,
        ),
      "实体已删除",
    );
    await open(graph.value.id);
  } catch {}
}
async function createRelation() {
  if (!graph.value) return;
  await op.run(async () => {
    await api.post(`/graphs/${graph.value!.id}/relations`, relation);
    relationDialog.value = false;
  }, "关系已保存");
  await open(graph.value.id);
}
async function removeRelation(edge: Record<string, any>) {
  if (!graph.value) return;
  try {
    await ElMessageBox.confirm(`删除关系“${edge.type}”？`, "删除确认", {
      type: "warning",
    });
    await op.run(
      () =>
        api.delete(
          `/graphs/${graph.value!.id}/relations/${edge.id}`,
          edge.revision,
        ),
      "关系已删除",
    );
    await open(graph.value.id);
  } catch {}
}
async function publish() {
  if (!graph.value) return;
  await op.run(
    () =>
      api.post(`/graphs/${graph.value!.id}/publications`, {
        revision: graph.value!.revision,
      }),
    "图谱已发布",
  );
  await open(graph.value.id);
}
onMounted(load);
</script>
<template>
  <div class="graph-workspace">
    <header class="graph-heading">
      <el-select :model-value="graph?.id" placeholder="选择图谱" @change="open"
        ><el-option
          v-for="item in graphs"
          :key="item.id"
          :value="item.id"
          :label="item.name" /></el-select
      ><el-button v-if="session.can('m09.write')" @click="graphDialog = true"
        >新建图谱</el-button
      ><span style="flex: 1"></span
      ><el-button
        v-if="session.can('m09.write')"
        :disabled="!graph"
        @click="entity(null)"
        >新增实体</el-button
      ><el-button
        v-if="session.can('m09.write')"
        :disabled="!graph?.nodes?.length"
        @click="relationDialog = true"
        >添加关系</el-button
      ><el-button
        v-if="session.can('m09.publish')"
        type="primary"
        :disabled="!graph"
        @click="publish"
        >发布</el-button
      >
    </header>
    <div class="graph-stage">
      <div class="graph-range">
        <h3>实体类型</h3>
        <el-checkbox-group v-model="selectedTypes"
          ><el-checkbox
            v-for="type in types"
            :key="String(type)"
            :value="type"
            >{{ type }}</el-checkbox
          ></el-checkbox-group
        ><el-input
          v-model="search"
          placeholder="搜索实体"
          clearable
          style="margin-top: 20px"
        />
      </div>
      <div class="graph-drawing">
        <GraphCanvas
          :nodes="nodes"
          :edges="edges"
          :types="types"
          :selected-id="selected?.id"
          @select="selected = $event"
        />
      </div>
      <aside v-if="selected" class="graph-details">
        <div class="panel-heading">
          <h3>{{ selected.name }}</h3>
          <el-button text @click="selected = null">×</el-button>
        </div>
        <el-descriptions :column="1" border
          ><el-descriptions-item label="类型">{{
            selected.type
          }}</el-descriptions-item
          ><el-descriptions-item
            v-for="(value, key) in selected.properties"
            :key="key"
            :label="String(key)"
            >{{ value }}</el-descriptions-item
          ></el-descriptions
        >
        <div
          v-if="session.can('m09.write')"
          class="row-actions"
          style="margin-top: 20px"
        >
          <el-button type="primary" @click="entity(selected)">编辑</el-button
          ><el-button
            v-if="session.can('m09.delete')"
            type="danger"
            plain
            @click="removeEntity"
            >删除</el-button
          >
        </div>
        <h3 style="margin-top: 25px">关联关系</h3>
        <div
          v-for="edge in (graph?.edges ?? []).filter(
            (edge: any) =>
              edge.source === selected?.id || edge.target === selected?.id,
          )"
          :key="edge.id"
          style="padding: 12px 0; border-bottom: 1px solid #eee"
        >
          <div>
            {{ edge.type }} →
            {{
              graph?.nodes?.find(
                (node: any) =>
                  node.id ===
                  (edge.source === selected?.id ? edge.target : edge.source),
              )?.name
            }}
          </div>
          <el-button
            v-if="session.can('m09.delete')"
            link
            type="danger"
            style="margin-top: 8px"
            @click="removeRelation(edge)"
            >删除关系</el-button
          >
        </div>
      </aside>
    </div>
    <el-dialog v-model="graphDialog" title="新建图谱" width="600px"
      ><el-form label-position="top" @submit.prevent="create"
        ><el-form-item label="图谱名称" :error="op.fields.value.name"
          ><el-input v-model="form.name" required /></el-form-item
        ><el-form-item label="本体"
          ><el-select v-model="form.ontology_id" filterable
            ><el-option
              v-for="item in ontologies"
              :key="item.id"
              :value="item.id"
              :label="item.name" /></el-select></el-form-item
        ><el-button
          type="primary"
          native-type="submit"
          :loading="op.busy.value"
          :disabled="!form.ontology_id"
          >创建</el-button
        ></el-form
      ></el-dialog
    ><el-dialog
      v-model="entityDialog"
      :title="editEntity ? '编辑实体' : '新增实体'"
      width="640px"
      destroy-on-close
      ><EntityForm
        :concepts="ontology?.concepts ?? []"
        :entity="editEntity"
        :busy="op.busy.value"
        :errors="op.fields.value"
        @submit="saveEntity"
        @cancel="entityDialog = false" /></el-dialog
    ><el-dialog v-model="relationDialog" title="添加关系" width="640px"
      ><el-form label-position="top" @submit.prevent="createRelation"
        ><el-form-item label="起点实体"
          ><el-select v-model="relation.source" filterable
            ><el-option
              v-for="node in graph?.nodes ?? []"
              :key="node.id"
              :value="node.id"
              :label="node.name" /></el-select></el-form-item
        ><el-form-item label="关系"
          ><el-select v-model="relation.type" filterable
            ><el-option
              v-for="type in ontology?.relations ?? []"
              :key="type.name"
              :value="type.name"
              :label="type.name" /></el-select></el-form-item
        ><el-form-item label="终点实体"
          ><el-select v-model="relation.target" filterable
            ><el-option
              v-for="node in graph?.nodes ?? []"
              :key="node.id"
              :value="node.id"
              :label="node.name" /></el-select></el-form-item
        ><el-button type="primary" native-type="submit" :loading="op.busy.value"
          >添加关系</el-button
        ></el-form
      ></el-dialog
    >
  </div>
</template>
<style scoped>
.graph-workspace {
  height: calc(100vh - 124px);
  display: grid;
  grid-template-rows: 52px minmax(0, 1fr);
  min-height: 500px;
}
.graph-heading {
  display: flex;
  align-items: center;
  gap: 12px;
  background: white;
  padding: 10px 18px;
}
.graph-heading > .el-select {
  width: 220px;
}
.graph-stage {
  position: relative;
  min-width: 0;
  min-height: 0;
  display: flex;
  background: #031734 url("/graph-bg.png") center/cover;
}
.graph-range {
  position: absolute;
  top: 12px;
  left: 12px;
  bottom: 12px;
  width: 230px;
  z-index: 2;
  border: 1px solid #24528b;
  background: linear-gradient(120deg, #1c3668ed, #000f2766);
  padding: 20px;
  color: #d2e5ff;
  overflow: auto;
}
.graph-range h3 {
  font-size: 14px;
}
.graph-range :deep(.el-checkbox) {
  display: flex;
  color: #c3daff;
}
.graph-drawing {
  flex: 1;
  min-width: 0;
  min-height: 0;
}
.graph-details {
  background: var(--surface);
  width: 360px;
  padding: 20px;
  overflow: auto;
  z-index: 3;
}
</style>
