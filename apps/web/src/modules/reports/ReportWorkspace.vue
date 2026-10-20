<script setup lang="ts">
import { onMounted, ref, reactive, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessageBox } from "element-plus";
import { api, type Entity, type ListResult } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import ReportStudio from "./ReportStudio.vue";
import InspectionReportView from "./InspectionReportView.vue";
import StatusTag from "../../components/common/StatusTag.vue";
import { useSession } from "../../stores/session";
const session = useSession();
const route = useRoute();
const router = useRouter();
const props = defineProps<{ config?: string }>();
const op = useOperation();
const reports = ref<Entity[]>([]);
const templates = ref<Entity[]>([]);
const selected = ref<Entity | null>(null);
const dialog = ref(false);
const form = reactive({ name: "", template_id: "", description: "" });
let initialized = false;
function isInspectionReport(report: Entity) {
  return report.layout?.kind === "inspection_report";
}
function exportFormats(report: Entity) {
  return isInspectionReport(report)
    ? ["html", "pdf", "docx", "xlsx", "pptx", "csv", "json"]
    : ["html", "csv", "json", "pdf", "png", "docx", "xlsx", "pptx"];
}
async function selectReport(report: Entity) {
  selected.value = report;
  if (route.query.report !== report.id)
    await router.replace({ query: { ...route.query, report: report.id } });
}
async function load() {
  await op.run(async () => {
    const [reportList, templateList] = await Promise.all([
      api.get<ListResult>("/reports?limit=100"),
      api.get<ListResult>("/report-templates?limit=100"),
    ]);
    reports.value = reportList.items as Entity[];
    templates.value = templateList.items as Entity[];
  });
}
async function create() {
  await op.run(async () => {
    const report = await api.post<Entity>("/reports", {
      ...form,
      template_id: form.template_id || undefined,
      layout: form.template_id
        ? undefined
        : {
            canvas: { width: 1440, height: 900, backgroundColor: "#07182d" },
            widgets: [],
          },
    });
    await selectReport(report);
    dialog.value = false;
  }, "报表已创建");
}
async function open(row: Entity) {
  await openId(row.id);
}
async function openId(id: string) {
  await op.run(async () => {
    const report = await api.get<Entity>(`/reports/${encodeURIComponent(id)}`);
    await selectReport(report);
  });
}
async function closeReport() {
  selected.value = null;
  if (route.query.report) {
    const query = { ...route.query };
    delete query.report;
    await router.replace({ query });
  }
  await load();
}
function useTemplate(template: Entity) {
  form.template_id = template.id;
  form.name = `${template.name}报表`;
  dialog.value = true;
}
async function remove(row: Entity) {
  try {
    await ElMessageBox.confirm(`删除“${row.name}”？`, "删除确认", {
      type: "warning",
    });
    await op.run(async () => {
      await api.delete(`/reports/${row.id}`, row.revision);
      reports.value = reports.value.filter((x) => x.id !== row.id);
    }, "已删除");
  } catch {}
}
async function download(row: Entity, format: string) {
  await op.run(async () => {
    const blob = await api.postBlob(`/reports/${row.id}/exports`, { format });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${row.name}.${format}`;
    anchor.click();
    URL.revokeObjectURL(url);
  });
}
onMounted(async () => {
  initialized = true;
  if (typeof route.query.report === "string") await openId(route.query.report);
  else await load();
});
watch(
  () => route.query.report,
  async (value) => {
    if (!initialized || value === selected.value?.id) return;
    if (typeof value === "string") await openId(value);
    else selected.value = null;
  },
);
</script>
<template>
  <InspectionReportView
    v-if="selected && isInspectionReport(selected)"
    :report="selected"
    @saved="selected = $event"
    @back="closeReport"
  />
  <ReportStudio
    v-else-if="selected"
    :report="selected"
    @saved="selected = $event"
    @back="closeReport"
  />
  <el-skeleton
    v-else-if="route.query.report && op.busy.value"
    class="panel"
    :rows="8"
    animated
  />
  <el-result
    v-else-if="route.query.report && op.error.value"
    icon="error"
    :title="op.error.value"
    ><template #extra
      ><el-button @click="closeReport">返回列表</el-button></template
    ></el-result
  >
  <div v-else class="page-stack">
    <div class="page-heading">
      <h2>{{ config === "templates" ? "组件与模板" : "报表设计" }}</h2>
      <el-button
        v-if="session.can('m07.write')"
        type="primary"
        @click="
          form.name = '';
          form.template_id = '';
          dialog = true;
        "
        >新建报表</el-button
      >
    </div>
    <section v-if="config !== 'templates'" class="panel">
      <el-table :data="reports" stripe v-loading="op.busy.value"
        ><el-table-column
          prop="name"
          label="报表名称"
          min-width="220"
        /><el-table-column label="状态" width="120"
          ><template #default="{ row }"
            ><StatusTag
              :value="row.status ?? 'draft'" /></template></el-table-column
        ><el-table-column label="内容" width="130"
          ><template #default="{ row }">{{
            isInspectionReport(row)
              ? "质检报告"
              : `${row.layout?.widgets?.length ?? row.widget_count ?? 0} 个组件`
          }}</template></el-table-column
        ><el-table-column
          prop="revision"
          label="版本"
          width="80"
        /><el-table-column
          prop="updated_at"
          label="更新时间"
          width="185"
        /><el-table-column label="操作" width="240"
          ><template #default="{ row }"
            ><el-button link type="primary" @click="open(row)">{{
              isInspectionReport(row) ? "查看" : "设计"
            }}</el-button
            ><el-dropdown
              ><el-button link type="primary">导出</el-button
              ><template #dropdown
                ><el-dropdown-menu
                  ><el-dropdown-item
                    v-for="format in exportFormats(row)"
                    :key="format"
                    @click="download(row, format)"
                    >{{ format.toUpperCase() }}</el-dropdown-item
                  ></el-dropdown-menu
                ></template
              ></el-dropdown
            ><el-button
              v-if="session.can('m07.delete')"
              link
              type="danger"
              @click="remove(row)"
              >删除</el-button
            ></template
          ></el-table-column
        ></el-table
      >
    </section>
    <div class="panel-heading">
      <h3>选择模板</h3>
      <span class="muted">{{ templates.length }}套</span>
    </div>
    <div class="template-grid">
      <article
        v-for="template in templates"
        :key="template.id"
        class="template-card"
      >
        <img
          v-if="template.preview"
          :src="template.preview"
          :alt="template.name"
        />
        <div v-else class="template-placeholder">{{ template.name }}</div>
        <div class="template-info">
          <h3>{{ template.name }}</h3>
          <span v-if="template.sample_data" class="muted">模板示例</span
          ><el-button
            v-if="session.can('m07.write')"
            type="primary"
            plain
            @click="useTemplate(template)"
            >使用模板</el-button
          >
        </div>
      </article>
    </div>
    <el-dialog v-model="dialog" title="新建报表" width="600px"
      ><el-form label-position="top" @submit.prevent="create"
        ><el-form-item label="报表名称" :error="op.fields.value.name"
          ><el-input
            v-model="form.name"
            required
            maxlength="160" /></el-form-item
        ><el-form-item label="模板"
          ><el-select
            v-model="form.template_id"
            clearable
            filterable
            placeholder="空白画布"
            ><el-option
              v-for="template in templates"
              :key="template.id"
              :value="template.id"
              :label="template.name" /></el-select></el-form-item
        ><el-button type="primary" native-type="submit" :loading="op.busy.value"
          >创建设计</el-button
        ></el-form
      ></el-dialog
    >
  </div>
</template>
<style scoped>
.template-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(240px, 1fr));
  gap: 15px;
}
.template-card {
  background: #fff;
  border: 1px solid #e1e6ef;
  border-radius: 2px;
  overflow: hidden;
}
.template-card > img {
  width: 100%;
  height: 170px;
  object-fit: cover;
  display: block;
}
.template-placeholder {
  height: 170px;
  display: grid;
  place-items: center;
  background: linear-gradient(140deg, #07182d, #1b4476);
  color: #b4d3ff;
}
.template-info {
  padding: 16px;
}
.template-info h3 {
  font-size: 14px;
  margin-bottom: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.template-info > .el-button {
  float: right;
}
</style>
