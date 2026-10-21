/** 当前画布不接原协作拖拽store；分组句柄和显式变量选择仍由React岛管理。 */
export function useStore<T>(
  selector: (state: {
    enteringNodePayload: any;
    hoveringAssignVariableGroupId: any;
  }) => T,
): T {
  return selector({
    enteringNodePayload: null,
    hoveringAssignVariableGroupId: null,
  });
}
