<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { api, type Entity, type ListResult } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import StatusTag from "../../components/common/StatusTag.vue";
const op = useOperation();
const files = ref<Entity[]>([]);
const result = ref<Entity | null>(null);
const form = reactive({
  title: "",
  format: "docx",
  source_file_ids: [] as string[],
  sections: [{ title: "", text: "" }],
});
async function submit() {
  await op.run(async () => {
    result.value = await api.post<Entity>("/document-jobs", form);
  }, "文档已生成");
}
async function download() {
  const file = result.value?.outputs?.file;
  if (!file) return;
  await op.run(async () => {
    const link = await api.post<{ url: string }>(
      `/files/${file.id}/download-links`,
      {},
    );
    const anchor = document.createElement("a");
    anchor.href = link.url;
    anchor.download = file.name;
    anchor.click();
  });
}
onMounted(() =>
  op.run(async () => {
    files.value = (await api.get<ListResult>("/files?limit=200"))
      .items as Entity[];
  }),
);
</script>
<template>
  <div class="two-columns">
    <section class="panel">
      <div class="panel-heading"><h3>生成文档</h3></div>
      <el-form label-position="top" @submit.prevent="submit"
        ><div class="form-grid">
          <el-form-item label="标题" :error="op.fields.value.title"
            ><el-input
              v-model="form.title"
              required
              maxlength="160" /></el-form-item
          ><el-form-item label="格式"
            ><el-radio-group v-model="form.format"
              ><el-radio-button value="docx">Word</el-radio-button
              ><el-radio-button value="xlsx">Excel</el-radio-button
              ><el-radio-button value="pptx"
                >PPT</el-radio-button
              ></el-radio-group
            ></el-form-item
          ><el-form-item label="关联文件" class="full-width"
            ><el-select v-model="form.source_file_ids" multiple filterable
              ><el-option
                v-for="file in files"
                :key="file.id"
                :value="file.id"
                :label="file.name" /></el-select
          ></el-form-item>
        </div>
        <div
          v-for="(section, index) in form.sections"
          :key="index"
          class="document-section"
        >
          <div class="row-actions">
            <el-input
              v-model="section.title"
              placeholder="章节标题"
            /><el-button
              link
              type="danger"
              :disabled="form.sections.length === 1"
              @click="form.sections.splice(index, 1)"
              >删除</el-button
            >
          </div>
          <el-input
            v-model="section.text"
            type="textarea"
            :rows="5"
            placeholder="章节内容"
            style="margin-top: 12px"
            required
          />
        </div>
        <div class="row-actions">
          <el-button @click="form.sections.push({ title: '', text: '' })"
            >添加章节</el-button
          ><span style="flex: 1"></span
          ><el-button
            type="primary"
            native-type="submit"
            :loading="op.busy.value"
            >生成文档</el-button
          >
        </div></el-form
      >
    </section>
    <section class="panel">
      <div class="panel-heading"><h3>生成结果</h3></div>
      <template v-if="result"
        ><StatusTag :value="result.status" />
        <h3 style="margin-top: 20px">{{ result.outputs?.file?.name }}</h3>
        <el-button
          type="primary"
          :disabled="!result.outputs?.file"
          @click="download"
          >下载文档</el-button
        ></template
      ><el-empty v-else description="暂无生成文档" :image-size="80" />
    </section>
  </div>
</template>
<style scoped>
.document-section {
  padding: 18px;
  border: 1px solid #e7ebf2;
  margin-bottom: 15px;
}
</style>
