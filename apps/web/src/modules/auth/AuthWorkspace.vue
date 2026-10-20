<script setup lang="ts">
import { reactive } from "vue";
import { useSession } from "../../stores/session";
import { useOperation } from "../../composables/useOperation";
const session = useSession();
const op = useOperation();
const form = reactive({
  username: "",
  password: "",
  display_name: "",
  workspace_name: "",
});
async function submit() {
  await op.run(() =>
    session.initialized
      ? session.login({ username: form.username, password: form.password })
      : session.setup({ ...form }),
  );
}
</script>
<template>
  <div class="auth-page">
    <div class="auth-art">
      <div class="auth-wordmark">
        <img src="/logo-k.png" alt="" /><img
          src="/logo-qknow.png"
          alt="qKnow"
        />
      </div>
      <h1>让工业数据<br />成为行动依据</h1>
      <div class="auth-geometry"></div>
    </div>
    <div class="auth-main">
      <el-form class="auth-form" label-position="top" @submit.prevent="submit"
        ><h2>{{ session.initialized ? "登录" : "创建工作区" }}</h2>
        <el-form-item label="账号" :error="op.fields.value.username"
          ><el-input
            v-model="form.username"
            autocomplete="username"
            maxlength="80"
            required /></el-form-item
        ><el-form-item
          v-if="!session.initialized"
          label="姓名"
          :error="op.fields.value.display_name"
          ><el-input
            v-model="form.display_name"
            maxlength="80"
            required /></el-form-item
        ><el-form-item
          v-if="!session.initialized"
          label="工作区名称"
          :error="op.fields.value.workspace_name"
          ><el-input
            v-model="form.workspace_name"
            maxlength="120"
            required /></el-form-item
        ><el-form-item label="密码" :error="op.fields.value.password"
          ><el-input
            v-model="form.password"
            type="password"
            show-password
            :autocomplete="
              session.initialized ? 'current-password' : 'new-password'
            "
            :minlength="session.initialized ? 1 : 12"
            required /></el-form-item
        ><el-button
          type="primary"
          native-type="submit"
          :loading="op.busy.value"
          class="auth-submit"
          >{{ session.initialized ? "登录" : "创建并进入" }}</el-button
        ></el-form
      >
    </div>
  </div>
</template>
