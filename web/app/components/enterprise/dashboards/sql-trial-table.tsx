import type { SqlTrialResult } from '@enterprise/business-contracts/types'
import { Button } from '@langgenius/dify-ui/button'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { formatTrialCell } from './sql-trial-result'

export function SqlTrialTable({ result }: { result: SqlTrialResult }) {
  const { t } = useTranslation('common')
  const [page, setPage] = useState(0)
  return (
    <>
      <div className="overflow-auto">
        <table className="w-full text-left system-sm-regular">
          <caption className="pb-3 text-left text-text-secondary">
            {t(($) => $['enterprise.sqlTrial.notice'])}
          </caption>
          <thead>
            <tr>
              {result.columns.map((column) => (
                <th key={column} scope="col" className="p-2">
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {result.rows.slice(page * 20, (page + 1) * 20).map((row, index) => (
              // Immutable capture rows have no unique business key and may contain identical values.
              // eslint-disable-next-line react/no-array-index-key
              <tr key={page * 20 + index}>
                {row.map((cell, column) => (
                  <td key={result.columns[column]} className="p-2 whitespace-pre-wrap">
                    {formatTrialCell(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {result.row_count === 0 && <p>{t(($) => $.noData)}</p>}
      {result.row_count > 20 && (
        <div className="flex items-center gap-3">
          <Button
            variant="secondary"
            disabled={page === 0}
            onClick={() => setPage((value) => value - 1)}
          >
            {t(($) => $['pagination.previous'])}
          </Button>
          <span>
            {page + 1} / {Math.ceil(result.row_count / 20)}
          </span>
          <Button
            variant="secondary"
            disabled={(page + 1) * 20 >= result.row_count}
            onClick={() => setPage((value) => value + 1)}
          >
            {t(($) => $['pagination.next'])}
          </Button>
        </div>
      )}
    </>
  )
}
