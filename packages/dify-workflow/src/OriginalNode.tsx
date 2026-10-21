import { memo, useEffect } from "react";
import { Handle, Position, useUpdateNodeInternals } from "reactflow";
import type { NodeProps } from "reactflow";
import { useEditor } from "./EditorContext";
import StartNode from "./vendor/workflow/nodes/start/node";
import EndNode from "./vendor/workflow/nodes/end/node";
import IfElseNode from "./vendor/workflow/nodes/if-else/node";
import AggregationNode from "./vendor/workflow/nodes/variable-assigner/node";
import type { CommonNodeType } from "./types";
import { readableAggregation } from "./draft";
const icons: Record<string, string> = { start: "▷", end: "□", "if-else": "⑂" };
/** 原节点内部直接复用；外层保留Dify头部/句柄分区，去除协作头像、插件安装与后台检查器。 */
function OriginalNode(props: NodeProps<CommonNodeType>) {
  const { readOnly } = useEditor(),
    { data, selected } = props;
  const updateInternals = useUpdateNodeInternals();
  const handles = JSON.stringify([
    !!data.advanced_settings?.group_enabled,
    data.advanced_settings?.groups?.map?.((group: any) => group.groupId) ?? [],
  ]);
  useEffect(() => {
    updateInternals(props.id);
  }, [props.id, handles, updateInternals]);
  return (
    <article
      className={`dify-original-node ${selected ? "is-selected" : ""}`}
      data-node-kind={data.type}
    >
      <header className="dify-node-header">
        <span className={`dify-node-icon ${data.type}`}>
          {icons[data.type] ?? "◇"}
        </span>
        <strong>{data.title || data.type}</strong>
      </header>
      {data.type !== "start" &&
        !["variable-aggregator", "variable-assigner"].includes(data.type) && (
          <Handle
            type="target"
            position={Position.Left}
            id="target"
            isConnectable={!readOnly}
          />
        )}
      {data.type === "start" ? (
        <StartNode
          {...props}
          data={{ ...data, variables: data.variables ?? [] }}
        />
      ) : data.type === "end" ? (
        <EndNode {...props} data={{ ...data, outputs: data.outputs ?? [] }} />
      ) : data.type === "if-else" ? (
        <IfElseNode
          {...props}
          data={{
            ...data,
            cases: data.cases ?? [],
            isInIteration: false,
            isInLoop: false,
          }}
        />
      ) : ["variable-aggregator", "variable-assigner"].includes(data.type) &&
        readableAggregation(data) ? (
        <AggregationNode
          id={props.id}
          data={{
            ...data,
            output_type: data.output_type ?? "any",
            variables: data.variables ?? [],
            advanced_settings: data.advanced_settings ?? {
              group_enabled: false,
              groups: [],
            },
          }}
        />
      ) : (
        <div className="dify-node-unsupported">
          {data.desc || "属性尚未接入"}
        </div>
      )}
      {data.type !== "end" && data.type !== "if-else" && (
        <Handle
          type="source"
          position={Position.Right}
          id="source"
          isConnectable={!readOnly}
        />
      )}
    </article>
  );
}
export default memo(OriginalNode);
