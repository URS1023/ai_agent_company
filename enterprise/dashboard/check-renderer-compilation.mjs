import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { isAbsolute, join } from 'node:path'

const dependencyRoot = process.argv[2]
assert.ok(
  dependencyRoot && isAbsolute(dependencyRoot),
  'Pass the existing frontend dependency directory',
)
const require = createRequire(join(dependencyRoot, 'package.json'))
const compiler = require('@vue/compiler-sfc')
const hash = (text) => createHash('sha256').update(text).digest('hex')
const root = new URL('./lynx-renderer/', import.meta.url)
const manifest = JSON.parse(await readFile(new URL('manifest.json', root), 'utf8'))
const report = {
  checkedAt: new Date().toISOString(),
  scope: 'Vue SFC compilation only; no bundling, browser rendering, network, SQL or data execution',
  compilerVersion: require('@vue/compiler-sfc/package.json').version,
  vueVersion: require('vue/package.json').version,
  echartsVersion: require('echarts/package.json').version,
  components: [],
}
for (const file of manifest.files.filter((file) => file.path.endsWith('.vue'))) {
  const source = await readFile(new URL(file.path, root), 'utf8')
  assert.equal(hash(source), file.sha256)
  const id = file.sha256.slice(0, 12)
  const parsed = compiler.parse(source, { filename: file.path })
  assert.deepEqual(parsed.errors, [], file.path)
  const { descriptor } = parsed
  assert.ok(descriptor.template)
  const script = compiler.compileScript(descriptor, { id })
  const template = compiler.compileTemplate({
    id,
    filename: file.path,
    source: descriptor.template.content,
    scoped: descriptor.styles.some((style) => style.scoped),
    compilerOptions: { bindingMetadata: script.bindings },
  })
  assert.deepEqual(template.errors, [], file.path)
  const styles = descriptor.styles.map((style) => {
    const result = compiler.compileStyle({
      id: `data-v-${id}`,
      filename: file.path,
      source: style.content,
      scoped: style.scoped,
    })
    assert.deepEqual(result.errors, [], file.path)
    return hash(result.code)
  })
  report.components.push({
    path: file.path,
    sourceSha256: file.sha256,
    scriptSha256: hash(script.content),
    templateSha256: hash(template.code),
    styleSha256: styles,
  })
}
const output = new URL('../artifacts/dashboard-renderer-compilation/', import.meta.url)
await mkdir(output, { recursive: true })
await writeFile(new URL('verification.json', output), `${JSON.stringify(report, null, 2)}\n`)
console.log(
  `Compiled script, template and scoped styles for ${report.components.length} original Vue components. Browser acceptance remains separate.`,
)
