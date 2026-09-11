import type {
  DashboardBindingView,
  DashboardQueryChoice,
  DashboardQueryParameter,
} from '@enterprise/business-contracts/types'
import {
  bindingReceiptMatches,
  buildBindingWrite,
  parseBindingParameter,
  suggestFieldMap,
} from '../binding-form'

describe('binding save command', () => {
  it('suggests exact-name or unique compatible fields without guessing ambiguous columns', () => {
    const slot = {
      slot_id: 'series',
      columns: [
        { name: 'name', kind: 'string' as const },
        { name: 'value', kind: 'decimal' as const, unit: 'C' },
      ],
    }
    const source = [
      { name: 'device', kind: 'string' as const },
      { name: 'temperature', kind: 'decimal' as const, unit: 'C' },
    ]
    expect(suggestFieldMap(slot, source)).toEqual({ name: 'device', value: 'temperature' })
    expect(
      suggestFieldMap(slot, [...source, { name: 'other', kind: 'decimal', unit: 'C' }]),
    ).toEqual({ name: 'device' })
    expect(
      suggestFieldMap(slot, [...source, { name: 'value', kind: 'decimal', unit: 'C' }]),
    ).toEqual({ name: 'device', value: 'value' })
    expect(suggestFieldMap(slot, [{ name: 'value', kind: 'decimal', unit: 'F' }])).toEqual({})
    expect(suggestFieldMap(slot, [{ name: 'value', kind: 'string' }])).toEqual({ name: 'value' })
  })

  const view: DashboardBindingView = {
    dashboard_id: 'screen',
    revision: 4,
    design_identity: 'a'.repeat(64),
    bindings: [],
    slots: [
      {
        slot_id: 'metric',
        columns: [{ name: 'value', kind: 'decimal', unit: 'C', required: true }],
      },
    ],
  }
  const query: DashboardQueryChoice = {
    query_ref: 'temperature',
    query_revision: 'v2',
    device_id: 'device',
    columns: [{ name: 'reading', kind: 'decimal', unit: 'C' }],
    parameters: [{ input_key: 'threshold', kind: 'decimal', nullable: false }],
  }
  const draft = {
    slotId: 'metric',
    queryRef: 'temperature',
    queryRevision: 'v2',
    fieldMap: { value: 'reading' },
    parameters: { threshold: { text: '98.2500', isNull: false } },
  }

  it('should require exact saved bindings, unchanged slots and one committed revision', () => {
    const command = buildBindingWrite(view, [query], [draft])
    const receipt = { ...view, revision: 5, bindings: command.bindings }
    expect(bindingReceiptMatches(view, command, receipt)).toBe(true)
    expect(bindingReceiptMatches(view, command, { ...receipt, bindings: [] })).toBe(false)
    expect(bindingReceiptMatches(view, command, { ...receipt, revision: 4 })).toBe(false)
    expect(bindingReceiptMatches(view, command, { ...receipt, revision: 6 })).toBe(false)
    expect(bindingReceiptMatches(view, command, { ...receipt, slots: [] })).toBe(false)
    expect(bindingReceiptMatches(view, command, { ...receipt, dashboard_id: 'other' })).toBe(false)
    expect(
      bindingReceiptMatches(view, command, { ...receipt, design_identity: 'b'.repeat(64) }),
    ).toBe(false)
  })

  it('should accept a no-op without advancing revision and ignore object key order', () => {
    const command = buildBindingWrite(view, [query], [draft])
    const existing = { ...view, bindings: command.bindings }
    const receipt = {
      ...existing,
      bindings: command.bindings.map((item) => ({
        parameters: item.parameters,
        field_map: item.field_map,
        slot_id: item.slot_id,
        query_revision: item.query_revision,
        query_ref: item.query_ref,
      })),
    }
    expect(bindingReceiptMatches(existing, command, receipt)).toBe(true)
  })

  it('should pin version and design while preserving typed parameters', () => {
    expect(buildBindingWrite(view, [query], [draft])).toEqual({
      expected_revision: 4,
      expected_design_identity: 'a'.repeat(64),
      bindings: [
        {
          slot_id: 'metric',
          query_ref: 'temperature',
          query_revision: 'v2',
          field_map: { value: 'reading' },
          parameters: { threshold: '98.2500' },
        },
      ],
    })
    expect(view.bindings).toEqual([])
  })

  it('should allow an explicit empty binding draft', () => {
    expect(buildBindingWrite(view, [query], []).bindings).toEqual([])
  })

  it('should reject stale query choices and unknown or duplicate slots', () => {
    expect(() => buildBindingWrite(view, [], [draft])).toThrow()
    expect(() => buildBindingWrite(view, [query], [{ ...draft, queryRevision: 'old' }])).toThrow()
    expect(() => buildBindingWrite(view, [query], [{ ...draft, slotId: 'unknown' }])).toThrow()
    expect(() => buildBindingWrite(view, [query], [draft, draft])).toThrow()
  })

  it('should reject missing, unknown, mismatched-type or mismatched-unit columns', () => {
    expect(() => buildBindingWrite(view, [query], [{ ...draft, fieldMap: {} }])).toThrow()
    expect(() =>
      buildBindingWrite(view, [query], [{ ...draft, fieldMap: { value: 'missing' } }]),
    ).toThrow()
    expect(() =>
      buildBindingWrite(
        view,
        [query],
        [{ ...draft, fieldMap: { value: 'reading', extra: 'reading' } }],
      ),
    ).toThrow()
    for (const column of [
      { name: 'reading', kind: 'decimal' as const, unit: 'F' },
      { name: 'reading', kind: 'string' as const, unit: 'C' },
    ]) {
      expect(() => buildBindingWrite(view, [{ ...query, columns: [column] }], [draft])).toThrow()
    }
  })

  it('should reject missing parameters and undeclared scope overrides', () => {
    expect(() => buildBindingWrite(view, [query], [{ ...draft, parameters: {} }])).toThrow()
    expect(() =>
      buildBindingWrite(
        view,
        [query],
        [
          {
            ...draft,
            parameters: { ...draft.parameters, device: { text: 'other', isNull: false } },
          },
        ],
      ),
    ).toThrow()
  })
})

describe('binding parameter input', () => {
  const declaration = (
    kind: DashboardQueryParameter['kind'],
    nullable = false,
  ): DashboardQueryParameter => ({ input_key: 'value', kind, nullable })

  it('should preserve string whitespace and empty strings', () => {
    expect(parseBindingParameter(declaration('string'), ' 001 ')).toBe(' 001 ')
    expect(parseBindingParameter(declaration('string', true), '')).toBe('')
  })

  it('should preserve decimal precision without converting to Number', () => {
    expect(parseBindingParameter(declaration('decimal'), '98.2500')).toBe('98.2500')
    expect(parseBindingParameter(declaration('decimal'), '1E-1000')).toBe('1E-1000')
    expect(parseBindingParameter(declaration('decimal'), '9007199254740993')).toBe(
      '9007199254740993',
    )
  })

  it('should require explicit null selection instead of treating empty input as null', () => {
    expect(parseBindingParameter(declaration('decimal', true), '', true)).toBeNull()
    expect(() => parseBindingParameter(declaration('decimal', true), '')).toThrow()
    expect(() => parseBindingParameter(declaration('string'), '', true)).toThrow()
  })

  it('should parse boolean values without string truthiness', () => {
    expect(parseBindingParameter(declaration('boolean'), 'false')).toBe(false)
    expect(parseBindingParameter(declaration('boolean'), 'true')).toBe(true)
    expect(() => parseBindingParameter(declaration('boolean'), '1')).toThrow()
  })

  it('should reject integers that JSON numbers would silently round', () => {
    expect(parseBindingParameter(declaration('integer'), '42')).toBe(42)
    expect(() => parseBindingParameter(declaration('integer'), '9007199254740993')).toThrow()
    expect(() => parseBindingParameter(declaration('integer'), '1.5')).toThrow()
  })

  it.each(['NaN', 'Infinity', '0x10', '', '1_000'])('should reject invalid decimal %s', (value) => {
    expect(() => parseBindingParameter(declaration('decimal'), value)).toThrow()
  })

  it('should preserve date and timezone-aware datetime text', () => {
    expect(parseBindingParameter(declaration('date'), '2026-09-11')).toBe('2026-09-11')
    expect(parseBindingParameter(declaration('datetime'), '2026-09-11T10:30:00+08:00')).toBe(
      '2026-09-11T10:30:00+08:00',
    )
    expect(() => parseBindingParameter(declaration('datetime'), '2026-09-11T10:30')).toThrow()
    expect(() => parseBindingParameter(declaration('date'), '2026-02-30')).toThrow()
  })
})
