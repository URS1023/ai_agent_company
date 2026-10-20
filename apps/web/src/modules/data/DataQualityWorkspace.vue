<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { api, type Entity, type ListResult } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import ResultTable from "../../components/common/ResultTable.vue";
const op = useOperation();
const queries = ref<Entity[]>([]);
const result = ref<any>(null);
const form = reactive({
  query_id: "",
  required_fields: [] as string[],
  unique_fields: [] as string[],
});
const columns = computed(() => {
  const query = queries.value.find((x) => x.id === form.query_id);
  return query?.columns ?? query?.result?.columns ?? [];
});
async function submit() {
  await op.run(async () => {
    result.value = await api.post("/data-quality-checks", form);
  }, "质量检查已完成");
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
          <el-form-item
            label="数据查询"
            :error="op.fields.value.query_id"
            class="full-width"
            ><el-select v-model="form.query_id" filterable
              ><el-option
                v-for="query in queries"
                :key="query.id"
                :value="query.id"
                :label="
                  query.name ?? query.sql ?? query.id
                " /></el-select></el-form-item
          ><el-form-item label="必填字段"
            ><el-select v-model="form.required_fields" multiple filterable
              ><el-option
                v-for="column in columns"
                :key="column"
                :value="column"
                :label="column" /></el-select></el-form-item
          ><el-form-item label="唯一字段"
            ><el-select v-model="form.unique_fields" multiple filterable
              ><el-option
                v-for="column in columns"
                :key="column"
                :value="column"
                :label="column" /></el-select
          ></el-form-item>
        </div>
        <el-button
          type="primary"
          native-type="submit"
          :loading="op.busy.value"
          :disabled="!form.query_id"
          >执行检查</el-button
        ></el-form
      >
    </section>
    <section v-if="result" class="panel">
      <div class="panel-heading"><h3>检查结果</h3></div>
      <ResultTable
        v-if="Array.isArray(result.checks)"
        :rows="result.checks"
      /><el-descriptions v-else :column="2" border
        ><el-descriptions-item
          v-for="(value, key) in result"
          :key="key"
          :label="String(key)"
          >{{
            typeof value === "object" ? JSON.stringify(value) : value
          }}</el-descriptions-item
        ></el-descriptions
      >
    </section>
  </div>
</template>
