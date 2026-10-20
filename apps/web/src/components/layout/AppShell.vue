<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Menu, Fold, Bell, SwitchButton } from "@element-plus/icons-vue";
import { modules, menuItems } from "../../modules/manifest";
import { useSession } from "../../stores/session";
import { useOperation } from "../../composables/useOperation";
const session = useSession();
const route = useRoute();
const router = useRouter();
const op = useOperation();
const collapsed = ref(false);
const tabs = ref<{ path: string; title: string }[]>([]);
const activeGroup = computed(
  () =>
    modules.find((group) =>
      group.sections.some((section) =>
        section.items.some((item) => item.path === route.path),
      ),
    ) ?? modules[0]!,
);
const groups = computed(() =>
  modules.filter((group) =>
    group.sections.some((section) =>
      section.items.some((item) => session.can(item.permission)),
    ),
  ),
);
const sections = computed(() =>
  activeGroup.value.sections
    .map((section) => ({
      ...section,
      items: section.items.filter((item) => session.can(item.permission)),
    }))
    .filter((x) => x.items.length),
);
const allowed = computed(() =>
  session.can(route.meta.permission as string | undefined),
);
watch(
  () => route.path,
  () => {
    const item = menuItems.find((x) => x.path === route.path);
    if (item && !tabs.value.some((x) => x.path === item.path))
      tabs.value.push({ path: item.path, title: item.label });
  },
  { immediate: true },
);
function selectGroup(id: string) {
  const first = modules
    .find((x) => x.id === id)
    ?.sections.flatMap((x) => x.items)
    .find((x) => session.can(x.permission));
  if (first) router.push(first.path);
}
function closeTab(path: string) {
  tabs.value = tabs.value.filter((x) => x.path !== path);
  if (route.path === path) router.push(tabs.value.at(-1)?.path ?? "/overview");
}
async function workspace(id: string) {
  await op.run(async () => {
    await session.switchWorkspace(id);
    window.location.reload();
  });
}
</script>
<template>
  <div class="app-shell" :class="{ collapsed }">
    <aside class="sidebar">
      <RouterLink class="brand" to="/overview" aria-label="qKnow首页"
        ><img src="/logo-k.png" alt="" /><img
          v-if="!collapsed"
          src="/logo-qknow.png"
          alt="qKnow"
      /></RouterLink>
      <div class="sidebar-menu">
        <section
          v-for="section in sections"
          :key="section.label"
          class="menu-section"
        >
          <div v-if="!collapsed" class="menu-section-title">
            {{ section.label }}
          </div>
          <RouterLink
            v-for="item in section.items"
            :key="item.key"
            :to="item.path"
            :title="item.label"
            class="menu-link"
            ><span class="menu-dot">{{ item.label.slice(0, 1) }}</span
            ><span v-if="!collapsed">{{ item.label }}</span></RouterLink
          >
        </section>
      </div>
    </aside>
    <div class="workspace">
      <header class="topbar">
        <el-button
          text
          :icon="collapsed ? Menu : Fold"
          aria-label="切换侧栏"
          @click="collapsed = !collapsed"
        />
        <nav class="topnav">
          <button
            v-for="group in groups"
            :key="group.id"
            :class="{ active: group.id === activeGroup.id }"
            @click="selectGroup(group.id)"
          >
            {{ group.label }}
          </button>
        </nav>
        <el-select
          :model-value="session.value?.workspace.id"
          class="workspace-picker"
          @change="workspace"
          ><el-option
            v-for="item in session.value?.workspaces"
            :key="item.id"
            :value="item.id"
            :label="item.name"
        /></el-select>
        <el-button
          text
          :icon="Bell"
          aria-label="消息通知"
          @click="router.push('/notices')"
        />
        <el-dropdown
          ><button class="avatar">
            {{
              (
                session.value?.user.display_name ??
                session.value?.user.username ??
                "用"
              ).slice(0, 1)
            }}</button
          ><template #dropdown
            ><el-dropdown-menu
              ><el-dropdown-item disabled>{{
                session.value?.user.display_name ?? session.value?.user.username
              }}</el-dropdown-item
              ><el-dropdown-item
                :icon="SwitchButton"
                @click="op.run(() => session.logout())"
                >退出登录</el-dropdown-item
              ></el-dropdown-menu
            ></template
          ></el-dropdown
        >
      </header>
      <div class="work-tabs">
        <div
          v-for="tab in tabs"
          :key="tab.path"
          :class="['work-tab', { active: tab.path === route.path }]"
          @click="router.push(tab.path)"
        >
          <span>{{ tab.title }}</span
          ><button
            v-if="tabs.length > 1"
            aria-label="关闭标签"
            @click.stop="closeTab(tab.path)"
          >
            ×
          </button>
        </div>
      </div>
      <main class="main-content">
        <RouterView
          v-if="allowed"
          :key="`${session.value?.workspace.id}:${route.path}`"
        /><el-result v-else icon="warning" title="你没有此页面的访问权限" />
      </main>
    </div>
  </div>
</template>
