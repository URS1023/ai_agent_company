import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, isAbsolute, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const source = process.argv[2]
assert.ok(source && isAbsolute(source), 'Pass the absolute existing Lynx repository directory')
const target = fileURLToPath(new URL('./lynx-renderer/', import.meta.url))
const sources = [
  ['LICENSE', 'LICENSE'],
  ...['JimuCanvas', 'JimuChart', 'JimuWidgetRenderer'].map((name) => [
    `lynx-ai-ui/src/components/jimu-screen/${name}.vue`,
    `src/components/${name}.vue`,
  ]),
  ...['dashboardTheme', 'dashboardData', 'aiChartOption'].map((name) => [
    `lynx-ai-ui/src/utils/${name}.js`,
    `src/utils/${name}.js`,
  ]),
  ['lynx-ai-ui/public/jimu-screen/china.json', 'public/china.json'],
]
async function writeExact(path, bytes) {
  await mkdir(dirname(path), { recursive: true })
  try {
    await writeFile(path, bytes, { flag: 'wx' })
  } catch (error) {
    if (error.code !== 'EEXIST') throw error
    assert.deepEqual(await readFile(path), bytes, `Existing source differs: ${path}`)
  }
}
const files = []
for (const [sourcePath, path] of sources) {
  const bytes = await readFile(join(source, sourcePath))
  await writeExact(join(target, path), bytes)
  files.push({
    path,
    sourcePath,
    bytes: bytes.length,
    sha256: createHash('sha256').update(bytes).digest('hex'),
  })
}
const git = (args) => execFileSync('git', ['-C', source, ...args], { encoding: 'utf8' }).trim()
const manifest = {
  schemaVersion: 1,
  kind: 'renderer-source-only',
  sourceCommit: git(['rev-parse', 'HEAD']),
  sourceWorktreeChanges: git(['status', '--porcelain', '--', ...sources.map(([path]) => path)])
    .split('\n')
    .filter(Boolean),
  externalModules: ['@/api/request', 'echarts', 'vue'],
  files,
}
await writeExact(
  join(target, 'manifest.json'),
  Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`),
)
console.log(
  `Verified ${files.length} renderer assets. Original network and sample-data behavior is not enabled in the enterprise app.`,
)
