import type { SqlProposals } from '@enterprise/business-contracts/types'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SqlProposalCards } from '../sql-proposal-cards'

const proposals: SqlProposals = {
  slots: [
    {
      slot_id: 'count',
      sql: 'SELECT COUNT(*) AS value FROM inspections',
      field_map: { value: 'value' },
      metric_definition: 'Completed inspections',
      time_definition: 'Current shift',
    },
    {
      slot_id: 'rate',
      sql: 'SELECT rate FROM daily_quality',
      field_map: { value: 'rate' },
      metric_definition: 'Pass rate',
      time_definition: 'Last seven days',
    },
  ],
}

describe('SQL proposal cards', () => {
  it('should show business definitions with technical content collapsed by default', () => {
    render(<SqlProposalCards proposals={proposals} />)

    expect(screen.getByText('Completed inspections')).toBeVisible()
    expect(screen.getByText('Current shift')).toBeVisible()
    expect(screen.getByText('Pass rate')).toBeVisible()
    for (const button of screen.getAllByRole('button', {
      name: 'common.enterprise.sqlGeneration.advanced',
    }))
      expect(button).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText(proposals.slots[0]!.sql)).not.toBeInTheDocument()
    expect(screen.queryByText('common.enterprise.sqlGeneration.mapping')).not.toBeInTheDocument()
  })

  it('should expand only the chosen slot and allow keyboard collapse', async () => {
    const user = userEvent.setup()
    render(<SqlProposalCards proposals={proposals} />)
    const cards = screen.getAllByRole('article')
    const button = within(cards[0]!).getByRole('button', {
      name: 'common.enterprise.sqlGeneration.advanced',
    })

    await user.click(button)

    expect(button).toHaveAttribute('aria-expanded', 'true')
    expect(within(cards[0]!).getByText(proposals.slots[0]!.sql)).toBeVisible()
    expect(within(cards[0]!).getByText('common.enterprise.sqlGeneration.mapping')).toBeVisible()
    expect(within(cards[1]!).queryByText(proposals.slots[1]!.sql)).not.toBeInTheDocument()
    await user.keyboard('{Enter}')
    await waitFor(() => expect(button).toHaveAttribute('aria-expanded', 'false'))
    expect(screen.getByText('Completed inspections')).toBeVisible()
  })

  it('should render untrusted SQL and mapping values as text rather than markup', async () => {
    const sql = '<img src=x onerror=alert(1)>'
    render(
      <SqlProposalCards
        proposals={{
          slots: [{ ...proposals.slots[0]!, sql, field_map: { value: '<script>bad</script>' } }],
        }}
      />,
    )

    await userEvent
      .setup()
      .click(screen.getByRole('button', { name: 'common.enterprise.sqlGeneration.advanced' }))

    expect(screen.getByText(sql)).toBeVisible()
    expect(screen.getByText(/<script>bad<\/script>/)).toBeVisible()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })
})
