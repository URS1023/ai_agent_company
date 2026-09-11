import { describe, expect, it } from 'vitest'
import { nextStaticImageTestPlugin } from '../next-static-image-test'

function load(projectRoot: string, id: string) {
  const plugin = nextStaticImageTestPlugin({ projectRoot })
  if (typeof plugin.load !== 'function') throw new Error('Expected a load hook')
  return Reflect.apply(plugin.load, {}, [id])
}

describe('Next static images in the test runtime', () => {
  it('normalizes Windows project roots and Vite module separators', () => {
    expect(load('F:\\project\\web\\', 'F:/project/web/assets/icon.svg')).toBe(
      'export default { src: "/__static__/assets/icon.svg" }\n',
    )
  })

  it('handles Vite filesystem URLs on Windows', () => {
    expect(load('F:\\project\\web\\', '/@fs/F:/project/web/assets/icon.svg')).toBe(
      'export default { src: "/__static__/assets/icon.svg" }\n',
    )
  })

  it('retains POSIX asset behavior', () => {
    expect(load('/project/web/', '/project/web/assets/icon.png')).toBe(
      'export default { src: "/__static__/assets/icon.png" }\n',
    )
  })

  it.each([
    '/project/web-other/icon.svg',
    '/project/web/icon.svg?url',
    '/project/web/icon.svg?raw',
  ])('leaves unrelated or explicit URL assets alone: %s', (id) => {
    expect(load('/project/web', id)).toBeNull()
  })
})
