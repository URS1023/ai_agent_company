<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { useSession } from "../../stores/session";
import { api, type Entity, type ListResult } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import StatusTag from "../../components/common/StatusTag.vue";
import ObservationForm from "./ObservationForm.vue";
import InspectionReportAction from "./InspectionReportAction.vue";
import ReinspectionPanel from "./ReinspectionPanel.vue";
import QualityCasePanel from "./QualityCasePanel.vue";
import PreparationQuerySelect from "../data/PreparationQuerySelect.vue";
import { usePreparationQueries } from "../data/usePreparationQueries";
const session = useSession();
const props = defineProps<{ inspectionId: string }>();
const emit = defineEmits<{ updated: []; back: []; navigate: [id: string] }>();
const op = useOperation();
const detail = ref<Entity | null>(null);
const samples = ref<Entity[]>([]);
const result = ref<Entity[]>([]);
const reviews = ref<Entity[]>([]);
const queryCatalog = usePreparationQueries();
const targetName = computed(
  () =>
    detail.value?.device_name ??
    detail.value?.product_name ??
    (detail.value?.target_type === "device" ? "设备" : "产品"),
);
const standardName = computed(
  () =>
    detail.value?.template_name ??
    detail.value?.standard_snapshot?.name ??
    "检验标准",
);
const evidenceRows = computed(() =>
  result.value.flatMap((sample, index) =>
    (sample.observations ?? []).map((observation: any) => ({
      sample:
        sample.external_ref ||
        samples.value.find((item) => item.id === sample.sample_id)
          ?.external_ref ||
        `样本 ${index + 1}`,
      metric:
        observation.name ??
        metricDefinitions.value.find(
          (metric: any) => metric.id === observation.metric_id,
        )?.name ??
        "检测项目",
      value:
        observation.value ??
        (Array.isArray(observation.defects)
          ? observation.defects.length
            ? `${observation.defects.length}处缺陷`
            : "未发现缺陷"
          : "—"),
      unit: observation.unit,
      source:
        observation.provenance?.type === "query"
          ? "数据库查询"
          : observation.provenance?.type === "manual"
            ? "人工补录"
            : "未登记",
      image_ref: observation.image_ref,
    })),
  ),
);
const metricDefinitions = computed(
  () =>
    detail.value?.metrics ??
    detail.value?.standard_snapshot?.metrics ??
    detail.value?.template_snapshot?.metrics ??
    detail.value?.template?.metrics ??
    [],
);
const observationDialog = ref(false);
const sampleDialog = ref(false);
const reviewDialog = ref(false);
const importDialog = ref(false);
const externalRef = ref("");
const review = reactive({ outcome: "review", note: "" });
const binding = reactive({
  query_id: "",
  sample_field: "",
  mappings: [] as {
    metric_id: string;
    value_field: string;
    defects_field: string;
    image_ref_field: string;
    unit_field: string;
    unit: string;
  }[],
});
const queryColumns = queryCatalog.columns;
watch(
  queryCatalog.queryId,
  (id) => {
    binding.query_id = id;
  },
  { flush: "sync" },
);
watch(
  () => binding.query_id,
  (id) => {
    queryCatalog.queryId.value = id;
  },
  { flush: "sync" },
);
async function load() {
  await op.run(async () => {
    const [record, sampleList, resultDetail] = await Promise.all([
      api.get<Entity>(`/inspections/${props.inspectionId}`),
      api.get<ListResult>(`/inspections/${props.inspectionId}/samples`),
      api.get<any>(`/inspections/${props.inspectionId}/results`),
    ]);
    detail.value = {
      ...record,
      standard_snapshot: resultDetail.standard_snapshot,
    };
    samples.value = sampleList.items as Entity[];
    result.value = (resultDetail.result?.samples ?? []).map(
      (sample: any, index: number) => ({
        ...sample,
        id: sample.sample_id,
        sample_label:
          sample.external_ref ||
          sampleList.items.find((item) => item.id === sample.sample_id)
            ?.external_ref ||
          `样本 ${index + 1}`,
        observations: sample.metrics?.map((metric: any) => ({
          ...metric.observation,
          metric_id: metric.metric_id,
          name: metric.name,
          outcome: metric.outcome,
          provenance: metric.provenance,
        })),
      }),
    );
    reviews.value = resultDetail.reviews ?? [];
  });
}
async function mutate(
  path: string,
  payload: Record<string, unknown>,
  success: string,
) {
  const saved = await op.run(async () => {
    await api.post(`/inspections/${props.inspectionId}/${path}`, {
      ...payload,
      ...(path === "samples" ? {} : { revision: detail.value?.revision }),
    });
    sampleDialog.value = false;
    observationDialog.value = false;
    reviewDialog.value = false;
    importDialog.value = false;
    emit("updated");
    return true;
  }, success);
  if (saved) await load();
}
async function createSample() {
  await mutate("samples", { external_ref: externalRef.value }, "样本已添加");
}
async function openImport() {
  await op.run(async () => {
    await queryCatalog.load();
    binding.mappings = metricDefinitions.value.map((metric: any) => ({
      metric_id: metric.id,
      value_field: "",
      defects_field: "",
      image_ref_field: "",
      unit_field: "",
      unit: metric.unit ?? "",
    }));
    importDialog.value = true;
  });
}
async function importData() {
  await mutate(
    "imports",
    {
      query_id: binding.query_id,
      sample_field: binding.sample_field,
      mappings: binding.mappings.map((row) =>
        Object.fromEntries(
          Object.entries(row).filter(([, value]) => value !== ""),
        ),
      ),
    },
    "数据已导入",
  );
}
watch(() => props.inspectionId, load, { immediate: true });
</script>
<template>
  <div class="page-stack" v-loading="op.busy.value">
    <div class="page-heading">
      <div class="row-actions">
        <el-button @click="emit('back')">返回</el-button>
        <h2>{{ detail?.name }}</h2>
        <StatusTag :value="detail?.status" />
      </div>
      <div class="row-actions">
        <InspectionReportAction v-if="detail" :inspection="detail" />
        <template v-if="session.can('m11.write')">
          <el-button
            v-if="session.can('m06.read') && detail?.status !== 'reviewed'"
            @click="openImport"
            >从查询导入</el-button
          ><el-button
            v-if="detail?.status !== 'reviewed'"
            @click="
              sampleDialog = true;
              externalRef = '';
            "
            >添加样本</el-button
          ><el-button
            v-if="detail?.status !== 'reviewed'"
            @click="observationDialog = true"
            :disabled="!samples.length"
            >补录观测</el-button
          ><el-button
            type="primary"
            @click="mutate('runs', {}, '检测已完成')"
            :disabled="!samples.length || detail?.status === 'reviewed'"
            >执行检测</el-button
          ><el-button
            type="primary"
            plain
            @click="reviewDialog = true"
            :disabled="
              !detail?.result?.samples?.length || detail?.status === 'reviewed'
            "
            >复核</el-button
          >
        </template>
      </div>
    </div>
    <section class="panel">
      <div class="detail-grid">
        <div>
          <div class="detail-label">检测对象</div>
          <div class="detail-value">
            {{ targetName }}
          </div>
        </div>
        <div>
          <div class="detail-label">标准版本</div>
          <div class="detail-value">
            {{ standardName }} · v{{ detail?.template_revision }}
          </div>
        </div>
        <div v-if="detail?.batch_number ?? detail?.batch_no">
          <div class="detail-label">批次</div>
          <div class="detail-value">
            {{ detail?.batch_number ?? detail?.batch_no }}
          </div>
        </div>
        <div>
          <div class="detail-label">检测结论</div>
          <StatusTag
            :value="
              detail?.outcome ??
              detail?.result?.outcome ??
              detail?.assessment?.outcome
            "
          />
        </div>
      </div>
    </section>
    <section class="panel">
      <el-tabs
        ><el-tab-pane label="检测结果"
          ><el-table :data="result" stripe
            ><el-table-column
              prop="sample_label"
              label="样本"
              min-width="200" /><el-table-column label="结论" width="140"
              ><template #default="{ row }"
                ><StatusTag
                  :value="
                    row.outcome ?? row.assessment?.outcome
                  " /></template></el-table-column
            ><el-table-column label="检测明细" min-width="300"
              ><template #default="{ row }"
                ><div
                  v-for="observation in row.observations ?? []"
                  :key="observation.metric_id"
                  style="line-height: 2"
                >
                  {{
                    metricDefinitions.find(
                      (m: any) => m.id === observation.metric_id,
                    )?.name ??
                    observation.name ??
                    "检测项目"
                  }}：{{
                    observation.value ??
                    (Array.isArray(observation.defects)
                      ? observation.defects.length
                        ? `${observation.defects.length}处缺陷`
                        : "未发现缺陷"
                      : "—")
                  }}
                  {{ observation.unit
                  }}<StatusTag
                    v-if="observation.outcome"
                    :value="observation.outcome"
                  /></div></template></el-table-column></el-table></el-tab-pane
        ><el-tab-pane label="样本"
          ><el-table :data="samples"
            ><el-table-column
              type="index"
              label="序号"
              width="80" /><el-table-column
              prop="external_ref"
              label="样本编号"
              min-width="180" /><el-table-column
              prop="created_at"
              label="登记时间"
              width="200" /></el-table></el-tab-pane
        ><el-tab-pane label="复核记录"
          ><el-table :data="reviews" stripe
            ><el-table-column label="复核结论" width="140"
              ><template #default="{ row }"
                ><StatusTag :value="row.outcome" /></template></el-table-column
            ><el-table-column prop="note" label="复核记录" /><el-table-column
              prop="reviewer_name"
              label="复核人"
              width="130" /><el-table-column
              prop="created_at"
              label="时间"
              width="185" /></el-table></el-tab-pane
        ><el-tab-pane label="检测证据">
          <el-table :data="evidenceRows"
            ><el-table-column prop="sample" label="样本" /><el-table-column
              prop="metric"
              label="检测项目" /><el-table-column label="观测"
              ><template #default="{ row }"
                >{{ row.value }} {{ row.unit }}</template
              ></el-table-column
            ><el-table-column
              prop="source"
              label="来源"
              width="140" /><el-table-column
              prop="image_ref"
              label="图片证据"
              min-width="200"
              show-overflow-tooltip
          /></el-table> </el-tab-pane
        ><el-tab-pane label="复检记录"
          ><ReinspectionPanel
            v-if="detail"
            :inspection="detail"
            @changed="
              load();
              emit('updated');
            "
            @open-inspection="emit('navigate', $event)" /></el-tab-pane
        ><el-tab-pane label="异常处置"
          ><QualityCasePanel
            v-if="detail"
            :inspection="detail"
            @changed="
              load();
              emit('updated');
            "
            @open-inspection="emit('navigate', $event)"
        /></el-tab-pane>
      </el-tabs>
    </section>
    <el-dialog v-model="sampleDialog" title="添加样本" width="520px"
      ><el-form label-position="top" @submit.prevent="createSample"
        ><el-form-item label="样本编号" :error="op.fields.value.external_ref"
          ><el-input
            v-model="externalRef"
            required
            maxlength="160" /></el-form-item
        ><el-button type="primary" native-type="submit" :loading="op.busy.value"
          >添加</el-button
        ></el-form
      ></el-dialog
    ><el-dialog
      v-model="observationDialog"
      title="补录观测"
      width="760px"
      destroy-on-close
      ><ObservationForm
        :metrics="metricDefinitions"
        :samples="samples"
        :busy="op.busy.value"
        :errors="op.fields.value"
        @cancel="observationDialog = false"
        @submit="mutate('results', $event, '观测已保存')" /></el-dialog
    ><el-dialog v-model="reviewDialog" title="复核" width="600px"
      ><el-form
        label-position="top"
        @submit.prevent="mutate('reviews', review, '复核已保存')"
        ><el-form-item label="复核结论"
          ><el-radio-group v-model="review.outcome"
            ><el-radio-button value="passed">合格</el-radio-button
            ><el-radio-button value="failed">不合格</el-radio-button
            ><el-radio-button value="review"
              >继续检查</el-radio-button
            ></el-radio-group
          ></el-form-item
        ><el-form-item label="复核记录" :error="op.fields.value.note"
          ><el-input
            v-model="review.note"
            type="textarea"
            :rows="4"
            required
            maxlength="2000" /></el-form-item
        ><el-button type="primary" native-type="submit" :loading="op.busy.value"
          >提交复核</el-button
        ></el-form
      ></el-dialog
    ><el-dialog v-model="importDialog" title="导入检测数据" width="850px"
      ><el-form label-position="top" @submit.prevent="importData"
        ><div class="form-grid">
          <el-form-item label="数据查询"
            ><PreparationQuerySelect :catalog="queryCatalog" /></el-form-item
          ><el-form-item label="样本编号字段"
            ><el-select v-model="binding.sample_field" filterable allow-create
              ><el-option
                v-for="column in queryColumns"
                :key="String(column)"
                :value="String(column)"
                :label="String(column)" /></el-select
          ></el-form-item>
        </div>
        <el-table :data="binding.mappings"
          ><el-table-column label="检测项目"
            ><template #default="{ row }">{{
              metricDefinitions.find((m: any) => m.id === row.metric_id)?.name
            }}</template></el-table-column
          ><el-table-column label="数值字段"
            ><template #default="{ row }"
              ><el-select
                v-if="
                  metricDefinitions.find((m: any) => m.id === row.metric_id)
                    ?.category !== 'appearance'
                "
                v-model="row.value_field"
                allow-create
                filterable
                ><el-option
                  v-for="column in queryColumns"
                  :key="String(column)"
                  :value="String(column)"
                  :label="
                    String(column)
                  " /></el-select></template></el-table-column
          ><el-table-column label="缺陷字段"
            ><template #default="{ row }"
              ><el-select
                v-if="
                  metricDefinitions.find((m: any) => m.id === row.metric_id)
                    ?.category === 'appearance'
                "
                v-model="row.defects_field"
                clearable
                filterable
                ><el-option
                  v-for="column in queryColumns"
                  :key="String(column)"
                  :value="String(column)"
                  :label="String(column)" /></el-select
              ><span v-else>{{ row.unit }}</span></template
            ></el-table-column
          ><el-table-column label="图片证据字段"
            ><template #default="{ row }"
              ><el-select
                v-model="row.image_ref_field"
                clearable
                allow-create
                filterable
                ><el-option
                  v-for="column in queryColumns"
                  :key="String(column)"
                  :value="String(column)"
                  :label="
                    String(column)
                  " /></el-select></template></el-table-column></el-table
        ><el-button
          type="primary"
          native-type="submit"
          :disabled="
            !binding.query_id ||
            !binding.sample_field ||
            queryCatalog.selecting.value ||
            queryCatalog.loading.value
          "
          :loading="op.busy.value"
          style="margin-top: 20px"
          >导入</el-button
        ></el-form
      ></el-dialog
    >
  </div>
</template>
