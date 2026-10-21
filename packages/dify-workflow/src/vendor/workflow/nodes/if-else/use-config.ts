import { useEditor } from "../../../../EditorContext";
import { cloneDefinition, removeBranch } from "../../../../draft";
import type { VariableOption } from "../../../../types";
import type { CaseItem, Condition, IfElseNodeType } from "./types";
import * as helpers from "./use-config.helpers";
export default function useConfig(id: string, inputs: IfElseNodeType) {
  const editor = useEditor();
  const update = (nextInputs: IfElseNodeType) =>
    editor.patchNode(id, nextInputs);
  const unavailable = () => editor.reportError("文件子条件编辑尚未接入");
  return {
    readOnly: editor.readOnly,
    inputs,
    filterVar: helpers.filterAllVars,
    availableNodes: [],
    nodesOutputVars: [],
    varsIsVarFileAttribute: {},
    handleAddCase: () => update(helpers.addCase(inputs)),
    handleRemoveCase: (caseId: string) => {
      if (editor.readOnly) return;
      const next = removeBranch(editor.definition, id, caseId);
      const node = next.graph.nodes.find((item) => item.id === id);
      if (node) node.data = helpers.removeCase(inputs, caseId);
      editor.change(next);
    },
    handleSortCase: (cases: (CaseItem & { id: string })[]) =>
      update(helpers.sortCases(inputs, cases)),
    handleAddCondition: (
      caseId: string,
      selector: string[],
      variable: VariableOption,
    ) =>
      update(
        helpers.addCondition({
          inputs,
          caseId,
          valueSelector: selector,
          variable: { type: variable.type, variable: selector.at(-1) ?? "" },
          isVarFileAttribute: false,
        }),
      ),
    handleUpdateCondition: (
      caseId: string,
      conditionId: string,
      condition: Condition,
    ) =>
      update(helpers.updateCondition(inputs, caseId, conditionId, condition)),
    handleRemoveCondition: (caseId: string, conditionId: string) =>
      update(helpers.removeCondition(inputs, caseId, conditionId)),
    handleToggleConditionLogicalOperator: (caseId: string) =>
      update(helpers.toggleConditionLogicalOperator(inputs, caseId)),
    handleAddSubVariableCondition: unavailable,
    handleRemoveSubVariableCondition: unavailable,
    handleUpdateSubVariableCondition: unavailable,
    handleToggleSubVariableConditionLogicalOperator: unavailable,
  };
}
