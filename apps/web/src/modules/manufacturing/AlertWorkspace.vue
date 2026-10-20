<script setup lang="ts">
import { ref } from "vue";
import IncidentWorkspace from "./IncidentWorkspace.vue";
import AlertRuleWorkspace from "./AlertRuleWorkspace.vue";
const active = ref("incidents");
</script>
<template>
  <div class="page-stack">
    <section class="panel" style="padding: 0 18px">
      <el-tabs v-model="active"
        ><el-tab-pane label="预警处置" name="incidents" /><el-tab-pane
          label="预警规则"
          name="rules"
      /></el-tabs>
    </section>
    <IncidentWorkspace v-if="active === 'incidents'" /><AlertRuleWorkspace
      v-else
      @incident="active = 'incidents'"
    />
  </div>
</template>
