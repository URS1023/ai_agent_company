'use client'

import type {
  ParameterKind,
  SourceDraftWritable,
  SourceView,
} from '@enterprise/business-contracts/types'
import { Button } from '@langgenius/dify-ui/button'
import { Collapsible, CollapsiblePanel, CollapsibleTrigger } from '@langgenius/dify-ui/collapsible'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Choice, TextField, Toggle } from './controls'
import { DevicePicker } from './device-picker'

export function SourceFields({
  initial,
  scope,
  disabled,
}: {
  initial?: SourceView | SourceDraftWritable
  scope: readonly string[]
  disabled: boolean
}) {
  const { t } = useTranslation('common')
  const [kind, setKind] = useState<SourceDraftWritable['connection']['kind']>(
    initial?.connection.kind ?? 'db',
  )
  const [parameters, setParameters] = useState(() =>
    (initial?.parameters ?? []).map((value) => ({ id: crypto.randomUUID(), value })),
  )
  const oldDb = initial?.connection.kind === 'db' ? initial.connection : undefined
  const oldHttp = initial?.connection.kind === 'http' ? initial.connection : undefined
  return (
    <div className="space-y-6">
      <input type="hidden" name="lifecycle_present" value="1" />
      <Toggle
        name="enabled"
        label={t(($) => $['enterprise.schedule.enabled'])}
        defaultChecked={initial?.enabled ?? true}
        disabled={disabled}
      />
      <TextField
        name="name"
        label={t(($) => $['enterprise.sources.name'])}
        defaultValue={initial?.name ?? ''}
        maxLength={200}
      />
      <Choice<SourceDraftWritable['connection']['kind']>
        name="kind"
        label={t(($) => $['enterprise.sources.type'])}
        value={kind}
        onChange={setKind}
        disabled={disabled}
        values={[
          { value: 'db', label: t(($) => $['enterprise.sources.db']) },
          { value: 'http', label: t(($) => $['enterprise.sources.http']) },
        ]}
      />
      <section
        aria-label={t(($) => $['enterprise.sources.connection'])}
        className="space-y-4 border-t border-divider-subtle pt-5"
      >
        {kind === 'db' ? (
          <>
            <div className="grid gap-4 sm:grid-cols-2">
              <Choice
                name="dialect"
                label={t(($) => $['enterprise.sources.dialect'])}
                defaultValue={oldDb?.dialect ?? 'postgresql'}
                disabled={disabled}
                values={[
                  { value: 'postgresql', label: 'PostgreSQL' },
                  { value: 'mysql', label: 'MySQL' },
                ]}
              />
              <TextField
                name="host"
                label={t(($) => $['enterprise.sources.host'])}
                defaultValue={oldDb?.host ?? ''}
                maxLength={253}
              />
              <TextField
                name="port"
                label={t(($) => $['enterprise.sources.port'])}
                defaultValue={String(oldDb?.port ?? 5432)}
                type="number"
                min={1}
                max={65535}
              />
              <TextField
                name="database"
                label={t(($) => $['enterprise.sources.database'])}
                defaultValue={oldDb?.database ?? ''}
                maxLength={200}
              />
            </div>
            <Toggle
              name="tls"
              label={t(($) => $['enterprise.sources.tls'])}
              defaultChecked={oldDb?.tls ?? true}
              disabled={disabled}
            />
            <div className="grid gap-4 sm:grid-cols-2">
              <TextField
                name="username"
                label={t(($) => $['enterprise.sources.username'])}
                defaultValue={oldDb && 'username' in oldDb ? (oldDb.username ?? '') : ''}
                autoComplete="off"
                maxLength={256}
              />
              <TextField
                name="password"
                label={t(($) => $['enterprise.sources.password'])}
                defaultValue={oldDb && 'password' in oldDb ? (oldDb.password ?? '') : ''}
                type="password"
                autoComplete="new-password"
                maxLength={4096}
              />
            </div>
            <p className="system-xs-regular text-text-tertiary">
              {t(($) => $['enterprise.sources.credentialHint'])}
            </p>
            <TextField
              name="allowed_tables"
              label={t(($) => $['enterprise.sources.allowedTables'])}
              defaultValue={oldDb?.allowed_tables.join('\n') ?? ''}
              multiline
            />
            <TextField
              name="sql"
              label={t(($) => $['enterprise.sources.sql'])}
              defaultValue={oldDb?.sql ?? ''}
              maxLength={20000}
              multiline
            />
          </>
        ) : (
          <HttpFields initial={oldHttp} disabled={disabled} />
        )}
      </section>
      <section
        aria-label={t(($) => $['enterprise.sources.scope'])}
        className="space-y-4 border-t border-divider-subtle pt-5"
      >
        <h3 className="system-md-semibold text-text-primary">
          {t(($) => $['enterprise.sources.scope'])}
        </h3>
        <DevicePicker scope={scope} initial={initial?.device_ids ?? []} disabled={disabled} />
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            name="device_parameter"
            label={t(($) => $['enterprise.sources.deviceParameter'])}
            defaultValue={initial?.device_parameter ?? 'device'}
            maxLength={80}
          />
          <TextField
            name="device_column"
            label={t(($) => $['enterprise.sources.deviceColumn'])}
            defaultValue={initial?.device_column ?? 'device_code'}
            maxLength={128}
          />
          <Choice
            name="scope_attribute"
            label={t(($) => $['enterprise.sources.scopeAttribute'])}
            defaultValue={initial?.scope_attribute ?? 'device_code'}
            disabled={disabled}
            values={[
              { value: 'device_code', label: t(($) => $['enterprise.sources.scopeByCode']) },
              { value: 'id', label: t(($) => $['enterprise.sources.scopeById']) },
            ]}
          />
          <TextField
            name="department_parameter"
            label={t(($) => $['enterprise.sources.departmentParameter'])}
            defaultValue={initial?.department_parameter ?? ''}
            maxLength={80}
          />
        </div>
      </section>
      <section aria-label={t(($) => $['enterprise.sources.parameters'])} className="space-y-4">
        <h3 className="system-md-semibold text-text-primary">
          {t(($) => $['enterprise.sources.parameters'])}
        </h3>
        {parameters.map(({ id, value }) => (
          <div key={id} className="space-y-3 rounded-xl border border-divider-subtle p-4">
            <input type="hidden" name="parameter_ids" value={id} />
            <div className="grid gap-3 sm:grid-cols-2">
              <TextField
                name={`parameters.${id}.input_key`}
                label={t(($) => $['enterprise.sources.inputKey'])}
                defaultValue={value.input_key}
                maxLength={128}
              />
              <TextField
                name={`parameters.${id}.parameter_name`}
                label={t(($) => $['enterprise.sources.parameterName'])}
                defaultValue={value.parameter_name}
                maxLength={80}
              />
              <Choice
                name={`parameters.${id}.kind`}
                label={t(($) => $['enterprise.sources.parameterKindLabel'])}
                defaultValue={value.kind}
                disabled={disabled}
                values={(
                  [
                    'string',
                    'integer',
                    'decimal',
                    'boolean',
                    'date',
                    'datetime',
                  ] as const satisfies readonly ParameterKind[]
                ).map((type) => ({
                  value: type,
                  label: t(($) => $[`enterprise.sources.parameterKind.${type}`]),
                }))}
              />
              <Toggle
                name={`parameters.${id}.nullable`}
                label={t(($) => $['enterprise.sources.nullable'])}
                defaultChecked={value.nullable}
                disabled={disabled}
              />
            </div>
            <Button
              type="button"
              variant="secondary"
              disabled={disabled}
              onClick={() => setParameters((rows) => rows.filter((row) => row.id !== id))}
            >
              {t(($) => $['operation.remove'])}
            </Button>
          </div>
        ))}
        <Button
          type="button"
          variant="secondary"
          disabled={disabled || parameters.length >= 100}
          onClick={() =>
            setParameters((rows) => [
              ...rows,
              {
                id: crypto.randomUUID(),
                value: { input_key: '', parameter_name: '', kind: 'string', nullable: false },
              },
            ])
          }
        >
          {t(($) => $['enterprise.sources.addParameter'])}
        </Button>
      </section>
      <Collapsible>
        <CollapsibleTrigger>{t(($) => $['enterprise.sources.advanced'])}</CollapsibleTrigger>
        <CollapsiblePanel keepMounted>
          <div className="grid gap-4 py-4 sm:grid-cols-2">
            <TextField
              name="max_rows"
              label={t(($) => $['enterprise.sources.maxRows'])}
              defaultValue={String(initial?.limits?.max_rows ?? 1000)}
              type="number"
              min={1}
              max={100000}
            />
            <TextField
              name="max_bytes"
              label={t(($) => $['enterprise.sources.maxBytes'])}
              defaultValue={String(initial?.limits?.max_bytes ?? 2097152)}
              type="number"
              min={1}
              max={104857600}
            />
            <TextField
              name="max_pages"
              label={t(($) => $['enterprise.sources.maxPages'])}
              defaultValue={String(initial?.limits?.max_pages ?? 20)}
              type="number"
              min={1}
              max={100}
            />
            <TextField
              name="timeout_seconds"
              label={t(($) => $['enterprise.sources.timeout'])}
              defaultValue={String(initial?.limits?.timeout_seconds ?? 15)}
              type="number"
              min={0.01}
              max={120}
              step="any"
            />
          </div>
        </CollapsiblePanel>
      </Collapsible>
    </div>
  )
}

function HttpFields({
  initial,
  disabled,
}: {
  initial?: Extract<SourceView['connection'] | SourceDraftWritable['connection'], { kind: 'http' }>
  disabled: boolean
}) {
  const { t } = useTranslation('common')
  const oldHeaders = initial && 'headers' in initial ? initial.headers : null
  const [headerMode, setHeaderMode] = useState<'keep' | 'replace' | 'clear'>(
    oldHeaders ? (oldHeaders.length ? 'replace' : 'clear') : 'keep',
  )
  const [headers, setHeaders] = useState(() =>
    (oldHeaders ?? []).map((value) => ({ id: crypto.randomUUID(), value })),
  )
  return (
    <>
      <TextField
        name="url"
        label={t(($) => $['enterprise.sources.url'])}
        defaultValue={initial?.url ?? ''}
        maxLength={4096}
      />
      <Choice
        name="method"
        label={t(($) => $['enterprise.sources.method'])}
        defaultValue={initial?.method ?? 'GET'}
        disabled={disabled}
        values={[
          { value: 'GET', label: 'GET' },
          { value: 'POST', label: 'POST' },
        ]}
      />
      <Toggle
        name="allow_plain_http"
        label={t(($) => $['enterprise.sources.allowHttp'])}
        defaultChecked={initial?.allow_plain_http}
        disabled={disabled}
      />
      <TextField
        name="rows_path"
        label={t(($) => $['enterprise.sources.rowsPath'])}
        defaultValue={initial?.rows_path?.join('\n') ?? ''}
        multiline
      />
      <Choice
        name="header_mode"
        label={t(($) => $['enterprise.sources.headerMode'])}
        value={headerMode}
        onChange={setHeaderMode}
        disabled={disabled}
        values={[
          { value: 'keep', label: t(($) => $['enterprise.sources.keepHeaders']) },
          { value: 'replace', label: t(($) => $['enterprise.sources.replaceHeaders']) },
          { value: 'clear', label: t(($) => $['enterprise.sources.clearHeaders']) },
        ]}
      />
      <p className="system-xs-regular text-text-tertiary">
        {t(($) => $['enterprise.sources.headersHint'])}
      </p>
      {initial && 'header_names' in initial && initial.header_names.length > 0 && (
        <p className="font-mono system-xs-regular break-words text-text-secondary">
          {initial.header_names.join(', ')}
        </p>
      )}
      {headerMode === 'replace' && (
        <div className="space-y-3">
          {headers.map(({ id, value }) => (
            <div key={id} className="space-y-3 rounded-xl border border-divider-subtle p-3">
              <input type="hidden" name="header_ids" value={id} />
              <TextField
                name={`headers.${id}.name`}
                label={t(($) => $['enterprise.sources.headerName'])}
                defaultValue={value.name}
                maxLength={128}
              />
              <TextField
                name={`headers.${id}.value`}
                label={t(($) => $['enterprise.sources.headerValue'])}
                defaultValue={value.value}
                type="password"
                autoComplete="new-password"
                maxLength={4096}
              />
              <Button
                type="button"
                variant="secondary"
                disabled={disabled}
                onClick={() => setHeaders((rows) => rows.filter((row) => row.id !== id))}
              >
                {t(($) => $['operation.remove'])}
              </Button>
            </div>
          ))}
          <Button
            type="button"
            variant="secondary"
            disabled={disabled || headers.length >= 30}
            onClick={() =>
              setHeaders((rows) => [
                ...rows,
                { id: crypto.randomUUID(), value: { name: '', value: '' } },
              ])
            }
          >
            {t(($) => $['enterprise.sources.addHeader'])}
          </Button>
        </div>
      )}
      <Collapsible>
        <CollapsibleTrigger>{t(($) => $['enterprise.sources.pagination'])}</CollapsibleTrigger>
        <CollapsiblePanel keepMounted>
          <div className="space-y-3 py-3">
            <Toggle
              name="pagination"
              label={t(($) => $['enterprise.sources.pagination'])}
              defaultChecked={Boolean(initial?.pagination)}
              disabled={disabled}
            />
            <TextField
              name="cursor_parameter"
              label={t(($) => $['enterprise.sources.cursorParameter'])}
              defaultValue={initial?.pagination?.parameter ?? 'cursor'}
              maxLength={128}
            />
            <TextField
              name="next_cursor_path"
              label={t(($) => $['enterprise.sources.nextCursorPath'])}
              defaultValue={initial?.pagination?.next_cursor_path.join('\n') ?? 'next_cursor'}
              multiline
            />
            <TextField
              name="has_more_path"
              label={t(($) => $['enterprise.sources.hasMorePath'])}
              defaultValue={initial?.pagination?.has_more_path?.join('\n') ?? 'has_more'}
              multiline
            />
          </div>
        </CollapsiblePanel>
      </Collapsible>
    </>
  )
}
