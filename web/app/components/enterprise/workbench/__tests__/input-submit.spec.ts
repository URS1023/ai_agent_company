import type { FileEntity } from '@/app/components/base/file-uploader/types'
import { decodeWorkbenchInputs, workbenchInputDefaults } from '../input-schema'
import { prepareWorkbenchInputs } from '../input-submit'

describe('workbench configured input submission', () => {
  it('should count text characters like native validation and preserve numeric strings', () => {
    const fields = decodeWorkbenchInputs([
      { 'text-input': { variable: 'text', label: 'Text', max_length: 1 } },
      { number: { variable: 'count', label: 'Count' } },
    ])
    expect(prepareWorkbenchInputs(fields, { text: '😀', count: '001' }, {})).toEqual({
      status: 'ready',
      inputs: { text: '😀', count: '001' },
    })
    expect(prepareWorkbenchInputs(fields, { text: 'ab', count: '1e3' }, {})).toEqual({
      status: 'invalid',
      errors: { text: 'invalid', count: 'invalid' },
    })
  })
  it('should detach submitted objects from form state and safely handle prototype-like keys', () => {
    const fields = decodeWorkbenchInputs([
      { json_object: { variable: '__proto__', label: 'Object' } },
    ])
    const values = Object.fromEntries([['__proto__', { batch: '001' }]])
    const result = prepareWorkbenchInputs(fields, values, {})
    if (result.status !== 'ready') throw new Error('Expected ready inputs')
    expect(Object.hasOwn(result.inputs, '__proto__')).toBe(true)
    expect(Object.getOwnPropertyDescriptor(result.inputs, '__proto__')?.value).not.toBe(
      Object.getOwnPropertyDescriptor(values, '__proto__')?.value,
    )
    expect(Object.getPrototypeOf(result.inputs)).toBe(Object.prototype)
  })
  it('should preserve hidden false and zero defaults and omit unrelated draft keys', () => {
    const fields = decodeWorkbenchInputs([
      { checkbox: { variable: 'flag', label: 'Flag', required: true, hide: true, default: false } },
      { number: { variable: 'count', label: 'Count', required: true, default: 0 } },
    ])
    expect(
      prepareWorkbenchInputs(
        fields,
        { ...workbenchInputDefaults(fields), unrelated: 'discard' },
        {},
      ),
    ).toEqual({ status: 'ready', inputs: { flag: false, count: 0 } })
  })
  it('should report all missing required fields without returning a partial payload', () => {
    const fields = decodeWorkbenchInputs([
      { 'text-input': { variable: 'batch', label: 'Batch', required: true } },
      { number: { variable: 'count', label: 'Count', required: true } },
    ])
    expect(prepareWorkbenchInputs(fields, { batch: '', count: null }, {})).toEqual({
      status: 'invalid',
      errors: { batch: 'required', count: 'required' },
    })
  })
  it('should parse valid JSON drafts and reject incomplete edits without borrowing defaults', () => {
    const fields = decodeWorkbenchInputs([
      { json_object: { variable: 'filter', label: 'Filter', default: { a: 1 } } },
    ])
    expect(prepareWorkbenchInputs(fields, { filter: '{"batch":"001"}' }, {})).toEqual({
      status: 'ready',
      inputs: { filter: { batch: '001' } },
    })
    expect(prepareWorkbenchInputs(fields, { filter: '{' }, {})).toEqual({
      status: 'invalid',
      errors: { filter: 'invalid' },
    })
  })
  it('should reject invalid scalar types and out-of-list selection', () => {
    const fields = decodeWorkbenchInputs([
      { checkbox: { variable: 'flag', label: 'Flag' } },
      { select: { variable: 'line', label: 'Line', options: ['01', '02'] } },
      { number: { variable: 'count', label: 'Count' } },
    ])
    expect(
      prepareWorkbenchInputs(fields, { flag: 'false', line: '03', count: 'oops' }, {}),
    ).toEqual({ status: 'invalid', errors: { flag: 'invalid', line: 'invalid', count: 'invalid' } })
  })
  it('should block pending files and enforce single-file cardinality', () => {
    const fields = decodeWorkbenchInputs([
      { file: { variable: 'report', label: 'Report', required: true } },
    ])
    const file: FileEntity = {
      id: 'ui',
      name: 'r.pdf',
      type: 'application/pdf',
      size: 1,
      progress: 50,
      transferMethod: 'local_file',
      supportFileType: 'document',
    }
    expect(prepareWorkbenchInputs(fields, {}, { report: [file] })).toEqual({
      status: 'invalid',
      errors: { report: 'pending' },
    })
    expect(prepareWorkbenchInputs(fields, {}, { report: [] })).toEqual({
      status: 'invalid',
      errors: { report: 'required' },
    })
    const completed = { ...file, progress: 100, uploadedId: '8e7ed5c6-6112-4331-9f9c-b26076083d41' }
    expect(prepareWorkbenchInputs(fields, {}, { report: [completed, completed] })).toEqual({
      status: 'invalid',
      errors: { report: 'invalid' },
    })
    expect(prepareWorkbenchInputs(fields, {}, { report: [completed] })).toEqual({
      status: 'ready',
      inputs: {
        report: {
          type: 'document',
          transfer_method: 'local_file',
          upload_file_id: completed.uploadedId,
          url: '',
        },
      },
    })
  })
  it('should not silently drop unhydrated file defaults', () => {
    const fields = decodeWorkbenchInputs([{ file: { variable: 'report', label: 'Report' } }])
    expect(prepareWorkbenchInputs(fields, { report: { upload_file_id: 'existing' } }, {})).toEqual({
      status: 'invalid',
      errors: { report: 'invalid' },
    })
  })
})
