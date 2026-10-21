import { useEditor } from "../EditorContext";
export function VariableLabelInNode({
  variables,
  nodeTitle,
}: {
  variables: string[];
  nodeTitle?: string;
  [key: string]: unknown;
}) {
  return (
    <div className="dify-variable-label">
      <span>{nodeTitle || "变量"}</span>
      <code>{variables.slice(1).join(".")}</code>
    </div>
  );
}
export function VariableLabelInText({
  variables,
}: {
  variables: string[];
  [key: string]: unknown;
}) {
  const { definition } = useEditor();
  return (
    <VariableLabelInNode
      variables={variables}
      nodeTitle={
        definition.graph.nodes.find((node) => node.id === variables[0])?.data
          .title
      }
    />
  );
}
