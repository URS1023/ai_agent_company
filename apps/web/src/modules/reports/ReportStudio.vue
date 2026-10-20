<script setup lang="ts">
import { cloneDto } from "../../utils/cloneDto";
import { computed, onMounted, provide, ref, watch } from "vue";
import { api, type Entity, type ListResult } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import JimuCanvas from "./reused/JimuCanvas.vue";
import ReportProperties from "./ReportProperties.vue";
import ComponentCatalog from "./ComponentCatalog.vue";
import { useCanvasActions } from "./useCanvasActions";
import { useSession } from "../../stores/session";
import { ElMessageBox } from "element-plus";
import ReportVersions from "./ReportVersions.vue";
const session = useSession();
const canWrite = computed(() => session.can("m07.write"));
const props = defineProps<{ report: Entity }>();
const emit = defineEmits<{ saved: [report: Entity]; back: [] }>();
const op = useOperation();
const draft = ref<Entity>(cloneDto(props.report));
let mediaRevision = 0;
let mediaPromise: Promise<Map<string, string>> | undefined;
provide("resolveReportImage", async (fileId: string) => {
  // 同一修订中的所有图片共享一次授权读取；地址不写回布局配置。
  if (!mediaPromise || mediaRevision !== draft.value.revision) {
    mediaRevision = draft.value.revision;
    mediaPromise = api
      .get<ListResult<{ file_id: string; data_uri: string }>>(
        `/reports/${draft.value.id}/image-assets`,
      )
      .then(
        (response) =>
          new Map(response.items.map((item) => [item.file_id, item.data_uri])),
      )
      .catch((error) => {
        mediaPromise = undefined;
        throw error;
      });
  }
  return (await mediaPromise).get(fileId) ?? "";
});
const selectedId = ref("");
const zoom = ref(0.6);
const queries = ref<Entity[]>([]);
const preview = ref(!canWrite.value);
const catalogDialog = ref(false);
const dataDialog = ref(false);
const dataDetail = ref<{
  columns: string[];
  rows: Record<string, any>[];
  total: number;
  truncated: boolean;
} | null>(null);
async function chooseComponent(item: Record<string, any>) {
  await op.run(async () => {
    // 每个源素材保留工厂返回的独立样式，不能按组件类型重建通用实例。
    const widget = await api.post<Record<string, any>>(
      "/report-component-instances",
      {
        source_id: item.id,
        x: 80,
        y: 80,
        order_num: layout.value.widgets.length,
      },
    );
    checkpoint();
    layout.value.widgets.push(widget);
    selectedId.value = widget.id;
    catalogDialog.value = false;
  });
}
const history = ref<string[]>([]);
const future = ref<string[]>([]);
const layout = computed(
  () =>
    draft.value.layout ?? {
      canvas: { width: 1920, height: 1080, backgroundColor: "#06142a" },
      widgets: [],
    },
);
const page = computed(() => ({
  design: layout.value.canvas,
  widgets: layout.value.widgets,
  theme: draft.value.theme,
}));
const widgets = computed({
  get: () => layout.value.widgets,
  set: (value) => {
    layout.value.widgets = value;
  },
});
const canvasActions = useCanvasActions({
  widgets,
  selectedId,
  checkpoint,
  preview,
  showData,
});
async function showData(widgetId: string) {
  if (canWrite.value && !(await save(true))) return;
  await op.run(async () => {
    dataDetail.value = await api.post(`/reports/${draft.value.id}/drilldowns`, {
      widget_id: widgetId,
      filters: {},
    });
    dataDialog.value = true;
  });
}
async function saveTemplate() {
  try {
    const result = await ElMessageBox.prompt("模板名称", "另存为模板", {
      inputValue: draft.value.name,
      inputValidator: (value) => !!value?.trim() || "请输入模板名称",
    });
    await op.run(async () => {
      await api.post("/report-templates", {
        name: result.value.trim(),
        layout: cloneDto(layout.value),
      });
    }, "模板已保存");
  } catch {}
}
function restored(report: Entity) {
  draft.value = report;
  history.value = [];
  future.value = [];
  selectedId.value = "";
  emit("saved", report);
}
const selected = computed({
  get: () =>
    layout.value.widgets.find(
      (widget: any) => widget.id === selectedId.value,
    ) ?? null,
  set: (widget: Record<string, any> | null) => {
    if (widget) {
      const index = layout.value.widgets.findIndex(
        (x: any) => x.id === widget.id,
      );
      if (index >= 0) layout.value.widgets[index] = widget;
    }
  },
});
const componentTypes = [
  { id: "JText", name: "文本" },
  { id: "JStatsSummary", name: "指标" },
  { id: "JLine", name: "折线图" },
  { id: "JBar", name: "柱状图" },
  { id: "JHorizontalBar", name: "条形图" },
  { id: "JArea", name: "面积图" },
  { id: "JPie", name: "饼图" },
  { id: "JRing", name: "环形图" },
  { id: "JGauge", name: "仪表盘" },
  { id: "JRadar", name: "雷达图" },
  { id: "JScrollBoard", name: "明细表" },
];
function checkpoint() {
  history.value.push(JSON.stringify(layout.value));
  if (history.value.length > 30) history.value.shift();
  future.value = [];
}
function updateWidget(id: string, patch: Record<string, any>) {
  const widget = layout.value.widgets.find((x: any) => x.id === id);
  if (widget) Object.assign(widget, patch);
}
function updateWidgets(changes: any[]) {
  for (const item of changes) updateWidget(item.id, item);
}
function add(type: string) {
  checkpoint();
  const id = crypto.randomUUID();
  layout.value.widgets.push({
    id,
    i: id,
    component: type,
    componentName: componentTypes.find((x) => x.id === type)?.name ?? type,
    type: type === "JText" ? "text" : "chart",
    x: 80,
    y: 80,
    w: type === "JText" ? 400 : 460,
    h: type === "JText" ? 60 : 260,
    rotation: 0,
    visible: true,
    locked: false,
    orderNum: layout.value.widgets.length,
    config:
      type === "JText"
        ? { option: { body: { text: "标题", fontSize: 28, color: "#eaf8ff" } } }
        : { chartDataParsed: [] },
    data: [],
  });
  selectedId.value = id;
}
function remove() {
  canvasActions.remove();
}
function duplicate() {
  canvasActions.duplicate();
}
function undo() {
  const item = history.value.pop();
  if (!item) return;
  future.value.push(JSON.stringify(layout.value));
  draft.value.layout = JSON.parse(item);
}
function redo() {
  const item = future.value.pop();
  if (!item) return;
  history.value.push(JSON.stringify(layout.value));
  draft.value.layout = JSON.parse(item);
}
async function save(silent = false) {
  const saved = await op.run(
    async () => {
      draft.value = await api.patch<Entity>(`/reports/${draft.value.id}`, {
        name: draft.value.name,
        layout: layout.value,
        revision: draft.value.revision,
      });
      emit("saved", draft.value);
      return true;
    },
    silent ? undefined : "报表已保存",
  );
  return saved === true;
}
async function refresh() {
  if (!(await save(true))) return;
  await op.run(async () => {
    const response = await api.post<Entity>(
      `/reports/${draft.value.id}/data-refreshes`,
      { revision: draft.value.revision },
    );
    draft.value = response;
    emit("saved", response);
  }, "数据已刷新");
}
async function publish() {
  if (!(await save(true))) return;
  await op.run(async () => {
    draft.value = await api.post<Entity>(
      `/reports/${draft.value.id}/publications`,
      { revision: draft.value.revision },
    );
    emit("saved", draft.value);
  }, "报表已发布");
}
onMounted(() =>
  op.run(async () => {
    if (!session.can("m06.read")) return;
    queries.value = (await api.get<ListResult>("/queries?limit=200"))
      .items as Entity[];
  }),
);
watch(
  () => props.report.id,
  () => {
    draft.value = cloneDto(props.report);
    selectedId.value = "";
  },
);
</script>
<template>
  <div class="report-studio">
    <header class="report-toolbar">
      <el-button @click="emit('back')">返回</el-button
      ><el-input
        v-model="draft.name"
        :readonly="!canWrite"
        style="width: 220px"
      /><span class="spacer"></span
      ><el-button v-if="canWrite" @click="undo" :disabled="!history.length"
        >撤销</el-button
      ><el-button v-if="canWrite" @click="redo" :disabled="!future.length"
        >重做</el-button
      ><ReportVersions :report="draft" @restored="restored" />
      <el-button v-if="canWrite" @click="saveTemplate">另存模板</el-button>
      <el-button v-if="canWrite" @click="preview = !preview">{{
        preview ? "编辑" : "预览"
      }}</el-button
      ><el-button
        v-if="canWrite && session.can('m06.read')"
        :loading="op.busy.value"
        @click="refresh"
        >刷新数据</el-button
      ><el-button v-if="canWrite" :loading="op.busy.value" @click="save()"
        >保存</el-button
      ><el-button
        v-if="canWrite && session.can('m07.publish')"
        type="primary"
        :loading="op.busy.value"
        @click="publish"
        >发布</el-button
      >
    </header>
    <div class="report-body">
      <aside v-if="!preview" class="report-library">
        <h3>组件</h3>
        <div class="component-grid">
          <button
            v-for="type in componentTypes"
            :key="type.id"
            @click="add(type.id)"
          >
            {{ type.name }}
          </button>
        </div>
        <el-button
          style="width: 100%; margin-top: 12px"
          @click="catalogDialog = true"
          >全部组件</el-button
        ><el-divider />
        <div class="panel-heading">
          <h3>图层</h3>
          <div>
            <el-button
              link
              type="primary"
              :disabled="!selected"
              @click="duplicate"
              >复制</el-button
            ><el-button link type="danger" :disabled="!selected" @click="remove"
              >删除</el-button
            >
          </div>
        </div>
        <button
          v-for="widget in [...layout.widgets].reverse()"
          :key="widget.id"
          class="layer-button"
          :class="{ active: widget.id === selectedId }"
          @click="canvasActions.select(widget.id)"
        >
          {{ widget.componentName ?? widget.title ?? widget.component
          }}<span v-if="widget.locked"> · 锁定</span>
        </button>
      </aside>
      <div class="report-canvas">
        <JimuCanvas
          :page="page"
          :widgets="layout.widgets"
          :selected-id="selectedId"
          :selected-ids="canvasActions.selectedIds.value"
          :can-paste="!!canvasActions.clipboard.value.length"
          :zoom="zoom"
          :preview="preview"
          @select="canvasActions.select"
          @area-select="canvasActions.areaSelect"
          @context-action="canvasActions.contextAction"
          @drop-component="chooseComponent($event.item)"
          @drilldown="showData($event.widget.id)"
          @update-widget="updateWidget"
          @update-widgets="updateWidgets"
          @interaction-start="checkpoint"
          @zoom-change="zoom = $event"
          @fit="zoom = 0.6"
        />
      </div>
      <ReportProperties v-if="!preview" v-model="selected" :queries="queries" />
    </div>
    <el-dialog
      v-model="catalogDialog"
      title="组件目录"
      width="1000px"
      destroy-on-close
      ><ComponentCatalog @select="chooseComponent"
    /></el-dialog>
    <el-dialog v-model="dataDialog" title="数据明细" width="900px"
      ><el-table v-if="dataDetail" :data="dataDetail.rows" max-height="500"
        ><el-table-column
          v-for="column in dataDetail.columns"
          :key="column"
          :prop="column"
          :label="column"
          min-width="140"
      /></el-table>
      <p v-if="dataDetail?.truncated" class="muted">
        当前显示 {{ dataDetail.rows.length }} 条
      </p></el-dialog
    >
  </div>
</template>
<style scoped>
.report-studio {
  height: calc(100vh - 124px);
  display: grid;
  grid-template-rows: 58px minmax(0, 1fr);
  background: #fff;
  min-height: 500px;
}
.report-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 15px;
  border-bottom: 1px solid #e5e7eb;
}
.spacer {
  flex: 1;
}
.report-body {
  display: flex;
  min-width: 0;
  min-height: 0;
}
.report-library {
  width: 210px;
  flex-shrink: 0;
  padding: 18px 12px;
  overflow: auto;
  border-right: 1px solid #e5e7eb;
}
.report-library h3 {
  font-size: 14px;
}
.component-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}
.component-grid button {
  background: #f7f9fc;
  border: 1px solid #e5eaf1;
  padding: 10px 4px;
  border-radius: 2px;
  color: #46617e;
}
.layer-button {
  width: 100%;
  text-align: left;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  border: 0;
  background: transparent;
  padding: 10px 8px;
  color: #666;
}
.layer-button.active {
  background: #e9effe;
  color: #2666fb;
}
.report-canvas {
  position: relative;
  flex: 1;
  min-width: 0;
  min-height: 0;
  background: #e9edf3;
  overflow: hidden;
}
.canvas-zoom {
  position: absolute;
  bottom: 16px;
  left: 16px;
  background: white;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 5px;
  border: 1px solid #ddd;
  border-radius: 2px;
}
.canvas-zoom span {
  font-size: 12px;
  width: 40px;
  text-align: center;
}
</style>
