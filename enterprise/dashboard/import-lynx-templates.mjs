import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, isAbsolute, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const sourceRoot = process.argv[2]
assert.ok(
  sourceRoot && isAbsolute(sourceRoot),
  'Pass the absolute existing Lynx repository directory',
)
const target = fileURLToPath(new URL('./lynx/', import.meta.url))
const hash = (bytes) => createHash('sha256').update(bytes).digest('hex')
const sourceCommit = execFileSync('git', ['-C', sourceRoot, 'rev-parse', 'HEAD'], {
  encoding: 'utf8',
}).trim()
assert.match(sourceCommit, /^[a-f0-9]{40}$/)
const sources = [
  ['LICENSE', 'LICENSE'],
  ['lynx-ai-ui/src/data/reportDashboardTemplates.js', 'src/reportDashboardTemplates.js'],
  ['lynx-ai-ui/src/data/reportDashboardShowcases.js', 'src/reportDashboardShowcases.js'],
  ...Array.from({ length: 20 }, (_, index) => {
    const name = `template-${String(index + 1).padStart(2, '0')}.jpg`
    return [`lynx-ai-ui/public/jimu-screen/templates/${name}`, `previews/${name}`]
  }),
]

async function writeNewOrMatch(path, bytes) {
  await mkdir(dirname(path), { recursive: true })
  try {
    await writeFile(path, bytes, { flag: 'wx' })
  } catch (error) {
    if (error.code !== 'EEXIST') throw error
    assert.deepEqual(
      await readFile(path),
      bytes,
      `Existing asset differs: ${path}; review a new snapshot instead of overwriting`,
    )
  }
}

const files = []
for (const [sourcePath, path] of sources) {
  const bytes = await readFile(join(sourceRoot, sourcePath))
  await writeNewOrMatch(join(target, path), bytes)
  files.push({ path, sourcePath, bytes: bytes.length, sha256: hash(bytes) })
}
await writeNewOrMatch(
  join(target, 'package.json'),
  Buffer.from('{"private":true,"type":"module"}\n'),
)
const { DASHBOARD_TEMPLATES, createDashboardTemplateContent } = await import(
  pathToFileURL(join(target, 'src/reportDashboardTemplates.js')).href
)
const manifest = {
  schemaVersion: 1,
  sourceCommit,
  sourceWorktreeChanges: execFileSync(
    'git',
    ['-C', sourceRoot, 'status', '--porcelain', '--', ...sources.map(([path]) => path)],
    { encoding: 'utf8' },
  )
    .trim()
    .split('\n')
    .filter(Boolean),
  files,
  templates: DASHBOARD_TEMPLATES.map((template) => {
    const content = createDashboardTemplateContent(template.id)
    return {
      id: template.id,
      name: template.name,
      code: template.code,
      preview: `previews/${template.preview.split('/').at(-1)}`,
      widgetCount: content.widgets.length,
      contentSha256: hash(JSON.stringify(content)),
    }
  }),
}
await writeNewOrMatch(
  join(target, 'manifest.json'),
  Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`),
)
console.log(
  `Verified ${files.length} imported assets and ${manifest.templates.length} actual template definitions. No renderer is published.`,
)
