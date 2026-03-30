import { createRouter, createWebHistory } from "vue-router";
import { useAuthStore } from "@/stores/auth";
import { ADMIN_ONLY_ROUTE_NAMES, getControlPlaneRoutes } from "@/config/controlPlane";
import { isTokenExpired } from "@/utils/jwt";
function redirectAfterLogin(auth: ReturnType<typeof useAuthStore>): { name: string } {
  if (auth.token) {
    return { name: "dashboard" };
  }
  return { name: "login" };
}

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: "/login",
      name: "login",
      component: () => import("@/views/Login.vue"),
      meta: { title: "Login" },
    },
    {
      path: "/invite",
      name: "invite",
      component: () => import("@/views/InviteView.vue"),
      meta: { title: "Invite" },
    },


    // Old paths (/family /elderly /kids /gallery /media /iot /board etc.) now return 404.
    ...getControlPlaneRoutes(),
  ],
});

router.beforeEach((to, _from, next) => {
  const auth = useAuthStore();

  if (auth.token && isTokenExpired(auth.token)) {
    auth.setToken(null);
  }

  if (to.meta.requiresAuth && !auth.token) {
    next({ name: "login" });
  } else if (
    auth.token &&
    typeof to.name === "string" &&
    ADMIN_ONLY_ROUTE_NAMES.has(to.name) &&
    !auth.isAdmin
  ) {
    next({ name: "dashboard" });
  } else if (to.name === "login" && auth.token) {
    next(redirectAfterLogin(auth));
  } else {
    next();
  }
});
