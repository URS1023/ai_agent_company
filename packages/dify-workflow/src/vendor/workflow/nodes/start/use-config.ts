import { useState } from "react";
import { useEditor } from "../../../../EditorContext";
import {
  cloneDefinition,
  isVariableUsed,
  renameReferences,
} from "../../../../draft";
import type { InputVar } from "../../../../types";
import type { StartNodeType } from "./types";
export default function useConfig(id: string, inputs: StartNodeType) {
  const editor = useEditor(),
    [isShowAddVarModal, setShow] = useState(false);
  const [isShowRemoveVarConfirm, setRemoving] = useState(false);
  const changeVariables = (variables: InputVar[]) => {
    if (editor.readOnly) return;
    if (
      new Set(variables.map((item) => item.variable)).size !==
        variables.length ||
      new Set(variables.map((item) => item.label)).size !== variables.length
    ) {
      editor.reportError("变量名和显示名称不能重复");
      return;
    }
    let next = cloneDefinition(editor.definition);
    const before = inputs.variables ?? [];
    if (
      before.some(
        (field) =>
          field.type === "json_object" &&
          JSON.stringify(field) !==
            JSON.stringify(
              variables.find((item) => item.variable === field.variable),
            ),
      )
    ) {
      editor.reportError("该结构化输入字段当前只读");
      return;
    }
    for (const variable of before)
      if (!variables.some((item) => item.variable === variable.variable)) {
        const replacement = variables.find(
          (item, index) =>
            index === before.indexOf(variable) &&
            before.length === variables.length,
        );
        if (replacement)
          next = renameReferences(
            next,
            id,
            variable.variable,
            replacement.variable,
          );
        else if (isVariableUsed(next, id, variable.variable)) {
          setRemoving(true);
          return;
        }
      }
    const node = next.graph.nodes.find((node) => node.id === id);
    if (node) node.data.variables = structuredClone(variables);
    editor.change(next);
  };
  return {
    readOnly: editor.readOnly,
    inputs,
    isShowAddVarModal,
    showAddVarModal: () => setShow(true),
    hideAddVarModal: () => setShow(false),
    handleVarListChange: changeVariables,
    handleAddVariable: (variable: InputVar) => {
      if (editor.readOnly) return false;
      if (
        (inputs.variables ?? []).some(
          (item) => item.variable === variable.variable,
        )
      ) {
        editor.reportError("变量名已存在");
        return false;
      }
      changeVariables([...(inputs.variables ?? []), variable]);
      return true;
    },
    isShowRemoveVarConfirm,
    hideRemoveVarConfirm: () => setRemoving(false),
    onRemoveVarConfirm: () => setRemoving(false),
  };
}
