<script setup lang="ts">
import { ref } from "vue";
import { useSession } from "../../stores/session";
import NativeWorkspace from "./NativeWorkspace.vue";
import CredentialWorkspace from "./CredentialWorkspace.vue";
import NativeMappingWorkspace from "./NativeMappingWorkspace.vue";
const active = ref("connections");
const session = useSession();
</script>
<template>
  <div class="page-stack">
    <section class="panel" style="padding: 0 18px">
      <el-tabs v-model="active"
        ><el-tab-pane label="集成连接" name="connections" /><el-tab-pane
          label="安全凭据"
          name="secrets" /><el-tab-pane
          v-if="session.can('m14.admin')"
          label="用户映射"
          name="identities" /><el-tab-pane
          v-if="session.can('m14.admin')"
          label="工作区映射"
          name="workspaces"
      /></el-tabs>
    </section>
    <NativeWorkspace
      v-if="active === 'connections'"
      config="connections"
    /><CredentialWorkspace
      v-else-if="active === 'secrets'"
    /><NativeMappingWorkspace
      v-else
      :key="active"
      :kind="
        active === 'identities' ? 'external-identities' : 'external-workspaces'
      "
    />
  </div>
</template>
