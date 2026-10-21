import type { GraphDefinition } from "../public";
export type {
  GraphDefinition,
  EditorProps,
  MountWorkflowEditor,
} from "../public";
export const VarType = {
  any: "any",
  string: "string",
  number: "number",
  integer: "integer",
  boolean: "boolean",
  object: "object",
  file: "file",
  array: "array",
  arrayString: "array[string]",
  arrayNumber: "array[number]",
  arrayBoolean: "array[boolean]",
  arrayObject: "array[object]",
  arrayFile: "array[file]",
} as const;
export type VarType = (typeof VarType)[keyof typeof VarType];
export const InputVarType = {
  textInput: "text-input",
  paragraph: "paragraph",
  select: "select",
  number: "number",
  checkbox: "checkbox",
  jsonObject: "json_object",
  singleFile: "file",
  multiFiles: "file-list",
} as const;
export type InputVarType = (typeof InputVarType)[keyof typeof InputVarType];
export const BlockEnum = {
  Start: "start",
  End: "end",
  IfElse: "if-else",
} as const;
export interface InputVar {
  variable: string;
  label: string;
  type: InputVarType;
  required: boolean;
  default?:
    string | number | boolean | unknown[] | Record<string, unknown> | null;
  options?: string[];
  max_length?: number;
  json_schema?: Record<string, unknown>;
  [key: string]: unknown;
}
export interface Variable {
  variable: string;
  value_selector: string[];
  value_type?: string;
  [key: string]: unknown;
}
export interface CommonNodeType {
  type: string;
  title: string;
  desc?: string;
  _targetBranches?: Branch[];
  [key: string]: any;
}
export type NodeProps<T extends CommonNodeType> = { id: string; data: T };
export type NodePanelProps<T extends CommonNodeType> = NodeProps<T>;
export interface Branch {
  id: string;
  name: string;
}
export interface Var {
  variable: string;
  type: VarType;
  [key: string]: any;
}
export type ValueSelector = string[];
export type MoreInfo = { type: string; payload?: any };
export type GraphNode = GraphDefinition["graph"]["nodes"][number];
export type Node = GraphNode;
export type GraphEdge = GraphDefinition["graph"]["edges"][number];
export type VariableOption = {
  selector: string[];
  label: string;
  type: VarType;
};
export type NodeOutPutVar = VariableOption;
