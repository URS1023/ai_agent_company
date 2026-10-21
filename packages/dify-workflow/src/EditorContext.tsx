import { createContext, useContext } from "react";
import type { GraphDefinition, GraphNode } from "./types";
export interface EditorState {
  definition: GraphDefinition;
  readOnly: boolean;
  selectedId: string;
  change: (next: GraphDefinition) => void;
  patchNode: (id: string, patch: Partial<GraphNode["data"]>) => void;
  reportError: (message: string) => void;
}
export const EditorContext = createContext<EditorState | null>(null);
export function useEditor(): EditorState {
  const value = useContext(EditorContext);
  if (!value) throw new Error("工作流编辑器上下文缺失");
  return value;
}
