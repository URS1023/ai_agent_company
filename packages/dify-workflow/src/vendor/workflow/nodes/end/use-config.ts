import { useEditor } from "../../../../EditorContext";
import type { Variable } from "../../../../types";
import type { EndNodeType } from "./types";
export default function useConfig(id: string, inputs: EndNodeType) {
  const editor = useEditor();
  return {
    readOnly: editor.readOnly,
    inputs,
    handleVarListChange: (outputs: Variable[]) =>
      editor.patchNode(id, { outputs }),
    handleAddVariable: () =>
      editor.patchNode(id, {
        outputs: [
          ...(inputs.outputs ?? []),
          { variable: "", value_selector: [] },
        ],
      }),
  };
}
