/** 宿主与React岛共享的图协议；保持框架无关，避免双份类型漂移。 */
export interface GraphDefinition {
  format: "graph";
  engine: "graphon";
  engine_version: string;
  root_node_id: string;
  graph: {
    nodes: {
      id: string;
      position: { x: number; y: number };
      data: {
        type: string;
        title: string;
        _targetBranches?: { id: string; name: string }[];
        [key: string]: any;
      };
      [key: string]: any;
    }[];
    edges: { id: string; source: string; target: string; [key: string]: any }[];
    [key: string]: any;
  };
  [key: string]: any;
}
export interface EditorProps {
  definition: GraphDefinition;
  readOnly: boolean;
  saving?: boolean;
  externalDirty?: boolean;
  onSave: (definition: GraphDefinition) => void;
  onDirtyChange?: (dirty: boolean) => void;
}
export interface EditorHandle {
  update: (props: EditorProps) => void;
  unmount: () => void;
}
export type MountWorkflowEditor = (
  host: HTMLElement,
  initial: EditorProps,
) => EditorHandle;

/** 用原Start/End协议组装输入输出骨架，不包含业务样例或外部资源。 */
export function createInitialDefinition(): GraphDefinition {
  const startId = crypto.randomUUID().replaceAll("-", ""),
    endId = crypto.randomUUID().replaceAll("-", "");
  return {
    format: "graph",
    engine: "graphon",
    engine_version: "0.8.0",
    root_node_id: startId,
    graph: {
      nodes: [
        {
          id: startId,
          type: "custom",
          position: { x: 80, y: 120 },
          data: {
            type: "start",
            title: "开始",
            variables: [
              {
                variable: "input",
                label: "输入",
                type: "text-input",
                required: true,
              },
            ],
          },
        },
        {
          id: endId,
          type: "custom",
          position: { x: 450, y: 120 },
          data: {
            type: "end",
            title: "结束",
            outputs: [
              {
                variable: "output",
                value_selector: [startId, "input"],
                value_type: "string",
              },
            ],
          },
        },
      ],
      edges: [
        {
          id: crypto.randomUUID(),
          source: startId,
          target: endId,
          sourceHandle: "source",
          targetHandle: "target",
          type: "custom",
        },
      ],
    },
  };
}
