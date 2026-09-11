import type { Parameters } from '@dify/contracts/api/console/installed-apps/types.gen'
import { describe, expect, it } from 'vitest'
import { decodeWorkbenchInputs, workbenchInputDefaults } from '../input-schema'

describe('workbench native input schema', () => {
  it('should preserve false, zero, empty text and hidden defaults instead of using truthiness', () => {
    const fields = decodeWorkbenchInputs([
      { number: { variable: 'count', label: 'Count', required: true, default: 0, hide: true } },
      { checkbox: { variable: 'flag', label: 'Flag', default: false } },
      { 'text-input': { variable: 'text', label: 'Text', default: '' } },
    ])
    expect(workbenchInputDefaults(fields)).toEqual({ count: 0, flag: false, text: '' })
    expect(fields[0]).toMatchObject({
      type: 'number',
      variable: 'count',
      required: true,
      hidden: true,
    })
  })
  it('should preserve select, file and JSON schema constraints for their real controls', () => {
    const forms: Parameters['user_input_form'] = [
      { select: { variable: 'line', label: 'Line', options: ['01', '02'], default: '01' } },
      {
        file: {
          variable: 'document',
          label: 'Document',
          allowed_file_types: ['document'],
          allowed_file_upload_methods: ['local_file'],
        },
      },
      {
        'file-list': {
          variable: 'photos',
          label: 'Photos',
          max_length: 3,
          allowed_file_extensions: ['.png'],
        },
      },
      {
        json_object: {
          variable: 'filter',
          label: 'Filter',
          json_schema: '{"type":"object"}',
          default: { batch: '001' },
        },
      },
    ]
    const fields = decodeWorkbenchInputs(forms)
    expect(fields.map((field) => field.type)).toEqual([
      'select',
      'file',
      'file-list',
      'json_object',
    ])
    expect(fields[1]?.definition).toEqual(forms[1]?.file)
    expect(fields[2]?.definition).toEqual(forms[2]?.['file-list'])
    expect(fields[3]?.definition.json_schema).toBe('{"type":"object"}')
    expect(workbenchInputDefaults(fields)).toEqual({ line: '01', filter: { batch: '001' } })
  })
  it('should exclude server-owned external tools without dropping regular fields', () => {
    const fields = decodeWorkbenchInputs([
      { external_data_tool: { variable: 'tool', config: {} } },
      { paragraph: { variable: 'note', label: 'Note' } },
    ])
    expect(fields.map((field) => field.variable)).toEqual(['note'])
    expect(workbenchInputDefaults(fields)).toEqual({})
  })
  it('should preserve explicitly present wrapper defaults and detach them from caller mutation', () => {
    const forms: Parameters['user_input_form'] = [
      { default: false, checkbox: { variable: 'flag', label: 'Flag', default: true } },
    ]
    const fields = decodeWorkbenchInputs(forms)
    forms[0]!.default = true
    expect(workbenchInputDefaults(fields)).toEqual({ flag: false })
  })
  it.each(
    [
      [{ future_type: { variable: 'x', label: 'X' } }],
      [{ number: { variable: 'x', label: 'X' }, checkbox: { variable: 'y', label: 'Y' } }],
      [{ number: { variable: 'x' } }],
      [{ number: { variable: 'x', label: 'X', hide: 'false' } }],
      [{ select: { variable: 'x', label: 'X', options: [1] } }],
      [
        { number: { variable: 'x', label: 'X' } },
        { checkbox: { variable: 'x', label: 'Duplicate' } },
      ],
    ].map((forms) => ({ forms })),
  )('should reject malformed or ambiguous schema without silently skipping it: %j', ({ forms }) => {
    expect(() => decodeWorkbenchInputs(forms)).toThrow('Invalid workbench input configuration')
  })
})

describe('workbench input defaults boundaries', () => {
  it('should use the variable as an accessible label when a native label is empty', () => {
    expect(
      decodeWorkbenchInputs([{ 'text-input': { variable: 'batch', label: '' } }])[0]?.label,
    ).toBe('batch')
  })
  it('should preserve explicit null without borrowing a different nested default', () => {
    expect(
      workbenchInputDefaults(
        decodeWorkbenchInputs([
          { default: null, number: { variable: 'count', label: 'Count', default: 9 } },
        ]),
      ),
    ).toEqual({ count: null })
  })
  it('should create own data properties for valid prototype-like variable names', () => {
    const value = workbenchInputDefaults(
      decodeWorkbenchInputs([
        { 'text-input': { variable: '__proto__', label: 'Value', default: 'data' } },
      ]),
    )
    expect(Object.hasOwn(value, '__proto__')).toBe(true)
    expect(Object.getOwnPropertyDescriptor(value, '__proto__')?.value).toBe('data')
    expect(Object.getPrototypeOf(value)).toBe(Object.prototype)
  })
  it('should return detached nested defaults on every form initialization', () => {
    const fields = decodeWorkbenchInputs([
      { json_object: { variable: 'settings', label: 'Settings', default: { batch: '001' } } },
    ])
    const first = workbenchInputDefaults(fields)
    if (!first.settings || typeof first.settings !== 'object' || Array.isArray(first.settings))
      throw new Error('Expected object default')
    expect(Reflect.set(first.settings, 'batch', 'changed')).toBe(true)
    expect(workbenchInputDefaults(fields)).toEqual({ settings: { batch: '001' } })
  })
})
