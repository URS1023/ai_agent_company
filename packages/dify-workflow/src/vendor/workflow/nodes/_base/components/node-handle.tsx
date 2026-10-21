import { Handle, Position } from "reactflow";
import { useEditor } from "../../../../../EditorContext";
/** 分支沿原case_id挂接ReactFlow句柄，不引入原工具目录或加节点请求。 */
export function NodeSourceHandle({
  handleId,
}: {
  handleId: string;
  [key: string]: unknown;
}) {
  const { readOnly } = useEditor();
  return (
    <Handle
      type="source"
      position={Position.Right}
      id={handleId}
      isConnectable={!readOnly}
      className="dify-branch-handle"
    />
  );
}
