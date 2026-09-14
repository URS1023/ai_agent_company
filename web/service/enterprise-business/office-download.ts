import type { DownloadDocumentEnterpriseApiV1OfficeFilesFileIdDocumentGetData } from '@enterprise/business-contracts/types'
import { contract } from '@enterprise/business-contracts/orpc'
import {
  zDownloadDocumentEnterpriseApiV1OfficeFilesFileIdDocumentGetPath,
  zDownloadDocumentEnterpriseApiV1OfficeFilesFileIdDocumentGetQuery,
} from '@enterprise/business-contracts/zod'
import { env } from '@/env'
// eslint-disable-next-line no-restricted-imports -- Binary downloads require raw Response, while native base retains cookies/CSRF and disables retries.
import { base } from '../fetch'

const mediaType = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'

export class OfficeDownloadError extends Error {
  constructor() {
    super('Office download failed')
    this.name = 'OfficeDownloadError'
  }
}

export async function fetchOfficeDocument(
  input: Pick<DownloadDocumentEnterpriseApiV1OfficeFilesFileIdDocumentGetData, 'path' | 'query'>,
  { signal }: { signal?: AbortSignal } = {},
): Promise<{ blob: Blob; filename: string }> {
  signal?.throwIfAborted()
  const path = zDownloadDocumentEnterpriseApiV1OfficeFilesFileIdDocumentGetPath.parse(input.path)
  const query = zDownloadDocumentEnterpriseApiV1OfficeFilesFileIdDocumentGetQuery.parse(input.query)
  const route = contract.office.files.byFileId.document.get['~orpc'].route
  if (!route.path || route.method !== 'GET') throw new OfficeDownloadError()
  const pathname = route.path.replace('{file_id}', encodeURIComponent(path.file_id))
  const url = new URL(`${env.NEXT_PUBLIC_BASE_PATH}${pathname}`, window.location.origin)
  if (url.origin !== window.location.origin) throw new OfficeDownloadError()
  url.searchParams.set('expected_revision', query.expected_revision)
  let response: Response | undefined
  try {
    response = await base<Response>(
      url.href,
      {
        method: 'GET',
        signal,
        redirect: 'error',
        cache: 'no-store',
        headers: { Accept: mediaType },
      },
      { fetchCompat: true, request: new Request(url), silent: true },
    )
    signal?.throwIfAborted()
    if (
      response.status !== 200 ||
      response.headers.get('Content-Type')?.split(';')[0]?.trim().toLowerCase() !== mediaType
    )
      throw new OfficeDownloadError()
    const blob = await response.blob()
    signal?.throwIfAborted()
    return { blob, filename: `office-${path.file_id}-r${query.expected_revision}.docx` }
  } catch {
    signal?.throwIfAborted()
    throw new OfficeDownloadError()
  } finally {
    if (response?.body && !response.body.locked) await response.body.cancel().catch(() => undefined)
  }
}
