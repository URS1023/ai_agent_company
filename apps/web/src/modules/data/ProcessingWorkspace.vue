<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { api, type Entity, type ListResult } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import ResultTable from "../../components/common/ResultTable.vue";
const op = useOperation();
const queries = ref<Entity[]>([]);
const result = ref<any>(null);
const form = reactive({
  name: "",
  query_id: "",
  operations: [{ type: "drop_null", fields: [] as string[], field: "" }],
});
const columns = computed(() => {
  const query = queries.value.find((x) => x.id === form.query_id);
  return query?.columns ?? query?.result?.columns ?? [];
});
async function submit() {
  await op.run(async () => {
    result.value = await api.post("/data-processing-jobs", {
      ...form,
      operations: form.operations.map((operation) =>
        operation.type === "drop_null"
          ? { type: operation.type, field: operation.field }
          : { type: operation.type, fields: operation.fields },
      ),
    });
  }, "数据处理已完成");
}
onMounted(() =>
  op.run(async () => {
    queries.value = (await api.get<ListResult>("/queries?limit=100"))
      .items as Entity[];
  }),
);
</script>
<template>
  <div class="page-stack">
    <section class="panel">
      <el-form label-position="top" @submit.prevent="submit"
        ><div class="form-grid">
          <el-form-item label="任务名称" :error="op.fields.value.name"
            ><el-input v-model="form.name" required /></el-form-item
          ><el-form-item label="数据查询" :error="op.fields.value.query_id"
            ><el-select v-model="form.query_id" filterable
              ><el-option
                v-for="query in queries"
                :key="query.id"
                :value="query.id"
                :label="query.name ?? query.sql ?? query.id" /></el-select
          ></el-form-item>
        </div>
        <div
          v-for="(operation, index) in form.operations"
          :key="index"
          class="processing-row"
        >
          <span>{{ index + 1 }}</span
          ><el-select v-model="operation.type"
            ><el-option value="select" label="选择字段" /><el-option
              value="drop_null"
              label="去除空值" /><el-option
              value="deduplicate"
              label="删除重复记录" /></el-select
          ><el-select
            v-if="operation.type === 'drop_null'"
            v-model="operation.field"
            filterable
            ><el-option
              v-for="column in columns"
              :key="column"
              :value="column"
              :label="column" /></el-select
          ><el-select v-else v-model="operation.fields" multiple filterable
            ><el-option
              v-for="column in columns"
              :key="column"
              :value="column"
              :label="column" /></el-select
          ><el-button
            link
            type="danger"
            :disabled="form.operations.length === 1"
            @click="form.operations.splice(index, 1)"
            >删除</el-button
          >
        </div>
        <div class="row-actions" style="margin-top: 20px">
          <el-button
            @click="
              form.operations.push({ type: 'drop_null', fields: [], field: '' })
            "
            >添加处理步骤</el-button
          ><span style="flex: 1"></span
          ><el-button
            type="primary"
            native-type="submit"
            :loading="op.busy.value"
            :disabled="!form.query_id"
            >执行处理</el-button
          >
        </div></el-form
      >
    </section>
    <section v-if="result" class="panel">
      <div class="panel-heading">
        <h3>处理结果</h3>
        <span>{{ result.result?.row_count }}行</span>
      </div>
      <ResultTable
        :rows="result.result?.rows ?? []"
        :columns="result.result?.columns"
      />
    </section>
  </div>
</template>
<style scoped>
.processing-row {
  display: grid;
  grid-template-columns: 30px 200px minmax(200px, 1fr) 65px;
  gap: 15px;
  align-items: center;
  margin-top: 15px;
  padding: 16px;
  border: 1px solid #e5eaf2;
}
</style>
