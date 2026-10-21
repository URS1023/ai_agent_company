import { useEditor } from "../EditorContext";
import { availableVariables, beforeNodeIds } from "../draft";
export function useWorkflow() {
  const { definition } = useEditor();
  return {
    getBeforeNodesInSameBranch: (id: string) => {
      const ids = beforeNodeIds(
        definition.graph.nodes,
        definition.graph.edges,
        id,
      );
      return definition.graph.nodes.filter((node) => ids.has(node.id));
    },
  };
}
export function useWorkflowVariables() {
  const { definition, selectedId } = useEditor();
  return {
    getCurrentVariableType: ({
      valueSelector,
    }: {
      valueSelector: string[];
      [key: string]: unknown;
    }) =>
      availableVariables(definition, selectedId).find(
        (option) =>
          JSON.stringify(option.selector) === JSON.stringify(valueSelector),
      )?.type,
  };
}
