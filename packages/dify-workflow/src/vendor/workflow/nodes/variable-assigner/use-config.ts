/** 原聚合配置函数的宿主适配；纯helpers保留原实现，引用和连线在同一草稿快照更新。 */
import { useState } from "react";
import { useEditor } from "../../../../EditorContext";
import {
  cloneDefinition,
  isSelectorUsed,
  renameSelectorReferences,
} from "../../../../draft";
import { availableVariables } from "../../../../variables";
import { useGetAvailableVars } from "../../../../adapters/aggregationHooks";
import {
  addGroup,
  filterVarByType,
  removeGroupByIndex,
  renameGroup,
  toggleGroupEnabled,
  updateNestedVarGroupItem,
  updateRootVarGroupItem,
} from "./use-config.helpers";
import type { VarGroupItem, VariableAssignerNodeType } from "./types";

export default function useConfig(
  id: string,
  inputs: VariableAssignerNodeType,
) {
  const editor = useEditor(),
    [isShowRemoveVarConfirm, setConfirm] = useState(false);
  const commit = (
    data: VariableAssignerNodeType,
    definition = cloneDefinition(editor.definition),
  ) => {
    if (editor.readOnly) return;
    const node = definition.graph.nodes.find((node) => node.id === id);
    if (node) {
      node.data = { ...node.data, ...data };
      editor.change(definition);
    }
  };
  const handleGroupEnabledChange = (enabled: boolean) => {
    if (editor.readOnly) return;
    const groups = inputs.advanced_settings?.groups ?? [];
    if (
      !enabled &&
      groups
        .slice(1)
        .some((group) =>
          isSelectorUsed(editor.definition, [id, group.group_name, "output"]),
        )
    ) {
      setConfirm(true);
      return;
    }
    const next = structuredClone(toggleGroupEnabled({ inputs, enabled }));
    const first = next.advanced_settings.groups[0];
    if (enabled && first?.output_type === "any" && first.variables.length) {
      const types = first.variables.map(
        (selector) =>
          availableVariables(editor.definition, id).find(
            (option) =>
              JSON.stringify(option.selector) === JSON.stringify(selector),
          )?.type,
      );
      if (
        types[0] &&
        types.every((type) => type === types[0]) &&
        types[0] !== "any"
      )
        first.output_type = types[0];
    }
    const before = enabled
      ? [id, "output"]
      : [id, groups[0]?.group_name ?? "", "output"];
    const after = enabled ? [id, first!.group_name, "output"] : [id, "output"];
    const definition = renameSelectorReferences(
      editor.definition,
      before,
      after,
    );
    definition.graph.edges = definition.graph.edges.map((edge) =>
      edge.target === id
        ? { ...edge, targetHandle: enabled ? first!.groupId : "target" }
        : edge,
    );
    commit(next, definition);
  };
  return {
    readOnly: editor.readOnly,
    inputs,
    isEnableGroup: !!inputs.advanced_settings?.group_enabled,
    handleListOrTypeChange: (payload: VarGroupItem) =>
      commit(updateRootVarGroupItem(inputs, payload)),
    handleListOrTypeChangeInGroup:
      (groupId: string) => (payload: VarGroupItem) =>
        commit(updateNestedVarGroupItem(inputs, groupId, payload)),
    handleGroupEnabledChange,
    handleAddGroup: () => commit(addGroup(inputs)),
    handleGroupRemoved: (groupId: string) => () => {
      if (editor.readOnly) return;
      const index = inputs.advanced_settings.groups.findIndex(
          (group) => group.groupId === groupId,
        ),
        group = inputs.advanced_settings.groups[index];
      if (!group) return;
      if (isSelectorUsed(editor.definition, [id, group.group_name, "output"])) {
        setConfirm(true);
        return;
      }
      const definition = cloneDefinition(editor.definition);
      definition.graph.edges = definition.graph.edges.filter(
        (edge) => edge.target !== id || edge.targetHandle !== groupId,
      );
      commit(removeGroupByIndex(inputs, index), definition);
    },
    handleVarGroupNameChange: (groupId: string) => (name: string) => {
      if (editor.readOnly) return;
      const group = inputs.advanced_settings.groups.find(
        (group) => group.groupId === groupId,
      );
      if (!group) return;
      if (!/^[A-Za-z_][A-Za-z0-9_]{0,29}$/.test(name)) {
        editor.reportError("请检查分组名称");
        return;
      }
      if (
        inputs.advanced_settings.groups.some(
          (group) => group.groupId !== groupId && group.group_name === name,
        )
      ) {
        editor.reportError("分组名称已存在");
        return;
      }
      commit(
        renameGroup(inputs, groupId, name),
        renameSelectorReferences(
          editor.definition,
          [id, group.group_name, "output"],
          [id, name, "output"],
        ),
      );
    },
    isShowRemoveVarConfirm,
    hideRemoveVarConfirm: () => setConfirm(false),
    onRemoveVarConfirm: () => {},
    getAvailableVars: useGetAvailableVars(),
    filterVar: filterVarByType,
  };
}
