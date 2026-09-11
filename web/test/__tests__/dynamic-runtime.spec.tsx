import { render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import dynamic from '@/next/dynamic'

it('loads a client-only dynamic component in the test browser', async () => {
  const loader = vi.fn(async () => ({ default: () => <p>Loaded client content</p> }))
  const Component = dynamic(loader, { ssr: false })
  render(<Component />)
  expect(await screen.findByText('Loaded client content')).toBeInTheDocument()
  expect(loader).toHaveBeenCalled()
})

it('renders the real Markdown component without substituting its content', async () => {
  const { Markdown } = await import('@/app/components/base/markdown')
  render(<Markdown content="**Rendered markdown content**" />)
  expect(
    await screen.findByText('Rendered markdown content', {}, { timeout: 10000 }),
  ).toBeInTheDocument()
}, 15000)
