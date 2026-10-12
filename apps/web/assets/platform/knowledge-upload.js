/**
 * 知识库上传队列模块（「上传 → 解析」两阶段的批处理状态机）
 *
 * 位置：被 knowledge.js 使用，服务于知识库页面的批量文件上传区（拖拽/选择文件后进入队列）。
 * 依赖接口：不直接发请求，upload/parse 由调用方注入（通常分别对应文件上传接口与知识解析接口）。
 * 与 iframe 内 Vue 应用无关。
 *
 * 关键约束：
 * - 上传与解析是两个独立步骤：解析失败时**必须保留已拿到的 fileId**，重试只重跑解析，
 *   否则同一文件会重复落库。错误文案也据此区分“已入库未解析”和“上传失败”。
 * - 队列在 running 期间禁止增删改（add/remove 直接返回），避免正在遍历的数组被修改。
 * - 同一文件以 [name,size,lastModified] 为键去重，并用缓存复用对象，保证失败后重试能命中 fileId。
 */

// Keep the uploaded file ID after a parsing failure so retries do not upload it again.
// 中文补充：创建上传队列。入参 upload(file) 返回 fileId；parse(fileId, settings) 执行解析；
// onChange(items) 在每次状态变更时回调（用于重绘列表）。返回 { items, add, remove, run }。
export function createDocumentUploadQueue({ upload, parse, onChange = () => {} }) {
  const items = [];
  const cached = new Map();
  let running = false;
  return {
    items,
    add(files) {
      // running 期间拒绝变更：run() 正在按数组顺序推进，插入/删除会造成漏传或重复解析
      if (running) return;
      for (const file of files) {
        const key = JSON.stringify([file.name, file.size, file.lastModified]);
        if (items.some(item => item.key === key)) continue;
        const item = cached.get(key) || { key, file, fileId: null, status: "pending", error: "" };
        cached.set(key, item);
        items.push(item);
      }
      onChange(items);
    },
    remove(key) {
      if (running) return;
      const index = items.findIndex(item => item.key === key);
      if (index !== -1) items.splice(index, 1);
      onChange(items);
    },
    async run(settings) {
      if (running) throw new Error("文件正在处理中，请等待完成。");
      if (!items.length) throw new Error("请选择要上传的文件。");
      running = true;
      try {
        for (const item of items) {
          if (item.status === "ready") continue;
          item.error = "";
          try {
            // 已有 fileId 说明上次失败在解析阶段，直接跳过上传，避免同一文件在文件库重复落库
            if (!item.fileId) {
              item.status = "uploading";
              onChange(items);
              item.fileId = await upload(item.file);
            }
            item.status = "parsing";
            onChange(items);
            await parse(item.fileId, settings);
            item.status = "ready";
          } catch (error) {
            item.status = "failed";
            const message = String(error.message || "请稍后重试").replace(/[。.!\s]+$/, "");
            // 文案按 fileId 是否存在区分：已入库未解析 vs 上传失败，直接决定用户该“重试”还是“移除后重传”
            item.error = item.fileId
              ? `文件已保存在文件库，但解析未完成：${message}。重试不会重复上传。`
              : `上传失败：${message}。请重试或移除此文件。`;
          }
          onChange(items);
        }
      } finally {
        running = false;
      }
      return {
        total: items.length,
        ready: items.filter(item => item.status === "ready").length,
        failed: items.filter(item => item.status === "failed").length,
      };
    },
  };
}
