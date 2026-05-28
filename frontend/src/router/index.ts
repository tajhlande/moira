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
    },
    {
      path: "/conversation/:id",
      name: "conversation",
      component: () => import("../components/ChatView.vue"),
      props: true,
    },
  ],
});

export default router;
