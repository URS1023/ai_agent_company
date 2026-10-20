<script setup lang="ts">
/** 应用外壳：左侧模块轨道 + 上下文栏目 + 顶栏 + 工作标签。
 *  导航收敛为两级（轨道选分组、侧栏选条目），顶部只承载当前页定位与账号操作。 */
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Bell, Fold, Menu, SwitchButton } from "@element-plus/icons-vue";
import NavigationSearch from "./NavigationSearch.vue";
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
    <!-- 模块轨道：一个分组一个入口，取代原先堆在顶栏的组按钮 -->
    <nav class="rail" aria-label="模块导航">
      <RouterLink class="rail-mark" to="/overview" aria-label="返回概览">
        <img src="/logo-k.png" alt="" />
      </RouterLink>
      <button
        v-for="group in groups"
        :key="group.id"
        class="rail-item"
        :class="{ active: group.id === activeGroup.id }"
        :title="group.label"
        @click="selectGroup(group.id)"
      >
        <i class="fa-solid" :class="group.icon" />
        <span>{{ group.label }}</span>
      </button>
    </nav>

    <aside class="sidebar">
      <RouterLink class="brand" to="/overview">
        <img src="/logo-qknow.png" alt="qKnow" />
        <span class="brand-text">
          <strong>{{ activeGroup.label }}</strong>
        </span>
      </RouterLink>
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
          >
            <span class="menu-dot">{{ item.label.slice(0, 1) }}</span>
            <span v-if="!collapsed">{{ item.label }}</span>
          </RouterLink>
        </section>
      </div>
    </aside>

    <div class="workspace" :class="{ 'without-tabs': tabs.length < 2 }">
      <header class="topbar">
        <el-button
          class="icon-button"
          text
          :icon="collapsed ? Menu : Fold"
          aria-label="切换侧栏"
          @click="collapsed = !collapsed"
        />
        <div class="topbar-spacer" />
        <NavigationSearch />
        <el-select
          :model-value="session.value?.workspace.id"
          class="workspace-picker"
          aria-label="切换工作区"
          @change="workspace"
        >
          <el-option
            v-for="item in session.value?.workspaces"
            :key="item.id"
            :value="item.id"
            :label="item.name"
          />
        </el-select>
        <el-button
          class="icon-button"
          text
          :icon="Bell"
          aria-label="消息通知"
          @click="router.push('/notices')"
        />
        <el-dropdown>
          <button class="avatar" aria-label="账号菜单">
            {{
              (
                session.value?.user.display_name ??
                session.value?.user.username ??
                "用"
              ).slice(0, 1)
            }}
          </button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item disabled>
                {{
                  session.value?.user.display_name ??
                  session.value?.user.username
                }}
              </el-dropdown-item>
              <el-dropdown-item
                :icon="SwitchButton"
                @click="op.run(() => session.logout())"
              >
                退出登录
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </header>

      <div v-if="tabs.length > 1" class="work-tabs">
        <div
          v-for="tab in tabs"
          :key="tab.path"
          :class="['work-tab', { active: tab.path === route.path }]"
          @click="router.push(tab.path)"
        >
          <span>{{ tab.title }}</span>
          <button
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
        />
        <el-result v-else icon="warning" title="你没有此页面的访问权限" />
      </main>
    </div>
  </div>
</template>

<style scoped>
.workspace.without-tabs {
  grid-template-rows: var(--topbar-height) minmax(0, 1fr);
}
</style>
