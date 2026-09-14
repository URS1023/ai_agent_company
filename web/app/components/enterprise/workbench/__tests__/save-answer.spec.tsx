import type { contract } from '@enterprise/business-contracts/orpc'
import type { ContractRouterClient } from '@orpc/contract'
import { zOfficeFileView } from '@enterprise/business-contracts/zod'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SaveAnswerDocument } from '../save-answer'

type Client = ContractRouterClient<typeof contract>
const api = vi.hoisted(() => ({
  create: vi.fn<Client['office']['files']['post']>(),
  download: vi.fn(),
  saveBlob: vi.fn(),
}))
vi.mock('@/service/enterprise-business/office-download', () => ({
  fetchOfficeDocument: api.download,
}))
vi.mock('@/utils/download', () => ({ downloadBlob: api.saveBlob }))
vi.mock('@/service/client', () => ({
  consoleClient: { business: { office: { files: { post: api.create } } } },
}))

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((complete) => {
    resolve = complete
  })
  return { promise, resolve }
}

function mount() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <SaveAnswerDocument messageId="message" answer="Report" actor="actor" workspace="workspace" />
    </QueryClientProvider>,
  )
}

describe('Save answer as document', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.download.mockResolvedValue({
      blob: new Blob(['verified transport fixture']),
      filename: 'answer.docx',
    })
    api.create.mockImplementation(async ({ body }) =>
      zOfficeFileView.parse({ ...body, revision: '1' }),
    )
  })

  it('should save once and show success only for the matching document', async () => {
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.save DOCX' }))
    expect(await screen.findByText('common.api.saved')).toBeInTheDocument()
    expect(api.create).toHaveBeenCalledOnce()
    expect(screen.getByRole('button', { name: 'common.operation.save DOCX' })).toBeDisabled()
  })

  it('should retry the exact request after an uncertain response', async () => {
    api.create.mockRejectedValueOnce(new Error('Connection lost'))
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.save DOCX' }))
    await screen.findByRole('alert')
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.retry' }))
    await waitFor(() => expect(api.create).toHaveBeenCalledTimes(2))
    expect(api.create.mock.calls[1]?.[0]).toEqual(api.create.mock.calls[0]?.[0])
    expect(await screen.findByText('common.api.saved')).toBeInTheDocument()
  })

  it('should replay the original request after navigation following an uncertain response', async () => {
    api.create.mockRejectedValueOnce(new Error('Connection lost after commit'))
    const view = mount()
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.save DOCX' }))
    await screen.findByRole('alert')
    view.unmount()
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.save DOCX' }))
    await screen.findByText('common.api.saved')
    expect(api.create.mock.calls[1]?.[0]).toEqual(api.create.mock.calls[0]?.[0])
  })

  it('should reject a response for another file', async () => {
    api.create.mockImplementation(async ({ body }) =>
      zOfficeFileView.parse({ ...body, revision: '1', file_id: crypto.randomUUID() }),
    )
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.save DOCX' }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.queryByText('common.api.saved')).not.toBeInTheDocument()
  })

  it('should download the saved exact revision without another create request', async () => {
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.save DOCX' }))
    await userEvent.click(await screen.findByRole('button', { name: 'common.operation.download' }))
    await waitFor(() => expect(api.saveBlob).toHaveBeenCalledOnce())
    expect(api.download.mock.calls[0]?.[0]).toEqual({
      path: { file_id: api.create.mock.calls[0]?.[0].body.file_id },
      query: { expected_revision: '1' },
    })
    expect(api.saveBlob.mock.calls[0]?.[0].fileName).toBe('answer.docx')
    expect(api.create).toHaveBeenCalledOnce()
  })

  it('should retry a failed download without recreating the document', async () => {
    api.download.mockRejectedValueOnce(new Error('Offline'))
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.save DOCX' }))
    await userEvent.click(await screen.findByRole('button', { name: 'common.operation.download' }))
    await screen.findByRole('alert')
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.download' }))
    await waitFor(() => expect(api.saveBlob).toHaveBeenCalledOnce())
    expect(api.download).toHaveBeenCalledTimes(2)
    expect(api.create).toHaveBeenCalledOnce()
  })

  it('should cancel an old-scope download before saving its late response', async () => {
    const pending = deferred<{ blob: Blob; filename: string }>()
    api.download.mockReturnValueOnce(pending.promise)
    const view = mount()
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.save DOCX' }))
    await userEvent.click(await screen.findByRole('button', { name: 'common.operation.download' }))
    const signal = api.download.mock.calls[0]?.[1].signal
    view.rerender(
      <QueryClientProvider client={new QueryClient()}>
        <SaveAnswerDocument
          messageId="message"
          answer="Report"
          actor="other"
          workspace="workspace"
        />
      </QueryClientProvider>,
    )
    expect(signal.aborted).toBe(true)
    await act(async () => pending.resolve({ blob: new Blob(['old scope']), filename: 'old.docx' }))
    expect(api.saveBlob).not.toHaveBeenCalled()
  })

  it('should ignore a late creation response after switching workspace', async () => {
    const pending = deferred<Awaited<ReturnType<Client['office']['files']['post']>>>()
    api.create.mockReturnValueOnce(pending.promise)
    const view = mount()
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.save DOCX' }))
    const original = api.create.mock.calls[0]?.[0].body
    view.rerender(
      <QueryClientProvider client={new QueryClient()}>
        <SaveAnswerDocument messageId="message" answer="Report" actor="actor" workspace="other" />
      </QueryClientProvider>,
    )
    await act(async () => pending.resolve(zOfficeFileView.parse({ ...original, revision: '1' })))
    expect(screen.queryByText('common.api.saved')).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'common.operation.download' }),
    ).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'common.operation.save DOCX' })).toBeEnabled()
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.save DOCX' }))
    await screen.findByText('common.api.saved')
    expect(api.create.mock.calls[1]?.[0].body.file_id).not.toBe(original?.file_id)
  })

  it.each([
    { actor: 'other', workspace: 'workspace' },
    { actor: 'actor', workspace: 'other' },
  ])('should reset the saved state when scope changes: %o', async (scope) => {
    const view = mount()
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.save DOCX' }))
    await screen.findByText('common.api.saved')
    view.rerender(
      <QueryClientProvider client={new QueryClient()}>
        <SaveAnswerDocument messageId="message" answer="Report" {...scope} />
      </QueryClientProvider>,
    )
    expect(screen.queryByText('common.api.saved')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.save DOCX' }))
    await screen.findByText('common.api.saved')
    expect(api.create.mock.calls[1]?.[0].body.file_id).not.toBe(
      api.create.mock.calls[0]?.[0].body.file_id,
    )
  })
})
