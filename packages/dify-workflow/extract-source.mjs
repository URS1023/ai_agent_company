/** 从锁定本地源码精确提取节点与面板，记录改动前SHA；不读取服务或账户。 */
import { readFile, writeFile, mkdir, copyFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";
const destination = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(destination, "../..");
const upstream = path.join(root, "dify-main");
const sourceRoot = path.join(upstream, "web/app/components/workflow");
const files = [
  "nodes/start/node.tsx",
  "nodes/start/panel.tsx",
  "nodes/start/types.ts",
  "nodes/end/node.tsx",
  "nodes/end/panel.tsx",
  "nodes/end/types.ts",
  "nodes/if-else/node.tsx",
  "nodes/if-else/panel.tsx",
  "nodes/if-else/types.ts",
  "nodes/if-else/utils.ts",
  "nodes/if-else/use-config.helpers.ts",
];
const manifest = [];
const relativeImport = (from, target) => {
  const result = path
    .relative(path.dirname(from), path.join(destination, "src", target))
    .replaceAll("\\", "/");
  return result.startsWith(".") ? result : `./${result}`;
};
for (const file of files) {
  const original = await readFile(path.join(sourceRoot, file), "utf8");
  const output = path.join(destination, "src/vendor/workflow", file);
  let adapted = original
    .replaceAll(
      "'@/app/components/workflow/types'",
      `'${relativeImport(output, "types")}'`,
    )
    .replaceAll(
      "'react-i18next'",
      `'${relativeImport(output, "adapters/i18n")}'`,
    )
    .replaceAll(
      "'@/app/components/workflow/nodes/_base/types'",
      `'${relativeImport(output, "adapters/variable-kind")}'`,
    )
    .replaceAll(
      "'@/app/components/workflow/nodes/_base/components/field'",
      `'${relativeImport(output, "controls/Field")}'`,
    )
    .replaceAll(
      "'@/app/components/workflow/nodes/_base/components/split'",
      `'${relativeImport(output, "controls/Split")}'`,
    )
    .replaceAll(
      "'@/app/components/app/configuration/config-var/config-modal'",
      `'${relativeImport(output, "controls/ConfigVariableModal")}'`,
    )
    .replaceAll(
      "'@/app/components/workflow/nodes/_base/components/variable/var-list'",
      `'${relativeImport(output, "controls/OutputVariables")}'`,
    )
    .replaceAll(
      "'@langgenius/dify-ui/button'",
      `'${relativeImport(output, "controls/primitives")}'`,
    )
    .replaceAll(
      "'@remixicon/react'",
      `'${relativeImport(output, "controls/icons")}'`,
    );
  if (file.endsWith("use-config.helpers.ts"))
    adapted = adapted.replace(
      "import { v4 as uuid4 } from 'uuid'",
      "const uuid4 = () => crypto.randomUUID()",
    );
  if (file === "nodes/start/panel.tsx") {
    adapted = adapted
      .replace(
        /\n            <div className="mt-1 space-y-1">[\s\S]*?\n            <\/div>\n          <\/>/,
        "\n          </>",
      )
      .replace("          supportFile\n", "")
      .replace(/^import Split.*\n/m, "")
      .replace(/^import VarItem.*\n/m, "")
      .replace("    isChatflow,\n", "");
  }
  if (file === "nodes/if-else/panel.tsx")
    adapted = adapted.replace(
      /        <div className="text-xs leading-4\.5 font-normal text-text-tertiary">[\s\S]*?<\/div>\n/,
      "",
    );
  if (file === "nodes/end/node.tsx") {
    adapted = adapted
      .replace("import { useAtomValue } from 'jotai'\n", "")
      .replace(/^import \{ isChatflowAtom \}.*\n/m, "")
      .replace(
        "'@/app/components/workflow/nodes/_base/components/variable/variable-label'",
        `'${relativeImport(output, "controls/VariableLabel")}'`,
      )
      .replace(
        "'../../hooks/use-workflow'",
        `'${relativeImport(output, "adapters/workflow-hooks")}'`,
      )
      .replace(
        "'../../hooks/use-workflow-variables'",
        `'${relativeImport(output, "adapters/workflow-hooks")}'`,
      )
      .replace(
        "const isChatflow = useAtomValue(isChatflowAtom)",
        "const isChatflow = false",
      );
  }
  if (file === "nodes/if-else/node.tsx")
    adapted = adapted
      .replace(
        ": !!c.value",
        ": c.value !== undefined && c.value !== null && c.value !== ''",
      )
      .replace(
        ": !!condition.value",
        ": condition.value !== undefined && condition.value !== null && condition.value !== ''",
      );
  await mkdir(path.dirname(output), { recursive: true });
  await writeFile(
    output,
    `/** 来源Dify 1.17.1 ${file}；平台隔离适配见SOURCE_MANIFEST.json。 */\n${adapted}`,
  );
  manifest.push({
    source: `dify-main/web/app/components/workflow/${file}`,
    target: path.relative(destination, output).replaceAll("\\", "/"),
    sha256: createHash("sha256").update(original).digest("hex"),
    changes:
      "保留节点/面板JSX及条件纯函数；替换平台上下文/本地控件引用，移除未支持文件/Chatflow固定项及冗余常驻文案；条件保留0/false。",
  });
}
await mkdir(path.join(destination, "licenses"), { recursive: true });
await copyFile(
  path.join(upstream, "LICENSE"),
  path.join(destination, "licenses/Dify-LICENSE.txt"),
);
await copyFile(
  path.join(upstream, "web/public/logo/logo.svg"),
  path.join(destination, "src/dify-logo.svg"),
);
await writeFile(
  path.join(destination, "SOURCE_MANIFEST.json"),
  JSON.stringify(
    {
      project: "Dify",
      version: "1.17.1",
      workflow_entry_sha256:
        "1ffaf2754f9b965a41d03bda480dcc58bc86c99ac1d9ac38906d43b45850a11a",
      license: "licenses/Dify-LICENSE.txt",
      files: manifest,
      scope:
        "Start/End/IfElse节点与属性外层、原条件修改纯函数；平台控件和显式保存适配；尚非全部原生编辑器。",
    },
    null,
    2,
  ) + "\n",
);
// 完整重新提取时也保留第二批原聚合组件与逐源清单。
await import("./extract-aggregation.mjs");
