/** 来源Dify1.17.1 nodes/variable-assigner/components/node-group-item.tsx；原聚合语义与宿主适配见SOURCE_MANIFEST。 */
import type { Node, ValueSelector, VarType } from "../../../types";
import type { VariableAssignerNodeType } from "../types";
import { cn } from "../../../../../controls/primitives";
import { memo, useMemo } from "react";
import { useTranslation } from "../../../../../adapters/i18n";
import { Handle, Position } from "reactflow";
import { useEditor } from "../../../../../EditorContext";
import { isSystemVar } from "../../../../../adapters/aggregationHooks";
import { VariableLabelInNode } from "../../../../../controls/VariableLabel";
import { isExceptionVariable } from "../../../../../adapters/aggregationHooks";
import { useStore } from "../../../../../adapters/aggregationStore";
import { BlockEnum } from "../../../types";
import {
  useGetAvailableVars,
  useVariableAssigner,
} from "../../../../../adapters/aggregationHooks";
import { filterVar } from "../../../../../adapters/aggregationHooks";
import AddVariable from "../../../../../controls/AggregationAddVariable";

const i18nPrefix = "nodes.variableAssigner";
type GroupItem = {
  groupEnabled: boolean;
  targetHandleId: string;
  title: string;
  type: string;
  variables: ValueSelector[];
  variableAssignerNodeId: string;
  variableAssignerNodeData: VariableAssignerNodeType;
};
type NodeGroupItemProps = {
  item: GroupItem;
};
const NodeGroupItem = ({ item }: NodeGroupItemProps) => {
  const { t } = useTranslation(["workflowLogic"]);
  const enteringNodePayload = useStore((s) => s.enteringNodePayload);
  const hoveringAssignVariableGroupId = useStore(
    (s) => s.hoveringAssignVariableGroupId,
  );
  const { definition, readOnly } = useEditor();
  const nodes: Node[] = definition.graph.nodes;
  const { handleGroupItemMouseEnter, handleGroupItemMouseLeave } =
    useVariableAssigner();
  const getAvailableVars = useGetAvailableVars();
  const groupEnabled = item.groupEnabled;
  const outputType = useMemo(() => {
    if (!groupEnabled) return item.variableAssignerNodeData.output_type;

    const group = item.variableAssignerNodeData.advanced_settings?.groups.find(
      (group) => group.groupId === item.targetHandleId,
    );
    return group?.output_type || "";
  }, [item.variableAssignerNodeData, item.targetHandleId, groupEnabled]);
  const availableVars = getAvailableVars(
    item.variableAssignerNodeId,
    item.targetHandleId,
    filterVar(outputType as VarType),
    true,
  );
  const showSelectionBorder = useMemo(() => {
    if (
      groupEnabled &&
      enteringNodePayload?.nodeId === item.variableAssignerNodeId
    ) {
      if (hoveringAssignVariableGroupId)
        return hoveringAssignVariableGroupId !== item.targetHandleId;
      else
        return (
          enteringNodePayload?.nodeData.advanced_settings?.groups[0]!
            .groupId !== item.targetHandleId
        );
    }

    return false;
  }, [
    enteringNodePayload,
    groupEnabled,
    hoveringAssignVariableGroupId,
    item.targetHandleId,
    item.variableAssignerNodeId,
  ]);
  const showSelectedBorder = useMemo(() => {
    if (
      groupEnabled &&
      enteringNodePayload?.nodeId === item.variableAssignerNodeId
    ) {
      if (hoveringAssignVariableGroupId)
        return hoveringAssignVariableGroupId === item.targetHandleId;
      else
        return (
          enteringNodePayload?.nodeData.advanced_settings?.groups[0]!
            .groupId === item.targetHandleId
        );
    }

    return false;
  }, [
    enteringNodePayload,
    groupEnabled,
    hoveringAssignVariableGroupId,
    item.targetHandleId,
    item.variableAssignerNodeId,
  ]);

  return (
    <div
      className={cn(
        "dify-aggregate-group relative rounded-lg border-[1.5px] border-transparent px-1.5 pt-1 pb-1.5",
        showSelectionBorder &&
          "border-dashed! border-divider-subtle! bg-state-base-hover",
        showSelectedBorder &&
          "border-text-accent! bg-util-colors-blue-blue-50!",
      )}
      onMouseEnter={() =>
        groupEnabled && handleGroupItemMouseEnter(item.targetHandleId)
      }
      onMouseLeave={handleGroupItemMouseLeave}
    >
      <Handle
        type="target"
        position={Position.Left}
        id={item.targetHandleId}
        isConnectable={!readOnly}
      />
      <div className="flex h-4 items-center justify-between text-2xs font-medium text-text-tertiary">
        <span
          className={cn(
            "grow truncate uppercase",
            showSelectedBorder && "text-text-accent",
          )}
          title={item.title}
        >
          {item.title}
        </span>
        <div className="flex items-center">
          <span className="ml-2 shrink-0">{item.type}</span>
          <div className="mr-1 ml-2 h-2.5 w-px bg-divider-regular"></div>
          <AddVariable
            availableVars={availableVars}
            variableAssignerNodeId={item.variableAssignerNodeId}
            variableAssignerNodeData={item.variableAssignerNodeData}
            handleId={item.targetHandleId}
          />
        </div>
      </div>
      {!item.variables.length && (
        <div
          className={cn(
            "relative flex h-5.5 items-center justify-between space-x-1 rounded-md bg-workflow-block-parma-bg px-1 text-2xs font-normal text-text-tertiary uppercase",
            (showSelectedBorder || showSelectionBorder) && "bg-black/2!",
          )}
        >
          {t(($) => $[`${i18nPrefix}.varNotSet`], { ns: "workflowLogic" })}
        </div>
      )}
      {!!item.variables.length && (
        <div className="space-y-0.5">
          {item.variables.map((variable = [], index) => {
            const isSystem = isSystemVar(variable);

            const node = isSystem
              ? nodes.find((node) => node.data.type === BlockEnum.Start)
              : nodes.find((node) => node.id === variable[0]);
            const varName = isSystem
              ? `sys.${variable[variable.length - 1]}`
              : variable.slice(1).join(".");
            const isException = isExceptionVariable(varName, node?.data.type);

            return (
              <VariableLabelInNode
                key={index}
                variables={variable}
                nodeType={node?.data.type}
                nodeTitle={node?.data.title}
                isExceptionVariable={isException}
              />
            );
          })}
        </div>
      )}
    </div>
  );
};

export default memo(NodeGroupItem);
