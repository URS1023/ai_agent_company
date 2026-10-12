/*
 * 中文说明（知识库上传队列的纯前端单元测试）：
 * 验证行为：createDocumentUploadQueue（apps/web/assets/platform/knowledge-upload.js）对同文件去重、
 *   顺序与状态推进的契约。
 * 运行方式：node --test tests/ui/knowledge_upload_queue.test.mjs（纯 Node，无浏览器/后端）。
 * 边界：只测队列逻辑，不真正上传文件。
 */
import test from "node:test";
import assert from "node:assert/strict";
import { createDocumentUploadQueue } from "../../apps/web/assets/platform/knowledge-upload.js";

const file = name => ({ name, size: 123, lastModified: 10 });

test("partial failures continue; retry reuses uploaded IDs and skips successful documents", async () => {
  const uploads = [], parses = [];
  let fail = true;
  const queue = createDocumentUploadQueue({
    upload: async item => { uploads.push(item.name); return item.name; },
    parse: async (id, settings) => {
      parses.push([id, settings]);
      if (id === "broken.pdf" && fail) throw new Error("PDF 格式不正确");
    },
  });
  queue.add([file("first.txt"), file("broken.pdf"), file("last.txt")]);
  const settings = { chunk_size: 500, overlap: 50 };
  assert.deepEqual(await queue.run(settings), { total: 3, ready: 2, failed: 1 });
  assert.equal(queue.items[1].fileId, "broken.pdf");
  assert.match(queue.items[1].error, /文件已保存在文件库/);
  fail = false;
  assert.deepEqual(await queue.run(settings), { total: 3, ready: 3, failed: 0 });
  assert.deepEqual(uploads, ["first.txt", "broken.pdf", "last.txt"]);
  assert.deepEqual(parses.map(item => item[0]), ["first.txt", "broken.pdf", "last.txt", "broken.pdf"]);
  assert(parses.every(item => item[1] === settings));
});

test("additional selections accumulate, duplicate selections do not duplicate upload, removal preserves retry ID", async () => {
  let calls = 0;
  const queue = createDocumentUploadQueue({ upload: async () => String(++calls), parse: async () => { throw new Error("parse"); } });
  queue.add([file("one.txt")]);
  queue.add([file("one.txt"), file("two.txt")]);
  assert.equal(queue.items.length, 2);
  queue.remove(queue.items[1].key);
  await queue.run({});
  queue.remove(queue.items[0].key);
  queue.add([file("one.txt")]);
  await queue.run({});
  assert.equal(calls, 1);
});

test("upload errors are isolated and retried; empty and overlapping runs cannot start", async () => {
  let uploads = 0, release;
  const queue = createDocumentUploadQueue({
    upload: async () => { if (++uploads === 1) throw new Error("网络中断"); return "id"; },
    parse: () => new Promise(resolve => { release = resolve; }),
  });
  await assert.rejects(queue.run({}), /请选择/);
  queue.add([file("one.txt")]);
  assert.equal((await queue.run({})).failed, 1);
  assert.match(queue.items[0].error, /上传失败/);
  const retry = queue.run({});
  await Promise.resolve();
  await assert.rejects(queue.run({}), /正在处理/);
  queue.remove(queue.items[0].key);
  assert.equal(queue.items.length, 1);
  release();
  assert.equal((await retry).ready, 1);
  assert.equal(uploads, 2);
});
