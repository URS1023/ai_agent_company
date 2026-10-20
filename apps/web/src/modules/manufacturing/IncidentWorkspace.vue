<script setup lang="ts">
import { onMounted, ref, reactive } from "vue";
import { useSession } from "../../stores/session";
import { api, type Entity } from "../../api/client";
import { useCollection } from "../../composables/useCollection";
import { useOperation } from "../../composables/useOperation";
import StatusTag from "../../components/common/StatusTag.vue";
const session = useSession();
const list = useCollection(() => "/incidents");
const op = useOperation();
const selected = ref<Entity | null>(null);
const form = reactive({ action: "acknowledge", note: "" });
async function save() {
  if (!selected.value) return;
  await op.run(async () => {
    await api.post(`/incidents/${selected.value!.id}/actions`, {
      revision: selected.value!.revision,
      ...form,
    });
    selected.value = null;
    await list.load();
  }, "处置已保存");
}
onMounted(list.load);
</script>
<template>
  <div class="page-stack">
    <div class="panel toolbar">
      <el-input
        v-model="list.query.value"
        placeholder="预警名称"
        @keyup.enter="list.load"
      /><el-button @click="list.load">查询</el-button>
    </div>
    <section class="panel">
      <el-table :data="list.items.value" stripe v-loading="list.loading.value"
        ><el-table-column
          prop="name"
          label="预警"
          min-width="220"
        /><el-table-column
          prop="device_name"
          label="设备"
          width="180"
        /><el-table-column label="风险等级" width="130"
          ><template #default="{ row }"
            ><StatusTag :value="row.severity" /></template></el-table-column
        ><el-table-column label="状态" width="130"
          ><template #default="{ row }"
            ><StatusTag :value="row.status" /></template></el-table-column
        ><el-table-column
          prop="created_at"
          label="发现时间"
          width="190"
        /><el-table-column width="100"
          ><template #default="{ row }"
            ><el-button
              v-if="session.can('m10.write')"
              link
              type="primary"
              @click="selected = row"
              >处置</el-button
            ></template
          ></el-table-column
        ></el-table
      >
    </section>
    <el-dialog
      :model-value="!!selected"
      :title="selected?.name ?? '预警处置'"
      width="600px"
      @close="selected = null"
      ><el-form label-position="top" @submit.prevent="save"
        ><el-form-item label="处置动作"
          ><el-select v-model="form.action"
            ><el-option value="acknowledge" label="受理" /><el-option
              value="resolve"
              label="解决" /><el-option
              value="reopen"
              label="重新打开" /><el-option
              value="comment"
              label="补充记录" /></el-select></el-form-item
        ><el-form-item label="处置记录" :error="op.fields.value.note"
          ><el-input
            v-model="form.note"
            type="textarea"
            :rows="4"
            required /></el-form-item
        ><el-button type="primary" native-type="submit" :loading="op.busy.value"
          >保存处置</el-button
        ></el-form
      ></el-dialog
    >
  </div>
</template>
