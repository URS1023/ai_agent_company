import { defineStore } from "pinia";
import { computed, ref, shallowRef } from "vue";
import { api, setCsrf } from "../api/client";

export interface Session {
  user: { id: string; username: string; display_name?: string };
  workspace: { id: string; name: string };
  workspaces: { id: string; name: string }[];
  permissions: string[];
  roles: string[];
  csrf_token: string;
}
export const useSession = defineStore("session", () => {
  const value = ref<Session | null>(null);
  const ready = shallowRef(false);
  const initialized = shallowRef(true);
  const authenticated = computed(() => !!value.value);
  function can(permission?: string) {
    const permissions = value.value?.permissions ?? [];
    return (
      !permission ||
      permissions.includes("*") ||
      permissions.includes(permission) ||
      permissions.includes(`${permission.split(".")[0]}.*`)
    );
  }
  async function bootstrap() {
    try {
      const status = await api.get("/auth/status");
      initialized.value = status.initialized ?? status.setup_complete ?? true;
      if (initialized.value) await refresh();
    } finally {
      ready.value = true;
    }
  }
  async function refresh() {
    try {
      value.value = await api.get<Session>("/me");
      setCsrf(value.value.csrf_token);
    } catch (e) {
      value.value = null;
      setCsrf("");
      throw e;
    }
  }
  async function login(data: Record<string, string>) {
    await api.post("/auth/login", data);
    await refresh();
  }
  async function setup(data: Record<string, string>) {
    await api.post("/auth/setup", data);
    initialized.value = true;
    await refresh();
  }
  async function logout() {
    await api.post("/auth/logout", {});
    value.value = null;
    setCsrf("");
  }
  async function switchWorkspace(id: string) {
    value.value = await api.post<Session>("/workspace-selections", {
      workspace_id: id,
    });
    setCsrf(value.value.csrf_token);
  }
  return {
    value,
    ready,
    initialized,
    authenticated,
    can,
    bootstrap,
    refresh,
    login,
    setup,
    logout,
    switchWorkspace,
  };
});
