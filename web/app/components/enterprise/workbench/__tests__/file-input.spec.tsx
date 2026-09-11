import type { UploadConfig } from '@dify/contracts/api/console/files/types.gen'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { WorkbenchFileInput } from '../file-input'
import { decodeWorkbenchInputs } from '../input-schema'

vi.mock('@/service/base', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/service/base')>()),
  upload: vi.fn().mockResolvedValue({ id: '8e7ed5c6-6112-4331-9f9c-b26076083d41' }),
}))

vi.mock('@/next/navigation', () => ({
  useParams: () => ({}),
  usePathname: () => '/enterprise/workbench',
}))

const limits: UploadConfig = {
  attachment_image_file_size_limit: 2,
  audio_file_size_limit: 50,
  batch_count_limit: 5,
  file_size_limit: 15,
  file_upload_limit: 5,
  image_file_batch_limit: 10,
  image_file_size_limit: 10,
  single_chunk_attachment_limit: 10,
  video_file_size_limit: 100,
  workflow_file_upload_limit: 10,
}
function field(hidden = false) {
  const result = decodeWorkbenchInputs([
    {
      file: {
        variable: 'report',
        label: 'Report',
        hide: hidden,
        allowed_file_types: ['custom'],
        allowed_file_upload_methods: ['local_file'],
        allowed_file_extensions: ['.pdf'],
      },
    },
  ])[0]
  if (!result) throw new Error('Missing field')
  return result
}

describe('workbench file parameter', () => {
  it('should send upload progress and completion to the latest callback using the real uploader', async () => {
    const first = vi.fn()
    const latest = vi.fn()
    const { rerender, container } = render(
      <WorkbenchFileInput field={field()} uploadConfig={limits} onChange={first} />,
    )
    rerender(<WorkbenchFileInput field={field()} uploadConfig={limits} onChange={latest} />)
    const input = container.querySelector('input[type="file"]')
    if (!input) throw new Error('Missing picker')
    fireEvent.change(input, {
      target: { files: [new File(['report'], 'report.pdf', { type: 'application/pdf' })] },
    })
    await waitFor(() =>
      expect(latest).toHaveBeenCalledWith([
        expect.objectContaining({
          progress: 100,
          uploadedId: '8e7ed5c6-6112-4331-9f9c-b26076083d41',
        }),
      ]),
    )
    expect(latest).toHaveBeenCalledWith([expect.objectContaining({ progress: 0 })])
    expect(first).not.toHaveBeenCalled()
    expect(input).toBeDisabled()
  })
  it('should render the native local picker with the configured label and extensions', () => {
    const { container } = render(
      <WorkbenchFileInput field={field()} uploadConfig={limits} onChange={vi.fn()} />,
    )
    expect(screen.getByRole('group', { name: 'Report' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /uploadFromComputer/ })).toBeInTheDocument()
    expect(container.querySelector('input[type="file"]')).toHaveAttribute(
      'accept',
      expect.stringContaining('.pdf'),
    )
  })
  it('should hide file selection when disabled and render nothing for hidden parameters', () => {
    const { rerender, container } = render(
      <WorkbenchFileInput field={field()} uploadConfig={limits} disabled onChange={vi.fn()} />,
    )
    expect(screen.queryByRole('button', { name: /uploadFromComputer/ })).not.toBeInTheDocument()
    rerender(<WorkbenchFileInput field={field(true)} uploadConfig={limits} onChange={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()
  })
})
