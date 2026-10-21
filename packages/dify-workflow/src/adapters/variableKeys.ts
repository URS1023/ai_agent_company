/** 分组名称保持可引用标识；校验失败由当前岛的字段反馈呈现。 */
export function checkKeys(keys: string[], _unused: boolean) {
  return {
    isValid: keys.every((key) => /^[A-Za-z_][A-Za-z0-9_]{0,29}$/.test(key)),
    errorKey: "",
    errorMessageKey: "invalidGroupName",
  };
}
export function replaceSpaceWithUnderscoreInVarNameInput(target: {
  value: string;
}) {
  target.value = target.value.replaceAll(" ", "_");
}
