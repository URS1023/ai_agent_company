<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { api, type Entity, type ListResult } from "../../api/client";
import { useSession } from "../../stores/session";
import { useOperation } from "../../composables/useOperation";
const props = defineProps<{
  sourceId: string;
  sources: Entity[];
  busy: boolean;
  errors: Record<string, string>;
}>();
const emit = defineEmits<{
  submit: [payload: Record<string, unknown>];
  sourceChange: [id: string];
}>();
const session = useSession();
const op = useOperation();
const models = ref<Entity[]>([]);
const form = reactive({
  datasource_id: props.sourceId,
  question: "",
  model_profile_id: "",
  max_rows: 100,
  execute: false,
});
onMounted(async () => {
  if (session.can("m15.read"))
    await op.run(async () => {
      models.value = (
        await api.get<ListResult>("/model-profiles?limit=100")
      ).items.filter((model) =>
        ["chat", "reasoning"].includes(model.role),
      ) as Entity[];
    });
});
</script>
<template>
  <el-form label-position="top" @submit.prevent="emit('submit', form)"
    ><div class="form-grid">
      <el-form-item label="数据源" :error="errors.datasource_id"
        ><el-select
          v-model="form.datasource_id"
          filterable
          @change="emit('sourceChange', $event)"
          ><el-option
            v-for="source in sources"
            :key="source.id"
            :value="source.id"
            :label="source.name" /></el-select></el-form-item
      ><el-form-item label="模型" :error="errors.model_profile_id"
        ><el-select v-model="form.model_profile_id" filterable
          ><el-option
            v-for="model in models"
            :key="model.id"
            :value="model.id"
            :label="model.name" /></el-select></el-form-item
      ><el-form-item
        label="查询问题"
        :error="errors.question"
        class="full-width"
        ><el-input
          v-model="form.question"
          type="textarea"
          :rows="5"
          placeholder="描述需要分析的数据"
          required
          minlength="2"
          maxlength="2000"
      /></el-form-item>
    </div>
    <div class="toolbar">
      <el-checkbox v-model="form.execute">生成后执行</el-checkbox
      ><span class="spacer"></span
      ><RouterLink
        v-if="!models.length && session.can('m15.write')"
        to="/models"
        >配置模型</RouterLink
      ><el-button
        type="primary"
        native-type="submit"
        :loading="busy"
        :disabled="!form.datasource_id || !form.model_profile_id"
        >{{ form.execute ? "执行分析" : "生成查询" }}</el-button
      >
    </div></el-form
  >
</template>
