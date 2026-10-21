import { useState } from "react";
import { useEditor } from "../EditorContext";
import type { VariableOption } from "../types";
import VariablePicker from "./VariablePicker";
import type { VariableAssignerNodeType } from "../vendor/workflow/nodes/variable-assigner/types";

/** 节点内原添加入口的无后台适配；沿所选分组追加准确选择器。 */
export default function AggregationAddVariable({
  availableVars,
  variableAssignerNodeId,
  variableAssignerNodeData,
  handleId,
}: {
  availableVars: VariableOption[];
  variableAssignerNodeId: string;
  variableAssignerNodeData: VariableAssignerNodeType;
  handleId?: string;
}) {
  const editor = useEditor(),
    [open, setOpen] = useState(false);
  if (editor.readOnly) return null;
  return (
    <span className="dify-aggregate-add nodrag nopan">
      <button
        type="button"
        aria-label="添加聚合变量"
        onClick={() => setOpen(!open)}
      >
        +
      </button>
      {open && (
        <VariablePicker
          nodeId={variableAssignerNodeId}
          value={[]}
          options={availableVars}
          label="选择聚合变量"
          onChange={(selector, option) => {
            const data = structuredClone(variableAssignerNodeData);
            const group = data.advanced_settings?.group_enabled
              ? data.advanced_settings.groups.find(
                  (item) => item.groupId === handleId,
                )
              : data;
            if (
              !group ||
              group.variables.some(
                (value) => JSON.stringify(value) === JSON.stringify(selector),
              )
            )
              return;
            group.variables.push(selector);
            if (option.type !== "any") group.output_type = option.type;
            editor.patchNode(variableAssignerNodeId, data);
            setOpen(false);
          }}
        />
      )}
    </span>
  );
}
