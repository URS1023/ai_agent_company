import { mkdirSync, writeFileSync } from 'node:fs'
import { buildDefaultWorkflow } from './default-workflows.mjs'

// JSON is a YAML subset accepted by Dify's native DSL importer.
for (const scenario of ['alert', 'quality']) {
  const runtime = new URL('../api/src/enterprise_platform/workflow_templates/', import.meta.url)
  mkdirSync(runtime, { recursive: true })
  for (const target of [
    new URL(`./default-${scenario}.yml`, import.meta.url),
    new URL(`default-${scenario}.yml`, runtime),
  ]) {
    writeFileSync(target, `${JSON.stringify(buildDefaultWorkflow(scenario), null, 2)}\n`, 'utf8')
  }
}
