import type { SourceView } from '@enterprise/business-contracts/types'
import { readSourceForm } from '../form-values'

function values(overrides: Record<string, string> = {}) {
  const data = new FormData()
  for (const [name, value] of Object.entries({
    name: 'Plant readings',
    kind: 'db',
    dialect: 'postgresql',
    host: 'db.internal',
    port: '5432',
    database: 'readings',
    tls: 'on',
    allowed_tables: 'public.measurements',
    sql: 'SELECT * FROM public.measurements WHERE device_code = :device',
    username: 'reader',
    password: 'test-only-password',
    device_ids: 'device-1',
    device_parameter: 'device',
    device_column: 'device_code',
    scope_attribute: 'device_code',
    read_only_confirmed: 'on',
    ...overrides,
  }))
    data.append(name, value)
  return data
}
function previous(): SourceView {
  return {
    ...readSourceForm(values()),
    workspace_id: 'workspace-1',
    source_id: 'source-1',
    source_revision: 'source-v1',
    read_id: 'read-1',
    read_revision: 'read-v1',
    revision: 4,
    created_at: '2026-09-08T00:00:00Z',
    updated_at: '2026-09-08T00:00:00Z',
    connection: {
      kind: 'db',
      dialect: 'postgresql',
      host: 'db.internal',
      port: 5432,
      database: 'readings',
      tls: true,
      allowed_tables: ['public.measurements'],
      sql: 'SELECT 1',
      credentials_configured: true,
    },
  }
}

describe('Source form values', () => {
  it('should preserve a disabled source when lifecycle controls are absent', () => {
    expect(readSourceForm(values(), { ...previous(), enabled: false }).enabled).toBe(false)
  })

  it.each([true, false])('should submit the explicit enabled checkbox state: %s', (enabled) => {
    const data = values({ lifecycle_present: '1' })
    if (enabled) data.set('enabled', 'on')
    expect(readSourceForm(data, { ...previous(), enabled: !enabled }).enabled).toBe(enabled)
  })

  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('should build a typed read-only draft without server identity fields', () => {
    const draft = readSourceForm(values())
    expect(draft).toMatchObject({
      device_ids: ['device-1'],
      read_only_confirmed: true,
      connection: { port: 5432, username: 'reader', password: 'test-only-password' },
    })
    expect(draft).not.toHaveProperty('source_id')
    expect(draft).not.toHaveProperty('revision')
  })

  it('should require an explicit read-only confirmation', () => {
    const data = values()
    data.delete('read_only_confirmed')
    expect(() => readSourceForm(data)).toThrow('invalid')
  })

  it('should preserve blank paired credentials only for an unchanged database target', () => {
    expect(
      readSourceForm(values({ username: '', password: '' }), previous()).connection,
    ).toMatchObject({ username: null, password: null })
    expect(() => readSourceForm(values({ username: '', password: '' }))).toThrow(
      'targetCredentials',
    )
  })

  it.each(
    Object.entries({
      host: 'other.internal',
      port: '5433',
      database: 'other',
      dialect: 'mysql',
      tls: '',
    }),
  )('should require fresh credentials when the target changes: %s', (field, value) => {
    expect(() =>
      readSourceForm(values({ [field]: value, username: '', password: '' }), previous()),
    ).toThrow('targetCredentials')
  })

  it('should reject a half-filled credential pair', () => {
    expect(() => readSourceForm(values({ password: '' }), previous())).toThrow('targetCredentials')
  })

  it('should preserve exact selected IDs and typed parameter declarations', () => {
    const data = values({
      device_ids: '0001',
      parameter_ids: 'row-1',
      'parameters.row-1.input_key': 'batch',
      'parameters.row-1.parameter_name': 'batch_id',
      'parameters.row-1.kind': 'string',
      'parameters.row-1.nullable': 'on',
    })
    expect(readSourceForm(data)).toMatchObject({
      device_ids: ['0001'],
      parameters: [
        { input_key: 'batch', parameter_name: 'batch_id', kind: 'string', nullable: true },
      ],
    })
  })

  it('should reject parameter scope collisions before submitting', () => {
    const data = values({
      parameter_ids: 'row-1',
      'parameters.row-1.input_key': 'batch',
      'parameters.row-1.parameter_name': 'device',
      'parameters.row-1.kind': 'string',
    })
    expect(() => readSourceForm(data)).toThrow('invalid')
  })

  it('should distinguish HTTP header retention from explicit clearing', () => {
    const data = values({
      kind: 'http',
      url: 'https://api.internal/readings',
      method: 'GET',
      header_mode: 'keep',
    })
    expect(readSourceForm(data).connection).toMatchObject({ headers: null, rows_path: [] })
    data.set('header_mode', 'clear')
    expect(readSourceForm(data).connection).toMatchObject({ headers: [] })
  })

  it('should require explicit headers after changing a credentialed HTTP target', () => {
    const source: SourceView = {
      ...previous(),
      connection: {
        kind: 'http',
        url: 'https://api.internal/readings',
        method: 'GET',
        allow_plain_http: false,
        header_names: ['Authorization'],
        credentials_configured: true,
      },
    }
    const data = values({
      kind: 'http',
      url: 'https://other.internal/readings',
      method: 'GET',
      header_mode: 'keep',
    })
    expect(() => readSourceForm(data, source)).toThrow('targetHeaders')
    data.set('header_mode', 'clear')
    expect(readSourceForm(data, source).connection).toMatchObject({ headers: [] })
  })

  it('should construct header replacements and cursor pagination from ordinary fields', () => {
    const data = values({
      kind: 'http',
      url: 'https://api.internal/readings',
      method: 'POST',
      rows_path: 'data\nrows',
      header_mode: 'replace',
      header_ids: 'h1',
      'headers.h1.name': 'Authorization',
      'headers.h1.value': 'test-only-header',
      pagination: 'on',
      cursor_parameter: 'cursor',
      next_cursor_path: 'meta\nnext',
      has_more_path: 'meta\nmore',
    })
    expect(readSourceForm(data).connection).toMatchObject({
      headers: [{ name: 'Authorization', value: 'test-only-header' }],
      rows_path: ['data', 'rows'],
      pagination: {
        parameter: 'cursor',
        next_cursor_path: ['meta', 'next'],
        has_more_path: ['meta', 'more'],
      },
    })
  })
})
