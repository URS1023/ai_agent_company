import type { UserConfig } from '@hey-api/openapi-ts'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const directory = path.dirname(fileURLToPath(import.meta.url))

const namingSegments = (route: string) => {
  const relative = route.startsWith('/enterprise/api/v1/')
    ? route.slice('/enterprise/api/v1/'.length)
    : route.slice('/enterprise/api/'.length)

  return relative
    .split('/')
    .filter(Boolean)
    .map((segment) => {
      const words = segment.replace(/[{}]/g, '').split('_')
      const camel = words
        .map((word, index) => (index ? `${word.charAt(0).toUpperCase()}${word.slice(1)}` : word))
        .join('')
      return segment.startsWith('{') ? `by${camel.charAt(0).toUpperCase()}${camel.slice(1)}` : camel
    })
}

export default {
  input: path.resolve(directory, '../contracts/business-openapi.json'),
  logs: { file: false },
  output: {
    entryFile: false,
    fileName: { suffix: '.gen' },
    path: path.resolve(directory, '../contracts/generated'),
  },
  plugins: [
    { comments: false, name: '@hey-api/typescript' },
    { name: 'zod' },
    {
      name: 'orpc',
      validator: 'zod',
      contracts: {
        contractName: { casing: 'camelCase', name: '{{name}}' },
        nesting: (operation) => [...namingSegments(operation.path), operation.method.toLowerCase()],
        segmentName: { casing: 'camelCase', name: '{{name}}' },
        strategy: 'single',
      },
    },
  ],
} satisfies UserConfig
