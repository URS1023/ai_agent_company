/** 原组件使用的有限中文词条；无语言后台请求、全局i18n单例或遥测。 */
const messages: Record<string, string> = {
  "operation.add": "添加",
  "operation.edit": "编辑",
  "operation.remove": "删除",
  "nodes.start.required": "必填",
  "nodes.start.inputField": "输入字段",
  "nodes.end.output.variable": "输出变量",
  "nodes.ifElse.else": "ELSE",
  "nodes.ifElse.conditionNotSetup": "请选择条件",
  "nodes.ifElse.and": "且",
  "nodes.ifElse.or": "或",
};
export function useTranslation(_namespaces?: string[]) {
  return {
    t: (
      selector: string | ((values: Record<string, string>) => string),
      _options?: unknown,
    ): string => {
      const key =
        typeof selector === "function"
          ? selector(
              new Proxy({}, { get: (_object, name) => String(name) }) as Record<
                string,
                string
              >,
            )
          : selector;
      return messages[key] ?? key;
    },
  };
}
