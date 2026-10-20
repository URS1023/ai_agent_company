<script setup lang="ts">
import { computed, reactive } from "vue";
import type { Entity } from "../../api/client";

const props = defineProps<{
  source: Entity;
  busy: boolean;
  errors: Record<string, string>;
}>();
const emit = defineEmits<{
  submit: [payload: Record<string, unknown>];
  cancel: [];
}>();
const target = computed(() => {
  const config = props.source.connection_config ?? {};
  const ports: Record<string, number> = {
    postgres: 5432,
    mysql: 3306,
    elasticsearch: 9200,
  };
  return {
    ...config,
    host: String(config.host ?? "").trim(),
    port:
      config.port ??
      (props.source.provider === "http_json"
        ? config.tls === false
          ? 80
          : 443
        : ports[props.source.provider]),
    database: config.database ?? "",
  };
});
const localRecovery = computed(() => props.source.provider === "sqlite");
const form = reactive({
  service_owner: "",
  expires_at: new Date(Date.now() + 4 * 60 * 60 * 1000),
  scope: ["metadata", "query"],
  note: "",
  resume_after_failure: false,
});
const valid = computed(
  () =>
    form.note.trim().length >= 5 &&
    (localRecovery.value ||
      (form.service_owner.trim().length >= 3 &&
        form.scope.length > 0 &&
        form.expires_at instanceof Date &&
        form.expires_at.getTime() > Date.now() &&
        form.expires_at.getTime() <= Date.now() + 86400000 &&
        (props.source.status !== "blocked" || form.resume_after_failure))),
);

function disabledDate(date: Date) {
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  return (
    date.getTime() < now.getTime() || date.getTime() > Date.now() + 86400000
  );
}
function submit() {
  if (!valid.value) return;
  emit("submit", {
    revision: props.source.revision,
    note: form.note.trim(),
    ...(localRecovery.value
      ? {}
      : {
          host: target.value.host,
          port: target.value.port,
          database: target.value.database ?? "",
          service_owner: form.service_owner.trim(),
          expires_at: form.expires_at.toISOString(),
          scope: form.scope,
          resume_after_failure: form.resume_after_failure,
        }),
  });
}
</script>

<template>
  <el-form label-position="top" @submit.prevent="submit">
    <div class="form-grid">
      <template v-if="!localRecovery">
        <el-form-item label="主机"
          ><el-input :model-value="target.host" readonly
        /></el-form-item>
        <el-form-item label="端口"
          ><el-input :model-value="String(target.port ?? '')" readonly
        /></el-form-item>
        <el-form-item
          v-if="source.provider !== 'http_json'"
          :label="source.provider === 'elasticsearch' ? '索引' : '数据库'"
        >
          <el-input :model-value="target.database" readonly />
        </el-form-item>
        <el-form-item label="服务归属" :error="errors.service_owner">
          <el-input
            v-model="form.service_owner"
            required
            minlength="3"
            maxlength="300"
          />
        </el-form-item>
        <el-form-item label="有效期至" :error="errors.expires_at">
          <el-date-picker
            v-model="form.expires_at"
            type="datetime"
            :clearable="false"
            :disabled-date="disabledDate"
            format="YYYY-MM-DD HH:mm"
          />
        </el-form-item>
        <el-form-item label="允许操作" :error="errors.scope">
          <el-checkbox-group v-model="form.scope">
            <el-checkbox value="metadata">读取元数据</el-checkbox>
            <el-checkbox value="query">查询数据</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
      </template>
      <el-form-item
        :label="source.status === 'blocked' ? '异常核查结果' : '访问用途'"
        class="full-width"
        :error="errors.note"
      >
        <el-input
          v-model="form.note"
          type="textarea"
          required
          minlength="5"
          maxlength="2000"
          :rows="3"
        />
      </el-form-item>
      <el-form-item
        v-if="!localRecovery && source.status === 'blocked'"
        class="full-width"
      >
        <el-checkbox v-model="form.resume_after_failure"
          >已完成异常核查，恢复访问</el-checkbox
        >
      </el-form-item>
    </div>
    <div class="row-actions" style="justify-content: flex-end">
      <el-button @click="emit('cancel')">取消</el-button>
      <el-button
        type="primary"
        native-type="submit"
        :disabled="!valid"
        :loading="busy"
        >{{ localRecovery ? "恢复访问" : "批准访问" }}</el-button
      >
    </div>
  </el-form>
</template>
