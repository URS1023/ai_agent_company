/** 精提原变量聚合组件；只替换宿主依赖，原始SHA与适配边界逐文件登记。 */
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { createHash } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";
const destination = path.dirname(fileURLToPath(import.meta.url));
const upstream = path.resolve(
  destination,
  "../../dify-main/web/app/components/workflow",
);
const files = [
  "nodes/variable-assigner/node.tsx",
  "nodes/variable-assigner/panel.tsx",
  "nodes/variable-assigner/types.ts",
  "nodes/variable-assigner/use-config.helpers.ts",
  "nodes/variable-assigner/components/node-group-item.tsx",
  "nodes/variable-assigner/components/var-group-item.tsx",
  "nodes/variable-assigner/components/var-list/index.tsx",
  "nodes/_base/components/add-button.tsx",
  "nodes/_base/components/remove-button.tsx",
  "nodes/_base/components/list-no-data-placeholder.tsx",
];
const imports = {
  "@/app/components/workflow/types": "types",
  "react-i18next": "adapters/i18n",
  "@remixicon/react": "controls/icons",
  "@langgenius/dify-ui/cn": "controls/primitives",
  "@langgenius/dify-ui/switch": "controls/primitives",
  "@langgenius/dify-ui/button": "controls/primitives",
  "@langgenius/dify-ui/icon-button": "controls/primitives",
  "@/app/components/workflow/nodes/_base/types": "adapters/variable-kind",
  "@/app/components/workflow/nodes/_base/components/field": "controls/Field",
  "@/app/components/workflow/nodes/_base/components/add-button":
    "vendor/workflow/nodes/_base/components/add-button",
  "../_base/components/field": "controls/Field",
  "@/app/components/workflow/nodes/_base/components/split": "controls/Split",
  "@/app/components/workflow/nodes/_base/components/output-vars":
    "controls/OutputVars",
  "@/app/components/workflow/nodes/_base/components/variable/var-reference-picker":
    "controls/VarReferencePicker",
  "../../_base/components/variable/var-reference-picker":
    "controls/VarReferencePicker",
  "@/app/components/workflow/nodes/_base/components/variable/variable-label":
    "controls/VariableLabel",
  "@/utils/var": "adapters/variableKeys",
  "../../../store": "adapters/aggregationStore",
  "../hooks": "adapters/aggregationHooks",
  "../utils": "adapters/aggregationHooks",
  "@/app/components/workflow/nodes/_base/components/variable/utils":
    "adapters/aggregationHooks",
  "@/app/components/workflow/utils": "adapters/aggregationHooks",
  "./add-variable": "controls/AggregationAddVariable",
};
const manifestPath = path.join(destination, "SOURCE_MANIFEST.json");
const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
for (const file of files) {
  const original = await readFile(path.join(upstream, file), "utf8");
  const output = path.join(destination, "src/vendor/workflow", file);
  const relative = (target) => {
    const result = path
      .relative(path.dirname(output), path.join(destination, "src", target))
      .replaceAll("\\", "/");
    return result.startsWith(".") ? result : "./" + result;
  };
  let adapted = original;
  if (file === "nodes/variable-assigner/node.tsx")
    adapted = adapted.replace(
      "import type { NodeProps } from 'reactflow'",
      "import type { NodeProps } from '" + relative("types") + "'",
    );
  for (const [specifier, target] of Object.entries(imports))
    adapted = adapted.replaceAll(`'${specifier}'`, `'${relative(target)}'`);
  adapted = adapted
    .replace(
      "import { v4 as uuid4 } from 'uuid'",
      "const uuid4 = () => crypto.randomUUID().replaceAll('-', '')",
    )
    .replace(
      "import { noop } from 'es-toolkit/function'",
      "const noop = () => undefined",
    );
  if (file.endsWith("/node-group-item.tsx")) {
    adapted = adapted
      .replace(
        "import { useNodes } from 'reactflow'",
        "import { Handle, Position } from 'reactflow'\nimport { useEditor } from '" +
          relative("EditorContext") +
          "'",
      )
      .replace(
        "const nodes: Node[] = useNodes()",
        "const { definition, readOnly } = useEditor()\n  const nodes: Node[] = definition.graph.nodes",
      )
      .replace(
        "'relative rounded-lg",
        "'dify-aggregate-group relative rounded-lg",
      )
      .replace(
        '      <div className="flex h-4',
        '      <Handle type="target" position={Position.Left} id={item.targetHandleId} isConnectable={!readOnly} />\n      <div className="flex h-4',
      );
  }
  if (file.endsWith("/var-group-item.tsx")) {
    adapted = adapted
      .replace(
        "import { toast } from '@/app/notifications'",
        "import { useEditor } from '" + relative("EditorContext") + "'",
      )
      .replace(
        "  const { t } = useTranslation(['appDebug', 'workflow'])",
        "  const { t } = useTranslation(['appDebug', 'workflow'])\n  const { reportError } = useEditor()",
      )
      .replace("toast.error(", "reportError(")
      .replace(
        '<div\n                  className="flex h-6 cursor-text',
        '<button type="button" disabled={readOnly} aria-label={`编辑分组${payload.group_name}`}\n                  className="flex h-6 cursor-text',
      )
      .replace(
        "{payload.group_name}\n                </div>",
        "{payload.group_name}\n                </button>",
      )
      .replace(
        '<div\n                className="ml-0.5 hidden cursor-pointer',
        '<button type="button" aria-label={`删除分组${payload.group_name}`}\n                className="ml-0.5 cursor-pointer',
      )
      .replace(
        '<RiDeleteBinLine className="size-4" />\n              </div>',
        '<RiDeleteBinLine className="size-4" />\n              </button>',
      )
      .replace(/\s*\/\/ style=\{\{[\s\S]*?\/\/ \}\}/, "")
      .replace(" // to fit the input width", "")
      .replace(
        'type="text"\n                  disabled',
        'type="text"\n                  aria-label="分组名称"\n                  disabled',
      )
      .replace(
        "filterVar={filterVar}\n      />",
        "filterVar={filterVar}\n        availableVars={availableVars}\n      />",
      );
  }
  if (file.endsWith("/var-list/index.tsx"))
    adapted = adapted
      .replace(
        "readonly: boolean",
        'availableVars?: import("' +
          relative("types") +
          '").VariableOption[]\n  readonly: boolean',
      )
      .replace(
        "onOpen = noop, filterVar })",
        "onOpen = noop, filterVar, availableVars })",
      )
      .replace(
        "filterVar={filterVar}",
        "filterVar={filterVar}\n            availableVars={availableVars}",
      );
  await mkdir(path.dirname(output), { recursive: true });
  await writeFile(
    output,
    `/** 来源Dify1.17.1 ${file}；原聚合语义与宿主适配见SOURCE_MANIFEST。 */\n${adapted}`,
  );
  const source = `dify-main/web/app/components/workflow/${file}`;
  const entry = {
    source,
    target: path.relative(destination, output).replaceAll("\\", "/"),
    sha256: createHash("sha256").update(original).digest("hex"),
    changes:
      "保留原聚合节点/面板/分组与列表JSX、纯修改函数；上下文/图变量/控件用平台无请求适配，分组句柄候选限制为平台新增边界；原console/inspect-vars/协作store未接入。",
  };
  manifest.files = manifest.files.filter((item) => item.source !== source);
  manifest.files.push(entry);
}
manifest.scope =
  "Start/End/IfElse与原变量聚合节点、分组面板/纯helpers；已有简单json_object字段只读保留，可选择字典子属性。不包含赋值/列表/Answer/容器编辑。";
manifest.platform_adaptations = [
  "分组targetHandle候选过滤为平台新增边界；原Dify useGetAvailableVars未使用handleId。",
  "Graphon0.8分组output_type不接受any；空草稿需选择实际类型变量后才能保存。聚合取首个存在值，不求和。",
  "已有简单json_object字段自身只读，字典子属性可选，数组下标不生成选择器；未知字段完整保留。",
  "新node ID去连字符兼容后续原模板；已有node ID不改写。40000文本长度与当前平台一致。",
  "原协作store和inspect-vars后台未接；当前岛无HTTP、自动保存或重试。",
];
await writeFile(manifestPath, JSON.stringify(manifest, null, 2) + "\n");
