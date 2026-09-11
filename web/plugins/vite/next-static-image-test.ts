import type { Plugin } from 'vite'
import path from 'node:path'
import { normalizeViteModuleId } from './utils.ts'

type NextStaticImageTestPluginOptions = {
  projectRoot: string
}

const STATIC_ASSET_RE = /\.(?:svg|png|jpe?g|gif)$/i
const EXCLUDED_QUERY_RE = /[?&](?:raw|url)\b/

const normalizePath = (value: string) =>
  path.posix
    .normalize(
      normalizeViteModuleId(value)
        .replaceAll('\\', '/')
        .replace(/^\/([a-z]:\/)/i, '$1'),
    )
    .replace(/\/$/, '')

export const nextStaticImageTestPlugin = ({
  projectRoot,
}: NextStaticImageTestPluginOptions): Plugin => {
  const root = normalizePath(projectRoot)
  return {
    name: 'next-static-image-test',
    enforce: 'pre',
    load(id) {
      if (EXCLUDED_QUERY_RE.test(id)) return null

      const cleanId = normalizePath(id)
      if (!cleanId.startsWith(`${root}/`) || !STATIC_ASSET_RE.test(cleanId)) return null

      const relativePath = path.posix.relative(root, cleanId)
      const src = `/__static__/${relativePath}`

      return `export default { src: ${JSON.stringify(src)} }\n`
    },
  }
}
