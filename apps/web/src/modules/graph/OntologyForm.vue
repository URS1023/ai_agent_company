<script setup lang="ts">
import { computed, reactive, watch } from "vue";
import { cloneDto } from "../../utils/cloneDto";
const props = defineProps<{
  entity?: Record<string, any> | null;
  busy: boolean;
  errors: Record<string, string>;
}>();
const emit = defineEmits<{
  submit: [payload: Record<string, unknown>];
  cancel: [];
}>();
interface Concept {
  name: string;
  properties: { name: string; type: string }[];
}
const form = reactive({
  name: "",
  description: "",
  concepts: [] as Concept[],
  relations: [] as { name: string; source_type: string; target_type: string }[],
});
watch(
  () => props.entity,
  (value) => {
    form.name = value?.name ?? "";
    form.description = value?.description ?? "";
    form.concepts = (value?.concepts ?? []).map((concept: any) => ({
      name: concept.name,
      properties: Object.entries(concept.properties ?? {}).map(
        ([name, type]) => ({ name, type }),
      ),
    }));
    form.relations = cloneDto(value?.relations ?? []);
  },
  { immediate: true },
);
const typeNames = computed(() =>
  form.concepts.map((x) => x.name).filter(Boolean),
);
function submit() {
  emit("submit", {
    name: form.name,
    description: form.description,
    concepts: form.concepts.map((concept) => ({
      name: concept.name,
      properties: Object.fromEntries(
        concept.properties.map((property) => [property.name, property.type]),
      ),
    })),
    relations: form.relations,
  });
}
</script>
<template>
  <el-form label-position="top" @submit.prevent="submit"
    ><el-form-item label="本体名称" :error="errors.name"
      ><el-input v-model="form.name" required maxlength="160"
    /></el-form-item>
    <div class="panel-heading">
      <h3>实体类型</h3>
      <el-button @click="form.concepts.push({ name: '', properties: [] })"
        >添加类型</el-button
      >
    </div>
    <div
      v-for="(concept, index) in form.concepts"
      :key="index"
      class="concept-block"
    >
      <div class="row-actions">
        <el-input
          v-model="concept.name"
          placeholder="类型名称"
          required
        /><el-button link type="danger" @click="form.concepts.splice(index, 1)"
          >删除类型</el-button
        >
      </div>
      <div
        v-for="(property, propertyIndex) in concept.properties"
        :key="propertyIndex"
        class="property-row"
      >
        <el-input
          v-model="property.name"
          placeholder="属性名称"
          required
        /><el-select v-model="property.type"
          ><el-option label="文本" value="string" /><el-option
            label="数字"
            value="number" /><el-option
            label="是/否"
            value="boolean" /></el-select
        ><el-button
          link
          type="danger"
          @click="concept.properties.splice(propertyIndex, 1)"
          >删除</el-button
        >
      </div>
      <el-button
        link
        type="primary"
        style="margin-top: 10px"
        @click="concept.properties.push({ name: '', type: 'string' })"
        >添加属性</el-button
      >
    </div>
    <div class="panel-heading" style="margin-top: 24px">
      <h3>关系类型</h3>
      <el-button
        :disabled="!typeNames.length"
        @click="
          form.relations.push({ name: '', source_type: '', target_type: '' })
        "
        >添加关系</el-button
      >
    </div>
    <div
      v-for="(relation, index) in form.relations"
      :key="index"
      class="relation-row"
    >
      <el-select v-model="relation.source_type" placeholder="起点类型"
        ><el-option
          v-for="name in typeNames"
          :key="name"
          :value="name"
          :label="name" /></el-select
      ><el-input
        v-model="relation.name"
        placeholder="关系名称"
        required
      /><el-select v-model="relation.target_type" placeholder="终点类型"
        ><el-option
          v-for="name in typeNames"
          :key="name"
          :value="name"
          :label="name" /></el-select
      ><el-button link type="danger" @click="form.relations.splice(index, 1)"
        >删除</el-button
      >
    </div>
    <el-form-item label="描述" style="margin-top: 20px"
      ><el-input v-model="form.description" type="textarea" :rows="3"
    /></el-form-item>
    <div class="row-actions" style="justify-content: flex-end">
      <el-button @click="emit('cancel')">取消</el-button
      ><el-button
        type="primary"
        native-type="submit"
        :loading="busy"
        :disabled="!form.concepts.length"
        >保存本体</el-button
      >
    </div></el-form
  >
</template>
<style scoped>
.concept-block {
  border: 1px solid #e1e7f0;
  padding: 18px;
  margin-bottom: 12px;
}
.property-row {
  display: grid;
  grid-template-columns: 1fr 130px 60px;
  gap: 12px;
  margin-top: 12px;
}
.relation-row {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr 50px;
  gap: 12px;
  margin-bottom: 12px;
}
</style>
