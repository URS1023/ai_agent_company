<script setup lang="ts">
import { ref } from "vue";
import AssetWorkspace from "./AssetWorkspace.vue";
import BatchWorkspace from "./BatchWorkspace.vue";
const active = ref("products");
</script>
<template>
  <div class="page-stack">
    <section class="panel" style="padding: 0 18px">
      <el-tabs v-model="active"
        ><el-tab-pane label="产品" name="products" /><el-tab-pane
          label="批次"
          name="batches"
      /></el-tabs>
    </section>
    <AssetWorkspace
      v-if="active === 'products'"
      config="products"
    /><BatchWorkspace v-else />
  </div>
</template>
