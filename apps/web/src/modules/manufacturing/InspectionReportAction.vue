<script setup lang="ts">
/** 从准确的不可变检测记录生成报告；只读质检权限也可配合报表写权限使用。 */
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import { api, type Entity, type ListResult } from "../../api/client";
import { useSession } from "../../stores/session";
import { useOperation } from "../../composables/useOperation";
import StatusTag from "../../components/common/StatusTag.vue";

const props = defineProps<{ inspection: Entity }>();
const router = useRouter();
const session = useSession();
const op = useOperation();
const dialog = ref(false);
const currentInspection = ref<Entity>(props.inspection);
const runs = ref<Entity[]>([]);
const selectedRun = ref("");
const name = ref("");
const allowed = computed(
  () => session.can("m11.read") && session.can("m07.write"),
);
const outcomes: Record<string, string> = {
  passed: "合格",
  failed: "不合格",
  review: "待复核",
  incomplete: "证据不足",
  needs_review: "待复核",
};

function dateText(value: unknown) {
  const date = new Date(String(value ?? ""));
  return Number.isNaN(date.getTime())
    ? "时间未登记"
    : date.toLocaleString("zh-CN", { hour12: false });
}

function runLabel(run: Entity, index: number) {
  return `第${runs.value.length - index}次 · ${dateText(run.result?.calculated_at ?? run.created_at)} · ${outcomes[run.result?.outcome] ?? "待核对"}`;
}

async function loadRuns() {
  await op.run(async () => {
    const [inspection, result] = await Promise.all([
      api.get<Entity>(`/inspections/${props.inspection.id}`),
      api.get<ListResult<Entity>>(`/inspections/${props.inspection.id}/runs`),
    ]);
    currentInspection.value = inspection;
    runs.value = result.items;
    const latest = inspection.result?.run_id;
    selectedRun.value = result.items.some((run) => run.id === latest)
      ? latest
      : (result.items[0]?.id ?? "");
  });
}

async function open() {
  dialog.value = true;
  runs.value = [];
  selectedRun.value = "";
  name.value = `${String(props.inspection.name ?? "质检").slice(0, 156)}质检报告`;
  await loadRuns();
}

async function generate() {
  if (!allowed.value || !selectedRun.value || !name.value.trim()) return;
  await op.run(async () => {
    const report = await api.post<Entity>("/inspection-report-generations", {
      inspection_id: currentInspection.value.id,
      run_id: selectedRun.value,
      inspection_revision: currentInspection.value.revision,
      name: name.value.trim(),
    });
    dialog.value = false;
    await router.push({ path: "/reports", query: { report: report.id } });
  }, "报告已生成");
}
</script>

<template>
  <el-button v-if="allowed" @click="open">生成报告</el-button>
  <el-dialog
    v-model="dialog"
    title="生成质检报告"
    width="700px"
    destroy-on-close
  >
    <el-skeleton v-if="op.busy.value && !runs.length" :rows="3" animated />
    <el-form
      v-else-if="runs.length"
      label-position="top"
      @submit.prevent="generate"
    >
      <el-form-item label="报告名称" :error="op.fields.value.name">
        <el-input v-model="name" required maxlength="160" />
      </el-form-item>
      <el-form-item label="检测记录" :error="op.fields.value.run_id">
        <el-select v-model="selectedRun" style="width: 100%">
          <el-option
            v-for="(run, index) in runs"
            :key="run.id"
            :value="run.id"
            :label="runLabel(run, index)"
          >
            <div class="run-option">
              <span
                >第{{ runs.length - index }}次 ·
                {{
                  dateText(run.result?.calculated_at ?? run.created_at)
                }}</span
              ><StatusTag :value="run.result?.outcome" /><span
                >{{
                  run.result?.actual_samples ?? run.result?.samples?.length ?? 0
                }}
                个样本</span
              >
            </div>
          </el-option>
        </el-select>
      </el-form-item>
      <div class="run-actions">
        <el-button @click="dialog = false">取消</el-button
        ><el-button
          type="primary"
          native-type="submit"
          :disabled="!selectedRun || !name.trim()"
          :loading="op.busy.value"
          >生成报告</el-button
        >
      </div>
    </el-form>
    <el-empty
      v-else
      :description="op.error.value || '暂无检测记录'"
      :image-size="75"
      ><el-button v-if="op.error.value" @click="loadRuns"
        >重新加载</el-button
      ></el-empty
    >
  </el-dialog>
</template>

<style scoped>
.run-option {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 14px;
}
.run-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 22px;
}
</style>
