<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter, useRoute } from "vue-router";
import { useSession } from "../../stores/session";
import { ElMessageBox } from "element-plus";
import { api, type Entity, type ListResult } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import ResultTable from "../../components/common/ResultTable.vue";
import StatusTag from "../../components/common/StatusTag.vue";
import DataImportForm from "./DataImportForm.vue";
import QueryEditor from "./QueryEditor.vue";
import DataSourceForm from "./DataSourceForm.vue";
import NaturalQueryForm from "./NaturalQueryForm.vue";
import TargetApprovalForm from "./TargetApprovalForm.vue";
const props = defineProps<{ config?: string }>();
const session = useSession();
const router = useRouter();
const route = useRoute();
const op = useOperation();
const mode = ref("sql");
const selectedSource = ref<Entity | null>(null);
const sources = ref<Entity[]>([]);
const queries = ref<Entity[]>([]);
const metadata = ref<any>({ tables: [] });
const sourceId = ref("");
const sql = ref("");
const result = ref<any>(null);
const imported = ref<Entity | null>(null);
const importDialog = ref(false);
const sourceDialog = ref(false);
const approvalDialog = ref(false);
async function load() {
  await op.run(async () => {
    const [sourceList, queryList] = await Promise.all([
      api.get<ListResult>("/datasources?limit=200"),
      api.get<ListResult>("/queries?limit=100"),
    ]);
    sources.value = sourceList.items as Entity[];
    queries.value = queryList.items as Entity[];
  });
}
async function selectSource(id: string) {
  sourceId.value = id;
  await op.run(async () => {
    metadata.value = await api.get(`/datasources/${id}/metadata`);
  });
}
function editSource(source: Entity | null) {
  selectedSource.value = source;
  sourceDialog.value = true;
}
async function saveSource(payload: Record<string, unknown>) {
  await op.run(async () => {
    if (selectedSource.value)
      await api.patch(`/datasources/${selectedSource.value.id}`, {
        ...payload,
        revision: selectedSource.value.revision,
      });
    else await api.post<Entity>("/datasources", payload);
    sourceDialog.value = false;
  }, "数据源已保存");
  if (!op.error.value) await load();
}
async function removeSource(source: Entity) {
  try {
    await ElMessageBox.confirm(`删除数据源“${source.name}”？`, "删除确认", {
      type: "warning",
    });
    await op.run(
      () => api.delete(`/datasources/${source.id}`, source.revision),
      "数据源已删除",
    );
    await load();
  } catch {}
}
async function checkSource(source: Entity) {
  await op.run(
    () => api.post(`/datasources/${source.id}/connection-checks`, {}),
    "连接检查已完成",
  );
}
function approveSource(source: Entity) {
  selectedSource.value = source;
  approvalDialog.value = true;
}
async function saveApproval(payload: Record<string, unknown>) {
  const source = selectedSource.value;
  if (!source) return;
  try {
    const localRecovery = source.provider === "sqlite";
    const target = source.connection_config ?? {};
    await ElMessageBox.confirm(
      localRecovery
        ? `恢复“${source.name}”的数据访问？`
        : `批准访问 ${target.host}:${target.port}${target.database ? ` / ${target.database}` : ""}，有效期至 ${new Date(String(payload.expires_at)).toLocaleString()}？`,
      localRecovery ? "恢复确认" : "访问确认",
      {
        type: "warning",
        confirmButtonText: localRecovery ? "恢复访问" : "批准访问",
      },
    );
    await op.run(
      async () => {
        await api.post(
          `/datasources/${source.id}/${localRecovery ? "recoveries" : "target-approvals"}`,
          payload,
        );
        approvalDialog.value = false;
      },
      localRecovery ? "访问已恢复" : "访问已批准",
    );
    if (!op.error.value) await load();
  } catch (error) {
    if (error !== "cancel" && error !== "close") throw error;
  }
}
async function syncMetadata(source: Entity) {
  await op.run(
    () =>
      api.post(`/datasources/${source.id}/metadata-syncs`, {
        revision: source.revision,
      }),
    "元数据已同步",
  );
  if (!op.error.value) await load();
}
async function natural(payload: Record<string, unknown>) {
  await op.run(async () => {
    const response = await api.post("/queries/natural-language", payload);
    if (response.rows || response.result?.rows) result.value = response;
    sql.value = response.sql ?? response.proposal?.sql ?? "";
    if (!response.rows && !response.result?.rows) mode.value = "sql";
  }, "查询已生成");
}
async function importData(payload: Record<string, unknown>) {
  await op.run(async () => {
    imported.value = await api.post<Entity>("/datasets", payload);
    importDialog.value = false;
  }, "数据已导入");
  await load();
  if (imported.value?.datasource_id)
    await selectSource(imported.value.datasource_id);
}
async function execute(payload: Record<string, unknown>) {
  await op.run(async () => {
    result.value = await api.post("/queries", payload);
    queries.value.unshift(result.value);
  }, "查询完成");
}
function chooseTable(table: any) {
  const name = table.tableName ?? table.name;
  sql.value = `SELECT * FROM "${String(name).replace(/"/g, '""')}" LIMIT 100`;
}
async function openQuery(query: Entity) {
  sourceId.value = query.datasource_id;
  sql.value = query.sql;
  result.value = await api.get(`/queries/${query.id}`);
}
onMounted(async () => {
  await load();
  if (typeof route.query.source === "string")
    await selectSource(route.query.source);
});
</script>
<template>
  <div class="page-stack">
    <div class="page-heading">
      <h2>
        {{
          config === "sources"
            ? "数据源"
            : config === "quality"
              ? "数据质量"
              : "数据查询"
        }}
      </h2>
      <div>
        <el-button @click="load">刷新</el-button
        ><el-button v-if="session.can('m05.write')" @click="editSource(null)"
          >新增数据源</el-button
        ><el-button
          v-if="session.can('m06.write')"
          type="primary"
          @click="importDialog = true"
          >导入CSV</el-button
        >
      </div>
    </div>
    <section v-if="config === 'sources'" class="panel">
      <el-table :data="sources" stripe v-loading="op.busy.value"
        ><el-table-column
          prop="name"
          label="数据源名称"
          min-width="200"
        /><el-table-column
          prop="provider"
          label="类型"
          width="150"
        /><el-table-column label="状态" width="150"
          ><template #default="{ row }"
            ><StatusTag :value="row.status" /></template></el-table-column
        ><el-table-column
          prop="max_rows"
          label="查询上限"
          width="120"
        /><el-table-column label="操作" width="365"
          ><template #default="{ row }"
            ><el-button
              v-if="session.can('m06.read')"
              link
              type="primary"
              @click="
                router.push({ path: '/analysis', query: { source: row.id } })
              "
              >查询</el-button
            ><el-button
              v-if="session.can('m05.write')"
              link
              type="primary"
              @click="editSource(row)"
              >设置</el-button
            ><el-button
              v-if="
                session.can('m05.write') &&
                ['postgres', 'mysql', 'elasticsearch'].includes(row.provider)
              "
              link
              type="primary"
              :disabled="row.status !== 'active' || op.busy.value"
              @click="syncMetadata(row)"
              >同步元数据</el-button
            ><el-button
              v-if="
                session.can('m05.admin') &&
                (row.provider !== 'sqlite' || row.status === 'blocked')
              "
              link
              type="primary"
              :disabled="op.busy.value"
              @click="approveSource(row)"
              >{{
                row.provider === "sqlite" ? "恢复访问" : "批准访问"
              }}</el-button
            ><el-button
              v-if="session.can('m05.write')"
              link
              type="primary"
              :disabled="row.status !== 'active' || op.busy.value"
              @click="checkSource(row)"
              >检查连接</el-button
            ><el-button
              v-if="session.can('m05.delete')"
              link
              type="danger"
              @click="removeSource(row)"
              >删除</el-button
            ></template
          ></el-table-column
        ></el-table
      >
    </section>
    <div v-else class="split-workspace">
      <aside class="panel">
        <h3>数据目录</h3>
        <el-tree
          :data="sources"
          node-key="id"
          :props="{ label: 'name' }"
          highlight-current
          @node-click="(node: any) => selectSource(node.id)"
        /><el-divider />
        <div
          v-for="table in metadata.tables"
          :key="table.tableName ?? table.name"
          class="data-table-entry"
        >
          <el-button link type="primary" @click="chooseTable(table)">{{
            table.tableName ?? table.name
          }}</el-button>
          <div
            v-for="column in table.columns ?? []"
            :key="column.columnName ?? column.name"
            class="data-column"
          >
            {{ column.columnName ?? column.name }}
            <span class="muted">{{ column.dataType ?? column.type }}</span>
          </div>
        </div>
        <el-empty
          v-if="!sources.length"
          description="暂无数据源"
          :image-size="60"
        />
      </aside>
      <div class="page-stack">
        <section class="panel">
          <el-tabs v-model="mode"
            ><el-tab-pane label="SQL查询" name="sql" /><el-tab-pane
              label="自然语言查询"
              name="natural" /></el-tabs
          ><QueryEditor
            v-if="mode === 'sql'"
            :sources="sources"
            :source-id="sourceId"
            :sql="sql"
            :busy="op.busy.value"
            :errors="op.fields.value"
            @source-change="selectSource"
            @submit="execute"
          /><NaturalQueryForm
            v-else
            :sources="sources"
            :source-id="sourceId"
            :busy="op.busy.value"
            :errors="op.fields.value"
            @source-change="selectSource"
            @submit="natural"
          />
        </section>
        <section v-if="result" class="panel">
          <div class="panel-heading">
            <h3>
              查询结果
              <span class="muted" style="font-weight: 400; font-size: 14px"
                >{{ result.rows?.length ?? 0 }}行</span
              >
            </h3>
            <StatusTag v-if="result.truncated" value="已截断" />
          </div>
          <ResultTable
            :rows="result.rows ?? []"
            :columns="result.columns"
          /><el-collapse style="margin-top: 15px"
            ><el-collapse-item title="执行计划" name="plan">
              <pre class="json-preview">{{
                JSON.stringify(result.plan ?? {}, null, 2)
              }}</pre>
            </el-collapse-item></el-collapse
          >
        </section>
        <section class="panel">
          <div class="panel-heading"><h3>查询记录</h3></div>
          <el-table :data="queries" stripe
            ><el-table-column
              prop="name"
              label="查询名称"
              min-width="150"
            /><el-table-column
              prop="sql"
              label="查询语句"
              min-width="280"
              show-overflow-tooltip
            /><el-table-column
              prop="created_at"
              label="时间"
              width="185"
            /><el-table-column width="80"
              ><template #default="{ row }"
                ><el-button link type="primary" @click="openQuery(row)"
                  >打开</el-button
                ></template
              ></el-table-column
            ></el-table
          >
        </section>
      </div>
    </div>
    <el-dialog
      v-model="importDialog"
      title="导入数据"
      width="760px"
      destroy-on-close
      ><DataImportForm
        :sources="sources"
        :busy="op.busy.value"
        :errors="op.fields.value"
        @submit="importData"
        @cancel="importDialog = false" /></el-dialog
    ><el-dialog
      v-model="sourceDialog"
      :title="selectedSource ? '编辑数据源' : '新增数据源'"
      width="760px"
      destroy-on-close
      ><DataSourceForm
        :entity="selectedSource"
        :busy="op.busy.value"
        :errors="op.fields.value"
        @submit="saveSource"
        @cancel="sourceDialog = false"
    /></el-dialog>
    <el-dialog
      v-model="approvalDialog"
      :title="selectedSource?.provider === 'sqlite' ? '恢复访问' : '批准访问'"
      width="760px"
      destroy-on-close
    >
      <TargetApprovalForm
        v-if="selectedSource"
        :source="selectedSource"
        :busy="op.busy.value"
        :errors="op.fields.value"
        @submit="saveApproval"
        @cancel="approvalDialog = false"
      />
    </el-dialog>
  </div>
</template>
<style scoped>
.data-table-entry {
  padding: 8px 0;
}
.data-column {
  padding: 6px 0 6px 14px;
  font-size: 13px;
  display: flex;
  justify-content: space-between;
}
</style>
