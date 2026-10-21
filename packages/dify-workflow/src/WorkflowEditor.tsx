import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactFlow, {
  Background,
  Controls,
  ReactFlowProvider,
  addEdge,
  useNodesState,
  useEdgesState,
} from "reactflow";
import type { Connection, Node, Edge } from "reactflow";
import type { EditorProps, GraphDefinition } from "./types";
import {
  cloneDefinition,
  editableNodeTypes,
  editingBlockers,
  validateDraft,
} from "./draft";
import { EditorContext } from "./EditorContext";
import OriginalNode from "./OriginalNode";
import PropertyPanel from "./PropertyPanel";
import logo from "./dify-logo.svg";
const nodeTypes = { dify: OriginalNode };
const names: Record<string, string> = {
  start: "开始",
  end: "结束",
  "if-else": "条件分支",
};

function Canvas(props: EditorProps) {
  const [definition, setDefinition] = useState(() =>
    cloneDefinition(props.definition),
  );
  const [selectedId, select] = useState(props.definition.root_node_id);
  const [error, setError] = useState(""),
    [dirty, setDirty] = useState(false);
  const [history, setHistory] = useState<GraphDefinition[]>([]),
    [future, setFuture] = useState<GraphDefinition[]>([]);
  const [newType, setNewType] = useState("end");
  const blockers = useMemo(() => editingBlockers(definition), [definition]);
  const readOnly = props.readOnly || blockers.length > 0 || !!props.saving;
  const stateRef = useRef(definition),
    readOnlyRef = useRef(readOnly);
  stateRef.current = definition;
  readOnlyRef.current = readOnly;
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  useEffect(() => {
    const next = cloneDefinition(props.definition);
    setDefinition(next);
    stateRef.current = next;
    setHistory([]);
    setFuture([]);
    setDirty(false);
    setError("");
  }, [props.definition]);
  useEffect(() => {
    props.onDirtyChange?.(dirty);
  }, [dirty, props.onDirtyChange]);
  useEffect(() => {
    setNodes(
      definition.graph.nodes.map((node) => ({
        ...node,
        type: "dify",
        selected: node.id === selectedId,
        draggable: !readOnly,
        deletable: false,
        connectable: !readOnly,
      })),
    );
    setEdges(
      definition.graph.edges.map((edge) => ({
        ...edge,
        type: "default",
        deletable: !readOnly,
      })),
    );
  }, [definition, selectedId, readOnly, setNodes, setEdges]);
  const change = useCallback((next: GraphDefinition) => {
    if (readOnlyRef.current) return;
    const previous = stateRef.current;
    if (JSON.stringify(previous) === JSON.stringify(next)) return;
    setHistory((items) => [...items.slice(-39), cloneDefinition(previous)]);
    setFuture([]);
    stateRef.current = next;
    setDefinition(next);
    setDirty(true);
    setError("");
  }, []);
  const patchNode = useCallback(
    (id: string, patch: Record<string, unknown>) => {
      if (readOnlyRef.current) return;
      const next = cloneDefinition(stateRef.current),
        node = next.graph.nodes.find((node) => node.id === id);
      if (node) {
        node.data = { ...node.data, ...structuredClone(patch) };
        change(next);
      }
    },
    [change],
  );
  const undo = () => {
    if (readOnly || !history.length) return;
    const previous = history.at(-1)!;
    setFuture((items) => [cloneDefinition(definition), ...items]);
    setHistory((items) => items.slice(0, -1));
    setDefinition(previous);
    stateRef.current = previous;
    setDirty(true);
    setError("");
  };
  const redo = () => {
    if (readOnly || !future.length) return;
    const next = future[0]!;
    setHistory((items) => [...items, cloneDefinition(definition)]);
    setFuture((items) => items.slice(1));
    setDefinition(next);
    stateRef.current = next;
    setDirty(true);
    setError("");
  };
  const connect = (connection: Connection) => {
    if (
      readOnly ||
      !connection.source ||
      !connection.target ||
      connection.source === connection.target
    )
      return;
    // 不允许把下游接回上游形成非容器循环；真正循环应使用原Loop节点。
    const next = cloneDefinition(definition),
      visited = new Set<string>(),
      queue = [connection.target];
    while (queue.length) {
      const id = queue.shift()!;
      if (id === connection.source) {
        setError("该连线会形成循环");
        return;
      }
      if (visited.has(id)) continue;
      visited.add(id);
      queue.push(
        ...next.graph.edges
          .filter((edge) => edge.source === id)
          .map((edge) => edge.target),
      );
    }
    next.graph.edges = addEdge(
      { ...connection, id: crypto.randomUUID(), type: "custom" },
      next.graph.edges,
    );
    change(next);
  };
  const addNode = () => {
    if (readOnly || !editableNodeTypes.has(newType) || newType === "start")
      return;
    const next = cloneDefinition(definition),
      offset = next.graph.nodes.length;
    const data =
      newType === "end"
        ? { type: newType, title: names[newType]!, outputs: [] }
        : {
            type: newType,
            title: names[newType]!,
            cases: [
              { case_id: "true", logical_operator: "and", conditions: [] },
            ],
            _targetBranches: [
              { id: "true", name: "IF" },
              { id: "false", name: "ELSE" },
            ],
          };
    const node = {
      id: crypto.randomUUID(),
      type: "custom",
      position: { x: 160 + offset * 45, y: 100 + offset * 50 },
      data,
    };
    next.graph.nodes.push(node);
    change(next);
    select(node.id);
  };
  const deleteSelected = () => {
    if (readOnly || selectedId === definition.root_node_id) return;
    const next = cloneDefinition(definition);
    next.graph.nodes = next.graph.nodes.filter(
      (node) => node.id !== selectedId,
    );
    next.graph.edges = next.graph.edges.filter(
      (edge) => edge.source !== selectedId && edge.target !== selectedId,
    );
    change(next);
    select(next.root_node_id);
  };
  const save = () => {
    if (readOnly || (!dirty && !props.externalDirty)) return;
    const errors = validateDraft(definition);
    if (Object.keys(errors).length) {
      setError(Object.values(errors)[0]!);
      const key = Object.keys(errors)[0] ?? "";
      const node = definition.graph.nodes.find((item) =>
        key.startsWith(`nodes.${item.id}`),
      );
      if (node) select(node.id);
      return;
    }
    props.onSave(cloneDefinition(definition));
  };
  return (
    <EditorContext.Provider
      value={{
        definition,
        readOnly,
        selectedId,
        change,
        patchNode,
        reportError: setError,
      }}
    >
      <div
        className="dify-island"
        onKeyDown={(event) => {
          const target = event.target as HTMLElement;
          if (
            ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) ||
            target.isContentEditable
          )
            event.stopPropagation();
        }}
      >
        <header className="dify-toolbar">
          <img src={logo} width="58" height="24" alt="Dify" />
          <span className="dify-copyright">© 2025 LangGenius, Inc.</span>
          <span className="grow" />
          <button
            type="button"
            disabled={readOnly || !history.length}
            onClick={undo}
          >
            撤销
          </button>
          <button
            type="button"
            disabled={readOnly || !future.length}
            onClick={redo}
          >
            重做
          </button>
          <select
            aria-label="节点类型"
            value={newType}
            disabled={readOnly}
            onChange={(event) => setNewType(event.target.value)}
          >
            <option value="end">结束</option>
            <option value="if-else">条件分支</option>
          </select>
          <button type="button" onClick={addNode} disabled={readOnly}>
            添加节点
          </button>
          <button
            type="button"
            disabled={readOnly || selectedId === definition.root_node_id}
            onClick={deleteSelected}
          >
            删除节点
          </button>
          <button
            type="button"
            className="primary"
            disabled={readOnly || (!dirty && !props.externalDirty)}
            onClick={save}
            title={blockers.join("；")}
          >
            {props.saving ? "保存中…" : "保存"}
          </button>
        </header>
        {error && (
          <div className="dify-error" role="alert">
            {error}
          </div>
        )}
        {blockers.length > 0 && (
          <div className="dify-readonly" role="status">
            {blockers[0]}
          </div>
        )}
        <div className="dify-editor-body">
          <div className="dify-canvas">
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onNodeClick={(_event, node) => select(node.id)}
              onNodeDragStop={(_event, node: Node) => {
                const next = cloneDefinition(definition),
                  target = next.graph.nodes.find((item) => item.id === node.id);
                if (target) {
                  target.position = { ...node.position };
                  change(next);
                }
              }}
              onEdgesDelete={(deleted: Edge[]) => {
                const next = cloneDefinition(definition);
                next.graph.edges = next.graph.edges.filter(
                  (edge) => !deleted.some((item) => item.id === edge.id),
                );
                change(next);
              }}
              onConnect={connect}
              nodesDraggable={!readOnly}
              nodesConnectable={!readOnly}
              elementsSelectable
              deleteKeyCode={readOnly ? null : ["Backspace", "Delete"]}
              fitView
              defaultViewport={definition.graph.viewport}
              minZoom={0.2}
              maxZoom={2}
              proOptions={{ hideAttribution: true }}
            >
              <Background color="#d6d9e3" gap={16} />
              <Controls showInteractive={false} />
            </ReactFlow>
          </div>
          <PropertyPanel />
        </div>
      </div>
    </EditorContext.Provider>
  );
}
export default function WorkflowEditor(props: EditorProps) {
  return (
    <ReactFlowProvider>
      <Canvas {...props} />
    </ReactFlowProvider>
  );
}
