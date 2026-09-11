import { restoreWorkbenchInputFiles } from '../input-file-defaults'
import { decodeWorkbenchInputs } from '../input-schema'
import { prepareWorkbenchInputs } from '../input-submit'

const id = '00000000-0000-4000-8000-000000000001'
const existing = {
  filename: 'batch.pdf',
  size: 42,
  mime_type: 'application/pdf',
  related_id: id,
  transfer_method: 'local_file',
  type: 'document',
}
function fields(value: unknown, type = 'file') {
  return decodeWorkbenchInputs([
    { [type]: { variable: 'report', label: 'Report', default: value } },
  ])
}
describe('workbench native file defaults', () => {
  it('should restore persisted file metadata without inventing display metadata or using preview URLs', () => {
    const restored = restoreWorkbenchInputFiles(fields(existing))
    expect(restored.report).toEqual([
      {
        id,
        uploadedId: id,
        name: 'batch.pdf',
        size: 42,
        type: 'application/pdf',
        supportFileType: 'document',
        transferMethod: 'local_file',
        progress: 100,
      },
    ])
    expect(prepareWorkbenchInputs(fields(existing), { report: existing }, restored)).toMatchObject({
      status: 'ready',
      inputs: { report: { upload_file_id: id } },
    })
  })
  it('should restore ordered file lists and preserve real remote references', () => {
    const remote = {
      ...existing,
      related_id: '00000000-0000-4000-8000-000000000002',
      transfer_method: 'remote_url',
      remote_url: 'https://example.com/batch.pdf',
    }
    const restored = restoreWorkbenchInputFiles(fields([existing, remote], 'file-list'))
    expect(restored.report?.map((file) => file.uploadedId)).toEqual([id, remote.related_id])
    expect(restored.report?.[1]?.url).toBe(remote.remote_url)
  })
  it.each([
    { upload_file_id: id },
    { ...existing, size: undefined },
    { ...existing, filename: '' },
    { ...existing, transfer_method: 'remote_url', url: 'file:///private' },
  ])('should reject incomplete or unusable persisted metadata: %j', (value) => {
    expect(() => restoreWorkbenchInputFiles(fields(value))).toThrow(
      'Invalid workbench input configuration',
    )
  })
  it('should not invent defaults for absent or empty optional files', () => {
    expect(restoreWorkbenchInputFiles(fields(null))).toEqual({})
    expect(restoreWorkbenchInputFiles(fields([], 'file-list'))).toEqual({ report: [] })
  })
})
