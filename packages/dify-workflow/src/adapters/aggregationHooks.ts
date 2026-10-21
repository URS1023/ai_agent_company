import { useEditor } from "../EditorContext";
import { availableVariables } from "../variables";
import type { Var } from "../types";
export function useGetAvailableVars() {
  const { definition } = useEditor();
  return (
    nodeId: string,
    handleId: string,
    filter?: (value: Var) => boolean,
    _hideEnv?: boolean,
  ) => {
    const node = definition.graph.nodes.find((item) => item.id === nodeId);
    // 句柄限制是平台新增边界；原Dify此参数未用于过滤，来源清单明确区分。
    return availableVariables(
      definition,
      nodeId,
      node?.data.advanced_settings?.group_enabled ? handleId : undefined,
    ).filter(
      (option) =>
        !filter ||
        filter({
          variable: option.selector.slice(1).join("."),
          type: option.type,
        }),
    );
  };
}
export const filterVar = (type: string) => (value: Var) =>
  type === "any" || value.type === "any" || value.type === type;
export const isSystemVar = (selector: string[]) => selector[0] === "sys";
export const isExceptionVariable = (_name: string, _kind?: string) => false;
export const useVariableAssigner = () => ({
  handleGroupItemMouseEnter: (_id?: string) => {},
  handleGroupItemMouseLeave: () => {},
});
