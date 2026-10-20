<script setup lang="ts">
import { memoryTypes, type MemoryRecord, type MemoryType } from "./useMemories";
defineProps<{
  items: MemoryRecord[];
  total: number;
  page: number;
  size: number;
  busy: boolean;
  canWrite: boolean;
  canDelete: boolean;
  error: string;
}>();
const query = defineModel<string>("query", { required: true });
const type = defineModel<MemoryType | "">("type", { required: true });
const emit = defineEmits<{
  search: [];
  page: [value: number];
  refresh: [];
  create: [];
  edit: [id: string];
  remove: [record: MemoryRecord];
}>();
function time(value: string) {
  const date = new Date(value);
  return Number.isFinite(date.getTime())
    ? date.toLocaleString("zh-CN", { hour12: false })
    : "—";
}
</script>
<template>
  <section class="panel">
    <form class="toolbar memory-toolbar" @submit.prevent="emit('search')">
      <el-input
        v-model="query"
        aria-label="搜索记忆"
        placeholder="搜索内容"
        :maxlength="200"
        clearable
        :disabled="busy"
        @clear="emit('search')"
      />
      <el-select
        v-model="type"
        aria-label="记忆类型"
        placeholder="全部类型"
        clearable
        :disabled="busy"
        @change="emit('search')"
      >
        <el-option
          v-for="item in memoryTypes"
          :key="item.value"
          :value="item.value"
          :label="item.label"
        />
      </el-select>
      <el-button native-type="submit" :disabled="busy">搜索</el-button>
      <span class="spacer" />
      <el-button :disabled="busy" @click="emit('refresh')">刷新</el-button>
      <el-button
        v-if="canWrite"
        type="primary"
        :disabled="busy"
        @click="emit('create')"
        >新增记忆</el-button
      >
    </form>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      show-icon
      :closable="false"
      class="memory-error"
    />
    <el-table v-loading="busy" :data="items" stripe empty-text="暂无匹配的记忆">
      <el-table-column
        prop="content"
        label="内容"
        min-width="320"
        show-overflow-tooltip
      />
      <el-table-column label="类型" width="125"
        ><template #default="{ row }">{{
          row.type === "context" ? "背景资料" : "个人偏好"
        }}</template></el-table-column
      >
      <el-table-column label="最近更新" width="190"
        ><template #default="{ row }">{{
          time(row.updated_at)
        }}</template></el-table-column
      >
      <el-table-column label="操作" width="140"
        ><template #default="{ row }">
          <el-button
            link
            type="primary"
            :disabled="busy"
            @click="emit('edit', row.id)"
            >{{ canWrite ? "编辑" : "查看" }}</el-button
          >
          <el-button
            v-if="canDelete"
            link
            type="danger"
            :disabled="busy"
            @click="emit('remove', row)"
            >删除</el-button
          >
        </template></el-table-column
      >
    </el-table>
    <el-pagination
      class="memory-pagination"
      :current-page="page"
      :page-size="size"
      :total="total"
      :disabled="busy || Boolean(error)"
      layout="total, prev, pager, next"
      @current-change="emit('page', $event)"
    />
  </section>
</template>
<style scoped>
.memory-toolbar {
  margin-bottom: var(--sp-4);
}
.memory-toolbar > .el-input {
  max-width: 360px;
}
.memory-toolbar > .el-select {
  width: 150px;
}
.memory-error {
  margin-bottom: var(--sp-4);
}
.memory-pagination {
  margin-top: var(--sp-4);
  justify-content: flex-end;
}
</style>
