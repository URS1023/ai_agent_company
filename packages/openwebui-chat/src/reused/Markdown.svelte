<script lang="ts">
  import DOMPurify from "dompurify";
  import { marked } from "marked";
  export let content = "";
  export let id = "";
  export let chatId = "";
  export let messageId = "";
  export let done = true;
  export let model: any = null;
  export let save = false;
  export let preview = false;
  export let compactPreview = false;
  export let editCodeBlock = false;
  export let topPadding = false;
  export let allowEmbeds = false;
  export let sourceIds: string[] = [];
  export let onSourceClick: any = () => {};
  export let onTaskClick: any = () => {};
  export let onToolCallResolved: any = () => {};
  export let onSave: any = () => {};
  export let onUpdate: any = () => {};
  export let onPreview: any = () => {};
  // 保留原marked分段显示；首批仅文本Markdown，禁止模型内容暗中加载远程文件/iframe。
  $: html = DOMPurify.sanitize(
    marked.parse(content ?? "", {
      gfm: true,
      breaks: true,
      async: false,
    }) as string,
    {
      FORBID_TAGS: [
        "script",
        "style",
        "img",
        "iframe",
        "object",
        "embed",
        "audio",
        "video",
        "form",
        "input",
        "button",
      ],
      FORBID_ATTR: ["style", "src", "srcset"],
    },
  );
</script>

<div {id} class="markdown-prose marked" dir="auto">{@html html}</div>

<style>
  .marked {
    overflow-wrap: anywhere;
    line-height: 1.75;
  }
  .marked :global(p) {
    margin: 0.45em 0;
  }
  .marked :global(pre) {
    overflow: auto;
    white-space: pre;
    border: 1px solid #e5e7eb;
    background: #f8fafc;
    padding: 12px;
    border-radius: 12px;
    font-size: 13px;
  }
  .marked :global(code) {
    font-family: Consolas, monospace;
  }
  .marked :global(table) {
    border-collapse: collapse;
    display: block;
    overflow: auto;
    max-width: 100%;
  }
  .marked :global(th),
  .marked :global(td) {
    border: 1px solid #e5e7eb;
    padding: 6px 10px;
  }
  .marked :global(a) {
    color: #6354d8;
    text-decoration: underline;
  }
  .marked :global(ul),
  .marked :global(ol) {
    padding-left: 1.5rem;
    list-style: revert;
  }
  .marked :global(blockquote) {
    border-left: 3px solid #d8d1fc;
    padding-left: 12px;
    color: #667085;
  }
  .marked :global(h1),
  .marked :global(h2),
  .marked :global(h3) {
    font-weight: 600;
    margin: 0.8em 0 0.4em;
    font-size: 1.15em;
  }
</style>
