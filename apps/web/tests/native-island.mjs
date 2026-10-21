/** 同页挂载故障纯回归：父props错误更新不会自动重建，只有用户重试才恢复。 */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";
import { parse, compileScript } from "@vue/compiler-sfc";
import { effectScope, reactive, nextTick } from "vue";
const asUrl = (code) =>
  `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
const events = [];
let attempts = 0,
  updates = 0,
  unmounts = 0;
globalThis.__nativeIslandTest = {
  mountNativeChat() {
    attempts++;
    if (attempts === 1) throw new Error("组件测试故障");
    return {
      update() {
        updates++;
      },
      unmount() {
        unmounts++;
      },
    };
  },
};
const source = await readFile(
  new URL("../src/modules/chat/NativeChatIsland.vue", import.meta.url),
  "utf8",
);
const parsed = parse(source),
  script = compileScript(parsed.descriptor, { id: "native-island-test" });
const compiled = ts
  .transpileModule(script.content, {
    compilerOptions: {
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2022,
    },
  })
  .outputText.replaceAll('"vue"', JSON.stringify(import.meta.resolve("vue")))
  .replaceAll("'vue'", JSON.stringify(import.meta.resolve("vue")))
  .replaceAll(
    '"@openwebui-chat"',
    JSON.stringify(
      asUrl("export const {mountNativeChat}=globalThis.__nativeIslandTest;"),
    ),
  );
const Component = (await import(asUrl(compiled))).default;
const props = reactive({ snapshot: { error: "" }, actions: {} });
const scope = effectScope();
let state;
scope.run(() => {
  state = Component.setup(props, {
    expose() {},
    emit(name, value) {
      events.push({ name, value });
      if (name === "failure") props.snapshot = { error: value };
    },
  });
});
const settle = async () => {
  for (let index = 0; index < 6; index++) {
    await nextTick();
    await new Promise((resolve) => setImmediate(resolve));
  }
};
state.host.value = {};
await settle();
assert.equal(attempts, 1);
assert.equal(events[0].name, "failure");
assert.equal(state.failure.value, "组件测试故障");
props.snapshot = { error: "其他业务状态" };
await settle();
assert.equal(attempts, 1);
await state.retry();
await settle();
assert.equal(attempts, 2);
assert.equal(state.failure.value, "");
assert.equal(events.at(-1).name, "recovered");
assert.equal(events.at(-1).value, "组件测试故障");
props.snapshot = { error: "" };
await settle();
assert.ok(updates >= 1);
scope.stop();
assert.equal(unmounts, 1);
console.log("原生聊天挂载失败锁纯检查通过：10项；0HTTP/模型/数据库");
