/** 仅操作本地组件fixture；回调记录不代表任何模型或平台API调用。 */
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
export async function verifyBranchControls(page, directory, state) {
  state.stage = "edit-controls";
  await page.getByRole("button", { name: "编辑消息", exact: true }).click();
  assert.equal(
    await page.getByLabel("编辑消息内容", { exact: true }).inputValue(),
    "本地组件验收",
  );
  assert.equal(await page.getByLabel("编辑附件方式").inputValue(), "inherit");
  await page
    .getByLabel("编辑消息内容", { exact: true })
    .fill("组件修改后的提问");
  await page.getByLabel("编辑附件方式").selectOption("replace");
  await page.getByLabel("替换为替换附件.txt", { exact: true }).check();
  assert.deepEqual(
    await page.evaluate(
      () => globalThis.__fixture.snapshot.editing.attachmentIds,
    ),
    ["replacement-file"],
  );
  await page.getByLabel("编辑附件方式").selectOption("remove");
  assert.equal(
    await page.getByLabel("替换为替换附件.txt", { exact: true }).count(),
    0,
  );
  await page.getByLabel("编辑附件方式").selectOption("inherit");
  assert.equal(
    await page.getByRole("button", { name: "原附件.txt", exact: true }).count(),
    1,
  );
  await page.screenshot({
    path: fileURLToPath(new URL("editing.png", directory)),
    animations: "disabled",
    timeout: 5000,
  });
  await page.getByRole("button", { name: "保存并发送", exact: true }).click();
  assert.equal(
    await page.getByLabel("编辑消息内容", { exact: true }).inputValue(),
    "组件修改后的提问",
  );
  assert.equal(
    await page.getByRole("button", { name: "取消", exact: true }).isDisabled(),
    true,
  );
  await page.getByRole("button", { name: "核对结果", exact: true }).waitFor();
  state.stage = "edit-branch-preservation";
  await page.evaluate(() => {
    const fixture = globalThis.__fixture;
    const history = structuredClone(fixture.snapshot.history);
    history.messages["new-user"] = {
      id: "new-user",
      role: "user",
      parentId: null,
      childrenIds: ["new-assistant"],
      content: "另一个组件分支",
    };
    history.messages["new-assistant"] = {
      id: "new-assistant",
      role: "assistant",
      parentId: "new-user",
      childrenIds: [],
      content: "本地分支内容",
      done: true,
      model: "fixture-model",
    };
    history.currentId = "new-assistant";
    fixture.render({ history });
  });
  assert.equal(
    await page.getByLabel("编辑消息内容", { exact: true }).inputValue(),
    "组件修改后的提问",
  );
  assert.equal(
    await page.getByLabel("编辑消息内容", { exact: true }).count(),
    1,
  );
  await page.evaluate(() =>
    globalThis.__fixture.render({
      editing: undefined,
      uncertain: false,
      status: "failed",
      error: "本地组件失败反馈",
    }),
  );
  assert.equal(
    await page.getByLabel("编辑消息内容", { exact: true }).count(),
    0,
  );
  await page.getByRole("button", { name: "重新生成", exact: true }).click();
  assert.equal(
    await page
      .getByRole("button", { name: "重新生成", exact: true })
      .isDisabled(),
    true,
  );
  assert.equal(
    await page.evaluate(
      () =>
        globalThis.__fixture.calls
          .filter((row) => row.name === "regenerate")
          .at(-1).value,
    ),
    "new-assistant",
  );
  assert.equal(
    await page.evaluate(
      () =>
        globalThis.__fixture.calls.filter((row) => row.name === "editAndSend")
          .length,
    ),
    1,
  );
  await page.evaluate(() =>
    globalThis.__fixture.render({ busy: false, status: "ready" }),
  );
  state.branch_controls = "passed";
}
