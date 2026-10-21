import { createRoot } from "react-dom/client";
import { Component } from "react";
import type { ReactNode } from "react";
import WorkflowEditor from "./WorkflowEditor";
import type { EditorProps, MountWorkflowEditor } from "./types";
import flowStyles from "reactflow/dist/style.css?inline";
import editorStyles from "./editor.css?inline";
export type { EditorProps, GraphDefinition } from "./types";
export { createInitialDefinition } from "../public";
class EditorBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <div role="alert">编辑器加载失败，请关闭后重新打开。</div>
    ) : (
      this.props.children
    );
  }
}
/** Vue只传当前已授权定义与显式保存回调；React岛无网络、存储或全局应用状态。 */
export const mountWorkflowEditor: MountWorkflowEditor = (host, initial) => {
  const shadow = host.shadowRoot ?? host.attachShadow({ mode: "open" });
  const style = document.createElement("style");
  style.textContent = `${flowStyles}\n${editorStyles}`;
  const container = document.createElement("div");
  container.className = "dify-island-root";
  shadow.replaceChildren(style, container);
  const root = createRoot(container);
  const update = (props: EditorProps) =>
    root.render(
      <EditorBoundary>
        <WorkflowEditor {...props} />
      </EditorBoundary>,
    );
  update(initial);
  return {
    update,
    unmount: () => {
      root.unmount();
      shadow.replaceChildren();
    },
  };
};
