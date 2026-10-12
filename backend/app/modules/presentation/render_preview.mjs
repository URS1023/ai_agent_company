#!/usr/bin/env node

import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

function arg(name, fallback = "") {
  const index = process.argv.indexOf(`--${name}`);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

async function saveBlob(blob, outputPath) {
  const data = blob && typeof blob.arrayBuffer === "function"
    ? Buffer.from(await blob.arrayBuffer())
    : Buffer.from(blob);
  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  await fs.writeFile(outputPath, data);
}

function slidesOf(presentation) {
  if (Array.isArray(presentation.slides?.items)) return presentation.slides.items;
  if (Number.isInteger(presentation.slides?.count) && typeof presentation.slides.getItem === "function") {
    return Array.from({ length: presentation.slides.count }, (_, index) => presentation.slides.getItem(index));
  }
  throw new Error("无法读取演示文稿页面");
}

async function main() {
  const input = path.resolve(arg("input"));
  const output = path.resolve(arg("output"));
  const slideNumber = Math.max(1, Number.parseInt(arg("slide", "1"), 10) || 1);
  const requestedSlides = arg("slides")
    .split(",")
    .map((value) => Number.parseInt(value.trim(), 10))
    .filter((value) => Number.isInteger(value) && value > 0);
  const scale = Math.max(0.1, Number.parseFloat(arg("scale", "0.75")) || 0.75);
  const modulesRoot = process.env.ARTIFACT_TOOL_MODULES || process.env.RUNTIME_NODE_MODULES;
  if (!modulesRoot) throw new Error("缺少 ARTIFACT_TOOL_MODULES");

  const entry = path.join(modulesRoot, "@oai", "artifact-tool", "dist", "artifact_tool.mjs");
  const { FileBlob, PresentationFile } = await import(pathToFileURL(entry).href);
  const presentation = await PresentationFile.importPptx(await FileBlob.load(input));
  const slides = slidesOf(presentation);
  if (!slides.length) throw new Error("演示文稿没有页面");
  if (requestedSlides.length) {
    await fs.mkdir(output, { recursive: true });
    const paths = [];
    for (const requested of requestedSlides) {
      const actual = Math.min(slides.length, requested);
      const outputPath = path.join(output, `slide-${actual}.png`);
      const preview = await presentation.export({ slide: slides[actual - 1], format: "png", scale });
      await saveBlob(preview, outputPath);
      paths.push(outputPath);
    }
    console.log(JSON.stringify({ output, slideNumbers: requestedSlides, paths }));
    return;
  }
  const selected = slides[Math.min(slides.length, slideNumber) - 1];
  const preview = await presentation.export({ slide: selected, format: "png", scale });
  await saveBlob(preview, output);
  console.log(JSON.stringify({ output, slideNumber: Math.min(slides.length, slideNumber) }));
}

main().catch((error) => {
  console.error(error.stack || error.message || String(error));
  process.exit(1);
});
