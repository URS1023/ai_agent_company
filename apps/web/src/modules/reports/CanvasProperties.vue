<script setup lang="ts">
/** 画布与导出共用实际尺寸和底色，控件范围与保存协议一致。 */
const canvas = defineModel<Record<string, any>>({ required: true });
</script>
<template>
  <h3>画布</h3>
  <el-form label-position="top">
    <el-form-item label="宽度"
      ><el-input-number
        v-model="canvas.width"
        :min="100"
        :max="10000"
        :step="10"
        controls-position="right"
    /></el-form-item>
    <el-form-item label="高度"
      ><el-input-number
        v-model="canvas.height"
        :min="100"
        :max="10000"
        :step="10"
        controls-position="right"
    /></el-form-item>
    <el-form-item label="背景颜色"
      ><el-color-picker v-model="canvas.backgroundColor"
    /></el-form-item>
  </el-form>
</template>
