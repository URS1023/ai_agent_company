import type { SourceDraftWritable, SourceView } from '@enterprise/business-contracts/types'
import { zSourceDraftWritable } from '@enterprise/business-contracts/zod'

export class SourceFormError extends Error {
  readonly code: 'invalid' | 'targetCredentials' | 'targetHeaders'

  constructor(code: SourceFormError['code']) {
    super(code)
    this.code = code
  }
}

export function readSourceForm(data: FormData, previous?: SourceView): SourceDraftWritable {
  const text = (name: string) => String(data.get(name) ?? '')
  const lines = (name: string) =>
    text(name)
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
  const checked = (name: string) => text(name) === 'on'
  const number = (name: string) => (text(name) === '' ? undefined : Number(text(name)))
  const rows = (name: string) => data.getAll(name).map(String)
  const parsed = zSourceDraftWritable.safeParse({
    name: text('name').trim(),
    enabled: data.has('lifecycle_present') ? checked('enabled') : (previous?.enabled ?? true),
    device_ids: rows('device_ids'),
    device_parameter: text('device_parameter').trim(),
    device_column: text('device_column').trim(),
    scope_attribute: text('scope_attribute'),
    department_parameter: text('department_parameter').trim() || null,
    read_only_confirmed: checked('read_only_confirmed'),
    parameters: rows('parameter_ids').map((id) => ({
      input_key: text(`parameters.${id}.input_key`).trim(),
      parameter_name: text(`parameters.${id}.parameter_name`).trim(),
      kind: text(`parameters.${id}.kind`),
      nullable: checked(`parameters.${id}.nullable`),
    })),
    limits: {
      max_rows: number('max_rows'),
      max_bytes: number('max_bytes'),
      max_pages: number('max_pages'),
      timeout_seconds: number('timeout_seconds'),
    },
    connection:
      text('kind') === 'db'
        ? {
            kind: 'db',
            dialect: text('dialect'),
            host: text('host').trim(),
            port: number('port'),
            database: text('database').trim(),
            tls: checked('tls'),
            allowed_tables: lines('allowed_tables'),
            sql: text('sql'),
            username: text('username') || null,
            password: text('password') || null,
          }
        : {
            kind: 'http',
            url: text('url').trim(),
            method: text('method'),
            allow_plain_http: checked('allow_plain_http'),
            rows_path: lines('rows_path'),
            headers:
              text('header_mode') === 'keep'
                ? null
                : text('header_mode') === 'clear'
                  ? []
                  : rows('header_ids').map((id) => ({
                      name: text(`headers.${id}.name`).trim(),
                      value: text(`headers.${id}.value`),
                    })),
            pagination: checked('pagination')
              ? {
                  parameter: text('cursor_parameter').trim(),
                  next_cursor_path: lines('next_cursor_path'),
                  has_more_path: lines('has_more_path'),
                }
              : null,
          },
  })
  if (!parsed.success) throw new SourceFormError('invalid')
  const draft = parsed.data
  const names = [
    draft.device_parameter,
    ...(draft.department_parameter ? [draft.department_parameter] : []),
    ...draft.parameters.map((item) => item.parameter_name),
  ]
  const inputs = draft.parameters.map((item) => item.input_key)
  if (
    new Set(names).size !== names.length ||
    new Set(inputs).size !== inputs.length ||
    new Set(draft.device_ids).size !== draft.device_ids.length ||
    inputs.some((input) => input === draft.device_parameter || input === draft.department_parameter)
  )
    throw new SourceFormError('invalid')
  const connection = draft.connection
  const old = previous?.connection
  if (connection.kind === 'db') {
    const changed =
      old?.kind !== 'db' ||
      old.dialect !== connection.dialect ||
      old.host !== connection.host ||
      old.port !== connection.port ||
      old.database !== connection.database ||
      (old.tls ?? true) !== connection.tls
    if (
      (connection.username === null) !== (connection.password === null) ||
      (changed && (!connection.username || !connection.password))
    )
      throw new SourceFormError('targetCredentials')
  } else if (old?.kind === 'http' && old.credentials_configured && connection.headers === null) {
    if (
      old.url !== connection.url ||
      (old.method ?? 'GET') !== connection.method ||
      (old.allow_plain_http ?? false) !== connection.allow_plain_http
    )
      throw new SourceFormError('targetHeaders')
  }
  return draft
}
