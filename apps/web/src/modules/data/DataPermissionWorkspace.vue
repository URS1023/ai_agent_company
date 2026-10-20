<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { api, type Entity, type ListResult } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import { useSession } from "../../stores/session";
const session = useSession();
const op = useOperation();
const sources = ref<Entity[]>([]);
const roles = ref<Entity[]>([]);
const source = ref<Entity | null>(null);
const fields = ref<
  { table: string; column: string; type: string; roles: string[] }[]
>([]);
const tables = computed(() =>
  Array.from(new Set(fields.value.map((field) => field.table))),
);
async function select(id: string) {
  await op.run(async () => {
    source.value = await api.get<Entity>(`/datasources/${id}`);
    fields.value = (source.value.schema_snapshot ?? []).flatMap((table: any) =>
      table.columns.map((column: any) => ({
        table: table.tableName,
        column: column.columnName,
        type: column.dataType ?? column.type ?? "",
        roles: [...(column.allowed_roles ?? [])],
      })),
    );
  });
}
async function save() {
  if (!source.value) return;
  await op.run(async () => {
    const field_roles = Object.fromEntries(
      tables.value.map((table) => [
        table,
        Object.fromEntries(
          fields.value
            .filter((field) => field.table === table)
            .map((field) => [field.column, field.roles]),
        ),
      ]),
    );
    source.value = await api.patch<Entity>(`/datasources/${source.value!.id}`, {
      revision: source.value!.revision,
      field_roles,
    });
  }, "字段权限已保存");
}
onMounted(() =>
  op.run(async () => {
    sources.value = (await api.get<ListResult>("/datasources?limit=200"))
      .items as Entity[];
    if (session.can("m14.read"))
      roles.value = (await api.get<ListResult>("/roles")).items as Entity[];
  }),
);
</script>
<template>
  <div class="page-stack">
    <section class="panel toolbar">
      <el-select
        :model-value="source?.id"
        placeholder="选择数据源"
        filterable
        @change="select"
        ><el-option
          v-for="item in sources"
          :key="item.id"
          :value="item.id"
          :label="item.name" /></el-select
      ><span class="spacer"></span
      ><el-button
        v-if="session.can('m05.admin')"
        type="primary"
        :loading="op.busy.value"
        :disabled="!source"
        @click="save"
        >保存权限</el-button
      >
    </section>
    <section class="panel">
      <el-table :data="fields" stripe v-loading="op.busy.value"
        ><el-table-column
          prop="table"
          label="数据表"
          min-width="200" /><el-table-column
          prop="column"
          label="字段"
          min-width="200" /><el-table-column
          prop="type"
          label="类型"
          width="150" /><el-table-column label="可见角色" min-width="300"
          ><template #default="{ row }"
            ><el-select
              v-model="row.roles"
              multiple
              filterable
              :allow-create="session.can('m05.admin')"
              :disabled="!session.can('m05.admin')"
              placeholder="所有角色"
              style="width: 100%"
              ><el-option
                v-for="role in roles"
                :key="role.id"
                :value="role.name"
                :label="role.name" /></el-select></template></el-table-column
      ></el-table>
    </section>
  </div>
</template>
