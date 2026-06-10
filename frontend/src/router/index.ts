import { createRouter, createWebHistory } from "vue-router";

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: "/",
      redirect: "/conversation/new",
    },
    {
      path: "/conversation/new",
      name: "new-conversation",
      component: () => import("../components/ChatView.vue"),
      meta: { sidebar: "conversations" },
    },
    {
      path: "/conversation/:id",
      name: "conversation",
      component: () => import("../components/ChatView.vue"),
      props: true,
      meta: { sidebar: "conversations" },
    },
    {
      path: "/tools",
      name: "tools",
      component: () => import("../components/ToolCatalogView.vue"),
      meta: { sidebar: "tools" },
    },
    {
      path: "/tools/ingest",
      name: "tool-ingest",
      component: () => import("../components/ToolIngestWizard.vue"),
      meta: { sidebar: "tools" },
    },
    {
      path: "/tools/new",
      redirect: { name: "tool-ingest" },
    },
    {
      path: "/tools/groups/:name",
      name: "tool-group",
      component: () => import("../components/ToolGroupView.vue"),
      meta: { sidebar: "tools" },
    },
    {
      path: "/tools/:name",
      name: "tool-detail",
      component: () => import("../components/ToolDetailView.vue"),
      meta: { sidebar: "tools" },
    },
    {
      path: "/settings",
      component: () => import("../components/SettingsView.vue"),
      meta: { sidebar: "settings" },
      children: [
        {
          path: "",
          redirect: { name: "settings-system" },
        },
        {
          path: "system",
          name: "settings-system",
          component: () => import("../components/SettingsSystem.vue"),
        },
        {
          path: "analytics",
          name: "settings-analytics",
          component: () => import("../components/SettingsAnalytics.vue"),
        },
        {
          path: "debug",
          name: "settings-debug",
          component: () => import("../components/SettingsDebug.vue"),
        },
      ],
    },
  ],
});

export default router;
