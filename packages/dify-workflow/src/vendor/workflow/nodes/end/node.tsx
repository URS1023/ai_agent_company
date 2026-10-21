/** 来源Dify 1.17.1 nodes/end/node.tsx；平台隔离适配见SOURCE_MANIFEST.json。 */
import type { FC } from "react";
import type { EndNodeType } from "./types";
import type { NodeProps } from "../../../../types";
import * as React from "react";
import { VariableLabelInNode } from "../../../../controls/VariableLabel";
import { BlockEnum } from "../../../../types";
import { useWorkflow } from "../../../../adapters/workflow-hooks";
import { useWorkflowVariables } from "../../../../adapters/workflow-hooks";

const Node: FC<NodeProps<EndNodeType>> = ({ id, data }) => {
  const { getBeforeNodesInSameBranch } = useWorkflow();
  const availableNodes = getBeforeNodesInSameBranch(id);
  const { getCurrentVariableType } = useWorkflowVariables();
  const isChatflow = false;

  const startNode = availableNodes.find((node) => {
    return node.data.type === BlockEnum.Start;
  });

  const getNode = (id: string) => {
    return availableNodes.find((node) => node.id === id) || startNode;
  };

  const { outputs } = data;
  const filteredOutputs = outputs.filter(
    ({ value_selector }) => value_selector.length > 0,
  );

  if (!filteredOutputs.length) return null;

  return (
    <div className="mb-1 space-y-0.5 px-3 py-1">
      {filteredOutputs.map(({ value_selector }, index) => {
        const node = getNode(value_selector[0]!);
        const varType = getCurrentVariableType({
          valueSelector: value_selector,
          availableNodes,
          isChatflow,
        });

        return (
          <VariableLabelInNode
            key={index}
            variables={value_selector}
            nodeType={node?.data.type}
            nodeTitle={node?.data.title}
            variableType={varType}
          />
        );
      })}
    </div>
  );
};

export default React.memo(Node);
