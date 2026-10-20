<script setup lang="ts">
/** 工作台只消费当前已发布公告；正文使用纯文本，不将配置内容当作HTML执行。 */
import { onMounted, ref } from "vue";
import { api } from "../../api/client";
import { useOperation } from "../../composables/useOperation";

interface Announcement {
  id: string;
  name: string;
  content: string;
  published_at: string;
}
const op = useOperation();
const items = ref<Announcement[]>([]);
const selected = ref<Announcement | null>(null);
const failed = ref(false);
const open = ref(false);

async function load() {
  failed.value = false;
  await op.run(async () => {
    try {
      const response = await api.get<{ items: Announcement[] }>(
        "/operations/announcement-feed",
      );
      items.value = response.items;
    } catch (error) {
      failed.value = true;
      throw error;
    }
  });
}
function show(item: Announcement) {
  selected.value = item;
  open.value = true;
}
function date(value: string) {
  return new Date(value).toLocaleDateString("zh-CN");
}
onMounted(load);
</script>

<template>
  <section v-if="items.length || failed" class="panel announcements">
    <div class="panel-heading">
      <h3>公告</h3>
      <el-button v-if="failed" :loading="op.busy.value" @click="load"
        >重新加载</el-button
      >
    </div>
    <el-collapse v-if="items.length" accordion>
      <el-collapse-item
        v-for="item in items.slice(0, 5)"
        :key="item.id"
        :name="item.id"
      >
        <template #title>
          <span class="notice-title">{{ item.name }}</span>
          <time>{{ date(item.published_at) }}</time>
        </template>
        <p class="notice-body">{{ item.content }}</p>
      </el-collapse-item>
    </el-collapse>
    <el-button
      v-if="items.length > 5"
      link
      type="primary"
      @click="
        open = true;
        selected = null;
      "
      >全部公告（{{ items.length }}）</el-button
    >
  </section>
  <el-drawer v-model="open" :title="selected?.name ?? '公告'" size="560px">
    <p v-if="selected" class="notice-body">{{ selected.content }}</p>
    <div v-else class="notice-list">
      <el-button v-for="item in items" :key="item.id" text @click="show(item)">
        {{ item.name }} <time>{{ date(item.published_at) }}</time>
      </el-button>
    </div>
  </el-drawer>
</template>

<style scoped>
.notice-title {
  flex: 1;
  min-width: 0;
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
}
time {
  flex: none;
  margin: 0 12px;
  color: #677a91;
  font-size: 13px;
}
.notice-body {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.8;
}
.notice-list {
  display: flex;
  flex-direction: column;
  align-items: stretch;
}
.notice-list .el-button {
  justify-content: space-between;
  margin: 0;
}
</style>
