/** 仅从锁定本机OWUI精提结构化输出、分组与图标；不执行原项目脚本。 */
import { readFile, writeFile, copyFile, mkdir } from "node:fs/promises";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import path from "node:path";
const destination = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(destination, "../..");
const upstream = path.join(root, ".local/native_windows/sources/openwebui");
const files = [
  "src/lib/components/chat/Messages/structuredOutput.ts",
  "src/lib/components/chat/Messages/StructuredOutputRenderer.svelte",
  "src/lib/components/chat/Messages/Markdown/ConsecutiveDetailsGroup.svelte",
  "src/lib/components/common/Collapsible.svelte",
  "src/lib/components/icons/ChevronUp.svelte",
  "src/lib/components/icons/ChevronDown.svelte",
  "src/lib/components/icons/Spinner.svelte",
  "src/lib/components/icons/Sparkles.svelte",
  "src/lib/components/icons/CheckCircle.svelte",
  "src/lib/components/icons/XMark.svelte",
  "src/lib/components/layout/Sidebar/icons/EditPencil.svelte",
];
const manifest = [];
for (const source of files) {
  let original;
  try {
    original = await readFile(path.join(upstream, source), "utf8");
  } catch (error) {
    if (source.endsWith("/icons/Spinner.svelte")) {
      continue;
    }
    throw error;
  }
  const name = path.basename(source),
    target = path.join(destination, "src/reused", name);
  let adapted = original
    .replaceAll(/\$lib\/components\/(?:common|icons)\//g, "./")
    .replaceAll(
      "'./Markdown/ConsecutiveDetailsGroup.svelte'",
      "'./ConsecutiveDetailsGroup.svelte'",
    )
    .replaceAll("'../icons/", "'./");
  adapted = adapted.replace(
    /import type \{ i18n as i18nType \} from 'i18next';/g,
    "type i18nType = { t: (key: string, values?: Record<string, unknown>) => string };",
  );
  adapted = adapted.replace(
    /getContext<typeof import\('\$lib\/i18n'\)\.default>/g,
    "getContext<any>",
  );
  adapted = adapted.replace(
    /import \{ settings \} from '\$lib\/stores';/g,
    "const { settings } = getContext<any>('native-chat-scope');",
  );
  adapted = adapted.replace(
    /import \{ resolveChatMessageToolCall \} from '\$lib\/apis\/chats';/g,
    "",
  );
  adapted = adapted.replace(/import \{ toast \} from 'svelte-sonner';/g, "");
  if (name === "StructuredOutputRenderer.svelte") {
    adapted = adapted.replace(
      '<script lang="ts">',
      "<script lang=\"ts\">\n import { getContext } from 'svelte';",
    );
    adapted = adapted.replace(
      "let resolvingCallId = '';",
      "export let onResolve: (callId: string, approved: boolean) => unknown = () => {};\n let resolvingCallId = '';\n let actionError = '';",
    );
    adapted = adapted.replace(
      /const res = await resolveChatMessageToolCall\([\s\S]*?\n\s*\);/,
      "const res = await onResolve(callId, approved);",
    );
    adapted = adapted.replace(
      "toast.error(String(err));",
      "actionError = err instanceof Error ? err.message : String(err);",
    );
    adapted = adapted.replace(
      "</script>",
      '</script>\n{#if actionError}<p role="alert">{actionError}</p>{/if}',
    );
  }
  if (name === "Collapsible.svelte") {
    adapted = adapted.replace(
      /import \{ v4 as uuidv4 \} from 'uuid';/,
      "const uuidv4 = () => crypto.randomUUID();",
    );
    adapted = adapted.replace(
      /\s*import dayjs[\s\S]*?dayjs.extend\(relativeTime\);/,
      "\n const duration = (value: number, _unit?: string) => ({ locale: (_language: string) => duration(value), humanize: () => `${Math.round(value / 60)}分钟` });\n const dayjs = { duration };",
    );
    adapted = adapted.replace(
      "export let attributes = null;",
      "export let attributes: Record<string, any> | null = null;",
    );
  }
  if (name === "ConsecutiveDetailsGroup.svelte")
    adapted = adapted.replace(
      /function parseJSONString\(str: string\) \{[\s\S]*?\n\t\}/,
      "function parseJSONString(str: string) {\n let value: unknown = str;\n while (typeof value === 'string') { try { const next = JSON.parse(value); if (next === value) break; value = next; } catch { break; } }\n return value;\n }",
    );
  await mkdir(path.dirname(target), { recursive: true });
  await writeFile(
    target,
    name.endsWith(".svelte")
      ? `<!-- 来源OWUI0.12.3；本地上下文与平台回调适配，详见SOURCE_MANIFEST。 -->\n${adapted}`
      : `/** 来源OWUI0.12.3；保留原结构化输出算法。 */\n${adapted}`,
  );
  manifest.push({
    source: source,
    target: "src/reused/" + name,
    sha256: createHash("sha256").update(original).digest("hex"),
    kind: "source_with_platform_adapters",
  });
}
for (const source of [
  "src/lib/components/chat/Messages.svelte",
  "src/lib/components/chat/Messages/Message.svelte",
  "src/lib/components/chat/Messages/UserMessage.svelte",
  "src/lib/components/chat/Messages/ResponseMessage.svelte",
  "src/lib/components/chat/Messages/Markdown.svelte",
  "src/lib/components/chat/MessageInput.svelte",
  "src/lib/components/chat/Chat.svelte",
  "src/lib/apis/streaming/index.ts",
  "src/lib/components/common/ToolCallDisplay.svelte",
  "src/tailwind.css",
]) {
  const original = await readFile(path.join(upstream, source));
  manifest.push({
    source,
    sha256: createHash("sha256").update(original).digest("hex"),
    kind: "adapted_presentation_fragment",
    changes:
      "提取原消息树/结构化展示及输入标记样式，平台替代不受支持的原账号、工具、语音与网络依赖；不宣称完整原组件。",
  });
}
await mkdir(path.join(destination, "licenses"), { recursive: true });
await copyFile(
  path.join(upstream, "LICENSE"),
  path.join(destination, "licenses/OpenWebUI-LICENSE.txt"),
);
await writeFile(
  path.join(destination, "SOURCE_MANIFEST.json"),
  JSON.stringify(
    {
      project: "Open WebUI",
      version: "0.12.3",
      source: ".local/native_windows/sources/openwebui",
      files: manifest,
      license: "licenses/OpenWebUI-LICENSE.txt",
    },
    null,
    2,
  ) + "\n",
);
