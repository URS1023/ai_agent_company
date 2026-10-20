<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef } from "vue";
import { ElMessageBox } from "element-plus";
import { api, type Entity, type ListResult } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import { useSession } from "../../stores/session";
import RowPolicyPanel from "./RowPolicyPanel.vue";
import type { PolicyTable } from "./rowPolicy";
const session = useSession();
const op = useOperation();
const sources = ref<Entity[]>([]);
const roles = ref<Entity[]>([]);
const source = ref<Entity | null>(null);
const tab = shallowRef("columns");
const rowDirty = shallowRef(false),
  rowBusy = shallowRef(false);
const columnsBaseline = shallowRef("");
const fields = ref<
  { table: string; column: string; type: string; roles: string[] }[]
>([]);
const tables = computed(() =>
  Array.from(new Set(fields.value.map((field) => field.table))),
);
const policyTables = computed<PolicyTable[]>(() =>
  tables.value.map((name) => ({
    name,
    columns: fields.value
      .filter((field) => field.table === name)
      .map((field) => ({ name: field.column, type: field.type })),
  })),
);
const columnFingerprint = computed(() =>
  JSON.stringify(
    fields.value.map((field) => [field.table, field.column, field.roles]),
  ),
);
const columnsDirty = computed(
  () => !!source.value && columnFingerprint.value !== columnsBaseline.value,
);
let active = true;
async function select(id: string) {
  if (id === source.value?.id || rowBusy.value || op.busy.value) return;
  if (rowDirty.value || columnsDirty.value) {
    try {
      await ElMessageBox.confirm(
        "放弃当前数据源未保存的权限修改？",
        "切换确认",
        {
          confirmButtonText: "放弃修改",
          cancelButtonText: "继续编辑",
          type: "warning",
        },
      );
    } catch {
      return;
    }
  }
  await op.run(async () => {
    const selected = await api.get<Entity>(`/datasources/${id}`);
    if (!active) return;
    source.value = selected;
    fields.value = (source.value.schema_snapshot ?? []).flatMap((table: any) =>
      table.columns.map((column: any) => ({
        table: table.tableName,
        column: column.columnName,
        type: column.dataType ?? column.type ?? "",
        roles: [...(column.allowed_roles ?? [])],
      })),
    );
    columnsBaseline.value = columnFingerprint.value;
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
    columnsBaseline.value = columnFingerprint.value;
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
onBeforeUnmount(() => {
  active = false;
});
</script>
<template>
  <div class="page-stack">
    <section class="panel toolbar">
      <el-select
        :model-value="source?.id"
        placeholder="选择数据源"
        :disabled="op.busy.value || rowBusy"
        filterable
        @change="select"
        ><el-option
          v-for="item in sources"
          :key="item.id"
          :value="item.id"
          :label="item.name" /></el-select
      ><span class="spacer"></span
      ><el-button
        v-if="session.can('m05.admin') && tab === 'columns'"
        type="primary"
        :loading="op.busy.value"
        :disabled="!source || !columnsDirty"
        @click="save"
        >保存权限</el-button
      >
    </section>
    <section class="panel">
      <el-tabs v-model="tab">
        <el-tab-pane label="字段权限" name="columns">
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
                    :label="
                      role.name
                    " /></el-select></template></el-table-column
          ></el-table>
        </el-tab-pane>
        <el-tab-pane
          v-if="session.can('m05.admin')"
          label="行权限"
          name="rows"
          lazy
        >
          <RowPolicyPanel
            v-if="source"
            :key="source.id"
            :source-id="source.id"
            :tables="policyTables"
            @dirty="rowDirty = $event"
            @busy="rowBusy = $event"
          />
          <el-empty v-else description="选择数据源" />
        </el-tab-pane>
      </el-tabs>
    </section>
  </div>
</template>
