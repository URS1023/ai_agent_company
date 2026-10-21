import { useEditor } from "./EditorContext";
import StartPanel from "./vendor/workflow/nodes/start/panel";
import EndPanel from "./vendor/workflow/nodes/end/panel";
import IfElsePanel from "./vendor/workflow/nodes/if-else/panel";
import AggregationPanel from "./vendor/workflow/nodes/variable-assigner/panel";
import { readableAggregation } from "./draft";
export default function PropertyPanel() {
  const editor = useEditor(),
    node = editor.definition.graph.nodes.find(
      (node) => node.id === editor.selectedId,
    );
  if (!node)
    return (
      <aside className="dify-properties">
        <div className="dify-empty-selection">选择节点</div>
      </aside>
    );
  const data = node.data;
  return (
    <aside className="dify-properties" aria-label="节点属性">
      <header className="dify-properties-header">
        <label>
          节点名称
          <input
            value={data.title ?? ""}
            disabled={editor.readOnly}
            maxLength={120}
            onChange={(event) =>
              editor.patchNode(node.id, { title: event.target.value })
            }
          />
        </label>
      </header>
      <div className="dify-properties-body" key={node.id}>
        {data.type === "start" ? (
          <StartPanel
            id={node.id}
            data={{ ...data, variables: data.variables ?? [] }}
          />
        ) : data.type === "end" ? (
          <EndPanel
            id={node.id}
            data={{ ...data, outputs: data.outputs ?? [] }}
          />
        ) : data.type === "if-else" ? (
          <IfElsePanel
            id={node.id}
            data={{
              ...data,
              cases: data.cases ?? [],
              isInIteration: false,
              isInLoop: false,
            }}
          />
        ) : ["variable-aggregator", "variable-assigner"].includes(data.type) &&
          readableAggregation(data) ? (
          <AggregationPanel
            id={node.id}
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
          <p role="status">该节点的属性编辑尚未接入</p>
        )}
      </div>
    </aside>
  );
}
