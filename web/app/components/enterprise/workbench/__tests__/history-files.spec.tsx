import type { MessageFile } from '@dify/contracts/api/console/installed-apps/types.gen'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { WorkbenchHistoryFiles } from '../history-files'

const file = (url?: string | null): MessageFile => ({
  id: 'file-1',
  filename: 'inspection.pdf',
  type: 'document',
  transfer_method: 'local_file',
  mime_type: 'application/pdf',
  size: 1024,
  url,
})

beforeEach(() => vi.clearAllMocks())

// Persisted URLs must be opened unchanged, never reconstructed from file IDs.
describe('WorkbenchHistoryFiles', () => {
  it.each([
    ['image', 'image/png', 'inspection.png'],
    ['audio', 'audio/wav', 'inspection.wav'],
    ['video', 'video/webm', 'inspection.webm'],
  ])(
    'should open %s only on request with its original media type and URL',
    async (type, mime_type, filename) => {
      const user = userEvent.setup()
      const url = '/files/inspection?signature=a%2Bb'
      render(<WorkbenchHistoryFiles files={[{ ...file(url), type, mime_type, filename }]} />)
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
      expect(document.querySelector('img, audio, video')).not.toBeInTheDocument()

      await user.click(screen.getByRole('button', { name: filename }))

      const dialog = screen.getByRole('dialog', { name: filename })
      const media = within(dialog).getByLabelText(filename)
      if (type === 'image') {
        expect(media).toHaveAttribute('src', url)
      } else {
        expect(media.querySelector('source')).toHaveAttribute('src', url)
        expect(media.querySelector('source')).toHaveAttribute('type', mime_type)
        expect(media).toHaveAttribute('controls')
        expect(media).toHaveAttribute('preload', 'metadata')
        expect(media).not.toHaveAttribute('autoplay')
      }
      expect(within(dialog).getByRole('link')).toHaveAttribute('href', url)

      await user.click(within(dialog).getByRole('button', { name: 'common.operation.close' }))
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
      expect(screen.getByRole('button', { name: filename })).toHaveFocus()
    },
  )

  it('should show a readable error and keep the original link if media loading fails', async () => {
    const user = userEvent.setup()
    render(
      <WorkbenchHistoryFiles
        files={[{ ...file('/files/photo'), type: 'image', mime_type: 'image/png' }]}
      />,
    )
    await user.click(screen.getByRole('button', { name: 'inspection.pdf' }))
    fireEvent.error(screen.getByRole('img'))
    expect(screen.getByRole('alert')).toHaveTextContent(
      'common.fileUploader.uploadFromComputerReadError',
    )
    expect(within(screen.getByRole('dialog')).getByRole('link')).toHaveAttribute(
      'href',
      '/files/photo',
    )
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'inspection.pdf' }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('img')).toBeInTheDocument()
  })

  it.each([
    ['image', 'text/html', '/files/photo'],
    ['document', 'image/png', '/files/photo'],
    ['video', null, '/files/video'],
    ['image', 'image/png', 'javascript:alert(1)'],
  ])(
    'should not offer media rendering for inconsistent or unusable metadata',
    (type, mime_type, url) => {
      render(<WorkbenchHistoryFiles files={[{ ...file(url), type, mime_type }]} />)
      expect(screen.queryByRole('button')).not.toBeInTheDocument()
    },
  )

  it.each([
    'https://files.example/report?sign=a%2Bb&expires=123',
    '/files/report?sign=abc',
    'http://192.168.1.2/report',
  ])('should preserve the exact valid attachment URL %s', (url) => {
    render(<WorkbenchHistoryFiles files={[file(url)]} />)
    expect(screen.getByRole('link', { name: /inspection.pdf/ })).toHaveAttribute('href', url)
    expect(screen.getByRole('link')).toHaveAttribute('rel', 'noopener noreferrer')
    expect(screen.getByText('1.00 KB')).toBeInTheDocument()
  })

  it.each([
    null,
    undefined,
    '',
    'javascript:alert(1)',
    'data:text/html,test',
    '//evil.example/file',
    '/\\evil.example/file',
    'https://user:password@example.com/file',
    'https://example.com/\nfile',
    'relative.pdf',
  ])('should retain metadata without a navigable link for %s', (url) => {
    render(<WorkbenchHistoryFiles files={[file(url)]} />)
    expect(screen.getByText('inspection.pdf')).toBeInTheDocument()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })

  it.each(['audio', 'video'])(
    'should preserve the source link when a %s decoder rejects the file',
    async (type) => {
      const user = userEvent.setup()
      render(
        <WorkbenchHistoryFiles
          files={[{ ...file('/files/media'), type, mime_type: `${type}/webm` }]}
        />,
      )
      await user.tab()
      await user.keyboard('{Enter}')
      const media = within(screen.getByRole('dialog')).getByLabelText('inspection.pdf')
      fireEvent.error(media)
      expect(screen.getByRole('alert')).toBeInTheDocument()
      expect(within(screen.getByRole('dialog')).getByRole('link')).toHaveAttribute(
        'href',
        '/files/media',
      )
    },
  )

  it('should not invent a file size when metadata is missing', () => {
    render(<WorkbenchHistoryFiles files={[{ ...file(), size: null }]} />)
    expect(screen.getByRole('listitem')).toHaveTextContent(/^inspection.pdf$/)
  })

  it('should render no list when there are no attachments', () => {
    render(<WorkbenchHistoryFiles files={[]} />)
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
  })
})
