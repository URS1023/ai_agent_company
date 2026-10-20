<script setup lang="ts">
import { reactive, watch } from "vue";
import type { Entity } from "../../api/client";
import SecretPicker from "../native/SecretPicker.vue";
const props = defineProps<{
  entity?: Entity | null;
  busy: boolean;
  errors: Record<string, string>;
}>();
const emit = defineEmits<{
  submit: [payload: Record<string, unknown>];
  cancel: [];
}>();
const form = reactive({
  name: "",
  provider: "sqlite",
  description: "",
  max_rows: 1000,
  credential_ref: "",
  connection_config: {
    host: "",
    port: 5432,
    database: "",
    schema_name: "public",
    username: "",
    tls: true,
    endpoint_path: "/data",
    response_key: "",
  },
});
watch(
  () => props.entity,
  (value) => {
    Object.assign(form, {
      name: value?.name ?? "",
      provider: value?.provider ?? "sqlite",
      description: value?.description ?? "",
      max_rows: value?.max_rows ?? 1000,
      credential_ref: value?.credential_ref ?? "",
      connection_config: {
        host: "",
        port: 5432,
        database: "",
        schema_name: "public",
        username: "",
        tls: true,
        endpoint_path: "/data",
        response_key: "",
        ...value?.connection_config,
      },
    });
  },
  { immediate: true },
);
function changeProvider() {
  form.connection_config.port =
    form.provider === "mysql"
      ? 3306
      : form.provider === "http_json"
        ? 443
        : form.provider === "elasticsearch"
          ? 9200
          : 5432;
}
function submit() {
  emit("submit", {
    name: form.name,
    ...(props.entity ? {} : { provider: form.provider }),
    description: form.description,
    max_rows: form.max_rows,
    ...(form.provider !== "sqlite"
      ? {
          connection_config: form.connection_config,
          credential_ref: form.credential_ref || null,
        }
      : {}),
  });
}
</script>
<template>
  <el-form label-position="top" @submit.prevent="submit"
    ><div class="form-grid">
      <el-form-item label="名称" :error="errors.name"
        ><el-input v-model="form.name" required maxlength="160" /></el-form-item
      ><el-form-item label="类型"
        ><el-select
          v-model="form.provider"
          :disabled="!!entity"
          @change="changeProvider"
          ><el-option label="本地数据集" value="sqlite" /><el-option
            label="PostgreSQL"
            value="postgres" /><el-option
            label="MySQL"
            value="mysql" /><el-option
            label="Elasticsearch"
            value="elasticsearch" /><el-option
            label="HTTP JSON"
            value="http_json" /></el-select></el-form-item
      ><template v-if="form.provider !== 'sqlite'"
        ><el-form-item label="主机" :error="errors['connection_config.host']"
          ><el-input
            v-model="form.connection_config.host"
            required /></el-form-item
        ><el-form-item label="端口"
          ><el-input-number
            v-model="form.connection_config.port"
            :min="1"
            :max="65535"
            :precision="0" /></el-form-item
        ><el-form-item
          v-if="form.provider !== 'http_json'"
          :label="form.provider === 'elasticsearch' ? '索引' : '数据库'"
          :error="errors['connection_config.database']"
          ><el-input
            v-model="form.connection_config.database"
            required /></el-form-item
        ><el-form-item v-if="form.provider === 'postgres'" label="Schema"
          ><el-input
            v-model="form.connection_config.schema_name" /></el-form-item
        ><el-form-item v-if="form.provider !== 'http_json'" label="用户名"
          ><el-input
            v-model="form.connection_config.username"
            autocomplete="off" /></el-form-item
        ><el-form-item label="凭据"
          ><SecretPicker v-model="form.credential_ref" /></el-form-item
        ><el-form-item v-if="form.provider === 'http_json'" label="请求路径"
          ><el-input
            v-model="form.connection_config.endpoint_path"
            required
            pattern="/.*" /></el-form-item
        ><el-form-item v-if="form.provider === 'http_json'" label="响应列表字段"
          ><el-input
            v-model="form.connection_config.response_key" /></el-form-item
        ><el-form-item label="TLS"
          ><el-switch
            v-model="form.connection_config.tls" /></el-form-item></template
      ><el-form-item label="单次查询行数上限"
        ><el-input-number
          v-model="form.max_rows"
          :min="1"
          :max="10000"
          :precision="0" /></el-form-item
      ><el-form-item label="备注" class="full-width"
        ><el-input
          v-model="form.description"
          type="textarea"
          :rows="3"
          maxlength="2000"
      /></el-form-item>
    </div>
    <div class="row-actions" style="justify-content: flex-end">
      <el-button @click="emit('cancel')">取消</el-button
      ><el-button type="primary" native-type="submit" :loading="busy"
        >保存数据源</el-button
      >
    </div></el-form
  >
</template>
