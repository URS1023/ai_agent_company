import { createRouter, createWebHistory } from "vue-router";
import { menuItems } from "./modules/manifest";
import type { Component } from "vue";

const views: Record<string, () => Promise<Component>> = {
  home: () => import("./modules/home/Workbench.vue"),
  tasks: () => import("./modules/home/TaskWorkspace.vue"),
  assets: () => import("./modules/manufacturing/AssetWorkspace.vue"),
  products: () => import("./modules/manufacturing/ProductWorkspace.vue"),
  inspections: () => import("./modules/manufacturing/InspectionWorkspace.vue"),
  standards: () => import("./modules/manufacturing/StandardWorkspace.vue"),
  maintenance: () => import("./modules/manufacturing/MaintenanceWorkspace.vue"),
  incidents: () => import("./modules/manufacturing/AlertWorkspace.vue"),
  data: () => import("./modules/data/DataWorkspace.vue"),
  "data-access": () => import("./modules/data/DataPermissionWorkspace.vue"),
  quality: () => import("./modules/data/DataQualityWorkspace.vue"),
  processing: () => import("./modules/data/ProcessingWorkspace.vue"),
  reports: () => import("./modules/reports/ReportWorkspace.vue"),
  native: () => import("./modules/native/NativeWorkspace.vue"),
  chat: () => import("./modules/chat/ChatWorkspace.vue"),
  kb: () => import("./modules/knowledge/KnowledgeWorkspace.vue"),
  flow: () => import("./modules/flows/WorkflowWorkspace.vue"),
  agent: () => import("./modules/agents/AgentWorkspace.vue"),
  plugin: () => import("./modules/agents/PluginWorkspace.vue"),
  "native-admin": () => import("./modules/native/NativeAdminWorkspace.vue"),
  catalog: () => import("./modules/catalog/CatalogWorkspace.vue"),
  roles: () => import("./modules/admin/RoleWorkspace.vue"),
  industry: () => import("./modules/catalog/IndustryWorkspace.vue"),
  backups: () => import("./modules/admin/BackupWorkspace.vue"),
  files: () => import("./modules/admin/FileWorkspace.vue"),
  documents: () => import("./modules/admin/DocumentWorkspace.vue"),
  health: () => import("./modules/admin/HealthWorkspace.vue"),
  ontology: () => import("./modules/graph/OntologyWorkspace.vue"),
  extraction: () => import("./modules/graph/ExtractionWorkspace.vue"),
  graph: () => import("./modules/graph/GraphWorkspace.vue"),
  applications: () => import("./modules/catalog/ApplicationWorkspace.vue"),
};
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/overview" },
    ...menuItems.map((item) => ({
      path: item.path,
      name: item.key,
      component: views[item.view],
      props: { config: item.config },
      meta: { title: item.label, permission: item.permission },
    })),
    { path: "/:pathMatch(.*)*", redirect: "/overview" },
  ],
});
