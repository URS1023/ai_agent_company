import type { FileEntity } from '@/app/components/base/file-uploader/types'
import { TransferMethod } from '@/types/app'
import { prepareWorkbenchFiles } from '../input-files'

function file(extra: Partial<FileEntity> = {}): FileEntity {
  return {
    id: 'ui-id',
    name: 'batch.pdf',
    size: 12,
    type: 'application/pdf',
    progress: 100,
    transferMethod: 'local_file',
    supportFileType: 'document',
    uploadedId: '8e7ed5c6-6112-4331-9f9c-b26076083d41',
    ...extra,
  }
}

describe('workbench file input payload', () => {
  it('should submit only native reference fields, never UI IDs or browser data', () => {
    expect(
      prepareWorkbenchFiles([
        file({ base64Url: 'private-preview', originalFile: new File(['x'], 'batch.pdf') }),
      ]),
    ).toEqual({
      status: 'ready',
      files: [
        {
          type: 'document',
          transfer_method: 'local_file',
          upload_file_id: '8e7ed5c6-6112-4331-9f9c-b26076083d41',
          url: '',
        },
      ],
    })
  })
  it.each([0, 30, 99])(
    'should keep all selected files pending while upload progress is %s',
    (progress) => {
      expect(prepareWorkbenchFiles([file(), file({ progress })])).toEqual({ status: 'pending' })
    },
  )
  it.each([-1, 101, Number.NaN])(
    'should reject failed or malformed upload progress %s instead of silently dropping files',
    (progress) => {
      expect(prepareWorkbenchFiles([file({ progress })])).toEqual({ status: 'invalid' })
    },
  )
  it.each(
    [
      { uploadedId: undefined },
      { uploadedId: 'ui-id' },
      { transferMethod: TransferMethod.all },
      { supportFileType: '' },
    ].map((extra) => ({ extra })),
  )('should reject incomplete native references: %j', ({ extra }) => {
    expect(prepareWorkbenchFiles([file(extra)])).toEqual({ status: 'invalid' })
  })
  it('should preserve a completed HTTP remote reference without browser preview data', () => {
    expect(
      prepareWorkbenchFiles([
        file({ transferMethod: 'remote_url', url: 'https://example.com/batch.pdf' }),
      ]),
    ).toEqual({
      status: 'ready',
      files: [
        {
          type: 'document',
          transfer_method: 'remote_url',
          upload_file_id: '8e7ed5c6-6112-4331-9f9c-b26076083d41',
          url: 'https://example.com/batch.pdf',
        },
      ],
    })
  })
  it.each(['', 'blob:preview', 'file:///C:/private', 'javascript:alert(1)'])(
    'should reject a remote reference with a non-HTTP URL: %s',
    (url) => {
      expect(prepareWorkbenchFiles([file({ transferMethod: 'remote_url', url })])).toEqual({
        status: 'invalid',
      })
    },
  )
  it('should represent an empty selection without inventing a file', () => {
    expect(prepareWorkbenchFiles([])).toEqual({ status: 'ready', files: [] })
  })
})
