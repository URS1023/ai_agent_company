<script setup lang="ts">
import { onMounted } from "vue";
import { useSession } from "../../stores/session";
import { useBusinessMetrics } from "./useBusinessMetrics";
import PreparationQuerySelect from "./PreparationQuerySelect.vue";
import PreparationHistory from "./PreparationHistory.vue";
import { usePreparationHistory } from "./usePreparationHistory";
import BusinessMetricList from "./BusinessMetricList.vue";
import MetricDefinitionForm from "./MetricDefinitionForm.vue";
import PreparationResult from "./PreparationResult.vue";
const session = useSession();
const state = useBusinessMetrics();
const history = usePreparationHistory();
onMounted(() => state.load(session.can("m06.read")));
</script>
<template>
  <div class="page-stack">
    <div class="panel toolbar">
      <el-input
        v-model="state.list.query.value"
        placeholder="搜索指标"
        clearable
        @keyup.enter="state.list.load"
      /><el-button @click="state.list.load">查询</el-button
      ><span class="spacer" /><el-button
        v-if="session.can('m05.write')"
        type="primary"
        :disabled="state.op.busy.value"
        @click="state.edit(null)"
        >新增指标</el-button
      >
    </div>
    <section class="panel">
      <BusinessMetricList
        :items="state.list.items.value"
        :loading="state.list.loading.value"
        :can-write="session.can('m05.write')"
        :can-delete="session.can('m05.delete')"
        :can-execute="session.can('m06.write')"
        :can-read-history="session.can('m06.read')"
        @edit="state.edit"
        @remove="state.remove"
        @execute="state.startExecution"
        @history="history.open($event.id)"
      /><el-pagination
        v-model:current-page="state.list.page.value"
        :total="state.list.total.value"
        :page-size="state.list.size.value"
        layout="total,prev,pager,next"
        @current-change="state.list.load"
      />
    </section>
    <PreparationResult
      v-if="state.result.value"
      :job="state.result.value"
      metric
      @permission-denied="state.result.value = null"
    />
    <el-dialog
      v-model="state.dialog.value"
      :title="state.selected.value ? '指标口径' : '新增指标'"
      width="860px"
      destroy-on-close
      :close-on-click-modal="false"
    >
      <el-form
        label-position="top"
        :disabled="state.op.busy.value || !session.can('m05.write')"
        @submit.prevent="state.save"
      >
        <div class="form-grid">
          <el-form-item label="指标名称" :error="state.errors.value.name"
            ><el-input v-model="state.name.value" /></el-form-item
          ><el-form-item
            label="数据源（可选）"
            :error="state.errors.value.datasource_id"
            ><el-select v-model="state.datasourceId.value" clearable filterable
              ><el-option
                v-for="source in state.datasources.value"
                :key="source.id"
                :value="source.id"
                :label="source.name" /></el-select></el-form-item
          ><el-form-item label="字段参考" class="full-width"
            ><PreparationQuerySelect
              :catalog="state.queries"
              :disabled="!session.can('m06.read')"
            />
          </el-form-item>
        </div>
        <MetricDefinitionForm
          v-model="state.definition.value"
          :columns="state.queries.columns.value"
          :errors="state.errors.value"
        />
      </el-form>
      <template #footer
        ><el-button @click="state.dialog.value = false">关闭</el-button
        ><el-button
          v-if="session.can('m05.write')"
          type="primary"
          :loading="state.op.busy.value"
          @click="state.save"
          >保存</el-button
        ></template
      >
    </el-dialog>
    <el-dialog
      :model-value="state.executing.value !== null"
      title="执行指标"
      width="680px"
      :close-on-click-modal="false"
      @close="state.executing.value = null"
      ><el-form
        label-position="top"
        :disabled="state.op.busy.value"
        @submit.prevent="state.execute"
        ><el-form-item label="指标">{{
          state.executing.value?.name
        }}</el-form-item
        ><el-form-item label="数据查询" :error="state.errors.value.query_id"
          ><PreparationQuerySelect
            :catalog="state.queries" /></el-form-item></el-form
      ><template #footer
        ><el-button
          :disabled="state.op.busy.value"
          @click="state.executing.value = null"
          >取消</el-button
        ><el-button
          type="primary"
          :loading="state.op.busy.value"
          :disabled="
            !state.queries.queryId.value ||
            state.queries.selecting.value ||
            state.queries.loading.value
          "
          @click="state.execute"
          >执行</el-button
        ></template
      ></el-dialog
    >
    <PreparationHistory :history="history" />
  </div>
</template>
