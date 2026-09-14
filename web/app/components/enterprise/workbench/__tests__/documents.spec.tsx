import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { OfficeDocuments } from '../documents'

const api = vi.hoisted(() => ({ list: vi.fn(), download: vi.fn(), save: vi.fn() }))
vi.mock('@/service/client', () => ({
  consoleQuery: {
    business: {
      office: {
        files: {
          get: {
            queryOptions: (options: { input: unknown }) => ({
              ...options,
              queryFn: () => api.list(options.input),
            }),
          },
        },
      },
    },
  },
}))
vi.mock('@/service/enterprise-business/office-download', () => ({
  fetchOfficeDocument: api.download,
}))
vi.mock('@/utils/download', () => ({ downloadBlob: api.save }))
const file = {
  file_id: '00000000-0000-4000-8000-000000000001',
  revision: '9007199254740993',
  kind: 'document',
  template_id: 'document-default',
  template_revision: '1',
}
function mount(actor = 'actor') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(
    <QueryClientProvider client={client}>
      <OfficeDocuments actor={actor} workspace="workspace" />
    </QueryClientProvider>,
  )
  return { ...view, client }
}

// Real query/component state; external HTTP and browser file saving are substituted.
describe('Office documents', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.list.mockResolvedValue({ items: [file], next_offset: null })
    api.download.mockResolvedValue({ blob: new Blob(['bytes']), filename: 'report.docx' })
  })

  it('should download the selected exact revision and use the transport filename', async () => {
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'common.operation.download' }))
    await waitFor(() => expect(api.save).toHaveBeenCalledOnce())
    expect(api.download.mock.calls[0]?.[0]).toEqual({
      path: { file_id: file.file_id },
      query: { expected_revision: file.revision },
    })
    expect(api.save.mock.calls[0]?.[0].fileName).toBe('report.docx')
  })

  it('should continue pagination even when every scanned file is filtered out', async () => {
    api.list
      .mockResolvedValueOnce({ items: [], next_offset: 50 })
      .mockResolvedValue({ items: [file], next_offset: null })
    mount()
    await screen.findByText('common.noData')
    fireEvent.click(screen.getByRole('button', { name: 'common.pagination.next' }))
    await screen.findByRole('button', { name: 'common.operation.download' })
    expect(api.list.mock.calls[1]?.[0]).toEqual({ query: { offset: 50 } })
  })

  it('should show download failure without saving a fabricated file', async () => {
    api.download.mockRejectedValue(new Error('private backend detail'))
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'common.operation.download' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('common.operation.downloadFailed')
    expect(screen.queryByText('private backend detail')).not.toBeInTheDocument()
    expect(api.save).not.toHaveBeenCalled()
  })

  it('should not offer a Word download for a presentation', async () => {
    api.list.mockResolvedValue({ items: [{ ...file, kind: 'presentation' }], next_offset: null })
    mount()
    await screen.findByText(file.file_id)
    expect(
      screen.queryByRole('button', { name: 'common.operation.download' }),
    ).not.toBeInTheDocument()
  })

  it.each([
    { actor: 'other', workspace: 'workspace' },
    { actor: 'actor', workspace: 'other-workspace' },
  ])(
    'should cancel an outstanding download when scope changes to $actor/$workspace',
    async (scope) => {
      let finish: ((value: { blob: Blob; filename: string }) => void) | undefined
      api.download.mockImplementation(
        () =>
          new Promise((resolve) => {
            finish = resolve
          }),
      )
      const { client, rerender } = mount()
      fireEvent.click(await screen.findByRole('button', { name: 'common.operation.download' }))
      await waitFor(() => expect(api.download).toHaveBeenCalledOnce())
      api.list.mockResolvedValue({ items: [], next_offset: null })
      rerender(
        <QueryClientProvider client={client}>
          <OfficeDocuments {...scope} />
        </QueryClientProvider>,
      )
      expect(api.download.mock.calls[0]?.[1].signal.aborted).toBe(true)
      finish?.({ blob: new Blob(['old']), filename: 'old.docx' })
      await screen.findByText('common.noData')
      expect(api.save).not.toHaveBeenCalled()
    },
  )

  it.each([
    { items: [file, file], next_offset: null },
    { items: [file], next_offset: 0 },
    { items: [file], next_offset: 51 },
    { items: [file], next_offset: 1.5 },
    { items: [{ ...file, file_id: 'invalid' }], next_offset: null },
  ])('should reject malformed directory page %# without displaying files', async (response) => {
    api.list.mockResolvedValue(response)
    mount()
    expect(await screen.findByRole('alert')).toHaveTextContent('common.enterprise.loadError')
    expect(screen.queryByText(file.file_id)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'common.pagination.next' })).toBeDisabled()
  })

  it('should retry directory failures explicitly without exposing backend details', async () => {
    api.list.mockRejectedValueOnce(new Error('private database detail'))
    mount()
    expect(await screen.findByRole('alert')).toHaveTextContent('common.enterprise.loadError')
    expect(screen.queryByText('private database detail')).not.toBeInTheDocument()
    expect(api.list).toHaveBeenCalledOnce()
    fireEvent.click(screen.getByRole('button', { name: 'common.operation.retry' }))
    expect(await screen.findByText(file.file_id)).toBeInTheDocument()
    expect(api.list).toHaveBeenCalledTimes(2)
  })
})
