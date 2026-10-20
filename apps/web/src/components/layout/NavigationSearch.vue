<script setup lang="ts">
/** 只搜索当前用户有权打开的功能入口，不触发业务数据库查询。 */
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Search } from "@element-plus/icons-vue";
import { modules } from "../../modules/manifest";
import { useSession } from "../../stores/session";

const session = useSession();
const router = useRouter();
const route = useRoute();
const query = ref("");
const items = computed(() =>
  modules.flatMap((group) =>
    group.sections.flatMap((section) =>
      section.items
        .filter((item) => session.can(item.permission))
        .map((item) => ({
          value: item.label,
          path: item.path,
          group: group.label,
        })),
    ),
  ),
);
function suggest(
  value: string,
  callback: (rows: Record<string, string>[]) => void,
) {
  const needle = value.trim().normalize("NFKC").toLocaleLowerCase();
  callback(
    needle
      ? items.value
          .filter((item) =>
            (item.value + item.group)
              .normalize("NFKC")
              .toLocaleLowerCase()
              .includes(needle),
          )
          .slice(0, 12)
      : [],
  );
}
function select(value: Record<string, any>) {
  if (items.value.some((item) => item.path === value.path))
    router.push(value.path);
}
watch(
  () => [route.path, session.value?.user.id, session.value?.workspace.id],
  () => {
    query.value = "";
  },
);
</script>

<template>
  <el-autocomplete
    v-model="query"
    class="topbar-search"
    placeholder="搜索功能"
    :prefix-icon="Search"
    :fetch-suggestions="suggest"
    :trigger-on-focus="false"
    clearable
    aria-label="搜索功能"
    @select="select"
  >
    <template #default="{ item }"
      ><span>{{ item.value }}</span
      ><span class="search-group">{{ item.group }}</span></template
    >
  </el-autocomplete>
</template>

<style scoped>
.search-group {
  margin-left: 16px;
  color: var(--text-muted);
}
</style>
